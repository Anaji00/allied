"""Round 4: fuzzing every new endpoint, a chase run that meets a deleted item, size limits, volume and speed,
database/model drift, and the response shapes the frontend relies on. Test data only; nothing external is called."""

import io
import itertools
import json
import re
import threading
import time
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from openpyxl import load_workbook

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemBallDB, NotificationDB, StaffContactDB, VendorJobDB
from app.services import bridge
from app.services.coordination import ball, forms, updates, vendors, workdays

TAG = uuid.uuid4().hex[:5]
MGR, ENG = f"mgr{TAG}@allied.test", f"eng{TAG}@allied.test"
M = {"email": MGR, "role": "MANAGER"}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, client):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bridge, "_ask", lambda t, s, g, a: SimpleNamespace(translation=f"[{g}] {t}", unsure_terms=[]))
    with SessionLocal() as db:
        for e in (MGR, ENG):
            if db.get(StaffContactDB, e) is None:
                db.add(StaffContactDB(email=e, name=e.split("@")[0], role_tag="staff", channels=["email"], active=True))
        db.commit()


def _order():
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return str(out.sales_order_number)


def _alerts(prefix):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB).all() if n.dedup_key.startswith(prefix)]


# ------------------------------------------------------------------------ fuzzing


PREFIXES = ("/api/v1/updates", "/api/v1/ball", "/api/v1/language", "/api/v1/workspace", "/api/v1/forms", "/api/v1/exports",
            "/api/v1/carriers", "/api/v1/fedex", "/api/v1/packing-slips")
JUNK_BODIES = [None, {}, [], "text", 5, True, {"a": None}, {"body": None, "item_type": None, "item_ref": None}, {"x": "李" * 5000},
               {"item_type": "order", "item_ref": ["x"], "body": {"a": 1}}, {"name": 5, "fields": "x", "milestones": 7, "filters": [1]},
               {"language": ["en"]}, {"answers": {"a": {"b": {"c": [1] * 50}}}}, {"mentions": [None, 1, "x"], "body": "hi", "item_type": "order", "item_ref": "1"},
               {"start": 20261101, "owners": 3}, {"carrier": ["x"], "rate_usd": {"a": 1}}, {"parent_id": {"x": 1}}, {"due": {"a": 1}, "note": 5}]
PATH_VALUES = ["1", "abc", "999999999999999999999", "%20", "李明", "a" * 300, "-1", "0", "..", "null"]
QUERY_JUNK = [{}, {"item_type": "order", "item_ref": "x" * 300}, {"format": "xlsx"}, {"format": "../../etc"}, {"lang": "zh"}, {"lang": "x"},
              {"days": "abc"}, {"who": "me"}, {"q": "李" * 100}, {"refs": ",".join(["1"] * 500), "item_type": "order"}, {"address": "x" * 2000}]


def test_no_new_endpoint_can_be_made_to_fail_with_a_server_error(client):
    from fastapi.testclient import TestClient

    client = TestClient(client.app, raise_server_exceptions=False)             # a crash comes back as a 500 we can list
    paths = client.app.openapi()["paths"]
    server_errors, tried = [], 0
    for path, ops in paths.items():
        if not path.startswith(PREFIXES):
            continue
        for method in ops:
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            has_body = method.upper() in ("POST", "PUT", "PATCH")
            for pv, body, query in itertools.islice(itertools.product(PATH_VALUES[:4] + PATH_VALUES[4::3], JUNK_BODIES if has_body else [None], QUERY_JUNK[:6]), 130):
                url = re.sub(r"\{[^}]+\}", pv, path)
                r = client.request(method.upper(), url, params=query, json=body if has_body else None)
                tried += 1
                if r.status_code >= 500:
                    server_errors.append((method.upper(), path, pv, str(body)[:60], query, r.status_code))
                    break
    assert tried > 500, tried
    assert server_errors == [], f"server errors: {server_errors[:8]}"


# ------------------------------------------------------------------------ the chase worker


def test_a_ball_on_something_that_was_deleted_never_stops_the_other_chasing(monkeypatch):
    job = vendors.create({"vendor": f"GONE{TAG}", "description": "x", "po_number": f"G{TAG}"}, M)
    so = _order()
    ball.set_ball("vendor_job", str(job["id"]), M, "vendor", "GONE", owner=MGR)
    ball.set_ball("order", so, M, "engineer", ENG, owner=MGR)
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()
    monkeypatch.setattr(ball, "_days", lambda since: 5.0)
    out = ball.run()                                                                       # must not raise
    assert out["escalated"] >= 1
    rid = next(r.id for r in _rows(so))
    assert _alerts(f"ball:{rid}:")
    assert not any(i["item_ref"] == str(job["id"]) for i in ball.list_open(include_derived=False))   # closed: its number could be reused


def _rows(so):
    with SessionLocal() as db:
        return db.query(ItemBallDB).filter(ItemBallDB.item_ref == so).all()


# ------------------------------------------------------------------------ size limits


def test_oversized_form_answers_and_mention_lists_are_refused_early(client):
    so = _order()
    big = json.dumps({"drawing_revision": "x" * 300_000})
    assert client.post("/api/v1/projects/milestones/123/submissions", files={"files": ("a.pdf", b"%PDF-1.4 x", "application/pdf")},
                       data={"answers": big}).status_code in (400, 404, 413, 422)
    r = client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": "hi", "mentions": [f"a{i}@x.test" for i in range(5000)]})
    assert r.status_code == 400
    assert client.post("/api/v1/workspace/views", json={"name": "n", "screen": "orders", "filters": {"k": "x" * 100_000}}).status_code == 400


def test_the_form_answers_field_has_a_hard_size_limit(client):
    r = client.post("/api/v1/projects/milestones/123/submissions", files={"files": ("a.pdf", b"%PDF-1.4 x", "application/pdf")},
                    data={"answers": "[" * 300_000})
    assert r.status_code == 400 and "too large" in r.json()["detail"].lower()


# ------------------------------------------------------------------------ volume and speed


def test_a_300_comment_thread_and_a_300_ball_list_stay_quick():
    so = _order()
    for i in range(300):
        updates.post("order", so, f"comment {i} @eng{TAG}" if i % 50 == 0 else f"comment {i}", {"email": MGR, "role": "MANAGER"})
    t0 = time.perf_counter()
    th = updates.thread("order", so, ENG, "en")
    assert len(th) == 300 and time.perf_counter() - t0 < 3.0, "a long thread should load in a few seconds"
    with SessionLocal() as db:
        for i in range(300):
            db.add(ItemBallDB(item_type="board_item", item_ref=f"vol{TAG}{i}", ball_with="vendor", ball_person="V", owner_email=MGR, history=[{"x": 1}],
                              escalation_level=0))
        db.commit()
    t0 = time.perf_counter()
    items = ball.list_open(include_derived=False)
    assert len(items) >= 300 and time.perf_counter() - t0 < 4.0
    t0 = time.perf_counter()
    ball.summary()
    assert time.perf_counter() - t0 < 6.0
    with SessionLocal() as db:
        db.query(ItemBallDB).filter(ItemBallDB.item_ref.like(f"vol{TAG}%")).delete(synchronize_session=False)
        db.commit()


def test_an_export_of_thousands_of_rows_finishes_and_is_capped():
    from app.services import exports

    rows = [{"a": f"row {i}", "b": i} for i in range(7000)]
    t0 = time.perf_counter()
    data = exports.to_xlsx("Big", [("a", "A"), ("b", "B")], rows)
    assert time.perf_counter() - t0 < 15
    assert len(list(load_workbook(io.BytesIO(data)).active.iter_rows(values_only=True))) == exports.MAX_ROWS + 1


# ------------------------------------------------------------------------ working-day arithmetic


def test_a_friday_afternoon_hand_over_does_not_look_two_days_late_on_monday_morning():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.BUSINESS_TZ)
    fri = workdays.as_utc(__import__("datetime").datetime(2026, 11, 6, 16, 30, tzinfo=tz))
    mon = workdays.as_utc(__import__("datetime").datetime(2026, 11, 9, 9, 30, tzinfo=tz))
    assert workdays.days_between(fri, mon) < 1.0
    tue = workdays.as_utc(__import__("datetime").datetime(2026, 11, 10, 9, 30, tzinfo=tz))
    assert 1.0 <= workdays.days_between(fri, tue) < 2.5


def test_the_clock_change_does_not_add_or_lose_a_working_day():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.BUSINESS_TZ)
    before = workdays.as_utc(datetime(2027, 3, 12, 12, 0, tzinfo=tz))             # a Friday before the spring change
    after = workdays.as_utc(datetime(2027, 3, 15, 12, 0, tzinfo=tz))              # the Monday after
    assert 0.9 <= workdays.days_between(before, after) <= 1.1


# ------------------------------------------------------------------------ the database matches the code


def test_a_fresh_database_built_by_the_migrations_has_every_table_and_column_the_code_uses(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    from app.core.database import Base

    root = Path(__file__).resolve().parent.parent
    url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    insp = sa.inspect(sa.create_engine(url))
    missing = []
    for table in ("packing_slips", "item_updates", "item_balls", "users", "engineering_notes", "vendor_jobs", "rfqs"):
        have = {c["name"] for c in insp.get_columns(table)}
        want = {c.name for c in Base.metadata.tables[table].columns}
        if want - have:
            missing.append((table, sorted(want - have)))
    assert missing == [], f"migrations leave out columns the models use: {missing}"
    assert any(i["unique"] and i["column_names"] == ["item_type", "item_ref"] for i in insp.get_indexes("item_balls"))


# ------------------------------------------------------------------------ the shapes the frontend reads


def test_the_response_shapes_the_frontend_reads_do_not_drift(client):
    so = _order()
    c = client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": f"@eng{TAG} hi"}).json()
    assert set(c) >= {"id", "item_type", "item_ref", "parent_id", "author_email", "author_name", "body", "removed", "mentions", "created_at", "edited_at", "mine", "unresolved"}
    t = client.get("/api/v1/updates", params={"item_type": "order", "item_ref": so}).json()[0]
    assert set(t) >= {"kind", "replies", "author_language", "translation", "needs_translation"} and t["kind"] == "comment"
    b = client.put("/api/v1/ball", json={"item_type": "order", "item_ref": so, "ball_with": "engineer", "ball_person": ENG, "owner": MGR}).json()
    assert set(b) >= {"item_type", "item_ref", "label", "owner_email", "owner_name", "ball_with", "ball_label", "ball_person", "ball_name", "note", "due_at",
                      "overdue", "since", "days_waiting", "level", "status", "resolved", "history", "derived"}
    s = client.get("/api/v1/ball/summary").json()
    assert set(s) >= {"open", "late", "escalated", "by_kind", "by_holder", "oldest", "rules"}
    mine = client.get("/api/v1/workspace/my/items").json()
    assert set(mine) >= {"ball", "mentions", "notes", "rfqs", "vendor_jobs", "drafts_waiting", "counts"}
    assert set(client.get("/api/v1/language/me").json()) == {"language", "options"}
    f = client.get("/api/v1/forms/templates").json()[0]
    assert set(f) >= {"id", "name", "enabled", "applies_to", "instructions", "fields"}
    assert {"key", "label", "type", "required"} <= set(f["fields"][0])
    assert set(client.get("/api/v1/exports").json()) >= {"lists", "formats"}
    assert set(client.get("/api/v1/carriers/rates").json()) >= {"lines", "stale_after_days", "needs_review", "live", "regions"}
    q = client.get("/api/v1/packing-slips/queue").json()
    assert set(q) == {"counts", "jobs"}
    pl = client.post("/api/v1/workspace/project-templates/wear-part-job/plan", json={"start": "2026-11-09"}).json()[0]
    assert set(pl) == {"project", "milestone", "due", "owner_role", "owner", "form"}

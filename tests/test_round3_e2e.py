"""Round 3: whole-life scenarios across the new features, ball edge cases, and a sweep for unprotected routes.
Test data only; the translation engine is a fake; no mail leaves and nothing touches monday."""

import io
import re
import threading
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemBallDB, NotificationDB, PackingSlipDB, StaffContactDB
from app.services import bridge
from app.services.coordination import ball, forms, projects, rfqs, updates, vendors
from tests.conftest import staff_token
from tests.test_coordination import board  # noqa: F401  (the projects-board fixture)

TAG = uuid.uuid4().hex[:5]
MGR, VIET, CHIN = (f"{n}{TAG}@alliedweldingsystems.com" for n in ("athish", "linh", "wei"))


class Engine:
    def __call__(self, text, source, target, actor):
        return SimpleNamespace(translation=f"[{target}] {text}", unsure_terms=[])


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, client):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bridge, "_ask", Engine())
    for email, role, lang, name in ((MGR, "MANAGER", "en", "Athish"), (VIET, "OPERATOR", "vi", "Phạm Linh"), (CHIN, "OPERATOR", "zh", "王伟")):
        staff_token(email, role)
        bridge.set_language(email, lang)
        with SessionLocal() as db:
            if db.get(StaffContactDB, email) is None:
                db.add(StaffContactDB(email=email, name=name, role_tag="staff", channels=["email"], active=True))
            db.commit()


def _order(po=None):
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    po = po or f"PO-{uuid.uuid4().hex[:6].upper()}"
    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Knauf Insulation", customer_po_number=po, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 2, "unit_price": 410}]))
    return po, str(out.sales_order_number)


def _alerts(prefix):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB).all() if n.dedup_key.startswith(prefix)]


def _days(monkeypatch, v):
    monkeypatch.setattr(ball, "_days", lambda since: v[0])


def _tok(email, role="OPERATOR"):
    return {"Authorization": f"Bearer {staff_token(email, role)}"}


# ------------------------------------------------------------ a job from customer PO to delivery


def test_a_china_made_job_from_customer_po_to_delivery(monkeypatch, client):
    po, so = _order()
    with SessionLocal() as db:                                                           # the slip was queued and made with the order
        assert db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).one().status == "READY"
    manager = {"email": MGR, "role": "MANAGER"}

    # the manager hands the design to the Vietnamese engineer, with a deadline
    b = ball.set_ball("order", so, manager, "engineer", VIET, owner=MGR, note="Check drawing rev C against the PO", due=str(date.today() + timedelta(days=5)))
    assert b["ball_name"] == "Phạm Linh" and b["days_waiting"] == 0
    assert any(n.recipients == [VIET] for n in _alerts("mention:"))                      # the engineer was told

    # the engineer answers in Vietnamese; the manager reads English, and the reply goes back in Vietnamese
    first = updates.post("order", so, "Em đã kiểm tra bản vẽ, cần xác nhận số lô nấu", {"email": VIET, "role": "OPERATOR"})
    updates.post("order", so, "Please send the heat number photo", manager, parent_id=first["id"])
    en = updates.thread("order", so, MGR, "en")
    note = next(c for c in en if c["id"] == first["id"])
    assert note["author_language"] == "vi" and note["translation"]["text"].startswith("[en] Em đã kiểm tra")
    vi = updates.thread("order", so, VIET, "vi")
    reply = next(c for c in vi if c["id"] == first["id"])["replies"][0]
    assert reply["translation"]["text"] == "[vi] Please send the heat number photo"

    # time passes with the ball on the engineer: reminder, then the owner, then leadership
    d = [2.0]
    _days(monkeypatch, d)
    ball.run()
    d[0] = 4.0
    ball.run()
    d[0] = 7.0
    ball.run()
    rid = _rows(so)[0].id
    kinds = sorted(n.dedup_key.rsplit(":", 1)[1] for n in _alerts(f"ball:{rid}:"))
    assert kinds == ["1", "2", "3"] and ball.get("order", so)["status"] == "leadership told"

    # the engineer finishes and hands it to the China vendor: the clock restarts and the owner is told to chase after two days
    d[0] = 0.0
    ball.set_ball("order", so, {"email": VIET, "role": "OPERATOR"}, "vendor", "KINGCERA", note="PO released, waiting for proforma")
    assert ball.get("order", so)["days_waiting"] == 0 and ball.get("order", so)["status"] == "on track"
    d[0] = 2.0
    ball.run()
    chase = [n for n in _alerts(f"ball:{rid}:") if n.dedup_key.endswith(":1")]
    assert len(chase) == 2 and any("follow up with KINGCERA" in n.title for n in chase)

    # everyone sees it where they look
    assert any(i["item_ref"] == so for i in ball.list_open("me", MGR))
    orders = list(load_workbook(io.BytesIO(client.get("/api/v1/exports/orders").content)).active.iter_rows(values_only=True))
    assert any(r[0] == po for r in orders[1:])

    # delivered
    assert ball.resolve("order", so, manager)["status"] == "done"
    assert so not in [i["item_ref"] for i in ball.list_open(include_derived=False)]
    assert [m["body"] for m in updates.thread("order", so, MGR, "en")][-1].startswith("Done:")


def _rows(so):
    with SessionLocal() as db:
        return db.query(ItemBallDB).filter(ItemBallDB.item_ref == so).all()


def test_the_engineer_cannot_submit_drawings_until_the_form_is_complete_and_can_read_it_in_his_language(board, client, monkeypatch):  # noqa: F811
    eng = {"email": VIET, "role": "OPERATOR"}
    translated = bridge.translate_many([forms.get("design-release")["instructions"]], "en", "vi")
    assert translated[0].startswith("[vi]")
    with pytest.raises(forms.FormError) as e:
        projects.submit(board["drawings"], [("rev.pdf", b"%PDF-1.4 x")], "", eng)
    assert "po_checked" in e.value.fields
    good = {"drawing_revision": "D-4410 Rev C", "po_checked": True, "mtr_checked": True, "customer_approval": "Approved in writing",
            "welding_procedure": "WPS attached", "special_requirements": "None"}
    sub = projects.submit(board["drawings"], [("rev.pdf", b"%PDF-1.4 x")], "", eng, answers=good)
    assert "D-4410 Rev C" in sub["note"]


# ------------------------------------------------------------ ball edge cases


@pytest.mark.parametrize("days,level", [(0, 0), (1.99, 0), (2.0, 1), (3.99, 1), (4.0, 2), (6.99, 2), (7.0, 3), (40, 3)])
def test_the_ladder_thresholds_are_exact(days, level):
    assert ball.level_for(days) == level
    assert ball.level_for(days, overdue=True) == max(level, 2)


def test_an_outsider_ball_with_no_owner_still_alerts_somebody_and_never_crashes(monkeypatch):
    _, so = _order()
    ball.set_ball("order", so, {"email": "ghost@nowhere.test", "role": "OPERATOR"}, "customer", "Knauf")
    with SessionLocal() as db:
        db.query(ItemBallDB).filter(ItemBallDB.item_ref == so).update({"owner_email": None})
        db.commit()
    _days(monkeypatch, [3.0])
    ball.run()                                                                           # no owner, no holder: falls back to leadership
    rid = _rows(so)[0].id
    assert len(_alerts(f"ball:{rid}:")) == 1


def test_two_runs_at_once_never_double_alert(monkeypatch):
    _, so = _order()
    ball.set_ball("order", so, {"email": MGR, "role": "MANAGER"}, "engineer", VIET, owner=MGR)
    _days(monkeypatch, [4.0])
    ts = [threading.Thread(target=ball.run) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rid = _rows(so)[0].id
    assert [n.dedup_key.rsplit(":", 1)[1] for n in _alerts(f"ball:{rid}:")] == ["2"]


def test_worked_out_balls_come_from_real_rfqs_and_vendor_jobs():
    r = rfqs.create({"customer": f"Vulcan{TAG}", "subject": "Chute liner", "contact_email": f"v{TAG}@x.test", "owner_email": MGR},
                    {"email": MGR, "role": "MANAGER"})
    job = vendors.create({"vendor": f"KINGCERA{TAG}", "description": "Cyclone", "po_number": f"P{TAG}", "ordered_on": str(date.today() - timedelta(days=21))},
                         {"email": MGR, "role": "MANAGER"})
    items = ball.list_open()
    rfq_row = next(i for i in items if i["item_type"] == "rfq" and i["item_ref"] == r["number"])
    job_row = next(i for i in items if i["item_type"] == "vendor_job" and i["item_ref"] == str(job["id"]))
    assert rfq_row["derived"] and rfq_row["ball_with"] == "us" and job_row["derived"] and job_row["ball_with"] == "vendor"
    assert job_row["days_waiting"] >= 10 and job_row["ball_name"] == f"KINGCERA{TAG}"
    ball.set_ball("vendor_job", str(job["id"]), {"email": MGR, "role": "MANAGER"}, "customer", "Knauf")      # a hand-set ball wins
    again = [i for i in ball.list_open() if i["item_type"] == "vendor_job" and i["item_ref"] == str(job["id"])]
    assert len(again) == 1 and again[0]["derived"] is False and again[0]["ball_with"] == "customer"


def test_the_summary_works_with_nothing_open(monkeypatch):
    monkeypatch.setattr(ball, "list_open", lambda *a, **k: [])
    s = ball.summary()
    assert s["open"] == 0 and s["by_kind"] == [] and s["oldest"] == [] and s["rules"]["nudge_days"] == 2


# ------------------------------------------------------------ every route needs a login


# These answer before login only because they check their own token after reading the request (a phone-call service,
# the public customer RFQ form) or are the landing page; each is proved below.
_PUBLIC = ("/api/v1/ai/calls", "/api/v1/ai/order-status", "/api/v1/rfqs/form", "/api/v1/ai/", "/api/v1/health", "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/webhooks", "/api/v1/docs", "/api/v1/openapi",
           "/openapi", "/docs", "/redoc", "/api/v1/actions/", "/api/v1/assistant/actions/link", "/api/v1/metrics", "/api/v1/mobile/")


def test_no_route_answers_without_a_login(auth_enforced, client):
    paths = client.app.openapi()["paths"]
    open_routes, checked = [], 0
    for path, ops in paths.items():
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        for m in ops:
            if m.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            r = client.request(m.upper(), concrete, json={} if m.upper() in ("POST", "PUT", "PATCH") else None)
            checked += 1
            if r.status_code not in (401, 403, 404, 405) and path != "/" and not any(path.startswith(p) for p in _PUBLIC):
                open_routes.append((m.upper(), path, r.status_code))
    assert checked > 150, f"the sweep should have visited the whole API (visited {checked})"
    assert open_routes == [], f"routes that answered without a login: {open_routes}"


def test_the_routes_that_check_their_own_token_refuse_a_real_request_without_one(auth_enforced, client):
    assert client.get("/api/v1/ai/order-status", params={"number": "PO-1"}).status_code in (401, 403)
    assert client.post("/api/v1/ai/calls", json={"transcript": "hello", "from_number": "+15555550100", "call_id": "c1"}).status_code in (401, 403)
    assert client.post("/api/v1/rfqs/form", json={"company": "X", "email": "a@b.co", "subject": "Y", "details": "Two liners please, urgent."}).status_code in (401, 403, 404)
    assert client.get("/").json()["status"] == "online"                                      # the landing page says nothing private
    for path in ("/api/v1/documents/samples", "/api/v1/chat/sources", "/api/v1/chat/templates"):
        assert client.get(path).status_code in (401, 403), path


# ------------------------------------------------------------ fixes from the independent review


def test_a_mention_works_straight_after_chinese_text_and_in_a_vietnamese_sentence():
    so = _order()[1]
    a = updates.post("order", so, "请通知@Athish确认", {"email": VIET, "role": "OPERATOR"})
    assert [m["email"] for m in a["mentions"]] == [MGR]
    b = updates.post("order", so, "@Phạm Linh xin kiểm tra", {"email": MGR, "role": "MANAGER"})
    assert [m["email"] for m in b["mentions"]] == [VIET]


def test_live_exchange_rates_are_fetched_even_when_the_server_just_started(monkeypatch):
    import time

    from app.services import fx

    monkeypatch.setattr(settings, "FX_LIVE_RATES", True)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)                                # a box that booted 100 seconds ago
    fx._cache.update(at=float("-inf"), cad=None, cny=None, date=None)
    calls = []
    monkeypatch.setattr(fx, "_fetch", lambda: calls.append(1) or {"cad": 2.0, "cny": 10.0, "date": "2026-11-07"})
    assert fx.cad_to_usd() == 0.5 and calls == [1]
    fx._cache.update(at=float("-inf"), cad=None, cny=None, date=None)


def test_a_packing_slip_that_fails_again_after_a_person_dealt_with_it_alerts_again(monkeypatch):
    from app.services import packing_slip_queue as q

    ids = iter([1000, 1001])
    monkeypatch.setattr(q, "_now", lambda: __import__("datetime").datetime.fromtimestamp(next(ids), __import__("datetime").timezone.utc))
    keys = []
    from app.services.coordination import notify

    monkeypatch.setattr(notify, "notify", lambda kind, key, *a, **k: keys.append(key))
    job = {"sales_order_number": "777", "customer_po_number": "PO-7", "attempts": 4, "last_error": "x"}
    q._alert(job)
    q._alert(job)
    assert len(set(keys)) == 2


def test_spreadsheets_survive_control_characters_and_hostile_column_titles():
    from app.services import exports

    cols = [("a", '=HYPERLINK("http://evil","x")'), ("b", "Note")]
    rows = [{"a": "ok", "b": "bad\x01char\x0bhere\x1f"}]
    data = exports.to_xlsx("T", cols, rows)
    sheet = list(load_workbook(io.BytesIO(data)).active.iter_rows(values_only=True))
    assert sheet[0][0].startswith("'=") and sheet[1][1] == "badcharhere"
    assert "'=HYPERLINK" in exports.to_csv(cols, rows).decode("utf-8-sig")


def test_a_naive_database_time_is_shown_as_the_same_moment_as_an_aware_one():
    from datetime import datetime, timezone

    from app.services import exports

    assert exports._cell(datetime(2026, 11, 7, 12, 0)) == exports._cell(datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))


def test_the_pdf_says_when_it_shows_only_part_of_a_long_list():
    from pypdf import PdfReader

    from app.services import exports

    pdf = exports.to_pdf("Big", [("n", "N")], [{"n": i} for i in range(1600)])
    assert "first 1,500 of 1,600 rows" in (PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or "")


def test_a_client_cannot_dodge_the_design_form_by_naming_a_different_one(board):  # noqa: F811
    forms.save({"name": "Trivial", "applies_to": ["nothing-matches"], "fields": [{"label": "Hi", "type": "text"}]})
    eng = {"email": VIET, "role": "OPERATOR"}
    with pytest.raises(forms.FormError) as e:
        projects.submit(board["drawings"], [("rev.pdf", b"%PDF-1.4 x")], "", eng, template_id="trivial", answers={"hi": "x"})
    assert "po_checked" in e.value.fields                                                       # the design form still applied


def test_the_starter_form_does_not_land_on_milestones_that_take_no_documents():
    assert forms.for_milestone("Design drawings") is not None
    for name in ("Vendor PO released", "Freight booked", "Delivered to customer", "Customer kickoff"):
        assert forms.for_milestone(name) is None, name


def test_a_form_attached_to_a_project_template_milestone_is_required_there():
    from app.services.coordination import project_templates as pt

    forms.save({"name": "Photo check", "applies_to": ["zzz"], "fields": [{"label": "Photos match the order", "type": "yesno", "required": True, "must_be_yes": True}]})
    pt.save({"name": "With photos", "milestones": [{"name": "Pre-ship photos", "offset_days": 1, "form": "photo-check"}]})
    assert forms.for_milestone("pre-ship PHOTOS")["id"] == "photo-check"


def test_applies_to_given_as_one_string_is_split_into_words_not_letters():
    t = forms.save({"name": "Comma list", "applies_to": "weld, grind", "fields": [{"label": "Done", "type": "yesno"}]})
    assert t["applies_to"] == ["weld", "grind"]


@pytest.mark.parametrize("payload", [
    {"item_type": "order", "item_ref": "X", "ball_with": "us", "owner": 5}, {"item_type": "order", "item_ref": "X", "ball_with": "us", "due": 20261101},
    {"item_type": "order", "item_ref": "X", "ball_with": "us", "note": ["a"]}, {"item_type": "order", "item_ref": "X", "ball_with": ["us"]},
    {"item_type": "order", "item_ref": "X", "ball_with": "vendor", "ball_person": {"a": 1}}])
def test_wrongly_shaped_requests_get_a_plain_400_not_a_500(payload, client):
    so = _order()[1]
    assert client.put("/api/v1/ball", json={**payload, "item_ref": so}).status_code == 400


def test_other_wrongly_shaped_requests_are_plain_errors_too(client):
    so = _order()[1]
    assert client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": "hi", "parent_id": "abc"}).status_code == 400
    assert client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": "hi", "mentions": "a@b.co"}).status_code == 400
    assert client.post("/api/v1/forms/templates", json={"name": "N", "fields": [{"label": "A", "type": "text", "min_chars": "x"}]}).status_code == 400
    assert client.post("/api/v1/forms/templates", json={"name": "N", "fields": ["nope"]}).status_code == 400
    assert client.post("/api/v1/workspace/project-templates", json={"name": "N", "milestones": ["nope"]}).status_code == 400
    assert client.post("/api/v1/workspace/project-templates/wear-part-job/plan", json={"start": "2026-11-09", "owners": ["x"]}).status_code == 200


def test_a_weekend_start_gives_each_milestone_its_own_working_day():
    from app.services.coordination import project_templates as pt

    sat = pt.plan("wear-part-job", "2026-11-07")
    mon = pt.plan("wear-part-job", "2026-11-09")
    assert [p["due"] for p in sat] == [p["due"] for p in mon]                                    # Saturday behaves like the Monday after


def test_two_balls_for_one_item_cannot_exist():
    from sqlalchemy.exc import IntegrityError

    so = _order()[1]
    ball.set_ball("order", so, {"email": MGR, "role": "MANAGER"}, "us", owner=MGR)
    with SessionLocal() as db:
        db.add(ItemBallDB(item_type="order", item_ref=so, ball_with="us", history=[], escalation_level=0))
        with pytest.raises(IntegrityError):
            db.commit()


def test_a_future_checked_date_on_a_carrier_price_is_refused():
    from app.services import carrier_rates

    with pytest.raises(carrier_rates.RateCardError, match="future"):
        carrier_rates.update("tforce-ltl", {"as_of": str(date.today() + timedelta(days=3))})


def test_a_busy_translation_engine_is_asked_a_limited_number_of_times_per_read(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "_ask", lambda *a: calls.append(1) or (_ for _ in ()).throw(RuntimeError("busy")))
    so = _order()[1]
    for i in range(15):
        updates.post("order", so, f"评论 {i}", {"email": CHIN, "role": "OPERATOR"})
    updates.thread("order", so, MGR, "en")
    assert len(calls) <= 8

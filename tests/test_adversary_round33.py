"""Round 33 (adversary): re-verify the demo-room fixes by crawling every GET route as OPERATOR and VIEWER looking for a seeded cost/margin/PO price, plus simulate and the backup tile. Temp DB only; nothing sent.
xfail(strict=False, reason="ADV33-Tn ...") marks holes."""
import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.coordination import jobs, purchase_orders, rfqs
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss33-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
EXPENSE, MARGIN, UNIT = "7123.45", "29.37", "4321.09"


def H(role):
    return {"Authorization": "Bearer " + staff_token(f"c33-{role.lower()}-{TAG}@alliedweldingsystems.com", role)}


@pytest.fixture(scope="module")
def seeded():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"X{tag}", customer_po_number=f"CPX{tag}", sales_order_number="1" + "".join(str(ord(c) % 10) for c in tag[:5]), customer_id=f"C{tag}", customer_name=f"CrawlCo {tag}",
                       total_revenue=10000, total_expense=float(EXPENSE), gross_margin_pct=float(MARGIN), line_items=[{"description": "x", "quantity": 1, "unit_price": 10000, "unit_cost": float(UNIT)}]))
        db.commit()
        so = db.query(OrderDB).filter(OrderDB.order_id == f"X{tag}").one().sales_order_number
    r = rfqs.create({"customer": f"CrawlCo {tag}", "subject": f"crawl {tag}"}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], f"CrawlVendor {tag}", BOSS, lines=[{"description": "plate", "quantity": 1, "unit_cost": float(UNIT)}])
    return {"so": so, "po": po["id"], "job": j["id"], "jobref": j.get("ref") or j["id"], "rfq": r["id"], "rfqno": r["number"], "tag": tag}


def _routes():
    from app.main import app

    paths = app.openapi().get("paths", {})
    return sorted(p for p, ops in paths.items() if "get" in ops and p.startswith("/api/v1") and not any(k in p for k in ("/stream", "/events", "/health/live")))


def _fill(path, s):
    def sub(m):
        n = m.group(1).lower()
        if "so" in n or "sales" in n:
            return str(s["so"])
        if n in ("po_id", "po") or n.startswith("po"):
            return str(s["po"])
        if "job" in n:
            return str(s["jobref"])
        if "rfq" in n:
            return str(s["rfqno"])
        return "1"
    return re.sub(r"\{([^}:]+)(?::[^}]*)?\}", sub, path)


def test_no_get_route_shows_cost_margin_or_po_prices_to_a_role_that_may_not_see_them(auth_enforced, seeded):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    leaks, ok200 = [], 0
    seen = c.get("/api/v1/orders", params={"q": seeded["tag"]}, headers=H("MANAGER")).text
    assert EXPENSE in seen, "sanity: a manager must see the seeded cost, or this crawl proves nothing"
    for path in _routes():
        url = _fill(path, seeded)
        for role, hidden in (("OPERATOR", (EXPENSE, MARGIN)), ("VIEWER", (EXPENSE, MARGIN, UNIT, "4321.1"))):
            try:
                r = c.get(url, headers=H(role))
            except Exception:  # noqa: BLE001
                continue
            if r.status_code != 200 or "json" not in r.headers.get("content-type", "") and "text" not in r.headers.get("content-type", ""):
                continue
            ok200 += 1
            body = r.text
            for h in hidden:
                if h in body:
                    leaks.append((role, path, h))
    assert ok200 > 60, f"only {ok200} routes answered 200: the crawl is too thin"
    assert not leaks, leaks


def test_the_practice_order_route_is_refused_for_every_role_and_creates_nothing(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    body = {"sender_email": "buyer@owenscorning.com", "sender_name": "x", "subject": "PO-1", "body_text": "PO 99887 please make 1 x AR400 plate"}
    with SessionLocal() as db:
        before = db.query(OrderDB).count()
    for role in ("VIEWER", "OPERATOR", "MANAGER", "ADMIN"):
        assert c.post("/api/v1/inbox/simulate", json=body, headers=H(role)).status_code in (401, 403)
    assert c.post("/api/v1/inbox/simulate", json=body).status_code in (401, 403)
    with SessionLocal() as db:
        assert db.query(OrderDB).count() == before


def test_other_routes_that_make_real_records_from_text_are_not_open_to_a_view_only_sign_in(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    for method, path, body in (("post", "/api/v1/orders/process", {}), ("post", "/api/v1/csv/import", {"import_type": "ITEMS", "csv_text": "a"}), ("post", "/api/v1/webhooks/monday/simulate-trigger", None),
                               ("post", "/api/v1/inbox/simulate", {}), ("post", "/api/v1/sage/export/batch", None)):
        r = c.request(method, path, json=body, headers=H("VIEWER"))
        assert r.status_code in (401, 403, 405, 422), (path, r.status_code)


def test_the_backup_tile_is_neutral_after_a_restart_and_red_only_when_failed():
    from datetime import datetime, timezone

    from app.services import backups

    backups.last.clear()
    t = backups.plain_text()
    assert t["state"] in ("ok", "stale", "none") and "Error" not in json.dumps(t) and "C:\\" not in json.dumps(t)
    backups.last.update(at=datetime.now(timezone.utc).isoformat(), ok=False, error=r"PermissionError: C:\Users\x\allied.db", kept_for_retry=True)
    try:
        t = backups.plain_text()
        st = backups.status()
    finally:
        backups.last.clear()
    assert t["state"] == "failed" and "allied.db" not in t["text"] and "PermissionError" not in json.dumps(st["last_run"]) and "allied.db" not in json.dumps(st["last_run"])

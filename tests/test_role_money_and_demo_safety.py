"""The server hides cost and margin from the roles that should not see them (null, not removed), the practice-order route refuses unless it is switched on, and the backup tile is neutral after a restart.
Temporary database."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services import backups
from app.services.coordination import jobs, purchase_orders, rfqs
from tests.conftest import staff_token

BOSS = {"email": "boss.money@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def H(role):
    return {"Authorization": "Bearer " + staff_token(f"money-{role.lower()}@alliedweldingsystems.com", role)}


def _order():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"M{tag}", customer_po_number=f"CPM{tag}", sales_order_number=f"1{tag[:5].upper()}".replace("A", "1").replace("B", "2").replace("C", "3").replace("D", "4").replace("E", "5").replace("F", "6"),
                       customer_id=f"C{tag}", customer_name=f"MoneyCo {tag}", total_revenue=1000, total_expense=700, gross_margin_pct=30, line_items=[{"description": "x", "quantity": 1, "unit_price": 1000, "unit_cost": 700}]))
        db.commit()
        o = db.query(OrderDB).filter(OrderDB.order_id == f"M{tag}").one()
        return o.sales_order_number, tag


def test_orders_show_cost_and_margin_to_managers_only_and_null_to_everyone_else(client):
    so, tag = _order()
    for role, sees in (("MANAGER", True), ("ADMIN", True), ("OPERATOR", False), ("VIEWER", False)):
        page = client.get("/api/v1/orders", params={"q": tag}, headers=H(role)).json()
        row = next(r for r in page["items"] if r["customer_po_number"] == f"CPM{tag}")
        assert (row["total_expense"] is not None) == sees and (row["gross_margin_pct"] is not None) == sees, role
        assert row["total_revenue"] is not None
        one = client.get(f"/api/v1/orders/{so}", headers=H(role)).json()
        assert (one["total_expense"] is not None) == sees and (one["gross_margin_pct"] is not None) == sees, role
        for ln in one.get("line_items") or []:
            assert (ln.get("unit_cost") is not None) == sees, role
        assert "total_expense" in one and "gross_margin_pct" in one                       # null, not removed: the shapes hold


def _po():
    r = rfqs.create({"customer": "MoneyCo", "subject": f"po money {uuid.uuid4().hex[:5]}"}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], f"MoneyVendor {uuid.uuid4().hex[:4]}", BOSS, lines=[{"description": "plate", "quantity": 2, "unit_cost": 300}])
    return j, po


def test_purchase_order_prices_and_totals_are_null_for_a_view_only_sign_in_and_kept_for_staff(client):
    j, po = _po()
    for role, sees in (("MANAGER", True), ("OPERATOR", True), ("VIEWER", False)):
        one = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=H(role)).json()
        assert (one["total"] is not None) == sees and all((ln.get("unit_cost") is not None) == sees for ln in one["lines"]), role
        assert one["lines"][0]["description"] == "plate" and "total" in one
        listed = client.get(f"/api/v1/jobs/{j['id']}/purchase-orders", headers=H(role)).json()
        assert (listed[0]["total"] is not None) == sees, role
        job = client.get(f"/api/v1/jobs/{j['id']}", headers=H(role)).json()
        assert not any("total" in p and (p["total"] is not None) != sees for p in job["purchase_orders"]), role                  # the job page carries no prices at all, or null ones


def test_the_practice_order_route_refuses_unless_it_is_switched_on_and_makes_nothing(client, monkeypatch):
    with SessionLocal() as db:
        before = db.query(OrderDB).count()
    body = {"sender_email": "buyer@owenscorning.com", "subject": "Order", "body_text": "PO 12345 please make 1 x AR400 plate"}
    r = client.post("/api/v1/inbox/simulate", json=body, headers=H("OPERATOR"))
    assert r.status_code == 403 and "real sales order" in r.json()["detail"] and "INBOX_SIMULATION_ENABLED" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.query(OrderDB).count() == before
    assert settings.INBOX_SIMULATION_ENABLED is False
    assert client.post("/api/v1/inbox/simulate", json=body, headers=H("VIEWER")).status_code == 403


def test_the_backup_tile_is_neutral_right_after_a_restart_and_the_raw_error_stays_in_details(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    fresh = tmp_path / f"allied-{now - timedelta(hours=3):%Y%m%d-%H%M%S}.db"
    fresh.write_bytes(b"x" * 10)
    monkeypatch.setattr(backups, "last", {})
    assert backups.plain_text()["state"] == "ok"
    fresh.unlink()
    old = tmp_path / f"allied-{now - timedelta(hours=200):%Y%m%d-%H%M%S}.db"
    old.write_bytes(b"x" * 10)
    t = backups.plain_text()
    assert t["state"] == "stale" and "hours ago" in t["text"]
    old.unlink()
    assert backups.plain_text()["state"] == "none"
    monkeypatch.setattr(backups, "last", {"at": now.isoformat(), "ok": False, "error": "PermissionError: [WinError 5] Access is denied: 'D:\\\\x'"})
    st = backups.status()
    assert "error" not in st["last_run"] and "Access is denied" in backups.plain_text()["details"] and "Access is denied" not in backups.plain_text()["text"]


def test_a_failed_attempt_only_counts_while_no_newer_backup_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    now = datetime.now(timezone.utc)
    failed_at = now - timedelta(hours=5)
    monkeypatch.setattr(backups, "last", {"at": failed_at.isoformat(), "ok": False, "error": "boom"})
    older = tmp_path / f"allied-{now - timedelta(hours=19):%Y%m%d-%H%M%S}.db"
    older.write_bytes(b"x")
    assert backups.plain_text()["state"] == "failed" and backups.status()["last_run"]["failed"] is True      # the newest file is older than the failed attempt
    newer = tmp_path / f"allied-{now - timedelta(hours=1):%Y%m%d-%H%M%S}.db"
    newer.write_bytes(b"x")
    t = backups.plain_text()
    assert t["state"] == "ok" and "worked" in t["text"] and backups.status()["last_run"]["failed"] is False   # a later backup file exists: the failure is history

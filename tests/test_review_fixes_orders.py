"""Regression tests for the order, auth, board-reader and Sage export review fixes."""

import csv
import io
import uuid
from decimal import Decimal

import pytest

from app.config import Settings, settings
from app.core.database import SessionLocal
from app.core.security import create_access_token
from app.models.db_models import MondayEventDB, MondayItemStateDB, OrderDB
from app.models.orders import InboundOrderRequest, OrderLineItem
from app.services.database_service import DatabaseService


def _bearer(role: str, email: str = "reviewer@allied.com") -> dict:
    token = create_access_token({"sub": email, "name": "R", "role": role, "facility": "HQ"})
    return {"Authorization": f"Bearer {token}"}


def _order_request(po: str) -> InboundOrderRequest:
    return InboundOrderRequest(
        customer_name="Owens Corning",
        customer_po_number=po,
        shipping_address="1 Plant Rd, Waxahachie, TX 75165",
        line_items=[OrderLineItem(raw_item_text="AR400 wear plate", quantity=Decimal("2"), unit_price=Decimal("0"))],
    )


_ORDER_BODY = {
    "customer_name": "Owens Corning",
    "customer_po_number": "PO-AUTH-1",
    "shipping_address": "1 Plant Rd, Waxahachie, TX 75165",
    "line_items": [{"raw_item_text": "AR400 wear plate", "quantity": "1", "unit_price": "0"}],
}


# -- 1. order endpoints require authentication --------------------------------


def test_order_endpoints_reject_anonymous_callers(auth_enforced, client):
    assert client.post("/api/v1/orders/process", json=_ORDER_BODY).status_code == 401
    assert client.post("/api/v1/orders/dry-run", json=_ORDER_BODY).status_code == 401
    assert client.get("/api/v1/orders/123/packing-slip").status_code == 401


def test_viewer_may_dry_run_but_not_process(auth_enforced, client):
    viewer = _bearer("VIEWER")
    assert client.post("/api/v1/orders/process", json=_ORDER_BODY, headers=viewer).status_code == 403
    assert client.post("/api/v1/orders/dry-run", json=_ORDER_BODY, headers=viewer).status_code == 200
    assert client.get("/api/v1/orders/999999999/packing-slip", headers=viewer).status_code == 404


# -- 2. packing slip path traversal -------------------------------------------


@pytest.mark.parametrize("so_number", ["..%5C..%5Csecret", "SO-..%5Cx", "1.2", "a%20b", "1%0A"])
def test_packing_slip_rejects_anything_but_a_document_number(client, so_number):
    assert client.get(f"/api/v1/orders/{so_number}/packing-slip").status_code == 400


def test_packing_slip_serves_a_real_slip(client):
    folder = settings.OUTPUT_DIR / "packing_slips"
    folder.mkdir(parents=True, exist_ok=True)
    so = str(uuid.uuid4().int % 10**9)
    (folder / f"packing_slip_SO_{so}.pdf").write_bytes(b"%PDF-1.4 test")
    for path_so in (so, f"SO-{so}"):
        resp = client.get(f"/api/v1/orders/{path_so}/packing-slip")
        assert resp.status_code == 200 and resp.content.startswith(b"%PDF")


# -- 3. production refuses shipped secrets; admin password not reset ---------


def test_production_refuses_default_or_empty_secrets():
    defaults = Settings(_env_file=None, APP_ENV="production")
    with pytest.raises(RuntimeError) as exc:
        defaults.assert_production_secrets()
    for name in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        assert name in str(exc.value)

    blank = Settings(_env_file=None, APP_ENV="production", SECRET_KEY="", JWT_SECRET_KEY="x" * 40, ADMIN_PASSWORD="y" * 20)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        blank.assert_production_secrets()

    strong = Settings(
        _env_file=None, APP_ENV="production",
        SECRET_KEY="a" * 40, JWT_SECRET_KEY="b" * 40, ADMIN_PASSWORD="c" * 20,
    )
    strong.assert_production_secrets()
    # Development keeps working with the shipped values.
    Settings(_env_file=None, APP_ENV="development").assert_production_secrets()


def test_seed_admin_does_not_overwrite_an_existing_password_in_production(monkeypatch):
    from app.core.security import seed_admin_user, verify_password

    seed_admin_user()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "a-different-password-in-the-env")
    seed_admin_user()
    with SessionLocal() as db:
        admin = DatabaseService.get_user_by_email(db, settings.ADMIN_EMAIL)
        assert not verify_password("a-different-password-in-the-env", admin.hashed_password)


# -- 4. disabled accounts lose access immediately -----------------------------


def test_disabled_account_token_is_rejected(auth_enforced, client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.post("/api/v1/orders/dry-run", json=_ORDER_BODY, headers=headers).status_code == 200
    with SessionLocal() as db:
        DatabaseService.get_user_by_email(db, settings.ADMIN_EMAIL).is_active = False
        db.commit()
    try:
        assert client.post("/api/v1/orders/dry-run", json=_ORDER_BODY, headers=headers).status_code == 403
        assert client.get("/api/v1/auth/profile", headers=headers).status_code == 403
    finally:
        with SessionLocal() as db:
            DatabaseService.get_user_by_email(db, settings.ADMIN_EMAIL).is_active = True
            db.commit()


# -- 5. PO-less emails get distinct synthetic POs ------------------------------


def test_po_less_emails_do_not_share_a_fallback_po():
    from app.services.inbound_email_daemon import InboundEmailMessage, inbound_email_daemon

    def email(message_id):
        return InboundEmailMessage(
            email_id=message_id, sender_email="buyer@knauf.com",
            subject="Need parts", body_text="Please send wear plates.",
        )

    first = inbound_email_daemon._extract_po_number(email("<abc123@mail.knauf.com>"))
    second = inbound_email_daemon._extract_po_number(email("<xyz789@mail.knauf.com>"))
    again = inbound_email_daemon._extract_po_number(email("<abc123@mail.knauf.com>"))
    assert first != second
    assert first == again  # a redelivered email stays idempotent
    assert first.startswith("PO-NOPO-")


# -- 6. duplicate-PO check normalises both sides ------------------------------


def test_duplicate_check_matches_po_regardless_of_case_and_spaces():
    from app.services.order_orchestrator import OrderOrchestrator

    raw = f"  po-norm-{uuid.uuid4().hex[:6]} "
    with SessionLocal() as db:
        db.add(OrderDB(order_id=uuid.uuid4().hex[:8].upper(), customer_po_number=raw,
                       sales_order_number=str(uuid.uuid4().int % 10**9), customer_id="C1", customer_name="X"))
        db.commit()
    found = OrderOrchestrator._find_existing_order(raw.strip().upper())
    assert found and found["customer_po_number"] == raw


# -- 7. a cache failure does not skip persistence -----------------------------


def test_order_is_persisted_even_when_the_cache_fails(monkeypatch):
    from app.core.container import get_orchestrator
    from app.services import order_orchestrator

    def broken(_order):
        raise RuntimeError("cache down")

    monkeypatch.setattr(order_orchestrator.agent_memory_cache, "cache_order", broken)
    po = f"PO-CACHE-{uuid.uuid4().hex[:6].upper()}"
    result = get_orchestrator().process_inbound_order(_order_request(po))
    with SessionLocal() as db:
        row = db.query(OrderDB).filter(OrderDB.customer_po_number == po).first()
        assert row is not None and row.sales_order_number == result.sales_order_number


# -- 8 / 9. board reader baseline and per-change atomicity --------------------


class _Board:
    live = True

    def __init__(self, items):
        self.items = items  # {item_id: (name, label)}

    def fetch_board_items(self, board_id, limit=500):
        return {"data": {"boards": [{"items_page": {"items": [
            {"id": str(i), "name": name, "column_values": [{"id": "status", "text": label}]}
            for i, (name, label) in self.items.items()
        ]}}]}}


@pytest.fixture
def reader_board(monkeypatch):
    from app.services import monday_events

    board = str(uuid.uuid4().int % 10**9)
    monkeypatch.setattr(settings, "MONDAY_BOARD_ID", board)
    monkeypatch.setattr(settings, "MONDAY_BOT_USER_ID", None)
    calls = []
    for action in (monday_events.CREATE_ORDER, monday_events.RELEASE_PO, monday_events.DISPATCH):
        monkeypatch.setitem(monday_events._HANDLERS, action, lambda e: calls.append(e["pulseId"]) or {})
    return board, calls


def test_first_live_read_fires_nothing_even_after_webhooks_saw_the_board(reader_board):
    from app.services import monday_events

    board, calls = reader_board
    a, b = (int(uuid.uuid4().int % 10**9) for _ in range(2))
    # A webhook (or simulation) records state for this board before the reader
    # has ever read it; that used to count as the reader's baseline.
    monday_events.record_event({
        "type": "update_column_value", "triggerUuid": uuid.uuid4().hex, "boardId": int(board),
        "pulseId": a, "columnId": "status", "value": {"label": {"text": "Working on it"}},
    })
    calls.clear()
    fake = _Board({a: ("Job A", "Shipped"), b: ("Job B", "Ready for Sage")})
    first = monday_events.read_boards(fake)
    assert first["changes_queued"] == 0 and calls == []
    with SessionLocal() as db:
        assert db.get(MondayItemStateDB, monday_events._baseline_marker_id(board)) is not None

    fake.items[a] = ("Job A", "Approved for Production")
    assert monday_events.read_boards(fake)["changes_queued"] == 1 and calls == [a]


def test_a_failed_change_is_detected_again_next_read(reader_board, monkeypatch):
    from app.services import monday_events

    board, calls = reader_board
    ids = [int(uuid.uuid4().int % 10**9) for _ in range(3)]
    fake = _Board({i: (f"Job {i}", "Working on it") for i in ids})
    monday_events.read_boards(fake)
    for i in ids:
        fake.items[i] = (f"Job {i}", "Shipped")

    real = monday_events._record_read_change
    broken = ids[0]

    def flaky(event, key, change):
        if int(change["item"]["id"]) == broken:
            raise RuntimeError("database hiccup")
        return real(event, key, change)

    monkeypatch.setattr(monday_events, "_record_read_change", flaky)
    assert monday_events.read_boards(fake)["changes_queued"] == 2
    assert broken not in calls and set(calls) == set(ids[1:])

    monkeypatch.setattr(monday_events, "_record_read_change", real)
    assert monday_events.read_boards(fake)["changes_queued"] == 1
    assert calls.count(broken) == 1
    with SessionLocal() as db:
        events = db.query(MondayEventDB).filter(MondayEventDB.item_id == str(broken)).count()
    assert events == 1


# -- 10 / 11. Sage export marking and rounding --------------------------------


def test_export_contains_exactly_the_orders_it_claimed(monkeypatch):
    from app.core.container import get_orchestrator
    from app.services.sage_export_service import sage_export_service

    po = f"PO-CLAIM-{uuid.uuid4().hex[:6].upper()}"
    so = get_orchestrator().process_inbound_order(_order_request(po)).sales_order_number
    stale = [o for o in sage_export_service._get_orders(only_new=True) if o.get("sales_order_number") == so]
    assert stale

    # An overlapping export claims the order between our read and our mark.
    assert sage_export_service.claim_for_export([so]) == [so]
    assert sage_export_service.claim_for_export([so]) == []

    monkeypatch.setattr(sage_export_service, "_get_orders", lambda facility=None, only_new=False: stale)
    content = sage_export_service.generate_sales_orders_csv(only_new=True, mark_exported=True)
    assert so not in [r["SO Number"] for r in csv.DictReader(io.StringIO(content))]


def test_amount_is_computed_from_the_rounded_unit_price(monkeypatch):
    from app.services.sage_export_service import money_line, sage_export_service

    assert money_line(3, 0.125) == (Decimal("0.13"), Decimal("0.39"))
    assert money_line(Decimal("2"), "10.005") == (Decimal("10.01"), Decimal("20.02"))

    order = {
        "customer_id": "C1", "sales_order_number": "SO-ROUND", "po_date": "2026-10-27",
        "customer_name": "Acme", "shipping_address": "1 Rd, Austin, TX 78701",
        "line_items": [{"item_id": "PLATE-1", "quantity": 3, "unit_price": 0.125}],
    }
    monkeypatch.setattr(sage_export_service, "_get_orders", lambda facility=None, only_new=False: [order])
    row = next(csv.DictReader(io.StringIO(sage_export_service.generate_sales_orders_csv())))
    assert (row["Unit Price"], row["Amount"]) == ("0.13", "0.39")

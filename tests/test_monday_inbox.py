"""monday.com webhook inbox: authentication, dedup, routing, retries, write-back."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayEventDB, OrderDB
from app.services import monday_events


def _status_event(label, item_id=None, column="status", **extra):
    return {
        "type": "update_column_value",
        "triggerUuid": uuid.uuid4().hex,
        "boardId": 555,
        "pulseId": item_id or int(uuid.uuid4().int % 10**9),
        "pulseName": extra.pop("pulseName", "Acme - PO-1 - Plate"),
        "columnId": column,
        "value": {"label": {"index": 1, "text": label}},
        "userId": extra.pop("userId", 42),
        **extra,
    }


@pytest.fixture
def board_settings():
    saved = {k: getattr(settings, k) for k in (
        "MONDAY_BOARD_ID", "MONDAY_BOT_USER_ID", "MONDAY_WEBHOOK_SECRET",
        "MONDAY_WEBHOOK_TOKEN", "APP_ENV", "WEBHOOK_MAX_ATTEMPTS",
    )}
    settings.MONDAY_BOARD_ID = None
    settings.MONDAY_BOT_USER_ID = None
    settings.MONDAY_WEBHOOK_SECRET = ""
    settings.MONDAY_WEBHOOK_TOKEN = ""
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


# -- routing -----------------------------------------------------------------


def test_unmapped_columns_and_labels_never_create_orders(board_settings):
    # The old handler turned any non-ship/approve change into a new order.
    assert monday_events.decide(_status_event("Working on it"))[0] is None
    assert monday_events.decide(_status_event("Shipped", column="text0"))[0] is None
    assert monday_events.decide({"type": "create_update", "pulseId": 1, "boardId": 1})[0] is None


def test_exact_labels_route_and_substrings_do_not(board_settings):
    assert monday_events.decide(_status_event("Shipped"))[0] == monday_events.DISPATCH
    assert monday_events.decide(_status_event("approved for production"))[0] == monday_events.RELEASE_PO
    assert monday_events.decide(_status_event("Ready for Sage"))[0] == monday_events.CREATE_ORDER
    # "SHIP" in "Not shipped yet" used to dispatch a shipment.
    assert monday_events.decide(_status_event("Not shipped yet"))[0] is None


def test_board_filter_and_own_write_backs_are_ignored(board_settings):
    settings.MONDAY_BOARD_ID = "999"
    assert monday_events.decide(_status_event("Shipped"))[0] is None
    settings.MONDAY_BOARD_ID = "555"
    settings.MONDAY_BOT_USER_ID = 7
    assert monday_events.decide(_status_event("Shipped", userId=7))[0] is None
    assert monday_events.decide(_status_event("Shipped", userId=8))[0] == monday_events.DISPATCH


# -- ingress -----------------------------------------------------------------


def test_redelivery_is_acknowledged_without_reprocessing(client, board_settings):
    event = _status_event("Working on it")
    first = client.post("/api/v1/webhooks/monday", json={"event": event})
    second = client.post("/api/v1/webhooks/monday", json={"event": event})
    assert first.json()["status"] == "enqueued"
    assert second.json() == {"status": "duplicate", "event_id": first.json()["event_id"]}
    with SessionLocal() as db:
        rows = db.query(MondayEventDB).filter(MondayEventDB.item_id == str(event["pulseId"])).all()
    assert len(rows) == 1 and rows[0].status == "IGNORED"


def test_url_token_mode(client, board_settings):
    settings.MONDAY_WEBHOOK_TOKEN = "s3cret-token"
    body = {"challenge": "abc"}
    assert client.post("/api/v1/webhooks/monday", json=body).status_code == 401
    assert client.post("/api/v1/webhooks/monday?token=wrong", json=body).status_code == 401
    ok = client.post("/api/v1/webhooks/monday?token=s3cret-token", json=body)
    assert ok.status_code == 200 and ok.json() == {"challenge": "abc"}


def test_app_jwt_mode(client, board_settings):
    settings.MONDAY_WEBHOOK_SECRET = "app-signing-secret-of-sufficient-length"
    good = jwt.encode(
        {"accountId": 1, "userId": 2, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.MONDAY_WEBHOOK_SECRET,
        algorithm="HS256",
    )
    forged = jwt.encode({"accountId": 1}, "some-other-secret-of-sufficient-len", algorithm="HS256")
    body = {"challenge": "xyz"}
    assert client.post("/api/v1/webhooks/monday", json=body, headers={"Authorization": forged}).status_code == 401
    assert client.post("/api/v1/webhooks/monday", json=body, headers={"Authorization": good}).json() == body


def test_production_without_credentials_fails_closed(client, board_settings):
    settings.APP_ENV = "production"
    assert client.post("/api/v1/webhooks/monday", json={"challenge": "x"}).status_code == 503


# -- handlers ----------------------------------------------------------------


def test_create_order_requires_customer_and_po(board_settings):
    event = _status_event("Ready for Sage", pulseName="just a name")
    event_id, _ = monday_events.record_event(event)
    assert monday_events.process_event(event_id) == "FAILED"
    with SessionLocal() as db:
        assert "missing" in db.get(MondayEventDB, event_id).last_error


def test_create_order_links_item_and_dispatch_runs_once(board_settings):
    po = f"PO-{uuid.uuid4().hex[:6].upper()}"
    item_id = int(uuid.uuid4().int % 10**9)
    name = f"Owens Corning - {po} - AR400 wear plate"

    created, _ = monday_events.record_event(_status_event("Ready for Sage", item_id, pulseName=name))
    assert monday_events.process_event(created) == "DONE"
    with SessionLocal() as db:
        order = db.query(OrderDB).filter(OrderDB.customer_po_number == po).one()
        assert order.monday_item_id == str(item_id)

    ship1, _ = monday_events.record_event(_status_event("Shipped", item_id, pulseName=name))
    ship2, _ = monday_events.record_event(_status_event("Shipped", item_id, pulseName=name))
    assert monday_events.process_event(ship1) == "DONE"
    assert monday_events.process_event(ship2) == "DONE"
    with SessionLocal() as db:
        second = db.get(MondayEventDB, ship2).result
    assert "already dispatched" in second["skipped"]


def test_transient_failure_retries_then_goes_dead(board_settings, monkeypatch):
    settings.WEBHOOK_MAX_ATTEMPTS = 2

    def boom(_event):
        raise ConnectionError("Sage host unreachable")

    monkeypatch.setitem(monday_events._HANDLERS, monday_events.DISPATCH, boom)
    event_id, _ = monday_events.record_event(_status_event("Shipped"))
    assert monday_events.process_event(event_id) == "RETRY"
    # Make the retry due, as the sweeper would find it.
    with SessionLocal() as db:
        db.query(MondayEventDB).filter(MondayEventDB.id == event_id).update(
            {MondayEventDB.next_attempt_at: datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        db.commit()
    assert monday_events.process_event(event_id) == "DEAD"
    assert monday_events.replay(event_id) is True
    with SessionLocal() as db:
        assert db.get(MondayEventDB, event_id).status == "PENDING"


def test_console_endpoints(client, board_settings):
    resp = client.get("/api/v1/webhooks/monday/events?limit=5")
    assert resp.status_code == 200
    assert "needs_attention" in resp.json()["stats"]
    setup = client.get("/api/v1/webhooks/monday/setup").json()
    assert {c["key"] for c in setup["checks"]} >= {"api_key", "auth", "board"}


def test_dispatch_finds_the_real_order_even_after_a_restart(board_settings):
    """Packing slips used to say 'Valued Allied Customer' because the lookup key never matched."""
    from decimal import Decimal

    from app.core.container import get_orchestrator
    from app.models.orders import InboundOrderRequest, OrderLineItem
    from app.services.agent_memory_cache import agent_memory_cache
    from app.services.logistics_dispatcher import LogisticsDispatchRequest, logistics_dispatcher

    result = get_orchestrator().process_inbound_order(
        InboundOrderRequest(
            customer_name="Owens Corning",
            customer_po_number=f"PO-DSP-{uuid.uuid4().hex[:6].upper()}",
            shipping_address="1 Plant Rd, Waxahachie, TX 75165",
            line_items=[OrderLineItem(raw_item_text="AR400 wear plate", quantity=Decimal("1"), unit_price=Decimal("0"))],
        )
    )
    # Simulate a restart: the in-memory cache forgets the order.
    for key in (result.order_id, result.sales_order_number, result.customer_po_number):
        agent_memory_cache._orders.pop(key, None)

    dispatched = logistics_dispatcher.dispatch_shipment(
        LogisticsDispatchRequest(sales_order_number=result.sales_order_number)
    )
    assert dispatched.customer_name != "Valued Allied Customer"


# -- review findings ---------------------------------------------------------


def test_done_events_cannot_be_replayed(board_settings):
    event_id, _ = monday_events.record_event(_status_event("Working on it"))
    monday_events.process_event(event_id)
    with SessionLocal() as db:
        db.query(MondayEventDB).filter(MondayEventDB.id == event_id).update({MondayEventDB.status: "DONE"})
        db.commit()
    assert monday_events.replay(event_id) is False


def test_jwt_without_expiry_is_rejected(client, board_settings):
    settings.MONDAY_WEBHOOK_SECRET = "app-signing-secret-of-sufficient-length"
    no_exp = jwt.encode({"accountId": 1}, settings.MONDAY_WEBHOOK_SECRET, algorithm="HS256")
    resp = client.post("/api/v1/webhooks/monday", json={"challenge": "x"}, headers={"Authorization": no_exp})
    assert resp.status_code == 401


def test_second_trigger_on_a_busy_item_waits_its_turn(board_settings, monkeypatch):
    item_id = int(uuid.uuid4().int % 10**9)
    first, _ = monday_events.record_event(_status_event("Shipped", item_id))
    second, _ = monday_events.record_event(_status_event("Dispatched", item_id))
    with SessionLocal() as db:
        db.query(MondayEventDB).filter(MondayEventDB.id == first).update(
            {MondayEventDB.status: "PROCESSING", MondayEventDB.attempts: 1,
             MondayEventDB.next_attempt_at: datetime.now(timezone.utc) + timedelta(minutes=5)}
        )
        db.commit()
    called = []
    monkeypatch.setitem(monday_events._HANDLERS, monday_events.DISPATCH, lambda e: called.append(e) or {})
    assert monday_events.process_event(second) == "RETRY"
    assert not called
    with SessionLocal() as db:
        assert db.get(MondayEventDB, second).attempts == 0  # backing off costs no attempt


def test_event_that_always_dies_mid_run_ends_dead(board_settings):
    settings.WEBHOOK_MAX_ATTEMPTS = 2
    event_id, _ = monday_events.record_event(_status_event("Working on it"))
    with SessionLocal() as db:
        db.query(MondayEventDB).filter(MondayEventDB.id == event_id).update(
            {MondayEventDB.status: "PROCESSING", MondayEventDB.attempts: 2,
             MondayEventDB.next_attempt_at: datetime.now(timezone.utc) - timedelta(minutes=1)}
        )
        db.commit()
    monday_events.sweep()
    with SessionLocal() as db:
        assert db.get(MondayEventDB, event_id).status == "DEAD"


# -- board reader (monday holds data; Allied's system runs the automations) -----


class _Board:
    """Stands in for the monday client: a board whose statuses tests can change."""

    live = True

    def __init__(self, board_id, items):
        self.board_id = board_id
        self.items = items  # {item_id: (name, label)}

    def fetch_board_items(self, board_id, limit=500):
        return {"data": {"boards": [{"items_page": {"items": [
            {"id": str(i), "name": name, "column_values": [{"id": "status", "text": label}]}
            for i, (name, label) in self.items.items()
        ]}}]}}


def test_board_reader_baselines_first_then_queues_only_changes(board_settings, monkeypatch):
    board = str(uuid.uuid4().int % 10**9)
    settings.MONDAY_BOARD_ID = board
    calls = []
    monkeypatch.setitem(monday_events._HANDLERS, monday_events.DISPATCH, lambda e: calls.append(e["pulseId"]) or {})
    a, b = int(uuid.uuid4().int % 10**9), int(uuid.uuid4().int % 10**9)
    fake = _Board(board, {a: ("Job A", "Shipped"), b: ("Job B", "Working on it")})

    first = monday_events.read_boards(fake)
    assert first["items_seen"] == 2 and first["changes_queued"] == 0  # existing jobs never fire
    assert calls == []

    fake.items[b] = ("Job B", "Shipped")
    second = monday_events.read_boards(fake)
    assert second["changes_queued"] == 1 and calls == [b]

    third = monday_events.read_boards(fake)  # nothing changed since
    assert third["changes_queued"] == 0 and calls == [b]


def test_board_reader_does_not_repeat_a_change_a_webhook_already_delivered(client, board_settings):
    board = str(uuid.uuid4().int % 10**9)
    settings.MONDAY_BOARD_ID = board
    item = int(uuid.uuid4().int % 10**9)
    fake = _Board(board, {item: ("Job C", "Working on it")})
    monday_events.read_boards(fake)

    fake.items[item] = ("Job C", "Stuck")
    event = _status_event("Stuck", item, boardId=int(board))
    client.post("/api/v1/webhooks/monday", json={"event": event})
    assert monday_events.read_boards(fake)["changes_queued"] == 0


def test_board_reader_is_idle_in_simulation(board_settings):
    settings.MONDAY_BOARD_ID = "123"

    class Sim(_Board):
        live = False

    assert "simulation" in monday_events.read_boards(Sim("123", {}))["skipped"]

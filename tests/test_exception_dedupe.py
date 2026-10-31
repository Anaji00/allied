"""One open problem per thing that is wrong, however often it is detected."""

import uuid
from decimal import Decimal

from app.core.database import SessionLocal
from app.models.db_models import MondayEventDB
from app.services import monday_events
from app.services.exception_queue_service import (
    ExceptionItem,
    ResolveExceptionRequest,
    exception_queue_service,
)
from app.services.reconciliation_service import (
    VendorInvoice,
    VendorInvoiceLine,
    reconciliation_service,
)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _open_for(reference: str):
    return [e for e in exception_queue_service.list_exceptions(status_filter="OPEN") if e.reference_id == reference]


def _invoice(number: str) -> VendorInvoice:
    return VendorInvoice(
        invoice_number=number,
        vendor_name="ZIBO",
        po_number="PO-ZIBO-4412",
        lines=[
            VendorInvoiceLine(
                item_id="HOSE-CERAMIC-4IN",
                quantity_billed=Decimal("2"),
                unit_cost_billed=Decimal("420.00"),
                total_amount=Decimal("840.00"),
            )
        ],
        freight_billed=Decimal("85.00"),
        total_amount_billed=Decimal("925.00"),
    )


def test_rechecking_a_duplicate_bill_keeps_one_open_problem():
    number = _uid("INV-REDUP")
    assert reconciliation_service.reconcile_vendor_invoice(_invoice(number)).reconciliation_status == "RECONCILED_APPROVED"
    for _ in range(3):
        res = reconciliation_service.reconcile_vendor_invoice(_invoice(number))
        assert res.reconciliation_status == "DUPLICATE_INVOICE"

    open_items = _open_for(number)
    assert len(open_items) == 1, [e.exception_id for e in open_items]
    assert open_items[0].title.startswith("Invoice already approved once")


def test_repeat_refreshes_the_open_problem_and_keeps_its_id():
    ref = _uid("PO-REP")
    first = exception_queue_service.add_exception(
        ExceptionItem(exception_type="UNMATCHED_SKU", title="Old", description="first look",
                      reference_id=ref, vendor_or_customer="Owens Corning")
    )
    again = exception_queue_service.add_exception(
        ExceptionItem(exception_type="UNMATCHED_SKU", title="New", description="second look",
                      reference_id=ref, vendor_or_customer="Owens Corning")
    )
    assert again.exception_id == first.exception_id
    (only,) = _open_for(ref)
    assert only.description == "second look"


def test_a_resolved_problem_that_comes_back_is_raised_again():
    ref = _uid("PO-BACK")
    first = exception_queue_service.add_exception(
        ExceptionItem(exception_type="UNMATCHED_SKU", title="t", description="d",
                      reference_id=ref, vendor_or_customer="KWS")
    )
    exception_queue_service.resolve_exception(
        first.exception_id, ResolveExceptionRequest(action_type="MANUALLY_RESOLVED")
    )
    back = exception_queue_service.add_exception(
        ExceptionItem(exception_type="UNMATCHED_SKU", title="t", description="d",
                      reference_id=ref, vendor_or_customer="KWS")
    )
    assert back.exception_id != first.exception_id
    assert [e.exception_id for e in _open_for(ref)] == [back.exception_id]


def test_different_parties_or_types_stay_separate():
    ref = _uid("PO-SEP")
    for vendor, kind in (("ZIBO", "UNMATCHED_SKU"), ("KWS", "UNMATCHED_SKU"), ("ZIBO", "STALE_VENDOR_PRICE")):
        exception_queue_service.add_exception(
            ExceptionItem(exception_type=kind, title="t", description="d", reference_id=ref, vendor_or_customer=vendor)
        )
    assert len(_open_for(ref)) == 3


def _failed_event() -> int:
    event_id, _ = monday_events.record_event({
        "type": "update_column_value",
        "triggerUuid": uuid.uuid4().hex,
        "boardId": 555,
        "pulseId": int(uuid.uuid4().int % 10**9),
        "pulseName": "Acme - PO-1 - Plate",
        "columnId": "status",
        "value": {"label": {"index": 1, "text": "Working on it"}},
    })
    with SessionLocal() as db:
        db.query(MondayEventDB).filter(MondayEventDB.id == event_id).update(
            {MondayEventDB.status: "FAILED", MondayEventDB.last_error: "test failure"}
        )
        db.commit()
    return event_id


def _stuck_event_problem(event_id: int) -> ExceptionItem:
    return exception_queue_service.add_exception(
        ExceptionItem(
            exception_type="MONDAY_EVENT_FAILED",
            severity="HIGH",
            title=f"monday automation stopped on event {event_id}",
            description="test failure",
            reference_id=f"MONDAY-EVT-{event_id}",
            vendor_or_customer="Acme - PO-1 - Plate",
            available_actions=[{"action": "REPLAY_MONDAY_EVENT", "label": "Replay after fixing the item"}],
        )
    )


def _event_status(event_id: int) -> str:
    with SessionLocal() as db:
        return db.query(MondayEventDB.status).filter(MondayEventDB.id == event_id).scalar()


def test_replay_action_on_the_problem_requeues_the_event():
    # It used to record a note and leave both the event and the problem as they were.
    event_id = _failed_event()
    problem = _stuck_event_problem(event_id)

    res = exception_queue_service.resolve_exception(
        problem.exception_id, ResolveExceptionRequest(action_type="REPLAY_MONDAY_EVENT")
    )
    assert res["exception_status"] == "RESOLVED", res
    assert _event_status(event_id) == "PENDING"


def test_replay_action_leaves_the_problem_open_when_nothing_replays():
    problem = _stuck_event_problem(999_999_999)  # no such event
    res = exception_queue_service.resolve_exception(
        problem.exception_id, ResolveExceptionRequest(action_type="REPLAY_MONDAY_EVENT")
    )
    assert res["exception_status"] == "OPEN"
    assert res["action_completed"] is False


def test_replaying_from_the_event_log_closes_the_problem(client, admin_token):
    event_id = _failed_event()
    _stuck_event_problem(event_id)

    res = client.post(
        f"/api/v1/webhooks/monday/events/{event_id}/replay",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200, res.text
    assert _open_for(f"MONDAY-EVT-{event_id}") == []

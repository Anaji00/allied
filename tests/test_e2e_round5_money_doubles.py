"""Round 5: the order desk, where a double costs money.

Owens Corning's PO arriving twice at once, two people exporting to Sage at once, one order
dispatched from two screens at once, a held order approved twice, and NIBROCK's overbilled
invoice submitted again after a restart. The property: each happens exactly once.
Temporary database and catalog copies; nothing real is touched."""

import threading
import uuid
from decimal import Decimal

import pytest

from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.models.orders import InboundOrderRequest

LINES = [{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 2, "unit_price": 410},
         {"raw_item_text": "Middle Brace Bars C1045 Steel", "quantity": 4, "unit_price": 38}]


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables and loads the catalogs, as on a real start."""


def _po():
    return f"PO-{uuid.uuid4().hex[:6].upper()}"


def _together(n, fn):
    """Runs fn(i) on n threads released at the same instant; returns results and errors."""
    out, errors, barrier = [None] * n, [], threading.Barrier(n)

    def run(i):
        barrier.wait()
        try:
            out[i] = fn(i)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return out, errors


def _orders_for(po):
    with SessionLocal() as db:
        return db.query(OrderDB).filter(OrderDB.customer_po_number == po).all()


def test_the_same_customer_po_arriving_five_times_at_once_makes_one_order():
    from app.services.order_orchestrator import OrderOrchestrator

    po = _po()
    names = ["Owens Corning", "Owens Corning Insulation", "OWENS CORNING", "Owens Corning", "Owens-Corning"]
    out, errors = _together(5, lambda i: OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name=names[i], customer_po_number=po, shipping_address="Edmonton, AB", line_items=LINES)))
    assert not errors, errors
    assert len({r.sales_order_number for r in out}) == 1          # everyone gets the same SO back
    assert len(_orders_for(po)) == 1


def test_one_order_dispatched_from_two_screens_at_once_ships_once():
    from app.services.logistics_dispatcher import AlreadyDispatchedError, LogisticsDispatchRequest, logistics_dispatcher
    from app.services.order_orchestrator import OrderOrchestrator

    po = _po()
    so = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=po, shipping_address="Edmonton, AB", line_items=LINES)).sales_order_number
    req = LogisticsDispatchRequest(sales_order_number=f"SO-{so}", carrier_name="Old Dominion", tracking_number=f"ODFL-{uuid.uuid4().hex[:6]}",
                                   recipient_email="receiving@owenscorning.com")
    out, errors = _together(4, lambda i: logistics_dispatcher.dispatch_shipment(req))
    shipped = [r for r in out if r is not None]
    assert len(shipped) == 1, f"{len(shipped)} dispatches went out"
    assert all(isinstance(e, AlreadyDispatchedError) for e in errors) and len(errors) == 3


def test_two_people_exporting_to_sage_at_once_put_each_order_in_one_file():
    from app.services.order_orchestrator import OrderOrchestrator
    from app.services.sage_export_service import sage_export_service

    pos = [_po() for _ in range(3)]
    sos = [OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=p, shipping_address="Edmonton, AB", line_items=LINES)).sales_order_number
        for p in pos]
    out, errors = _together(2, lambda i: sage_export_service.export_sales_orders(only_new=True, mark_exported=True))
    assert not errors, errors
    exported = [so for _, sos_in_file in out for so in sos_in_file if so.replace("SO-", "") in {str(s) for s in sos}]
    assert sorted(exported) == sorted(set(exported)) and len(set(exported)) == 3


def test_a_held_order_approved_by_two_people_at_once_is_staged_once():
    from app.services.exception_queue_service import ExceptionItem, ResolveExceptionRequest, exception_queue_service

    item = exception_queue_service.add_exception(ExceptionItem(
        exception_type="ORDER_EXCEPTION", severity="MEDIUM", title="Check order email: PO 77120 liners",
        description="Quantity unclear.", reference_id=_po(), vendor_or_customer="Owens Corning",
        available_actions=[{"action": "APPROVE_AND_STAGE", "label": "Approve & Stage Order"},
                           {"action": "MANUALLY_RESOLVED", "label": "Resolved by hand"}]))
    eid = getattr(item, "exception_id", None) or item
    req = ResolveExceptionRequest(action_type="MANUALLY_RESOLVED", resolution_notes="Checked with the customer.")
    out, errors = _together(5, lambda i: exception_queue_service.resolve_exception(eid, req, f"p{i}@allied.test"))
    assert len([r for r in out if r]) == 1 and len(errors) == 4
    assert all("already" in str(e) for e in errors)


def test_nibrock_resubmitting_an_overbilled_invoice_after_a_restart_is_not_disputed_twice(monkeypatch):
    from app.services import reconciliation_service as rs
    from app.services.reconciliation_service import VendorInvoice, VendorInvoiceLine

    disputes = []
    real = rs.ReconciliationService._dispatch_dispute

    def counting(self, vendor, invoice_number, draft, skip_if_attempted=False, already_disputed=False):
        out = real(self, vendor, invoice_number, draft, skip_if_attempted, already_disputed)
        if out is not None or (draft and not already_disputed and not skip_if_attempted):
            disputes.append(invoice_number)
        return out

    monkeypatch.setattr(rs.ReconciliationService, "_dispatch_dispute", counting)
    number = f"INV-NIB-{uuid.uuid4().hex[:5]}"
    bill = VendorInvoice(invoice_number=number, vendor_name="NIBROCK", po_number="PO-NIBROCK-9921",
                         lines=[VendorInvoiceLine(item_id="PLATE-300XL-38", quantity_billed=Decimal("4"),
                                                  unit_cost_billed=Decimal("128.50"), total_amount=Decimal("514.00"))],
                         freight_billed=Decimal("195.00"), total_amount_billed=Decimal("709.00"))
    first = rs.ReconciliationService().reconcile_vendor_invoice(bill)
    assert first.reconciliation_status == "DISCREPANCY_FLAGGED"
    after_restart = rs.ReconciliationService()                     # a fresh process: its memory is empty
    again = after_restart.reconcile_vendor_invoice(bill)
    assert again.reconciliation_status == "DISCREPANCY_FLAGGED"
    from app.services.outbound_email_service import outbound_email_service

    letters = [m for m in outbound_email_service.get_outbox(500) if number in (m.get("subject") or "")]
    assert len(letters) <= 1, f"{len(letters)} dispute letters for one invoice"

"""The system uses Allied's words: a PO is what Allied issues to a VENDOR; customers send RFQs and, on accepting a quote, an order
with their own reference number; a sales order (SO) is internal and its number is never shown to a customer. So nothing a
customer reads calls their document a PO, and the AI is told the same words. Test data only."""

import re
import uuid

import pytest

from app.models.orders import InboundOrderRequest
from app.services.ai import features
from app.services.assistant import agent
from app.services.order_orchestrator import OrderOrchestrator

PO_WORD = re.compile(r"\bP\.?O\.?\b|purchase order", re.IGNORECASE)


def test_the_ai_is_told_what_a_po_an_rfq_and_a_sales_order_are():
    for text in (features._COMPANY, " ".join(m["content"] for m in agent._system({"role": "OPERATOR"}))):
        assert "VENDOR" in text and "RFQs" in text and "INTERNAL" in text and "customer reference" in text and "never a PO" in text


def _order():
    ref = f"REF-{uuid.uuid4().hex[:6].upper()}"
    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=ref, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return ref, str(out.sales_order_number)


def test_nothing_a_customer_reads_calls_their_document_a_po_or_shows_our_sales_order_number(client):
    from pypdf import PdfReader

    from app.services.inbound_email_daemon import inbound_email_daemon
    from app.models.orders import OrderLineItem
    from app.services.logistics_dispatcher import LogisticsDispatchRequest, logistics_dispatcher

    ref, so = _order()
    slip = client.get(f"/api/v1/orders/{so}/packing-slip")
    text = " ".join(p.extract_text() or "" for p in PdfReader(__import__("io").BytesIO(slip.content)).pages)
    assert ref in text and "Your Reference" in text and not PO_WORD.search(text.replace("P.O. BOX", ""))      # (Allied's own mailing address says P.O. Box)
    ack = inbound_email_daemon._generate_order_acknowledgment("Owens Corning", ref, f"SO-{so}",
                                                              [OrderLineItem(raw_item_text="Screen", quantity=2, unit_price=5)], "a@b.com")
    assert ref in ack and not PO_WORD.search(ack) and so not in ack
    mail = logistics_dispatcher.dispatch_shipment(LogisticsDispatchRequest(
        sales_order_number=f"SO-{so}", carrier_name="Old Dominion", tracking_number=f"ODFL-{uuid.uuid4().hex[:6]}",
        recipient_email="r@owenscorning.com")).customer_notification_email
    assert ref in mail and so not in mail and f"your reference {ref}" in mail
    assert not re.search(r"\byour PO\b", mail, re.IGNORECASE)


def test_the_automatic_replies_ask_for_a_reference_not_a_po():
    from app.services.coordination import secretary

    src = open(secretary.__file__, encoding="utf-8").read()
    assert "order reference number you gave us" in src and "Could you send the PO number" not in src
    from app.services import exception_queue_service

    assert "confirm your order reference number" in open(exception_queue_service.__file__, encoding="utf-8").read()

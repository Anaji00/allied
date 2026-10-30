"""Tests for blind-shipping sanitization, margin assertions, and packing slip compliance."""

from decimal import Decimal
from pathlib import Path
import pytest

from app.config import settings
from app.models.orders import (
    CustomerDetails,
    InboundOrderRequest,
    MasterOrder,
    OrderArchetype,
    OrderLineItem,
    OrderStatus,
    ShippingDetails,
)
from app.services.order_orchestrator import OrderOrchestrator
from app.services.packing_slips import PackingSlipGenerator


@pytest.fixture
def orchestrator():
    return OrderOrchestrator()


@pytest.fixture
def packing_slip_gen(tmp_path):
    return PackingSlipGenerator(output_dir=tmp_path)


def _make_master_order(
    revenue_per_unit: Decimal = Decimal("5000.00"),
    cost_per_unit: Decimal = Decimal("1500.00"),
    qty: Decimal = Decimal("2.0"),
) -> MasterOrder:
    """Helper to build a MasterOrder for compliance testing."""
    return MasterOrder(
        order_id="COMP-001",
        customer_po_number="TEST-PO-123",
        customer=CustomerDetails(
            customer_id="10221",
            customer_name="Owens Corning Edmonton",
            billing_address="INVOICES@OWENSCORNING.COM",
            payment_terms="Net 150 Days",
        ),
        shipping=ShippingDetails(
            recipient_name="OWENS CORNING- EDMONTON",
            shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1, Canada",
            blind_shipping_required=True,
        ),
        line_items=[
            OrderLineItem(
                item_id="OC755507",
                raw_item_text="Hammermill Screens",
                description="2-1/2\" DIA HAMMERMILL SCREEN WITH FRAMES",
                quantity=qty,
                unit_price=revenue_per_unit,
                unit_cost=cost_per_unit,
                preferred_vendor="NIBROCK",
            )
        ],
        archetype=OrderArchetype.BLIND_DROPSHIP,
    )


def test_gross_margin_above_threshold():
    """Verifies margin calculation for healthy margins."""
    order = _make_master_order(
        revenue_per_unit=Decimal("3772.91"),
        cost_per_unit=Decimal("1785.00"),
        qty=Decimal("2.0"),
    )
    # Revenue: 7545.82, Expense: 3570.00, Margin: 52.7%
    assert order.gross_margin_pct > Decimal(str(settings.MIN_GROSS_MARGIN_PCT))


def test_gross_margin_below_threshold():
    """Verifies detection of margin below configured minimum."""
    order = _make_master_order(
        revenue_per_unit=Decimal("100.00"),
        cost_per_unit=Decimal("95.00"),
        qty=Decimal("1.0"),
    )
    # Revenue: 100, Expense: 95, Margin: 5%
    assert order.gross_margin_pct < Decimal(str(settings.MIN_GROSS_MARGIN_PCT))


def test_zero_revenue_margin():
    """Verifies zero-revenue edge case does not divide by zero."""
    order = _make_master_order(
        revenue_per_unit=Decimal("0.00"),
        cost_per_unit=Decimal("100.00"),
        qty=Decimal("1.0"),
    )
    assert order.gross_margin_pct == Decimal("0.00")


def test_packing_slip_pdf_generation(packing_slip_gen):
    """Tests that a PDF file is produced for a valid order."""
    order = _make_master_order()
    pdf_path = packing_slip_gen.generate_pdf(order, sales_order_number="12999")

    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"
    assert pdf_path.stat().st_size > 500  # Not an empty stub


def test_packing_slip_contains_no_cost_data(packing_slip_gen):
    """Verifies the PDF content does not leak vendor cost or margin data."""
    order = _make_master_order()
    pdf_path = packing_slip_gen.generate_pdf(order, sales_order_number="13000")

    # Read PDF text to verify no cost leakage
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    full_text = ""
    for page in reader.pages:
        text = page.extract_text() or ""
        full_text += text

    full_text_upper = full_text.upper()

    # Vendor costs and names must NOT appear on packing slip
    assert "1785" not in full_text, "Vendor unit cost leaked into packing slip"
    assert "3570" not in full_text, "Vendor total cost leaked into packing slip"
    assert "NIBROCK" not in full_text_upper, "Vendor name leaked into packing slip"
    assert "MARGIN" not in full_text_upper, "Margin data leaked into packing slip"

    # Customer PO and Allied branding MUST appear
    assert "TEST-PO-123" in full_text, "Customer PO reference missing from packing slip"
    assert "ALLIED" in full_text_upper, "Allied branding missing from packing slip"


def test_blind_shipping_flag_propagation(orchestrator):
    """Verifies that blind-ship flag flows through orchestration into generated POs."""
    req = InboundOrderRequest(
        customer_name="OC-Edmonton",
        customer_po_number="BLIND-TEST-001",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1, Canada",
        line_items=[
            OrderLineItem(
                raw_item_text="Hammermill Screens",
                quantity=Decimal("1.0"),
                unit_price=Decimal("3772.91"),
                unit_cost=Decimal("1785.00"),
            )
        ],
        blind_shipping=True,
    )
    result = orchestrator.process_inbound_order(req)

    assert result.status == OrderStatus.SYNCED_TO_SAGE
    assert "NIBROCK" in result.purchase_orders

    # Verify the SDK writer logged the PO with blind_shipment = True
    po_logs = [
        t for t in orchestrator.sdk_writer.transaction_log
        if t["type"] == "PURCHASE_ORDER"
    ]
    assert len(po_logs) >= 1
    assert po_logs[-1]["blind_shipment"] is True

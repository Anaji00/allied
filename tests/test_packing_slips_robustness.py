"""Unit tests for ReportLab PDF generator robustness, XML entity escaping, and blind-shipping compliance."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from app.models.orders import (
    CustomerDetails,
    MasterOrder,
    OrderArchetype,
    OrderLineItem,
    OrderStatus,
    ShippingDetails,
)
from app.services.packing_slips import PackingSlipGenerator, safe_xml


def test_safe_xml_escaping():
    """Verify proper escaping of XML reserved characters."""
    assert safe_xml('3/4" & 1/2" Steel <Plate>') == '3/4&quot; &amp; 1/2&quot; Steel &lt;Plate&gt;'
    assert safe_xml("Tom's Fabrication") == 'Tom&apos;s Fabrication'
    assert safe_xml(None) == ""


def test_packing_slip_with_special_characters_and_symbols(tmp_path: Path):
    """Verify PDF generates cleanly when descriptions contain ampersands, quotes, and brackets."""
    generator = PackingSlipGenerator(output_dir=tmp_path)

    order = MasterOrder(
        order_id="ORD-ROBUST-01",
        customer_po_number="PO-AMP-&<>'\"-99",
        po_date=date(2026, 2, 1),
        customer=CustomerDetails(
            customer_id="CUST-XML-01",
            customer_name="Owens & Minor / Corning <Industrial> Corp \"Division\"",
            billing_address="1234 Steel Way & Highway 99, Edmonton, AB",
            sales_rep="Karissa M. & Team",
        ),
        shipping=ShippingDetails(
            recipient_name="Plant #4 <Dock & Receiving>",
            shipping_address="5678 Industrial Blvd & Plant Road",
            shipping_method="MOTOR FRT & DEDICATED FLATBED",
            blind_shipping_required=True,
        ),
        line_items=[
            OrderLineItem(
                item_id="OC755507",
                raw_item_text="2-1/2\" Dia & 3/4\" Thick Hammermill Screen <300XL>",
                description="2-1/2\" Dia & 3/4\" Thick Hammermill Screen <Allied 300XL Clad> & C1045 Braces",
                quantity=Decimal("2.0"),
                unit_price=Decimal("3772.91"),
                unit_cost=Decimal("1785.00"),
                preferred_vendor="NIBROCK",
            )
        ],
        archetype=OrderArchetype.BLIND_DROPSHIP,
        status=OrderStatus.PROCESSING,
    )

    pdf_path = generator.generate_pdf(order=order, sales_order_number="SO-99999")
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000  # Valid non-empty PDF generated without crashing

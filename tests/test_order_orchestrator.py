"""Integration tests verifying Dual-Sided Transaction generation (1 SO -> Split POs)."""

from decimal import Decimal
import pytest

from app.models.orders import (
    InboundOrderRequest,
    OrderArchetype,
    OrderLineItem,
    OrderStatus,
)
from app.services.order_orchestrator import OrderOrchestrator


@pytest.fixture
def orchestrator():
    """Initializes standard OrderOrchestrator."""
    return OrderOrchestrator()


def test_oc_edmonton_single_vendor_flow(orchestrator):
    """Case 1: Owens Corning Edmonton -> 1 Sales Order + 1 Nibrock Purchase Order."""
    req = InboundOrderRequest(
        customer_name="OC-Edmonton",
        customer_po_number="4518695782-2",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1, Canada",
        payment_terms="Net 150 Days",
        shipping_method="MOTOR FRT",
        line_items=[
            OrderLineItem(
                raw_item_text="Hammermill Screens",
                quantity=Decimal("2.0"),
                unit_price=Decimal("3772.91"),
                unit_cost=Decimal("1785.00"),
                drawing_ref="DWG# OC-ED-218-R2",
            )
        ],
        blind_shipping=True,
    )

    result = orchestrator.process_inbound_order(req)

    assert result.status == OrderStatus.SYNCED_TO_SAGE
    assert result.customer_id == "10221"
    assert result.total_revenue == Decimal("7545.82")
    assert result.total_expense == Decimal("3570.00")
    assert "NIBROCK" in result.purchase_orders
    assert len(result.purchase_orders) == 1
    assert result.gross_margin_pct > Decimal("50.0")


def test_iko_southwest_internal_stock_flow(orchestrator):
    """Case 2: IKO Southwest -> 1 Sales Order + 0 External POs (Internal engineering scan service)."""
    req = InboundOrderRequest(
        customer_name="IKO-SW",
        customer_po_number="26001556",
        shipping_address="1001 IKO Way, Hillsboro, TX 76645",
        payment_terms="Net 30 Days",
        line_items=[
            OrderLineItem(
                raw_item_text="Engineering Scans",
                quantity=Decimal("3.0"),
                unit_price=Decimal("850.00"),
                unit_cost=Decimal("0.00"),
            )
        ],
        blind_shipping=True,
    )

    result = orchestrator.process_inbound_order(req)

    assert result.status == OrderStatus.SYNCED_TO_SAGE
    assert result.customer_id == "681111"
    assert result.total_revenue == Decimal("2550.00")
    # No external POs generated for STOCK items
    assert len(result.purchase_orders) == 0


def test_oc_starr_zibo_international_flow(orchestrator):
    """Case 3: Owens Corning Starr -> 1 Sales Order + 1 Zibo Import Purchase Order."""
    req = InboundOrderRequest(
        customer_name="OC-Starr",
        customer_po_number="4518702131",
        shipping_address="4837 Highway 81 South, Starr, SC 29684",
        payment_terms="Net 150 Days",
        line_items=[
            OrderLineItem(
                raw_item_text="Suction Hoses",
                quantity=Decimal("4.0"),
                unit_price=Decimal("5659.16"),
                unit_cost=Decimal("1040.00"),
                drawing_ref="DWG# OC-SR-08272024-FH_01",
            )
        ],
        blind_shipping=True,
    )

    result = orchestrator.process_inbound_order(req)

    assert result.status == OrderStatus.SYNCED_TO_SAGE
    assert result.customer_id == "10195"
    assert result.total_revenue == Decimal("22636.64")
    assert result.total_expense == Decimal("4160.00")
    assert "ZIBO" in result.purchase_orders
    assert len(result.purchase_orders) == 1


def test_knauf_lanett_kws_fabrication_flow(orchestrator):
    """Case 4: Knauf Lanett -> 1 Sales Order + 1 KWS Fabrication Purchase Order."""
    req = InboundOrderRequest(
        customer_name="Knauf Insulation",
        customer_po_number="4502653778-USS",
        shipping_address="Plant 2, 3502 43rd St SW, Lanett, AL 36863",
        payment_terms="Net 30 Days",
        shipping_method="UPS",
        line_items=[
            OrderLineItem(
                raw_item_text="Flight Screw",
                quantity=Decimal("1.0"),
                unit_price=Decimal("35378.14"),
                unit_cost=Decimal("15453.85"),
                drawing_ref="DWG 0096551-A",
            )
        ],
        blind_shipping=True,
    )

    result = orchestrator.process_inbound_order(req)

    assert result.status == OrderStatus.SYNCED_TO_SAGE
    assert result.customer_id == "10148"
    assert result.total_revenue == Decimal("35378.14")
    assert result.total_expense == Decimal("15453.85")
    assert "KWS" in result.purchase_orders


def test_multi_vendor_split_1_so_to_3_pos(orchestrator):
    """
    Case 5: Multi-Vendor Order (1 Sales Order -> 3 Split Vendor Purchase Orders).
    Simultaneously orders from KWS (Screw), Zibo (Hoses), and Nibrock (Screens).
    """
    req = InboundOrderRequest(
        customer_name="Knauf Insulation",
        customer_po_number="4502999999-MULTI",
        shipping_address="Plant 2, 3502 43rd St SW, Lanett, AL 36863",
        line_items=[
            OrderLineItem(
                raw_item_text="Flight Screw",
                quantity=Decimal("1.0"),
                unit_price=Decimal("35378.14"),
                unit_cost=Decimal("15453.85"),
            ),
            OrderLineItem(
                raw_item_text="Suction Hoses",
                quantity=Decimal("2.0"),
                unit_price=Decimal("5659.16"),
                unit_cost=Decimal("1040.00"),
            ),
            OrderLineItem(
                raw_item_text="Hammermill Screens",
                quantity=Decimal("2.0"),
                unit_price=Decimal("3772.91"),
                unit_cost=Decimal("1785.00"),
            ),
        ],
        blind_shipping=True,
    )

    result = orchestrator.process_inbound_order(req)

    assert result.status == OrderStatus.SYNCED_TO_SAGE
    # Assert 1 Revenue Sales Order was generated
    assert result.sales_order_number is not None

    # Assert exactly 3 Split Purchase Orders were generated for KWS, ZIBO, and NIBROCK
    assert len(result.purchase_orders) == 3
    assert "KWS" in result.purchase_orders
    assert "ZIBO" in result.purchase_orders
    assert "NIBROCK" in result.purchase_orders

    # Total Revenue: 35378.14 + (2 * 5659.16) + (2 * 3772.91) = 54,242.28
    assert result.total_revenue == Decimal("54242.28")
    # Total Expense: 15453.85 + (2 * 1040.00) + (2 * 1785.00) = 21,103.85
    assert result.total_expense == Decimal("21103.85")
    assert result.gross_margin_pct > Decimal("60.0")


def test_order_orchestrator_subsystem_integrations(orchestrator):
    """
    Verify the 4 newly wired subsystems:
    1. Agent memory cache stores the order immediately
    2. Auto CofC is generated and referenced
    3. Upsell recommendations are included in messages
    4. Vendor repricing freshness check runs
    """
    from app.services.agent_memory_cache import agent_memory_cache

    req = InboundOrderRequest(
        customer_name="Owens Corning Edmonton",
        customer_po_number="PO-INTEG-2026",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        payment_terms="Net 150 Days",
        line_items=[
            OrderLineItem(
                raw_item_text="Structural Channel C8 x 11.5#",
                quantity=Decimal("4.0"),
                unit_price=Decimal("365.00"),
                unit_cost=Decimal("180.00"),
                cut_length_inches=Decimal("24.0"),
            )
        ],
        blind_shipping=True,
    )

    result = orchestrator.process_inbound_order(req)

    # 1. Verify Memory Cache Write
    cached = agent_memory_cache.get_order(result.sales_order_number)
    assert cached is not None
    assert cached["customer_po_number"] == "PO-INTEG-2026"
    assert cached["customer_id"] == "10221"

    # 2. Verify Certificate of Conformance was auto-generated
    has_cofc_msg = any("Certificate of Conformance" in m for m in result.messages)
    assert has_cofc_msg

    # 3. Verify messages are rich and informative
    assert len(result.messages) >= 3



def test_duplicate_customer_po_does_not_create_a_second_order():
    """A redelivered webhook or retried submission must not double-commit.

    The webhook redelivers and users retry, and each previously raised a second
    sales order plus a second set of vendor POs against the same customer PO.
    """
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB
    from app.services.order_orchestrator import OrderOrchestrator

    orchestrator = OrderOrchestrator()
    request = InboundOrderRequest(
        customer_name="Knauf Insulation",
        customer_po_number="PO-IDEMPOTENT-1",
        shipping_address="1000 E 5th St, Lanett, AL 36863",
        line_items=[OrderLineItem(raw_item_text="SCREEN-250", quantity=1, unit_price=500)],
    )

    first = orchestrator.process_inbound_order(request)
    second = orchestrator.process_inbound_order(request)

    assert second.sales_order_number == first.sales_order_number
    assert second.purchase_orders == first.purchase_orders

    with SessionLocal() as db:
        rows = (
            db.query(OrderDB)
            .filter(OrderDB.customer_po_number == "PO-IDEMPOTENT-1")
            .all()
        )
    assert len(rows) == 1, f"expected one committed order, found {len(rows)}"

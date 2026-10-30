"""Tests for Inbound Autonomous Email Daemon and REST Ingestion."""

from fastapi.testclient import TestClient
from app.main import app
from app.services.inbound_email_daemon import InboundEmailMessage, inbound_email_daemon

client = TestClient(app)


def test_inbound_email_processing_service():
    """Verify autonomous PO email parsing, order creation, and acknowledgment."""
    msg = InboundEmailMessage(
        sender_email="purchasing@owenscorning.com",
        sender_name="Mark Richardson (Owens Corning)",
        subject="PO-9921: Screens Reorder for Plant",
        body_text="Please send 2x SCREEN-250 to Edmonton plant under PO-9921.",
    )
    result = inbound_email_daemon.process_inbound_email(msg)

    assert result.status == "PROCESSED_ORDER_CREATED"
    assert "Owens Corning" in result.customer_name
    assert result.customer_po_number == "PO-9921"
    assert result.sales_order_number is not None
    assert len(result.purchase_orders) > 0
    assert "Order Acknowledgment" in result.customer_acknowledgment_email
    assert "Owens Corning" in result.customer_acknowledgment_email


def test_inbound_email_stale_pricing_flagged_for_review():
    """Verify orders with stale pricing get flagged for review and queued in exceptions.

    The stale quote is registered explicitly. It used to come from a demo
    fixture that re-dated itself to 74 days old on every construction, which
    made PLATE-300XL-38 permanently unorderable in production.
    """
    from datetime import date, timedelta

    from app.config import settings
    from app.services.vendor_reprice import vendor_reprice_service

    vendor_reprice_service.register_price(
        item_id="PLATE-300XL-38",
        vendor="NIBROCK",
        unit_cost=110.0,
        quoted_date=date.today() - timedelta(days=settings.VENDOR_PRICE_STALENESS_DAYS + 14),
    )
    try:
        msg = InboundEmailMessage(
            sender_email="purchasing@owenscorning.com",
            sender_name="Mark Richardson (Owens Corning)",
            subject="PO-STALE-88: Heavy Wear Plate",
            body_text="Please send 4x PLATE-300XL-38 to Edmonton under PO-STALE-88.",
        )
        result = inbound_email_daemon.process_inbound_email(msg)
        assert result.status == "FLAGGED_FOR_REVIEW"
        assert "SO-PENDING" in result.sales_order_number
        assert "Under Review" in result.customer_acknowledgment_email
    finally:
        # A fresh quote clears the halt, which is the operational escape hatch
        # the registry previously had no caller for.
        vendor_reprice_service.register_price(
            item_id="PLATE-300XL-38", vendor="NIBROCK", unit_cost=110.0
        )


def test_registering_a_fresh_price_clears_the_halt():
    """A re-quoted price must let the same order through on the next attempt."""
    from app.models.orders import OrderLineItem
    from app.services.vendor_reprice import vendor_reprice_service

    line = OrderLineItem(
        item_id="PLATE-300XL-38",
        raw_item_text="PLATE-300XL-38",
        quantity=1,
        unit_price=500,
        preferred_vendor="NIBROCK",
    )
    vendor_reprice_service.register_price("PLATE-300XL-38", "NIBROCK", 110.0)
    # Does not raise.
    vendor_reprice_service.validate_pricing_freshness({"NIBROCK": [line]}, "PO-FRESH-1")


def test_inbound_email_simulate_api():
    """Verify REST API endpoint for simulating customer email ingestion."""
    payload = {
        "sender_email": "procurement@knaufinsulation.com",
        "sender_name": "Sarah Jenkins (Knauf)",
        "subject": "PO-KNF-8812: Flight Screw Reorder",
        "body_text": "Need 1 unit of Flight Screw for Lanett line.",
    }
    resp = client.post("/api/v1/inbox/simulate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PROCESSED_ORDER_CREATED"
    assert "Knauf Insulation" in data["customer_name"]
    assert data["sales_order_number"] is not None


def test_inbound_email_feed_api():
    """Verify retrieval of ingested customer email stream."""
    resp = client.get("/api/v1/inbox/feed")
    assert resp.status_code == 200
    feed = resp.json()
    assert len(feed) >= 1
    assert "email_id" in feed[0]
    assert "customer_name" in feed[0]

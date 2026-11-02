import uuid
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


def test_an_old_vendor_price_never_holds_an_order():
    """Vendor repricing was dropped: Allied can't renegotiate much with its vendors, so an
    order is never held because a catalog price is old."""
    po = f"PO-OLDPRICE-{uuid.uuid4().hex[:5].upper()}"
    msg = InboundEmailMessage(
        sender_email="purchasing@owenscorning.com",
        sender_name="Mark Richardson (Owens Corning)",
        subject=f"{po}: Heavy Wear Plate",
        body_text=f"Please send 4x PLATE-300XL-38 to Edmonton under {po}.",
    )
    result = inbound_email_daemon.process_inbound_email(msg)
    assert result.status == "PROCESSED_ORDER_CREATED"
    assert not result.sales_order_number.startswith("SO-PENDING")


def test_inbound_email_simulate_api(monkeypatch):
    """Verify REST API endpoint for simulating customer email ingestion."""
    from app.config import settings

    # Knauf's buyers write from knaufinsulation.com; the record only has its knauf.com AP address.
    monkeypatch.setattr(settings, "ORDER_TRUSTED_SENDER_DOMAINS", "knaufinsulation.com=10148")
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

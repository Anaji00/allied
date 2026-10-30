"""Unit tests for Sales Pipeline, Predictive Wear Life, Surcharges, and Upsell recommendations."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_list_predictive_wear_equipment():
    """Verify monitoring customer equipment wear statuses."""
    response = client.get("/api/v1/sales/predictive-wear")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 4
    first = data[0]
    assert "equipment_tag" in first
    assert "estimated_wear_pct" in first
    assert float(first["estimated_wear_pct"]) > 0


def test_draft_quote_for_wear_component():
    """Verify drafting a proactive replacement quote before equipment failure."""
    payload = {
        "equipment_tag": "OC-EDM-HM-01",
        "apply_volume_discount_pct": 5.0,
        "target_delivery_weeks": 2,
    }
    response = client.post("/api/v1/sales/predictive-wear/draft-quote", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "Q-10221-" in data["quote_number"]
    assert data["customer_id"] == "10221"
    assert data["suggested_sku"] == "OC755507"
    assert float(data["quoted_unit_price"]) > 0





def test_scan_wear_and_alert():
    """Verify proactive scan and alert endpoint for expiring plant equipment."""
    response = client.post("/api/v1/sales/predictive-wear/scan-and-alert?discount_pct=10.0&delivery_weeks=2")
    assert response.status_code == 200
    data = response.json()
    assert data["alerts_dispatched"] >= 2
    assert "alerts" in data
    critical_or_warning = [a for a in data["alerts"] if "CRITICAL" in a["alert_level"] or "WARNING" in a["alert_level"]]
    assert len(critical_or_warning) >= 2
    for alert in critical_or_warning:
        assert "monday_alert_message" in alert
        assert alert["quote_number"] is not None
        assert alert["quoted_price"] > 0


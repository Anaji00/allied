"""Tests for Bi-Directional Monday.com Webhooks and Status Triggers."""

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_monday_webhook_challenge_verification():
    """Verify non-blocking challenge verification."""
    resp = client.post("/api/v1/webhooks/monday", json={"challenge": "test_challenge_token_xyz"})
    assert resp.status_code == 200
    assert resp.json()["challenge"] == "test_challenge_token_xyz"


def test_monday_status_trigger_simulation():
    """Verify simulating status changes (Dispatched / Shipped, Approved)."""
    # 1. Shipped status trigger
    resp_ship = client.post(
        "/api/v1/webhooks/monday/simulate-trigger?pulse_id=441920&status_label=Dispatched%20%2F%20Shipped"
    )
    assert resp_ship.status_code == 200
    assert resp_ship.json()["status"] == "simulation_enqueued"

    # 2. Approved status trigger
    resp_appr = client.post(
        "/api/v1/webhooks/monday/simulate-trigger?pulse_id=441920&status_label=Approved%20for%20Production"
    )
    assert resp_appr.status_code == 200
    assert resp_appr.json()["status"] == "simulation_enqueued"

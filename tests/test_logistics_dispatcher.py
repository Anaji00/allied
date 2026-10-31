"""Tests for Automated Packing List Generation & Logistics Dispatcher."""

from app.services.agent_memory_cache import agent_memory_cache
from app.services.logistics_dispatcher import (
    LogisticsDispatchRequest,
    logistics_dispatcher,
)


def _cache_order(so_number: str, **overrides):
    """Puts a minimal order in the cache for the dispatcher to read."""
    data = {
        "order_id": f"ORD-{so_number}",
        "sales_order_number": so_number,
        "customer_po_number": f"PO-{so_number}",
        "customer_name": "Owens Corning",
        "shipping_address": "1450 E US-287, Midlothian, TX 76065",
        "heat_numbers": ["HT-300XL-8819"],
        "line_items": [
            {
                "item_id": "PLATE-300XL-38",
                "description": "Allied 300XL Wear Plate",
                "quantity": 4.0,
                "unit_price": 375.0,
                "unit_cost": 110.0,
                "preferred_vendor": "NIBROCK",
            }
        ],
    }
    data.update(overrides)
    agent_memory_cache.cache_order(data)
    return data


def test_logistics_dispatch_reports_shipment_details():
    """Verify shipping dispatch echoes carrier, tracking, and the order's own heats."""
    _cache_order("SO-12930")
    req = LogisticsDispatchRequest(
        sales_order_number="SO-12930",
        carrier_name="TForce Freight",
        tracking_number="PRO-998811",
        recipient_email="receiving@owenscorning.com",
    )
    result = logistics_dispatcher.dispatch_shipment(req)

    assert result.sales_order_number == "SO-12930"
    assert "packing_slip_SO_12930.pdf" in result.packing_slip_filename
    assert "TForce Freight" in result.customer_notification_email
    assert "PRO-998811" in result.customer_notification_email
    # Heats come from the order, not a hardcoded pair.
    assert result.heat_numbers_certified == ["HT-300XL-8819"]


def test_dispatch_does_not_claim_a_packing_slip_that_does_not_exist():
    """The reported status must reflect whether the PDF is actually on disk."""
    _cache_order("SO-NO-SLIP-1", packing_slip_path=None)
    result = logistics_dispatcher.dispatch_shipment(
        LogisticsDispatchRequest(sales_order_number="SO-NO-SLIP-1")
    )

    from pathlib import Path

    slip_exists = Path(result.packing_slip_path).is_file()
    if slip_exists:
        assert result.dispatch_status == "DISPATCHED_PACKING_LIST_GENERATED"
        assert "Official Allied Packing List" in result.customer_notification_email
    else:
        assert result.dispatch_status == "DISPATCHED_PACKING_LIST_MISSING"
        assert "Official Allied Packing List" not in result.customer_notification_email
        assert "separate cover" in result.customer_notification_email


def test_dispatch_route_dispatches_and_requires_an_operator(client, auth_enforced):
    """POST /orders/{so}/dispatch is the real route the frontend calls."""
    from tests.conftest import staff_token

    _cache_order("SO-12931")
    body = {"carrier_name": "TForce Freight", "tracking_number": "PRO-12931"}

    viewer = staff_token("dispatch-viewer@allied.com", "VIEWER")
    denied = client.post("/api/v1/orders/SO-12931/dispatch", json=body,
                         headers={"Authorization": f"Bearer {viewer}"})
    assert denied.status_code == 403

    operator = staff_token("dispatch-operator@allied.com", "OPERATOR")
    res = client.post("/api/v1/orders/SO-12931/dispatch", json=body,
                      headers={"Authorization": f"Bearer {operator}"})
    assert res.status_code == 200, res.text
    assert res.json()["sales_order_number"] == "SO-12931"
    assert "PRO-12931" in res.json()["customer_notification_email"]


def test_dispatch_route_refuses_path_tricks(client):
    res = client.post("/api/v1/orders/..%5C..%5Cx/dispatch", json={})
    assert res.status_code in (400, 404)

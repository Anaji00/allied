"""Unit tests for the In-Memory Fast Context Cache (AgentMemoryCache)."""

import pytest
from app.services.agent_memory_cache import agent_memory_cache


def test_agent_memory_cache_initialization():
    """Verify singleton initialization with baseline orders and vendors."""
    summary = agent_memory_cache.get_summary()
    assert summary["cached_orders_count"] >= 3
    assert summary["registered_vendors_count"] >= 4
    assert summary["cache_status"] == "ONLINE_FAST_MEMORY"


def test_get_order_by_keys():
    """Verify sub-millisecond retrieval by Order ID, SO Number, and PO Number."""
    # Lookup by SO number
    order_so = agent_memory_cache.get_order("SO-10881")
    assert order_so is not None
    assert order_so["customer_name"] == "Owens Corning (Edmonton Plant)"
    assert order_so["customer_po_number"] == "PO-9921"

    # Lookup by Customer PO
    order_po = agent_memory_cache.get_order("PO-8812")
    assert order_po is not None
    assert order_po["customer_name"] == "Knauf Insulation (Lanett Plant)"
    assert order_po["sales_order_number"] == "SO-10882"


def test_get_orders_by_customer():
    """Verify customer purchase trail retrieval."""
    oc_orders = agent_memory_cache.get_orders_by_customer("Owens Corning")
    assert len(oc_orders) >= 2
    assert any("Edmonton" in o["customer_name"] for o in oc_orders)
    assert any("Starr" in o["customer_name"] for o in oc_orders)


def test_get_vendor_context_and_blind_shipping():
    """Verify vendor profile and blind-shipping rule retrieval."""
    nibrock = agent_memory_cache.get_vendor_context("NIBROCK")
    assert nibrock is not None
    assert "Nibrock Mining" in nibrock["name"]
    assert "Strict" in nibrock["blind_ship_rules"]
    assert nibrock["contract_discount_pct"] == 35.0

    zibo = agent_memory_cache.get_vendor_context("ZIBO")
    assert zibo is not None
    assert "Ceramic" in zibo["category"]
    assert "Omit all Chinese" in zibo["blind_ship_rules"]


def test_order_compliance_bundle():
    """Verify full compliance bundle assembly (MTRs + blind ship + margin)."""
    bundle = agent_memory_cache.get_order_compliance_bundle("SO-10881")
    assert bundle is not None
    assert bundle["blind_shipping_enforced"] is True
    assert bundle["margin_status"] == "COMPLIANT"
    assert "HT-300XL-8819" in bundle["heat_numbers"]
    assert "NIBROCK" in bundle["purchase_orders"]


def test_dynamic_cache_insertion():
    """Verify dynamic cache insertion of new orders."""
    new_ord = {
        "order_id": "ORD-TEST-9999",
        "sales_order_number": "SO-9999",
        "customer_po_number": "PO-TEST-99",
        "customer_id": "CUST-TEST-01",
        "customer_name": "Test Customer Inc.",
        "purchase_orders": {"STOCK": "PO-VEND-STK-99"},
        "heat_numbers": ["HT-TEST-1111"],
        "line_items": [{"item_id": "PLATE-300XL-38", "quantity": 1}],
        "total_revenue": 2950.00,
        "total_expense": 1450.00,
        "gross_margin_pct": 50.85,
    }
    oid = agent_memory_cache.cache_order(new_ord)
    assert oid == "ORD-TEST-9999"

    retrieved = agent_memory_cache.get_order("SO-9999")
    assert retrieved is not None
    assert retrieved["customer_name"] == "Test Customer Inc."

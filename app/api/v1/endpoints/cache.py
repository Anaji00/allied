"""Fast API Endpoints for Agent Fast Memory Cache inspection and sub-millisecond retrieval."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.security import require_viewer
from app.services.agent_memory_cache import agent_memory_cache

# Every route here returns customer orders, margins, or supplier terms, so all
# of them require an authenticated user.
router = APIRouter(dependencies=[Depends(require_viewer)])


@router.get("/summary")
def get_cache_summary() -> Dict[str, Any]:
    """Returns real-time cache statistics across orders, vendors, and indexes."""
    return agent_memory_cache.get_summary()


@router.get("/orders")
def search_cached_orders(
    q: Optional[str] = Query(None, description="Search term (Customer, SO, PO, SKU, Drawing)"),
    customer: Optional[str] = Query(None, description="Filter by customer name or ID"),
    vendor: Optional[str] = Query(None, description="Filter by vendor ID (NIBROCK, KWS, ZIBO, STOCK)"),
    limit: int = Query(10, ge=1, le=50),
) -> List[Dict[str, Any]]:
    """Sub-millisecond retrieval of cached orders by query, customer, or vendor."""
    if customer:
        return agent_memory_cache.get_orders_by_customer(customer)
    if vendor:
        return agent_memory_cache.get_orders_by_vendor(vendor)
    if q:
        return agent_memory_cache.search_orders(q, limit=limit)
    # Default return all unique orders
    return agent_memory_cache.search_orders("", limit=limit)


@router.get("/orders/{order_key}")
def get_order_by_key(order_key: str) -> Dict[str, Any]:
    """Instant lookup by Order ID, SO Number, or Customer PO Number."""
    order = agent_memory_cache.get_order(order_key)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{order_key}' not found in fast cache.")
    return order


@router.get("/orders/{order_key}/compliance")
def get_order_compliance_bundle(order_key: str) -> Dict[str, Any]:
    """Returns the compliance bundle (MTR heats, blind-shipping check, gross margin) for an order."""
    bundle = agent_memory_cache.get_order_compliance_bundle(order_key)
    if not bundle:
        raise HTTPException(status_code=404, detail=f"Order '{order_key}' not found.")
    return bundle


@router.get("/vendors")
def list_all_vendors() -> Dict[str, Dict[str, Any]]:
    """Returns all registered supplier profiles, lead times, and blind-ship rules."""
    return agent_memory_cache.get_all_vendors()


@router.get("/vendors/{vendor_id}")
def get_vendor_profile(vendor_id: str) -> Dict[str, Any]:
    """Returns vendor details, MOQ, freight terms, and blind-shipping protocol."""
    vendor = agent_memory_cache.get_vendor_context(vendor_id)
    if not vendor:
        raise HTTPException(status_code=404, detail=f"Vendor '{vendor_id}' not found.")
    return vendor

"""Direct order processing, dry-run simulation, replay, and packing slip retrieval endpoints."""

import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.core.audit import audit_log
from app.core.database import get_db
from app.services.database_service import DatabaseService
from sqlalchemy.orm import Session
from app.core.logging import logger
from app.core.security import require_operator, require_viewer
from app.models.orders import DualSidedResult, InboundOrderRequest
from app.core.container import get_orchestrator

router = APIRouter()
orchestrator = get_orchestrator()


class OrderPage(BaseModel):
    """A page of the order list, plus how many orders matched in all."""
    total: int
    limit: int
    offset: int
    min_margin_pct: float
    items: List[Dict[str, Any]]


@router.get("", response_model=OrderPage, summary="List orders, newest first, with filters and paging")
def list_orders(
    q: Optional[str] = Query(None, description="Sales order, customer PO or customer name"),
    view: Literal["all", "attention", "in_sage", "not_in_sage"] = Query(
        "all", description="attention = failed checks or Sage writes, or margin under the floor"
    ),
    order_status: Optional[str] = Query(None, alias="status", description="One order status, e.g. SAGE_ERROR"),
    vendor: Optional[str] = Query(None, description="Supplier id with a PO on the order, e.g. ZIBO"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_viewer),
) -> OrderPage:
    """The orders table. Reads the database, so it survives restarts, unlike the cache."""
    floor = float(settings.MIN_GROSS_MARGIN_PCT)
    page = DatabaseService.list_orders(
        db, q=q, view=view, status=order_status, vendor=vendor,
        min_margin_pct=floor, limit=limit, offset=offset,
    )
    return OrderPage(total=page["total"], limit=limit, offset=offset, min_margin_pct=floor, items=page["items"])


@router.post(
    "/process",
    response_model=DualSidedResult,
    status_code=status.HTTP_200_OK,
    summary="Process Inbound Order (Dual-Sided Commit)",
)
def process_order(
    request: InboundOrderRequest,
    current_user: dict = Depends(require_operator),
) -> DualSidedResult:
    """
    Directly processes an inbound purchase order:
    1. Resolves customer terms & item master records.
    2. Atomically generates 1 Customer Sales Order + N Split Vendor Purchase Orders.
    3. Generates sanitized white-label PDF packing slip.
    """
    try:
        result = orchestrator.process_inbound_order(request)
        return result
    except Exception as exc:
        logger.error(f"Failed to process order: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Order processing error: {str(exc)}",
        )


@router.post(
    "/dry-run",
    status_code=status.HTTP_200_OK,
    summary="Dry-Run Order Simulation (No Sage Write)",
)
def dry_run_order(
    request: InboundOrderRequest,
    current_user: dict = Depends(require_viewer),
) -> Dict[str, Any]:
    """
    Performs SKU matching, shop math, margin validation, and vendor splitting preview
    without committing records to Sage 50.
    """
    try:
        # Match customer
        cust_record, cust_conf = orchestrator.entity_matcher.match_customer(request.customer_name)
        
        # Match items & split by vendor
        matched_items = []
        vendor_splits: Dict[str, list] = {}
        for item in request.line_items:
            item_data, item_conf = orchestrator.entity_matcher.match_item(
                item.item_id or item.raw_item_text
            )
            preferred_vendor = item_data.get("preferred_vendor", "STOCK")
            matched_items.append({
                "raw_text": item.raw_item_text,
                "matched_sku": item_data["item_id"],
                "description": item_data["description"],
                "confidence": item_conf,
                "preferred_vendor": preferred_vendor,
                "standard_cost": float(item_data.get("standard_cost", 0)),
                "base_price": float(item_data.get("base_price", 0)),
            })
            if preferred_vendor not in vendor_splits:
                vendor_splits[preferred_vendor] = []
            vendor_splits[preferred_vendor].append(item_data["item_id"])

        return {
            "mode": "DRY_RUN",
            "customer_resolved": cust_record["customer_name"],
            "customer_id": cust_record["customer_id"],
            "customer_confidence": cust_conf,
            "line_items_count": len(matched_items),
            "matched_items": matched_items,
            "vendor_po_splits": vendor_splits,
            "vendor_po_count": len([v for v in vendor_splits if v != "STOCK"]),
            "status": "VALIDATED",
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dry-run validation error: {str(exc)}",
        )


# Sales order numbers are plain document numbers. Anything else (slashes,
# backslashes, dots) is refused before it can be joined into a path: on
# Windows a backslash is a separator, so "..\..\x" escaped the folder.
_SO_NUMBER_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


@router.get("/{so_number}", summary="One order by sales order number")
def get_order(
    so_number: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_viewer),
) -> Dict[str, Any]:
    """Lines, supplier POs, totals and Sage status for the order drawer."""
    order = DatabaseService.get_order_by_so(db, so_number)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Order {so_number} not found.")
    slip = order.pop("packing_slip_path", None)
    order["has_packing_slip"] = bool(slip) and Path(slip).is_file()
    order["min_margin_pct"] = float(settings.MIN_GROSS_MARGIN_PCT)
    return order


@router.get(
    "/{so_number}/packing-slip",
    summary="Download Sanitized White-Label Packing Slip PDF",
)
def download_packing_slip(
    so_number: str,
    current_user: dict = Depends(require_viewer),
):
    """Retrieves and streams the generated white-label packing slip PDF."""
    clean_so = so_number[3:] if so_number.upper().startswith("SO-") else so_number
    if not _SO_NUMBER_RE.fullmatch(clean_so):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sales order number.",
        )
    pdf_filename = f"packing_slip_SO_{clean_so}.pdf"
    slip_dir = (settings.OUTPUT_DIR / "packing_slips").resolve()
    pdf_path = (slip_dir / pdf_filename).resolve()
    # Belt and braces: the pattern above already rules out separators.
    if pdf_path.parent != slip_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sales order number.",
        )

    if not pdf_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Packing slip for Sales Order #{so_number} not found.",
        )

    # Named for the customer's PO: the file goes to the customer, and our sales order number is internal.
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB
    from app.services.logistics_dispatcher import _safe_ref

    with SessionLocal() as db:
        row = db.query(OrderDB).filter(OrderDB.sales_order_number.in_([clean_so, f"SO-{clean_so}"])).first()
        po = row.customer_po_number if row is not None else ""
    download_name = f"packing_list_{_safe_ref(po)}.pdf" if _safe_ref(po) else "packing_list.pdf"

    return FileResponse(
        path=str(pdf_path),
        filename=download_name,
        media_type="application/pdf",
        # The slip carries the customer's name and address; a browser cache
        # would hand it to the next person on the machine without a sign-in.
        headers={"Cache-Control": "private, no-store"},
    )


class DispatchRequest(BaseModel):
    """What the person dispatching chooses; the order itself comes from the path."""
    carrier_name: str = "TForce Freight / Motor Freight"
    origin_facility: str = "HQ"
    tracking_number: Optional[str] = None
    recipient_email: Optional[str] = None
    freight_terms: str = "Prepaid & Add"
    currency: str = "USD"


@router.post(
    "/{so_number}/dispatch",
    summary="Dispatch an order: packing list, customs papers, carrier and customer notice",
)
def dispatch_order(
    so_number: str,
    request: DispatchRequest,
    current_user: dict = Depends(require_operator),
) -> Dict[str, Any]:
    """Generates the packing list (and customs invoice if cross-border) and dispatches the order.

    Until now this was reachable only through the assistant's action runner,
    and the frontend's dispatch call pointed at a route that did not exist.
    """
    clean_so = so_number[3:] if so_number.upper().startswith("SO-") else so_number
    if not _SO_NUMBER_RE.fullmatch(clean_so):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sales order number.",
        )
    from app.services.logistics_dispatcher import LogisticsDispatchRequest, logistics_dispatcher

    result = logistics_dispatcher.dispatch_shipment(
        LogisticsDispatchRequest(sales_order_number=so_number, **request.model_dump())
    )
    audit_log(
        action="DISPATCH",
        resource_type="ORDER",
        resource_id=so_number,
        actor_email=current_user.get("email", "system"),
        actor_role=current_user.get("role", "SYSTEM"),
        facility=request.origin_facility,
        detail=f"Dispatched with {request.carrier_name}",
    )
    return result.model_dump()

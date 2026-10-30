"""Direct order processing, dry-run simulation, replay, and packing slip retrieval endpoints."""

import re
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.config import settings
from app.core.logging import logger
from app.core.security import require_operator, require_viewer
from app.models.orders import DualSidedResult, InboundOrderRequest
from app.core.container import get_orchestrator

router = APIRouter()
orchestrator = get_orchestrator()


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

    return FileResponse(
        path=str(pdf_path),
        filename=pdf_filename,
        media_type="application/pdf",
    )

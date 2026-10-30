"""FastAPI router for 3-way vendor invoice matching and reconciliation history."""

from typing import Any, Dict, List
from app.core.security import require_operator, require_viewer
from fastapi import APIRouter, Depends, HTTPException, status

from app.services.reconciliation_service import (
    VendorInvoice,
    ReconciliationResult,
    reconciliation_service,
)

router = APIRouter()


@router.post(
    "/match",
    response_model=ReconciliationResult,
    status_code=status.HTTP_200_OK,
    summary="Execute 3-Way Match Audit on Incoming Vendor Invoice",
)
def match_vendor_invoice(
    invoice: VendorInvoice,
    _user: dict = Depends(require_operator),
) -> ReconciliationResult:
    """
    Audits incoming vendor bill against authorized Sage 50 PO and sales order terms.
    Detects price increases, quantity over-billings, or freight surcharges.
    """
    try:
        return reconciliation_service.reconcile_vendor_invoice(invoice)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Reconciliation failed: {str(e)}",
        )


@router.get(
    "/history",
    response_model=List[Dict[str, Any]],
    summary="Get 3-Way Reconciliation Audit Trail",
)
def get_reconciliation_history(
    limit: int = 20,
    _user: dict = Depends(require_viewer),
) -> List[Dict[str, Any]]:
    """Returns recent invoice reconciliations with approval status and variance logs."""
    return reconciliation_service.get_reconciliation_history(limit=limit)

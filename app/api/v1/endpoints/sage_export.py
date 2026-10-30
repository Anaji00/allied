"""FastAPI router for Sage 50 US standard import CSV batch exports."""

from typing import Optional
from app.core.security import require_manager
from fastapi import APIRouter, Depends, Response

from app.services.sage_export_service import (
    SageExportSummary,
    sage_export_service,
)

router = APIRouter()


@router.get(
    "/summary",
    response_model=SageExportSummary,
    summary="Get Sage 50 Export Batch Summary Metrics",
)
def get_sage_export_summary(_user: dict = Depends(require_manager)) -> SageExportSummary:
    """Returns total volume, sales order counts, and purchase order counts ready for export."""
    return sage_export_service.get_batch_summary()


@router.get(
    "/sales-orders",
    summary="Download Sage 50 Sales Orders CSV (SO_IMPORT.CSV)",
)
def download_sales_orders_csv(
    customer: Optional[str] = None,
    only_new: bool = False,
    mark_exported: bool = False,
    _user: dict = Depends(require_manager),
):
    """Generates and returns standard SO_IMPORT.CSV formatted for Sage 50 US.

    only_new=true&mark_exported=true is the daily routine: each order reaches
    Sage once, so a re-run never imports a duplicate sales order.

    Orders are marked exported when the file is generated, not when the
    download completes: if the download is lost, clear sage_exported_at for
    those orders before re-running. Marking is atomic -- each order is claimed
    with a conditional update and the file holds exactly the claimed orders,
    so two overlapping requests never both export the same order.
    """
    csv_data = sage_export_service.generate_sales_orders_csv(
        customer_filter=customer, only_new=only_new, mark_exported=mark_exported
    )
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=SO_IMPORT.CSV"},
    )


@router.get(
    "/purchase-orders",
    summary="Download Sage 50 Purchase Orders CSV (PO_IMPORT.CSV)",
)
def download_purchase_orders_csv(
    vendor: Optional[str] = None,
    _user: dict = Depends(require_manager),
):
    """Generates and returns standard PO_IMPORT.CSV formatted for Sage 50 US."""
    csv_data = sage_export_service.generate_purchase_orders_csv(vendor_filter=vendor)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=PO_IMPORT.CSV"},
    )


@router.post(
    "/batch",
    summary="Generate Full In-Memory Batch Bundle Payload",
)
def generate_batch_bundle(_user: dict = Depends(require_manager)):
    """Returns JSON containing both SO_IMPORT.CSV and PO_IMPORT.CSV contents and metrics."""
    summary = sage_export_service.get_batch_summary()
    so_csv = sage_export_service.generate_sales_orders_csv()
    po_csv = sage_export_service.generate_purchase_orders_csv()
    return {
        "summary": summary.model_dump(),
        "files": {
            "SO_IMPORT.CSV": so_csv,
            "PO_IMPORT.CSV": po_csv,
        },
    }

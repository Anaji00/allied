"""FastAPI router for Sage 50 US standard import CSV batch exports."""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.security import require_manager

from app.services.sage_export_service import (
    SageExportSummary,
    sage_export_service,
)

router = APIRouter()

# Lists the orders a download marked as exported, so the screen can offer to
# undo it if the file never saved.
EXPORTED_HEADER = "X-Sage-Exported-Orders"


def _csv(content: str, filename: str, marked: List[str]) -> Response:
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    if marked:
        headers[EXPORTED_HEADER] = ",".join(marked)
    return Response(content=content, media_type="text/csv", headers=headers)


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
    download completes. The marked sales order numbers come back in the
    X-Sage-Exported-Orders header; if the file never saved, POST them to
    /sage/export/unmark. Marking is atomic -- each order is claimed with a
    conditional update and the file holds exactly the claimed orders, so two
    overlapping requests never both export the same order.
    """
    csv_data, marked = sage_export_service.export_sales_orders(
        customer_filter=customer, only_new=only_new, mark_exported=mark_exported
    )
    return _csv(csv_data, "SO_IMPORT.CSV", marked)


@router.get(
    "/purchase-orders",
    summary="Download Sage 50 Purchase Orders CSV (PO_IMPORT.CSV)",
)
def download_purchase_orders_csv(
    vendor: Optional[str] = None,
    only_new: bool = False,
    mark_exported: bool = False,
    _user: dict = Depends(require_manager),
):
    """Generates and returns standard PO_IMPORT.CSV formatted for Sage 50 US.

    only_new / mark_exported work as for sales orders, with a separate mark,
    so each order's vendor POs reach Sage once. A vendor filter never marks.
    """
    csv_data, marked = sage_export_service.export_purchase_orders(
        vendor_filter=vendor, only_new=only_new, mark_exported=mark_exported
    )
    return _csv(csv_data, "PO_IMPORT.CSV", marked)


class UnmarkBody(BaseModel):
    kind: Literal["sales-orders", "purchase-orders"]
    sales_order_numbers: List[str] = Field(..., min_length=1, max_length=5000)


@router.post("/unmark", summary="Put orders back in the next export (the file never arrived)")
def unmark_exported(body: UnmarkBody, user: dict = Depends(require_manager)):
    reset = sage_export_service.unmark_exported(
        body.sales_order_numbers, kind="so" if body.kind == "sales-orders" else "po",
        actor=user.get("email", ""),
    )
    return {"reset": reset}


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

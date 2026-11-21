"""FastAPI router for Master Catalog and Batch Order CSV Imports."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core.security import require_manager, require_viewer
from app.services.csv_import_service import CSVImportResult, check_preview_token, csv_import_service

router = APIRouter()

# An ITEMS/CUSTOMERS/VENDORS import rewrites the master catalog on disk and an
# ORDERS_BATCH import commits real Sage transactions, so both need MANAGER+.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class CSVImportPayload(BaseModel):
    """Payload for importing CSV text directly via JSON."""
    csv_text: str = Field(..., max_length=MAX_UPLOAD_BYTES)
    import_type: str  # ITEMS, CUSTOMERS, VENDORS, ORDERS_BATCH
    token: Optional[str] = Field(None, description="The token from POST /csv/preview: the import is refused when the sheet is not the one that was previewed")


@router.post(
    "/import",
    response_model=CSVImportResult,
    status_code=status.HTTP_200_OK,
    summary="Import Master Data or Batch Orders via CSV",
)
def import_csv_data(
    payload: CSVImportPayload,
    current_user: dict = Depends(require_manager),
) -> CSVImportResult:
    """
    Ingests and merges CSV data into Allied systems:
    - ITEMS: Master Items catalog (pricing, cost, vendors, stock)
    - CUSTOMERS: Master Customer accounts and terms
    - VENDORS: Master Vendor suppliers and terms
    - ORDERS_BATCH: Batch Customer POs converted into dual-sided Sage SOs + vendor POs
    """
    # max_length counts characters; the cap is on encoded size, like the upload.
    if len(payload.csv_text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"CSV exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB import limit.",
        )
    if payload.token:
        why = check_preview_token(payload.token, payload.csv_text, payload.import_type)
        if why:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=why)
    result = csv_import_service.import_csv(
        payload.csv_text,
        payload.import_type,
        actor_email=current_user.get("email", "system"),
        actor_role=current_user.get("role", "SYSTEM"),
    )
    if result.status == "FAILED" and not result.records_created and not result.records_updated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.message + " " + "; ".join(result.errors),
        )
    return result


class CSVPreviewChange(BaseModel):
    field: str
    was: str = ""
    now: str = ""


class CSVPreviewRow(BaseModel):
    model_config = {"extra": "allow"}
    row: int
    action: str  # new, changed, skipped
    key: str = ""
    changes: List[CSVPreviewChange] = []
    reason: Optional[str] = None


class CSVPreview(BaseModel):
    import_type: str
    new: int
    changed: int
    skipped: int
    rows: List[CSVPreviewRow]
    errors: List[str] = []
    message: str = ""
    token: str


@router.post("/preview", response_model=CSVPreview, summary="Dry run of an import: what each row would do, and nothing is written (same body as /import)")
def preview_csv_data(payload: CSVImportPayload, current_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    if len(payload.csv_text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"CSV exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB import limit.")
    if payload.import_type.strip().upper() not in ("ITEMS", "CUSTOMERS", "VENDORS", "ORDERS_BATCH"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose ITEMS, CUSTOMERS, VENDORS, or ORDERS_BATCH.")
    return csv_import_service.preview_csv(payload.csv_text, payload.import_type, actor_email=current_user.get("email", "system"))


@router.post(
    "/upload",
    response_model=CSVImportResult,
    status_code=status.HTTP_200_OK,
    summary="Upload CSV File for Master Data or Batch Orders",
)
async def upload_csv_file(
    import_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(require_manager),
) -> CSVImportResult:
    """Accepts multipart/form-data CSV file upload."""
    # Read in chunks and stop at the cap, rather than reading the whole file
    # into memory and only then checking its size.
    buf = bytearray()
    try:
        while True:
            chunk = await file.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"CSV exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB upload limit.",
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to read uploaded file: {str(e)}",
        )
    content = bytes(buf)

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded file is not valid UTF-8 text: {e}",
        )

    result = await run_in_threadpool(
        csv_import_service.import_csv,
        text,
        import_type,
        actor_email=current_user.get("email", "system"),
        actor_role=current_user.get("role", "SYSTEM"),
    )
    if result.status == "FAILED" and not result.records_created and not result.records_updated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.message + " " + "; ".join(result.errors),
        )
    return result


@router.get(
    "/templates",
    response_model=Dict[str, Any],
    summary="Get CSV Format Templates",
)
def get_csv_templates(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    """Returns sample CSV templates and column headers for each target type."""
    return {"templates": csv_import_service.get_templates()}

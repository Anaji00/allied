"""Import from Sage 50 export files: preview (saves nothing), commit (saves what the preview showed), history. The app never touches a Sage company file."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core.security import require_manager, require_operator
from app.services import sage_import

router = APIRouter()


def _refuse(exc: Exception):
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


async def _read(upload: UploadFile) -> bytes:
    """The upload, read in pieces so an oversized file is refused as it arrives."""
    out = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            return bytes(out)
        out += chunk
        if len(out) > sage_import.MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                detail=f"The file is larger than {sage_import.MAX_BYTES // (1024 * 1024)} MB. Export a shorter date range or split it.")


@router.get("/kinds", summary="What can be imported from Sage 50, and the columns each needs")
def kinds(_user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return sage_import.kinds()


@router.post("/preview", summary="Read a Sage 50 export file and say what importing it would do. Saves nothing; returns a token for /commit")
async def preview(
    file: UploadFile = File(...),
    kind: str = Form("auto", max_length=24, description="parts, customers, vendors, invoices, payments, open_sales_orders, open_purchase_orders, bills, or auto"),
    date_order: Optional[str] = Form(None, max_length=3, description="mdy (month first, like Sage US) or dmy, when the file's own dates do not settle it"),
    user: dict = Depends(require_operator),
) -> Dict[str, Any]:
    data = await _read(file)
    try:
        return await run_in_threadpool(sage_import.preview, data, file.filename or "", kind, user, date_order or None)
    except sage_import.ImportRefused as exc:
        _refuse(exc)


@router.post("/commit", summary="Save what a preview showed (managers). The same file twice changes nothing")
def commit(body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return sage_import.commit(str(body.get("token") or ""), user, confirm_close=body.get("confirm_close") is True)
    except sage_import.ImportRefused as exc:
        _refuse(exc)


@router.post("/history/{import_id}/undo", summary="Take back exactly what one import saved (managers): its new rows are removed, rows it changed are put back. Safe to repeat")
def undo(import_id: int, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return sage_import.undo(import_id, user)
    except sage_import.ImportRefused as exc:
        _refuse(exc)


@router.get("/history", summary="The files brought in from Sage 50, newest first")
def history(limit: int = Query(50, ge=1, le=200), _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return sage_import.history(limit)

"""File sharing: attach files to any order, RFQ, vendor job, shipment, milestone or board item (or to a comment), see them
where the item is, and download them. Files stay on this server; nothing is sent to monday."""

from typing import Any, Dict, List, Optional

from fastapi import Body, APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.config import settings
from app.core.security import require_operator, require_viewer
from app.services.coordination import files, updates

router = APIRouter()
_MAX_AT_ONCE = 10


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# A plain `def`: reading and saving a big file runs in a worker thread, not on the event loop.
@router.post("", status_code=status.HTTP_201_CREATED, summary="Attach one or more files to an item (or to a comment on it)")
def attach(item_type: str = Form(..., max_length=24), item_ref: str = Form(..., max_length=64), upload: List[UploadFile] = File(...),
           update_id: Optional[int] = Form(None, ge=1, le=2_147_483_647), user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    if len(upload) > _MAX_AT_ONCE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Attach at most {_MAX_AT_ONCE} files at once.")
    limit = settings.SUBMISSION_MAX_MB * 1024 * 1024
    out = []
    for f in upload:
        data = f.file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{f.filename} is larger than {settings.SUBMISSION_MAX_MB} MB.")
        try:
            out.append(files.add(item_type, item_ref, f.filename or "file", data, user, update_id))
        except (LookupError, files.FileError, updates.UpdateError) as exc:
            _fail(exc)
    return out


@router.get("", summary="The files on one item, newest first")
def listing(item_type: str = Query(..., max_length=24), item_ref: str = Query(..., max_length=64), _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    try:
        return files.listing(item_type, item_ref)
    except (LookupError, updates.UpdateError) as exc:
        _fail(exc)


@router.get("/allowed", summary="Which kinds of file can be attached, and the size limit")
def allowed(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return {"extensions": files.allowed(), "max_mb": settings.SUBMISSION_MAX_MB, "max_per_item": files.MAX_PER_ITEM}


@router.get("/{file_id}/download", summary="Download a file (always as a download: a browser never opens or runs it)")
def download(file_id: int = Path(..., ge=1, le=2_147_483_647), _user: dict = Depends(require_viewer)) -> FileResponse:
    try:
        path, name, ctype = files.open_file(file_id)
    except LookupError as exc:
        _fail(exc)
    return FileResponse(path, filename=name, media_type=ctype, content_disposition_type="attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a file (whoever attached it, or a manager)")
def remove(file_id: int = Path(..., ge=1, le=2_147_483_647), user: dict = Depends(require_operator)) -> None:
    try:
        files.remove(file_id, user)
    except (LookupError, PermissionError) as exc:
        _fail(exc)


@router.patch("/{file_id}", summary="Mark a file as a design file (only these go to vendors) or take the mark off")
def mark_design(file_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)):
    try:
        return files.set_design(file_id, bool(body.get("design")), user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

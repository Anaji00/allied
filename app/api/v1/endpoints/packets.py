"""Scanned job packets that are not on a job yet: read one, see the waiting ones, attach one to a job, download the original."""

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.core.security import require_operator
from app.services.coordination import jobs, packets

router = APIRouter()


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    if isinstance(exc, jobs.JobError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That request could not be processed. Check what was entered and try again.")


@router.post("", summary="Read a scanned packet (PDF). It joins a job when a printed PO or sales order number matches exactly one; otherwise it waits")
def upload(file: UploadFile = File(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return packets.ingest(packets.read_upload(file.file), file.filename or "packet.pdf", user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.get("", summary="Packets waiting to be attached to a job")
def waiting(_user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return packets.unattached()


@router.post("/{packet}/attach", summary="Attach every document of a packet to a job")
def attach(packet: str, body: Dict[str, Any] = Body(...), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return packets.attach(packet, str(body.get("job") or ""))
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.get("/{packet}/file", summary="Download the original scanned packet (staff only)")
def original(packet: str, _user: dict = Depends(require_operator)):
    try:
        return FileResponse(packets.file_path(packet), media_type="application/pdf", filename="packet.pdf")
    except (LookupError, ValueError) as exc:
        _fail(exc)


@router.post("/documents/{doc_id}/confirm", summary="A person confirms (and may correct) the facts read from a document; only confirmed ones are staged to monday")
def confirm_document(doc_id: int, body: Dict[str, Any] = Body(default={}), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        fixes = body.get("fields") if isinstance(body, dict) else None
        if fixes is not None and not isinstance(fixes, dict):
            raise jobs.JobError("fields must be a set of fact names and values.")
        return packets.confirm(doc_id, fixes, user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)

"""The packing slip queue: what is waiting, ready, failed or printed, with Retry and Printed."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import require_operator, require_viewer
from app.services import packing_slip_queue

router = APIRouter()


@router.get("/queue", summary="Packing slips queued for sales orders, newest first, with counts by state")
def queue(state: Optional[str] = Query(None, max_length=16, description="QUEUED, READY, FAILED or PRINTED"),
          _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return packing_slip_queue.list_queue(state)


@router.post("/{so_number}/retry", summary="Make a slip again now (after a failure, or to refresh it)")
def retry(so_number: str, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return packing_slip_queue.retry(so_number.replace("SO-", ""))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{so_number}/printed", summary="Mark a ready slip as printed")
def printed(so_number: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return packing_slip_queue.mark_printed(so_number.replace("SO-", ""), user.get("email", "unknown"))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

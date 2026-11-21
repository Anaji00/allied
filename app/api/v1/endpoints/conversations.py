"""Conversations: one list of threads (comments, engineering notes, drafts waiting, who it waits on) with unread counts for me, and a way to mark them read."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.view_models import Conversations
from app.core.security import require_viewer
from app.services import conversations
from app.services.coordination import updates

router = APIRouter()


@router.get("", summary="Threads, newest first, with my unread counts, the last sentence and who each waits on; unread_total is the badge", response_model=Conversations, response_model_exclude_unset=True)
def listing(only_unread: bool = False, limit: int = Query(100, ge=1, le=300), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return conversations.listing(user, only_unread, limit)


@router.post("/read-all", summary="Mark every thread read for me")
def read_all(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return conversations.mark_all_read(user)


@router.post("/{item_type}/{item_ref:path}/read", summary="Mark one thread read for me (messages by others after this are unread)")
def read(item_type: str = Path(..., max_length=24), item_ref: str = Path(..., max_length=64), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return conversations.mark_read(user, item_type, item_ref)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except (ValueError, updates.UpdateError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

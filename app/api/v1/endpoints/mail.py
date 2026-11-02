"""Every email the mailbox reader has seen, and what was done with each."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import require_operator, require_viewer
from app.services import mail_log
from app.services.mailbox_poller import mailbox_poller

router = APIRouter()


@router.get("", summary="Emails read: what each was and what the system did, newest first")
def list_mail(category: Optional[str] = Query(None, max_length=32), needs_action: Optional[bool] = None,
              q: Optional[str] = Query(None, max_length=100), direction: Optional[str] = Query(None, max_length=16),
              days: int = Query(30, ge=1, le=365), limit: int = Query(200, ge=1, le=1000),
              tag: Optional[str] = Query(None, max_length=32), include_quiet: bool = False,
              _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    """Quiet mail (newsletters, auto-replies, bounces, invites, notifications, sent mail and the
    first-start backlog) is left out unless include_quiet, or asked for by category."""
    return {**mail_log.list_mail(category, needs_action, q, direction, days, limit, tag, include_quiet),
            "reader": mailbox_poller.status()}


@router.post("/{mail_id}/handled", summary="Mark an email that needed a person as dealt with")
def handled(mail_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return mail_log.mark_handled(mail_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

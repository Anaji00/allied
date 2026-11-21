"""Every email the mailbox reader has seen, and what was done with each."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.security import require_manager, require_operator, require_viewer
from app.services import mail_log, mail_reply
from app.services.mailbox_poller import mailbox_poller

router = APIRouter()


@router.get("", summary="Emails read: what each was and what the system did, newest first")
def list_mail(category: Optional[str] = Query(None, max_length=32), needs_action: Optional[bool] = None,
              q: Optional[str] = Query(None, max_length=100), direction: Optional[str] = Query(None, max_length=16),
              days: int = Query(30, ge=1, le=365), limit: int = Query(200, ge=1, le=1000),
              tag: Optional[str] = Query(None, max_length=32), include_quiet: bool = False,
              mine: bool = Query(False, description="Only mail the signed-in person is on (sender, To or Cc)"), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    """Quiet mail (newsletters, auto-replies, bounces, invites, notifications, sent mail and the
    first-start backlog) is left out unless include_quiet, or asked for by category."""
    return {**mail_log.list_mail(category, needs_action, q, direction, days, limit, tag, include_quiet, user.get("email", "") if mine else ""),
            "reader": mailbox_poller.status()}


@router.get("/status", summary="Is the mailbox connected? {connected, reason, polling}: one plain sentence for the Email screen")
def mail_status(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.config import settings

    st = mailbox_poller.status()
    if not settings.IMAP_HOST:
        reason, connected = "Not connected yet: the mailbox is not set up.", False
    elif not (settings.IMAP_USERNAME and settings.IMAP_PASSWORD):
        reason, connected = "Not connected yet: the mailbox login is missing.", False
    elif not st.get("enabled"):
        reason, connected = "Not connected yet: reading the mailbox is switched off.", False
    elif not st.get("running"):
        reason, connected = "The mailbox is set up but is not being read right now.", False
    else:
        reason, connected = "Connected: " + str(st.get("detail") or "the mailbox is being read."), True
    return {"connected": connected, "reason": reason, "polling": {"running": bool(st.get("running")), "every_seconds": st.get("poll_seconds"), "folders": st.get("folders")}}


@router.post("/{mail_id}/handled", summary="Mark an email that needed a person as dealt with")
def handled(mail_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return mail_log.mark_handled(mail_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


class DraftReplyBody(BaseModel):
    use_ai: bool = False


@router.post("/{mail_id}/draft-reply", summary="A reply draft for this email: a rule template first, the AI second (only if you opted in), else a plain template. It waits in the approval queue; nothing is sent")
def draft_reply(mail_id: int, body: DraftReplyBody = DraftReplyBody(), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return mail_reply.draft_reply(mail_id, user, body.use_ai)
    except mail_reply.ReplyError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/templates", summary="The reply templates managers keep, with the merge fields they may use")
def get_templates(_user: dict = Depends(require_operator)) -> Any:
    return mail_reply.list_templates()


@router.put("/templates/{key}", summary="Add or change a reply template (a manager); merge fields: customer, vendor, po, job, date, first_name, signature")
def put_template(key: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return mail_reply.put_template(key, body, user)
    except mail_reply.ReplyError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.delete("/templates/{key}", summary="Remove a reply template (a manager)")
def delete_template(key: str, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    mail_reply.delete_template(key)
    return {"removed": True, "key": key}


settings_router = APIRouter()


@settings_router.get("", summary="My mail-AI settings (everything is off until I turn it on)")
def get_mail_settings(user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return mail_reply.get_settings(user.get("email", ""))


@settings_router.put("", summary="Save my mail-AI settings: ai_opt_in, auto_draft, style_note (500 characters), exclude_senders, exclude_words")
def put_mail_settings(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return mail_reply.put_settings(user.get("email", ""), body)
    except mail_reply.ReplyError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)

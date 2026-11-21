"""One-click actions on a job, PO, shipment, order or board row: what applies, a clean text summary to copy, an email draft for approval.
Nothing here sends anything."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status

from app.core.security import require_operator
from app.services import actions

router = APIRouter()


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:                          # ActionError, jobs.JobError, UpdateError and BallError are all ValueErrors with plain messages
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _s(body: Dict[str, Any], key: str) -> str:
    v = body.get(key)
    if v is not None and (not isinstance(v, (str, int, float)) or isinstance(v, bool)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{key}' must be text.")
    return "" if v is None else str(v)


KIND = Path(..., pattern="^(job|rfq|po|shipment|order|row|product)$", description="job, rfq, po, shipment, order, product or row (a board row: ref is board_id:item_id)")


@router.get("/{kind}/{ref:path}/text", summary="A clean plain-text summary (and TSV) to copy to the clipboard")
def text(kind: str = KIND, ref: str = Path(..., max_length=80), audience: str = Query("internal", max_length=12), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(actions.text, kind, ref, audience, user.get("role") in ("MANAGER", "ADMIN"))


@router.post("/{kind}/{ref:path}/email-draft", summary="A draft email to the vendor, customer or a person, with attachments; waits for approval, nothing is sent")
def email_draft(kind: str = KIND, ref: str = Path(..., max_length=80), body: Dict[str, Any] = Body(default={}), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    attach = body.get("attach") or []
    if not isinstance(attach, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'attach' must be a list of {type, id}.")
    return _run(actions.email_draft, kind, ref, user, _s(body, "to"), _s(body, "subject"), _s(body, "body"), _s(body, "audience"), attach, body.get("include_summary") is not False)


@router.post("/{kind}/{ref:path}/share", summary="Comment on it with an @mention and a link (a board row: staged for monday)")
def share(kind: str = KIND, ref: str = Path(..., max_length=80), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(actions.share, kind, ref, _s(body, "body"), body.get("mentions"), user)


@router.post("/{kind}/{ref:path}/handoff", summary="Hand the ball in one call: to a person (email) or a vendor, the customer or a carrier by name, with a note, a due date and an optional comment")
def handoff(kind: str = KIND, ref: str = Path(..., max_length=80), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(actions.handoff, kind, ref, user, _s(body, "to"), _s(body, "ball_with"), _s(body, "note"), _s(body, "due"), _s(body, "comment"))


@router.post("/{kind}/{ref:path}/to-monday", summary="Stage this record as a new row on a monday board (matching facts go into matching columns). Staged only")
def to_monday(kind: str = KIND, ref: str = Path(..., max_length=80), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services import create_anything

    return _run(create_anything.to_monday_stage, kind, ref, _s(body, "board_id"), _s(body, "group") or None, user, body.get("dry_run") is True)


@router.get("/{kind}/{ref:path}", summary="What can be done with this one: each action with its request, recipients and attachments")
def available(kind: str = KIND, ref: str = Path(..., max_length=80), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(actions.available, kind, ref)

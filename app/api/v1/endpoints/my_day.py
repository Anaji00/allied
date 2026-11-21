"""My day: one list of everything waiting on me, each row with a sentence, why, one click, age and urgency."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status

from app.api.v1.view_models import Brief, Chase, MyDay, PrepOut, SinceOut
from app.core.security import require_operator, require_viewer
from app.services import my_day

router = APIRouter()


@router.get("", summary="One list of what is waiting on me: approvals, drafts, problems, alerts, balls, mentions, notes, shipments", response_model=MyDay, response_model_exclude_unset=True)
def get_my_day(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return my_day.items(user)


@router.post("/dismiss-notices", summary="Dismiss my information-only alerts older than two days (the 'older notices' row)")
def dismiss_notices(user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return my_day.dismiss_notices(user)


brief_router = APIRouter()


@brief_router.get("", summary="The morning brief as data: a headline and a sentence with a link for each thing waiting on me (business timezone)", response_model=Brief, response_model_exclude_unset=True)
def get_brief(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return my_day.brief(user)


prep_router = APIRouter()


@prep_router.get("/{kind}/{ref:path}", summary="Meeting prep for a job, customer or vendor: emails, POs and dates, RFQs, drawing revisions and acknowledgements, shipments, the ball, comments, and a copy-ready text", response_model=PrepOut, response_model_exclude_unset=True)
def get_prep(kind: str = Path(..., pattern="^(job|customer|vendor)$"), ref: str = Path(..., max_length=120), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return my_day.prep(kind, ref, user.get("role") in ("MANAGER", "ADMIN"))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


since_router = APIRouter()


@since_router.get("", summary="What changed on a job or board since yesterday (or today, this week, 3d, a date): plain sentences, newest first", response_model=SinceOut, response_model_exclude_unset=True)
def get_since(kind: str = Query(..., pattern="^(job|board)$"), ref: str = Query(..., max_length=64), since: str = Query("yesterday", max_length=30), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return my_day.since(kind, ref, since, user.get("role") in ("MANAGER", "ADMIN"))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


chase_router = APIRouter()


@chase_router.get("", summary="Who owes us something, oldest first, each with the follow-up already drafted (if any) and a one-call send or dismiss", response_model=Chase, response_model_exclude_unset=True)
def get_chase(user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return my_day.chase(user)


@chase_router.post("/{draft_id}", summary="Send or dismiss one follow-up draft ({action: send|dismiss}); only the follow-up kinds can be decided here")
def decide_chase(draft_id: str = Path(..., max_length=64), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return my_day.chase_decide(draft_id, str(body.get("action") or ""), user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

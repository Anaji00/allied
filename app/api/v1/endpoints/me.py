"""What needs the signed-in person, in one call."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.v1.view_models import MyCounts, NotificationPrefs

from app.core.security import require_viewer
from app.services import my_prefs
from app.services.coordination import needs

router = APIRouter()


@router.get("/needs", summary="Drafts waiting for my OK, my open alerts and where I hold the ball: one count and one list")
def my_needs(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return needs.for_person(user)


@router.get("/counts", summary="Unread alerts, mail needing action and drafts waiting for my OK in one call (the same numbers as Today)", response_model=MyCounts, response_model_exclude_unset=True)
def my_counts(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return my_prefs.counts(user)


@router.get("/notifications", summary="My notification choices: channels per group, quiet hours, instant or digest", response_model=NotificationPrefs)
def get_notifications(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return my_prefs.get_prefs(user.get("email", ""))


@router.put("/notifications", summary="Save my notification choices (only mine)", response_model=NotificationPrefs)
def put_notifications(body: Dict[str, Any] = Body(...), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return my_prefs.put_prefs(user.get("email", ""), body)
    except my_prefs.PrefsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

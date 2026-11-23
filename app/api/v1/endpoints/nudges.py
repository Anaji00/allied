"""Nudges and milestone blasts to Allied staff (named senders and admins only)."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security import require_operator
from app.services import nudges

router = APIRouter()


class MilestoneIn(BaseModel):
    name: str = Field("", max_length=120)
    due: Optional[str] = Field(None, max_length=10, description="YYYY-MM-DD")


class NudgeIn(BaseModel):
    kind: str = Field("nudge", description="nudge or milestone")
    to: List[str] = Field(..., description="Allied staff emails (no outside addresses)")
    message: str = Field(..., max_length=nudges.MAX_MESSAGE)
    channels: List[str] = Field(["app"], description="app, email, sms")
    milestone: Optional[MilestoneIn] = None
    practice: bool = Field(False, description="True rehearses it: nothing is sent and nothing reaches anyone")


def _fail(exc: Exception):
    raise HTTPException(status_code=getattr(exc, "status", 400), detail=getattr(exc, "message", str(exc)))


@router.post("", summary="Send a nudge or a milestone blast now (named senders and admins). practice=true sends nothing")
def send_nudge(body: NudgeIn, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return nudges.send(user, body.kind, body.to, body.message, body.channels, body.milestone.model_dump() if body.milestone else None, body.practice)
    except nudges.NudgeError as exc:
        _fail(exc)


@router.get("", summary="Recent nudges: your own, or everyone's for an admin")
def nudge_history(limit: int = Query(100, ge=1, le=500), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    if not nudges.may_send(user):
        raise HTTPException(status_code=403, detail="Only the people named to send nudges, and admins, can see this.")
    return {"can_send": True, "senders_set": bool(nudges.senders()), "history": nudges.history(user, limit)}

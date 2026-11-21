"""Follow an item and see what changed since I looked. Only my own follows and seen marks; read-only for everything else."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import require_viewer
from app.services import follow as svc

router = APIRouter()


class FollowBody(BaseModel):
    item_kind: str = Field(..., max_length=24, description="job, order, rfq or board_item")
    item_ref: str = Field(..., max_length=120, description="the job ref, sales order number, RFQ number, or '<board id>:<item id>'")


def _run(fn, user, body: FollowBody) -> Dict[str, Any]:
    try:
        return fn(user, body.item_kind, body.item_ref)
    except svc.FollowError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/follow", summary="Follow an item (a viewer can follow a job, staff also an order, RFQ or board row)")
def follow(body: FollowBody, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return _run(svc.follow, user, body)


@router.post("/unfollow", summary="Stop following an item")
def unfollow(body: FollowBody, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return _run(svc.unfollow, user, body)


@router.get("/following", summary="Everything I follow, newest change first, each with changed_at and changed_since_seen")
def following(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return svc.following(user)
    except svc.FollowError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/seen", summary="I have looked at this item now (clears 'changed since you looked' for me only)")
def seen(body: FollowBody, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return _run(svc.mark_seen, user, body)

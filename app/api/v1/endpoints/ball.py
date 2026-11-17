"""Owner and "ball is with": whose turn it is on an item, since when, and the chasing that follows.

The app's own record; nothing is written to monday."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.core.security import require_operator, require_viewer
from app.services.coordination import ball, updates

router = APIRouter()


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", summary="Whose turn it is on one item (null if nobody has set it)")
def one(item_type: str = Query(..., max_length=24), item_ref: str = Query(..., max_length=64),
        _user: dict = Depends(require_viewer)) -> Optional[Dict[str, Any]]:
    try:
        return ball.get(item_type, item_ref)
    except (LookupError, updates.UpdateError, TypeError, AttributeError, ValueError) as exc:
        _fail(exc)


@router.put("", summary="Set the owner and/or hand the ball to someone; handing it over restarts the clock and alerts them")
def put(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return ball.set_ball(str(body.get("item_type", "")), str(body.get("item_ref", "")), user, body.get("ball_with"),
                             body.get("ball_person"), body.get("owner"), body.get("note"), body.get("due"))
    except (LookupError, updates.UpdateError, ball.BallError, TypeError, AttributeError, ValueError) as exc:
        _fail(exc)


@router.post("/resolve", summary="Nobody is waiting on this any more")
def resolve(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return ball.resolve(str(body.get("item_type", "")), str(body.get("item_ref", "")), user)
    except (LookupError, updates.UpdateError, TypeError, AttributeError, ValueError) as exc:
        _fail(exc)


@router.get("/open", summary="Everything with a ball, longest wait first (RFQs and vendor jobs nobody set are worked out)")
def open_items(who: str = Query("all", pattern="^(all|me)$"), kind: str = Query("", max_length=16), late: bool = False,
               user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return ball.list_open(who, user.get("email", ""), kind, late)


@router.get("/summary", summary="The management view: where the ball sits, who holds the most, what is oldest")
def summary(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return ball.summary()


@router.post("/run", summary="Run the chasing now (it also runs by itself)")
def run_now(_user: dict = Depends(require_operator)) -> Dict[str, int]:
    return ball.run()

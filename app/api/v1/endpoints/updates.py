"""Updates: comments with @mentions on any order, RFQ, vendor job, shipment, milestone or board item.

The app's own threads; nothing is written to monday."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status

from app.core.security import require_operator, require_viewer
from app.services.coordination import updates

router = APIRouter()


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", summary="The thread on one item, oldest first, with replies")
def thread(item_type: str = Query(..., max_length=24), item_ref: str = Query(..., max_length=64),
           lang: Optional[str] = Query(None, pattern="^(en|zh|vi)$", description="Show comments in this language (default: the reader's own)"),
           user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.services import bridge

    try:
        return updates.thread(item_type, item_ref, user.get("email", ""), lang or bridge.language_of(user.get("email", "")))
    except (LookupError, updates.UpdateError, TypeError, AttributeError, ValueError) as exc:
        _fail(exc)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Write a comment (or a reply); @name alerts that person")
def post(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return updates.post(str(body.get("item_type", "")), str(body.get("item_ref", "")), str(body.get("body", "")), user,
                            body.get("parent_id"), body.get("mentions") or [])
    except (LookupError, updates.UpdateError, TypeError, AttributeError, ValueError) as exc:
        _fail(exc)


@router.patch("/{update_id}", summary="Change your own comment")
def change(update_id: int = Path(..., ge=1, le=2_147_483_647), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return updates.edit(update_id, str(body.get("body", "")), user, body.get("mentions") or [])
    except (LookupError, PermissionError, updates.UpdateError, TypeError, AttributeError, ValueError) as exc:
        _fail(exc)


@router.delete("/{update_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a comment (its author, or a manager)")
def remove(update_id: int = Path(..., ge=1, le=2_147_483_647), user: dict = Depends(require_operator)) -> None:
    try:
        updates.delete(update_id, user)
    except (LookupError, PermissionError) as exc:
        _fail(exc)


@router.get("/mentions", summary="Comments that name me, newest first")
def mine(days: int = Query(30, ge=1, le=365), user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return updates.mentions_of(user.get("email", ""), days)


@router.get("/people", summary="Who can be @mentioned (for the picker)")
def people(q: str = Query("", max_length=60), _user: dict = Depends(require_viewer)) -> List[Dict[str, str]]:
    return updates.search_people(q)


@router.get("/counts", summary="How many comments each item has (for a badge on a list)")
def counts(item_type: str = Query(..., max_length=24), refs: str = Query(..., max_length=4000, description="Comma-separated"),
           _user: dict = Depends(require_viewer)) -> Dict[str, int]:
    try:
        return updates.counts(item_type, [r.strip() for r in refs.split(",") if r.strip()])
    except updates.UpdateError as exc:
        _fail(exc)

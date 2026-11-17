"""The monday push queue: changes the app wants to make on monday are staged here and sent only when a manager pushes.

Like git: look at what is staged (`changes`, `preview`), then `push`. While MONDAY_READ_ONLY is on, a push sends nothing and
says so. No endpoint here can change that switch."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.core.security import require_manager, require_operator, require_viewer
from app.services import monday_push

router = APIRouter()


def _ids(raw: Optional[str]) -> Optional[List[int]]:
    try:
        return [int(x) for x in raw.split(",") if x.strip()] if raw else None
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The change numbers must be whole numbers, separated by commas.")


@router.get("/status", summary="Is monday connected, is writing on, and how many changes are waiting")
def state(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return monday_push.status()


@router.get("/changes", summary="Staged changes (or pushed, failed, skipped), newest first")
def changes(state: Optional[str] = Query(None, pattern="^(?i)(STAGED|PUSHING|PUSHED|FAILED|SKIPPED|EXPIRED|CONFLICT)$"), _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return monday_push.changes(state)


@router.get("/preview", summary="What each waiting change would do, with what monday shows now")
def preview(ids: Optional[str] = Query(None, max_length=500, description="Comma-separated change numbers; blank for all"),
            board_id: Optional[str] = Query(None, max_length=32, description="Only this board's changes"),
            _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return monday_push.preview(_ids(ids), board_id)


@router.post("/push", summary="Send staged changes to monday (managers). Sends nothing while monday is read-only.")
def push(body: Dict[str, Any] = Body(default={}), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    ids = body.get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids) or len(ids) > 500):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids must be a list of change numbers.")
    board = body.get("board_id")
    if board is not None and not (isinstance(board, str) and board.isdigit() and len(board) <= 20):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="board_id must be a board number.")
    return monday_push.push(ids, who=user.get("email", ""), dry_run=bool(body.get("dry_run", False)), board_id=board, force=bool(body.get("force", False)))


@router.post("/changes/{change_id}/skip", summary="Drop a staged change without sending it")
def skip(change_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return monday_push.skip(change_id, user.get("email", ""), role=user.get("role", "VIEWER"))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/changes/{change_id}/retry", summary="Put a failed change back in the staged list")
def retry(change_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return monday_push.retry(change_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/changes/{change_id}/rebase", summary="After a conflict: keep this edit, made against what monday shows now (back to staged)")
def rebase(change_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return monday_push.rebase(change_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

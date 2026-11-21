"""Edit any board like monday does: type in a cell, add or move a row. Everything is STAGED (see /monday-push); nothing is sent until a manager pushes
and the owner has switched writing on."""

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.core.security import require_operator, require_viewer
from app.services import board_edit

router = APIRouter()


def _run(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _text(body: Dict[str, Any], key: str) -> str:
    v = body.get(key)
    if v is not None and not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{key}' must be text.")
    return "" if v is None else str(v)


@router.get("/boards", summary="Every board the app has a copy of, and whether it can be edited here")
def boards(_user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return board_edit.boards_list()


@router.get("/boards/{board_id}/columns", summary="A board's columns (type, labels, hint), groups and whether each can be edited: what the screen needs for pickers")
def columns(board_id: str, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return _run(board_edit.columns_info, board_id)


@router.post("/boards/{board_id}/items/{item_id}/cell", summary="Change one cell from typed text (fri, +3d, nov 20, a label's first letters...). Staged, undoable")
def cell(board_id: str, item_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.edit_cell, board_id, item_id, _text(body, "column"), _text(body, "text"), user)


@router.post("/jobs/from-monday/bulk", summary="Make jobs from several monday items at once ({ids:[...]}); once-only per item, one answer per item")
def jobs_from_monday_bulk(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services import jobs_board

    return _run(jobs_board.create_jobs_from_monday, body.get("ids"), user)


@router.post("/jobs/from-monday/{item_id}", summary="Make a job from a monday projects-board item that has none (the one-click 'Make a job')")
def job_from_monday(item_id: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services import jobs_board

    return _run(jobs_board.create_job_from_monday, item_id, user)


@router.post("/jobs/{ref}/link", summary="Link a job to a monday item (the 'Link it?' suggestion, or a pick). Saved on the job: audited and undoable; one item links to one job")
def link_job(ref: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services import records

    out = _run(records.patch, "job", ref, "monday_item_id", _text(body, "item_id"), user)
    return {**out, "linked": True, "message": f"{ref} is linked to monday item {out['now']}."}


@router.post("/jobs/{ref}/unlink", summary="Remove a job's link to its monday item (saved on the job: audited and undoable)")
def unlink_job(ref: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services import records

    out = _run(records.patch, "job", ref, "monday_item_id", "", user)
    return {**out, "linked": False, "message": f"{ref} is no longer linked to a monday item."}


@router.get("/boards/{board_id}/items", summary="The board's rows as one working list: real rows with each pending new row inside its group (pending:true, id new:<staged id>)")
def items(board_id: str, limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0, le=1_000_000), _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    out = _run(board_edit.board_items, board_id, limit, offset)
    if _user.get("role") in ("ADMIN", "MANAGER", "OPERATOR") and board_id != "jobs":
        from app.services import follow

        follow.annotate(_user, "board_item", out.get("items", []), lambda r: None if r.get("pending") else f"{board_id}:{r['id']}")     # a pending new row is not on monday yet
    from app.services import organize

    organize.annotate_board_rows(_user, board_id, out.get("items", []))
    return out


@router.post("/boards/{board_id}/items/{item_id}/comment", summary="Comment on a row (a pending new row's comment is posted once it is pushed). Staged")
def comment(board_id: str, item_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.comment_row, board_id, item_id, _text(body, "text"), user)


@router.post("/boards/{board_id}/items", summary="Add a row: name, optional group and cells typed as text. Staged")
def add_row(board_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    cells = body.get("cells") or {}
    if not isinstance(cells, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'cells' must be {column: text}.")
    if body.get("names") is not None:                      # several rows at once: one per name
        return _run(board_edit.add_rows, board_id, body.get("names"), _text(body, "group") or None, cells, user)
    return _run(board_edit.add_row, board_id, _text(body, "name"), _text(body, "group") or None, cells, user, None, None, _text(body, "client_key") or None)


@router.post("/boards/{board_id}/items/{item_id}/duplicate", summary="Copy a row. Staged")
def duplicate(board_id: str, item_id: str, body: Dict[str, Any] = Body(default={}), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.duplicate_row, board_id, item_id, body.get("with_updates") is True, user)


@router.post("/boards/{board_id}/items/{item_id}/move", summary="Move a row to another group (by name or first letters). Staged")
def move(board_id: str, item_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.move_row, board_id, item_id, _text(body, "group"), user)


@router.post("/boards/{board_id}/items/{item_id}/archive", summary="Delete a row = archive it. Staged, undoable until a manager pushes")
def archive(board_id: str, item_id: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.archive_row, board_id, item_id, user)


@router.post("/undo", summary="Take back my most recent staged edit (optionally on one board)")
def undo(body: Dict[str, Any] = Body(default={}), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    board = body.get("board_id")
    return _run(board_edit.undo_last, user, str(board) if board else None)


@router.post("/boards/{board_id}/columns", summary="Add a column: title, type (text, number, status, date, people, dropdown, checkbox, link, email, timeline, rating, hour) and labels for status/dropdown. Staged")
def add_column(board_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    labels = body.get("labels")
    if labels is not None and (not isinstance(labels, list) or not all(isinstance(x, str) for x in labels)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'labels' must be a list of text.")
    return _run(board_edit.add_column, board_id, _text(body, "title"), _text(body, "type"), labels, user)


@router.post("/boards/{board_id}/columns/{column}/rename", summary="Rename a column (by id or title). Staged")
def rename_column(board_id: str, column: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.rename_column, board_id, column, _text(body, "title"), user)


@router.post("/boards/{board_id}/columns/{column}/delete", summary="Delete a column and every value in it. Staged, undoable until a manager pushes")
def delete_column(board_id: str, column: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.delete_column, board_id, column, user)


@router.post("/boards/{board_id}/groups", summary="Add a group. Staged")
def add_group(board_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.add_group, board_id, _text(body, "title"), user)


@router.post("/boards/{board_id}/groups/{group}/rename", summary="Rename a group (by id or first letters of its title). Staged")
def rename_group(board_id: str, group: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(board_edit.rename_group, board_id, group, _text(body, "title"), user)


@router.post("/boards/{board_id}/paste", summary="Paste cells from Excel: the first goes into (item, column), the rest follow. All or nothing; dry_run shows what each cell becomes. Staged")
def paste(board_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    top = body.get("top_left")
    if not isinstance(top, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'top_left' must be {item_id, column}.")
    return _run(board_edit.paste, board_id, _text(top, "item_id"), _text(top, "column"), body.get("rows"), user, body.get("dry_run") is True, body.get("tsv"))


@router.post("/boards/{board_id}/fill-down", summary="Copy one cell down to the rows below it (through_item) or to listed rows (to_items). dry_run shows the result. Staged")
def fill_down(board_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    through = body.get("through_item")
    return _run(board_edit.fill_down, board_id, _text(body, "column"), _text(body, "from_item"), user, str(through) if through is not None else None,
                body.get("to_items"), body.get("dry_run") is True)

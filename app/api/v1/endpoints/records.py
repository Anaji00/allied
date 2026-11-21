"""Edit any record in place from typed text, and list its fields (type, editable, choices) so one inline editor works everywhere."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status

from app.core.security import require_operator, require_viewer
from app.services import records

router = APIRouter()
KIND = Path(..., pattern="^(job|rfq|order|po|shipment|product|customer|vendor)$")


def _run(fn, *args):
    try:
        return fn(*args)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except ValueError as exc:                                # RecordError and the services' own plain-language errors
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/undo", summary="Put back my own most recent record edit (from the old value kept in the audit trail)")
def undo(user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(records.undo_last, user)


@router.get("/{kind}/{ref:path}/fields", summary="The record's fields: label, type, editable, choices and the value now")
def fields(kind: str = KIND, ref: str = Path(..., max_length=80), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return _run(records.fields, kind, ref, user.get("role", "VIEWER"))


@router.patch("/{kind}/{ref:path}", summary="Change one field from typed text (dates like fri or +3d, a choice by its first letters...)")
def patch(kind: str = KIND, ref: str = Path(..., max_length=80), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    for k in ("field", "text"):
        v = body.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (str, int, float))):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{k}' must be text.")
    return _run(records.patch, kind, ref, str(body.get("field") or ""), "" if body.get("text") is None else str(body["text"]), user)

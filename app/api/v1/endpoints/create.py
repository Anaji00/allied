"""Add anything on a dime: POST /create/{kind} with plain fields. Optional to_monday also stages the same thing as a new row on a board (staged only)."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Path, status

from app.core.security import require_operator
from app.services import create_anything

router = APIRouter()


@router.post("/{kind}", summary="Add a job, rfq, product, order, po, shipment or board_row from plain fields; returns {kind, ref, title, link, message}")
def create(kind: str = Path(..., pattern="^(job|rfq|product|order|po|shipment|board_row)$"), body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    fields = body.get("fields", {k: v for k, v in body.items() if k != "to_monday"})
    try:
        return create_anything.create(kind, fields, user, body.get("to_monday"))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except ValueError as exc:                 # CreateError and the services' own plain-language errors
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

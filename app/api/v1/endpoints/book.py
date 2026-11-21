"""The book: the rows of Orders (RFQs, sales orders, purchase orders) or Jobs, each cell with its type and whether it is editable in place."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import require_operator
from app.services import book

router = APIRouter()


@router.get("", summary="Rows for area=orders (kind rfq|so|po|all) or area=jobs, with per-cell type and editable")
def get_book(area: str = Query("orders", max_length=12), kind: str = Query("all", max_length=8), q: str = Query("", max_length=100), limit: int = Query(200, ge=1, le=500),
             status_: str = Query("", alias="status", max_length=40), sort: str = Query("", max_length=40), desc: bool = False, mine: bool = Query(False, description="Only rows waiting on me"), late: bool = Query(False, description="Only jobs waiting more than 3 days"),
             user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return book.rows(area, kind, q, limit, status_, sort, desc, mine, user.get("email", ""), late)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

"""One search across orders, RFQs, vendor jobs, shipments, monday board rows (the app's copy), comments and people."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, Query

from app.core.security import require_viewer
from app.services import search as search_service

router = APIRouter()


@router.get("", summary="Search everything at once; results are grouped by kind with where to open each")
def find(q: str = Query(..., min_length=2, max_length=80), limit: int = Query(8, ge=1, le=25), _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return search_service.search(q, limit)

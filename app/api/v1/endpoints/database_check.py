"""Check the database file now (a manager). Read only: it never repairs anything."""

from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.security import require_manager

router = APIRouter()


@router.post("/check", summary="Check the database file now: the same read-only check as the health page, run again at once (a manager)")
def check_now(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.core.database import check_live_database, invalidate_check

    invalidate_check()
    return check_live_database(force=True)

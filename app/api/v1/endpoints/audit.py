"""Audit log API endpoint for querying the immutable audit trail."""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_manager
from app.services.database_service import DatabaseService

router = APIRouter()


@router.get("", summary="Query audit trail (Manager+ only)")
def get_audit_log(
    resource_type: Optional[str] = Query(None, description="Filter by resource type (ORDER, USER, SAGE_EXPORT, etc.)"),
    actor_email: Optional[str] = Query(None, description="Filter by actor email"),
    q: Optional[str] = Query(None, max_length=200, description="Search record id, detail, action and who"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_manager),
):
    """Returns the immutable audit trail with optional filters, newest first.

    Requires ADMIN or MANAGER role. `total` counts every matching entry, so
    `offset` pages back through older ones.
    """
    entries, total = DatabaseService.get_audit_log(
        db=db,
        resource_type=resource_type,
        actor_email=actor_email,
        q=q,
        limit=limit,
        offset=offset,
        with_total=True,
    )
    return {
        "total": total,
        "entries": entries,
        "resource_types": DatabaseService.audit_resource_types(db),
    }

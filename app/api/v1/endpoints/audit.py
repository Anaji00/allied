"""Audit log API endpoint for querying the immutable audit trail."""

import json
import re
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_manager
from app.services.database_service import DatabaseService

router = APIRouter()

_HEX = re.compile(r"\b[0-9a-f]{12,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def plain_entry(e: Dict[str, Any]) -> str:
    """One audit entry as a sentence a person can read: who, what, to what, and the detail, with internal ids (long hex or UUID ids, event ids) left out. A JSON detail is shown as 'field: before -> after'."""
    who = str(e.get("actor_email") or "someone").split("@")[0].replace(".", " ").replace("-", " ").title()
    what = " ".join(str(e.get("action") or "").replace("_", " ").lower().split())
    kind = " ".join(str(e.get("resource_type") or "").replace("_", " ").lower().split())
    ref = str(e.get("resource_id") or "")
    ref = "" if _HEX.fullmatch(ref) or len(ref) > 40 else ref
    detail = str(e.get("detail") or "")
    if detail.startswith("{"):
        try:
            d = json.loads(detail)
            if "field" in d:
                detail = f"{d.get('label') or d['field']}: {d.get('before') if d.get('before') not in (None, '') else 'blank'} -> {d.get('after') if d.get('after') not in (None, '') else 'blank'}"
        except ValueError:
            pass
    detail = _HEX.sub("", detail)
    detail = " ".join(detail.split())[:200]
    return f"{who} {what}" + (f" {kind}" if kind else "") + (f" {ref}" if ref else "") + (f": {detail}" if detail else "") + "."


@router.get("", summary="Query audit trail (Manager+ only)")
def get_audit_log(
    resource_type: Optional[str] = Query(None, description="Filter by resource type (ORDER, USER, SAGE_EXPORT, etc.)"),
    actor_email: Optional[str] = Query(None, description="Filter by actor email"),
    action: Optional[str] = Query(None, description="Filter by action (LOGIN, LOGIN_FAILED, UPDATE, etc.)"),
    start_date: Optional[str] = Query(None, description="Filter entries on or after YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Filter entries on or before YYYY-MM-DD"),
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
        action=action,
        start_date=start_date,
        end_date=end_date,
        q=q,
        limit=limit,
        offset=offset,
        with_total=True,
    )
    for e in entries:
        e["text"] = plain_entry(e)
    return {
        "total": total,
        "entries": entries,
        "resource_types": DatabaseService.audit_resource_types(db),
    }

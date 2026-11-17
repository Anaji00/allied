"""What needs the signed-in person, in one call."""

from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.core.security import require_viewer
from app.services.coordination import needs

router = APIRouter()


@router.get("/needs", summary="Drafts waiting for my OK, my open alerts and where I hold the ball: one count and one list")
def my_needs(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return needs.for_person(user)

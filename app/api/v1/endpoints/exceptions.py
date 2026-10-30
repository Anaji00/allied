"""FastAPI router for Operations Exceptions & Discrepancies Queue."""

from typing import Any, Dict, List, Optional
from app.core.security import require_operator, require_viewer
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.services.exception_queue_service import (
    ExceptionItem,
    ResolveExceptionRequest,
    exception_queue_service,
)

router = APIRouter()


@router.get(
    "",
    response_model=List[ExceptionItem],
    summary="List Operational Discrepancies & Exceptions",
)
def list_exceptions(
    status_filter: Optional[str] = Query(None, description="OPEN or RESOLVED"),
    _user: dict = Depends(require_viewer),
) -> List[ExceptionItem]:
    """Returns active operational exceptions (stale prices, invoice variances, unmatched SKUs)."""
    return exception_queue_service.list_exceptions(status_filter=status_filter)


@router.post(
    "/{exception_id}/resolve",
    response_model=Dict[str, Any],
    summary="Resolve Exception with 1-Click Action",
)
def resolve_exception(
    exception_id: str,
    request: ResolveExceptionRequest,
    current_user: dict = Depends(require_operator),
) -> Dict[str, Any]:
    """Applies resolution action (override price, send dispute notice, map alias)."""
    try:
        return exception_queue_service.resolve_exception(
            exception_id, request, actor_email=current_user["email"]
        )
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exception {exception_id} not found",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to resolve exception: {str(e)}",
        )

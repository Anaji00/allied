"""FedEx set-up: what is configured, a connection check, and a live price lookup (prices only)."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import require_manager, require_operator
from app.services import fedex

router = APIRouter()


@router.get("/status", summary="Is FedEx set up, and what is still missing")
def fedex_status(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return fedex.status()


@router.post("/check", summary="Log in to FedEx to prove the credentials work (books and buys nothing)")
def fedex_check(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return fedex.check()


@router.get("/quote", summary="Live FedEx parcel prices to an address (prices only)")
def fedex_quote(address: str = Query(..., min_length=5, max_length=300, description="Ship-to address with ZIP or postal code"),
                weight_lbs: Optional[float] = Query(None, gt=0, le=2000), _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    try:
        return fedex.quote(address, weight_lbs)
    except fedex.FedExError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

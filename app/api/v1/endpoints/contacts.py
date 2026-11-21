"""Contacts: customers, vendors and carriers with their last order and open orders. Edit a customer or vendor with PATCH /records/{customer|vendor}/{ref}."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.security import require_operator
from app.services import contacts

router = APIRouter()


@router.get("", summary="Customers, vendors or carriers: name, email, phone, last order, open orders, owner")
def listing(kind: str = Query("customer", max_length=10), q: str = Query("", max_length=100), limit: int = Query(200, ge=1, le=1000), sort: str = Query("name", max_length=16),
            _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return contacts.listing(kind, q, limit, sort)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

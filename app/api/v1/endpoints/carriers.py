"""Carriers: the rate card the team keeps current, and which carriers can give live prices.

Writes only change the app's own rate card file; nothing is booked or bought, and nothing goes to a carrier."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.config import settings
from app.core.audit import audit_log
from app.core.security import require_manager, require_operator, require_viewer
from app.services import carrier_providers, carrier_rates

router = APIRouter()


def _audit(action: str, line_id: str, user: dict, detail: str = "") -> None:
    audit_log(action, "carrier_rate", line_id, actor_email=user.get("email", "unknown"), actor_role=user.get("role", ""), detail=detail)


def _bad(exc: Exception, code: int = status.HTTP_400_BAD_REQUEST):
    raise HTTPException(status_code=code, detail=str(exc).strip("'\""))


@router.get("/rates", summary="The rate card, how fresh each price is, and which carriers give live prices")
def rates(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    lines = carrier_rates.view()
    return {"lines": lines, "stale_after_days": settings.CARRIER_RATE_STALE_DAYS, "needs_review": sum(1 for r in lines if r["stale"] and r["enabled"]),
            "live": carrier_providers.statuses(), "regions": list(carrier_rates.REGIONS)}


@router.post("/rates", status_code=status.HTTP_201_CREATED, summary="Add a carrier price line")
def add_line(data: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        line = carrier_rates.add(data)
    except carrier_rates.RateCardError as exc:
        _bad(exc)
    _audit("CARRIER_RATE_ADDED", line["id"], user)
    return line


@router.patch("/rates/{line_id}", summary="Change a price line (typing a new price counts as checking it today)")
def change_line(line_id: str, data: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        line = carrier_rates.update(line_id, {k: v for k, v in data.items() if k != "id"})
    except carrier_rates.RateCardError as exc:
        _bad(exc)
    except LookupError as exc:
        _bad(exc, status.HTTP_404_NOT_FOUND)
    _audit("CARRIER_RATE_CHANGED", line_id, user, ", ".join(sorted(data)))
    return line


@router.post("/rates/{line_id}/confirm", summary="'I checked this price today'")
def confirm_line(line_id: str, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        line = carrier_rates.confirm(line_id)
    except LookupError as exc:
        _bad(exc, status.HTTP_404_NOT_FOUND)
    _audit("CARRIER_RATE_CONFIRMED", line_id, user)
    return line


@router.delete("/rates/{line_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a price line")
def remove_line(line_id: str, user: dict = Depends(require_manager)) -> None:
    try:
        carrier_rates.remove(line_id)
    except LookupError as exc:
        _bad(exc, status.HTTP_404_NOT_FOUND)
    _audit("CARRIER_RATE_REMOVED", line_id, user)


@router.get("/quote", summary="Shipping prices to an address from every carrier: live prices first, then the rate card")
def quote(address: str = Query(..., min_length=3, max_length=300), weight_lbs: Optional[float] = Query(None, gt=0, le=100000),
          origin: str = Query("HQ", max_length=40), _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    from app.services.logistics_dispatcher import logistics_dispatcher

    return [{"carrier": q.carrier_name, "service": q.service_level, "live": q.service_level.startswith("Live "),
             "transit_days": q.estimated_transit_days, "usd": q.rate_usd, "cad": q.rate_cad, "cny": q.rate_cny}
            for q in logistics_dispatcher.get_carrier_quotes(origin, address, weight_lbs)]

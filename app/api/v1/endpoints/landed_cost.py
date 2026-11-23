"""Landed cost, the tariff table, the markup workbook and the freight quote request. Managers only: these hold supplier pricing and markups."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.core.security import require_manager
from app.services import landed_cost as lc

router = APIRouter()


def _fail(exc: Exception):
    raise HTTPException(status_code=getattr(exc, "status", 400), detail=getattr(exc, "message", str(exc)))


class RateBody(BaseModel):
    category: str = Field(..., max_length=120)
    rate_percent: str
    hs_code: str = ""
    country: str = ""
    effective_from: str = Field("", description="As-of date YYYY-MM-DD; blank = today")
    note: str = ""


class LineBody(BaseModel):
    description: str = ""
    quantity: str
    unit_cost: str
    category: str = ""
    hs_code: str = ""
    country: str = ""
    from_ocr: bool = Field(False, description="The figures were read from a scan: they are flagged for a person to confirm")


class SheetBody(BaseModel):
    lines: List[LineBody]
    freight: Optional[str] = Field(None, description="Blank means unknown, never zero")
    currency: str = "USD"
    as_of: Optional[str] = None
    tiers: Optional[List[str]] = Field(None, description="Markup percentages; blank uses the default tiers")
    job_id: Optional[int] = None
    rfq_id: Optional[int] = None
    vendor_quote_ref: str = ""


class PickBody(BaseModel):
    tier: str


class CargoBody(BaseModel):
    text: str = Field(..., max_length=100000, description="The vendor quote's text")


class FreightRfqBody(BaseModel):
    forwarder_email: str
    origin: str = ""
    destination: str = ""
    text: str = Field(..., max_length=100000, description="The vendor quote's text, to read the weights and dimensions from")
    job_id: Optional[int] = None


@router.get("/tariffs", summary="The tariff table, newest first, with a stale warning")
def tariffs(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return lc.list_rates()


@router.post("/tariffs", summary="Add a tariff rate with its as-of date (the newest rate in force on a date is used)")
def add_tariff(body: RateBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return lc.set_rate(body.category, body.rate_percent, user, body.hs_code, body.country, body.effective_from, body.note)
    except lc.CostError as exc:
        _fail(exc)


@router.delete("/tariffs/{rate_id}", summary="Remove a tariff rate")
def delete_tariff(rate_id: int, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        lc.delete_rate(rate_id, user)
        return {"deleted": rate_id}
    except lc.CostError as exc:
        _fail(exc)


@router.post("/calculate", summary="Work out the landed cost and the markup tiers without saving (unknown duty or freight is flagged, never zero)")
def calculate(body: SheetBody, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return lc.compute([l.model_dump() for l in body.lines], body.freight, body.currency, body.as_of, body.tiers)
    except lc.CostError as exc:
        _fail(exc)


@router.post("/sheets", summary="Save a landed cost sheet")
def create_sheet(body: SheetBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        data = body.model_dump()
        return lc.create_sheet(data, user)
    except lc.CostError as exc:
        _fail(exc)


@router.get("/sheets/{sheet_id}", summary="A saved landed cost sheet")
def get_sheet(sheet_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return lc.get_sheet(sheet_id)
    except lc.CostError as exc:
        _fail(exc)


@router.post("/sheets/{sheet_id}/pick", summary="Record which markup tier was picked (audited)")
def pick(sheet_id: int, body: PickBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return lc.pick_tier(sheet_id, body.tier, user)
    except lc.CostError as exc:
        _fail(exc)


@router.post("/sheets/{sheet_id}/confirm", summary="A person confirms the figures read from a scan against the image (needed before a tier can be picked)")
def confirm(sheet_id: int, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return lc.confirm_sheet(sheet_id, user)
    except lc.CostError as exc:
        _fail(exc)


@router.get("/sheets/{sheet_id}/xlsx", summary="The markup workbook (xlsx) for a saved sheet")
def sheet_xlsx(sheet_id: int, _user: dict = Depends(require_manager)) -> Response:
    try:
        data = lc.sheet_xlsx(sheet_id)
    except lc.CostError as exc:
        _fail(exc)
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="landed-cost-{sheet_id}.xlsx"'})


@router.post("/cargo", summary="Read weights, dimensions and package counts out of a vendor quote's text (flagged: a person confirms them)")
def cargo(body: CargoBody, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return lc.parse_cargo(body.text)


@router.post("/freight-rfq", summary="Draft a freight quote request to the forwarder from the quote's weights and dimensions (staged for a person to send; names no customer)")
def freight_rfq(body: FreightRfqBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return lc.freight_rfq_draft(lc.parse_cargo(body.text), body.forwarder_email, body.origin, body.destination, user, body.job_id)
    except lc.CostError as exc:
        _fail(exc)

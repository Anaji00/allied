"""Fulfilment pipelines (UPS parcel, Mountain Movers, China forwarder), the forwarder weekly digest and the after-delivery freight close."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import require_manager, require_operator
from app.services import freight_close, pipelines

router = APIRouter()


def _fail(exc: Exception):
    raise HTTPException(status_code=getattr(exc, "status", 400), detail=getattr(exc, "message", str(exc)))


class PipelineBody(BaseModel):
    pipeline: str = Field(..., description="US_OFFICE_PARCEL, US_MOUNTAIN_MOVERS or CN_FREIGHT_FORWARDER")
    tracking_no: str = ""
    carrier: str = ""
    bol_file_id: Optional[int] = None
    quote_options: Optional[List[Dict[str, Any]]] = None


class TextBody(BaseModel):
    text: str = Field(..., max_length=200000)


class DigestBody(TextBody):
    apply: bool = Field(False, description="False only shows what would change")
    sender: str = Field("", max_length=320, description="The From address of the mail the text came from: dates are applied only for a freight forwarder's own address (FORWARDER_DOMAINS), or by a manager")


class DeliveryBody(BaseModel):
    sales_order_number: str
    freight_actual: str
    freight_quoted: Optional[str] = None
    po_ids: List[int] = []


@router.get("/shipment/{shipment_id}", summary="A shipment's pipeline record: tracking, BOL, quote options, freight actual and quoted, variance")
def get_pipeline(shipment_id: int, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    got = pipelines.get(shipment_id)
    if got is None:
        raise HTTPException(status_code=404, detail="This shipment has no pipeline yet.")
    return got


@router.put("/shipment/{shipment_id}", summary="Set a shipment's pipeline and tracking number (a UPS number is checked)")
def put_pipeline(shipment_id: int, body: PipelineBody, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return pipelines.set_pipeline(shipment_id, body.pipeline, user, body.tracking_no, body.carrier, body.bol_file_id, body.quote_options)
    except pipelines.PipelineError as exc:
        _fail(exc)


@router.post("/parse/mountain-movers", summary="Read quote options, BOL numbers, pickup and delivery dates out of a Mountain Movers email (suggestions: a person confirms)")
def parse_mm(body: TextBody, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return pipelines.parse_mountain_movers(body.text)


@router.post("/forwarder-digest", summary="Read a forwarder's weekly update; with apply=true move each matching shipment's ETA (ambiguous dates are never applied)")
def forwarder_digest(body: DigestBody, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return pipelines.apply_forwarder_digest(body.text, user, body.apply, body.sender)
    except pipelines.PipelineError as exc:
        _fail(exc)


@router.post("/shipment/{shipment_id}/delivered", summary="Delivery confirmed: record the freight paid, flag a variance, stage the SO-to-invoice payload for Sage and open the delivery payments (manager)")
def delivered(shipment_id: int, body: DeliveryBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return freight_close.confirm_delivery(shipment_id, user, body.sales_order_number, body.freight_actual, body.freight_quoted, body.po_ids)
    except freight_close.FreightError as exc:
        _fail(exc)

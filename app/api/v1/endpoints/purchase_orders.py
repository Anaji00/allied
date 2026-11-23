"""Purchase orders to vendors: read, edit, approve (after the design is approved), PDF, email draft."""

import re
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app.core.security import require_manager, require_operator, require_viewer
from app.services.coordination import jobs, purchase_orders

router = APIRouter()


def _fail(exc: Exception):
    """Our own plain-language messages (JobError) and 404s are shown as they are; anything else becomes a fixed message, never raw text."""
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    if isinstance(exc, jobs.JobError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That request could not be processed. Check what was entered and try again.")


@router.get("", summary="Every purchase order once (the PO table, vendor lines and orders merged by PO number)")
def all_pos(q: str = Query("", max_length=80), vendor: str = Query("", max_length=80), status_text: str = Query("", alias="status", max_length=40),
            user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.core.money_view import hide_pos

    return hide_pos(purchase_orders.unified(q, vendor, status_text, staff=user.get("role") in ("ADMIN", "MANAGER", "OPERATOR")), user)


@router.get("/{po_id}", summary="One purchase order")
def one(po_id: int, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.core.money_view import hide_po

    try:
        return hide_po(purchase_orders.get(po_id), _user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.patch("/{po_id}", summary="Change lines, terms, ship via, good-thru or the vendor email until it is sent")
def change(po_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return purchase_orders.update(po_id, body, user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.post("/{po_id}/approve", summary="A manager approves it (400 until the design is approved); the PO number is assigned now")
def approve(po_id: int, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return purchase_orders.approve(po_id, user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.get("/{po_id}/pdf", summary="The purchase order as a PDF in Allied's layout")
def pdf(po_id: int, _user: dict = Depends(require_viewer)) -> Response:
    try:
        raw = purchase_orders.get(po_id)["po_number"] or f"draft_{po_id}"
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw)).strip("_") or f"draft_{po_id}"
        return Response(purchase_orders.pdf(po_id), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="PO_{name}.pdf"'})
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


def _paper(po_id: int, destination: str, currency: str) -> Dict[str, Any]:
    from app.services import import_paperwork

    try:
        return import_paperwork.build(po_id, destination, currency)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.get("/{po_id}/import-paperwork", summary="What the customs invoice and packing list for goods coming in will say, and what is still missing (China and Canada origin; destination US or CA)")
def import_paperwork_facts(po_id: int, destination: str = Query("", max_length=2, description="US or CA; blank follows the shipment's route"), currency: str = Query("", max_length=3), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _paper(po_id, destination, currency)


@router.get("/{po_id}/commercial-invoice.pdf", summary="The commercial invoice for the incoming goods as a PDF; marked INCOMPLETE while anything is missing")
def commercial_invoice(po_id: int, destination: str = Query("", max_length=2, description="US or CA; blank follows the shipment's route"), currency: str = Query("", max_length=3), _user: dict = Depends(require_operator)) -> Response:
    from app.services import import_paperwork

    info = _paper(po_id, destination, currency)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(info["po_number"] or f"draft_{po_id}")).strip("_") or f"draft_{po_id}"
    return Response(import_paperwork.commercial_invoice_pdf(info), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="CI_{name}.pdf"'})


@router.get("/{po_id}/packing-list.pdf", summary="The packing list for the incoming goods as a PDF: no vendor name, no prices, no customer (it goes on the outside of a blind shipment)")
def packing_list(po_id: int, destination: str = Query("", max_length=2, description="US or CA; blank follows the shipment's route"), currency: str = Query("", max_length=3), _user: dict = Depends(require_operator)) -> Response:
    from app.services import import_paperwork

    info = _paper(po_id, destination, currency)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(info["po_number"] or f"draft_{po_id}")).strip("_") or f"draft_{po_id}"
    return Response(import_paperwork.packing_list_pdf(info), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="PL_{name}.pdf"'})


@router.get("/{po_id}/bill-match", summary="The PO's lines next to the supplier bills checked against it, with the differences (quantity, unit price, freight) as figures")
def bill_match(po_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.core.money_view import hide_po
    from app.services import bill_match as bm

    try:
        po = hide_po(purchase_orders.get(po_id), user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)
    return bm.for_po(po_id, po)


@router.post("/{po_id}/email-draft", summary="Queue the vendor email (PO PDF + design files) for a person to OK; nothing is sent here")
def email_draft(po_id: int, body: Dict[str, Any] = Body(default={}), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        ids = body.get("file_ids") if isinstance(body, dict) else None
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, int) for i in ids)):
            raise jobs.JobError("file_ids must be a list of file numbers.")
        return purchase_orders.email_draft(po_id, user, ids)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{po_id}/resolve-send", summary="A manager settles a PO whose send was interrupted: it did go ({sent: true}) or it did not")
def resolve_send(po_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return purchase_orders.resolve_send(po_id, bool(body.get("sent")), user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{po_id}/cancel", summary="A manager cancels a purchase order, with a reason. Nothing is emailed; if the vendor already has it, the answer says to tell them")
def cancel(po_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        if not isinstance(body.get("reason"), str):
            raise jobs.JobError("Give the reason as text.")
        return purchase_orders.cancel(po_id, body["reason"], user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{po_id}/revision-ack", summary="Record which drawing revision the vendor acknowledged for one line: {line, revision, by}")
def revision_ack(po_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return purchase_orders.record_revision_ack(po_id, body.get("line"), body.get("revision"), body.get("by"), user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.patch("/{po_id}/customs", summary="Customs details by line ({lines: [{line, hts_code, origin, uom, net_kg, cert_required}], incoterm, incoterm_place}) on an approved or sent PO; lines are not resent")
def customs(po_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return purchase_orders.set_customs(po_id, body, user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.post("/{po_id}/acknowledge", summary="Mark a sent purchase order as acknowledged by the vendor")
def acknowledge(po_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return purchase_orders.acknowledge(po_id, user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)

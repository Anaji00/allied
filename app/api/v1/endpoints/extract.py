"""Less typing: extract fields from text or a file for a person to review, prefill, duplicate, create from text, bulk edit with a dry run, remembered choices, and what people change. Nothing here saves a record
or sends anything."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.core.security import require_manager, require_operator, require_viewer
from app.services import assist, extract

router = APIRouter()


def _fail(exc: Exception):
    raise HTTPException(status_code=getattr(exc, "status", 400), detail=getattr(exc, "message", str(exc)))


async def _pdf(file: UploadFile) -> bytes:
    cap = settings.SUBMISSION_MAX_MB * 1024 * 1024
    data = await file.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(status_code=413, detail=f"That file is larger than {settings.SUBMISSION_MAX_MB} MB. Use a smaller scan or split it.")
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")
    return data


class EmailIn(BaseModel):
    text: str = Field("", max_length=60000, description="The email's text (paste it)")
    mail_id: Optional[int] = Field(None, description="Or the number of an email the app already holds")
    sender: str = Field("", max_length=320)
    subject: str = Field("", max_length=300)


class QuoteIn(BaseModel):
    text: str = Field(..., max_length=100000)


class CallIn(BaseModel):
    text: str = Field(..., max_length=60000)
    use_ai: bool = Field(False, description="Ask the AI for a better summary (uses the free quota); the plain summary is always there")


class FeedbackIn(BaseModel):
    extraction_id: str
    fields: Dict[str, Any] = Field(..., description="{name: final value} or {name: {shown, final}}: what the person saved")


class FromTextIn(BaseModel):
    text: str = Field(..., max_length=60000)
    kind: str = ""
    sender: str = ""


class BulkIn(BaseModel):
    kind: str
    refs: List[str]
    changes: Dict[str, Any]
    dry_run: bool = Field(True, description="True (the default) only shows what would change")


@router.post("/extract/rfq-from-email", summary="Read an RFQ out of an email: fields with confidence and source. Saves nothing")
def rfq_from_email(body: EmailIn, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    extract.set_by(_user.get("email", ""))
    try:
        return extract.rfq_from_email(body.text, body.mail_id, body.sender, body.subject)
    except extract.ExtractError as exc:
        _fail(exc)


@router.post("/extract/order-from-email", summary="Read a customer order out of an email: fields with confidence and source. Saves nothing")
def order_from_email(body: EmailIn, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    extract.set_by(_user.get("email", ""))
    try:
        return extract.order_from_email(body.text, body.mail_id, body.sender, body.subject)
    except extract.ExtractError as exc:
        _fail(exc)


@router.post("/extract/po-from-pdf", summary="Read a scanned or digital purchase order (PDF): fields with confidence, page and source line. Saves nothing")
async def po_from_pdf(file: UploadFile = File(...), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    data = await _pdf(file)
    me = _user.get("email", "")

    def run():
        extract.set_by(me)
        return extract.po_from_pdf(data, file.filename or "")

    try:
        return await run_in_threadpool(run)
    except extract.ExtractError as exc:
        _fail(exc)


@router.post("/extract/vendor-quote", summary="Read a vendor's quote (text, or a PDF file with the form field 'file'): number, total, validity, lead time, terms, weights. Saves nothing")
async def vendor_quote(file: Optional[UploadFile] = File(None), text: str = Form(""), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    pdf = await _pdf(file) if file is not None else None
    me = _user.get("email", "")

    def run():
        extract.set_by(me)
        return extract.vendor_quote(text, pdf)

    try:
        return await run_in_threadpool(run)
    except extract.ExtractError as exc:
        _fail(exc)


@router.post("/extract/call-to-job-note", summary="Turn a call's text into a short note with the PO numbers, dates and who-will-do-what. Saves nothing")
def call_to_job_note(body: CallIn, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    extract.set_by(_user.get("email", ""))
    try:
        return extract.call_to_job_note(body.text, body.use_ai)
    except extract.ExtractError as exc:
        _fail(exc)


@router.post("/extract/feedback", summary="Call this when the person saves the form: what they kept, changed or added is counted per field")
def feedback(body: FeedbackIn, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return extract.feedback(body.extraction_id, body.fields, user)
    except extract.ExtractError as exc:
        _fail(exc)


@router.get("/ai/extraction-stats", summary="How often people kept or changed each suggested field, per way it was found (manager)")
def extraction_stats(days: int = Query(180, ge=1, le=3650), _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return extract.extraction_stats(days)


@router.get("/prefill/{kind}", summary="Likely values for a new order, PO, RFQ or shipment, each with where it came from")
def prefill(kind: str, customer: str = Query("", max_length=256), vendor: str = Query("", max_length=256), job: str = Query("", max_length=64), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return assist.prefill(kind, customer, vendor, job)
    except assist.AssistError as exc:
        _fail(exc)


@router.get("/records/{kind}/{ref}/duplicate-template", summary="A record's fields without its numbers, dates and status, for 'make another like this'")
def duplicate_template(kind: str, ref: str, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return assist.duplicate_template(kind, ref)
    except assist.AssistError as exc:
        _fail(exc)


@router.post("/create/from-text", summary="Paste text: it says what it is (RFQ, order, shipment) and proposes the body to create. Saves nothing")
def create_from_text(body: FromTextIn, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    extract.set_by(_user.get("email", ""))
    try:
        return assist.create_from_text(body.text, body.kind, body.sender)
    except (assist.AssistError, extract.ExtractError) as exc:
        _fail(exc)


@router.post("/records/bulk", summary="Owner, tags and who has the ball for many records, with a dry run first (default)")
def bulk(body: BulkIn, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return assist.bulk(body.kind, body.refs, body.changes, user, body.dry_run)
    except assist.AssistError as exc:
        _fail(exc)


@router.get("/me/defaults", summary="My remembered choices (last customer, vendor, currency, forwarder, owner, view)")
def get_defaults(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return assist.get_defaults(user)


@router.put("/me/defaults", summary="Remember some choices for me (blank forgets one)")
def put_defaults(values: Dict[str, Any] = Body(...), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return assist.put_defaults(user, values)
    except assist.AssistError as exc:
        _fail(exc)

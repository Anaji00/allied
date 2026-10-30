"""AI tools: read, listen, translate, draft and suggest. Nothing here commits.

Every result is stored (app.services.ai.store). Results someone must act on
wait as SUGGESTED until a person approves or rejects them here.
"""

import hmac
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.core.security import get_current_user, require_operator, require_viewer
from app.services.ai import features, speech, store
from app.services.ai.drawings import check_drawings
from app.services.ai.providers import AIUnavailableError, get_provider

router = APIRouter()

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _fail(exc: Exception):
    if isinstance(exc, AIUnavailableError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise exc


def _read(upload: UploadFile) -> bytes:
    """Reads in chunks and stops as soon as the upload passes the limit."""
    chunks, size = [], 0
    while chunk := upload.file.read(1024 * 1024):
        size += len(chunk)
        if size > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File is larger than 25 MB.")
        chunks.append(chunk)
    if not size:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{upload.filename or 'File'} is empty.")
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Status and review queue
# ---------------------------------------------------------------------------


@router.get("/status", summary="Which AI engine is in use and whether it is ready")
def ai_status(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return {
        "engine": get_provider().status(),
        "recordings": speech.status(),
        "confidence_threshold": settings.AI_CONFIDENCE_THRESHOLD,
        "email_triage_enabled": settings.AI_EMAIL_TRIAGE_ENABLED,
        "languages": features.LANGUAGES,
        "queue": store.counts(),
    }


@router.get("/suggestions", summary="AI results, newest first")
def list_suggestions(
    feature: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    _user: dict = Depends(require_viewer),
) -> Dict[str, Any]:
    return {"counts": store.counts(), "items": store.list_recent(feature, status_filter, limit)}


class ReviewRequest(BaseModel):
    approve: bool
    note: str = ""


@router.post("/suggestions/{suggestion_id}/review", summary="Approve or reject an AI suggestion")
def review_suggestion(suggestion_id: int, body: ReviewRequest, user: dict = Depends(require_operator)):
    row = store.review(suggestion_id, body.approve, user, body.note)
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="That suggestion is not waiting for review.")
    return row


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class TriageRequest(BaseModel):
    sender: str = ""
    subject: str = ""
    body: str = Field(..., min_length=1)


@router.post("/email/triage", summary="Sort an email and read any order out of it")
def email_triage(body: TriageRequest, user: dict = Depends(require_operator)):
    try:
        result, sid = features.triage_email(body.sender, body.subject, body.body, actor=user.get("email", "system"))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"suggestion_id": sid, "result": result, "needs_review": store.get(sid)["needs_review"]}


class DraftRequest(BaseModel):
    kind: Literal["order_acknowledgement", "supplier_chaser", "delivery_update", "invoice_dispute", "general_reply"]
    facts: str = Field(..., min_length=1, description="What the email must say; the AI adds nothing else")
    language: Literal["en", "vi", "zh"] = "en"
    recipient: str = ""


@router.post("/email/draft", summary="Draft an email for a person to check and send")
def email_draft(body: DraftRequest, user: dict = Depends(require_operator)):
    try:
        result, sid = features.draft_email(
            body.kind, body.facts, body.language, body.recipient, actor=user.get("email", "system")
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"suggestion_id": sid, "result": result, "needs_review": store.get(sid)["needs_review"]}


# ---------------------------------------------------------------------------
# Translation and speech
# ---------------------------------------------------------------------------


class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)
    target: Literal["en", "vi", "zh"]
    source: Literal["en", "vi", "zh"] = "en"


@router.post("/translate", summary="Translate between English, Vietnamese and Mandarin")
def translate(body: TranslateRequest, user: dict = Depends(require_viewer)):
    if body.source == body.target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick two different languages.")
    try:
        result, sid = features.translate(body.text, body.target, body.source, actor=user.get("email", "system"))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"suggestion_id": sid, "result": result}


@router.get("/glossary", summary="Engineering term list used by translation")
def get_glossary(_user: dict = Depends(require_viewer)):
    return {"terms": features.glossary()}


# Handlers that call Whisper, PyMuPDF or the model are plain `def` (or hand the
# work to run_in_threadpool) so a long call never blocks the event loop.


@router.post("/transcribe", summary="Turn a recording into text (local Whisper)")
def transcribe(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    _user: dict = Depends(require_viewer),
):
    data = _read(file)
    try:
        return speech.transcribe(data, suffix=speech.safe_suffix(file.filename, ".wav"), language=language)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


# ---------------------------------------------------------------------------
# Phone calls
# ---------------------------------------------------------------------------


async def _caller(request: Request) -> Dict[str, Any]:
    """Staff (bearer token) or the phone system (?token= matching PHONE_WEBHOOK_TOKEN)."""
    token = request.query_params.get("token", "")
    expected = settings.PHONE_WEBHOOK_TOKEN
    if expected and token and hmac.compare_digest(token.encode(), expected.encode()):
        return {"email": "phone-system", "role": "SYSTEM"}
    from fastapi.security import HTTPAuthorizationCredentials

    auth = request.headers.get("Authorization", "")
    creds = None
    if auth.lower().startswith("bearer "):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=auth[7:])
    user = await get_current_user(creds)
    if user.get("role") not in ("ADMIN", "MANAGER", "OPERATOR"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operators only.")
    return user


class CallRequest(BaseModel):
    transcript: str = Field(..., min_length=1)
    from_number: str = ""
    call_id: Optional[str] = None


def _handle_call(transcript: str, from_number: str, call_id: Optional[str], actor: str) -> Dict[str, Any]:
    # The transcript comes from the caller, so the summary is never posted to a
    # job from here. A summary naming a job waits in the review queue, and
    # approving it posts the note (features._post_call_note).
    summary, sid = features.summarize_call(transcript, from_number, reference=call_id, actor=actor)
    orders = features.find_orders(summary.po_numbers)
    return {
        "suggestion_id": sid,
        "result": summary,
        "matched_orders": orders,
        "monday_updates": [],
        "needs_review": store.get(sid)["needs_review"],
    }


@router.post("/calls", summary="Summarise a received call from its transcript")
async def receive_call(body: CallRequest, request: Request):
    actor = await _caller(request)
    try:
        return await run_in_threadpool(
            _handle_call, body.transcript, body.from_number, body.call_id, actor.get("email", "system")
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@router.post("/calls/audio", summary="Transcribe a call recording, then summarise it")
async def receive_call_audio(request: Request):
    """Multipart form: file (the recording), from_number, call_id.

    The form is parsed only after the caller is checked, so an unauthenticated
    request is refused before its upload is read.
    """
    actor = await _caller(request)
    form = await request.form()
    file = form.get("file")
    if not hasattr(file, "file"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Attach the recording as 'file'.")
    from_number = str(form.get("from_number") or "")
    call_id = form.get("call_id") or None
    data = await run_in_threadpool(_read, file)

    def work() -> Dict[str, Any]:
        text = speech.transcribe(data, suffix=speech.safe_suffix(file.filename, ".wav"))
        result = _handle_call(text["text"], from_number, call_id, actor.get("email", "system"))
        result["transcript"] = text
        return result

    try:
        return await run_in_threadpool(work)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@router.get("/order-status", summary="Read-only order lookup for callers")
async def order_status(number: str = Query(..., min_length=2), request: Request = None):
    await _caller(request)
    orders = features.find_orders([number])
    # Status only: never prices, never other customers' details.
    return {
        "found": bool(orders),
        "orders": [
            {k: o[k] for k in ("sales_order_number", "customer_po_number", "status", "po_date")} for o in orders
        ],
    }


# ---------------------------------------------------------------------------
# Exceptions, part names, drawings
# ---------------------------------------------------------------------------


@router.post("/exceptions/{exception_id}/explain", summary="Plain-English explanation of a stuck job")
def explain(exception_id: str, user: dict = Depends(require_viewer)):
    from app.services.exception_queue_service import exception_queue_service

    item = next((e for e in exception_queue_service.list_exceptions() if e.exception_id == exception_id), None)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such exception.")
    try:
        result, sid = features.explain_exception(item.model_dump(mode="json"), actor=user.get("email", "system"))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"suggestion_id": sid, "result": result}


class ItemRequest(BaseModel):
    raw_text: str = Field(..., min_length=2, max_length=500)


@router.post("/items/suggest", summary="Suggest the catalogue item a customer meant")
def suggest_item(body: ItemRequest, user: dict = Depends(require_operator)):
    try:
        result, sid, candidates = features.suggest_item(body.raw_text, actor=user.get("email", "system"))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return {"suggestion_id": sid, "result": result, "candidates": candidates}


@router.post("/drawings/compare", summary="List differences between a supplier drawing and the master")
def compare_drawings(
    master: UploadFile = File(..., description="Allied's master drawing (PDF or image)"),
    supplier: UploadFile = File(..., description="The supplier's drawing (PDF or image)"),
    user: dict = Depends(require_operator),
):
    m, s = _read(master), _read(supplier)
    try:
        result, sid = check_drawings(
            (m, master.filename or "master.pdf"),
            (s, supplier.filename or "supplier.pdf"),
            actor=user.get("email", "system"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Drawing check failed: {exc}")
        _fail(exc)
    return {"suggestion_id": sid, "result": result}

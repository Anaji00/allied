"""Vendor payment schedules (terms engine) and engineer gates."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.database import SessionLocal
from app.core.security import require_manager, require_operator
from app.models.db_models import JobDB, PurchaseOrderDB
from app.services import gates, payments, vendor_payments

router = APIRouter()
gates_router = APIRouter()


def _fail(exc: Exception):
    status = getattr(exc, "status", 400)
    raise HTTPException(status_code=status, detail=getattr(exc, "message", str(exc)))


class ScheduleBody(BaseModel):
    terms: str = Field(..., description="30/50/20, 30/70, 50/50 or custom percentages adding up to exactly 100")
    total: Optional[str] = Field(None, description="Defaults to the PO's own total")
    currency: str = ""
    triggers: Optional[List[str]] = Field(None, description="One per payment: PO_ISSUED, PROFORMA, GATE2_APPROVED, DELIVERY_CONFIRMED, CUSTOM")
    labels: Optional[List[str]] = None
    confirm_reason: str = Field("", max_length=300, description="Needed when the total is more than the PO's own total: why it is right")


class FireBody(BaseModel):
    trigger: str = Field(..., description="PROFORMA, DELIVERY_CONFIRMED, CUSTOM, PO_ISSUED or GATE2_APPROVED (the last two normally fire by themselves)")


@router.get("/po/{po_id}", summary="The payment schedule of a vendor PO: tranches, triggers, what is due and paid (manager: it holds supplier amounts)")
def get_schedule(po_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    out = payments.get_for_po(po_id)
    if out is None:
        raise HTTPException(status_code=404, detail="This PO has no payment schedule yet.")
    return out


@router.put("/po/{po_id}/schedule", summary="Set a PO's payment terms as tranches (manager)")
def put_schedule(po_id: int, body: ScheduleBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return payments.create_schedule(po_id, body.terms, user, body.total, body.currency, body.triggers, body.labels, body.confirm_reason)
    except payments.PaymentError as exc:
        _fail(exc)


@router.post("/po/{po_id}/fire", summary="Say a payment trigger happened, e.g. delivery confirmed (manager); the matching tranches become due once")
def fire(po_id: int, body: FireBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    if body.trigger.upper() not in payments.TRIGGERS:
        raise HTTPException(status_code=422, detail=f"The trigger is one of: {', '.join(payments.TRIGGERS)}.")
    if body.trigger.upper() in ("PO_ISSUED", "GATE2_APPROVED"):
        raise HTTPException(status_code=422, detail="That one opens by itself: when the PO is really sent, or when Gate 2 is approved or overridden with a reason. It cannot be set by hand.")
    return {"became_due": payments.fire(po_id, body.trigger, user)}


class RecordBody(BaseModel):
    paid_on: str = Field(..., description="YYYY-MM-DD")
    amount: Optional[str] = Field(None, description="Must equal what the payment is due for; blank means that amount")
    method: str = "WIRE"
    reference: str = ""
    wire_file_id: Optional[int] = None
    proforma_file_id: Optional[int] = None
    idem_key: str = Field("", max_length=80, description="The same key twice is one payment; blank uses the payment itself")
    draft_remittance: bool = True


@router.post("/tranche/{tranche_id}/record", summary="Record a vendor payment once: updates the job's payment sheet, stages a monday update, rebuilds the xlsx ledger and stages a remittance email (manager)")
def record_payment(tranche_id: int, body: RecordBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return vendor_payments.record(tranche_id, user, body.paid_on, body.amount, body.method, body.reference, body.wire_file_id, body.proforma_file_id, body.idem_key, body.draft_remittance)
    except payments.PaymentError as exc:
        _fail(exc)


class EmailDraftBody(BaseModel):
    note: str = Field("", max_length=500, description="A short line for the vendor")
    wire_file_id: Optional[int] = None
    proforma_file_id: Optional[int] = None


@router.post("/tranche/{tranche_id}/email-draft", summary="A draft note to the vendor about this payment step (PO, amount, optional wire copy and proforma that are files of the job); waits for a person to send, nothing is sent (manager)")
def tranche_email_draft(tranche_id: int, body: EmailDraftBody = Body(default=EmailDraftBody()), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return vendor_payments.draft_tranche_email(tranche_id, user, body.note, body.wire_file_id, body.proforma_file_id)
    except payments.PaymentError as exc:
        _fail(exc)


@router.get("/po/{po_id}/payments", summary="The payments recorded against a PO (manager)")
def po_payments(po_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return {"payments": vendor_payments.list_for_po(po_id)}


@router.get("/ledger.xlsx", summary="The master vendor-payments ledger as an Excel file, one sheet per vendor (manager)")
def ledger_file(_user: dict = Depends(require_manager)):
    from fastapi.responses import Response

    vendor_payments.write_ledger()
    data = vendor_payments.ledger_path().read_bytes()
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="vendor-payments-ledger.xlsx"'})


@router.get("/schedules", summary="Every active vendor payment schedule with its terms, what is paid and left, the next step and all steps, plus a by-vendor summary (manager)")
def all_schedules(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return payments.schedules_by_vendor()


@router.get("/due", summary="Every vendor payment that is due and unpaid (manager)")
def due(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return {"due": payments.due_list()}


# ------------------------------------------------------------------ engineer gates

class GateRequest(BaseModel):
    job_id: int
    gate: int = Field(..., ge=1, le=2)
    po_id: Optional[int] = None
    file_ids: List[int] = []


class DecideBody(BaseModel):
    approve: bool
    note: str = ""


class ReasonBody(BaseModel):
    reason: str = Field(..., min_length=8, max_length=500)


class OverrideBody(ReasonBody):
    job_id: int
    gate: int = Field(..., ge=1, le=2)
    po_id: Optional[int] = None


def _drafted_by(gate_id_job: int) -> List[str]:
    with SessionLocal() as db:
        job = db.get(JobDB, gate_id_job)
        names = [job.created_by] if job and job.created_by else []
        names += [r[0] for r in db.query(PurchaseOrderDB.created_by).filter(PurchaseOrderDB.job_id == gate_id_job).all() if r[0]]
    return names


_STATUS_WORD = {"NONE": "Not asked yet", "PENDING": "Waiting for the engineer", "APPROVED": "Approved", "REJECTED": "Sent back", "OVERRIDDEN": "Passed by a manager override", "REVOKED": "Approval taken back"}


def _name_of(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    from app.services.coordination import shipments

    return shipments.owner_name(email)


def _decorate(job_id: int, user: dict, entry: Dict[str, Any]) -> Dict[str, Any]:
    """One gate's state with everything the screen needs to show its buttons: the gate row id (null before anyone asked), plain words, who decided, and what THIS person may do."""
    me = (user.get("email") or "").strip().lower()
    manager = str(user.get("role", "")).upper() in ("MANAGER", "ADMIN")
    status = entry.get("status") or "NONE"
    approvers = gates.approvers()
    with SessionLocal() as db:
        drafted = me in gates.drafters(db, job_id)
    return {**entry, "id": entry.get("id"), "status_word": _STATUS_WORD.get(status, status.title()), "decided_by_name": _name_of(entry.get("decided_by")), "decided_at": entry.get("decided_at"),
            "needs_approver_set": not approvers, "can_request": status in ("NONE", "REJECTED", "REVOKED"), "request_url": "/api/v1/gates/request" if status in ("NONE", "REJECTED", "REVOKED") else None,
            "can_approve": bool(status == "PENDING" and entry.get("id") and me in approvers and not drafted), "can_override": bool(manager and status not in ("APPROVED", "OVERRIDDEN")),
            "can_revoke": bool(manager and entry.get("id") and status in ("APPROVED", "OVERRIDDEN", "REJECTED"))}


@gates_router.get("/job/{job_id}", summary="The two engineer gates of a job, with their ids, plain words, who decided, and what the signed-in person may do with each")
def job_gates(job_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    st = gates.status_for(job_id)
    return {"job_id": job_id, "approvers_set": bool(gates.approvers()), "needs_approver_set": not gates.approvers(), "status": {k: _decorate(job_id, user, v) for k, v in st.items()}, "history": gates.for_job(job_id)}


@gates_router.post("/request", summary="Ask the engineer to approve Gate 1 (vendor shop drawing) or Gate 2 (measurement photos)")
def request_gate(body: GateRequest, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return gates.request(body.job_id, body.gate, user, body.po_id, body.file_ids)
    except gates.GateError as exc:
        _fail(exc)


@gates_router.post("/{gate_id}/decide", summary="An engineer in ENGINEER_APPROVERS approves or rejects; final unless a manager revokes it")
def decide_gate(gate_id: int, body: DecideBody, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        with SessionLocal() as db:
            from app.models.db_models import EngineerGateDB

            g = db.get(EngineerGateDB, gate_id)
            job_id = g.job_id if g else 0
        if any((user.get("email") or "").strip().lower() == str(n).strip().lower() for n in _drafted_by(job_id)):
            raise gates.GateError(403, "Nobody approves a gate on a job they drafted.")
        return gates.decide(gate_id, body.approve, user, body.note)
    except gates.GateError as exc:
        _fail(exc)


@gates_router.post("/override", summary="A manager lets a step go ahead without the gate, with a written reason (audited)")
def override_gate(body: OverrideBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return gates.override(body.job_id, body.gate, body.reason, user, body.po_id)
    except gates.GateError as exc:
        _fail(exc)


@gates_router.post("/{gate_id}/revoke", summary="A manager revokes a decision, with a written reason (audited)")
def revoke_gate(gate_id: int, body: ReasonBody, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return gates.revoke(gate_id, body.reason, user)
    except gates.GateError as exc:
        _fail(exc)


# ------------------------------------------------------------------ redaction firewall

from app.services import redaction  # noqa: E402

redaction_router = APIRouter()


class CheckBody(BaseModel):
    job_id: int
    file_id: int
    extra_terms: List[str] = Field([], description="Anything else that names the customer (a plant address, a part number prefix)")


class AttestBody(BaseModel):
    job_id: int
    file_id: int
    statement: str = Field(..., min_length=8, max_length=500, description="What the engineer checked")


def _red_fail(exc: Exception):
    raise HTTPException(status_code=getattr(exc, "status", 400), detail=getattr(exc, "message", str(exc)))


@redaction_router.get("/job/{job_id}", summary="The redaction records of a job's drawings: checked, verified, attested, void")
def job_redactions(job_id: int, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return {"job_id": job_id, "artifacts": redaction.for_job(job_id), "enforced": redaction.settings.REDACTION_ENFORCED, "ocr_available": redaction.ocr_available()}


@redaction_router.get("/job/{job_id}/terms", summary="What the check looks for on this job: the customer, its spellings, contacts and PO numbers")
def job_terms(job_id: int, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return {"terms": redaction.terms_for_job(job_id)}
    except redaction.RedactionError as exc:
        _red_fail(exc)


@redaction_router.post("/check", summary="Redact a PDF or DXF drawing, check the copy again and store it on the job; other files wait for an engineer's attestation")
def check_drawing(body: CheckBody, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return redaction.check_file(body.job_id, body.file_id, user, body.extra_terms)
    except redaction.RedactionError as exc:
        _red_fail(exc)


@redaction_router.post("/{artifact_id}/verify", summary="A person confirms the redacted copy shows nothing of the customer (engineer or manager)")
def verify_drawing(artifact_id: int, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return redaction.verify(artifact_id, user)
    except redaction.RedactionError as exc:
        _red_fail(exc)


@redaction_router.post("/attest", summary="An engineer in ENGINEER_APPROVERS attests a DWG (or other file) is free of the customer's details; void if the file changes")
def attest_drawing(body: AttestBody, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return redaction.attest(body.job_id, body.file_id, user, body.statement)
    except redaction.RedactionError as exc:
        _red_fail(exc)

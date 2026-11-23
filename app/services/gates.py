"""Engineer gates. Gate 1: the vendor's shop drawing is approved before production counts as started. Gate 2: the vendor's measurement photos are approved before crating, a pre-ship payment or a carrier pickup.

Hard blocks. A manager may override one with a written reason (audited). Only the people listed in ENGINEER_APPROVERS may approve (empty = nobody can until the owner sets it), nobody approves a gate on a job
they drafted, and a decision is final unless a manager revokes it with a reason. Reading (`status_for`, `milestone_text`) never raises: a missing table reads as "no gate recorded"."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import EngineerGateDB, JobDB, PurchaseOrderDB

GATE_NAMES = {1: "Gate 1 (vendor shop drawing)", 2: "Gate 2 (measurement photos)"}
PASSED = ("APPROVED", "OVERRIDDEN")


class GateError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def approvers() -> List[str]:
    """The people who may approve a gate: ENGINEER_APPROVERS (comma separated emails). Empty means nobody; it is never defaulted to anyone."""
    return [e.strip().lower() for e in str(getattr(settings, "ENGINEER_APPROVERS", "") or "").split(",") if e.strip()]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dict(g: EngineerGateDB) -> Dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": g.id, "job_id": g.job_id, "po_id": g.po_id, "gate": g.gate, "name": GATE_NAMES.get(g.gate), "file_ids": list(g.file_ids or []), "status": g.status,
            "requested_by": g.requested_by, "requested_at": iso(g.requested_at), "decided_by": g.decided_by, "decided_at": iso(g.decided_at), "note": g.note,
            "override_by": g.override_by, "override_reason": g.override_reason, "override_at": iso(g.override_at),
            "revoked_by": g.revoked_by, "revoked_at": iso(g.revoked_at), "revoke_reason": g.revoke_reason, "passed": g.status in PASSED}


def _latest(db, job_id: int, gate: int, po_id: Optional[int]) -> Optional[EngineerGateDB]:
    q = db.query(EngineerGateDB).filter(EngineerGateDB.job_id == job_id, EngineerGateDB.gate == gate)
    if po_id is not None:
        q = q.filter((EngineerGateDB.po_id == po_id) | (EngineerGateDB.po_id.is_(None)))
    return q.order_by(EngineerGateDB.id.desc()).first()


def status_for(job_id: Optional[int], po_id: Optional[int] = None) -> Dict[int, Dict[str, Any]]:
    """{1: {status, passed, ...}, 2: {...}} for a job (and PO); a gate nobody has asked for reads NONE. Never raises."""
    out: Dict[int, Dict[str, Any]] = {1: {"gate": 1, "status": "NONE", "passed": False}, 2: {"gate": 2, "status": "NONE", "passed": False}}
    if not job_id:
        return out
    try:
        with SessionLocal() as db:
            for n in (1, 2):
                g = _latest(db, int(job_id), n, po_id)
                if g is not None:
                    out[n] = _as_dict(g)
    except Exception:  # noqa: BLE001 - before the migration the table is not there
        return out
    return out


def milestone_text(job_id: Optional[int], po_id: Optional[int] = None) -> str:
    """Where a vendor order stands, in the words a check-in email uses."""
    if not job_id:
        return "milestone not recorded"
    st = status_for(job_id, po_id)
    if not st[1]["passed"]:
        return "awaiting Gate 1 shop drawing approval"
    if not st[2]["passed"]:
        return "in production, awaiting Gate 2 measurement photos"
    return "both engineer gates passed"


def blocked(job_id: Optional[int], gate: int, po_id: Optional[int] = None) -> Optional[str]:
    """A plain sentence when this step must wait for the gate, or None when it may go ahead. A job with no gate record is blocked (the gate was never passed)."""
    if not job_id:
        return None                                   # nothing tied to a job: nothing to gate
    if status_for(job_id, po_id)[gate]["passed"]:
        return None
    return f"{GATE_NAMES[gate]} has not been approved for this job. An engineer approves it, or a manager overrides it with a written reason."


def _check_ids(db, job_id: int, po_id: Optional[int]) -> JobDB:
    """The job must exist, and a PO named with it must exist and belong to that job."""
    job = db.get(JobDB, job_id) if job_id else None
    if job is None:
        raise GateError(404, "There is no such job.")
    if po_id is not None:
        po = db.get(PurchaseOrderDB, po_id)
        if po is None or po.job_id != job_id:
            raise GateError(422, "That purchase order does not belong to this job.")
    return job


def drafters(db, job_id: int) -> List[str]:
    """The people who drafted the job or any of its POs: none of them may approve its gates."""
    job = db.get(JobDB, job_id)
    names = [job.created_by] if job is not None and job.created_by else []
    names += [r[0] for r in db.query(PurchaseOrderDB.created_by).filter(PurchaseOrderDB.job_id == job_id).all() if r[0]]
    return [str(n).strip().lower() for n in names]


def request(job_id: int, gate: int, user: Dict[str, Any], po_id: Optional[int] = None, file_ids: Optional[List[int]] = None, drafted_by: Optional[str] = None) -> Dict[str, Any]:
    if gate not in (1, 2):
        raise GateError(422, "The gate is 1 or 2.")
    with SessionLocal() as db:
        _check_ids(db, job_id, po_id)
        g = _latest(db, job_id, gate, po_id)
        if g is not None and g.status in ("PENDING",) + PASSED:
            if file_ids:
                g.file_ids = sorted({*list(g.file_ids or []), *[int(i) for i in file_ids]})
                db.commit()
            return _as_dict(g)
        g = EngineerGateDB(job_id=job_id, po_id=po_id, gate=gate, file_ids=[int(i) for i in (file_ids or [])], status="PENDING", requested_by=user.get("email"), requested_at=_now())
        db.add(g)
        db.commit()
        db.refresh(g)
        return _as_dict(g)


def decide(gate_id: int, approve: bool, user: Dict[str, Any], note: str = "", job_drafted_by: Optional[str] = None) -> Dict[str, Any]:
    email = (user.get("email") or "").strip().lower()
    if email not in approvers():
        raise GateError(403, "Only an engineer named in the approver list can approve or reject a gate.")
    with SessionLocal() as db:
        g = db.get(EngineerGateDB, gate_id)
        if g is None:
            raise GateError(404, "No such gate.")
        if g.status != "PENDING":
            raise GateError(409, f"This gate is already {g.status.lower()}; a decision is final unless a manager revokes it.")
        if email in drafters(db, g.job_id) or (job_drafted_by and email == str(job_drafted_by).strip().lower()):
            raise GateError(403, "Nobody approves a gate on a job they drafted.")
        now = _now()
        won = db.query(EngineerGateDB).filter(EngineerGateDB.id == gate_id, EngineerGateDB.status == "PENDING").update(
            {EngineerGateDB.status: "APPROVED" if approve else "REJECTED", EngineerGateDB.decided_by: email, EngineerGateDB.decided_at: now, EngineerGateDB.note: (note or "")[:500]}, synchronize_session=False)
        db.commit()
        if not won:                                                            # two decisions at once: the first one stands
            raise GateError(409, "This gate has just been decided by someone else; a decision is final unless a manager revokes it.")
        db.refresh(g)
        out = _as_dict(g)
    if approve and out["gate"] == 2:
        _gate2_passed(out["job_id"], user)
    audit_log(action="GATE_APPROVED" if approve else "GATE_REJECTED", resource_type="ENGINEER_GATE", resource_id=str(gate_id), actor_email=email, actor_role=user.get("role", ""),
              detail=f"{GATE_NAMES.get(out['gate'])} on job {out['job_id']}: {'approved' if approve else 'rejected'}. {note or ''}"[:500])
    return out


def _gate2_passed(job_id: int, user: Dict[str, Any]) -> None:
    """Measurement photos approved: the payments that wait for Gate 2 become due."""
    from app.services import payments

    payments.fire_for_job(job_id, "GATE2_APPROVED", user)


def override(job_id: int, gate: int, reason: str, user: Dict[str, Any], po_id: Optional[int] = None) -> Dict[str, Any]:
    if str(user.get("role", "")).upper() not in ("MANAGER", "ADMIN"):
        raise GateError(403, "Only a manager can override a gate.")
    if len((reason or "").strip()) < 8:
        raise GateError(422, "An override needs a written reason (at least a short sentence).")
    with SessionLocal() as db:
        _check_ids(db, job_id, po_id)
        g = _latest(db, job_id, gate, po_id)
        if g is None or g.status in PASSED:
            g = EngineerGateDB(job_id=job_id, po_id=po_id, gate=gate, file_ids=[], status="PENDING", requested_by=user.get("email"), requested_at=_now()) if g is None else g
            if g.status in PASSED:
                raise GateError(409, "This gate has already passed.")
            db.add(g)
        g.status, g.override_by, g.override_reason, g.override_at = "OVERRIDDEN", user.get("email"), reason.strip()[:500], _now()
        db.commit()
        db.refresh(g)
        out = _as_dict(g)
    if gate == 2:
        _gate2_passed(job_id, user)
    audit_log(action="GATE_OVERRIDDEN", resource_type="ENGINEER_GATE", resource_id=str(out["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"{GATE_NAMES.get(gate)} on job {job_id} overridden: {reason.strip()}"[:500])
    return out


def revoke(gate_id: int, reason: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if str(user.get("role", "")).upper() not in ("MANAGER", "ADMIN"):
        raise GateError(403, "Only a manager can revoke a decision.")
    if len((reason or "").strip()) < 8:
        raise GateError(422, "Revoking a decision needs a written reason.")
    with SessionLocal() as db:
        g = db.get(EngineerGateDB, gate_id)
        if g is None:
            raise GateError(404, "No such gate.")
        if g.status not in PASSED + ("REJECTED",):
            raise GateError(409, "Only a decided gate can be revoked.")
        g.status, g.revoked_by, g.revoked_at, g.revoke_reason = "REVOKED", user.get("email"), _now(), reason.strip()[:500]
        db.commit()
        out = _as_dict(g)
    if out["gate"] == 2:
        from app.services import payments

        payments.unfire_for_job(out["job_id"], "GATE2_APPROVED", user)           # what Gate 2's approval opened (the pre-ship payment) goes back to waiting unless it was already paid
    audit_log(action="GATE_REVOKED", resource_type="ENGINEER_GATE", resource_id=str(gate_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"Revoked: {reason.strip()}"[:500])
    return out


def for_job(job_id: int) -> List[Dict[str, Any]]:
    try:
        with SessionLocal() as db:
            return [_as_dict(g) for g in db.query(EngineerGateDB).filter(EngineerGateDB.job_id == job_id).order_by(EngineerGateDB.id).all()]
    except Exception:  # noqa: BLE001
        return []

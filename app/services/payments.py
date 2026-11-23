"""Vendor payment terms engine: a PO's terms become tranches (30/50/20, 30/70, 50/50 or custom, adding up to exactly 100.00 percent), each with a trigger (PO issued or proforma, Gate 2 approved, delivery
confirmed). Money is Decimal throughout; the last tranche takes the rounding remainder so the tranches always add up to the PO total to the cent. Nothing here pays anyone: a tranche becomes DUE when its
trigger fires, and a person records the payment (see vendor_payments)."""
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.money import parse_amount
from app.core.database import SessionLocal
from app.models.db_models import JobDB, PaymentScheduleDB, PaymentTrancheDB, PurchaseOrderDB

TRIGGERS = ("PO_ISSUED", "PROFORMA", "GATE2_APPROVED", "DELIVERY_CONFIRMED", "CUSTOM")
CENT = Decimal("0.01")
MAX_TRANCHES = 6


class PaymentError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _dec(value: Any, what: str = "amount") -> Decimal:
    try:
        return parse_amount(value, what)
    except ValueError as exc:
        raise PaymentError(422, str(exc))


def _pct(value: Any) -> Decimal:
    """A percentage as typed ("30", "33.33", "1e2"): a finite number between 0 and 100, never a huge exponent."""
    try:
        d = Decimal(str(value).strip().rstrip("%"))
    except (InvalidOperation, ValueError):
        raise PaymentError(422, "A percentage is not a number.")
    if not d.is_finite() or d > 100 or d < 0:
        raise PaymentError(422, "Each percentage must be between 0 and 100.")
    return d


def parse_terms(text: Any) -> List[Decimal]:
    """Percentages from "30/50/20", "30-70", "50 / 50" or a list; each above zero, at most two decimals, adding up to exactly 100.00."""
    if isinstance(text, (list, tuple)):
        parts = [str(p) for p in text]
    else:
        parts = [p for p in re.split(r"\s*[/\-,+]\s*|\s+", str(text or "").replace("%", " ").strip()) if p]
    if not parts:
        raise PaymentError(422, "Give the payment terms, for example 30/50/20.")
    if len(parts) > MAX_TRANCHES:
        raise PaymentError(422, f"At most {MAX_TRANCHES} payments.")
    pcts = [_pct(p) for p in parts]
    for p in pcts:
        if p <= 0 or p != p.quantize(CENT):
            raise PaymentError(422, "Each percentage must be above zero with at most two decimals.")
    if sum(pcts) != Decimal("100.00"):
        raise PaymentError(422, f"The percentages add up to {sum(pcts).normalize():f}, not 100.")
    return [p.quantize(CENT) for p in pcts]


def default_triggers(n: int) -> List[str]:
    """First payment when the PO is issued; the last when delivery is confirmed (three or more) or before shipping, at Gate 2 (two); the ones between at Gate 2."""
    if n == 1:
        return ["PO_ISSUED"]
    if n == 2:
        return ["PO_ISSUED", "GATE2_APPROVED"]
    return ["PO_ISSUED"] + ["GATE2_APPROVED"] * (n - 2) + ["DELIVERY_CONFIRMED"]


def split_amount(total: Decimal, pcts: List[Decimal]) -> List[Decimal]:
    """Each tranche's amount to the cent; the last takes what is left, so the sum equals the total exactly."""
    cents = int((total * 100).to_integral_value(rounding=ROUND_HALF_UP))
    raw = [Decimal(cents) * p / Decimal(100) for p in pcts]
    floors = [int(r // 1) for r in raw]
    left = cents - sum(floors)                                              # whole cents still to hand out: to the biggest fractional parts first (largest remainder), so no tranche is ever negative
    for i in sorted(range(len(raw)), key=lambda i: (raw[i] - floors[i], -i), reverse=True)[:max(0, left)]:
        floors[i] += 1
    return [(Decimal(c) / Decimal(100)).quantize(CENT) for c in floors]


def po_total(po: PurchaseOrderDB) -> Decimal:
    total = Decimal("0")
    for ln in po.lines or []:
        try:
            amount = ln.get("amount")
            if amount in (None, ""):
                amount = Decimal(str(ln.get("quantity", ln.get("qty", 0)) or 0)) * Decimal(str(ln.get("unit_cost", 0) or 0))
            total += Decimal(str(amount).replace(",", "").replace("$", ""))
        except (InvalidOperation, ValueError, AttributeError):
            continue
    return total.quantize(CENT)


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v else None


def _tranche(t: PaymentTrancheDB) -> Dict[str, Any]:
    return {"id": t.id, "schedule_id": t.schedule_id, "seq": t.seq, "label": t.label, "percent": f"{t.percent:.2f}", "amount": f"{t.amount:.2f}", "trigger": t.trigger, "due_on": t.due_on,
            "status": t.status, "triggered_at": _iso(t.triggered_at), "paid_at": _iso(t.paid_at)}


def _schedule(db, s: PaymentScheduleDB) -> Dict[str, Any]:
    tr = db.query(PaymentTrancheDB).filter(PaymentTrancheDB.schedule_id == s.id).order_by(PaymentTrancheDB.seq).all()
    paid = sum((t.amount for t in tr if t.status == "PAID"), Decimal("0"))
    return {"id": s.id, "po_id": s.po_id, "job_id": s.job_id, "vendor": s.vendor, "currency": s.currency, "total": f"{s.total:.2f}", "terms_code": s.terms_code, "status": s.status,
            "created_by": s.created_by, "created_at": _iso(s.created_at), "paid": f"{paid:.2f}", "remaining": f"{s.total - paid:.2f}", "tranches": [_tranche(t) for t in tr]}


def get_for_po(po_id: int) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        s = db.query(PaymentScheduleDB).filter(PaymentScheduleDB.po_id == po_id, PaymentScheduleDB.status == "ACTIVE").order_by(PaymentScheduleDB.id.desc()).first()
        return _schedule(db, s) if s else None


def create_schedule(po_id: int, terms: Any, user: Dict[str, Any], total: Any = None, currency: str = "", triggers: Optional[List[str]] = None, labels: Optional[List[str]] = None, confirm_reason: str = "") -> Dict[str, Any]:
    """The tranches for a PO. total defaults to the PO's own lines; a PO with payments already recorded keeps its schedule (cancel it first)."""
    pcts = parse_terms(terms)
    trig = [str(t).upper() for t in (triggers or default_triggers(len(pcts)))]
    if len(trig) != len(pcts) or any(t not in TRIGGERS for t in trig):
        raise PaymentError(422, f"Give one trigger per payment, each one of: {', '.join(TRIGGERS)}.")
    with SessionLocal() as db:
        po = db.get(PurchaseOrderDB, po_id)
        if po is None:
            raise PaymentError(404, "No such purchase order.")
        if po.status == "CANCELLED":
            raise PaymentError(409, "This purchase order was cancelled, so it has no payments.")
        amount = _dec(total, "total").quantize(CENT, rounding=ROUND_HALF_UP) if total not in (None, "") else po_total(po)
        if amount <= 0:
            raise PaymentError(422, "The PO has no total yet: give the total or add priced lines first.")
        po_tot = po_total(po)
        if po_tot > 0 and amount > po_tot and len((confirm_reason or "").strip()) < 8:
            raise PaymentError(422, f"The total {amount:.2f} is more than the PO's own total {po_tot:.2f}. A manager can confirm it with a written reason.")
        old = db.query(PaymentScheduleDB).filter(PaymentScheduleDB.po_id == po_id, PaymentScheduleDB.status == "ACTIVE").first()
        if old is not None:
            if db.query(PaymentTrancheDB).filter(PaymentTrancheDB.schedule_id == old.id, PaymentTrancheDB.status == "PAID").count():
                raise PaymentError(409, "A payment has already been recorded against this PO's schedule; it cannot be replaced.")
            old.status = "REPLACED"
            for t in db.query(PaymentTrancheDB).filter(PaymentTrancheDB.schedule_id == old.id, PaymentTrancheDB.status != "PAID").all():
                t.status = "VOID"                                                   # a replaced schedule's payments can no longer be paid
        s = PaymentScheduleDB(po_id=po_id, job_id=po.job_id, vendor=po.vendor, currency=(currency or "").upper()[:3] or None, total=amount,
                              terms_code="/".join(format(p.normalize(), "f") for p in pcts), status="ACTIVE", created_by=user.get("email"))
        db.add(s)
        db.flush()
        for i, (p, a, t) in enumerate(zip(pcts, split_amount(amount, pcts), trig), start=1):
            db.add(PaymentTrancheDB(schedule_id=s.id, seq=i, label=(labels[i - 1] if labels and i <= len(labels) and labels[i - 1] else f"Payment {i} of {len(pcts)}"), percent=p, amount=a,
                                    trigger=t, status="PENDING"))
        db.commit()
        out = _schedule(db, s)
        out["po_total"] = f"{po_tot:.2f}"
        out["total_differs"] = bool(po_tot > 0 and amount != po_tot)
    audit_log(action="PAYMENT_SCHEDULE_SET", resource_type="PURCHASE_ORDER", resource_id=str(po_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"Vendor payment schedule {out['terms_code']} on a total of {out['total']} (PO total {out['po_total']})" + (f"; confirmed: {confirm_reason.strip()[:200]}" if out["total_differs"] and confirm_reason else ""))
    return out


def fire(po_id: int, trigger: str, user: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The trigger happened for this PO: its pending tranches with that trigger become DUE (once; a repeat changes nothing). PO_ISSUED also opens PROFORMA ones. Returns the tranches that just became due.
    Never raises into the caller's own work."""
    trig = str(trigger or "").upper()
    names = {"PO_ISSUED": ("PO_ISSUED", "PROFORMA")}.get(trig, (trig,))
    try:
        now = datetime.now(timezone.utc)
        opened: List[Dict[str, Any]] = []
        with SessionLocal() as db:
            s = db.query(PaymentScheduleDB).filter(PaymentScheduleDB.po_id == po_id, PaymentScheduleDB.status == "ACTIVE").first()
            if s is None:
                return []
            po = db.get(PurchaseOrderDB, po_id)
            if po is None or po.status == "CANCELLED":
                return []
            if trig == "GATE2_APPROVED" and settings.GATES_ENFORCED and s.job_id:      # the pre-ship payment opens only when Gate 2 has really passed (approved or overridden with a reason), never by hand
                from app.services import gates

                if gates.blocked(s.job_id, 2, po_id):
                    return []
            for t in db.query(PaymentTrancheDB).filter(PaymentTrancheDB.schedule_id == s.id, PaymentTrancheDB.status == "PENDING", PaymentTrancheDB.trigger.in_(names)).order_by(PaymentTrancheDB.seq):
                t.status, t.triggered_at = "DUE", now
                opened.append(_tranche(t))
            db.commit()
        for t in opened:
            audit_log(action="PAYMENT_DUE", resource_type="PURCHASE_ORDER", resource_id=str(po_id), actor_email=(user or {}).get("email", "system"), actor_role=(user or {}).get("role", "SYSTEM"),
                      detail=f"{t['label']} ({t['percent']}%, {t['amount']}) is due: {trig.lower().replace('_', ' ')}")
        return opened
    except Exception:  # noqa: BLE001 - a missing table or a failed audit never stops a PO from being sent
        return []


def fire_for_job(job_id: int, trigger: str, user: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The trigger for every active schedule of a job (Gate 2 is approved per job)."""
    try:
        with SessionLocal() as db:
            pos = [r[0] for r in db.query(PaymentScheduleDB.po_id).filter(PaymentScheduleDB.job_id == job_id, PaymentScheduleDB.status == "ACTIVE").all()]
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for po in pos:
        out += fire(po, trigger, user)
    return out


def unfire_for_job(job_id: int, trigger: str, user: Optional[Dict[str, Any]] = None) -> int:
    """The trigger was taken back (Gate 2 revoked): its tranches that were opened and not paid go back to PENDING. Returns how many."""
    n = 0
    try:
        with SessionLocal() as db:
            for s in db.query(PaymentScheduleDB).filter(PaymentScheduleDB.job_id == job_id, PaymentScheduleDB.status == "ACTIVE").all():
                for t in db.query(PaymentTrancheDB).filter(PaymentTrancheDB.schedule_id == s.id, PaymentTrancheDB.trigger == trigger, PaymentTrancheDB.status == "DUE").all():
                    t.status, t.triggered_at = "PENDING", None
                    n += 1
            db.commit()
        if n:
            audit_log(action="PAYMENT_REOPENED_AS_PENDING", resource_type="JOB", resource_id=str(job_id), actor_email=(user or {}).get("email", "system"), actor_role=(user or {}).get("role", "SYSTEM"),
                      detail=f"{n} payment(s) went back to waiting: {trigger.lower().replace('_', ' ')} was taken back")
    except Exception:  # noqa: BLE001
        return n
    return n


def due_list() -> List[Dict[str, Any]]:
    """Every tranche that is due and unpaid, oldest first, with its PO and vendor."""
    with SessionLocal() as db:
        rows = (db.query(PaymentTrancheDB, PaymentScheduleDB).join(PaymentScheduleDB, PaymentScheduleDB.id == PaymentTrancheDB.schedule_id)
                .join(PurchaseOrderDB, PurchaseOrderDB.id == PaymentScheduleDB.po_id)
                .filter(PaymentTrancheDB.status == "DUE", PaymentScheduleDB.status == "ACTIVE", PurchaseOrderDB.status != "CANCELLED").order_by(PaymentTrancheDB.triggered_at).all())
        jobs = {j.id: j.ref for j in db.query(JobDB).filter(JobDB.id.in_([s.job_id for _t, s in rows if s.job_id]))} if rows else {}
        pos = {p.id: p.po_number for p in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.id.in_([s.po_id for _t, s in rows]))} if rows else {}
        return [{**_tranche(t), "po_id": s.po_id, "po_number": pos.get(s.po_id), "job_id": s.job_id, "job_ref": jobs.get(s.job_id), "vendor": s.vendor, "currency": s.currency} for t, s in rows]


def schedules_by_vendor() -> Dict[str, Any]:
    """Every active payment schedule (cancelled POs left out), each with what is paid and left and the next step still to pay, and the same grouped by vendor. Read only; money, so managers only."""
    with SessionLocal() as db:
        rows = (db.query(PaymentScheduleDB).join(PurchaseOrderDB, PurchaseOrderDB.id == PaymentScheduleDB.po_id)
                .filter(PaymentScheduleDB.status == "ACTIVE", PurchaseOrderDB.status != "CANCELLED").order_by(PaymentScheduleDB.vendor, PaymentScheduleDB.id).all())
        jobs = {j.id: j.ref for j in db.query(JobDB).filter(JobDB.id.in_([s.job_id for s in rows if s.job_id]))} if rows else {}
        pos = {p.id: p.po_number for p in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.id.in_([s.po_id for s in rows]))} if rows else {}
        out = []
        for s in rows:
            d = _schedule(db, s)
            steps = d.pop("tranches")
            nxt = next((t for t in steps if t["status"] != "PAID"), None)
            out.append({**d, "po_number": pos.get(s.po_id), "job_ref": jobs.get(s.job_id), "next_step": nxt, "steps": steps})
    vendors: Dict[str, Dict[str, Any]] = {}
    for d in out:
        v = vendors.setdefault(d["vendor"] or "(no vendor)", {"vendor": d["vendor"] or "(no vendor)", "po_count": 0, "next_due_on": None, "po_ids": []})
        v["po_count"] += 1
        v["po_ids"].append(d["po_id"])
        due = (d["next_step"] or {}).get("due_on")
        if due and (v["next_due_on"] is None or due < v["next_due_on"]):
            v["next_due_on"] = due
    return {"schedules": out, "vendors": list(vendors.values())}

"""Customer RFQs: every request logged, owned, and quoted before its deadline.

The clock starts when the request arrives and runs in business hours:
acknowledge within RFQ_ACK_BUSINESS_HOURS, quote within 1, 2 or 5 business
days by complexity. Waiting on a vendor's price still counts (that delay is
Allied's, not the customer's); waiting on the customer for missing details
pauses the clock and is reported separately.

RFQs arrive from email (anything the email sorter calls a quote request, or
with RFQ/quote in the subject when the AI is off) or are entered here.
"""

import re
import statistics
import threading
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import RfqDB
from app.services.coordination import notify as alerts
from app.services.coordination import workdays

STATUSES = ["NEW", "ACKNOWLEDGED", "CLARIFYING", "WAITING_VENDOR", "PRICING", "QUOTE_SENT", "WON", "LOST", "NO_BID"]
OPEN = {"NEW", "ACKNOWLEDGED", "CLARIFYING", "WAITING_VENDOR", "PRICING"}
QUOTED = {"QUOTE_SENT", "WON", "LOST"}
CLOSED = {"WON", "LOST", "NO_BID"}
COMPLEXITY = ("SIMPLE", "STANDARD", "COMPLEX")
ALERT_KEYS = ("rfq-new:", "rfq-ack:", "rfq-due:", "rfq-overdue:", "rfq-vendor:")
_SUBJECT = re.compile(r"\b(rfq|r\.f\.q|request for (a )?quot(e|ation)|quote request|please quote|pricing request)\b", re.I)
_number_lock = threading.Lock()
# One change to an RFQ at a time: each save rewrites its history list, so two at once (a
# person and the board read, or two people) could lose a note or a status move.
_write_lock = threading.RLock()


def _quote_days(complexity: str) -> int:
    return {"SIMPLE": settings.RFQ_QUOTE_DAYS_SIMPLE, "COMPLEX": settings.RFQ_QUOTE_DAYS_COMPLEX}.get(
        complexity, settings.RFQ_QUOTE_DAYS_STANDARD)


def team() -> List[str]:
    listed = [e.strip() for e in (settings.RFQ_TEAM_EMAILS or "").split(",") if e.strip()]
    return listed or alerts.leadership()


def looks_like_rfq(subject: str) -> bool:
    return bool(_SUBJECT.search(subject or ""))


def _iso(at: Optional[datetime]) -> Optional[str]:
    return workdays.as_utc(at).isoformat() if at else None


def _as_dict(r: RfqDB) -> Dict[str, Any]:
    d = {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
    for k in ("received_at", "ack_due_at", "due_at", "acknowledged_at", "waiting_since", "paused_at", "quoted_at", "closed_at"):
        d[k] = _iso(d.get(k))
    d["quote_amount"] = str(d["quote_amount"]) if d.get("quote_amount") is not None else None
    d["paused_hours"] = float(d.get("paused_hours") or 0)
    return d


def _next_number(db, received: datetime) -> str:
    year = workdays.as_utc(received).year
    prefix = f"RFQ-{year}-"
    last = (db.query(RfqDB.number).filter(RfqDB.number.like(f"{prefix}%")).order_by(RfqDB.number.desc()).first())
    n = int(last[0].rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def _log(row: RfqDB, who: str, change: str) -> None:
    row.history = list(row.history or []) + [{"at": workdays.now().isoformat(), "by": who, "change": change}]


# ---------------------------------------------------------------------------
# Where each RFQ stands
# ---------------------------------------------------------------------------


def assess(r: Dict[str, Any], at: Optional[datetime] = None) -> Dict[str, Any]:
    """state: overdue / due_soon / ack_overdue / chase_vendor / paused / open / quoted / closed."""
    at = at or workdays.now()
    received = datetime.fromisoformat(r["received_at"])
    due = datetime.fromisoformat(r["due_at"])
    paused = float(r.get("paused_hours") or 0)
    if r.get("paused_at"):
        paused += workdays.hours_between(datetime.fromisoformat(r["paused_at"]), at)
    due = workdays.add_hours(due, paused) if paused else due
    out: Dict[str, Any] = {"effective_due_at": due.isoformat(), "reasons": []}
    end = datetime.fromisoformat(r["quoted_at"]) if r.get("quoted_at") else at
    out["age_business_days"] = round(max(0.0, workdays.days_between(received, end) - paused / max(1, settings.BUSINESS_DAY_END - settings.BUSINESS_DAY_START)), 1)
    out["hours_left"] = round(workdays.hours_between(at, due), 1) if r["status"] in OPEN else None
    if r["status"] in CLOSED:
        out["state"] = "closed"
        return out
    if r["status"] == "QUOTE_SENT":
        out["state"] = "quoted"
        return out
    if r["status"] == "CLARIFYING":
        out["state"] = "paused"
        out["reasons"].append("waiting on the customer; the clock is paused")
        return out
    state = "open"
    if out["hours_left"] is not None and out["hours_left"] <= 0:
        state = "overdue"
        out["reasons"].append(f"quote was due {due.astimezone(alerts.now_local().tzinfo):%a %d %b %H:%M}")
    elif out["hours_left"] is not None and out["hours_left"] <= max(4, (settings.BUSINESS_DAY_END - settings.BUSINESS_DAY_START) // 2):
        state = "due_soon"
        out["reasons"].append(f"quote due in {out['hours_left']:.0f} working hours")
    if r["status"] == "NEW" and datetime.fromisoformat(r["ack_due_at"]) <= at:
        if state == "open":
            state = "ack_overdue"
        out["reasons"].append("not acknowledged to the customer yet")
    if r["status"] == "WAITING_VENDOR" and r.get("waiting_since"):
        waited = workdays.days_between(datetime.fromisoformat(r["waiting_since"]), at)
        out["vendor_wait_days"] = round(waited, 1)
        if waited >= settings.RFQ_VENDOR_CHASE_DAYS:
            if state == "open":
                state = "chase_vendor"
            out["reasons"].append(f"waiting {waited:.1f} business days on {r.get('vendor') or 'the vendor'} for a price")
    if not r.get("owner_email"):
        out["reasons"].append("nobody owns it")
    out["state"] = state
    return out


def bucket(age: float) -> str:
    if age < 2:
        return "0-1 days"
    if age < 4:
        return "2-3 days"
    if age <= 5:
        return "4-5 days"
    return "over 5 days"


def list_rfqs(open_only: bool = True, owner: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(RfqDB)
        if open_only:
            q = q.filter(RfqDB.status.in_(OPEN))
        if owner:
            q = q.filter(RfqDB.owner_email == owner)
        rows = [_as_dict(r) for r in q.order_by(RfqDB.due_at).limit(limit)]
    at = workdays.now()
    for r in rows:
        r.update(assess(r, at))
        r["bucket"] = bucket(r["age_business_days"])
    order = {"overdue": 0, "ack_overdue": 1, "due_soon": 2, "chase_vendor": 3, "open": 4, "paused": 5, "quoted": 6, "closed": 7}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["effective_due_at"]))
    return rows


def get(rfq: Any) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = _find(db, rfq)
        out = _as_dict(row)
    out.update(assess(out))
    return out


def _find(db, rfq: Any) -> RfqDB:
    row = None
    if isinstance(rfq, int) or (isinstance(rfq, str) and rfq.isdigit()):
        row = db.get(RfqDB, int(rfq))
    if row is None and isinstance(rfq, str):
        row = db.query(RfqDB).filter(RfqDB.number == rfq.strip().upper()).first()
    if row is None:
        raise LookupError(f"No RFQ {rfq}.")
    return row


def metrics(days: int = 90) -> Dict[str, Any]:
    since = workdays.now() - timedelta(days=days)
    with SessionLocal() as db:
        rows = [_as_dict(r) for r in db.query(RfqDB).filter(RfqDB.received_at >= since)]
    turnaround, on_time, won, lost, won_value, quoted_value = [], 0, 0, 0, Decimal(0), Decimal(0)
    by_speed: Dict[str, List[int]] = {"within 2 days": [0, 0], "slower": [0, 0]}
    for r in rows:
        if r.get("quoted_at"):
            a = assess(r)
            turnaround.append(a["age_business_days"])
            on_time += int(datetime.fromisoformat(r["quoted_at"]) <= datetime.fromisoformat(a["effective_due_at"]))
            if r["status"] in ("WON", "LOST"):
                speed = "within 2 days" if a["age_business_days"] <= 2 else "slower"
                by_speed[speed][1] += 1
                by_speed[speed][0] += int(r["status"] == "WON")
        amount = Decimal(r["quote_amount"]) if r.get("quote_amount") else Decimal(0)
        if r["status"] in ("WON", "LOST"):
            quoted_value += amount
        if r["status"] == "WON":
            won += 1
            won_value += amount
        elif r["status"] == "LOST":
            lost += 1
    open_rows = list_rfqs(open_only=True)
    buckets: Dict[str, int] = {}
    for r in open_rows:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    return {
        "days": days, "received": len(rows), "quoted": len(turnaround),
        "open": len(open_rows), "overdue": sum(1 for r in open_rows if r["state"] == "overdue"),
        "turnaround_median_days": round(statistics.median(turnaround), 1) if turnaround else None,
        "turnaround_p90_days": round(sorted(turnaround)[int(0.9 * (len(turnaround) - 1))], 1) if turnaround else None,
        "on_time_pct": round(on_time / len(turnaround), 3) if turnaround else None,
        "win_rate": round(won / (won + lost), 3) if (won + lost) else None,
        "win_rate_value": float(round(won_value / quoted_value, 3)) if quoted_value else None,
        "win_rate_by_speed": {k: (round(v[0] / v[1], 3) if v[1] else None) for k, v in by_speed.items()},
        "open_by_age": buckets,
    }


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


def create(data: Dict[str, Any], user: Dict[str, Any], source: str = "manual", announce: bool = True) -> Dict[str, Any]:
    who = user.get("email", "system")
    complexity = (data.get("complexity") or "STANDARD").upper()
    if complexity not in COMPLEXITY:
        raise ValueError(f"Complexity must be one of {', '.join(COMPLEXITY)}.")
    customer = (data.get("customer") or "").strip()
    subject = (data.get("subject") or "").strip()
    if not customer and not subject:
        raise ValueError("Give at least the customer or what they want quoted.")
    received = workdays.as_utc(data.get("received_at")) or workdays.now()
    if received > workdays.now() + timedelta(minutes=5):
        raise ValueError("The received time cannot be in the future.")
    with _number_lock, SessionLocal() as db:
        if data.get("email_id"):
            existing = db.query(RfqDB).filter(RfqDB.email_id == data["email_id"]).first()
            if existing:
                return {**_as_dict(existing), "duplicate": True}
        row = RfqDB(
            number=_next_number(db, received), email_id=data.get("email_id") or None,
            monday_item_id=data.get("monday_item_id") or None, vendor=(data.get("vendor") or "").strip() or None,
            customer=customer[:256], contact_email=(data.get("contact_email") or "").strip() or None,
            subject=subject[:512], summary=data.get("summary") or None, items=list(data.get("items") or []),
            complexity=complexity, status="NEW", owner_email=(data.get("owner_email") or "").strip() or None,
            received_at=received, ack_due_at=workdays.add_hours(received, settings.RFQ_ACK_BUSINESS_HOURS),
            due_at=workdays.add_days(received, _quote_days(complexity)), notes=data.get("notes") or None,
            history=[], source=source, created_by=who, paused_hours=0,
        )
        _log(row, who, f"received ({source})")
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(RfqDB).filter(RfqDB.email_id == data.get("email_id")).first()
            if existing:
                return {**_as_dict(existing), "duplicate": True}
            raise
        db.refresh(row)
        out = _as_dict(row)
    local_due = datetime.fromisoformat(out["due_at"]).astimezone(alerts.now_local().tzinfo)
    announce and alerts.notify(
        "rfq_new", f"rfq-new:{out['id']}:",
        f"New RFQ {out['number']}: {out['customer'] or out['subject']}",
        f"{out['subject'] or 'Request for quote'} from {out['customer'] or out['contact_email'] or 'unknown'}.\n"
        f"{out.get('summary') or ''}\nAcknowledge it within {settings.RFQ_ACK_BUSINESS_HOURS} working hours; "
        f"the quote is due {local_due:%a %d %b %H:%M}. Owner: {out['owner_email'] or 'not assigned yet'}.",
        to=[out["owner_email"]] if out["owner_email"] else team(), link=f"/rfqs?id={out['id']}",
    )
    audit_log(action="CREATE", resource_type="RFQ", resource_id=out["number"], actor_email=who,
              actor_role=user.get("role", ""), detail=f"{out['customer']}: {out['subject']} ({source})")
    from app.services.coordination import events

    events.emit("rfq.received", {"rfq": out}, f"{out['number']} from {out['customer'] or out['contact_email'] or 'unknown'} ({source})")
    return out


def update(rfq: Any, changes: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    with _write_lock:
        return _update(rfq, changes, user)


def _update(rfq: Any, changes: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    who = user.get("email", "system")
    now = workdays.now()
    moved = None
    with SessionLocal() as db:
        row = _find(db, rfq)
        notes = []
        for field in ("owner_email", "vendor", "customer", "contact_email", "notes"):
            if changes.get(field) is not None and str(changes[field]).strip() != (getattr(row, field) or ""):
                setattr(row, field, str(changes[field]).strip() or None if field != "customer" else str(changes[field]).strip())
                notes.append(f"{field.replace('_', ' ')}: {getattr(row, field) or 'cleared'}")
        if changes.get("complexity"):
            c = changes["complexity"].upper()
            if c not in COMPLEXITY:
                raise ValueError(f"Complexity must be one of {', '.join(COMPLEXITY)}.")
            if c != row.complexity:
                row.complexity = c
                row.due_at = workdays.add_days(row.received_at, _quote_days(c))
                notes.append(f"complexity {c}; quote due date moved")
        if changes.get("quote_number"):
            row.quote_number = str(changes["quote_number"]).strip()[:64]
        if changes.get("quote_amount") not in (None, ""):
            try:
                amount = Decimal(str(changes["quote_amount"]).replace(",", "").replace("$", ""))
            except InvalidOperation:
                raise ValueError("Quote amount must be a number.")
            if amount < 0:
                raise ValueError("Quote amount cannot be negative.")
            row.quote_amount = amount.quantize(Decimal("0.01"))
        status = (changes.get("status") or "").upper()
        if status and status != row.status:
            if status not in STATUSES:
                raise ValueError(f"Status must be one of {', '.join(STATUSES)}.")
            if row.status in CLOSED:
                raise ValueError(f"{row.number} is already closed ({row.status}).")
            if status in ("LOST", "NO_BID") and not (changes.get("reason") or "").strip():
                raise ValueError("Say why it was lost or not quoted; that is what the win-rate report learns from.")
            if status == "QUOTE_SENT" and not (row.quote_number or row.quote_amount is not None):
                raise ValueError("Give the quote number or amount when marking it sent.")
            if status == "WON" and row.status not in QUOTED:
                row.quoted_at = row.quoted_at or now
            if row.status == "CLARIFYING" and row.paused_at:
                row.paused_hours = Decimal(str(round(float(row.paused_hours or 0) + workdays.hours_between(row.paused_at, now), 2)))
                row.paused_at = None
            if status == "CLARIFYING":
                row.paused_at = now
            if status != "NEW" and not row.acknowledged_at:
                row.acknowledged_at = now
            if status == "WAITING_VENDOR":
                row.waiting_since = now
            if status == "QUOTE_SENT":
                row.quoted_at = now
            if status in CLOSED:
                row.closed_at = now
                row.close_reason = (changes.get("reason") or "").strip()[:256] or None
            notes.append(f"status {row.status} -> {status}" + (f" ({changes['reason']})" if changes.get("reason") else ""))
            moved = (row.status, status)
            row.status = status
        if changes.get("note"):
            notes.append(str(changes["note"])[:500])
        if not notes and not changes.get("quote_number") and changes.get("quote_amount") in (None, ""):
            return {**_as_dict(row), **assess(_as_dict(row))}
        _log(row, who, "; ".join(notes) or "quote details updated")
        db.commit()
        db.refresh(row)
        out = _as_dict(row)
    out.update(assess(out))
    rid = out["id"]
    if out["status"] != "NEW":
        alerts.resolve([f"rfq-ack:{rid}:", f"rfq-new:{rid}:"])
    if out["status"] not in OPEN:
        alerts.resolve([f"{k}{rid}:" for k in ALERT_KEYS])
    if out["status"] != "WAITING_VENDOR":
        alerts.resolve([f"rfq-vendor:{rid}:"])
    audit_log(action="UPDATE", resource_type="RFQ", resource_id=out["number"], actor_email=who,
              actor_role=user.get("role", ""), detail="; ".join(notes) or "quote details")
    if moved:
        from app.services.coordination import events

        events.emit("rfq.status_changed", {"rfq": out, "from": moved[0], "to": moved[1]},
                    f"{out['number']} {moved[0]} -> {moved[1]}")
    return out


def from_email(email_id: str, sender: str, sender_name: str, subject: str, body: str, ai: Any = None,
               received_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Logs an emailed request for a quote (once per email)."""
    items = []
    if ai is not None:
        items = [{"description": line.description, "quantity": line.quantity, "unit": line.unit} for line in ai.lines]
    summary = (ai.summary if ai is not None else "") or (body or "").strip()[:400]
    complexity = "COMPLEX" if re.search(r"\b(drawing|dwg|step|custom|new design|fabricat)", f"{subject} {body}", re.I) else "STANDARD"
    return create({
        "email_id": email_id, "customer": (ai.customer_name if ai is not None else "") or sender_name or sender,
        "contact_email": sender, "subject": subject, "summary": summary, "items": items,
        "complexity": complexity, "received_at": received_at,
    }, {"email": "email-intake", "role": "SYSTEM"}, source="email")


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def _contacts_note(vendor: Optional[str]) -> str:
    """Which bidders get a drafted reminder and which must be chased by hand."""
    from app.services.coordination.autopilot import modes, vendor_email

    if not settings.AUTOPILOT_ENABLED or modes().get("rfq_vendor_chaser") == "off":
        return ""
    names = [n.strip() for n in (vendor or "").split(",") if n.strip()]
    by_hand = [n for n in names if not vendor_email(n)]
    drafted = [n for n in names if n not in by_hand]
    return ((f" A reminder to {', '.join(drafted)} is drafted in the assistant." if drafted else "")
            + (f" No email on file for {', '.join(by_hand)}: chase by phone or add it." if by_hand else ""))


def run_checks() -> Dict[str, int]:
    sent = {"rfq_ack": 0, "rfq_due": 0, "rfq_overdue": 0, "rfq_vendor": 0}
    rows = list_rfqs(open_only=True, limit=1000)
    alerts.resolve_missing(ALERT_KEYS, (str(r["id"]) for r in rows))
    tz = alerts.now_local().tzinfo
    for r in rows:
        rid, owner = r["id"], [r["owner_email"]] if r.get("owner_email") else []
        label = f"{r['number']} {r['customer'] or r['subject']}"
        link = f"/rfqs?id={rid}"
        due = datetime.fromisoformat(r["effective_due_at"]).astimezone(tz)
        if "not acknowledged to the customer yet" in r["reasons"]:
            sent["rfq_ack"] += int(bool(alerts.notify(
                "rfq_ack", f"rfq-ack:{rid}:", f"Acknowledge RFQ {label}",
                f"Received {datetime.fromisoformat(r['received_at']).astimezone(tz):%a %d %b %H:%M} and the customer has not heard back. "
                "Reply to say it is being quoted and when, then mark it acknowledged.",
                severity="warning", to=owner or team(), link=link,
            )))
        if r["state"] == "overdue":
            late_days = -r["hours_left"] / max(1, settings.BUSINESS_DAY_END - settings.BUSINESS_DAY_START)
            sent["rfq_overdue"] += int(bool(alerts.notify(
                "rfq_overdue", f"rfq-overdue:{rid}:{r['effective_due_at'][:10]}",
                f"Quote overdue: {label}",
                f"The quote was due {due:%a %d %b %H:%M}. Status: {r['status'].replace('_', ' ').lower()}. "
                f"Owner: {r['owner_email'] or 'nobody'}.\n" + "\n".join(f"- {x}" for x in r["reasons"]),
                severity="critical" if late_days >= 1 else "warning", to=owner + team(),
                include_leadership=late_days >= 1 or not owner, link=link,
                replaces=[f"rfq-overdue:{rid}:", f"rfq-due:{rid}:"],
            )))
        elif r["state"] == "due_soon":
            sent["rfq_due"] += int(bool(alerts.notify(
                "rfq_due", f"rfq-due:{rid}:{r['effective_due_at'][:10]}",
                f"Quote due {due:%a %H:%M}: {label}",
                f"{r['hours_left']:.0f} working hours left. Status: {r['status'].replace('_', ' ').lower()}.",
                to=owner or team(), link=link, replaces=[f"rfq-due:{rid}:"],
            )))
        wait = r.get("vendor_wait_days") or 0
        if r["status"] == "WAITING_VENDOR" and wait >= settings.RFQ_VENDOR_CHASE_DAYS:
            escalate = wait >= settings.RFQ_VENDOR_ESCALATE_DAYS
            sent["rfq_vendor"] += int(bool(alerts.notify(
                "rfq_vendor", f"rfq-vendor:{rid}:{'escalate' if escalate else 'chase'}",
                f"{'Still no' if escalate else 'Chase'} vendor price: {label}",
                f"Waiting {wait:.1f} business days on {r.get('vendor') or 'the vendor'}. "
                + ("Escalate with the vendor or price it another way." if escalate else "Send the vendor a reminder.")
                + _contacts_note(r.get("vendor")),
                severity="warning" if escalate else "info", to=owner or team(), include_leadership=escalate, link=link,
                replaces=[f"rfq-vendor:{rid}:"],
            )))
    return sent


def for_summary() -> List[str]:
    return [f"{r['number']} {r['customer'] or r['subject']}: {r['reasons'][0] if r['reasons'] else r['state']}"
            for r in list_rfqs(open_only=True) if r["state"] in ("overdue", "ack_overdue")]

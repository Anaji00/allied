"""Purchase orders Allied issues to vendors for a job: one PO per vendor per job. DRAFT -> APPROVED (after a manager approved the design;
the number is assigned here) -> SENT (a person OKs the email draft) -> ACKNOWLEDGED. The number is kept when the customer's order
arrives for the same vendor, so the vendor never gets a second PO. The app never writes to Sage: numbers are above SAGE floors and the
POs reach Sage only through the import files."""

import re
import unicodedata
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core import status_words
from app.core.logging import logger
from app.models.db_models import PurchaseOrderDB
from app.services import order_documents
from app.services.coordination import files as item_files
from app.services.coordination import jobs, rfqs

_lock = threading.RLock()      # one process, one writer: creating, numbering and approving a PO never run twice at once
_INCOTERMS = {"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"}       # the eleven Incoterms 2020
_CUSTOMS_KEYS = ("hts_code", "origin", "uom", "net_kg", "cert_required")
STATUSES = ("DRAFT", "APPROVED", "SENDING", "SENT", "ACKNOWLEDGED", "CANCELLED")


def notify_today():
    """Today in the business time zone (the server's own date can already be tomorrow in the evening)."""
    from app.services.coordination import notify

    return notify.today()


def _iso(at: Optional[datetime]) -> Optional[str]:
    return at.isoformat() if at else None


def _rev_key(v: Any) -> str:
    """'Rev B', 'REV. B', 'Revision B' and 'B.' all mean B: drop the word, punctuation and spaces, ignore case."""
    text = str(v or "").casefold()
    text = re.sub(r"\b(revision|rev)\b\.?", " ", text)
    return re.sub(r"[^0-9a-z]+", "", text)


def _same_rev(a: Any, b: Any) -> bool:
    return _rev_key(a) == _rev_key(b)


def revision_view(r: PurchaseOrderDB) -> List[Dict[str, Any]]:
    """Per line: the drawing revision sent to the vendor (the line's revision once the PO has been sent; the lines are frozen from approval),
    the revision the vendor said it is making, and whether the two differ."""
    acked = (r.revision_marks or {}).get("acked", {}) if isinstance(r.revision_marks, dict) else {}
    went = r.status in ("SENT", "ACKNOWLEDGED")
    out = []
    for i, ln in enumerate(r.lines or [], 1):
        ln = ln if isinstance(ln, dict) else {}
        sent = (ln.get("revision") or None) if went else None
        a = acked.get(str(i)) if isinstance(acked, dict) else None
        a = a if isinstance(a, dict) else None
        out.append({"line": i, "revision_sent": sent, "revision_acked": a.get("revision") if a else None, "acked_by": a.get("by") if a else None,
                    "acked_at": a.get("at") if a else None, "mismatch": bool(went and a and sent and not _same_rev(sent, a.get("revision")))})
    return out


def _job_ref_of(job_id: Any) -> Optional[str]:
    from app.models.db_models import JobDB

    with SessionLocal() as db:
        row = db.get(JobDB, job_id) if job_id else None
        return row.ref if row else None


CURRENCIES = {"USD", "CAD", "CNY", "EUR", "GBP", "JPY", "MXN", "AUD"}


def _currency(value: Any) -> Optional[str]:
    """A currency as typed: three letters from the list the paperwork knows, or blank. Anything else is refused, never guessed."""
    cur = str(value or "").strip().upper()
    if not cur:
        return None
    if cur not in CURRENCIES:
        raise jobs.JobError("The currency must be one of: " + ", ".join(sorted(CURRENCIES)) + " (or left blank).")
    return cur


def effective_currency(r: PurchaseOrderDB) -> Dict[str, Optional[str]]:
    """The PO's own currency when it has been stated; else the one on its vendor payment schedule when there is one; else nothing (never assumed to be dollars)."""
    if r.currency:
        return {"currency": r.currency, "currency_source": "purchase order"}
    try:
        from app.models.db_models import PaymentScheduleDB

        with SessionLocal() as db:
            s = db.query(PaymentScheduleDB).filter(PaymentScheduleDB.po_id == r.id, PaymentScheduleDB.currency.isnot(None)).order_by(PaymentScheduleDB.id.desc()).first()
            if s is not None and s.currency:
                return {"currency": s.currency, "currency_source": "payment schedule"}
    except Exception:  # noqa: BLE001
        pass
    return {"currency": None, "currency_source": None}


def _as_dict(r: PurchaseOrderDB) -> Dict[str, Any]:
    revs = revision_view(r)
    return {**effective_currency(r), "revisions": revs, "revision_mismatch": [x["line"] for x in revs if x["mismatch"]],
            "revision_sent": [x["revision_sent"] for x in revs], "revision_acked": [x["revision_acked"] for x in revs],
            "id": r.id, "job_id": r.job_id, "po_number": r.po_number, "vendor": r.vendor, "vendor_email": r.vendor_email, "lines": r.lines or [],
            "terms": r.terms, "ship_via": r.ship_via, "good_thru": r.good_thru, "status": r.status, "approved_by": r.approved_by,
            "status_word": status_words.word("po", r.status), "approved_at": _iso(r.approved_at), "sent_at": _iso(r.sent_at), "action_id": r.action_id,
            "cancelled_at": _iso(r.cancelled_at), "cancel_reason": r.cancel_reason, "incoterm": r.incoterm, "incoterm_place": r.incoterm_place,
            "customs_changed_after_send": (r.revision_marks or {}).get("customs_after_send") if isinstance(r.revision_marks, dict) and r.status in ("SENT", "ACKNOWLEDGED") else None,
            "total": lines_total(r.lines), "approval_limit": float(getattr(settings, "PO_APPROVAL_LIMIT", 0.0) or 0.0) or None,
            "needs_admin": bool(float(getattr(settings, "PO_APPROVAL_LIMIT", 0.0) or 0.0) > 0 and lines_total(r.lines) > float(getattr(settings, "PO_APPROVAL_LIMIT", 0.0))
                                and r.status == "DRAFT"),"approved_by_creator": _same_person(r.approved_by, r.created_by),
            "pdf_url": f"/api/v1/purchase-orders/{r.id}/pdf", "job_ref": _job_ref_of(r.job_id),
            "missing_revision": [i + 1 for i, ln in enumerate(r.lines or []) if isinstance(ln, dict) and not ln.get("revision")]}      # lines with no drawing revision named: a warning, never a block


def _row(db, po_id: int) -> PurchaseOrderDB:
    row = db.get(PurchaseOrderDB, int(po_id)) if 0 < int(po_id) < 2**31 else None
    if row is None:
        raise LookupError(f"No purchase order {po_id}.")
    return row


def for_job(job_id: int) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        return [_as_dict(r) for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == job_id).order_by(PurchaseOrderDB.id).all()]


def _norm(name: str) -> str:
    return jobs.canon_vendor(name)


def _one_email(v: Any) -> str:
    """One email address, or a plain refusal. A list ("a@x.com, b@y.com") or text with spaces is not an address."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()
    if s and not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]{2,}", s):
        raise jobs.JobError("Give one email address for the vendor (no commas, spaces or names).")
    return s[:256]


def find_for_vendor(job_id: int, vendor: str) -> Optional[Dict[str, Any]]:
    want = _norm(vendor)
    for p in for_job(job_id):
        if _norm(p["vendor"]) == want and p["status"] != "CANCELLED":      # a cancelled PO is history: a new one can follow it
            return p
    return None


def _cancelled_for_vendor(job_id: int, vendor: str) -> Optional[Dict[str, Any]]:
    want = _norm(vendor)
    return next((p for p in for_job(job_id) if _norm(p["vendor"]) == want and p["status"] == "CANCELLED"), None)


def get(po_id: int) -> Dict[str, Any]:
    with SessionLocal() as db:
        return _as_dict(_row(db, po_id))


def create(job: Any, vendor: str, user: Dict[str, Any], lines: Optional[List[Dict[str, Any]]] = None, terms: str = "", ship_via: str = "",
           good_thru: str = "", currency: str = "") -> Dict[str, Any]:
    """The PO for this vendor on this job: made once; asking again returns the one that exists (one number per vendor per job).
    Two requests at once still make one: a lock in this process, and a unique index in the database behind it."""
    from app.services.coordination.autopilot import vendor_email

    vendor = re.sub(r"[\x00-\x1f\x7f‪-‮⁦-⁩]+", " ", vendor or "").strip()[:256]
    if not vendor:
        raise jobs.JobError("Say which vendor the purchase order is for.")
    j = jobs.get(job)
    cur = _currency(currency)
    with _lock:
        have = find_for_vendor(j["id"], vendor)
        if have:
            return have
        gone = _cancelled_for_vendor(j["id"], vendor)
        if gone and user.get("role") not in ("ADMIN", "MANAGER"):
            raise jobs.JobError(f"The purchase order to {gone['vendor']} on this job was cancelled ({gone.get('cancel_reason') or 'no reason given'}). A manager raises a new one.")
        if lines is None and j.get("rfq"):
            r = rfqs.get(j["rfq"]["id"])
            lines = order_documents.po_lines([{"quantity": i.get("quantity"), "description": i["description"], "revision": i.get("revision")}
                                              for i in (r.get("items") or []) if isinstance(i, dict) and i.get("description")])
        else:
            lines = _typed_lines(lines or [])
        try:
            with SessionLocal() as db:
                row = PurchaseOrderDB(job_id=j["id"], vendor=vendor, vendor_email=vendor_email(vendor), lines=lines, terms=terms or None,
                                      ship_via=ship_via or None, good_thru=good_thru or None, currency=cur, status="DRAFT", created_by=user.get("email", "system"))
                db.add(row)
                db.commit()
                db.refresh(row)
                return _as_dict(row)
        except IntegrityError:                         # another process made it a moment ago: that one is the PO
            have = find_for_vendor(j["id"], vendor)
            if have:
                return have
            raise


def update(po_id: int, changes: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Terms, ship via, good-thru and the vendor email can change until it is sent; the lines only while it is still a draft."""
    with _lock, SessionLocal() as db:
        row = _row(db, po_id)
        if row.status == "SENDING":
            raise jobs.JobError("This purchase order is being sent right now; it can't be changed until that finishes.")
        if row.status == "CANCELLED":
            raise jobs.JobError("This purchase order was cancelled; it can't be changed.")
        if row.status in ("SENT", "ACKNOWLEDGED"):
            raise jobs.JobError("This purchase order has been sent; it can no longer be changed.")
        if "lines" in changes and row.status != "DRAFT":
            # After approval the lines are fixed, except the customs details (HTS code, origin, unit, weight, certificate), which are often only known later.
            new_lines = _typed_lines(changes["lines"]) if isinstance(changes["lines"], list) else None
            old_lines = [ln for ln in (row.lines or []) if isinstance(ln, dict)]
            core = ("qty", "item", "description", "unit_cost", "amount", "revision")
            if row.status != "APPROVED" or new_lines is None or len(new_lines) != len(old_lines) or any(
                    any(a.get(k) != b.get(k) for k in core) for a, b in zip(old_lines, new_lines)):
                raise jobs.JobError("The lines can only change while the purchase order is a draft, before it is approved (only the customs details can be added later).")
            row.lines = new_lines
            changes = {k: v for k, v in changes.items() if k != "lines"}
        if "lines" in changes:
            if row.status != "DRAFT":
                raise jobs.JobError("The lines can only change while the purchase order is a draft, before it is approved.")
            if not isinstance(changes["lines"], list) or len(changes["lines"]) > 100:
                raise jobs.JobError("Lines must be a list (at most 100).")
            row.lines = _typed_lines(changes["lines"])
        if "vendor_email" in changes and row.status != "DRAFT" and user.get("role") not in ("ADMIN", "MANAGER"):
            raise jobs.JobError("Only a manager can change where an approved purchase order is sent.")
        for k in ("terms", "ship_via", "good_thru"):
            if k in changes:
                setattr(row, k, re.sub(r"[\x00-\x1f\x7f]+", " ", str(changes[k] or "")).strip()[:256] or None)
        if "currency" in changes:
            row.currency = _currency(changes["currency"])
        if "incoterm" in changes:
            term = re.sub(r"[^A-Za-z0-9 ]+", "", str(changes["incoterm"] or "")).strip().upper()[:16]
            if term and term not in _INCOTERMS:
                raise jobs.JobError("The Incoterm must be one of: " + ", ".join(sorted(_INCOTERMS)) + " (or left blank).")
            row.incoterm = term or None
        if "incoterm_place" in changes:
            row.incoterm_place = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(changes["incoterm_place"] or "")).split())[:64] or None
        if "vendor_email" in changes:
            row.vendor_email = _one_email(changes["vendor_email"]) or None
        db.commit()
        return _as_dict(row)


def _ascii_digits(s: str) -> str:
    """Fullwidth and Arabic-Indic digits written as plain 0-9, so "５０１２３" is 50123."""
    return "".join(str(unicodedata.digit(c)) if c.isdigit() and not c.isascii() and unicodedata.digit(c, None) is not None else c for c in s)


def _used_numbers() -> set:
    """Every number already in use, as digit strings without leading zeros: a PO number typed on the vendor board ("PO-50124", "PO# 50124 (rev 2)",
    "050124"), kept on an order, or already a purchase order. Each value counts as a whole and by every run of digits in it. Built once per allocation."""
    from app.models.db_models import OrderDB, VendorJobDB

    with SessionLocal() as db:
        seen = [r[0] for r in db.query(PurchaseOrderDB.po_number).all()] + [r[0] for r in db.query(VendorJobDB.po_number).all()]
        seen += [r[0] for r in db.query(OrderDB.customer_po_number).all()]
        for (pos,) in db.query(OrderDB.purchase_orders).all():
            seen += list((pos or {}).values()) if isinstance(pos, dict) else []
    used = set()
    for v in seen:
        s = _ascii_digits(str(v or ""))
        for d in [re.sub(r"\D", "", s)] + re.findall(r"\d+", s):
            if d.lstrip("0"):
                used.add(d.lstrip("0"))
    return used


def _taken(number: str, used: Optional[set] = None) -> bool:
    want = re.sub(r"\D", "", _ascii_digits(str(number))).lstrip("0")
    return bool(want) and want in (used if used is not None else _used_numbers())


def _next_number() -> str:
    from app.services.sage_sdk_writer import SageSDKWriter

    used = _used_numbers()
    n = SageSDKWriter._next_number("PO")
    for _ in range(50):
        if not _taken(str(n), used):
            return n
        n = SageSDKWriter._next_number("PO")
    raise jobs.JobError("Every number tried was already in use. Check the vendor board and the PO number floor (PO_NUMBER_FLOOR).")


def _record_line(po_id: int, vendor: str, po_number: str, job: Dict[str, Any], user: Dict[str, Any]) -> None:
    """The chasing line for a numbered PO, pointing back at it. Made after the PO is saved, so a failure here leaves a numbered PO and no
    orphan line; the next board read links any line that carries the number."""
    from app.models.db_models import VendorJobDB
    from app.services.coordination import vendors

    try:
        prefix = f"{job['rfq']['number']}: " if job.get("rfq") else f"{job['ref']}: "
        line = vendors.create({"vendor": vendor, "description": prefix + (job.get("title") or ""), "po_number": po_number,
                               "ordered_on": notify_today().isoformat(), "status": "WAITING ON PI"}, user)
        with SessionLocal() as other:
            other.query(VendorJobDB).filter(VendorJobDB.id == line["id"]).update({VendorJobDB.purchase_order_id: po_id})
            other.commit()
    except Exception as exc:  # noqa: BLE001 - the PO itself is saved and numbered; only its chasing line is missing
        logger.exception(f"Could not record the vendor line for PO {po_number}: {exc}")


def _line_value(ln: Any) -> Optional[float]:
    """What one line is worth, counted the cautious way: the larger of its amount and quantity x unit cost, never below zero (a credit line does not
    offset another line). None when a price is written but cannot be read."""
    if not isinstance(ln, dict):
        return 0.0
    amount = order_documents.money(ln.get("amount"))[0] if ln.get("amount") not in (None, "") else None
    unit = order_documents.money(ln.get("unit_cost"))[0] if ln.get("unit_cost") not in (None, "") else None
    qty = order_documents.num(ln.get("quantity", ln.get("qty")))
    if (ln.get("amount") not in (None, "") and amount is None) or (ln.get("unit_cost") not in (None, "") and unit is None):
        return None
    worth = [v for v in (amount, unit * qty if unit is not None and qty is not None else None) if v is not None]
    if unit is not None and qty is None and amount is None:
        return None                                       # a unit cost with a quantity that is not a number: the line's worth is unknown
    return max(max(worth), 0.0) if worth else 0.0


def lines_total(lines: Any) -> float:
    """What the lines add up to, counted the cautious way (see _line_value). A line whose price cannot be read counts as zero here."""
    return sum(_line_value(ln) or 0.0 for ln in (lines if isinstance(lines, list) else []))


def check_lines_limit(lines: Any, user: Dict[str, Any]) -> None:
    """Above PO_APPROVAL_LIMIT only an administrator approves. 0 (the default) means no limit. With a limit set, a line whose price or quantity cannot
    be read is refused (its worth is unknown), whoever approves."""
    limit = float(getattr(settings, "PO_APPROVAL_LIMIT", 0.0) or 0.0)
    if limit <= 0:
        return
    for n, ln in enumerate(lines if isinstance(lines, list) else [], 1):
        if _line_value(ln) is None:
            raise jobs.JobError(f"Line {n}: the price or quantity cannot be read as a number, so the purchase order cannot be checked against the {limit:,.2f} limit. Fix the line first.")
    total = lines_total(lines)
    if limit > 0 and total <= 0 and user.get("role") != "ADMIN" and isinstance(lines, list) and lines:
        raise jobs.JobError("This purchase order has no prices yet, so it cannot be checked against the approval limit. Add the prices, or an administrator approves it.")
    if total > limit and user.get("role") != "ADMIN":
        raise jobs.JobError(f"This purchase order adds up to {total:,.2f}, over the {limit:,.2f} a manager can approve. An administrator approves it.")


def _check_limit(row: PurchaseOrderDB, user: Dict[str, Any]) -> None:
    check_lines_limit(row.lines, user)


def _same_person(a: Any, b: Any) -> bool:
    """The same mailbox, ignoring case and a +tag (boss+po@x.com is boss@x.com)."""
    def key(v: Any) -> str:
        local, _, domain = str(v or "").strip().lower().partition("@")
        return f"{local.split('+', 1)[0]}@{domain}"
    return bool(a and b) and key(a) == key(b)


def _approve_row(po_id: int, user: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    """Numbers and approves one PO exactly once (callers hold the lock). A second call finds it done and returns it unchanged. If the database refuses
    the number (another PO already holds it), the next number is tried; if it still cannot be numbered the person is told, never left with a draft that
    looks approved."""
    for _attempt in range(6):
        with SessionLocal() as db:
            row = _row(db, po_id)
            if row.status == "CANCELLED":
                raise jobs.JobError("This purchase order was cancelled; it cannot be approved.")
            if row.status != "DRAFT" and row.po_number:
                return _as_dict(row)
            _check_limit(row, user)
            number = row.po_number or _next_number()
            row.po_number, row.status, row.approved_by, row.approved_at = number, "APPROVED", user.get("email"), datetime.now(timezone.utc)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                fresh = _row(db, po_id)
                if fresh.status != "DRAFT" and fresh.po_number:        # someone else approved it first: theirs stands
                    return _as_dict(fresh)
                continue                                               # the number was taken by another PO: ask for the next one
            vendor, out = row.vendor, _as_dict(row)
        audit_log("PO_APPROVED", "purchase_orders", str(number), actor_email=user.get("email", "system"), actor_role=str(user.get("role", "")),
                  detail=f"approved the purchase order to {vendor}, total {out.get('total')}"[:300])
        _record_line(po_id, vendor, number, job, user)
        return out
    raise jobs.JobError("This purchase order could not be given a number just now. Try again.")


def approve(po_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    """Refused until a manager has approved the design. Approving assigns the PO number, once."""
    if user.get("role") not in ("ADMIN", "MANAGER"):
        raise jobs.JobError("Only a manager can approve a purchase order.")
    with _lock:
        with SessionLocal() as db:
            row = _row(db, po_id)
            if row.status == "CANCELLED":
                raise jobs.JobError("This purchase order was cancelled; it cannot be approved.")
            job = jobs.get(row.job_id)
            if not job["design"]["approved_at"]:
                raise jobs.JobError(f"The design for {job['ref']} is not approved yet. A manager approves it on the job first.")
            if not (row.lines or []):
                raise jobs.JobError("This purchase order has no lines yet.")
        return _approve_row(po_id, user, job)


def _vendor_address(vendor: str) -> str:
    """The vendor's address from the vendor list, when the list has it; blank otherwise (the real PO leaves it blank when unknown)."""
    try:
        from app.core.container import get_entity_matcher

        want = _norm(vendor)
        for rec in get_entity_matcher().vendors_cache.values():
            if want and want in (_norm(rec.get("vendor_name") or ""), _norm(rec.get("vendor_id") or "")):
                return str(rec.get("vendor_address") or "")
    except Exception:  # noqa: BLE001 - no address is not an error
        pass
    return ""


def pdf(po_id: int) -> bytes:
    """The PO as a PDF, always built from the saved record (what is previewed is what is sent). Blanks stay blank, as on Allied's own form."""
    with SessionLocal() as db:
        r = _row(db, po_id)
        return order_documents.vendor_po_pdf({
            "po_number": r.po_number or "", "vendor_name": r.vendor, "vendor_address": _vendor_address(r.vendor), "terms": r.terms or "",
            "incoterm": " ".join(x for x in (r.incoterm, r.incoterm_place) if x), "currency": effective_currency(r)["currency"] or "",
            "ship_via": r.ship_via or "", "good_thru": r.good_thru or "", "lines": r.lines or [],
            "date": r.approved_at.strftime("%m/%d/%y") if r.approved_at else ""})


def email_draft(po_id: int, user: Dict[str, Any], file_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Queues the vendor email (PO PDF + the job's design files) for a person to OK. Nothing is sent here. Asking again while that draft is
    still waiting returns it, so the vendor can never be queued the same PO twice. `file_ids` picks which of the job's files go (default: all)."""
    from app.models.db_models import AssistantActionDB
    from app.services import monday_events
    from app.services.coordination.autopilot import Intent, reviewers

    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status != "APPROVED":
            raise jobs.JobError("Approve the purchase order first; only an approved one can be emailed.")
        if not r.vendor_email:
            raise jobs.JobError(f"No email on file for {r.vendor}. Add it to the purchase order first.")
        if r.action_id:
            waiting = db.get(AssistantActionDB, r.action_id)
            if waiting is not None and waiting.status == "PENDING":
                return _as_dict(r)
        job = jobs.get(r.job_id)
        files = [f for f in job["design"]["files"] if (f["id"] in set(file_ids) if file_ids is not None else f.get("design"))]
        lines = "\n".join((f"  - {ln.get('qty') or ''} {ln.get('description') or ''}" + (f" (drawing revision {ln['revision']})" if ln.get("revision") else "")).replace("\n", " ")
                          for ln in (r.lines or []))
        ids = [f["id"] for f in files]
        drawings = " and the approved drawings" if ids else ""
        body = (f"Hello,\n\nPlease find Allied Welding Systems purchase order {r.po_number}{drawings} attached.\n\nItems:\n{lines}\n\n"
                "Please send us your proforma invoice with your lead time and freight"
                + (", and confirm you are working to the attached drawing revision" if ids else "") + ".\n\n"
                f"BLIND SHIPMENT: {order_documents.default_notes(r.vendor)[0]}\n\nThank you,\nAllied Welding Systems")
        shown = [{"name": f"PO_{r.po_number}.pdf", "size": None}] + [{"name": f["name"], "size": f["size"]} for f in files]
        why = [] if ids else ["No drawings are marked for the vendor on this job, so none are attached. Mark the design files first, or send without drawings."]
        key = f"vendor-po:{r.po_number}:{uuid.uuid4().hex[:6]}"
        monday_events._queue_drafts("vendor_po", [Intent("draft", key, f"Send purchase order {r.po_number} to {r.vendor}",
                                                         to=reviewers(user.get("email")), tool="send_vendor_po",
                                                         args={"to": r.vendor_email, "vendor_name": r.vendor, "po_number": r.po_number, "po_id": r.id,
                                                               "body": body, "attach_file_ids": ids,
                                                               "_attachments": shown, "_why": why, "_job_id": r.job_id})])
        r.action_id = uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{key}").hex
        db.commit()
        return _as_dict(r)


def _typed_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return order_documents.po_lines(lines, strict=True)
    except ValueError as exc:
        raise jobs.JobError(str(exc)) from exc


def by_number(po_number: str) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        r = db.query(PurchaseOrderDB).filter(PurchaseOrderDB.po_number == str(po_number)).first()
        return _as_dict(r) if r else None


def _send_age(r: PurchaseOrderDB) -> float:
    if not r.sent_at:
        return 0.0
    started = r.sent_at if r.sent_at.tzinfo else r.sent_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds()


def begin_send(po_id: int) -> Any:
    """Claims an APPROVED, numbered PO for sending (-> SENDING) under the lock, then lets go of the lock for the network send. A PO already sent or
    being sent is refused. A SENDING claim older than ten minutes means the process died mid-send: nobody can tell from the app whether the vendor got
    it, so it is never sent again on a guess; a manager checks and settles it with resolve_send."""
    settle_from_ledger(po_id)                       # an interrupted send the ledger can settle is settled first (sent, or reopened)
    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status in ("SENT", "ACKNOWLEDGED"):
            raise ValueError(f"Purchase order {r.po_number} has already been sent to the vendor.")
        if r.status == "SENDING":
            if _send_age(r) >= 600:
                raise ValueError(f"A send of purchase order {r.po_number} was interrupted and may have gone out. Check with the vendor, then a manager marks it sent or reopens it.")
            raise ValueError(f"Purchase order {r.po_number} is being sent right now.")
        if r.status != "APPROVED" or not r.po_number:
            raise ValueError("Only an approved, numbered purchase order can be sent.")
        r.status, r.sent_at = "SENDING", datetime.now(timezone.utc)
        db.commit()
        return r.sent_at.replace(tzinfo=None)                  # the claim's token: only this send may settle it


def finish_send(po_id: int, sent: bool, token: Any = None) -> None:
    """Records the result: SENT only when the mail really went to the vendor; otherwise the PO is APPROVED again (so it can be sent when mail works)."""
    with _lock, SessionLocal() as db:
        r = db.get(PurchaseOrderDB, int(po_id))
        if r is not None and r.status == "SENDING" and token is not None and r.sent_at is not None and r.sent_at.replace(tzinfo=None) != token:
            if sent:      # a superseded send's mail DID reach the vendor: record it, so a failing newer send cannot reopen the PO for a third mail
                r.status, r.sent_at = "SENT", datetime.now(timezone.utc)
                db.commit()
                audit_log("PO_SEND_SETTLED", "purchase_orders", str(r.po_number), actor_email="system", detail="an older send went out after a reopen and a newer send began: marked sent, the vendor may have it twice")
            return                                            # otherwise a newer send holds the claim: its result is not ours to settle
        if r is not None and r.status == "SENDING":
            r.status, r.sent_at = ("SENT", datetime.now(timezone.utc)) if sent else ("APPROVED", None)
            db.commit()
            if sent:
                from app.services import payments

                payments.fire(int(po_id), "PO_ISSUED")                      # the first payment (the deposit) becomes due once the PO has really gone to the vendor
        elif r is not None and sent and r.status == "APPROVED":          # a manager reopened it while the mail was still going: it did go, so it is SENT
            r.status, r.sent_at = "SENT", datetime.now(timezone.utc)
            db.commit()
            audit_log("PO_SEND_SETTLED", "purchase_orders", str(r.po_number), actor_email="system", detail="the mail went after the send was reopened: marked sent")


def settle_from_ledger(po_id: int) -> Optional[str]:
    """For a PO stuck in SENDING: the send ledger often knows. A mail it shows as really SENT makes the PO SENT; a mail it shows as FAILED, only drafted
    or redirected to the pilot inbox (and none still ATTEMPTING) reopens it as APPROVED. With nothing recorded, or a mail still ATTEMPTING, a
    manager must decide (None). Returns the new status when it settled."""
    from app.models.db_models import OutboundMailDB

    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status != "SENDING" or _send_age(r) < 600 or not r.po_number:
            return None
        since = (r.sent_at if r.sent_at.tzinfo else r.sent_at.replace(tzinfo=timezone.utc)) - timedelta(minutes=1)
        rows = [m for m in db.query(OutboundMailDB).filter(OutboundMailDB.category == "PURCHASE_ORDER", OutboundMailDB.reference == str(r.po_number)).all()
                if (m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=timezone.utc)) >= since]
        want = (r.vendor_email or "").strip().casefold()
        if any(m.status == "SENT" and not m.redirected and (not want or not m.to_addr or want in m.to_addr.casefold()) for m in rows):
            r.status = "SENT"
            detail = "the send ledger shows the mail went out: marked sent"
        elif rows and not any(m.status == "ATTEMPTING" for m in rows):
            r.status, r.sent_at = "APPROVED", None
            detail = "the send ledger shows the mail did not reach the vendor: reopened"
        else:
            return None
        db.commit()
        audit_log("PO_SEND_SETTLED", "purchase_orders", str(r.po_number), actor_email="system", detail=detail)
        return r.status


def _wind_down(po_id: int, po_number: Optional[str], draft_id: Optional[str]) -> None:
    """After a cancel: the vendor chasing line made for this PO is marked CANCELLED in the app (never on monday), so the chaser stops asking the vendor, and
    a vendor email still waiting in the review queue is cancelled too."""
    from app.models.db_models import AssistantActionDB, VendorJobDB

    try:
        with SessionLocal() as db:
            db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po_id).update({VendorJobDB.status: "CANCELLED"}, synchronize_session=False)
            if draft_id:
                db.query(AssistantActionDB).filter(AssistantActionDB.id == draft_id, AssistantActionDB.status == "PENDING").update(
                    {AssistantActionDB.status: "CANCELLED"}, synchronize_session=False)
            db.commit()
    except Exception:  # noqa: BLE001 - the PO is cancelled either way; the chase line and the draft are tidied up on a best-effort basis
        logger.exception(f"Could not wind down the chase line or draft for PO {po_number}")


def cancel(po_id: int, reason: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """A manager cancels a purchase order (the customer cancelled, or it was raised by mistake), with a reason. Nothing is emailed: when the vendor
    already has it, the answer carries a warning to tell them. Never while it is being sent. Cancelling twice changes nothing."""
    if user.get("role") not in ("ADMIN", "MANAGER"):
        raise jobs.JobError("Only a manager can cancel a purchase order.")
    why = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(reason or "")).split())[:256]
    if len(why) < 3:
        raise jobs.JobError("Say why it is cancelled (a few words).")
    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status == "CANCELLED":
            return _as_dict(r)
        if r.status == "SENDING":
            raise jobs.JobError("This purchase order is being sent right now. Wait for that to finish (or settle it) before cancelling.")
        was = r.status
        r.status, r.cancelled_at, r.cancel_reason = "CANCELLED", datetime.now(timezone.utc), why
        waiting_draft, r.action_id = r.action_id, None
        db.commit()
        _wind_down(r.id, r.po_number, waiting_draft)
        audit_log("PO_CANCELLED", "purchase_orders", str(r.po_number or r.id), actor_email=user.get("email", "system"), detail=f"was {was}: {why}"[:300])
        out = _as_dict(r)
    if was in ("SENT", "ACKNOWLEDGED"):
        out["warning"] = f"The vendor already has purchase order {out['po_number']}. Nothing was emailed: tell {out['vendor']} it is cancelled."
    return out


def stuck_sending() -> List[Dict[str, Any]]:
    """Purchase orders that have been SENDING for ten minutes or more: the send was interrupted and nobody can tell from the app if the vendor got it."""
    with SessionLocal() as db:
        return [{"id": r.id, "po_number": r.po_number, "vendor": r.vendor, "minutes": int(_send_age(r) // 60)}
                for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.status == "SENDING").all() if _send_age(r) >= 600]


def alert_stuck_sends() -> int:
    """One alert per stuck purchase order per day to leadership (or the admin): check with the vendor, then a manager marks it sent or reopens it."""
    from app.services.coordination import notify

    n = 0
    for po in stuck_sending():
        if settle_from_ledger(po["id"]):                    # the ledger knew: nothing to ask a person
            continue
        try:
            notify.notify("po_send_stuck", f"po-send-stuck:{po['id']}:{notify_today().isoformat()}", f"Purchase order {po['po_number']} may not have reached {po['vendor']}",
                          f"The send of purchase order {po['po_number']} to {po['vendor']} stopped part-way {po['minutes']} minutes ago. Ask the vendor whether they got it, "
                          "then a manager marks it sent or reopens it on the purchase order. It is not sent again until then.",
                          severity="warning", include_leadership=True, link="/orders")
            n += 1
        except Exception:  # noqa: BLE001 - telling people is best effort
            logger.exception("Could not raise the stuck-send alert")
    return n


def resolve_send(po_id: int, sent: bool, user: Dict[str, Any]) -> Dict[str, Any]:
    """A manager settles a PO whose send was interrupted: it did go (SENT) or it did not (APPROVED again, can be sent)."""
    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status != "SENDING" or _send_age(r) < 600:
            raise jobs.JobError("Only a purchase order whose send was interrupted (more than ten minutes ago) needs settling.")
        r.status, r.sent_at = ("SENT", r.sent_at) if sent else ("APPROVED", None)
        db.commit()
        audit_log("PO_SEND_SETTLED", "purchase_orders", str(r.po_number), actor_email=user.get("email", "system"), detail="marked sent" if sent else "reopened")
        return _as_dict(r)


def record_revision_ack(po_id: int, line: Any, revision: Any, by: Any, user: Dict[str, Any]) -> Dict[str, Any]:
    """Records which drawing revision the vendor said it is making for one line (1 is the first line). Only a sent purchase order has one to
    acknowledge. A different revision from the one sent is kept and shown as a mismatch; nothing is blocked."""
    clean = " ".join("".join(ch for ch in re.sub(r"[\x00-\x1f\x7f]+", " ", str(revision or "")) if unicodedata.category(ch) != "Cf").split())[:20]
    if not clean:
        raise jobs.JobError("Say which revision the vendor acknowledged.")
    who = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(by or "")).split())[:100] or user.get("email", "unknown")
    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status not in ("SENT", "ACKNOWLEDGED"):
            raise jobs.JobError("Only a sent purchase order has a revision to acknowledge.")
        if isinstance(line, bool) or not isinstance(line, (int, str)) or not str(line).strip().isdigit() or not (1 <= int(line) <= len(r.lines or [])):
            raise jobs.JobError(f"Line must be a number from 1 to {len(r.lines or [])}.")
        marks = dict(r.revision_marks) if isinstance(r.revision_marks, dict) else {}
        acked = dict(marks.get("acked") or {})
        acked[str(int(line))] = {"revision": clean, "by": who, "at": datetime.now(timezone.utc).isoformat()}
        marks["acked"] = acked
        r.revision_marks = marks
        db.commit()
        audit_log("PO_REVISION_ACK", "purchase_orders", str(r.po_number), actor_email=user.get("email", "system"), detail=f"line {int(line)}: vendor acknowledged revision {clean}")
        return _as_dict(r)


def set_customs(po_id: int, body: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Customs details (tariff number, origin, unit, net kg, certificate) by line number, and the Incoterm and its place, on a purchase order that
    is already approved or even sent, without resending its lines. The core of a line (quantity, item, price, revision) is never touched here."""
    if not isinstance(body, dict):
        raise jobs.JobError("Send the customs details as an object.")
    with _lock, SessionLocal() as db:
        r = _row(db, po_id)
        if r.status in ("SENDING", "CANCELLED"):
            raise jobs.JobError("This purchase order is " + ("being sent right now." if r.status == "SENDING" else "cancelled.") + " Customs details can't change now.")
        incoming = body.get("lines")
        if incoming is not None:
            if not isinstance(incoming, list) or len(incoming) > 100:
                raise jobs.JobError("Lines must be a list (at most 100).")
            lines = [dict(ln) if isinstance(ln, dict) else ln for ln in (r.lines or [])]
            for item in incoming:
                if not isinstance(item, dict) or isinstance(item.get("line"), bool) or not str(item.get("line", "")).strip().isdigit() \
                        or not (1 <= int(item["line"]) <= len(lines)) or not isinstance(lines[int(item["line"]) - 1], dict):
                    raise jobs.JobError(f"Each entry needs a line number from 1 to {len(lines)}.")
                n = int(item["line"]) - 1
                typed = order_documents.po_lines([{**{k: item[k] for k in _CUSTOMS_KEYS if k in item}, "quantity": 1, "description": "x"}])[0]
                for key in _CUSTOMS_KEYS:
                    if key not in item:
                        continue                      # not mentioned: left as it is
                    if key in typed:
                        lines[n][key] = typed[key]
                    elif item[key] in (None, "", False, 0):
                        lines[n].pop(key, None)      # blank clears it
                    else:
                        raise jobs.JobError(f"Line {n + 1}: '{key}' is not valid" + (" (a tariff number like 7318.15.20)." if key == "hts_code" else "."))
            _label = {"hts_code": "tariff code", "origin": "country of origin", "uom": "unit", "net_kg": "net weight (kg)", "cert_required": "certificate required"}
            _show = lambda v: "blank" if v in (None, "") else str(v)
            changed = [f"line {i + 1} {_label.get(k, k)}: {_show((r.lines[i] or {}).get(k))} -> {_show(lines[i].get(k))}" for i in range(len(lines))
                       if isinstance(lines[i], dict) and i < len(r.lines or []) and isinstance(r.lines[i], dict)
                       for k in _CUSTOMS_KEYS if (r.lines[i] or {}).get(k) != lines[i].get(k)]
            r.lines = lines
        else:
            changed = []
        if "incoterm" in body:
            term = re.sub(r"[^A-Za-z0-9 ]+", "", str(body["incoterm"] or "")).strip().upper()[:16]
            if term and term not in _INCOTERMS:
                raise jobs.JobError("The Incoterm must be one of: " + ", ".join(sorted(_INCOTERMS)) + " (or left blank).")
            r.incoterm = term or None
        if "incoterm_place" in body:
            r.incoterm_place = " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(body["incoterm_place"] or "")).split())[:64] or None
        if r.status in ("SENT", "ACKNOWLEDGED"):          # the vendor's copy still has the old details: say so until someone resends or settles it
            marks = dict(r.revision_marks) if isinstance(r.revision_marks, dict) else {}
            marks["customs_after_send"] = datetime.now(timezone.utc).isoformat()
            r.revision_marks = marks
        db.commit()
        audit_log("PO_CUSTOMS_EDIT", "purchase_orders", str(r.po_number), actor_email=user.get("email", "system"), detail=("customs details changed: " + "; ".join(changed))[:900] if changed else "customs details changed")
        return _as_dict(r)


def acknowledge(po_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    with SessionLocal() as db:
        r = _row(db, po_id)
        if r.status != "SENT":
            raise jobs.JobError("Only a sent purchase order can be marked acknowledged.")
        r.status = "ACKNOWLEDGED"
        db.commit()
        return _as_dict(r)


def number_for_rfq(rfq_number: str, vendor: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """For a PO drafted straight from an RFQ: this job's PO for the vendor, numbered and approved now (once). Refused unless the design is
    approved. Returns {"po_number", "id"}."""
    if user.get("role") not in ("ADMIN", "MANAGER"):
        raise jobs.JobError("Only a manager can approve a purchase order.")
    j = jobs.from_rfq(rfq_number, user)
    if not j["design"]["approved_at"]:
        raise jobs.JobError(f"The design for {j['ref']} is not approved yet. A manager approves it on the job first.")
    with _lock:
        po = find_for_vendor(j["id"], vendor) or create(j["id"], vendor, user)
        out = po if (po["po_number"] and po["status"] != "DRAFT") else _approve_row(po["id"], user, j)
    return {"po_number": str(out["po_number"]), "id": out["id"], "status": out["status"]}


def attachments(file_ids: List[Any], strict: bool = False) -> List[Dict[str, Any]]:
    """The files to attach. With strict=True a file that was removed (or can no longer be read) stops the send with a plain message, instead of
    the vendor receiving an email that quietly lacks its drawing."""
    out = []
    from app.services import redaction

    for fid in file_ids or []:
        blocked = redaction.vendor_block(fid)                                  # the customer-redaction firewall: only a verified redacted (or attested) copy goes to a vendor
        if blocked:
            raise ValueError(blocked)
        try:
            path, name, mime = item_files.open_file(int(fid))
            out.append({"filename": name, "content": path.read_bytes(), "mime": mime})
        except (LookupError, ValueError, TypeError, OSError):
            if strict:
                raise ValueError("A drawing on this email was removed or can no longer be read. Put it back, or take it off the draft, then send again.")
    return out


def design_file_ids(rfq_number: str) -> set:
    """The ids of the design-marked files on an RFQ: the only files a vendor may be sent."""
    return {f["id"] for f in item_files.listing("rfq", rfq_number) if f.get("design")}


def link_vendor_lines() -> int:
    """Points each vendor line that carries a PO number at the purchase order record with that number. Returns how many were linked."""
    from app.models.db_models import VendorJobDB

    n = 0
    with SessionLocal() as db:
        by_number = {str(r.po_number): r.id for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.po_number.isnot(None)).all()}
        for line in db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id.is_(None), VendorJobDB.po_number.isnot(None)).all():
            pid = by_number.get(str(line.po_number))
            if pid:
                line.purchase_order_id = pid
                n += 1
        db.commit()
    return n


def unified(q: str = "", vendor: str = "", status: str = "", staff: bool = True) -> List[Dict[str, Any]]:
    """Every purchase order once, whichever place first recorded it: the PO table, the board's vendor lines and the orders' own lists are merged
    by PO number. One row per PO, so a PO is never shown twice. The sales order number is internal: only staff see it."""
    from app.models.db_models import JobDB, OrderDB, VendorJobDB

    rows: Dict[str, Dict[str, Any]] = {}

    def row(po: str, vend: str) -> Dict[str, Any]:
        return rows.setdefault(str(po), {"po_number": str(po), "vendor": vend, "status": None, "board_status": None, "job": None, "purchase_order_id": None,
                                         "vendor_line_id": None, "sales_order_number": None, "promised_on": None, "shipped_on": None, "sources": []})

    with SessionLocal() as db:
        jobs_by_id = {j.id: j.ref for j in db.query(JobDB).all()}
        for p in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.po_number.isnot(None)).all():
            r = row(p.po_number, p.vendor)
            r.update(vendor=p.vendor, status=p.status, job=jobs_by_id.get(p.job_id), purchase_order_id=p.id)
            r["sources"].append("purchase order")
        for v in db.query(VendorJobDB).filter(VendorJobDB.po_number.isnot(None)).all():
            r = row(v.po_number, v.vendor)
            r.update(vendor_line_id=v.id, board_status=v.status, promised_on=v.promised_on, shipped_on=v.shipped_on)
            r["status"] = r["status"] or v.status
            r["sources"].append("vendor line")
        for o in db.query(OrderDB).all():
            for code, po in (o.purchase_orders or {}).items():
                r = row(po, code)
                r["status"] = r["status"] or o.status
                if staff:
                    r["sales_order_number"] = o.sales_order_number
                r["sources"].append("order")
    out = list(rows.values())
    t = (q or "").casefold()
    if t:
        out = [r for r in out if t in " ".join(str(r.get(k) or "") for k in ("po_number", "vendor", "job", "sales_order_number")).casefold()]
    if vendor:
        out = [r for r in out if vendor.casefold() in str(r["vendor"]).casefold()]
    if status:
        out = [r for r in out if status.casefold() in f"{r['status'] or ''} {r['board_status'] or ''}".casefold()]
    out.sort(key=lambda r: (-int(r["po_number"]) if r["po_number"].isdigit() else 0, r["po_number"]))
    return out

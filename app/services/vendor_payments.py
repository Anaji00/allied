"""Single-entry vendor payment sync. One entry records a payment against a due tranche and, from that one entry:
  1. updates the job packet's payment sheet record (a PAYMENT_SHEET document on the job: payments, paid total, remaining),
  2. stages an update for monday (never pushed from here),
  3. rebuilds the master vendor-payments xlsx ledger (one sheet per vendor, written atomically from the database, so it can never hold a row twice),
  4. stages a remittance email draft to the vendor attaching the wire copy and the proforma (a person sends it).
Idempotent: a tranche is paid once, and the same entry again (same idempotency key, by default the tranche) returns the first one and changes nothing. Money is Decimal."""
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import ItemFileDB, JobDB, JobDocumentDB, PaymentScheduleDB, PaymentTrancheDB, PurchaseOrderDB, VendorPaymentDB
from app.services.payments import CENT, PaymentError, _dec

_ledger_lock = threading.Lock()
METHODS = ("WIRE", "ACH", "CHECK", "CARD", "OTHER")


def ledger_path() -> Path:
    return Path(settings.OUTPUT_DIR) / "vendor_payments_ledger.xlsx"


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v else None


def _as_dict(p: VendorPaymentDB) -> Dict[str, Any]:
    return {"id": p.id, "tranche_id": p.tranche_id, "idem_key": p.idem_key, "paid_on": p.paid_on, "amount": f"{p.amount:.2f}", "currency": p.currency, "method": p.method, "reference": p.reference,
            "wire_file_id": p.wire_file_id, "proforma_file_id": p.proforma_file_id, "recorded_by": p.recorded_by, "recorded_at": _iso(p.recorded_at), "ledger_written_at": _iso(p.ledger_written_at),
            "monday_staged_at": _iso(p.monday_staged_at), "remittance_action_id": p.remittance_action_id, "voided": bool(p.voided_at)}


def _belongs(db, f: ItemFileDB, po: Optional[PurchaseOrderDB], job: Optional[JobDB]) -> bool:
    """A wire copy or proforma must be a file attached to THIS job, its RFQ, this PO or a vendor line of this PO: never any file on the server."""
    from app.models.db_models import RfqDB, VendorJobDB

    kind, ref = str(f.item_type or ""), str(f.item_ref or "")
    # Only files uploaded for THIS PO or THIS vendor's line. Job and RFQ files are shared by every vendor on the job and hold the customer's own documents, so they never qualify.
    if kind in ("po", "purchase_order"):
        return po is not None and ref in (str(po.id), str(po.po_number))
    if kind == "vendor_job":
        v = db.get(VendorJobDB, int(ref)) if ref.isdigit() else None
        return v is not None and po is not None and (v.purchase_order_id == po.id or (po.po_number and v.po_number == po.po_number))
    return False


def _check_file(db, file_id: Optional[int], what: str, po: Optional[PurchaseOrderDB] = None, job: Optional[JobDB] = None) -> Optional[int]:
    if file_id in (None, ""):
        return None
    f = db.get(ItemFileDB, int(file_id))
    if f is None or f.deleted_at:
        raise PaymentError(422, f"The {what} file is not on the server (upload it first, then give its number).")
    if not _belongs(db, f, po, job):
        raise PaymentError(422, f"The {what} file does not belong to this job, its RFQ, this PO or this vendor's line, so it was not used.")
    return int(file_id)


def record(tranche_id: int, user: Dict[str, Any], paid_on: str, amount: Any = None, method: str = "WIRE", reference: str = "", wire_file_id: Any = None, proforma_file_id: Any = None,
           idem_key: str = "", draft_remittance: bool = True) -> Dict[str, Any]:
    """Records one payment. {payment, duplicate, effects}: duplicate is True when this exact entry was already recorded (nothing is done twice)."""
    key = (idem_key or f"tranche-{int(tranche_id)}").strip()[:80]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(paid_on or "")):
        raise PaymentError(422, "Give the date it was paid as YYYY-MM-DD.")
    try:
        datetime.strptime(paid_on, "%Y-%m-%d")
    except ValueError:
        raise PaymentError(422, "That is not a real date.")
    if str(method or "").upper() not in METHODS:
        raise PaymentError(422, f"The method is one of: {', '.join(METHODS)}.")
    with SessionLocal() as db:
        existing = db.query(VendorPaymentDB).filter(VendorPaymentDB.idem_key == key).first()
        if existing is not None:
            if existing.tranche_id != int(tranche_id):
                raise PaymentError(409, "That idempotency key was already used for a different payment. Use a new key.")
            return {"payment": _as_dict(existing), "duplicate": True, "effects": {}}
        t = db.get(PaymentTrancheDB, int(tranche_id))
        if t is None:
            raise PaymentError(404, "No such payment.")
        if t.status == "PAID":
            raise PaymentError(409, "This payment has already been recorded.")
        sched0 = db.get(PaymentScheduleDB, t.schedule_id)
        po0 = db.get(PurchaseOrderDB, sched0.po_id) if sched0 is not None else None
        if sched0 is None or sched0.status != "ACTIVE" or t.status == "VOID":
            raise PaymentError(409, "This payment belongs to a schedule that was replaced, so it cannot be paid.")
        if po0 is None or po0.status == "CANCELLED":
            raise PaymentError(409, "This purchase order was cancelled, so nothing is paid against it.")
        if t.status != "DUE":
            why = {"GATE2_APPROVED": "Gate 2 (measurement photos) has not been approved, so this payment cannot go out yet.", "DELIVERY_CONFIRMED": "Delivery has not been confirmed yet.",
                   "PO_ISSUED": "The PO has not been sent to the vendor yet.", "PROFORMA": "The vendor's proforma has not arrived yet."}.get(t.trigger, "This payment is not due yet.")
            raise PaymentError(409, why)
        sched = db.get(PaymentScheduleDB, t.schedule_id)
        due_amount = Decimal(t.amount).quantize(CENT)
        paid = _dec(amount, "amount").quantize(CENT) if amount not in (None, "") else due_amount
        if paid != due_amount:
            raise PaymentError(422, f"The amount must be {due_amount:.2f}, what this payment is due for.")
        job0 = db.get(JobDB, sched0.job_id) if sched0.job_id else None
        wire, pro = _check_file(db, wire_file_id, "wire copy", po0, job0), _check_file(db, proforma_file_id, "proforma", po0, job0)
        now_ = datetime.now(timezone.utc)
        won = db.query(PaymentTrancheDB).filter(PaymentTrancheDB.id == t.id, PaymentTrancheDB.status == "DUE").update({PaymentTrancheDB.status: "PAID", PaymentTrancheDB.paid_at: now_}, synchronize_session=False)
        if not won:                                                            # another entry paid it between our check and now: only one can
            db.rollback()
            raise PaymentError(409, "This payment has already been recorded.")
        row = VendorPaymentDB(tranche_id=t.id, idem_key=key, paid_on=paid_on, amount=paid, currency=sched.currency if sched else None, method=str(method).upper(), reference=(reference or "")[:120],
                              wire_file_id=wire, proforma_file_id=pro, recorded_by=user.get("email"))
        db.add(row)
        try:
            db.commit()
        except IntegrityError:                                         # the same entry arrived twice at once: the other one won (and the tranche flip above was undone with this attempt)
            db.rollback()
            first = db.query(VendorPaymentDB).filter(VendorPaymentDB.idem_key == key).first()
            if first is None or first.tranche_id != int(tranche_id):
                raise PaymentError(409, "This payment has already been recorded.")
            return {"payment": _as_dict(first), "duplicate": True, "effects": {}}
        db.refresh(row)
        pid, sid, po_id, job_id = row.id, t.schedule_id, (sched.po_id if sched else None), (sched.job_id if sched else None)
    audit_log(action="VENDOR_PAYMENT_RECORDED", resource_type="PURCHASE_ORDER", resource_id=str(po_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"Vendor payment {paid:.2f} on {paid_on} ({str(method).upper()}) recorded against tranche {tranche_id}")
    effects = {"sales_sheet": _safe(update_sales_sheet, job_id, sid), "monday": _safe(stage_monday, pid), "ledger": _safe(write_ledger, pid),
               "remittance_draft": _safe(draft_remittance_email, pid, user) if draft_remittance else None}
    with SessionLocal() as db:
        return {"payment": _as_dict(db.get(VendorPaymentDB, pid)), "duplicate": False, "effects": effects}


def _safe(fn, *args):
    """One effect failing never undoes the payment record; it says so, and can be run again."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Vendor payment effect {fn.__name__} failed: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}"}


def sheet_summary(schedule_id: int) -> Dict[str, Any]:
    with SessionLocal() as db:
        s = db.get(PaymentScheduleDB, schedule_id)
        tr = db.query(PaymentTrancheDB).filter(PaymentTrancheDB.schedule_id == schedule_id).order_by(PaymentTrancheDB.seq).all()
        pays = {p.tranche_id: p for p in db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id.in_([t.id for t in tr]), VendorPaymentDB.voided_at.is_(None)).all()} if tr else {}
        paid = sum((t.amount for t in tr if t.status == "PAID"), Decimal("0"))
        return {"po_id": s.po_id, "vendor": s.vendor, "currency": s.currency, "total": f"{s.total:.2f}", "terms": s.terms_code, "paid": f"{paid:.2f}", "remaining": f"{s.total - paid:.2f}",
                "tranches": [{"seq": t.seq, "label": t.label, "percent": f"{t.percent:.2f}", "amount": f"{t.amount:.2f}", "status": t.status, "paid_on": (pays.get(t.id).paid_on if pays.get(t.id) else None),
                              "reference": (pays.get(t.id).reference if pays.get(t.id) else None)} for t in tr]}


def update_sales_sheet(job_id: Optional[int], schedule_id: int) -> Dict[str, Any]:
    """The job packet's payment sheet record: one PAYMENT_SHEET document per job and PO, rewritten from the schedule (so repeats cannot double anything)."""
    if not job_id:
        return {"ok": False, "skipped": "not tied to a job"}
    info = sheet_summary(schedule_id)
    sha = f"payment-sheet-{job_id}-{info['po_id']}"
    with SessionLocal() as db:
        doc = db.query(JobDocumentDB).filter(JobDocumentDB.job_id == job_id, JobDocumentDB.kind == "PAYMENT_SHEET", JobDocumentDB.source_sha == sha).first()
        if doc is None:
            doc = JobDocumentDB(job_id=job_id, kind="PAYMENT_SHEET", label="Vendor payments", page_from=0, page_to=0, fields=info, source_name="payments", source_sha=sha, created_by="system")
            db.add(doc)
        else:
            doc.fields = info
        db.commit()
    return {"ok": True, "paid": info["paid"], "remaining": info["remaining"]}


def stage_monday(payment_id: int) -> Dict[str, Any]:
    """An update staged for the job's monday item (a person pushes it). Nothing is sent from here."""
    from app.services import monday_push

    with SessionLocal() as db:
        p = db.get(VendorPaymentDB, payment_id)
        t = db.get(PaymentTrancheDB, p.tranche_id)
        s = db.get(PaymentScheduleDB, t.schedule_id)
        job = db.get(JobDB, s.job_id) if s.job_id else None
        if job is None or not (job.monday_board_id and job.monday_item_id):
            return {"ok": False, "skipped": "the job is not linked to a monday item"}
        po = db.get(PurchaseOrderDB, s.po_id)
        text = f"Vendor payment recorded: {s.vendor}, PO {(po.po_number if po else '') or s.po_id}, {t.label}, {p.amount:.2f} {s.currency or ''} on {p.paid_on}.".replace("  ", " ")
        board, item = job.monday_board_id, job.monday_item_id
        key = f"vendor-payment:{p.idem_key}"
    change = monday_push.stage("post_update", board, item, {"text": text}, f"Vendor payment on PO {s.po_id}", key=key, source="payments", by=p.recorded_by or "app")
    with SessionLocal() as db:
        db.get(VendorPaymentDB, payment_id).monday_staged_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True, "change_id": change}


_RESERVED_SHEETS = {"history"}


def _text(value: Any) -> Any:
    """A text cell that starts like a formula (= + - @, or a control character) is written with a leading apostrophe, so Excel shows it and never runs it."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + value
    return value


def _sheet_name(vendor: str, used: set) -> str:
    base = re.sub(r"[\[\]\*\?/\\:]", " ", vendor or "Vendor").strip().strip("'").strip()[:31].strip("'").strip() or "Vendor"
    if base.casefold() in _RESERVED_SHEETS:
        base = (base + " sheet")[:31]
    name, n = base, 2
    while name.casefold() in used:
        name = f"{base[:28]} {n}"
        n += 1
    used.add(name.casefold())
    return name


def write_ledger(payment_id: Optional[int] = None) -> Dict[str, Any]:
    """The master ledger, rebuilt from the database and swapped in atomically: a crash leaves the old file or the new one, never half of one. One sheet per vendor, plus a Summary sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    with SessionLocal() as db:
        rows = (db.query(VendorPaymentDB, PaymentTrancheDB, PaymentScheduleDB).join(PaymentTrancheDB, PaymentTrancheDB.id == VendorPaymentDB.tranche_id)
                .join(PaymentScheduleDB, PaymentScheduleDB.id == PaymentTrancheDB.schedule_id).filter(VendorPaymentDB.voided_at.is_(None)).order_by(VendorPaymentDB.paid_on, VendorPaymentDB.id).all())
        po_numbers = {po.id: po.po_number for po in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.id.in_({s.po_id for _, _, s in rows}))} if rows else {}
        data = [(p.id, s.vendor or "", po_numbers.get(s.po_id) or str(s.po_id), t.label, p.paid_on, p.amount, s.currency or "", p.method or "", p.reference or "", p.recorded_by or "") for p, t, s in rows]
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append(["Vendor", "Currency", "Payments", "Total paid"])
    head = ["Date", "PO", "Payment", "Amount", "Currency", "Method", "Reference", "Recorded by", "Entry"]
    used = {"summary"}
    by_vendor: Dict[str, List[tuple]] = {}
    for r in data:
        by_vendor.setdefault(r[1], []).append(r)
    totals: Dict[tuple, Decimal] = {}
    for vendor in sorted(by_vendor, key=str.casefold):
        ws = wb.create_sheet(_sheet_name(vendor, used))
        ws.append(head)
        for c in ws[1]:
            c.font = Font(bold=True)
        for (pid, _v, po, label, paid_on, amount, cur, method, ref, who) in by_vendor[vendor]:
            ws.append([_text(paid_on), _text(po), _text(label), float(amount), _text(cur), _text(method), _text(ref), _text(who), pid])
            k = (vendor, cur)
            totals[k] = totals.get(k, Decimal("0")) + Decimal(amount)
    for (vendor, cur), total in sorted(totals.items(), key=lambda kv: (kv[0][0].casefold(), kv[0][1])):
        summary.append([_text(vendor), cur, len([r for r in by_vendor[vendor] if r[6] == cur]), float(total)])
    for c in summary[1]:
        c.font = Font(bold=True)
    path = ledger_path()
    with _ledger_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".ledger.", suffix=".tmp", dir=str(path.parent))
        os.close(fd)
        try:
            wb.save(tmp)
            os.replace(tmp, path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.query(VendorPaymentDB).filter(VendorPaymentDB.voided_at.is_(None), VendorPaymentDB.ledger_written_at.is_(None)).update({VendorPaymentDB.ledger_written_at: now})
        db.commit()
    return {"ok": True, "rows": len(data), "path": path.name}


def draft_remittance_email(payment_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    """A remittance advice staged as a draft email to the vendor (a person reads it and sends it): it attaches the wire copy and the proforma, and names only the PO, the amount and the date."""
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    with SessionLocal() as db:
        p = db.get(VendorPaymentDB, payment_id)
        if p.remittance_action_id:
            return {"ok": True, "action_id": p.remittance_action_id, "again": True}
        t = db.get(PaymentTrancheDB, p.tranche_id)
        s = db.get(PaymentScheduleDB, t.schedule_id)
        po = db.get(PurchaseOrderDB, s.po_id)
        email = (po.vendor_email or "").strip() if po else ""
        if not email:
            return {"ok": False, "skipped": "no email is on file for the vendor on this PO"}
        names = {}
        for fid in (p.wire_file_id, p.proforma_file_id):
            f = db.get(ItemFileDB, fid) if fid else None
            if f is not None and not f.deleted_at:
                names[f.id] = f.filename
        number = po.po_number or str(po.id)
        body = "\n".join(["Hello,", "", f"We have sent a payment on Allied PO {number}.", f"Amount: {p.amount:.2f} {s.currency or ''}".strip(), f"Date: {p.paid_on}", f"Method: {p.method or ''}",
                          f"Reference: {p.reference}" if p.reference else "", "", "The wire copy and the proforma are attached." if names else "", "Please confirm when you have it.", "", "Thank you,",
                          "Allied Welding Systems"])
        specs = [{"type": "payment_doc", "id": fid, "name": nm} for fid, nm in names.items()]
        args = {"to": email, "subject": f"Allied PO {number}: payment sent", "body": "\n".join(l for i, l in enumerate(body.split("\n")) if l or (i and body.split("\n")[i - 1])), "_audience": "vendor",
                "_attach": specs, "_attachments": [{"name": n, "size": None} for n in names.values()], "_job_id": s.job_id, "_payment_id": p.id}
    action = agent._propose(TOOLS["send_email"], args, user)
    with SessionLocal() as db:
        db.get(VendorPaymentDB, payment_id).remittance_action_id = action["id"]
        db.commit()
    return {"ok": True, "action_id": action["id"], "attachments": list(names.values())}


def draft_tranche_email(tranche_id: int, user: Dict[str, Any], note: str = "", wire_file_id: Optional[int] = None, proforma_file_id: Optional[int] = None) -> Dict[str, Any]:
    """A short note to the vendor about one payment step, staged as a draft a person reads and sends. It names the PO and the amount; the wire copy and the proforma are attached only if they
    are files of this job or PO (otherwise the person adds them). Nothing is sent and no payment is recorded."""
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    note = " ".join(str(note or "").split())[:500]
    if note:
        from app.services.coordination import blind

        names = list(blind.customer_names())
        with SessionLocal() as db0:
            t0 = db0.get(PaymentTrancheDB, tranche_id)
            s0 = db0.get(PaymentScheduleDB, t0.schedule_id) if t0 else None
            j0 = db0.get(JobDB, s0.job_id) if s0 and s0.job_id else None
            if j0 is not None and j0.customer:
                names.append(str(j0.customer))
        if blind.found(note, [n for n in names if n], min_len=2):
            raise PaymentError(422, "The note names the customer. A vendor is never told the customer's name; take it out.")
    with SessionLocal() as db:
        t = db.get(PaymentTrancheDB, tranche_id)
        if t is None:
            raise PaymentError(404, "There is no such payment step.")
        s = db.get(PaymentScheduleDB, t.schedule_id)
        po = db.get(PurchaseOrderDB, s.po_id) if s else None
        job = db.get(JobDB, s.job_id) if s and s.job_id else (db.get(JobDB, po.job_id) if po else None)
        if po is None:
            raise PaymentError(404, "The purchase order for this payment step is gone.")
        email = (po.vendor_email or "").strip()
        if not email:
            raise PaymentError(422, "No email is on file for the vendor on this PO. Add it to the PO first.")
        names: Dict[int, str] = {}
        for fid, what in ((wire_file_id, "wire copy"), (proforma_file_id, "proforma")):
            if _check_file(db, fid, what, po, job):
                names[int(fid)] = db.get(ItemFileDB, int(fid)).filename
        number = po.po_number or str(po.id)
        label = f" ({t.label})" if t.label else ""
        lines = ["Hello,", "", f"This is about Allied PO {number}{label}.", f"Payment step: {t.amount:.2f} {s.currency or ''}".strip(), (f"Due: {t.due_on}" if t.due_on else ""), ""]
        if note:
            lines += [note, ""]
        lines += ["The wire copy and the proforma are attached." if names else "Please send your proforma invoice if you have not already.", "", "Thank you,", "Allied Welding Systems"]
        specs = [{"type": "po_file", "id": fid, "po": po.id, "name": nm} for fid, nm in names.items()]
        args = {"to": email, "subject": f"Allied PO {number}: payment", "body": "\n".join(l for i, l in enumerate(lines) if l or (i and lines[i - 1])), "_audience": "vendor",
                "_attach": specs, "_attachments": [{"name": n, "size": None} for n in names.values()], "_job_id": s.job_id}
        vendor, amount = po.vendor, f"{t.amount:.2f}"
    action = agent._propose(TOOLS["send_email"], args, user)
    audit_log(action="PAYMENT_EMAIL_DRAFTED", resource_type="PAYMENT_TRANCHE", resource_id=str(tranche_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"to {email[:80]}")
    return {"drafted": True, "action_id": action["id"], "vendor": vendor, "po_number": number, "amount": amount, "attached": list(names.values()),
            "to_add_by_hand": [w for w, f in (("wire copy", wire_file_id), ("proforma invoice", proforma_file_id)) if not f], "message": "Draft saved for approval. Nothing was sent."}


def po_file_bytes(file_id: int, po_id: int) -> Dict[str, Any]:
    """A file for a vendor email about a PO: only one attached to that PO's job, its RFQ, the PO or its vendor line."""
    with SessionLocal() as db:
        po = db.get(PurchaseOrderDB, po_id)
        job = db.get(JobDB, po.job_id) if po else None
        f = db.get(ItemFileDB, file_id)
        if po is None or f is None or f.deleted_at or not _belongs(db, f, po, job):
            raise PaymentError(403, "That file does not belong to this PO's job, so it was not attached.")
        return {"filename": f.filename, "content": Path(f.stored_path).read_bytes(), "mime": f.content_type or "application/octet-stream"}


def list_for_po(po_id: int) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        ids = [r[0] for r in db.query(PaymentTrancheDB.id).join(PaymentScheduleDB, PaymentScheduleDB.id == PaymentTrancheDB.schedule_id).filter(PaymentScheduleDB.po_id == po_id).all()]
        return [_as_dict(p) for p in db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id.in_(ids)).order_by(VendorPaymentDB.id).all()] if ids else []


def payment_doc_bytes(file_id: int) -> Dict[str, Any]:
    """A wire copy or proforma for a remittance email: only a file that a recorded vendor payment names."""
    with SessionLocal() as db:
        ok = db.query(VendorPaymentDB.id).filter(VendorPaymentDB.voided_at.is_(None), ((VendorPaymentDB.wire_file_id == file_id) | (VendorPaymentDB.proforma_file_id == file_id))).first()
        f = db.get(ItemFileDB, file_id)
        if not ok or f is None or f.deleted_at:
            raise PaymentError(403, "That file is not a wire copy or proforma of a recorded payment, so it was not attached.")
        path = Path(f.stored_path)
        return {"filename": f.filename, "content": path.read_bytes(), "mime": f.content_type or "application/octet-stream"}

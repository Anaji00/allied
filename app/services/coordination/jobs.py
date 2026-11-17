"""Jobs: one record from the customer's RFQ to the shipment. It links what the app already keeps (RFQ, engineering thread, ball,
vendor POs, drafts, sales order, shipments) and works out the stage from what exists. Nothing here writes to monday or Sage."""

import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from app.core.database import SessionLocal
from app.core import status_words
from app.core.logging import logger
from app.models.db_models import (AssistantActionDB, ItemFileDB, JobDocumentDB, ItemUpdateDB, JobDB, JobResearchDB, OrderDB, PurchaseOrderDB, RfqDB,
                                  ShipmentDB, VendorJobDB)
from app.services.coordination import ball, rfqs

_quote_lock = threading.RLock()      # one vendor quotation recorded at a time, so two clicks cannot make two
_so_lock = threading.RLock()        # making a sales order for a job, and tying orders to jobs, never run twice at once
STAGES = ["RFQ", "RESEARCH", "ENGINEERING", "DESIGN_APPROVED", "VENDOR_QUOTE", "CUSTOMER_QUOTED", "CUSTOMER_ORDERED", "PO_ISSUED", "SO_CREATED", "SHIPPED", "CLOSED"]


class JobError(ValueError):
    pass


def _iso(at: Optional[datetime]) -> Optional[str]:
    return at.isoformat() if at else None


def _next_ref(db) -> str:
    year = datetime.now(timezone.utc).year
    n = db.query(JobDB).filter(JobDB.ref.like(f"JOB-{year}-%")).count() + 1
    while db.query(JobDB).filter(JobDB.ref == f"JOB-{year}-{n:04d}").first():
        n += 1
    return f"JOB-{year}-{n:04d}"


def from_rfq(rfq: Any, user: Dict[str, Any]) -> Dict[str, Any]:
    """The job for an RFQ; made once, then the same one is returned."""
    r = rfqs.get(rfq)
    with SessionLocal() as db:
        row = db.query(JobDB).filter(JobDB.rfq_id == r["id"]).first()
        if row is None:
            row = JobDB(ref=_next_ref(db), title=r.get("subject") or r["number"], customer=r.get("customer"), rfq_id=r["id"],
                        created_by=user.get("email", "system"))
            db.add(row)
            db.commit()
            db.refresh(row)
        jid = row.id
    return get(jid)


def _find(db, job: Any) -> JobDB:
    row = db.get(JobDB, int(job)) if str(job).isdigit() and len(str(job)) < 10 else db.query(JobDB).filter(JobDB.ref == str(job).strip().upper()).first()
    if row is None:
        raise LookupError(f"No job {job}.")
    return row


def link_order(job: Any, sales_order_number: str) -> None:
    with SessionLocal() as db:
        row = _find(db, job)
        row.sales_order_number = str(sales_order_number)
        db.commit()


def approve_design(job: Any, user: Dict[str, Any]) -> Dict[str, Any]:
    """A manager's sign-off on the engineers' design. A PO cannot be approved or sent before it."""
    if user.get("role") not in ("ADMIN", "MANAGER"):
        raise JobError("Only a manager can approve a design.")
    with SessionLocal() as db:
        row = _find(db, job)
        if row.design_approved_at is None:
            row.design_approved_by = user.get("email")
            row.design_approved_at = datetime.now(timezone.utc)
            db.commit()
            first = True
        else:
            first = False
        jid, ref = row.id, row.ref
    if first:
        from app.core.audit import audit_log

        audit_log("DESIGN_APPROVED", "jobs", str(ref), actor_email=user.get("email", "system"), actor_role=str(user.get("role", "")), detail="approved the design")
    return get(jid)


def design_approved(job_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(JobDB, job_id)
        return bool(row and row.design_approved_at)


def _po_revisions(r: PurchaseOrderDB) -> List[Dict[str, Any]]:
    from app.services.coordination import purchase_orders    # imported here: that module imports this one

    return purchase_orders.revision_view(r)


def _self_approved(r: PurchaseOrderDB) -> bool:
    """The same person drafted and approved this PO, with time between the two. A PO that one action both drafts and approves (numbered straight from an
    RFQ) is not flagged, because there was no separate drafting step to look over."""
    from app.services.coordination import purchase_orders

    if not purchase_orders._same_person(r.approved_by, r.created_by) or not (r.approved_at and r.created_at):
        return False
    made, ok = r.created_at, r.approved_at
    made = made if made.tzinfo else made.replace(tzinfo=timezone.utc)
    ok = ok if ok.tzinfo else ok.replace(tzinfo=timezone.utc)
    return (ok - made).total_seconds() >= 60


def _parts(row: JobDB) -> Dict[str, Any]:
    with SessionLocal() as db:
        rfq = db.get(RfqDB, row.rfq_id) if row.rfq_id else None
        rfq_d = {"id": rfq.id, "number": rfq.number, "status": rfq.status} if rfq else None
        pos = [{"id": r.id, "po_number": r.po_number, "vendor": r.vendor, "status": r.status, "pdf_url": f"/api/v1/purchase-orders/{r.id}/pdf",
                "incoterm": r.incoterm, "revision_mismatch": [x["line"] for x in _po_revisions(r) if x["mismatch"]],
                "lines_missing_overseas": sum(1 for ln in (r.lines or []) if isinstance(ln, dict) and (not ln.get("hts_code") or not ln.get("origin"))),
                "cert_lines": sum(1 for ln in (r.lines or []) if isinstance(ln, dict) and ln.get("cert_required")),
                "approved_by_creator": _self_approved(r)}
               for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == row.id).order_by(PurchaseOrderDB.id)]
        if rfq:
            for j in db.query(VendorJobDB).filter(VendorJobDB.description.like(f"{rfq.number}:%")).all():
                if j.purchase_order_id is None and not any(str(p["po_number"]) == str(j.po_number) for p in pos):
                    pos.append({"id": None, "po_number": j.po_number, "vendor": j.vendor, "status": j.status})
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == row.sales_order_number).first() if row.sales_order_number else None
        order_status, order_ref = (order.status, order.customer_po_number) if order else (None, None)
        if order:
            for vendor, po in (order.purchase_orders or {}).items():
                if not any(str(p["po_number"]) == str(po) for p in pos):
                    pos.append({"id": None, "po_number": str(po), "vendor": vendor, "status": order.status})
        ships = []
        for po in pos:
            for s in db.query(ShipmentDB).filter(ShipmentDB.reference == f"PO {po['po_number']}").all():
                ships.append({"id": s.id, "reference": s.reference, "mode": s.mode, "forwarder": s.forwarder, "status": s.status, "eta": s.eta_current or s.eta_original})
        thread = rfq is not None and db.query(ItemUpdateDB).filter(ItemUpdateDB.item_type == "rfq", ItemUpdateDB.item_ref == rfq.number).count() > 0
        drafts = []
        if True:
            for a in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT").order_by(AssistantActionDB.created_at.desc()).limit(500):
                args = a.args or {}
                if (rfq is not None and args.get("_rfq_id") == rfq.id) or (rfq is not None and args.get("rfq_number") == rfq.number) or args.get("_job_id") == row.id:
                    from app.services.assistant import agent as _agent
                    from app.services.coordination import workdays as _wd

                    live = a.status != "PENDING" or _wd.as_utc(a.created_at) >= _wd.now() - _agent._ttl(a)      # a draft past its time is no longer in the inbox: not "waiting" here either
                    drafts.append({"id": a.id, "kind": args.get("_play"), "status": a.status if live else "EXPIRED", "summary": a.summary})
        research = [{"id": n.id, "title": n.title, "url": n.url, "note": n.note, "source": n.source, "at": _iso(n.created_at)}
                    for n in db.query(JobResearchDB).filter(JobResearchDB.job_id == row.id).order_by(JobResearchDB.id)]
        design_files = [{"id": f.id, "name": f.filename, "size": f.size, "design": bool(f.design), "at": _iso(f.created_at), "url": f"/api/v1/files/{f.id}"}
                        for f in db.query(ItemFileDB).filter(ItemFileDB.item_type == "rfq", ItemFileDB.item_ref == rfq.number,
                                                             ItemFileDB.deleted_at.is_(None)).order_by(ItemFileDB.id)] if rfq else []
        doc_rows = db.query(JobDocumentDB.kind, JobDocumentDB.fields, JobDocumentDB.confirmed_by, JobDocumentDB.source_name).filter(JobDocumentDB.job_id == row.id).order_by(JobDocumentDB.id).all()
        docs = [(k, dict(f or {})) for k, f, _c, _s in doc_rows]
        checked = [(k, dict(f or {})) for k, f, c, s in doc_rows if c or s == "entered by hand"]        # read by OCR and not yet checked by a person: not used for warnings
        printed = {f["sales_order_number"] for k, f in docs if k == "allied_so" and f.get("sales_order_number")}
        so_confirmed = bool(printed) and db.query(OrderDB).filter(OrderDB.sales_order_number.in_(printed)).count() > 0
    return {"rfq": rfq_d, "purchase_orders": pos, "shipments": ships, "thread": thread, "drafts": drafts, "research": research, "docs": docs, "checked": checked, "so_confirmed": so_confirmed,
            "design_files": design_files, "order_status": order_status, "order_ref": order_ref}


def _stage(row: JobDB, p: Dict[str, Any]) -> Tuple[str, List[str]]:
    """What is done, worked out from what exists: records in the app and the documents in the job's packets. A stage is done when its evidence
    is there; the current stage is the furthest done. Nothing is assumed from a blank."""
    kinds = {k for k, _ in p["docs"]}
    so_in_docs = bool(p.get("so_confirmed"))              # a printed number counts only when it is a real order: a misread digit proves nothing
    rfq_status = (p["rfq"] or {}).get("status") or ""
    numbered = any(po.get("po_number") for po in p["purchase_orders"] if po.get("status") != "CANCELLED")
    ok = {
        "RFQ": bool(p["rfq"] or row.sales_order_number or kinds),
        "RESEARCH": bool(p["research"]),
        "ENGINEERING": bool(p["thread"] or row.design_approved_at or "drawing_notes" in kinds),
        "DESIGN_APPROVED": bool(row.design_approved_at),
        "VENDOR_QUOTE": "vendor_quote" in kinds,
        "CUSTOMER_QUOTED": "allied_quote" in kinds or rfq_status in ("QUOTE_SENT", "WON"),
        "CUSTOMER_ORDERED": "customer_po" in kinds or bool(row.sales_order_number) or rfq_status == "WON",
        "PO_ISSUED": numbered or "allied_po" in kinds,
        "SO_CREATED": bool(row.sales_order_number) or so_in_docs,
        "SHIPPED": bool(p["shipments"]) or str(p["order_status"] or "").upper() == "SHIPPED",
        "CLOSED": bool(row.closed_at),
    }
    done = [s for s in STAGES if ok[s]]
    return (done[-1] if done else "RFQ"), done


def stage_history(row: JobDB, p: Dict[str, Any], done: List[str]) -> List[Dict[str, Any]]:
    """Every stage with when it was reached and by whom, read from the records and documents that prove it (never typed in), and the plain
    reason the first unfinished stage is stuck, when something says why. A stage with no dated evidence has entered_at null."""
    kinds_at: Dict[str, Tuple[Optional[datetime], Optional[str]]] = {}
    when: Dict[str, Tuple[Optional[datetime], Optional[str]]] = {}
    with SessionLocal() as db:
        for kind, at, by in (db.query(JobDocumentDB.kind, JobDocumentDB.created_at, JobDocumentDB.created_by)
                             .filter(JobDocumentDB.job_id == row.id).order_by(JobDocumentDB.created_at, JobDocumentDB.id)):
            kinds_at.setdefault(kind, (at, by))          # the earliest document of each kind, with the person who made THAT one
        rfq = db.get(RfqDB, row.rfq_id) if row.rfq_id else None
        when["RFQ"] = (rfq.received_at if rfq else row.created_at, row.created_by)
        r = db.query(JobResearchDB.created_at, JobResearchDB.created_by).filter(JobResearchDB.job_id == row.id).order_by(JobResearchDB.created_at, JobResearchDB.id).first()
        when["RESEARCH"] = (r[0], r[1]) if r and r[0] else (None, None)
        if rfq is not None:
            u = db.query(ItemUpdateDB).filter(ItemUpdateDB.item_type == "rfq", ItemUpdateDB.item_ref == rfq.number).order_by(ItemUpdateDB.id).first()
            when["ENGINEERING"] = (u.created_at, u.author_email) if u else (None, None)
        when["DESIGN_APPROVED"] = (row.design_approved_at, row.design_approved_by)
        when["VENDOR_QUOTE"] = kinds_at.get("vendor_quote", (None, None))
        quoted = kinds_at.get("allied_quote", (None, None))
        when["CUSTOMER_QUOTED"] = quoted if quoted[0] else ((rfq.quoted_at, None) if rfq is not None and rfq.quoted_at else (None, None))
        when["CUSTOMER_ORDERED"] = kinds_at.get("customer_po", (None, None))
        issued = db.query(PurchaseOrderDB.approved_at, PurchaseOrderDB.approved_by).filter(
            PurchaseOrderDB.job_id == row.id, PurchaseOrderDB.status != "CANCELLED", PurchaseOrderDB.po_number.isnot(None),
            PurchaseOrderDB.approved_at.isnot(None)).order_by(PurchaseOrderDB.approved_at, PurchaseOrderDB.id).first()
        when["PO_ISSUED"] = (issued[0], issued[1]) if issued and issued[0] else kinds_at.get("allied_po", (None, None))
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == row.sales_order_number).first() if row.sales_order_number else None
        when["SO_CREATED"] = (order.created_at, order.created_by) if order else kinds_at.get("allied_so", (None, None))
        refs = [f"PO {po['po_number']}" for po in p["purchase_orders"] if po.get("po_number")]
        ship = db.query(func.min(ShipmentDB.created_at)).filter(ShipmentDB.reference.in_(refs)).scalar() if refs else None
        when["SHIPPED"] = (ship, None)
        when["CLOSED"] = (row.closed_at, None)
    waiting_on = (ball.get("rfq", str(row.rfq_id)) or {}).get("ball_with") if row.rfq_id else None
    pending = [d for d in p["drafts"] if d["status"] == "PENDING"]
    draft_po = any(po.get("status") == "DRAFT" for po in p["purchase_orders"])
    d = set(done)
    reasons = {
        "VENDOR_QUOTE": "Waiting for the vendor's price." if waiting_on == "vendor" else None,
        "PO_ISSUED": ("The design needs a manager's approval before a purchase order can be approved." if "VENDOR_QUOTE" in d and "DESIGN_APPROVED" not in d
                      else "The purchase order is drafted and waiting for approval." if draft_po and "DESIGN_APPROVED" in d else None),
        "SO_CREATED": "The customer's order is in, but the sales order has not been made." if "CUSTOMER_ORDERED" in d else None,
        "SHIPPED": "Made and waiting to ship." if "SO_CREATED" in d else None,
        "CUSTOMER_ORDERED": "Waiting for the customer's order." if "CUSTOMER_QUOTED" in d and waiting_on == "customer" else None,
    }
    first_open = next((st for st in STAGES if st not in d and st != "CLOSED"), None)
    out = []
    for st in STAGES:
        at, by = when.get(st, (None, None))
        reason = None
        if st == first_open:
            reason = reasons.get(st) or (f"A draft is waiting for a reviewer: {pending[0]['summary']}" if pending else None)                 or (f"Waiting for {waiting_on}." if waiting_on and waiting_on not in ("us", "") else None)
        out.append({"stage": st, "stage_word": status_words.word("job_stage", st), "done": st in d, "entered_at": _iso(at) if st in d or at else None, "by": by if (st in d or at) else None, "blocked_reason": reason})
    return out


def _next_steps(row: JobDB, p: Dict[str, Any], done: List[str]) -> List[Dict[str, str]]:
    """What a person would do next on this job, in plain words, from what is and is not there (at most three)."""
    d = set(done)
    out: List[Dict[str, str]] = []
    if "VENDOR_QUOTE" not in d and "PO_ISSUED" not in d and "SO_CREATED" not in d:
        out.append({"key": "ask_vendor", "text": "Draft the CAD drawings and ask the vendor for a price."})
    if "VENDOR_QUOTE" in d and "PO_ISSUED" not in d:
        out.append({"key": "approve_design" if "DESIGN_APPROVED" not in d else "approve_po",
                    "text": ("The vendor's price is in. When you agree it, approve the design, then the purchase order." if "DESIGN_APPROVED" not in d
                             else "The vendor's price is in and the design is approved: approve the purchase order.")})
    if "CUSTOMER_ORDERED" in d and "SO_CREATED" not in d:
        out.append({"key": "make_sales_order", "text": "The customer's order is in: make the sales order."})
    if "SO_CREATED" in d and "SHIPPED" not in d:
        out.append({"key": "ship", "text": "The order is made and waiting to ship."})
    return out[:3]


def _amount(fields: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    from app.services import order_documents

    n, cur = order_documents.money(fields.get("total")) if fields.get("total") else (None, None)
    if not fields.get("currency") and re.search("[¥￥]", str(fields.get("total") or "")):
        cur = "?"                                              # a yen sign fits yen and yuan: never compared
    return n, (fields.get("currency") or cur)


def _cur(c: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """A currency as a code. Nothing printed: `default`. A symbol that fits many currencies (the yen sign) or odd text: "?" (unknown, never compared)."""
    if not c:
        return default
    t = str(c).strip().upper()
    return {"$": "USD", "US$": "USD", "RMB": "CNY"}.get(t, t if re.fullmatch(r"[A-Z]{3}", t) else "?")


def job_checks(p: Dict[str, Any], today: Optional[Any] = None) -> List[Dict[str, str]]:
    """Things worth a second look, worked out from the job's CHECKED documents (confirmed by a person, or entered by hand). Only printed or entered
    facts are compared; a missing figure or an unstated vendor currency means no check, never a guess. Allied sells in USD unless a quote says
    otherwise. Each check is {key, level: warn|info, text}; none blocks anything."""
    from zoneinfo import ZoneInfo

    from app.config import settings
    from app.services.coordination import packets

    today = today or datetime.now(ZoneInfo(settings.BUSINESS_TZ)).date()
    docs = []
    for entry in (p.get("checked") if "checked" in p else p.get("docs")) or []:       # odd shapes (None, strings, non-dict facts) are skipped, never raised on
        try:
            k, f = entry
        except (TypeError, ValueError):
            continue
        if isinstance(f, dict):
            docs.append((k, f))
    out: List[Dict[str, str]] = []
    vq = [f for k, f in docs if k == "vendor_quote"]
    aq = [f for k, f in docs if k == "allied_quote"]
    cp = [f for k, f in docs if k == "customer_po"]
    pos = [po for po in p.get("purchase_orders") or [] if isinstance(po, dict) and po.get("po_number") and po.get("status") != "CANCELLED"]
    ships = [sh for sh in p.get("shipments") or [] if isinstance(sh, dict)]
    for kind, label, over in (("vendor_quote", "vendor", bool(pos)), ("allied_quote", "Allied", bool(cp))):
        if over:
            continue                                         # the PO is out (or the customer has ordered): an old quote's date no longer matters
        mine_docs = [f for k, f in docs if k == kind]
        latest: Dict[str, Dict[str, Any]] = {}
        for _i, f in sorted(enumerate(mine_docs), key=lambda t: (packets.iso_date(str(t[1].get("date") or "")) or "9999-12-31", t[0])):      # an undated quote is the latest one (scan order breaks ties)
            latest[canon_vendor(f.get("vendor") or "") if kind == "vendor_quote" else ""] = f          # only the newest quote of each vendor can still run out
        for f in latest.values():
            raw = str(f.get("valid_until") or "")
            iso = packets.iso_date(raw)
            if not iso:
                if raw.strip():
                    out.append({"key": f"{kind}_date_unread", "level": "info", "text": f"The {label} quotation's valid-until date \"{raw.strip()[:30]}\" could not be read as a date (month/day/year or 2026-01-31). Check it on the page."})
                continue
            m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*", raw)
            if m and int(m.group(1)) <= 12 and int(m.group(2)) <= 12 and m.group(1) != m.group(2):
                out.append({"key": f"{kind}_date_unclear", "level": "info",
                            "text": f"The {label} quotation's valid-until date {raw.strip()} was read as month/day ({iso}); it could be day/month. Check it on the page."})
            if iso < today.isoformat():
                who = f" from {f['vendor']}" if f.get("vendor") else ""
                out.append({"key": f"{kind}_expired", "level": "warn", "text": f"The {label} quotation{who} ran out on {iso}. Ask for a fresh price before relying on it."})
            elif (datetime.fromisoformat(iso).date() - today).days <= 7:
                out.append({"key": f"{kind}_expiring", "level": "info", "text": f"The {label} quotation runs out on {iso}."})
    q_total, q_cur = next((_amount(f) for f in reversed(aq) if f.get("total")), (None, None))      # the newest quote
    c_total, c_cur = next((_amount(f) for f in reversed(cp) if f.get("total")), (None, None))
    q_cur, c_cur = _cur(q_cur, "USD"), _cur(c_cur, "USD")           # Allied sells in dollars unless a document says otherwise
    if q_total is not None and c_total is not None and q_cur == c_cur and q_cur != "?" and abs(q_total - c_total) >= 0.005:
        out.append({"key": "customer_po_differs", "level": "warn",
                    "text": f"The customer's order total ({c_total:,.2f}) is not the Allied quote total ({q_total:,.2f}). Tax or freight may explain it. Check before making the sales order."})
    sell, sell_cur = (c_total, c_cur) if c_total is not None else (q_total, q_cur)
    issued = {canon_vendor(po.get("vendor") or "") for po in pos}
    newest: Dict[str, Dict[str, Any]] = {}
    ordered = sorted(enumerate(vq), key=lambda p: (packets.iso_date(str(p[1].get("date") or "")) or "9999-12-31", p[0]))
    for _i, f in ordered:                                     # one price per vendor: the latest-dated quote replaces older ones (scan order breaks ties)
        if f.get("total"):
            newest[canon_vendor(f.get("vendor") or "")] = f
    issued = {v for v in issued if v}
    mine = [f for v, f in newest.items() if v in issued] if issued else list(newest.values())
    costs = [(n, _cur(c)) for n, c in (_amount(f) for f in mine) if n is not None]
    if issued and len(mine) < len(issued):
        costs = []                                            # an issued vendor has no price on file: nothing to compare, and the losing vendors' prices are not it
    if sell and costs:
        if any(c is None or c == "?" or c != sell_cur for _, c in costs) or sell_cur == "?":
            out.append({"key": "margin_currency", "level": "info", "text": "The vendor's price has no clear currency, or it differs from the customer's, so the margin was not checked."})
        else:
            cost = sum(n for n, _ in costs) if issued else min(n for n, _ in costs)
            pct = (sell - cost) / sell * 100
            floor = float(getattr(settings, "MIN_MARGIN_PCT", 0.0) or 0.0)
            if pct < max(floor, 0.0):
                what = f"Selling at {sell:,.2f} against a vendor price of {cost:,.2f} is a loss of {-pct:.1f}%." if pct < 0 else f"The margin is {pct:.1f}%, under the {floor:g}% you set."
                out.append({"key": "margin_low", "level": "warn", "text": what + " Check before approving the purchase order."})
    everything = [po for po in p.get("purchase_orders") or [] if isinstance(po, dict) and po.get("po_number")]
    if everything and all(po.get("status") == "CANCELLED" for po in everything):
        out.append({"key": "all_pos_cancelled", "level": "info", "text": "Every purchase order on this job was cancelled. Nothing is waiting on a vendor."})
    for po in pos:
        overseas = bool(po.get("incoterm"))                  # an Incoterm on the PO is what says the vendor is overseas
        if overseas and po.get("lines_missing_overseas"):
            out.append({"key": "overseas_details_missing", "level": "info",
                        "text": f"Purchase order {po.get('po_number')} is for an overseas vendor but some lines have no HTS code or country of origin yet. Customs will ask for them."})
        if po.get("revision_mismatch"):
            lines = ", ".join(str(n) for n in po["revision_mismatch"])
            out.append({"key": "revision_mismatch", "level": "warn",
                        "text": f"The vendor on purchase order {po.get('po_number')} acknowledged a different drawing revision from the one sent (line {lines}). Confirm which one they are making."})
        if po.get("approved_by_creator"):
            out.append({"key": "self_approved", "level": "warn",
                        "text": f"Purchase order {po.get('po_number')} was approved by the same person who drafted it. A second person should look it over."})
        if overseas:
            out.append({"key": "tariff_reminder", "level": "info", "text": f"Duty rates change. Confirm the duty on purchase order {po.get('po_number')} with your customs broker before it ships."})
        if str(po.get("status") or "").upper() in ("SENT", "ACKNOWLEDGED") and po.get("cert_lines"):
            out.append({"key": "certificate_expected", "level": "info",
                        "text": f"Purchase order {po.get('po_number')} asks for a certificate on some lines. Check it arrived before the job ships."})
    departed = any(str(sh.get("status") or "").upper() not in ("", "BOOKED", "CARGO_READY", "GATE_IN") for sh in ships)
    if departed or str(p.get("order_status") or "").upper() == "SHIPPED":      # the job reads as shipped (something has left, or the order says so): loose ends are info only
        for po in pos:
            if str(po.get("status") or "").upper() in ("APPROVED", "SENDING"):
                out.append({"key": "po_not_sent", "level": "info", "text": f"This job reads as shipped, but purchase order {po.get('po_number')} to {po.get('vendor') or 'the vendor'} was never marked sent."})
        for sh in ships:
            if str(sh.get("status") or "").upper() == "BOOKED":
                out.append({"key": "shipment_still_booked", "level": "info", "text": "This job reads as shipped, but a shipment is still only booked. Update its status when it leaves."})
    return out


def _reviewer_name() -> str:
    """Who a waiting draft waits on, by name (the first reviewer), never the word "reviewer"."""
    try:
        from app.services.coordination import notify
        from app.services.coordination.autopilot import reviewers

        people = reviewers()
        first = people[0] if people else ""
        name = (notify.contacts().get(first.lower()) or {}).get("name") or first
        return name + (f" and {len(people) - 1} more" if len(people) > 1 else "") if name else "a reviewer"
    except Exception:  # noqa: BLE001 - a name is a nicety
        return "a reviewer"


def _view(row: JobDB, full: bool = True, staff: bool = True) -> Dict[str, Any]:
    p = _parts(row)
    stage, done = _stage(row, p)
    b = ball.get("rfq", str(row.rfq_id)) if row.rfq_id else None
    waiting = [{"what": d["summary"], "who": _reviewer_name(), "action_id": d["id"]} for d in p["drafts"] if d["status"] == "PENDING"]
    if b and b.get("ball_with"):
        waiting.append({"what": b.get("note") or "waiting", "who": b.get("ball_with"), "since": b.get("since")})
    out = {"id": row.id, "ref": row.ref, "title": row.title, "customer": row.customer, "stage": stage, "stage_word": status_words.word("job_stage", stage), "done": done, "waiting": waiting,
           "next_steps": _next_steps(row, p, done), "checks": job_checks(p),
           "ball": b, "updated_at": _iso(row.closed_at or row.design_approved_at or row.created_at)}
    if full:
        out["stages"] = stage_history(row, p, done)
        so = ({"number": row.sales_order_number if staff else None, "customer_reference": p["order_ref"]}
              if row.sales_order_number else None)      # our own sales order number is internal: staff see it, nobody else
        out.update({"rfq": p["rfq"], "research": p["research"], "thread": {"entity": "rfq", "id": row.rfq_id} if row.rfq_id else None,
                    "design": {"approved_by": row.design_approved_by, "approved_at": _iso(row.design_approved_at), "files": p["design_files"]},
                    "purchase_orders": p["purchase_orders"], "sales_order": so, "shipments": p["shipments"], "drafts": p["drafts"],
                    "documents": _documents(row.id, staff)})
    return out


def _documents(job_id: int, staff: bool) -> Dict[str, Any]:
    from app.services.coordination import packets

    docs = packets.for_job(job_id, staff)
    return {"items": docs, "found": packets.found(docs)}


def get(job: Any, staff: bool = True) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = _find(db, job)
        db.expunge(row)
    return _view(row, staff=staff)


def list_jobs(stage: str = "", owner: str = "", q: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Jobs, newest first. The text filter and the paging happen in the database; only the rows on the page are worked out in full, unless
    the stage or owner filter needs every row's state."""
    if stage and stage.upper() not in STAGES:
        raise JobError(f"Unknown stage '{stage[:24]}'. The stages are: {', '.join(STAGES)}.")
    with SessionLocal() as db:
        query = db.query(JobDB)
        if q:
            like = f"%{q.strip().casefold()}%"
            query = query.filter(func.lower(JobDB.ref + " " + JobDB.title + " " + func.coalesce(JobDB.customer, "")).like(like))
        if not stage and not owner:
            total = query.count()
            rows = query.order_by(JobDB.id.desc()).offset(offset).limit(limit).all()
            db.expunge_all()
            return {"items": [_view(r, full=False) for r in rows], "total": total}
        rows = query.order_by(JobDB.id.desc()).all()
        db.expunge_all()
    items = [_view(r, full=False) for r in rows]
    if stage:
        items = [i for i in items if i["stage"] == stage.upper()]
    if owner:
        items = [i for i in items if owner in ((i["ball"] or {}).get("owner"), (i["ball"] or {}).get("owner_email"))]
    return {"items": items[offset:offset + limit], "total": len(items)}


def add_research(job: Any, title: str, url: str, note: str, user: Dict[str, Any], source: str = "manual") -> Dict[str, Any]:
    title = (title or "").strip()[:500]
    if not title and not (note or "").strip():
        raise JobError("Give the research note a title or some text.")
    with SessionLocal() as db:
        row = _find(db, job)
        db.add(JobResearchDB(job_id=row.id, title=title, url=(url or "")[:1000] or None, note=(note or "")[:4000] or None, source=source,
                             created_by=user.get("email", "system")))
        db.commit()
        jid = row.id
    return get(jid)


def search_research(job: Any, query: str, user: Dict[str, Any], max_results: int = 5) -> Dict[str, Any]:
    """Scans the web for the query (free search) and saves the top results as research notes on the job."""
    from app.services import lead_agent

    query = (query or "").strip()
    if len(query) < 3:
        raise JobError("Say what to look up.")
    if not lead_agent.HAS_DDGS:
        raise JobError("Web search isn't installed on this server (it needs the duckduckgo-search package).")
    try:
        found = lead_agent.DDGS().text(query, max_results=max(1, min(int(max_results), 10))) or []
    except Exception as exc:  # noqa: BLE001 - said plainly, never replaced with made-up results
        raise JobError(f"The web search didn't work just now: {exc}") from exc
    if not found:
        raise JobError("The search found nothing. Try different words, or add a note by hand.")
    with SessionLocal() as db:
        row = _find(db, job)
        for f in found:
            db.add(JobResearchDB(job_id=row.id, title=str(f.get("title") or "")[:500], url=str(f.get("href") or "")[:1000] or None,
                                 note=str(f.get("body") or "")[:4000] or None, source="search", created_by=user.get("email", "system")))
        db.commit()
        jid = row.id
    return get(jid)


def po_for_vendor(sales_order_number: str, vendor_name: str) -> Optional[str]:
    """When the customer's order arrives, the vendor keeps the PO number it already has for this job."""
    with SessionLocal() as db:
        job = db.query(JobDB).filter(JobDB.sales_order_number == str(sales_order_number)).first()
        if job is None:
            return None
        want = (vendor_name or "").strip().casefold()
        for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == job.id, PurchaseOrderDB.po_number.isnot(None), PurchaseOrderDB.status != "CANCELLED"):
            if _same_vendor(r.vendor, vendor_name):
                return str(r.po_number)
    return None


def _clean_line(value: Any, limit: int = 100) -> str:
    """One line of text: control and text-direction characters gone, spaces collapsed, cut at `limit`."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+", " ", str(value if value is not None else "")).split())[:limit]


def _plain_number(value: Any) -> Optional[Any]:
    """A plain finite decimal number written with ASCII digits (12, 12.5, 3772.91), or None: not '$1,000.00', 'ten', a list or 'NaN'."""
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool) or value is None or isinstance(value, (list, dict, tuple)):
        return None
    text = str(value).strip()
    if not text or not text.isascii() or not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def create_sales_order(job: Any, data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Allied's internal sales order for this job, from the customer's own reference and lines. Vendors that already have a PO on
    this job keep that PO number on the order (no second PO). One at a time, so two clicks make one order."""
    from app.core.exceptions import EntityNotFoundError
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    with _so_lock:
        j = get(job)
        if j["sales_order"]:
            raise JobError("This job already has a sales order.")
        ref = _clean_line(data.get("customer_reference"))
        address = "\n".join(_clean_line(p, 200) for p in str(data.get("shipping_address") or "").splitlines() if p.strip())
        lines = [ln for ln in (data.get("lines") or []) if isinstance(ln, dict) and (ln.get("description") or ln.get("raw_item_text"))]
        if not ref or not lines:
            raise JobError("Give the customer's own reference and at least one line (description, quantity, unit price).")
        if len(str(data.get("customer_reference") or "").strip()) > 100:
            raise JobError("The customer's reference is too long (at most 100 characters).")
        if len(address) > 500 or len(lines) > 100:
            raise JobError("The ship-to address is too long (at most 500 characters) or there are more than 100 lines.")
        items = []
        for n, ln in enumerate(lines, 1):
            qty, price = _plain_number(ln.get("quantity", 1)), _plain_number(ln.get("unit_price"))
            if qty is None or qty <= 0 or qty > 1_000_000 or qty != qty.to_integral_value():
                raise JobError(f"Line {n}: the quantity must be a whole number of pieces above zero, like 2.")
            if price is None:
                raise JobError(f"Line {n}: give the unit price as a plain number like 3772.91 (no $ or commas).")
            if price < 0 or price > 10_000_000:
                raise JobError(f"Line {n}: the unit price must be between 0 and 10,000,000.")
            if price != price.quantize(price.__class__("0.01")):
                raise JobError(f"Line {n}: the unit price must be to the cent (two decimals at most), like 3772.91.")
            items.append({"raw_item_text": _clean_line(ln.get("raw_item_text") or ln["description"], 300), "quantity": str(qty), "unit_price": str(price)})
        try:
            req = InboundOrderRequest(customer_name=j["customer"] or "", customer_po_number=ref, shipping_address=address, line_items=items, job=j["ref"])
        except ValueError as exc:                 # a pydantic complaint is told in plain words, never as "ValidationError"
            raise JobError("The sales order could not be read: check the customer's reference, the address and every line's quantity and price.") from exc
        try:
            result = OrderOrchestrator().process_inbound_order(req, actor_email=user.get("email", "system"))
        except EntityNotFoundError as exc:
            q = (getattr(exc, "details", None) or {}).get("query") or ""
            close = catalog_search(q, 3)
            hint = ("; closest part names: " + ", ".join(f"{c['item_id']} ({c['description']})" for c in close)) if close else ""
            raise JobError(f"'{q}' isn't a part in our catalog yet{hint}. Pick one of those names, or ask for the part to be added.") from exc
        except JobError:
            raise
        except Exception as exc:  # noqa: BLE001 - the details go to the log, never to the screen
            logger.exception(f"Sales order for {j['ref']} failed: {exc}")
            raise JobError("The sales order could not be created. Check the customer's reference and the lines, then try again. If it keeps failing, the details are in the server log.") from exc
        so = str(result.sales_order_number)
        link_order(j["id"], so)
        _keep_job_pos(j["id"], so)
        return get(j["id"])


def _keep_job_pos(job_id: int, so: str) -> None:
    from app.core.container import get_entity_matcher

    vendors = get_entity_matcher().vendors_cache
    with SessionLocal() as db:
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == so).first()
        if order is None:
            return
        pos = dict(order.purchase_orders or {})
        for code, po in list(pos.items()):
            name = (vendors.get(code) or {}).get("vendor_name", code)
            keep = po_for_vendor(so, name)
            if keep:
                pos[code] = keep
        order.purchase_orders = pos
        db.commit()


def sales_order_sheet(job: Any) -> bytes:
    """Allied's Sales Order Information sheet. Filled from what the app already knows (dates, totals, vendors); anything it does not know
    stays blank, exactly as on the paper form, and nobody is asked to fill it in."""
    from app.models.db_models import ShipmentDB
    from app.services import order_documents

    j = get(job)
    if not j["sales_order"]:
        raise JobError("This job has no sales order yet.")
    with SessionLocal() as db:
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == j["sales_order"]["number"]).first()
        if order is None:
            raise LookupError("The sales order is missing.")
        lines = [ln for ln in (order.line_items or []) if isinstance(ln, dict)]
        vendors = []
        for p in j["purchase_orders"]:
            rec = db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == j["id"], PurchaseOrderDB.po_number == p["po_number"]).first()
            line = db.query(VendorJobDB).filter(VendorJobDB.po_number == p["po_number"]).first()
            ship = db.query(ShipmentDB).filter(ShipmentDB.reference == f"PO {p['po_number']}").first()
            total = 0.0
            for ln in (rec.lines if rec else []) or []:
                try:
                    total += float(ln.get("amount") or 0)
                except (TypeError, ValueError):
                    pass
            vendors.append({"po_number": p["po_number"], "vendor": p["vendor"],
                            "po_date": rec.approved_at.strftime("%m/%d/%y") if rec and rec.approved_at else (line.ordered_on if line else None),
                            "drawing_approval_date": j["design"]["approved_at"][:10] if j["design"]["approved_at"] else None,
                            "po_total": total or None, "ship_date": line.promised_on if line else None,
                            "arrival_date": (ship.eta_current or ship.eta_original) if ship else None})
        one = len(lines) == 1
        info = {"customer_name": order.customer_name, "customer_reference": order.customer_po_number, "date": order.po_date, "ship_to": order.shipping_address,
                "quantity": str(lines[0].get("quantity", "")) if one else "",
                "description": ("; ".join(f"{ln.get('quantity', '')} x {ln.get('raw_item_text') or ln.get('description') or ''}" for ln in lines) if not one
                                else str(lines[0].get("raw_item_text") or lines[0].get("description") or "")),
                "sales_order_number": order.sales_order_number, "so_total": order.total_revenue, "vendors": vendors}
    return order_documents.sales_order_sheet_pdf(info)


def catalog_search(q: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Catalog parts whose id or description looks like the text (for a pick-list)."""
    import difflib

    from app.core.container import get_entity_matcher

    t = (q or "").strip().casefold()
    rows = []
    for item_id, rec in get_entity_matcher().items_cache.items():
        desc = str(rec.get("description") or rec.get("item_description") or rec.get("name") or "")
        hay = f"{item_id} {desc}".casefold()
        score = 1.0 if t and t in hay else difflib.SequenceMatcher(None, t, hay).ratio()
        rows.append((score, {"item_id": item_id, "description": desc}))
    rows.sort(key=lambda x: -x[0])
    return [r for _, r in rows[:max(1, min(int(limit), 25))]]


_CORPORATE = {"ltd", "limited", "llc", "inc", "incorporated", "co", "corp", "corporation", "company", "gmbh", "sa", "lp", "llp", "pty", "plc",
              "有限公司", "公司", "有限责任公司"}


_LOOKALIKE = {c: l for c, l in zip("авеікмнорстухѕјԁһԛԝӏαεορικνυ",
                                   "abeikmhopctyxsjdhqwlaeopikvu")}      # Cyrillic and Greek letters that look like Latin ones: one name written either way
_GREEK_CAPITALS = str.maketrans("ΑΒΕΖΗΙΚΜΝΟΡΤΧΥ", "ABEZHIKMNOPTXY")     # read as capitals before case folding (capital Ν is an N, small ν is a v)


def norm_name(name: str) -> str:
    """A business name reduced for comparing: accents folded, case folded, punctuation gone, "Ltd/LLC/Inc/Co" dropped ("KWS LTD.", "kws ltd" and
    "KWS Ltd" are one name). Chinese and other letters are kept, so a name written only in Chinese is not reduced to nothing."""
    import unicodedata

    t = "".join(_LOOKALIKE.get(ch, ch) for ch in unicodedata.normalize("NFKD", (name or "").translate(_GREEK_CAPITALS).casefold()))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"\b([^\W\d_])\.", r"\1", t)            # "K.W.S." is "KWS" (a single letter followed by a dot; "4.5" is not an abbreviation)
    words = re.sub(r"[\W_]+", " ", t).split()
    while len(words) > 1 and words[-1] in _CORPORATE:  # "Ltd", "LLC", "Co" only count at the end, and never empty a name
        words.pop()
    if words and words[-1] not in _CORPORATE:         # a Chinese ending written without a space ("淄博陶瓷有限公司", "股份有限公司", traditional 責) is the ending too, if a name is left
        tail = words[-1].replace("責", "责")
        for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份公司", "公司"):
            if tail.endswith(suffix) and len(tail) > len(suffix):
                words[-1] = tail[: -len(suffix)]
                break
    return " ".join(words)


_canon_cache: Dict[str, Any] = {"key": None, "map": {}}


def _canon_map() -> Dict[str, str]:
    """{cleaned name or id: the vendor list's full cleaned name}, built once per vendor list (it is rebuilt only when the list changes). Names win
    over ids, so a short id that equals another vendor's name can never take that vendor over."""
    try:
        from app.core.container import get_entity_matcher

        recs = get_entity_matcher().vendors_cache
    except Exception:  # noqa: BLE001 - no vendor list: no aliases
        return {}
    key = (id(recs), len(recs))
    if _canon_cache["key"] != key:
        out: Dict[str, str] = {}
        for rec in recs.values():
            full = norm_name(str(rec.get("vendor_name") or ""))
            if full:
                out[full] = full
        for rec in recs.values():
            full = norm_name(str(rec.get("vendor_name") or ""))
            vid = norm_name(str(rec.get("vendor_id") or ""))
            if full and vid and vid not in out:
                out[vid] = full
        _canon_cache.update(key=key, map=out)
    return _canon_cache["map"]


def canon_vendor(name: str) -> str:
    """A vendor's one name: the cleaned name, and when the vendor list knows the vendor by its short id or full name, the list's full name (so
    "ZIBO" and "Zibo Supereal Industrial Ceramic Co. Ltd" are one vendor, while "Shanghai Steel" and "Shanghai Metal" never are)."""
    n = norm_name(name)
    return _canon_map().get(n, n) if n else ""


def _same_vendor(a: str, b: str) -> bool:
    ca, cb = canon_vendor(a), canon_vendor(b)
    return bool(ca) and ca == cb


def same_customer(a: str, b: str) -> bool:
    """The same customer: equal names, or one is whole words of the other and at least six letters ("Owens Corning" matches "Owens Corning
    Insulating Systems"; "ACME" does not match "ACME Industrial")."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(short.replace(" ", "")) >= 6 and set(short.split()) <= set(long.split())


def _open_job_pos(db, customer_name: str, vendor_names: List[str]) -> List[Dict[str, Any]]:
    """Numbered POs on jobs that have no sales order yet, for these vendors; with the job's customer and RFQ number."""
    out = []
    cust = (customer_name or "").strip().casefold()
    for job in db.query(JobDB).filter(JobDB.sales_order_number.is_(None), JobDB.closed_at.is_(None)).all():
        rfq = db.get(RfqDB, job.rfq_id) if job.rfq_id else None
        for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == job.id, PurchaseOrderDB.po_number.isnot(None), PurchaseOrderDB.status != "CANCELLED"):
            if any(_same_vendor(r.vendor, v) for v in vendor_names):
                out.append({"job_id": job.id, "job": job.ref, "vendor": r.vendor, "po_number": str(r.po_number), "rfq": rfq.number if rfq else None,
                            "customer_ok": same_customer(customer_name, job.customer or "")})
    return out


def existing_pos(customer_name: str, vendor_names: List[str]) -> List[Dict[str, Any]]:
    """For the New-order check: vendors that already have a PO on an open job of this customer."""
    with SessionLocal() as db:
        return [{"vendor": c["vendor"], "po_number": c["po_number"], "job": c["job"]} for c in _open_job_pos(db, customer_name, vendor_names) if c["customer_ok"]]


def match_for_order(customer_name: str, vendor_names: List[str], texts: List[str], job_ref: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The job an arriving order belongs to: the one named, else the one whose RFQ number the order mentions, else the only open job of
    this customer with a PO to these vendors. When it is not clear (none, or several), nothing is guessed; a person picks."""
    with SessionLocal() as db:
        if job_ref:
            row = _find(db, job_ref)
            if row.sales_order_number:
                raise JobError(f"{row.ref} already has a sales order.")
            return {"id": row.id, "ref": row.ref}
        cands = _open_job_pos(db, customer_name, vendor_names)
        blob = " ".join(t for t in texts if t).casefold()
        by_rfq = {c["job_id"] for c in cands if c["rfq"] and c["rfq"].casefold() in blob}
        ids = by_rfq or {c["job_id"] for c in cands if c["customer_ok"]}
        if len(ids) != 1:
            return None
        row = db.get(JobDB, next(iter(ids)))
        return {"id": row.id, "ref": row.ref}


def po_number_on_job(job_id: int, vendor_name: str) -> Optional[str]:
    with SessionLocal() as db:
        for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == job_id, PurchaseOrderDB.po_number.isnot(None), PurchaseOrderDB.status != "CANCELLED"):
            if _same_vendor(r.vendor, vendor_name):
                return str(r.po_number)
    return None


def link_existing_order(job: Any, sales_order_number: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Ties an order that is already made to the job. A vendor PO on the order that was not sent yet takes the job's number; one
    that was already sent is listed as a duplicate for a person to sort out with the vendor."""
    from app.services.outbound_email_service import outbound_email_service

    so = str(sales_order_number or "").strip()
    _so_lock.acquire()
    try:
        return _link_existing_order(job, so, outbound_email_service)
    finally:
        _so_lock.release()


def _link_existing_order(job: Any, so: str, outbound_email_service) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = _find(db, job)
        if row.sales_order_number:
            raise JobError(f"{row.ref} already has a sales order.")
        other = db.query(JobDB).filter(JobDB.sales_order_number == so).first()
        if other is not None:
            raise JobError(f"That order already belongs to {other.ref}.")
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == so).first()
        if order is None:
            raise LookupError(f"No order {so}.")
        job_id = row.id
    sent = set(outbound_email_service.really_sent("PURCHASE_ORDER"))      # from the send ledger, not the in-memory outbox
    with SessionLocal() as db:       # the PO records know exactly which were sent, however long ago
        sent |= {str(r.po_number) for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.status.in_(("SENT", "ACKNOWLEDGED")),
                                                                              PurchaseOrderDB.po_number.isnot(None)).all()}
    from app.core.container import get_entity_matcher

    vendors = get_entity_matcher().vendors_cache
    duplicates = []
    with SessionLocal() as db:
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == so).first()
        pos = dict(order.purchase_orders or {})
        for code, po in list(pos.items()):
            keep = po_number_on_job(job_id, (vendors.get(code) or {}).get("vendor_name", code))
            if keep and keep != str(po):
                if str(po) in sent:
                    duplicates.append({"vendor": (vendors.get(code) or {}).get("vendor_name", code), "order_po": str(po), "job_po": keep})
                else:
                    pos[code] = keep
        order.purchase_orders = pos
        db.commit()
    link_order(job_id, so)
    out = get(job_id)
    out["duplicates"] = duplicates
    return out


def _clean_text(v: Any, n: int = 64) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()[:n]


def record_vendor_quote(job: Any, vendor: str, fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """The vendor's quotation is in. Recorded as ONE document per vendor on the job (a second entry updates it), with only the facts given and
    checked: a total must be a real, non-negative number; a date must be a date. It then makes the PO DRAFT for that vendor (no number, nothing
    sent) and tells the managers, so the next step is a person's agreement. A vendor the RFQ never asked is accepted, with a warning."""
    import uuid

    from app.services import order_documents
    from app.services.coordination import notify, packets, purchase_orders

    vendor = _clean_text(vendor, 120)
    if not vendor:
        raise JobError("Say which vendor the quotation is from.")
    keep: Dict[str, str] = {}
    for k in ("quote_order_number", "date", "total", "lead_time", "valid_until", "currency"):
        v = _clean_text((fields or {}).get(k))
        if not v:
            continue
        if k == "currency":
            if not re.fullmatch(r"[A-Za-z]{3}", v):
                raise JobError("The currency is a three-letter code like USD or CNY.")
            keep["currency"] = v.upper()
            continue
        if k == "total":
            n, cur = order_documents.money(v)
            if n is None or n < 0:
                raise JobError("The total must be a number that is not negative, written like 1200.00 or 1,200.00 (a total written 1.234,50 is ambiguous: write it 1234.50).")
            v = f"{n:,.2f}"
            if cur:
                keep["currency"] = cur
        elif k in ("date", "valid_until"):
            if not packets.iso_date(v):
                raise JobError("The date was not understood. Use a date like 2026-09-01 or 9/1/26.")
            v = packets.iso_date(v)
        keep[k] = v
    warnings: List[str] = []
    _quote_lock.acquire()
    try:
        return _record_quote(job, vendor, keep, warnings, user)
    finally:
        _quote_lock.release()


def _record_quote(job: Any, vendor: str, keep: Dict[str, str], warnings: List[str], user: Dict[str, Any]) -> Dict[str, Any]:
    import uuid

    from app.services.coordination import notify, purchase_orders

    with SessionLocal() as db:
        row = _find(db, job)
        jid, ref, title, rfq_id = row.id, row.ref, row.title, row.rfq_id
        rfq = db.get(RfqDB, rfq_id) if rfq_id else None
        asked = [n.strip() for n in ((rfq.vendor if rfq else "") or "").split(",") if n.strip()]
        if asked and not any(_same_vendor(vendor, a) for a in asked):
            warnings.append(f"{vendor} is not one of the vendors this RFQ was sent to ({', '.join(asked)}).")
        existing = next((d for d in db.query(JobDocumentDB).filter(JobDocumentDB.job_id == jid, JobDocumentDB.kind == "vendor_quote").all()
                         if same_vendor_name(dict(d.fields or {}).get("vendor", ""), vendor)), None)
        if existing is not None:
            existing.fields = {**dict(existing.fields or {}), **keep, "vendor": vendor}
            warnings.append("A quotation from this vendor was already recorded; it was updated, not added twice.")
        else:
            db.add(JobDocumentDB(job_id=jid, kind="vendor_quote", label=f"Vendor quotation: {vendor}", page_from=1, page_to=1, fields={"vendor": vendor, **keep},
                                 source_name="entered by hand", source_sha=f"manual-{uuid.uuid4().hex}", created_by=user.get("email", "system")))
        db.commit()
    po = purchase_orders.create(jid, vendor, user)
    notify.notify("vendor_quote_in", f"vendor-quote:{jid}:{canon_vendor(vendor)}:{keep.get('total', '')}:{keep.get('date', '')}", f"{vendor}'s price is in for {ref}",
                  f"{vendor} quoted on {title}. When you agree the price: approve the design, then approve the purchase order (a draft is ready on the job)."
                  + (f" Note: {warnings[0]}" if warnings else ""), include_leadership=True, link=f"/jobs/{ref}")
    out = get(jid)
    out["purchase_order_draft"] = po["id"]
    out["warnings"] = warnings
    return out


def same_vendor_name(a: str, b: str) -> bool:
    return _same_vendor(a, b)

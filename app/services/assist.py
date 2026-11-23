"""Less typing: forms that start filled (prefill), "make another like this" (duplicate template), a proposed record from pasted text (create from text), a safe bulk edit with a dry run, and each person's own
remembered choices. Everything here only READS or proposes; the only writes are a person's own defaults (a small JSON file) and, on an explicit apply, owner/tag changes that already exist one record at a time."""
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB, PurchaseOrderDB, RfqDB, ShipmentDB

PREFILL_KINDS = ("order", "po", "rfq", "shipment")


class AssistError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _v(value: Any, source: str) -> Optional[Dict[str, Any]]:
    return {"value": value, "source": source} if value not in (None, "") else None


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v}


# ------------------------------------------------------------------ prefill

def prefill(kind: str, customer: str = "", vendor: str = "", job: str = "") -> Dict[str, Any]:
    """Likely values for a new form, each with where it came from (the customer's master record, their last order, the vendor's last PO). Only what is known comes back; nothing is guessed."""
    if kind not in PREFILL_KINDS:
        raise AssistError(422, f"The kind must be one of: {', '.join(PREFILL_KINDS)}.")
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    cust, vend = " ".join(str(customer or "").split()), " ".join(str(vendor or "").split())
    if cust:
        from app.services import extract

        master = next((r for r in extract._customers().values() if str(r.get("customer_name") or "").strip().casefold() == cust.casefold()), None)
        if master:
            out["customer_id"] = _v(master.get("customer_id"), "customer master list")
            out["shipping_address"] = _v(master.get("shipping_address"), "customer master list")
            out["billing_address"] = _v(master.get("billing_address"), "customer master list")
            out["payment_terms"] = _v(master.get("payment_terms"), "customer master list")
            out["contact_email"] = _v(master.get("email"), "customer master list")
            out["contact_name"] = _v(master.get("contact_name"), "customer master list")
        with SessionLocal() as db:
            last = db.query(OrderDB).filter(OrderDB.customer_name == cust).order_by(OrderDB.id.desc()).first()
            if last is not None:
                out["shipping_address"] = _v(last.shipping_address, f"last order {last.customer_po_number}") or out.get("shipping_address")
                out["payment_terms"] = _v(last.payment_terms, f"last order {last.customer_po_number}") or out.get("payment_terms")
                out["currency"] = _v(last.currency, f"last order {last.customer_po_number}")
                out["owner"] = _v(last.created_by, f"who made the last order {last.customer_po_number}")
            rfq = db.query(RfqDB).filter(RfqDB.customer == cust).order_by(RfqDB.id.desc()).first()
            if rfq is not None:
                out["contact_email"] = out.get("contact_email") or _v(rfq.contact_email, f"last RFQ {rfq.number}")
                out["owner"] = out.get("owner") or _v(rfq.owner_email, f"owner of the last RFQ {rfq.number}")
                if getattr(rfq, "quote_currency", None):
                    out["currency"] = out.get("currency") or _v(rfq.quote_currency, f"last RFQ {rfq.number}")
    if vend:
        from app.core.container import get_entity_matcher

        vm = next((r for r in (get_entity_matcher().vendors_cache or {}).values() if str(r.get("vendor_name") or "").strip().casefold() == vend.casefold() or str(r.get("vendor_id") or "").casefold() == vend.casefold()), None)
        if vm:
            out["vendor_email"] = _v(vm.get("email"), "vendor master list")
            out["payment_terms"] = out.get("payment_terms") if kind != "po" else (_v(vm.get("payment_terms"), "vendor master list") or out.get("payment_terms"))
        with SessionLocal() as db:
            po = db.query(PurchaseOrderDB).filter(PurchaseOrderDB.vendor == vend).order_by(PurchaseOrderDB.id.desc()).first()
            if po is not None:
                ref = f"last PO {po.po_number or po.id}"
                out["vendor_email"] = _v(po.vendor_email, ref) or out.get("vendor_email")
                if kind == "po":
                    out["payment_terms"] = _v(po.terms, ref) or out.get("payment_terms")
                out["ship_via"] = _v(po.ship_via, ref)
                out["incoterm"] = _v(po.incoterm, ref)
                out["incoterm_place"] = _v(po.incoterm_place, ref)
            sh = db.query(ShipmentDB).filter(ShipmentDB.forwarder.isnot(None)).order_by(ShipmentDB.id.desc()).first()
            if sh is not None and kind == "shipment":
                out["forwarder"] = _v(sh.forwarder, f"last shipment {sh.reference or sh.id}")
                out["mode"] = _v(sh.mode, f"last shipment {sh.reference or sh.id}")
    return {"kind": kind, "values": _clean(out), "note": "Suggestions from Allied's own records. Check them before you save."}


# ------------------------------------------------------------------ duplicate template

_DROP = {"id", "order_id", "number", "ref", "po_number", "sales_order_number", "customer_po_number", "status", "created_at", "updated_at", "received_at", "ack_due_at", "due_at", "acknowledged_at", "sent_at",
         "approved_at", "approved_by", "action_id", "cancelled_at", "cancel_reason", "history", "packing_slip_path", "sage_exported_at", "sage_po_exported_at", "monday_item_id", "monday_board_id", "email_id",
         "last_update_at", "container_no", "awb", "reference", "eta_original", "eta_current", "etd", "free_time_ends", "po_date", "revision_marks", "total_revenue", "total_expense", "gross_margin_pct", "summary"}


def duplicate_template(kind: str, ref: str) -> Dict[str, Any]:
    """A record's fields without its numbers, dates and status: the body for "make another like this". Nothing is saved."""
    if kind not in PREFILL_KINDS:
        raise AssistError(422, f"The kind must be one of: {', '.join(PREFILL_KINDS)}.")
    model, key = {"order": (OrderDB, "customer_po_number"), "po": (PurchaseOrderDB, "po_number"), "rfq": (RfqDB, "number"), "shipment": (ShipmentDB, "reference")}[kind]
    with SessionLocal() as db:
        q = db.query(model)
        row = (q.filter(model.id == int(ref)).first() if str(ref).isdigit() and kind in ("po", "shipment") else q.filter(getattr(model, key) == ref).first()) or (q.filter(model.id == int(ref)).first() if str(ref).isdigit() else None)
        if row is None:
            raise AssistError(404, "That record was not found.")
        data = {c.name: getattr(row, c.name) for c in model.__table__.columns if c.name not in _DROP and not c.name.startswith("_")}
    body = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in data.items() if v not in (None, "", [], {})}
    return {"kind": kind, "from": ref, "body": body, "left_out": "numbers, dates, status and anything that identifies the old record", "note": "Check it, change what differs, then save."}


# ------------------------------------------------------------------ create from text

def _detect(text: str) -> str:
    t = " ".join(text.lower().split())[:20000]                                    # one space between words and a bounded length: no pattern below can be made to crawl
    if re.search(r"\b(rfq|request for (?:a )?quote|please quote|quotation request|can you quote|quote for)\b", t):
        return "rfq"
    if re.search(r"\b(container|tracking|bol|bill of lading|eta|awb|booking)\b", t) and not re.search(r"\bpurchase order\b", t):
        return "shipment"
    if re.search(r"\b(purchase order|p\.?o\.?[ #:]{0,3}(?:no\.?|number)?[ #:]{0,3}[a-z0-9\-]{4,})", t):
        return "order"
    return ""


def create_from_text(text: str, kind: str = "", sender: str = "") -> Dict[str, Any]:
    """Detects what the text is (RFQ, customer order, shipment) and returns the proposed body for POST /create/{kind}. Nothing is saved."""
    from app.services import extract

    t = str(text or "").strip()
    if len(t) < 10:
        raise AssistError(422, "Paste the text of the email or note.")
    k = kind or _detect(t)
    if k not in ("rfq", "order", "shipment"):
        return {"kind": None, "message": "I could not tell whether this is an RFQ, a customer order or a shipment. Choose one.", "options": ["rfq", "order", "shipment"], "proposed_body": None, "fields": {}, "needs_check": [], "create_url": None}
    if k == "shipment":
        b = extract.Builder("shipment", t)
        c = re.search(r"\b([A-Z]{4}\d{7})\b", t)
        if c:
            b.put("container_no", c.group(1), 0.9)
        e = re.search(r"(?i)\beta\b[\s:]*" + extract._DATE, t)
        if e:
            b.put("eta", e.group(1), 0.9)
        f = re.search(r"(?i)\b(?:forwarder|carrier)[\s:]+([A-Z][A-Za-z0-9 &.\-]{2,40})", t)
        if f:
            b.put("forwarder", f.group(1).strip(), 0.7)
        bk = re.search(r"(?i)\bbooking\s*(?:no\.?|#|number)?[\s:]*([A-Z0-9\-]{5,20})", t)
        if bk:
            b.put("reference", bk.group(1), 0.9)
        out = b.done()
    elif k == "rfq":
        out = extract.rfq_from_email(text=t, sender=sender)
    else:
        out = extract.order_from_email(text=t, sender=sender)
    f = out["fields"]
    val = {n: v["value"] for n, v in f.items()}
    if k == "rfq":
        body = _clean({"customer": val.get("customer"), "subject": val.get("subject") or t.splitlines()[0][:120], "contact_email": val.get("contact_email"), "items": val.get("items"), "due_date": val.get("due_date")})
    elif k == "order":
        body = _clean({"customer": val.get("customer"), "customer_po_number": val.get("customer_po_number"), "items": val.get("items"), "total": val.get("total"), "payment_terms": val.get("payment_terms"), "due_date": val.get("due_date")})
    else:
        body = _clean({n: val.get(n) for n in ("reference", "container_no", "eta", "forwarder")})
    return {"kind": k, "proposed_body": body, "fields": f, "warnings": out["warnings"], "needs_check": out["needs_check"], "extraction_id": out["extraction_id"], "ai_used": out["ai_used"],
            "create_url": f"/api/v1/create/{k}", "message": "This is a proposal. Nothing has been saved; check it, then save."}


# ------------------------------------------------------------------ bulk edit

BULK_MAX = 100
BULK_KINDS = ("job", "order", "rfq", "po", "shipment", "customer", "vendor")


def _exists(kind: str, ref: str) -> bool:
    """Is there such a record? (A bulk change never makes a tag or an owner row for something that is not there.)"""
    from app.models.db_models import JobDB

    ref = str(ref)
    with SessionLocal() as db:
        if kind == "job":
            return db.query(JobDB).filter((JobDB.ref == ref) | (JobDB.id == (int(ref) if ref.isdigit() else -1))).first() is not None
        if kind == "order":
            return db.query(OrderDB).filter((OrderDB.customer_po_number == ref) | (OrderDB.sales_order_number == ref) | (OrderDB.order_id == ref)).first() is not None
        if kind == "rfq":
            return db.query(RfqDB).filter(RfqDB.number == ref).first() is not None
        if kind == "po":
            return db.query(PurchaseOrderDB).filter((PurchaseOrderDB.po_number == ref) | (PurchaseOrderDB.id == (int(ref) if ref.isdigit() else -1))).first() is not None
        if kind == "shipment":
            return db.query(ShipmentDB).filter((ShipmentDB.reference == ref) | (ShipmentDB.id == (int(ref) if ref.isdigit() else -1))).first() is not None
    if kind == "customer":
        from app.services import extract

        return any(ref in (str(r.get("customer_id")), str(r.get("customer_name"))) for r in extract._customers().values())
    if kind == "vendor":
        from app.core.container import get_entity_matcher

        return any(ref in (str(r.get("vendor_id")), str(r.get("vendor_name"))) for r in (get_entity_matcher().vendors_cache or {}).values())
    return False


def _tag_list(value: Any, what: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise AssistError(422, f"{what} must be a list of words, for example [\"urgent\"].")
    tags = [" ".join(t.split()) for t in value if t.strip()]
    if len(tags) > 10 or any(len(t) > 40 for t in tags):
        raise AssistError(422, f"{what}: at most 10 tags per change, each up to 40 characters.")
    return tags


def bulk(kind: str, refs: List[str], changes: Dict[str, Any], user: Dict[str, Any], dry_run: bool = True) -> Dict[str, Any]:
    """Owner, tags and who has the ball for many records at once, only. dry_run=True (the default) says what would change per record and changes nothing. Money, status and anything outward are not here.
    changes: {owner?, ball_with?, add_tags?[], remove_tags?[]}. Tags: any staff. Owner and ball: managers."""
    from app.services import organize

    allowed = {"owner", "ball_with", "add_tags", "remove_tags"}
    extra = set(changes or {}) - allowed
    if extra:
        raise AssistError(422, f"Only these can be changed in bulk: {', '.join(sorted(allowed))}. Not: {', '.join(sorted(extra))}.")
    if not changes:
        raise AssistError(422, "Say what to change.")
    if ("owner" in changes or "ball_with" in changes) and not dry_run and str(user.get("role", "")).upper() not in ("MANAGER", "ADMIN"):
        raise AssistError(403, "Only a manager changes owners or who has the ball for many records at once.")
    if kind not in BULK_KINDS:
        raise AssistError(422, f"Bulk changes are for: {', '.join(BULK_KINDS)}.")
    add_tags, remove_tags = _tag_list(changes.get("add_tags"), "add_tags"), _tag_list(changes.get("remove_tags"), "remove_tags")
    if "owner" in changes and str(changes["owner"] or "").strip():
        from app.services import nudges

        if not nudges._recipient(str(changes["owner"]).strip().lower()):
            raise AssistError(422, "An owner must be an Allied staff member: give their email address.")
    if "ball_with" in changes and (not isinstance(changes["ball_with"], str) or len(changes["ball_with"]) > 80):
        raise AssistError(422, "Who has the ball is a short name (80 characters at most).")
    refs = list(dict.fromkeys(str(r).strip() for r in (refs or []) if str(r).strip()))
    if not refs:
        raise AssistError(422, "Choose at least one record.")
    if len(refs) > BULK_MAX:
        raise AssistError(422, f"At most {BULK_MAX} records at a time.")
    try:
        organize._kind(kind)
    except organize.OrganizeError as exc:
        raise AssistError(exc.status, exc.message)
    manager = str(user.get("role", "")).upper() in ("MANAGER", "ADMIN")
    if ("owner" in changes or "ball_with" in changes) and not manager and not dry_run:
        raise AssistError(403, "Only a manager changes owners or who has the ball for many records at once.")
    rows, done = [], 0
    for ref in refs:
        if not _exists(kind, ref):
            rows.append({"ref": ref, "status": "not found: no such record, nothing changed", "changes": []})
            continue
        cur = organize.get_owner(kind, ref) or {}
        tags = organize.tags_for(kind, ref)
        would: List[str] = []
        if "owner" in changes:
            would.append(f"owner: {cur.get('owner') or 'none'} -> {changes['owner'] or 'none'}")
        if "ball_with" in changes:
            would.append(f"ball with: {cur.get('ball_with') or 'none'} -> {changes['ball_with'] or 'none'}")
        for t in add_tags:
            would.append(f"add tag {t}" if str(t).casefold() not in tags else f"tag {t} already there")
        for t in remove_tags:
            would.append(f"remove tag {t}" if str(t).casefold() in tags else f"tag {t} not on it")
        status = "would change"
        if not dry_run:
            try:
                if "owner" in changes or "ball_with" in changes:
                    organize.set_owner(kind, ref, {"owner": changes.get("owner", cur.get("owner")), "ball_with": changes.get("ball_with", cur.get("ball_with")), "note": cur.get("note")}, user)
                for t in add_tags:
                    organize.add_tag(kind, ref, t, user)
                for t in remove_tags:
                    organize.remove_tag(kind, ref, t)
                status = "changed"
                done += 1
            except organize.OrganizeError as exc:
                status = f"not changed: {exc.message}"
        rows.append({"ref": ref, "status": status, "changes": would})
    return {"kind": kind, "dry_run": bool(dry_run), "records": len(refs), "changed": done, "rows": rows,
            "message": ("Preview only: nothing was changed." if dry_run else f"{done} of {len(refs)} records changed.")}


# ------------------------------------------------------------------ each person's remembered choices

DEFAULT_KEYS = ("last_customer", "last_vendor", "currency", "forwarder", "owner", "view", "last_kind")


def _path() -> Path:
    return Path(settings.DB_DIR) / "user_defaults.json"


_defaults_lock = threading.RLock()
_last_good_defaults: Dict[str, Any] = {}


def _all(strict: bool = False) -> Dict[str, Any]:
    """Everyone's remembered choices. A damaged file is never read as "nobody has any" by a save: the last good copy is used (the damaged file is kept as .bak), and with none the save is refused."""
    try:
        raw = _path().read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        d = json.loads(raw)
        if not isinstance(d, dict):
            raise ValueError("not a dictionary")
        _last_good_defaults.clear()
        _last_good_defaults.update(d)
        return d
    except ValueError:
        if _last_good_defaults:
            try:
                _path().with_name(_path().name + ".bak").write_text(raw, encoding="utf-8")
            except OSError:
                pass
            return json.loads(json.dumps(_last_good_defaults))
        if strict:
            raise AssistError(409, "The file of remembered choices is damaged, so nothing was saved. Ask whoever looks after the server.")
        return {}


def get_defaults(user: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (_all().get((user.get("email") or "").strip().lower()) or {}).items() if k in DEFAULT_KEYS}


def put_defaults(user: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    bad = set(values or {}) - set(DEFAULT_KEYS)
    if bad:
        raise AssistError(422, f"These can be remembered: {', '.join(DEFAULT_KEYS)}. Not: {', '.join(sorted(bad))}.")
    email = (user.get("email") or "").strip().lower()
    if not email:
        raise AssistError(403, "Sign in first.")
    clean: Dict[str, str] = {}
    for k, v in (values or {}).items():
        v = " ".join(str(v if v is not None else "").split())
        if len(v) > 120:
            raise AssistError(422, f"{k} is too long to remember (120 characters at most).")
        clean[k] = v
    with _defaults_lock:                                                       # read, change and swap as one step: simultaneous saves by different people all survive
        data = _all(strict=True)
        mine = dict(data.get(email) or {})
        for k, v in clean.items():
            if v:
                mine[k] = v
            else:
                mine.pop(k, None)
        data[email] = mine
        folder = _path().parent
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".defaults.", suffix=".tmp", dir=str(folder))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
            os.replace(tmp, _path())
            _last_good_defaults.clear()
            _last_good_defaults.update(data)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        return {k: v for k, v in mine.items() if k in DEFAULT_KEYS}

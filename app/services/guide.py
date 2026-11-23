"""What a non-technical person needs to know: what needs attention (Control room), what the Secretary start page shows, what this person may do (/me/can), a plain-language task finder, the glossary, and a
typed request routed to the right draft tool. Everything here only READS (counts and lists) or makes DRAFTS that wait for a person; nothing is sent, paid or written to monday or Sage."""
import json
import os
import re
import tempfile
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import SessionLocal

MANAGER_ROLES = ("MANAGER", "ADMIN")
STAFF_ROLES = ("OPERATOR", "MANAGER", "ADMIN")


def _role(user: Dict[str, Any]) -> str:
    return str(user.get("role", "")).upper()


def is_manager(user: Dict[str, Any]) -> bool:
    return _role(user) in MANAGER_ROLES


def is_staff(user: Dict[str, Any]) -> bool:
    return _role(user) in STAFF_ROLES


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken source never blanks the page
        return default


# ------------------------------------------------------------------ Control room

def control_attention(user: Dict[str, Any]) -> Dict[str, Any]:
    """One call for the Control room: {quiet_shipments, payments_due, gates_waiting, drawings_unverified, drafts_waiting, backups:{state,text}}. Each list entry is {count, items[<=10]}. Money (payments_due) is
    for managers only: others get null."""
    from app.models.db_models import EngineerGateDB, RedactedArtifactDB
    from app.services import backups, my_prefs, payments
    from app.services.coordination import shipments

    def quiet():
        rows = [s for s in shipments.list_shipments(include_finished=False) if s.get("quiet_flag")]
        return {"count": len(rows), "items": [{"id": s["id"], "reference": s.get("reference") or s.get("container_no"), "quiet_days": s.get("quiet_days"), "quiet_flag": s["quiet_flag"], "owner_name": s.get("owner_name")} for s in rows[:10]]}

    def gates_waiting():
        with SessionLocal() as db:
            rows = db.query(EngineerGateDB).filter(EngineerGateDB.status == "PENDING").order_by(EngineerGateDB.id).all()
            return {"count": len(rows), "items": [{"id": g.id, "job_id": g.job_id, "gate": g.gate, "since": g.requested_at.isoformat() if g.requested_at else None} for g in rows[:10]]}

    def drawings():
        with SessionLocal() as db:
            rows = db.query(RedactedArtifactDB).filter(RedactedArtifactDB.status.in_(("PENDING", "CLEAN", "BLOCKED"))).order_by(RedactedArtifactDB.id).all()
            return {"count": len(rows), "items": [{"id": a.id, "job_id": a.job_id, "status": a.status, "file_id": a.source_file_id} for a in rows[:10]]}

    def due():
        rows = payments.due_list()
        return {"count": len(rows), "items": [{"tranche_id": r["id"], "po_id": r["po_id"], "vendor": r["vendor"], "amount": r["amount"], "label": r["label"]} for r in rows[:10]]}

    empty = {"count": 0, "items": []}
    b = _safe(backups.plain_text, {"state": "unknown", "text": "The backup state could not be read."})
    return {"quiet_shipments": _safe(quiet, empty), "payments_due": _safe(due, empty) if is_manager(user) else None, "gates_waiting": _safe(gates_waiting, empty),
            "drawings_unverified": _safe(drawings, empty), "drafts_waiting": int(_safe(lambda: my_prefs.counts(user)["drafts_waiting"], 0)), "backups": {"state": b.get("state"), "text": b.get("text")}}


# ------------------------------------------------------------------ Secretary start page

def secretary_summary(user: Dict[str, Any]) -> Dict[str, Any]:
    """{mail_needing_reply, drafts_waiting, order_emails, bills_to_check, owed_to_us, late_orders}: counts with a few lines each. Money lines (bills, owed to us) are for managers; others get null."""
    from app.models.db_models import SageBillDB, SageInvoiceDB
    from app.services import mail_log, my_prefs
    from app.services.coordination import vendors

    email = (user.get("email") or "").strip().lower()

    def mail():
        if not email:                                                       # an account with no address of its own sees nobody's mail, never everybody's
            return {"count": 0, "items": []}
        rows = mail_log.list_mail(needs_action=True, days=60, limit=200, mine=email)["mail"]
        return {"count": len(rows), "items": [{"id": r.get("id"), "from": r.get("sender"), "subject": r.get("subject")} for r in rows[:5]]}

    def orders():
        rows = mail_log.list_mail(category="order", days=7, limit=200)["mail"]
        return {"count": len(rows), "items": [{"id": r.get("id"), "from": r.get("sender"), "subject": r.get("subject")} for r in rows[:5]]}

    def bills():
        with SessionLocal() as db:
            rows = [b for b in db.query(SageBillDB).order_by(SageBillDB.due_date).all() if b.amount_due is not None and Decimal(str(b.amount_due)) > 0]
            return {"count": len(rows), "total": f"{sum((Decimal(str(b.amount_due)) for b in rows), Decimal('0')):.2f}", "note": "As of the last Sage import.",
                    "items": [{"vendor": b.vendor_name, "bill": b.bill_number, "due": b.due_date, "amount_due": f"{Decimal(str(b.amount_due)):.2f}"} for b in rows[:5]]}

    def owed():
        with SessionLocal() as db:
            rows = [i for i in db.query(SageInvoiceDB).order_by(SageInvoiceDB.due_date).all() if i.amount_due is not None and Decimal(str(i.amount_due)) > 0]
            return {"count": len(rows), "total": f"{sum((Decimal(str(i.amount_due)) for i in rows), Decimal('0')):.2f}", "note": "As of the last Sage import.",
                    "items": [{"customer": i.customer_name, "invoice": i.invoice_number, "due": i.due_date, "amount_due": f"{Decimal(str(i.amount_due)):.2f}"} for i in rows[:5]]}

    def late():
        data = vendors.overview()
        rows = [j for j in data["jobs"] if j.get("state") in ("late", "will_miss")]
        return {"count": len(rows), "items": [{"id": j["id"], "vendor": j["vendor"], "po_number": j.get("po_number"), "text": j.get("text")} for j in rows[:5]]}

    empty = {"count": 0, "items": []}
    return {"mail_needing_reply": _safe(mail, empty), "drafts_waiting": int(_safe(lambda: my_prefs.counts(user)["drafts_waiting"], 0)), "order_emails": _safe(orders, empty),
            "bills_to_check": _safe(bills, empty) if is_manager(user) else None, "owed_to_us": _safe(owed, empty) if is_manager(user) else None, "late_orders": _safe(late, empty)}


# ------------------------------------------------------------------ what this person may do

def me_can(user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import gates, nudges, redaction

    email = (user.get("email") or "").strip().lower()
    mgr, staff = is_manager(user), is_staff(user)
    approvers = gates.approvers()
    flags = {
        "can_send_nudges": nudges.may_send(user), "can_approve_gates": bool(email and email in approvers), "can_override_gates": mgr, "can_record_payments": mgr, "can_make_orders": staff,
        "can_send_mail_drafts": staff, "can_import_sage": mgr, "can_undo_sage_import": mgr, "can_verify_redaction": bool(redaction.may_verify(user)), "can_see_money": mgr,
    }
    why = {
        "can_send_nudges": "Only the people the owner named, and admins, can send nudges.",
        "can_approve_gates": "Only the engineers on the approver list can approve a gate." if approvers else "Nobody can approve a gate yet: the owner has to set the approver list first.",
        "can_override_gates": "Only a manager can let a step go ahead without its gate, and must give a reason.",
        "can_record_payments": "Only a manager records vendor payments.",
        "can_make_orders": "Making orders needs a staff sign-in, not a view-only one.",
        "can_send_mail_drafts": "Sending mail drafts needs a staff sign-in, not a view-only one.",
        "can_import_sage": "Only a manager brings a Sage file in.",
        "can_undo_sage_import": "Only a manager undoes a Sage import.",
        "can_verify_redaction": "Only an engineer or a manager confirms a drawing is free of the customer's details.",
        "can_see_money": "Supplier prices, payments and markups are for managers.",
    }
    return {**flags, "role": _role(user), "reasons": {k: why[k] for k, v in flags.items() if not v}}


# ------------------------------------------------------------------ the task finder

TASKS: List[Dict[str, Any]] = [
    {"id": "record_payment", "title": "Record a payment to a vendor", "why": "A vendor payment is due and you have sent it.", "link": "/boards?board=jobs", "needs": "can_record_payments", "keywords": "record pay payment paid wire vendor deposit tranche remit"},
    {"id": "approve_shop_drawing", "title": "Approve the vendor's shop drawing (Gate 1)", "why": "Production does not start until an engineer approves the drawing.", "link": "/boards?board=jobs", "needs": "can_approve_gates", "keywords": "approve shop drawing gate 1 engineer production start"},
    {"id": "approve_photos", "title": "Approve the measurement photos (Gate 2)", "why": "Crating, the pre-ship payment and pickup wait for these photos.", "link": "/boards?board=jobs", "needs": "can_approve_gates", "keywords": "approve photos measurement gate 2 crating pickup"},
    {"id": "override_gate", "title": "Let a step go ahead without its gate", "why": "A manager can override a gate with a written reason.", "link": "/boards?board=jobs", "needs": "can_override_gates", "keywords": "override skip gate manager reason"},
    {"id": "chase_vendor", "title": "Chase a vendor", "why": "A vendor is late or has not confirmed a ship date.", "link": "/contacts?section=vendors", "needs": "can_send_mail_drafts", "keywords": "chase vendor late follow up ship date check in"},
    {"id": "send_nudge", "title": "Nudge a colleague", "why": "Ask a teammate to do something now, by app, email or text.", "link": "/admin", "needs": "can_send_nudges", "keywords": "nudge pester remind colleague milestone blast"},
    {"id": "reply_to_mail", "title": "Reply to an email", "why": "A customer or vendor wrote and needs an answer.", "link": "/inbox", "needs": "can_send_mail_drafts", "keywords": "reply answer email respond customer mail"},
    {"id": "check_drawing", "title": "Remove the customer's details from a drawing", "why": "A drawing may go to a vendor only after the customer's name is taken off and checked.", "link": "/boards?board=jobs", "needs": "can_send_mail_drafts", "keywords": "redact redaction remove customer details drawing vendor blind clean"},
    {"id": "verify_drawing", "title": "Confirm a cleaned drawing is safe to send", "why": "A person must look at the cleaned copy before a vendor gets it.", "link": "/boards?board=jobs", "needs": "can_verify_redaction", "keywords": "verify confirm cleaned drawing redacted safe"},
    {"id": "bring_in_sage", "title": "Bring in a file from Sage", "why": "Update invoices, payments, bills or open orders from a Sage export.", "link": "/orders?tab=sage", "needs": "can_import_sage", "keywords": "sage import bring in export invoices payments bills open orders"},
    {"id": "undo_sage_import", "title": "Undo a Sage import", "why": "A wrong file was brought in; take back exactly what it saved.", "link": "/orders?tab=sage", "needs": "can_undo_sage_import", "keywords": "undo sage import wrong file remove rows"},
    {"id": "landed_cost", "title": "Work out the landed cost and prices", "why": "Vendor quote plus duty plus freight, with markup choices.", "link": "/toolkit?tab=landed", "needs": "can_see_money", "keywords": "landed cost duty tariff freight markup price quote tiers"},
    {"id": "confirm_delivery", "title": "Confirm a delivery and add the freight", "why": "Freight goes on the sales order after the delivery is confirmed.", "link": "/logistics", "needs": "can_record_payments", "keywords": "confirm delivery delivered freight invoice variance close"},
    {"id": "forwarder_update", "title": "Read the forwarder's weekly update", "why": "Move arrival dates from the forwarder's email.", "link": "/logistics", "needs": "can_make_orders", "keywords": "forwarder weekly update eta arrival dates digest"},
    {"id": "new_order", "title": "Make an order", "why": "Turn a customer's purchase order into an order and vendor POs.", "link": "/orders", "needs": "can_make_orders", "keywords": "make order new order customer po sales order purchase"},
    {"id": "find_record", "title": "Find a job, order, quote or email", "why": "Type a name or number in the search box.", "link": "/help", "needs": None, "keywords": "find search look up job order quote email"},
]


def tasks_for(q: str, user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The tasks that match what was typed (words in the title, reason or keywords), best first, each with whether THIS person may do it."""
    can = me_can(user)
    words = [w for w in re.findall(r"[a-z0-9]+", (q or "").lower()) if len(w) > 1]
    out = []
    for t in TASKS:
        hay = f"{t['title']} {t['why']} {t['keywords']}".lower()
        score = sum(2 if w in t["keywords"].split() else (1 if w in hay else 0) for w in words)
        if words and score == 0:
            continue
        allowed = True if not t["needs"] else bool(can.get(t["needs"]))
        out.append({"id": t["id"], "title": t["title"], "why": t["why"], "link": t["link"], "allowed_for_me": allowed, "not_allowed_because": None if allowed else can["reasons"].get(t["needs"]), "_s": score})
    out.sort(key=lambda r: (-r["_s"], not r["allowed_for_me"], r["title"]))
    for r in out:
        r.pop("_s")
    return out[:12]


# ------------------------------------------------------------------ glossary

DEFAULTS: Dict[str, str] = {
    "PO": "Purchase order: what Allied sends a vendor to order parts, with the price and the date it is wanted.",
    "Customer PO": "The customer's own purchase order to Allied. Its number goes on our packing list so they can match it.",
    "RFQ": "Request for quote: a customer asking Allied what something would cost.",
    "Gate": "A check an engineer must approve before the work goes on. Gate 1 is the vendor's shop drawing; Gate 2 is the measurement photos.",
    "Deposit": "The first payment to a vendor, paid when the PO is issued, before they start.",
    "Redaction": "Removing the customer's details (name, logo, address, their PO numbers) from a drawing before a vendor sees it.",
    "Blind ship": "Shipping so the customer never learns who made the part, and the vendor never learns who the customer is.",
    "Awaiting invoice": "The goods are delivered and Allied is waiting for the vendor's invoice before it can be paid or billed on.",
    "Landed cost": "What a part really costs once it is here: the vendor's price plus duty and freight.",
    "Proforma": "A vendor's advance invoice, sent before shipping so Allied can pay a deposit.",
    "BOL": "Bill of lading: the paper the truck driver signs when the goods are picked up.",
    "ETA": "Estimated arrival: when the goods are expected to get there.",
}


_glossary_lock = threading.RLock()


def _glossary_path() -> Path:
    return Path(settings.DB_DIR) / "app_glossary.json"


def _backup_path() -> Path:
    return _glossary_path().with_name(_glossary_path().name + ".bak")


_last_good: Dict[str, Any] = {}                                                 # the edits as last read or saved cleanly, in memory: what a damaged file is put back from


def _load(path: Path) -> Optional[Dict[str, Any]]:
    """The file's edits keyed by the word in lower case, or None when it is missing; ValueError when it is damaged."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("not a dictionary")
    out: Dict[str, Any] = {}
    for k, v in data.items():
        entry = v if isinstance(v, dict) else {"plain": str(v)}
        out.setdefault(str(k).casefold(), {"term": entry.get("term") or str(k), **entry})
    return out


def _read_overrides(strict: bool = False) -> Dict[str, Any]:
    """The manager's edits. A damaged file is never read as 'no edits' by a save: the last good copy (.bak) is used, the damaged file is kept beside it (.corrupt), and with neither a save is refused."""
    try:
        got = _load(_glossary_path()) or {}
        _last_good.clear()
        _last_good.update(got)
        return got
    except ValueError:
        if _last_good:                                                           # the damaged file is kept beside the file (.bak) for whoever looks after the server, and the last good edits are used
            try:
                _backup_path().write_bytes(_glossary_path().read_bytes())
            except OSError:
                pass
            return dict(_last_good)
        if strict:
            raise ValueError("The glossary file is damaged and there is no good copy to go back to, so nothing was saved. Ask whoever looks after the server.")
        return {}


def glossary() -> List[Dict[str, Any]]:
    over = _read_overrides()
    terms: Dict[str, Dict[str, Any]] = {k.casefold(): {"term": k, "plain": v, "edited": False} for k, v in DEFAULTS.items()}
    for key, v in over.items():
        shown = terms[key]["term"] if key in terms else v.get("term") or key
        terms[key] = {"term": shown, "plain": v.get("plain"), "edited": True}
    return sorted(terms.values(), key=lambda r: r["term"].casefold())


def set_term(term: str, plain: str, user: Dict[str, Any]) -> Dict[str, Any]:
    from app.core.audit import audit_log

    t = " ".join(str(term or "").split())[:60]
    text = " ".join(str(plain or "").split())
    if not t or len(text) < 5 or len(text) > 400:
        raise ValueError("Give the word and a plain explanation of 5 to 400 characters.")
    key = t.casefold()
    with _glossary_lock:                                                       # read, change and swap as one step: two managers saving at once both keep their edit
        over = _read_overrides(strict=True)
        was = (over.get(key) or {}).get("plain") or next((v for k, v in DEFAULTS.items() if k.casefold() == key), None)
        shown = next((k for k in DEFAULTS if k.casefold() == key), None) or (over.get(key) or {}).get("term") or t
        over[key] = {"term": shown, "plain": text, "by": user.get("email")}
        folder = _glossary_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".glossary.", suffix=".tmp", dir=str(folder))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({v["term"]: {k2: v2 for k2, v2 in v.items() if k2 != "term"} | {"term": v["term"]} for v in over.values()}, f, indent=2, ensure_ascii=False)
            os.replace(tmp, _glossary_path())
            _last_good.clear()
            _last_good.update(over)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
    audit_log(action="GLOSSARY_EDITED", resource_type="GLOSSARY", resource_id=shown[:60], actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"{shown}: was \"{(was or '(not defined)')[:200]}\"; now \"{text[:200]}\"")
    return {"term": shown, "plain": text, "edited": True}


# ------------------------------------------------------------------ a typed request to the Secretary

EXAMPLES = ["Reply to Maria about the quote", "Follow up with everyone who owes us something", "Give me my summary for today"]


def secretary_request(prompt: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Routes a typed request to an existing tool and returns DRAFTS ONLY (they wait in the approvals list; nothing is sent). {understood, intent, message, drafts[], lines[]}. No AI is needed: the words are matched
    by rules; when nothing matches it says so and gives examples."""
    from app.services import mail_log, mail_reply, my_day

    text = " ".join(str(prompt or "").split())
    if not text:
        return {"understood": False, "intent": None, "message": "Type what you want, for example: " + EXAMPLES[0] + ".", "drafts": [], "lines": [], "examples": EXAMPLES}
    low = text.lower()
    wants_reply = re.search(r"\b(?:reply|respond|answer|write back)\b", low)
    wants_chase = re.search(r"\b(follow.?up|chase|owe|owes|remind)\b", low)
    if not (wants_reply or wants_chase) and re.search(r"\b(summary|brief|catch me up|what.?s (going on|happening)|today)\b", low):
        b = my_day.brief(user)
        return {"understood": True, "intent": "summary", "message": b.get("headline") or "Here is your day.", "drafts": [], "lines": [l.get("text") if isinstance(l, dict) else str(l) for l in (b.get("lines") or b.get("items") or [])][:20], "examples": EXAMPLES}
    if re.search(r"\b(follow.?up|chase|owe|owes|remind)\b", low):
        c = my_day.chase(user)
        rows = c.get("rows") or c.get("items") or []
        drafts = [{"id": (r.get("draft") or {}).get("id") if isinstance(r.get("draft"), dict) else r.get("draft_id"), "about": r.get("sentence") or r.get("text") or r.get("title")} for r in rows if r.get("draft") or r.get("draft_id")]
        return {"understood": True, "intent": "follow_up", "message": f"{len(rows)} thing{'s' if len(rows) != 1 else ''} to chase; {len(drafts)} follow-up draft{'s' if len(drafts) != 1 else ''} waiting for your OK. Nothing has been sent.", "drafts": drafts,
                "lines": [str(r.get("sentence") or r.get("text") or r.get("title")) for r in rows[:15]], "examples": EXAMPLES}
    m = re.search(r"\b(?:reply|respond|answer|write back)\b(?:\s+to)?\s+(?P<who>[^,.;]{2,60})", low)
    if m:
        who = re.sub(r"\b(about|regarding|re|on)\b.*$", "", m.group("who")).strip()
        found = mail_log.list_mail(q=who, days=60, limit=20, direction_="in")["mail"] if who else []      # only mail that came IN: never a reply to our own outgoing message
        if not found:
            return {"understood": True, "intent": "reply", "message": f"I could not find a recent email from or about \"{who}\". Say the name as it appears in the email.", "drafts": [], "lines": [], "examples": EXAMPLES}
        row = found[0]
        try:
            d = mail_reply.draft_reply(int(row["id"]), user, use_ai=False)
        except Exception as exc:  # noqa: BLE001
            return {"understood": True, "intent": "reply", "message": str(getattr(exc, "message", None) or exc)[:300], "drafts": [], "lines": [], "examples": EXAMPLES}
        return {"understood": True, "intent": "reply", "message": f"A reply to \"{row.get('subject')}\" from {row.get('sender')} is waiting for your OK. Nothing has been sent.", "drafts": [d], "lines": [], "examples": EXAMPLES}
    return {"understood": False, "intent": None, "message": "I could not tell what you want. I can reply to an email, chase people who owe us something, or give you today's summary.", "drafts": [], "lines": [], "examples": EXAMPLES}

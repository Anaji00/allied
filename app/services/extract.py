"""Extract-to-form: text or a file in, FIELDS out for a person to review. Nothing is saved, nothing is sent. Rules first (patterns, master lists, the packet readers that already exist); a field the rules cannot
find is simply absent (blanks stay blank). Each field says how it was found (rule, master data, ai), how sure that is (coarse: exact pattern 0.9, matched to a master list 0.8, AI 0.5), and the page and the line
it came from. Money, PO numbers, dates and quantities are always listed in needs_check: a person confirms them. Works with AI off; AI is only ever used for a gap, only when asked (use_ai), and says so (ai_used).
Every extraction is remembered in the audit trail so that what people later change can be counted per field (extraction_stats)."""
import json
import re
import uuid
from collections import defaultdict
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB, MailLogDB

MAX_TEXT = 20000                                                    # text read per request: long enough for any email, short enough that no pattern can be made to crawl
_BY: ContextVar = ContextVar("extract_by", default="")
CUT_WARNING = "Only the start and the last 5,000 characters were read; the middle was not. Check the original for anything that is missing."


def _cap(text: str, limit: int = 0) -> tuple:
    """(text, was_cut): a text over the limit keeps its START and its last 5,000 characters (where totals and signatures sit); the middle is dropped and the caller says so."""
    limit = limit or MAX_TEXT
    t = text or ""
    if len(t) <= limit:
        return t, False
    return t[: limit - 5000] + "\n[...]\n" + t[-5000:], True


def set_by(email: str) -> None:
    """Who is asking (set by the route): remembered with the extraction, so only that person can report back on it."""
    _BY.set((email or "").strip().lower())


ALWAYS_CHECK = {"items", "weights", "dimensions", "customer_po_number", "po_number", "total", "unit_price", "amount", "quote_number", "quote_order_number", "date", "due_date", "valid_until", "eta", "payment_terms", "lead_time",
                "customer_rfq_number", "quantity", "po_date", "price"}
_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}|\d{1,2} [A-Z][a-z]{2,8}\.? \d{4})"
_LOCAL = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")
_DOMAIN = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")


def _emails(text: str) -> List[str]:
    """Addresses in a text, found by looking for each @ and reading outward (linear in the text: a long run of letters with no @ costs nothing)."""
    t, out, at = (text or "")[:MAX_TEXT], [], 0
    while len(out) < 50:
        at = t.find("@", at)
        if at < 0:
            break
        a = at
        while a > 0 and t[a - 1] in _LOCAL and at - a < 64:
            a -= 1
        b = at + 1
        while b < len(t) and t[b] in _DOMAIN and b - at < 255:
            b += 1
        local, domain = t[a:at], t[at + 1:b].rstrip(".-")
        if local and "." in domain and re.fullmatch(r"[A-Za-z]{2,}", domain.rsplit(".", 1)[-1] or ""):
            out.append(f"{local}@{domain}")
        at = b if b > at else at + 1
    return out


class ExtractError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Builder:
    def __init__(self, kind: str, text: str, page_texts: Optional[List[str]] = None):
        self.kind, self.text, self.pages = kind, text or "", page_texts or [text or ""]
        self.fields: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.ai_used = False
        self.extra_check: set = set()

    def snippet(self, value: str) -> Dict[str, Any]:
        v = str(value)
        for i, page in enumerate(self.pages, start=1):
            at = page.find(v)
            if at >= 0:
                line_start = page.rfind("\n", 0, at) + 1
                line_end = page.find("\n", at)
                return {"page": i, "text": page[line_start: line_end if line_end >= 0 else len(page)].strip()[:160]}
        return {"page": None, "text": None}

    def put(self, name: str, value: Any, confidence: float, method: str = "rule", source: Optional[Dict[str, Any]] = None) -> None:
        if value in (None, "", [], {}):
            return
        if name in self.fields and self.fields[name]["confidence"] >= confidence:
            return
        self.fields[name] = {"value": value, "confidence": round(confidence, 2), "method": method, "source": source or (self.snippet(value) if isinstance(value, str) else {"page": None, "text": None})}

    def done(self) -> Dict[str, Any]:
        check = sorted({n for n, f in self.fields.items() if n in ALWAYS_CHECK or n in self.extra_check or re.fullmatch(r"[$]?\d[\d,]*\.\d{2}", str(f["value"]))})
        out = {"extraction_id": uuid.uuid4().hex, "kind": self.kind, "fields": self.fields, "warnings": self.warnings, "needs_check": check, "ai_used": self.ai_used}
        _remember(out)
        return out


def _remember(out: Dict[str, Any]) -> None:
    try:
        shown = {n: {"value": f["value"] if not isinstance(f["value"], (list, dict)) else json.dumps(f["value"])[:300], "method": f["method"], "confidence": f["confidence"]} for n, f in out["fields"].items()}
        audit_log(action="EXTRACT_SHOWN", resource_type="EXTRACTION", resource_id=out["extraction_id"], actor_email=_BY.get() or "system", detail=json.dumps({"kind": out["kind"], "fields": shown, "ai_used": out["ai_used"]})[:8000])
    except Exception:  # noqa: BLE001 - the log is a convenience
        pass


# ------------------------------------------------------------------ master data

def _customers() -> Dict[str, Dict[str, Any]]:
    from app.core.container import get_entity_matcher

    return get_entity_matcher().customers_cache or {}


def customer_from(text: str, sender: str = "") -> Optional[Dict[str, Any]]:
    """The master customer an email or document is from: the sender's address or its domain, else a name that appears in the text (spelling, spacing and accents ignored). None when unsure."""
    from app.services import organize
    from app.services.redaction import find_runs

    rows = _customers().values()
    s = (sender or "").strip().lower()
    for r in rows:
        if s and str(r.get("email") or "").strip().lower() == s:
            return {"name": r.get("customer_name"), "customer_id": r.get("customer_id"), "how": "the sender's address is on this customer"}
    dom = s.rsplit("@", 1)[-1] if "@" in s else ""
    if dom and dom not in ("gmail.com", "outlook.com", "yahoo.com", "hotmail.com", "icloud.com"):
        for r in rows:
            if str(r.get("email") or "").strip().lower().endswith("@" + dom):
                return {"name": r.get("customer_name"), "customer_id": r.get("customer_id"), "how": f"the sender's domain {dom} is on this customer"}
    words = (text or "").split()
    for r in rows:
        nm = str(r.get("customer_name") or "")
        if len(nm) >= 4 and find_runs(words, [nm]):
            return {"name": nm, "customer_id": r.get("customer_id"), "how": "the name is in the text (not from the sender): check it", "weak": True}
    res = organize.resolve(text[:120]) if text else {}
    if res.get("company"):
        return {"name": res["company"], "customer_id": None, "how": "a known spelling of this customer (not from the sender): check it", "weak": True}
    return None


def _mail(mail_id: Any) -> Dict[str, str]:
    with SessionLocal() as db:
        m = db.get(MailLogDB, int(mail_id)) if str(mail_id).isdigit() else None
        if m is None:
            raise ExtractError(404, "That email was not found.")
        return {"sender": m.sender or "", "subject": m.subject or "", "text": (m.summary or ""), "id": str(m.id)}


def _items_from(text: str) -> List[Dict[str, Any]]:
    """Lines that look like '10 x AR400 liner plate', '2 pcs WEAR-PLATE-12', 'Qty 4 ...'."""
    out = []
    for line in text.splitlines():
        line = line[:300]
        if not re.match(r"%ss*(?:qty[:%ss]*)?%sd{1,6}%ss*(?:x|pcs|pc|ea|each|off)?%ss+[A-Za-z0-9]" % (chr(92),chr(92),chr(92),chr(92),chr(92)), line):
            for im in re.finditer(r"\b(\d{1,6})\s*x\s+([A-Za-z][^.;\r\n@$]{3,60})", line):
                it = {"quantity": im.group(1), "description": im.group(2).strip(" -:,")}
                pm = re.match(r"\s*(?:@|at)\s*\$?([\d,]+\.\d{2})", line[im.end():])
                if pm:
                    it["unit_price"] = pm.group(1).replace(",", "")
                out.append(it)
            continue
        m = re.match(r"\s*(?:qty[:\s]*)?(\d{1,6})\s*(?:x|pcs|pc|ea|each|off)?\s+([A-Za-z0-9][^\n$]{3,100}?)(?:\s+(?:@|at)\s+\$?([\d,]+\.\d{2}))?\s*$", line, re.I)
        if m and not re.match(r"\d{4}-\d{2}-\d{2}", line.strip()):
            item = {"quantity": m.group(1), "description": m.group(2).strip(" -:")}
            if m.group(3):
                item["unit_price"] = m.group(3).replace(",", "")
            out.append(item)
    if not out:                                                          # "please quote 10 x liner plate." written inside a sentence
        for m in re.finditer(r"\b(\d{1,6})\s*x\s+([A-Za-z][^.;\r\n]{3,60})", text):
            out.append({"quantity": m.group(1), "description": m.group(2).strip(" -:,")})
    return out[:30]


# ------------------------------------------------------------------ the extractors

def _common_email(b: Builder, sender: str, subject: str, text: str) -> None:
    def outside(addrs):
        return [a for a in addrs if not re.search(r"@(?:[\w-]+\.)*alliedweldingsystems\.com$", a, re.I)]

    contact, how = "", ""
    from_lines = [m.group(1) for m in re.finditer(r"(?im)^[ >]*from\s*:\s*(.{3,300})$", text or "")]       # a pasted message carries its own header; a forwarded one has several
    for found, label in ((outside(_emails(sender)) if "@" in sender else [], "the sender"),
                         (outside([a for ln in from_lines for a in _emails(ln)]), "the From: line"),
                         (outside(_emails(text)), "an address in the text")):
        if found:
            contact, how = found[0], label
            break
    if contact:
        b.put("contact_email", contact, 0.9 if how != "an address in the text" else 0.6, source={"page": None, "text": how})
    b.extra_check.add("contact_email")                                                                    # an address is always for a person to confirm: it may be a forwarder or a copy
    cust = customer_from(f"{subject}\n{text}", sender)
    if cust and cust.get("name"):
        b.put("customer", cust["name"], 0.4 if cust.get("weak") else 0.8, "master data", {"page": None, "text": cust["how"]})
        if cust.get("weak"):
            b.extra_check.add("customer")
        if cust.get("customer_id"):
            b.put("customer_id", cust["customer_id"], 0.8, "master data", {"page": None, "text": cust["how"]})
    b.put("subject", subject.strip()[:200], 0.9, source={"page": None, "text": "the email's subject"})


def _squash(text: str) -> str:
    """Runs of spaces become one space and runs of blank lines one blank line, so no pattern below ever has a long run of whitespace to chew on."""
    t = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    return re.sub(r"\n[ ]?(?:\n[ ]?)+", "\n\n", t)


def _put_date(b: "Builder", name: str, text: str) -> None:
    """A date as printed, unless it can be read two ways (03/04/2026): then it is not offered, and the warning says why."""
    from app.services.pipelines import read_date

    r = read_date(text)
    if r["ambiguous"]:
        b.warnings.append(f"The date {text} can be read two ways (day first or month first), so it was not filled in. Check the email.")
        return
    b.put(name, text, 0.9)


def rfq_from_email(text: str = "", mail_id: Any = None, sender: str = "", subject: str = "") -> Dict[str, Any]:
    if mail_id:
        m = _mail(mail_id)
        sender, subject, text = sender or m["sender"], subject or m["subject"], f"{m['text']}\n{text}"
    text, cut = _cap(text)
    text, subject, sender = _squash(text), (subject or "")[:300], (sender or "")[:320]
    if not (text or subject):
        raise ExtractError(422, "Give the email's text or its number.")
    b = Builder("rfq", f"{subject}\n{text}")
    if cut:
        b.warnings.append(CUT_WARNING)
    _common_email(b, sender, subject, text)
    body = f"{subject}\n{text}"
    n = re.search(r"(?i)\b(?:rfq|request for quote|quote request|enquiry|inquiry)\b[\s#:.\-]{0,4}(?:no\.?|number)?[\s#:.\-]{0,4}([A-Z0-9][A-Z0-9\-]{3,20})\b", body)
    if n and any(ch.isdigit() for ch in n.group(1)):
        b.put("customer_rfq_number", n.group(1), 0.9)
    d = re.search(r"(?i)\b(?:due(?: by| on| date)?|needed by|deadline|respond by|reply by|quote by)[\s:]*" + _DATE, body)
    if d:
        _put_date(b, "due_date", d.group(1))
    items = _items_from(text)
    if items:
        b.put("items", items, 0.8, source={"page": None, "text": f"{len(items)} line(s) that start with a quantity"})
    if "customer" not in b.fields:
        b.warnings.append("The customer could not be matched to a customer on file. Choose it from the list.")
    if "items" not in b.fields:
        b.warnings.append("No lines with a quantity were found. Add what is being asked for.")
    return b.done()


def order_from_email(text: str = "", mail_id: Any = None, sender: str = "", subject: str = "") -> Dict[str, Any]:
    if mail_id:
        m = _mail(mail_id)
        sender, subject, text = sender or m["sender"], subject or m["subject"], f"{m['text']}\n{text}"
    text, cut = _cap(text)
    text, subject, sender = _squash(text), (subject or "")[:300], (sender or "")[:320]
    if not (text or subject):
        raise ExtractError(422, "Give the email's text or its number.")
    b = Builder("order", f"{subject}\n{text}")
    if cut:
        b.warnings.append(CUT_WARNING)
    _common_email(b, sender, subject, text)
    body = f"{subject}\n{text}"
    po = re.search(r"(?i)\b(?:customer\s+)?(?:p\.?o\.?|purchase order)\b[\s#:.\-]{0,4}(?:no\.?|number)?[\s#:.\-]{0,4}([A-Z0-9][A-Z0-9\-]{3,20})\b", body)
    if po and any(ch.isdigit() for ch in po.group(1)):
        b.put("customer_po_number", po.group(1), 0.9)
    d = re.search(r"(?i)\b(?:deliver(?:y)?(?: date)?|ship(?: date)?|needed by|required by)(?:\s+(?:by|on|before))?[\s:]*" + _DATE, body)
    if d:
        _put_date(b, "due_date", d.group(1))
    t = re.search(r"(?i)\btotal(?:\s+(?:amount|price|due|cost|value|invoice|order))?\s*(?:\(?(?:usd|cad|eur|cny)\)?)?\s*[:=-]?\s*(?:\$|usd|cad|eur|cny)?\s*([\d,]+\.\d{2})(?!\s*(?:kg|kgs|lb|lbs|tons?|pcs|pc|units?|ea\b|mm|cm|%))", body)
    if t:
        b.put("total", t.group(1).replace(",", ""), 0.9)
    terms = re.search(r"(?i)\bnet\s*(\d{1,3})\b", body)
    if terms:
        b.put("payment_terms", f"Net {terms.group(1)} Days", 0.9)
    items = _items_from(text)
    if items:
        try:
            from app.services.ai import features

            for it in items:
                hits = features.candidate_items(it["description"], limit=1)
                if hits and hits[0]["score"] >= 90:
                    it["item_id"], it["item_match"] = hits[0]["item_id"], f"{hits[0]['score']}% like {hits[0]['description']}"
        except Exception:  # noqa: BLE001
            pass
        b.put("items", items, 0.8, source={"page": None, "text": f"{len(items)} line(s) that start with a quantity"})
    if "customer_po_number" not in b.fields:
        b.warnings.append("No customer PO number was found in the text. Check the attached PO.")
    if "customer" not in b.fields:
        b.warnings.append("The customer could not be matched to a customer on file. Choose it from the list.")
    return b.done()


def _picture_pages(pdf: bytes) -> set:
    """1-based pages whose text came from reading a picture (no usable text layer, or a picture on the page): their figures are less sure."""
    try:
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        return {i for i, p in enumerate(doc, start=1) if len(p.get_text().strip()) < 40 or p.get_images()}
    except Exception:  # noqa: BLE001
        return set()


def po_from_pdf(pdf: bytes, filename: str = "") -> Dict[str, Any]:
    from app.services.coordination import packets

    try:
        pages = packets.ocr_pages(pdf)
    except ValueError as exc:
        raise ExtractError(422, str(exc))
    pics = _picture_pages(pdf)
    b = Builder("po", "\n".join(pages), pages)
    unreadable = [i for i, t in enumerate(pages, start=1) if not (t or "").strip()]
    for i in unreadable:
        b.warnings.append(f"Page {i} could not be read (no text and the picture could not be read). Type its facts by hand.")
    docs = packets.split(pages)
    po = next((d for d in docs if d["kind"] == "customer_po"), None) or next((d for d in docs if d["kind"] == "allied_po"), None)
    if po is None:
        b.warnings.append("No purchase order was recognised in this file.")
        return b.done()
    f, page = po["fields"], po["page_from"]
    seen_picture = bool(pics & set(range(po["page_from"], po["page_to"] + 1)))
    for src, dst in (("customer_po_number", "customer_po_number"), ("po_number", "po_number"), ("date", "po_date"), ("total", "total"), ("currency", "currency")):
        if f.get(src):
            b.put(dst, f[src], 0.6 if seen_picture else 0.9, "ocr" if seen_picture else "rule", source={"page": page, "text": b.snippet(f[src])["text"]})
            if seen_picture:
                b.extra_check.add(dst)
    if seen_picture:
        b.warnings.append("These figures were read from a picture of the page, not from typed text. Check each one against the page.")
    if len(docs) > 1:
        b.warnings.append(f"The file holds {len(docs)} documents; this reads the first purchase order only.")
    return b.done()


_LEAD = re.compile(r"(?i)lead\s*time[^\d]{0,12}(\d{1,3})(?:\s*(?:-|to)\s*(\d{1,3}))?\s*(weeks?|days?|months?)")


def vendor_quote(text: str = "", pdf: Optional[bytes] = None) -> Dict[str, Any]:
    from app.services import landed_cost
    from app.services.coordination import packets

    pages: List[str] = [text] if text else []
    if pdf is not None:
        try:
            pages = packets.ocr_pages(pdf)
        except ValueError as exc:
            raise ExtractError(422, str(exc))
    if not any((p or "").strip() for p in pages):
        raise ExtractError(422, "There is no text to read: give the quote's text, or a PDF that can be read.")
    full, vq_cut = _cap("\n".join(pages), MAX_TEXT * 3)
    full = _squash(full)
    pics = _picture_pages(pdf) if pdf is not None else set()
    b = Builder("vendor_quote", full, pages)
    if vq_cut:
        b.warnings.append(CUT_WARNING)
    if pics:
        b.warnings.append("These figures were read from a picture of the page, not from typed text. Check each one against the page.")
    for i, t in enumerate(pages, start=1):
        if not (t or "").strip():
            b.warnings.append(f"Page {i} could not be read. Type its facts by hand.")
    facts: Dict[str, str] = {}
    for d in packets.split(pages):
        if d["kind"] in ("vendor_quote", "allied_quote"):
            facts = d["fields"]
            break
    facts = facts or packets.extract("vendor_quote", full)
    for src, dst in (("quote_order_number", "quote_number"), ("quote_number", "quote_number"), ("date", "date"), ("total", "total"), ("currency", "currency"), ("valid_until", "valid_until")):
        if facts.get(src):
            b.put(dst, facts[src], 0.9)
    m = _LEAD.search(full)
    if m:
        b.put("lead_time", f"{m.group(1)}{'-' + m.group(2) if m.group(2) else ''} {m.group(3).lower()}", 0.9, source=b.snippet(m.group(0)))
    t = re.search(r"(?i)(?:payment\s+)?terms[:\s]*(net\s*\d{1,3}(?:\s*days)?|\d{1,3}\s*/\s*\d{1,3}(?:\s*/\s*\d{1,3})?|[A-Za-z ]{4,30})", full)
    if t:
        b.put("payment_terms", " ".join(t.group(1).split()), 0.8, source=b.snippet(t.group(0)))
    cargo = landed_cost.parse_cargo(full)
    if cargo["weights"]:
        b.put("weights", cargo["weights"], 0.8, source=b.snippet(cargo["weights"][0]["value"]))
    if cargo["dimensions"]:
        b.put("dimensions", cargo["dimensions"], 0.8)
    if pics:                                                            # everything read from a picture is less sure and is checked
        for n, fld in b.fields.items():
            fld["confidence"], fld["method"] = min(fld["confidence"], 0.6), "ocr"
            b.extra_check.add(n)
    if "total" not in b.fields:
        b.warnings.append("No total was found. Check the last page.")
    return b.done()


def call_to_job_note(text: str, use_ai: bool = False) -> Dict[str, Any]:
    text, call_cut = _cap(str(text or ""))
    t = " ".join(text.split())
    if len(t) < 10:
        raise ExtractError(422, "Give the call's text (a transcript or your own notes).")
    b = Builder("call_note", str(text))
    if call_cut:
        b.warnings.append(CUT_WARNING)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if len(s.strip()) > 8]
    b.put("summary", " ".join(sentences[:3])[:500], 0.7, "rule", {"page": None, "text": "the first sentences of the call"})
    pos = sorted(set(re.findall(r"(?i)\b(?:p\.?o\.?|purchase order)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{3,20})\b", t)))
    pos = [p for p in pos if any(ch.isdigit() for ch in p)]
    if pos:
        b.put("po_numbers", pos, 0.9)
    dates = sorted(set(re.findall(_DATE, t)))
    if dates:
        b.put("dates", dates, 0.9)
    actions = [s for s in sentences if re.match(r"(?i)(we|i|they|he|she)?\s*(will|need to|needs to|must|please|can you|promised|agreed)\b", s) or re.search(r"(?i)\b(will send|will ship|will confirm|will call|need(?:s)? (?:the|a))\b", s)]
    if actions:
        b.put("action_items", actions[:8], 0.7, "rule", {"page": None, "text": "sentences that say who will do something"})
    if use_ai:
        try:
            from app.services.ai import features

            res, _sid = features.summarize_call(str(text), actor="extract")
            summary = getattr(res, "summary", None)
            if summary:
                b.put("summary", str(summary)[:500], 0.5, "ai", {"page": None, "text": "AI summary, not checked"})
                b.ai_used = True
        except Exception:  # noqa: BLE001 - AI off or busy: the rule-based note stands
            b.warnings.append("The AI summary was not available, so the plain summary above was used.")
    return b.done()


# ------------------------------------------------------------------ feedback and stats

def feedback(extraction_id: str, fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """What the person saved for each field: counted later as kept, changed, left blank or added. fields: {name: final value} or {name: {shown, final}}."""
    with SessionLocal() as db:
        row = db.query(AuditLogDB).filter(AuditLogDB.action == "EXTRACT_SHOWN", AuditLogDB.resource_id == str(extraction_id)).first()
        if row is None:
            raise ExtractError(404, "That extraction was not found (it may be old).")
        me = (user.get("email") or "").strip().lower()
        if (row.actor_email or "").strip().lower() not in (me, "system", ""):
            raise ExtractError(403, "That extraction was made by someone else, so you cannot report on it.")
        if db.query(AuditLogDB).filter(AuditLogDB.action == "EXTRACT_FEEDBACK", AuditLogDB.resource_id == str(extraction_id)).first() is not None:
            raise ExtractError(409, "That form was already reported. Each suggestion is counted once.")
        try:
            shown_doc = json.loads(row.detail or "{}")
        except ValueError:
            shown_doc = {}
    shown = shown_doc.get("fields") or {}
    result: Dict[str, Any] = {}
    names = set(shown) | set(fields or {})
    for name in sorted(names):
        entry = (fields or {}).get(name)
        final = entry.get("final") if isinstance(entry, dict) else entry
        was = shown.get(name)
        method = (was or {}).get("method") or "typed"
        shown_value = str((was or {}).get("value", "")) if was else ""
        final_text = "" if final in (None, "") else (final if isinstance(final, str) else json.dumps(final))
        if was and final_text == "":
            outcome = "blank"
        elif was and final_text.strip() == shown_value.strip():
            outcome = "kept"
        elif was:
            outcome = "changed"
        elif final_text:
            outcome = "added"
        else:
            continue
        result[name] = {"method": method, "outcome": outcome}
    audit_log(action="EXTRACT_FEEDBACK", resource_type="EXTRACTION", resource_id=str(extraction_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=json.dumps({"kind": shown_doc.get("kind"), "fields": result})[:8000])
    return {"extraction_id": extraction_id, "counted": result}


def extraction_stats(days: int = 180) -> Dict[str, Any]:
    """Per kind and field: how often people kept, changed, blanked or added it, and how that differs by how it was found (rule, master data, ai). Read from the audit trail."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 3650)))
    table: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    n = 0
    with SessionLocal() as db:
        for r in db.query(AuditLogDB).filter(AuditLogDB.action == "EXTRACT_FEEDBACK", AuditLogDB.timestamp >= since).all():
            try:
                d = json.loads(r.detail or "{}")
            except ValueError:
                continue
            n += 1
            for name, v in (d.get("fields") or {}).items():
                table[f"{d.get('kind')}.{name}"][v.get("method", "typed")][v.get("outcome", "kept")] += 1
    rows = []
    for key in sorted(table):
        for method, counts in sorted(table[key].items()):
            total = sum(counts.values())
            rows.append({"field": key, "method": method, "shown": total, "kept": counts.get("kept", 0), "changed": counts.get("changed", 0), "blank": counts.get("blank", 0), "added": counts.get("added", 0),
                         "kept_percent": round(100 * counts.get("kept", 0) / total, 1) if total else None})
    return {"days": days, "forms_reviewed": n, "fields": rows, "note": "Counts from what people saved after seeing a suggestion. Use them to decide which fields need a closer look."}

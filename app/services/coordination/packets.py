"""Job packets: the scanned stack Allied keeps for one job (the handwritten Sales Order Information sheet, the customer's emails and PO, Allied's
quotation, the vendor's quotation, Allied's purchase order, Allied's sales order and packing list, the drawings). This reads such a PDF page by
page, says what each page is, groups neighbouring pages of one kind into documents, and pulls out only the facts that are actually printed on them
(order numbers, dates, totals). A blank stays blank: nothing is guessed, copied from another page or filled in. Handwriting is not read.

Allied's own forms are the standard here; this never changes them. It only brings what they already say into the job's view."""

import hashlib
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from app.core.logging import logger

MAX_PAGES = 60
_ocr_slots = threading.BoundedSemaphore(2)      # at most two packets are read at once; the rest wait their turn (or are told to retry)

# kind -> how a page of it gets recognised (any phrase, case-insensitive). First match in this order wins, so the specific ones come first.
KINDS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("cover_sheet", "Sales Order Information sheet", ("sales order information",)),
    ("allied_po", "Allied purchase order to a vendor", ()),       # recognised only by Allied's own letterhead at the top (see classify)
    ("allied_so", "Allied sales order", ("allied\nsales order", "sales order number", "sales order date")),
    ("packing_list", "Allied packing list", ("packing list",)),
    ("allied_quote", "Allied quotation to the customer", ()),       # recognised by classify: Allied's name and the word quotation in the title
    ("vendor_quote", "Vendor quotation", ()),
    ("customer_po", "Customer's purchase order", ("purchase order number", "purchase order ", "p.o. number")),
    ("email_printout", "Email printout", ("gmail", "mess")),
    ("drawing_notes", "Drawing or general notes", ("general notes", "drawing no", "dwg no")),
]


def _clean(text: str) -> str:
    """The page's text without a fax header line (its date and number belong to the fax machine, not to the document)."""
    text = re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", re.sub(r"[ \t]{2,}", " ", (text or "")[:200000]))      # bounded and without long runs of blanks (they make the patterns crawl)
    lines = text.splitlines()
    return "\n".join(l for i, l in enumerate(lines) if not re.match(r"^\s*fax\b", l, re.I) and not (i < 3 and re.search(r"\bfax\b", l, re.I) and re.search(r"\d", l)))


def classify(text: str) -> str:
    text = _clean(text)
    t = (text or "").lower()
    if not t.strip():
        return "blank"
    # An Allied PO to a vendor carries Allied's letterhead at the TOP. A customer's PO to Allied may mention Allied's address further down (bill-to
    # or supplier): that is still the customer's PO.
    if "gmail" in t[:300] and "@" in t:
        return "email_printout"                             # an email printout may mention quotations, POs or Allied: it is still an email
    at = t.find("welding systems")
    own_letterhead = 0 <= at < 160 and not re.search(r"bill to|supplier|vendor|sold to|ship to|deliver|\bto\s*:", t[:at])    # Allied's name opens the page: it is Allied's own paper
    if own_letterhead and ("date issued" in t or "authorized signature" in t or "248-8838" in t or "continued" in t):
        return "allied_po"                                  # Allied's letterhead on top AND something only Allied's own PO form has; a bare "purchase order no" is not enough
    if re.search(r"purchase\s+order\s*(?:number|no\.?)?[ \t:#]*\d{5,}", t):
        return "customer_po"                                # a PO that names Allied as the supplier or bill-to is the customer's
    if 0 <= t.find("allied") < 60 and ("quotation" in t[:300] or "quote number" in t):
        return "allied_quote"                               # Allied's own quotation opens with Allied's name; a vendor's quotation only mentions Allied further down
    if "quotation" in t[:300]:
        return "vendor_quote"                               # "quotation" in the title; the word in the body ("per your quotation") proves nothing
    # An Allied sales order or packing list also contains the words "sales order number"; the packing list says so in its title
    if "packing list" in t[:400]:
        return "packing_list"
    for kind, _label, phrases in KINDS:
        if any(p in t for p in phrases):
            return kind
    return "other"


def label(kind: str) -> str:
    return next((lab for k, lab, _ in KINDS if k == kind), {"blank": "Blank page", "other": "Other page"}.get(kind, kind))


_VALID = r"(?:valid\s+(?:until|through|thru|to)|good\s+(?:until|through|thru)|expires?(?:\s+on)?|expiry\s+date|offer\s+valid\s+until)[ \t:]*"
_DATE = r"(\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})"


def _first(pattern: str, text: str, flags: int = re.I) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m and m.group(1).strip() else None


_CODE = re.compile(r"\b(USD|CAD|CNY|RMB|EUR|GBP|JPY)\b", re.I)
_SYMBOL = re.compile(r"(US\$|\$|¥|￥|€|£)")
_EXCLUDE = re.compile(r"sub[\s\-]*total|page\s+total|\bdeposit\b|\bbalance\b|\bdiscount\b|total\s+(?:tax|freight|shipping|handling|weight|qty|quantity|pcs|pieces|items?)\b|\b(?:tax|vat|freight|shipping|handling|weight|qty|quantity)\s+total\b", re.I)
_CURRENCIES = {"USD", "CAD", "CNY", "EUR", "GBP", "JPY"}
_INCL = re.compile(r"\((?:incl|including|inc)\b[^)]*\)|\b(?:tax|vat)\s+inclusive\b|\bincl(?:uding|usive)?\.?\s+\w+", re.I)
_AMOUNT = r"(?<![\d\-.,(])([\d,]+\.\d{2})(?![\d,.]*\d)"        # not part of a longer number ("1.250,00"), not negative


def _total(text: str) -> Dict[str, str]:
    """The document's grand total and the currency printed with it. A "Grand total" or "Amount due" line wins; otherwise the LAST plain "Total" line
    (a quote's grand total is at the bottom). Sub-totals, page totals, and total weight, tax, freight or quantity lines are never the total, but a
    label like "Grand Total (incl. tax)" still is. An explicit code (CAD) beats a symbol ($). Numbers written the European way are not read."""
    best: Optional[tuple] = None
    for line in text.splitlines():
        if _EXCLUDE.search(_INCL.sub("", line)):          # "Total (incl. deposit)" and "Total Tax Inclusive" are still totals
            continue
        raw = line
        lead = re.match(r"\s*(grand\s+total|amount\s+due|total\s+amount|total\s+price|total)\b", raw, re.I)
        amounts = list(re.finditer(_AMOUNT, raw))
        if lead and amounts and re.search(r"\bincl", raw, re.I):
            # "Total (incl. 8.25% tax): 1,081.50", "Total including all taxes, duties and freight: 1,000.00": whatever the label says, the total is the LAST amount on the line
            label, amount, near, after = lead.group(1), amounts[-1].group(1), raw[lead.start(1):amounts[-1].end(1)], raw[amounts[-1].end(1):amounts[-1].end(1) + 6]
        else:
            line = _INCL.sub(" ", raw)
            m = re.search(r"(grand\s+total|amount\s+due|total\s+amount|total\s+price|\btotal)\b[^\d\n]{0,25}" + _AMOUNT, line, re.I)
            if not m:
                continue
            label, amount = m.group(1), m.group(2)
            near, after = line[m.start(1):m.end(2)], line[m.end(2):m.end(2) + 6]       # the currency written beside THIS amount, not one for another amount on the line
        # a grand total beats a plain total; "amount due" can be the balance after a deposit, so a grand total printed with it wins
        rank = 3 if re.match(r"grand", label, re.I) else 2 if re.match(r"amount\s+due", label, re.I) else 1
        if best is None or rank >= best[0]:
            code, sym = _CODE.search(near) or _CODE.search(after), _SYMBOL.search(near) or _SYMBOL.search(after)
            best = (rank, amount, (code.group(1).upper() if code else (sym.group(1) if sym else None)))
    if not best:
        return {}
    return {"total": best[1], **({"currency": best[2]} if best[2] else {})}


def _money(text: str, label_pattern: str) -> Optional[str]:
    m = re.search(label_pattern + r"[^\d\n]{0,25}" + _AMOUNT, text, re.I)
    return m.group(1) if m else None


def extract(kind: str, text: str) -> Dict[str, str]:
    """The facts printed on a page of this kind, and only those. Keys with nothing printed are simply absent."""
    text = _clean(text)
    f: Dict[str, Optional[str]] = {}
    if kind == "allied_po":
        f["po_number"] = _first(r"purchase\s+order\s+no\.?:?\s*\n?\s*(\d{3,8})\b", text) or (
            _first(r"\n(\d{4,6})\n\s*\d{1,2}/\d{1,2}/\d{2,4}\n", text) if re.search(r"date issued", text, re.I) else None)   # a bare number is only trusted next to "Date Issued"
        f["date"] = _first(_DATE, text[:700])
        f.update(_total(text))
    elif kind == "allied_so":
        f["sales_order_number"] = _first(r"sales\s+order\s+number:?[ \t]*(\d{4,8})\b", text)       # a blank number stays blank
        f["date"] = _first(r"sales\s+order\s+date:?[ \t]*" + _DATE, text)
    elif kind == "packing_list":
        f["sales_order_number"] = _first(r"sales\s+order\s+number:?[ \t]*(\d{4,8})\b", text)
        f["date"] = _first(r"sales\s+order\s+date:?[ \t]*" + _DATE, text)
    elif kind == "allied_quote":
        f["quote_number"] = _first(r"quote\s+number:?[ \t]*([0-9]{6,8}-\d+)", text)
        f["date"] = _first(r"quote\s+date:?[ \t]*" + _DATE, text)
        f.update(_total(text))
        f["valid_until"] = _first(_VALID + _DATE, text)
    elif kind == "vendor_quote":
        f["quote_order_number"] = _first(r"order\s+number:?[ \t]*(\d{4,10})\b", text)
        f["date"] = _first(r"order\s+date:?[ \t]*" + _DATE, text)
        f.update(_total(text))
        f["valid_until"] = _first(_VALID + _DATE, text)
    elif kind == "customer_po":
        f["customer_po_number"] = _first(r"purchase\s+order(?:\s+number|\s+no\.?)?[ \t:#]*(\d{6,12})\b", text)
        f["date"] = _first(r"\bdate[\s:]{1,12}" + _DATE, text)
        f.update(_total(text))
        if "total" not in f and _money(text, r"total\s+amount[^\n]*?(?:USD|CAD)?"):
            f["total"] = _money(text, r"total\s+amount[^\n]*?(?:USD|CAD)?")
    elif kind == "email_printout":
        f["subject"] = _first(r"\n([^\n]{3,80})\n[^\n]*\bmes", text)
    return {k: v for k, v in f.items() if v}


def split(pages: List[str]) -> List[Dict[str, Any]]:
    """Neighbouring pages of one kind become one document (a PO that runs onto a second page is one PO). Facts come from the first page that
    states them; a later page never overwrites an earlier one and never fills a gap with a guess."""
    docs: List[Dict[str, Any]] = []
    for i, text in enumerate(pages, 1):
        kind = classify(text)
        facts = extract(kind, text)
        ids = ("po_number", "customer_po_number", "sales_order_number", "quote_number", "quote_order_number")
        new_number = bool(docs) and any(k in facts and k in docs[-1]["fields"] and facts[k] != docs[-1]["fields"][k] for k in ids)
        if docs and docs[-1]["kind"] == kind and kind not in ("other", "email_printout") and not new_number:      # a page with a different number starts a new document
            d = docs[-1]
            d["page_to"] = i
            for k, v in facts.items():
                if k in ("total", "currency") and kind in ("allied_quote", "vendor_quote"):
                    d["fields"][k] = v                        # the grand total is on the last page
                else:
                    d["fields"].setdefault(k, v)
            continue
        docs.append({"kind": kind, "label": label(kind), "page_from": i, "page_to": i, "fields": facts})
    for d in docs:
        if d["kind"] == "blank":
            d["kind"], d["label"] = "unreadable", "Unreadable page (blank or too faint to read)"
    return docs


def ocr_pages(pdf: bytes, dpi: int = 130) -> List[str]:
    """The text of each page: the PDF's own text where it has any, otherwise OCR (turned upright if a page was scanned sideways)."""
    import fitz
    from PIL import Image

    import app.services.document_scanner_service  # noqa: F401  (registers the module)
    import sys

    scanner = sys.modules["app.services.document_scanner_service"]
    scanner.init_tesseract()
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - a truncated or corrupt file
        raise ValueError("That PDF is damaged or incomplete and could not be opened.") from exc
    if doc.needs_pass:
        raise ValueError("That PDF is password protected. Save an unprotected copy and upload that.")
    if len(doc) == 0:
        raise ValueError("That PDF has no pages.")
    if len(doc) > MAX_PAGES:
        raise ValueError(f"That PDF has {len(doc)} pages; the limit is {MAX_PAGES}.")
    texts: List[Optional[str]] = [None] * len(doc)
    images: Dict[int, Any] = {}
    for i, page in enumerate(doc):
        own = page.get_text().strip()
        if len(own) >= 40 and not page.get_images():      # a page that is a picture is read from the picture: an old text layer can disagree with it
            texts[i] = own
        else:
            images[i] = Image.open(io.BytesIO(page.get_pixmap(dpi=scanner._capped_dpi(page, dpi)).tobytes("png")))
    if images:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for i, text in zip(images, pool.map(scanner.ocr_upright, images.values())):
                texts[i] = text
    return [t or "" for t in texts]


def read_upload(stream, limit_mb: Optional[int] = None) -> bytes:
    """The uploaded bytes, read in pieces so an oversized upload is refused as it arrives, not after the whole thing is in memory."""
    from app.config import settings
    from app.services.coordination import jobs

    cap = (limit_mb if limit_mb is not None else settings.SUBMISSION_MAX_MB) * 1024 * 1024
    out = bytearray()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            return bytes(out)
        out += chunk
        if len(out) > cap:
            raise jobs.JobError(f"That PDF is larger than {cap // (1024 * 1024)} MB.")


def sha(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()


# ----------------------------------------------------------------------------- storing and linking

def _db():
    from app.core.database import SessionLocal

    return SessionLocal()


def _as_dict(r, staff: bool = True) -> Dict[str, Any]:
    fields = dict(r.fields or {})
    if not staff:
        fields.pop("sales_order_number", None)             # our own sales order number is internal
    return {"id": r.id, "job_id": r.job_id, "kind": r.kind, "label": r.label, "pages": f"{r.page_from}-{r.page_to}" if r.page_to != r.page_from else str(r.page_from),
            "page_from": r.page_from, "page_to": r.page_to, "fields": fields, "warnings": warnings_for(fields), "confirmed": bool(r.confirmed_at), "confirmed_by": r.confirmed_by, "source": r.source_name, "packet": r.source_sha, "at": r.created_at.isoformat() if r.created_at else None}


def ambiguous_date(text: str) -> bool:
    """03/04/26 could be 4 March or 3 April: both parts are 12 or less and differ."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", (text or "").strip())
    return bool(m) and int(m.group(1)) <= 12 and int(m.group(2)) <= 12 and m.group(1) != m.group(2)


def warnings_for(fields: Dict[str, Any]) -> List[str]:
    out = []
    d = fields.get("date")
    if d and ambiguous_date(d):
        out.append(f"The date {d} could be read two ways (month/day or day/month). Check it against the page.")
    return out


def match_job(fields_by_doc: List[Dict[str, str]], text: str = "") -> Optional[int]:
    """The job a packet belongs to, only when it is clear: a customer PO number or Allied sales order number printed in the packet belongs to exactly
    one existing order, AND that order's customer is named in the packet. Numbers repeat between customers and OCR misreads digits, so a number alone
    never attaches a packet. Otherwise the packet waits unattached for a person."""
    from app.models.db_models import JobDB, OrderDB

    cust = {f["customer_po_number"] for f in fields_by_doc if f.get("customer_po_number")}
    sos = {f["sales_order_number"] for f in fields_by_doc if f.get("sales_order_number")}
    if not (cust or sos):
        return None
    hay = re.sub(r"[^a-z0-9]+", " ", (text or "").casefold())
    found = set()
    with _db() as db:
        for o in db.query(OrderDB).filter(OrderDB.customer_po_number.in_(cust) | OrderDB.sales_order_number.in_(sos)).all():
            from app.services.coordination import jobs as _jobs

            words = _jobs.norm_name(o.customer_name or "").split()
            phrase = " ".join(words[:2]) if len(words) >= 2 else (words[0] if words and len(words[0]) >= 6 else "")
            if not phrase or f" {phrase} " not in f" {_jobs.norm_name(text)} ":
                continue
            for j in db.query(JobDB).filter(JobDB.sales_order_number == o.sales_order_number).all():
                found.add(j.id)
    return next(iter(found)) if len(found) == 1 else None


def ingest(pdf: bytes, name: str, user: Dict[str, Any], job: Any = None) -> Dict[str, Any]:
    """Reads a scanned packet and stores one record per document in it. Uploading the same file twice changes nothing. With `job` the documents
    go straight onto that job; without it they attach by a printed PO or sales order number when exactly one job matches, else they wait."""
    from pathlib import Path

    from app.config import settings
    from app.core.uploads import ensure_upload_dir, safe_upload_name
    from app.models.db_models import JobDocumentDB
    from app.services.coordination import jobs

    if not pdf.startswith(b"%PDF"):
        raise jobs.JobError("That file is not a PDF.")
    if len(pdf) > settings.SUBMISSION_MAX_MB * 1024 * 1024:
        raise jobs.JobError(f"That PDF is larger than {settings.SUBMISSION_MAX_MB} MB.")
    digest = sha(pdf)
    with _db() as db:
        have = db.query(JobDocumentDB).filter(JobDocumentDB.source_sha == digest).order_by(JobDocumentDB.page_from).all()
        if have:
            return {"packet": digest, "already_read": True, "documents": [_as_dict(r) for r in have]}
    if not _ocr_slots.acquire(timeout=120):
        raise jobs.JobError("Other packets are being read right now. Try again in a minute.")
    try:
        pages = ocr_pages(pdf)
    except ValueError as exc:
        raise jobs.JobError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the details go to the log; the person gets a plain message
        logger.exception(f"Reading a packet failed: {exc}")
        raise jobs.JobError("That PDF could not be read. If it is a photo or a very faint scan, try a clearer copy.") from exc
    finally:
        _ocr_slots.release()
    docs = split(pages)
    if not any(d["kind"] != "unreadable" for d in docs):
        raise jobs.JobError("No readable pages were found in that PDF. Try a clearer or upright scan.")
    job_id = None
    if job is not None:
        with _db() as db:
            job_id = jobs._find(db, job).id
    else:
        job_id = match_job([d["fields"] for d in docs], "\n".join(pages))
    folder = Path(ensure_upload_dir()) / "packets"
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{digest}.pdf"
    new_file = not stored.exists()
    stored.write_bytes(pdf)
    try:
        with _db() as db:
            rows = []
            for d in docs:
                r = JobDocumentDB(job_id=job_id, kind=d["kind"], label=d["label"], page_from=d["page_from"], page_to=d["page_to"], fields=d["fields"],
                                  text="\n".join(pages[d["page_from"] - 1:d["page_to"]])[:30000], source_name=safe_upload_name(name) or "packet.pdf",
                                  source_sha=digest, created_by=user.get("email", "system"))
                db.add(r)
                rows.append(r)
            db.commit()
            for r in rows:
                db.refresh(r)
            out = [_as_dict(r) for r in rows]
    except IntegrityError:        # the same packet was read by someone else a moment ago: theirs stands
        with _db() as db:
            have = db.query(JobDocumentDB).filter(JobDocumentDB.source_sha == digest).order_by(JobDocumentDB.page_from).all()
            return {"packet": digest, "already_read": True, "documents": [_as_dict(r) for r in have]}
    except Exception:
        if new_file:
            stored.unlink(missing_ok=True)       # no database rows, so no file left behind
        raise
    return {"packet": digest, "already_read": False, "job_id": job_id, "documents": out}


def for_job(job_id: int, staff: bool = True) -> List[Dict[str, Any]]:
    from app.models.db_models import JobDocumentDB

    with _db() as db:
        return [_as_dict(r, staff) for r in db.query(JobDocumentDB).filter(JobDocumentDB.job_id == job_id).order_by(JobDocumentDB.id).all()]


def unattached() -> List[Dict[str, Any]]:
    """Packets nobody has attached to a job yet, one row per packet."""
    from app.models.db_models import JobDocumentDB

    with _db() as db:
        rows = db.query(JobDocumentDB).filter(JobDocumentDB.job_id.is_(None)).order_by(JobDocumentDB.id).all()
    packets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        p = packets.setdefault(r.source_sha, {"packet": r.source_sha, "source": r.source_name, "documents": []})
        p["documents"].append(_as_dict(r))
    return list(packets.values())


def attach(packet: str, job: Any) -> Dict[str, Any]:
    """Puts every document of a packet on a job (a person's call). Nothing in a packet already on another job is moved."""
    from app.models.db_models import JobDocumentDB
    from app.services.coordination import jobs

    with _db() as db:
        j = jobs._find(db, job)
        rows = db.query(JobDocumentDB).filter(JobDocumentDB.source_sha == packet).all()
        if not rows:
            raise LookupError("No such packet.")
        if any(r.job_id not in (None, j.id) for r in rows):
            raise jobs.JobError("That packet is already on another job.")
        for r in rows:
            r.job_id = j.id
        db.commit()
        jid = j.id
    return {"job_id": jid, "documents": for_job(jid)}


def file_path(packet: str):
    from pathlib import Path

    from app.core.uploads import ensure_upload_dir

    if not re.fullmatch(r"[0-9a-f]{64}", packet or ""):
        raise LookupError("No such packet.")
    p = Path(ensure_upload_dir()) / "packets" / f"{packet}.pdf"
    if not p.is_file():
        raise LookupError("No such packet.")
    return p


def found(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The facts the job's documents state, gathered in one place. Only what is printed: a fact no document states is not in here."""
    out: Dict[str, Any] = {}
    for d in documents:
        for k, v in d["fields"].items():
            if k in ("date", "total", "valid_until", "currency"):
                continue
            if k in ("po_number", "quote_number", "quote_order_number"):
                out.setdefault(k + "s", [])
                if v not in out[k + "s"]:
                    out[k + "s"].append(v)
            else:
                out.setdefault(k, v)
    return out


def _clean_fact(key: str, value: Any) -> str:
    from app.services import order_documents
    from app.services.coordination import jobs

    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:64]
    if not text:
        return ""
    if key == "total":
        n, _cur = order_documents.money(text)
        if n is None or n < 0:
            raise jobs.JobError("The total must be a number that is not negative, written like 1200.00 or 1,200.00 (a total written 1.234,50 is ambiguous: write it 1234.50).")
        return f"{n:,.2f}"
    if key == "currency":
        if text.upper() not in _CURRENCIES:
            raise jobs.JobError("The currency must be one of " + ", ".join(sorted(_CURRENCIES)) + ".")
        return text.upper()
    if key == "valid_until" and not iso_date(text):
        raise jobs.JobError("The valid-until date was not understood. Use a date like 2026-09-01 or 9/1/26.")
    if key == "date" and not iso_date(text):
        raise jobs.JobError("The date was not understood. Use a date like 2026-09-01 or 9/1/26.")
    if key in ("po_number", "sales_order_number", "customer_po_number") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_/.]{0,31}", text):
        raise jobs.JobError(f"'{key}' must be letters, digits and dashes only.")
    return text


def confirm(doc_id: int, corrections: Optional[Dict[str, Any]], user: Dict[str, Any]) -> Dict[str, Any]:
    """A person checked this document's facts against the page (and corrected any misread value). Only confirmed documents are used to stage anything
    onto monday: a wrong PO number or total from a smudged scan must never travel on its own. Values are cleaned: a total is a real number, a date is a
    date, a number is a number; an emptied value is a blank again."""
    import datetime as _dt

    from app.models.db_models import JobDocumentDB
    from app.services import order_documents
    from app.services.coordination import jobs

    with _db() as db:
        r = db.get(JobDocumentDB, int(doc_id)) if 0 < int(doc_id) < 2**31 else None
        if r is None:
            raise LookupError("No such document.")
        allowed = set(r.fields or {}) | {"po_number", "date", "total", "sales_order_number", "quote_number", "quote_order_number", "customer_po_number", "vendor", "valid_until", "currency"}
        fields = dict(r.fields or {})
        for k, v in (corrections or {}).items():
            if k not in allowed:
                raise jobs.JobError(f"'{k}' is not a fact this kind of document carries.")
            text = _clean_fact(k, v)
            if text:
                fields[k] = text
                if k == "total":          # a total typed with its currency ("EUR 5,000.00") keeps the currency, so it is never assumed to be dollars
                    code = order_documents.money(str(v))[1]
                    yen_only = bool(re.search("[¥￥]", str(v))) and not re.search(r"\b(CNY|RMB|JPY)\b", str(v), re.I)      # a yen sign alone could be yuan or yen: unknown, never guessed
                    if code in _CURRENCIES and not yen_only:
                        fields["currency"] = code
            else:
                fields.pop(k, None)            # an emptied value is a blank again, not a guess
                if k == "total":
                    fields.pop("currency", None)       # a currency belongs to its total: no total, no currency left behind
        r.fields = fields
        r.confirmed_by, r.confirmed_at = user.get("email", "system"), _dt.datetime.now(_dt.timezone.utc)
        db.commit()
        db.refresh(r)
        return _as_dict(r)


# ----------------------------------------------------------------------------- bringing a packet's facts to the 2026 Orders board (staged)

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def iso_date(text: str) -> Optional[str]:
    """A printed date ("8/14/26", "Aug 14, 2026", "2026-08-14") as 2026-08-14, or None when it is not clearly a date."""
    t = (text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        mo, d, y = int(m.group(2)), int(m.group(3)), int(m.group(1))
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t)
    if m and "/" in t:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y += (1900 if y > 50 else 2000) if y < 100 else 0
    elif not m:
        m = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}), (\d{4})", t)
        if not m or m.group(1).lower() not in _MONTHS:
            return None
        mo, d, y = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
    import datetime as _d

    try:
        return _d.date(y, mo, d).isoformat() if 1900 <= y <= 2200 else None
    except ValueError:
        return None                                       # 2026-13-45 and 2/31/26 are not dates


# the fact a packet states -> the column on the 2026 Orders board it belongs in
BOARD_COLUMNS = {"customer_po_number": "Customer PO#", "po_number": "Allied PO#", "customer_po_date": "PO received",
                 "po_date": "Vendor PO Issued", "vendor": "Vendor"}


def facts_for_board(documents: List[Dict[str, Any]]) -> Dict[str, str]:
    """What the job's documents state that the orders board has a column for. Only printed facts; a missing one is simply not here."""
    out: Dict[str, str] = {}
    for d in documents:
        f = d["fields"]
        if d["kind"] == "customer_po":
            if f.get("customer_po_number"):
                out.setdefault("customer_po_number", f["customer_po_number"])
            if iso_date(f.get("date", "")):
                out.setdefault("customer_po_date", iso_date(f["date"]))
        elif d["kind"] == "allied_po":
            if f.get("po_number"):
                out.setdefault("po_number", f["po_number"])
            if iso_date(f.get("date", "")):
                out.setdefault("po_date", iso_date(f["date"]))
        elif d["kind"] == "vendor_quote" and f.get("vendor"):
            out.setdefault("vendor", f["vendor"])
    return out


def stage_to_monday(job: Any, user: Dict[str, Any]) -> Dict[str, Any]:
    """Stages (never sends) the job's packet facts onto its row on the 2026 Orders board. The row is found by its Allied PO# or Customer PO#, and only
    when exactly one row matches. A column that already holds a value is left alone (and listed if it disagrees); only blanks are staged."""
    from app.config import settings
    from app.services import monday_push
    from app.services.coordination import boards, jobs

    board_id = str(settings.MONDAY_VENDOR_BOARD_ID or "").strip()
    b = boards._mirror.get(board_id) if board_id else None
    if b is None:
        raise jobs.JobError("The orders board has not been read yet, so there is nowhere to stage this. Read the board first.")
    with _db() as db:
        j = jobs._find(db, job)
        job_id, ref = j.id, j.ref
    all_docs = for_job(job_id)
    docs = [d for d in all_docs if d["confirmed"]]
    facts = facts_for_board(docs)
    if not facts:
        raise jobs.JobError("Nothing is ready to stage. Confirm the documents' facts first (a person checks each one against the page)." if all_docs else
                            "This job has no documents yet.")
    cols = {c["title"].casefold(): c for c in b["columns"]}
    missing = [t for t in BOARD_COLUMNS.values() if t.casefold() not in cols and t in [BOARD_COLUMNS[k] for k in facts]]
    pos = {p["po_number"] for p in jobs.get(job_id)["purchase_orders"] if p.get("po_number")} | ({facts["po_number"]} if facts.get("po_number") else set())
    def val(item, title):
        c = cols.get(title.casefold())
        return str((item["values"].get(c["id"]) if c else "") or "").strip()
    hits = [i for i in b["items"] if (val(i, "Allied PO#") in pos and val(i, "Allied PO#")) or
            (facts.get("customer_po_number") and val(i, "Customer PO#") == facts["customer_po_number"])]
    if len(hits) != 1:
        raise jobs.JobError("No single row on the orders board matches this job's PO numbers." if not hits else
                            f"{len(hits)} rows on the orders board match this job's PO numbers; staging waits until one does.")
    item = hits[0]
    staged: Dict[str, str] = {}
    differs: List[Dict[str, str]] = []
    for key, value in facts.items():
        title = BOARD_COLUMNS[key]
        if title.casefold() not in cols:
            continue
        have = val(item, title)
        if not have:
            staged[title] = value
        elif have != value:
            differs.append({"column": title, "monday": have, "document": value})
    change_id = None
    if staged:
        change_id = monday_push.stage("update_columns", board_id, item["id"], {"columns": staged},
                                      f"From the {ref} packet: " + ", ".join(f"{k} = {v}" for k, v in staged.items()), source="packet", by=user.get("email", "app"))
    return {"board_read_at": b.get("read_at"), "unconfirmed_documents": len(all_docs) - len(docs), "item": item["name"], "item_id": item["id"], "staged": staged, "change_id": change_id, "differs": differs,
            "no_column_for": [BOARD_COLUMNS[k] for k in facts if BOARD_COLUMNS[k].casefold() not in cols]}

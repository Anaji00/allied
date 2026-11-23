"""The customer-redaction firewall. A drawing may go to a vendor (an RFQ or a PO packet) only as a verified redacted copy stored on the job, or, for a file this cannot clean (DWG), with an engineer's attestation
tied to the file's hash (void the moment the file changes).

What it looks for: the job's customer and every spelling of it, the customer's contacts and domains, and the customer's own RFQ / PO numbers, in the PDF text layer, the picture of each page (read by local
OCR when Tesseract is there), metadata, bookmarks, annotations, form fields, links and embedded files; in a DXF, every text entity, attribute, dimension text and header property.
PDFs are redacted by painting over each hit and rebuilding the file from the page pictures (so no hidden text layer, metadata or embedded file survives), then the result is scanned again: it must come out clean.
A page that is only a picture and cannot be read (no OCR here) is never called clean: it needs a person. Nothing leaves the building: all of this is local."""
import hashlib
import io
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, RedactedArtifactDB

MASK = "[REDACTED]"
AUTO_TYPES = ("pdf", "dxf")


class RedactionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# Letters from other alphabets that look like Latin ones: a name written with a Cyrillic or Greek letter is still the name.
_LOOKALIKE = {"\u0430": "a", "\u0432": "b", "\u0435": "e", "\u043a": "k", "\u043c": "m", "\u043d": "h", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0442": "t", "\u0443": "y", "\u0445": "x",
              "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u0501": "d", "\u03bf": "o", "\u03b1": "a", "\u03b5": "e", "\u03b9": "i", "\u03ba": "k", "\u03bd": "v", "\u03c1": "p", "\u03c4": "t",
              "\u03c5": "u", "\u03c7": "x", "\u03b2": "b", "\u03b7": "n", "\u03bc": "u", "\u03c9": "w", "\u0131": "i", "\u0261": "g"}


UNKNOWN_GLYPH = (chr(0xfffd), chr(0xb7))                  # what a PDF text layer shows for a letter its font could not draw


_LOOKALIKE.update({chr(0xd8): "o", chr(0xf8): "o", chr(0x141): "l", chr(0x142): "l", chr(0x110): "d", chr(0x111): "d", chr(0x153): "oe", chr(0xe6): "ae", chr(0xc6): "ae", chr(0x13f): "l", chr(0x140): "l"})
_LEET_I = str.maketrans({"0": "o", "1": "i", "5": "s", "3": "e", "4": "a", "7": "t", "$": "s", "@": "a"})
_LEET = str.maketrans({"0": "o", "1": "l", "5": "s", "3": "e", "4": "a", "7": "t", "$": "s", "@": "a"})


def fold(text: Any) -> str:
    """The one way names are compared, by the scan and by the painter alike: NFKC, zero-width and other invisible characters dropped, accents dropped, look-alike letters mapped to Latin, case folded, and every
    space, dot, hyphen and other mark removed ("Owens . Corning", "OWENS-CORNING", "O w e n s", "Owens\u200bCorning" and "Oewens" with a Cyrillic o all become "owenscorning")."""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch) not in ("Cf", "Cc", "Mn", "Me") or ch in "\n")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = "".join(_LOOKALIKE.get(ch, ch) for ch in t)
    return "".join((chr(0) if ch in UNKNOWN_GLYPH else ch) for ch in t if ch.isalnum() or ch in UNKNOWN_GLYPH)      # a glyph the PDF could not draw stays as a one-letter wildcard


def _norm(text: Any) -> str:
    return fold(text)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ what to look for

def clean_terms(raw: Any) -> List[str]:
    """Distinct terms of at least two characters, longest first (so a long name is painted before a short word inside it)."""
    seen, out = set(), []
    for t in raw or []:
        t = " ".join(str(t or "").split())
        if len(t) >= 2 and _norm(t) not in seen:
            seen.add(_norm(t))
            out.append(t)
    return sorted(out, key=len, reverse=True)


def terms_for_job(job_id: int, extra: Any = ()) -> List[str]:
    """Everything that identifies the customer on a job: its name and spellings, plant names, contact email and domain, the RFQ's customer, and the customer PO numbers of the job's orders."""
    from app.models.db_models import AliasTargetDB, CustomerAliasDB, CustomerFamilyDB, JobDB, OrderDB, RfqDB
    from app.services import organize

    terms: List[str] = list(extra or [])
    with SessionLocal() as db:
        job = db.get(JobDB, job_id)
        if job is None:
            raise RedactionError(404, "No such job.")
        names = [job.customer or ""]
        rfq = db.get(RfqDB, job.rfq_id) if job.rfq_id else None
        if rfq is not None:
            names.append(rfq.customer or "")
            if rfq.contact_email:
                terms.append(rfq.contact_email)
                if "@" in rfq.contact_email:
                    terms.append(rfq.contact_email.split("@", 1)[1])
        if job.sales_order_number:
            for o in db.query(OrderDB).filter(OrderDB.sales_order_number == job.sales_order_number):
                terms += [o.customer_po_number or "", o.customer_name or ""]
                names.append(o.customer_name or "")
        for n in names:
            if not n:
                continue
            terms.append(n)
            for a in db.query(CustomerAliasDB).filter(CustomerAliasDB.canonical == n):
                terms.append(a.alias)
        keyset = {organize.norm(n) for n in names if n}
        fams = [f for f in db.query(CustomerFamilyDB).all() if organize.norm(f.company) in keyset or organize.norm(f.family) in keyset]
        for f in fams:
            terms += [f.company, f.plant or "", f.family]
            for a in db.query(AliasTargetDB).filter(AliasTargetDB.family_id == f.id):
                terms.append(a.alias_text)
    return clean_terms(terms)


# ------------------------------------------------------------------ reading pictures (local OCR)

def ocr_available() -> bool:
    try:
        return bool(_scanner().init_tesseract())
    except Exception:  # noqa: BLE001
        return False


def _scanner() -> Any:
    """The scanner MODULE (the package name holds a service object of the same name, so it is taken from sys.modules)."""
    import sys

    import app.services.document_scanner_service  # noqa: F401  (registers the module)

    return sys.modules["app.services.document_scanner_service"]


def ocr_words(img: Any) -> Optional[List[Dict[str, Any]]]:
    """Words of a page picture with their boxes ({text, left, top, width, height}) from local Tesseract, or None when OCR is not available here. Replaced in tests."""
    if not ocr_available():
        return None
    import pytesseract

    data = pytesseract.image_to_data(img, lang=_scanner().ocr_lang(), output_type=pytesseract.Output.DICT)
    return [{"text": t, "left": data["left"][i], "top": data["top"][i], "width": data["width"][i], "height": data["height"][i]} for i, t in enumerate(data["text"]) if str(t).strip()]


def find_runs(words: List[str], terms: List[str]) -> List[Tuple[str, int, int]]:
    """Where the terms are in a reading-order list of words: [(term, first_word, last_word)]. The words are folded and joined, so a name split across words, lines or spaced out is found; a term shorter than 8
    characters (after folding) must start and end on a word edge (so "3M" is not found inside another word), a longer one may sit inside a word (OwensCorning, Mr.OwensCorning)."""
    folded = [fold(w) for w in words]
    joined, owner, starts, ends = "", [], {}, {}
    for idx, f in enumerate(folded):
        if f:
            starts[len(joined)] = idx
        joined += f
        owner += [idx] * len(f)
        if f:
            ends[len(joined)] = idx
    out: List[Tuple[str, int, int]] = []
    for term in terms:
        t = fold(term)
        if len(t) < 2:
            continue
        hays = [joined]
        if len(t) >= 6 and not any(ch.isdigit() for ch in t):                                 # a name written with digits for letters (0wens C0rning) is read a second way; a term that has digits itself is not
            for table in (_LEET, _LEET_I):                                   # a 1 can stand for an l or an i
                leet = joined.translate(table)
                if leet != joined and leet not in hays:
                    hays.append(leet)
        seen_at = set()
        for hay in hays:
            rx = _wild_pattern(t) if chr(0) in hay else None
            pos = 0
            while True:
                if rx is None:
                    pos = hay.find(t, pos)
                    end = pos + len(t)
                else:
                    m = rx.search(hay, pos)
                    pos, end = (m.start(), m.end()) if m else (-1, -1)
                if pos == -1:
                    break
                if (pos, end) not in seen_at and (len(t) >= 8 or (pos in starts and end in ends)):
                    seen_at.add((pos, end))
                    out.append((term, owner[pos], owner[end - 1]))
                pos += 1
    return out


def _wild_pattern(t: str) -> Any:
    """A term as a pattern that tolerates unknown-glyph marks in the text: a mark may stand in for one letter of the term, or be an extra invisible character between letters."""
    import re

    nul = chr(0)
    return re.compile("".join(f"{re.escape(nul)}*(?:{re.escape(c)}|{re.escape(nul)})" for c in t))


def _match_boxes(words: List[Dict[str, Any]], terms: List[str]) -> List[Tuple[int, int, int, int, str]]:
    """Boxes of the OCR words that spell a term (same matcher as the text layer)."""
    hits = []
    for term, a, b in find_runs([w["text"] for w in words], terms):
        run = [w for w in words[a:b + 1] if fold(w["text"])]
        if run:
            hits.append((min(w["left"] for w in run), min(w["top"] for w in run), max(w["left"] + w["width"] for w in run), max(w["top"] + w["height"] for w in run), term))
    return hits


def _has(text: str, terms: List[str]) -> List[str]:
    """Which terms are in a text (same fold as everything else: case, accents, invisible characters, look-alikes, spaces and dots do not hide a name)."""
    return list(dict.fromkeys(t for t, _a, _b in find_runs(str(text or "").split(), terms)))


# ------------------------------------------------------------------ PDF

def _page_needs_eyes(page: Any) -> bool:
    """A page that holds a picture or any vector drawing can show a name its text layer does not hold (a logo, a title block, outlined letters): it must be read by looking at it."""
    try:
        return bool(page.get_images()) or bool(page.get_drawings())
    except Exception:  # noqa: BLE001
        return True


def _text_words(page: Any) -> List[Tuple[Any, ...]]:
    return page.get_text("words") or []


def scan_pdf(data: bytes, terms: List[str], use_ocr: bool = True) -> Dict[str, Any]:
    """{findings: [{page, where, term}], pages, ocr_available, unreadable_pages}: every place the terms appear in a PDF. A page with a picture or vector drawing is also read by looking at it (local OCR); when that
    cannot be done it is listed as unreadable (never called clean)."""
    import fitz
    from PIL import Image

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise RedactionError(422, "That PDF is damaged or incomplete and could not be opened.") from exc
    if doc.needs_pass:
        raise RedactionError(422, "That PDF is password protected. Save an unprotected copy.")
    findings: List[Dict[str, Any]] = []
    unreadable: List[int] = []
    ocr_ok = use_ocr and ocr_available()

    def add(page, where, texts):
        for t in _has(" ".join(str(x) for x in texts if x), terms):
            findings.append({"page": page, "where": where, "term": t})

    add(0, "metadata", list((doc.metadata or {}).values()) + [doc.get_xml_metadata() if hasattr(doc, "get_xml_metadata") else ""])
    add(0, "bookmarks", [e[1] for e in doc.get_toc()])
    names = doc.embfile_names() if hasattr(doc, "embfile_names") else []
    if names:
        findings.append({"page": 0, "where": "embedded file", "term": ", ".join(names)[:80]})          # a file hidden inside the PDF is never allowed
    for i, page in enumerate(doc, start=1):
        words = _text_words(page)
        for term, _a, _b in find_runs([w[4] for w in words], terms):
            findings.append({"page": i, "where": "text", "term": term})
        add(i, "text", [page.get_text()])
        for a in (page.annots() or []):
            info = a.info or {}
            add(i, "annotation", [info.get("content"), info.get("title"), info.get("subject")])
        for w in (page.widgets() or []):
            add(i, "form field", [w.field_name, w.field_value])
        add(i, "link", [l.get("uri") or "" for l in page.get_links()])
        if _page_needs_eyes(page):                                                                          # a picture or a drawing: read what it shows
            if not ocr_ok:
                unreadable.append(i)
                continue
            pix = page.get_pixmap(dpi=150)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            ow = ocr_words(img)
            if ow is None:
                unreadable.append(i)
                continue
            for term, _a, _b in find_runs([w["text"] for w in ow], terms):
                findings.append({"page": i, "where": "picture text", "term": term})
    seen, unique = set(), []
    for f in findings:                                                                                      # the same place found twice (words and whole text) is one finding
        key = (f["page"], f["where"], fold(f["term"]))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return {"findings": unique, "pages": len(doc), "ocr_available": bool(ocr_ok), "unreadable_pages": unreadable}


def redact_pdf(data: bytes, terms: List[str], dpi: int = 170) -> Tuple[bytes, Dict[str, Any]]:
    """The PDF with every hit painted over, rebuilt from page pictures with no metadata, bookmarks, annotations, form fields or embedded files. The painter uses the SAME matcher as the scan, on the text
    layer's own words and on the words OCR reads from the rendered page. {pages, painted, ocr_available}."""
    import fitz
    from PIL import Image, ImageDraw

    try:
        src = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise RedactionError(422, "That PDF is damaged or incomplete and could not be opened.") from exc
    if src.needs_pass:
        raise RedactionError(422, "That PDF is password protected. Save an unprotected copy.")
    if len(src) > 60:
        raise RedactionError(422, "That PDF has more than 60 pages; split it first.")
    out = fitz.open()
    painted, ocr_ok = 0, ocr_available()
    scale = dpi / 72.0
    for page in src:
        pix = page.get_pixmap(dpi=dpi, annots=True)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        draw = ImageDraw.Draw(img)
        words = _text_words(page)
        for _term, a, b in find_runs([w[4] for w in words], terms):                                       # the text layer: the very words the scan matched
            for w in words[a:b + 1]:
                if fold(w[4]):
                    draw.rectangle([w[0] * scale - 3, w[1] * scale - 3, w[2] * scale + 3, w[3] * scale + 3], fill="black")
            painted += 1
        ow = ocr_words(img) if ocr_ok else None                                                             # the picture: wherever OCR reads the words
        if ow:
            for x0, y0, x1, y1, _t in _match_boxes(ow, terms):
                draw.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4], fill="black")
                painted += 1
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        np = out.new_page(width=page.rect.width, height=page.rect.height)
        np.insert_image(np.rect, stream=buf.getvalue())
    out.set_metadata({})
    return out.tobytes(garbage=4, deflate=True), {"pages": len(src), "painted": painted, "ocr_available": ocr_ok}


# ------------------------------------------------------------------ DXF

def _dxf_doc(data: bytes) -> Any:
    from app.services import drawing_dxf

    try:
        return drawing_dxf._read(data)
    except Exception as exc:  # noqa: BLE001
        raise RedactionError(422, str(getattr(exc, "message", exc)) or "That DXF could not be read.")


def _dxf_texts(doc: Any) -> List[Tuple[str, Any, str]]:
    """(where, entity, text) for every piece of text a DXF holds."""
    out: List[Tuple[str, Any, str]] = []
    layouts = list(doc.layouts) + list(doc.blocks)
    for layout in layouts:
        for e in layout:
            kind = e.dxftype()
            try:
                if kind in ("TEXT", "ATTRIB", "ATTDEF"):
                    out.append((kind.lower(), e, e.dxf.text or ""))
                elif kind == "MTEXT":
                    out.append(("mtext", e, e.text or ""))
                elif kind == "DIMENSION":
                    out.append(("dimension", e, getattr(e.dxf, "text", "") or ""))
                if kind == "INSERT":
                    for a in e.attribs:
                        out.append(("attribute", a, a.dxf.text or ""))
            except Exception:  # noqa: BLE001
                continue
    return out


def scan_dxf(data: bytes, terms: List[str]) -> Dict[str, Any]:
    doc = _dxf_doc(data)
    findings: List[Dict[str, Any]] = []
    for where, _e, text in _dxf_texts(doc):
        for t in _has(text, terms):
            findings.append({"page": 0, "where": where, "term": t})
    header = []
    for key in ("$PROJECTNAME", "$LASTSAVEDBY"):
        try:
            header.append(str(doc.header.get(key, "")))
        except Exception:  # noqa: BLE001
            pass
    try:
        header += [str(v) for _k, v in doc.header.custom_vars]
    except Exception:  # noqa: BLE001
        pass
    for t in _has(" ".join(header), terms):
        findings.append({"page": 0, "where": "header", "term": t})
    names = [str(b.name) for b in doc.blocks] + [str(l.dxf.name) for l in doc.layers]
    for t in _has(" ".join(names), terms):
        findings.append({"page": 0, "where": "layer or block name", "term": t})              # a name cannot be repainted: a person must rename it
    return {"findings": findings, "pages": 1, "ocr_available": False, "unreadable_pages": []}


def redact_dxf(data: bytes, terms: List[str]) -> Tuple[bytes, Dict[str, Any]]:
    doc = _dxf_doc(data)
    changed = 0
    pats = [re.compile(re.escape(t), re.I) for t in terms]
    for where, e, text in _dxf_texts(doc):
        new = text
        for p in pats:
            new = p.sub(MASK, new)
        if new != text:
            changed += 1
            if where == "mtext":
                e.text = new
            else:
                e.dxf.text = new
    for key in ("$PROJECTNAME", "$LASTSAVEDBY"):
        try:
            doc.header[key] = ""
        except Exception:  # noqa: BLE001
            pass
    try:
        doc.header.custom_vars.clear()
    except Exception:  # noqa: BLE001
        pass
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8"), {"pages": 1, "painted": changed, "ocr_available": False}


def scan(data: bytes, ext: str, terms: List[str]) -> Dict[str, Any]:
    return scan_pdf(data, terms) if ext == "pdf" else scan_dxf(data, terms)


def redact(data: bytes, ext: str, terms: List[str]) -> Tuple[bytes, Dict[str, Any]]:
    return redact_pdf(data, terms) if ext == "pdf" else redact_dxf(data, terms)


# ------------------------------------------------------------------ the artifact record

def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def _artifact(a: RedactedArtifactDB) -> Dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"id": a.id, "job_id": a.job_id, "source_file_id": a.source_file_id, "redacted_file_id": a.redacted_file_id, "method": a.method, "status": a.status,
            "findings_before": a.findings or [], "verified_by": a.verified_by, "verified_at": iso(a.verified_at), "attested_by": a.attested_by, "attested_at": iso(a.attested_at), "created_at": iso(a.created_at),
            "needs": {"PENDING": "A person must check this file (it cannot be cleaned automatically).", "CLEAN": "Cleaned and re-checked. A person must look at the redacted copy and verify it.",
                      "BLOCKED": "The redacted copy still showed the customer. It cannot go to a vendor.", "VERIFIED": "Ready for a vendor.", "VOID": "The file changed after it was checked."}.get(a.status, "")}


def _load(file_id: int) -> Tuple[ItemFileDB, bytes]:
    from app.services.coordination import files

    with SessionLocal() as db:
        f = db.get(ItemFileDB, file_id)
        if f is None or f.deleted_at:
            raise RedactionError(404, "That file is gone.")
        item_type, item_ref, name, stored = f.item_type, f.item_ref, f.filename, f.stored_path
    try:
        path, _n, _m = files.open_file(file_id)
        data = path.read_bytes()
    except (LookupError, OSError):
        raise RedactionError(404, "The file is no longer on the server.")
    stub = ItemFileDB(item_type=item_type, item_ref=item_ref, filename=name, stored_path=stored, sha256=hashlib.sha256(data).hexdigest())
    stub.id = file_id
    return stub, data


def check_file(job_id: int, file_id: int, user: Dict[str, Any], extra_terms: Any = ()) -> Dict[str, Any]:
    """Cleans a drawing for a vendor. PDF and DXF: the customer is painted out, the copy is scanned again, and (when clean) stored on the job as a design file waiting for a person to verify it. Anything
    else (DWG): recorded as needing an engineer's attestation. Returns the artifact."""
    from app.services.coordination import files

    f, data = _load(file_id)
    with SessionLocal() as db:
        _require_file_on_job(db, f, job_id)
    ext = _ext(f.filename)
    terms = terms_for_job(job_id, extra_terms)
    sha = hashlib.sha256(data).hexdigest()
    if ext not in AUTO_TYPES:
        findings: List[Dict[str, Any]] = []
        status, red_id, red_sha = "PENDING", None, None
    else:
        if not terms:
            raise RedactionError(422, "No customer name is known for this job, so there is nothing to look for. Link the job to its customer first.")
        before = scan(data, ext, terms)
        if _has(f.filename, terms):
            before["findings"].append({"page": 0, "where": "file name", "term": _has(f.filename, terms)[0]})
        out, report = redact(data, ext, terms)
        after = scan(out, ext, terms)
        in_page = len([f for f in before["findings"] if f["where"] in ("text", "picture text", "mtext", "attdef", "attrib", "dimension", "attribute")])
        if report.get("painted", 0) < (1 if in_page else 0) or (ext == "pdf" and report.get("painted", 0) < len({(f["page"], fold(f["term"])) for f in before["findings"] if f["where"] in ("text", "picture text")})):
            after = {**after, "findings": after["findings"] + [{"page": 0, "where": "not everything found could be painted", "term": ""}]}
        findings = before["findings"]
        # The rebuilt pages are pictures, so without OCR they cannot be read again; but a page that came from a text layer was painted exactly where the text was found. Only a page that was ALREADY a picture
        # the scan could not read is unverifiable.
        stuck = sorted(set(after["unreadable_pages"]) & set(before["unreadable_pages"]))
        clean = not after["findings"] and not stuck
        after = {**after, "unreadable_pages": stuck}
        red_id = red_sha = None
        status = "CLEAN" if clean else "BLOCKED"
        if clean:
            name = f"redacted-drawing-{sha[:8]}.{ext}"                                                # never built from the original name: that may carry the customer
            added = files.add(f.item_type, f.item_ref, name, out, user)
            red_id, red_sha = added["id"], hashlib.sha256(out).hexdigest()
            files.set_design(red_id, True, user)
        else:
            findings = findings + [{"page": p, "where": "unreadable picture page", "term": ""} for p in after["unreadable_pages"]] + after["findings"]
    with SessionLocal() as db:
        art = RedactedArtifactDB(job_id=job_id, source_file_id=file_id, source_sha256=sha, redacted_file_id=red_id, redacted_sha256=red_sha, method="AUTO", findings=findings, status=status, created_by=user.get("email"))
        db.add(art)
        db.commit()
        db.refresh(art)
        out = _artifact(art)
    audit_log(action="REDACTION_CHECKED", resource_type="REDACTED_ARTIFACT", resource_id=str(out["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"Job {job_id}, file {file_id}: {status}, {len(findings)} place(s) found")
    return out


def file_belongs_to_job(db, f: ItemFileDB, job: Any) -> bool:
    """A drawing belongs to a job when it is attached to the job itself or to the job's RFQ."""
    from app.models.db_models import RfqDB

    kind, ref = str(f.item_type or ""), str(f.item_ref or "")
    if job is None:
        return False
    if kind == "job":
        return ref in (str(job.ref), str(job.id))
    if kind == "rfq":
        rfq = db.get(RfqDB, job.rfq_id) if job.rfq_id else None
        return rfq is not None and ref == str(rfq.number)
    return False


def _require_file_on_job(db, f: ItemFileDB, job_id: int) -> None:
    from app.models.db_models import JobDB

    if not file_belongs_to_job(db, f, db.get(JobDB, job_id)):
        raise RedactionError(422, "That file is not attached to this job (or its RFQ), so it cannot be checked or attested for it.")


def may_verify(user: Dict[str, Any]) -> bool:
    from app.services import gates

    return (user.get("email") or "").strip().lower() in gates.approvers() or str(user.get("role", "")).upper() in ("MANAGER", "ADMIN")


def verify(artifact_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    """A person has looked at the redacted copy and says it shows nothing of the customer. Only a CLEAN artifact whose redacted file is unchanged can be verified; only an engineer or a manager may."""
    if not may_verify(user):
        raise RedactionError(403, "Only an engineer or a manager can verify a redacted drawing.")
    with SessionLocal() as db:
        a = db.get(RedactedArtifactDB, artifact_id)
        if a is None:
            raise RedactionError(404, "No such redaction record.")
        if a.status != "CLEAN" or not a.redacted_file_id:
            raise RedactionError(409, "Only a cleaned copy that passed the second check can be verified.")
        if not _still_valid(db, a):
            raise RedactionError(409, "The redacted file is not the one that was checked. Check the drawing again.")
        a.status, a.verified_by, a.verified_at = "VERIFIED", user.get("email"), _now()
        db.commit()
        out = _artifact(a)
    audit_log(action="REDACTION_VERIFIED", resource_type="REDACTED_ARTIFACT", resource_id=str(artifact_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail="Redacted drawing verified")
    return out


def attest(job_id: int, file_id: int, user: Dict[str, Any], statement: str = "") -> Dict[str, Any]:
    """An engineer attests that a file this cannot clean (a DWG) shows nothing of the customer. Tied to the file's hash: if the file changes, the attestation is void."""
    from app.services import gates

    if (user.get("email") or "").strip().lower() not in gates.approvers():
        raise RedactionError(403, "Only an engineer named in the approver list can attest a drawing.")
    if len((statement or "").strip()) < 8:
        raise RedactionError(422, "Write the attestation: what you checked (at least a short sentence).")
    f, data = _load(file_id)
    with SessionLocal() as db:
        _require_file_on_job(db, f, job_id)
    sha = hashlib.sha256(data).hexdigest()
    with SessionLocal() as db:
        art = RedactedArtifactDB(job_id=job_id, source_file_id=file_id, source_sha256=sha, method="ATTESTED", findings=[{"page": 0, "where": "attestation", "term": statement.strip()[:300]}], status="VERIFIED",
                                 attested_by=user.get("email"), attested_at=_now(), verified_by=user.get("email"), verified_at=_now(), created_by=user.get("email"))
        db.add(art)
        db.commit()
        db.refresh(art)
        out = _artifact(art)
    audit_log(action="REDACTION_ATTESTED", resource_type="REDACTED_ARTIFACT", resource_id=str(out["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"File {file_id} (sha {sha[:12]}) attested for vendors: {statement.strip()[:200]}")
    return out


def for_job(job_id: int) -> List[Dict[str, Any]]:
    try:
        with SessionLocal() as db:
            rows = db.query(RedactedArtifactDB).filter(RedactedArtifactDB.job_id == job_id).order_by(RedactedArtifactDB.id).all()
            out = []
            for a in rows:
                d = _artifact(a)
                if a.status == "VERIFIED" and not _still_valid(db, a):
                    d["status"], d["needs"] = "VOID", "The file changed after it was checked."
                out.append(d)
            return out
    except Exception:  # noqa: BLE001 - before the migration
        return []


def _still_valid(db, a: RedactedArtifactDB) -> bool:
    """The file on disk is byte for byte the one that was checked (its hash is worked out from the bytes now, never taken from the record: one changed byte voids it)."""
    fid, sha = (a.redacted_file_id, a.redacted_sha256) if a.method == "AUTO" else (a.source_file_id, a.source_sha256)
    f = db.get(ItemFileDB, fid) if fid else None
    if f is None or f.deleted_at or not sha:
        return False
    try:
        from pathlib import Path

        return hashlib.sha256(Path(f.stored_path).read_bytes()).hexdigest() == sha
    except OSError:
        return False


def vendor_block(file_id: Any) -> Optional[str]:
    """None when a vendor may be sent this file, else the sentence saying why not. Allowed: the redacted copy of a VERIFIED artifact (unchanged since), or a source file with an engineer's attestation
    tied to its current hash. Off only when REDACTION_ENFORCED is False."""
    if not settings.REDACTION_ENFORCED:
        return None
    try:
        fid = int(file_id)
        with SessionLocal() as db:
            f = db.get(ItemFileDB, fid)
            name = f.filename if f else f"file {fid}"
            for a in db.query(RedactedArtifactDB).filter(RedactedArtifactDB.status == "VERIFIED"):
                if ((a.method == "AUTO" and a.redacted_file_id == fid) or (a.method == "ATTESTED" and a.source_file_id == fid)) and _still_valid(db, a):
                    return None
    except Exception:  # noqa: BLE001 - if it cannot be checked it is not sent
        return "The redaction check could not run, so this drawing was not sent."
    return f"{name} has not been checked for the customer's details. Run the redaction check on it (or have an engineer attest it) before it goes to a vendor."

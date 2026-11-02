"""Checks a supplier's drawing against Allied's master drawing.

AI only *reads*: each drawing is turned into a structured list (number,
revision, materials, dimensions, welds, notes in English). The comparison of
the two lists is plain code, so the same two drawings always produce the same
difference list, and nothing here ever approves a drawing.

Missing a real difference is worse than flagging a false one, so anything the
reader could not read is reported as "unreadable", which counts as a difference.
"""

import threading
import json
import re
from collections import Counter
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import settings
from app.services.ai import store
from app.services.ai.providers import AIUnavailableError, get_provider, to_json

_MIN_TEXT_CHARS = 40
_MAX_PAGES = 4


class Dimension(BaseModel):
    label: str = Field(description="What the dimension measures, e.g. 'overall length', 'hole dia'")
    value: float
    unit: Literal["mm", "in"]
    tolerance: str = Field(
        description="Only a tolerance written right next to this dimension, e.g. '±0.5'. Blank if none. "
        "General tolerances stated in the notes do NOT go here."
    )


class DrawingSpec(BaseModel):
    drawing_number: str
    revision: str
    title: str = Field(description="Part name in English")
    materials: List[str] = Field(description="Material grades exactly as written, e.g. 'Q355B', 'ASTM A572 Gr 50'")
    dimensions: List[Dimension]
    welds: List[str] = Field(
        description="Weld callouts only (fillet, groove, plug, weld symbols), e.g. '6mm fillet both sides'. "
        "Holes, chamfers and machining callouts are not welds."
    )
    standards: List[str] = Field(description="Standards referenced, e.g. 'AWS D1.1', 'GB/T 1591'")
    notes_english: List[str] = Field(description="Every note on the drawing, translated to English")
    unreadable: List[str] = Field(description="Anything you could not read clearly")


_READ_SYSTEM = (
    "You read engineering drawings for a steel fabrication company. Copy every value exactly as it "
    "appears; never guess or round. Translate Chinese notes to English in notes_english but keep "
    "material grades and standard names as written. Put anything you cannot read clearly in unreadable. "
    "Rules: welds lists weld callouts only (fillet, groove, butt, plug, weld symbols); holes such as "
    "'2x Ø22 THRU', chamfers and machining are NOT welds. A dimension's tolerance is only one written "
    "right next to that number; a general tolerance from the notes goes in notes, not on each dimension."
)

# Small models do not always follow those rules, so the same rules are applied
# again in code after reading. Fixed rules, so the result is repeatable.
_WELD_WORDS = re.compile(r"WELD|FILLET|GROOVE|BUTT|PLUG|SEAL|BEVEL|SPOT|\bFW\b|\bCJP\b|\bPJP\b", re.I)
_HOLE_WORDS = re.compile(r"Ø|THRU|HOLE|\bDIA\b|DRILL|TAP|CSK|C'BORE", re.I)
_GENERAL_TOL = re.compile(r"UNTOLERANCED|GENERAL|UNLESS|未注|GB/T\s*1804|ISO\s*2768", re.I)


def _clean(spec: "DrawingSpec") -> "DrawingSpec":
    welds = [w for w in spec.welds if _WELD_WORDS.search(w) or not _HOLE_WORDS.search(w)]
    dims = [
        d.model_copy(update={"tolerance": ""}) if _GENERAL_TOL.search(d.tolerance or "") else d
        for d in spec.dimensions
    ]
    return spec.model_copy(update={"welds": welds, "dimensions": dims})


# PyMuPDF is not thread-safe, and PDFs are read from request threads and the mailbox
# poller at once; every use in the app goes through this lock.
PDF_LOCK = threading.Lock()


def pdf_text(data: bytes) -> List[str]:
    import pymupdf

    with PDF_LOCK, pymupdf.open(stream=data, filetype="pdf") as doc:
        return [page.get_text() for page in list(doc)[:_MAX_PAGES]]


# Longest side of a rendered page. A large-format (or crafted) page at 150 dpi
# would otherwise allocate a huge pixmap; such pages render at a lower dpi.
_MAX_RENDER_PX = 5000


def _page_dpi(page, dpi: int = 150) -> int:
    longest_pt = max(page.rect.width, page.rect.height, 1)
    return max(36, min(dpi, int(_MAX_RENDER_PX * 72 / longest_pt)))


def pdf_images(data: bytes) -> List[bytes]:
    import pymupdf

    with PDF_LOCK, pymupdf.open(stream=data, filetype="pdf") as doc:
        return [page.get_pixmap(dpi=_page_dpi(page)).tobytes("png") for page in list(doc)[:_MAX_PAGES]]


def read_drawing(data: bytes, filename: str) -> Tuple[DrawingSpec, str]:
    """Returns the spec and how it was read ('text' or 'image')."""
    provider = get_provider()
    is_pdf = filename.lower().endswith(".pdf")
    text = "\n".join(pdf_text(data)) if is_pdf else ""
    pages = _page_count(data) if is_pdf else 1
    if len(text.strip()) >= _MIN_TEXT_CHARS:
        from app.services.ai.features import fence

        fenced, rule = fence(text[:30000])
        prompt = f"Drawing file: {filename}\nText extracted from the drawing:\n{fenced}"
        spec = _clean(provider.structured(_READ_SYSTEM + rule, prompt, DrawingSpec))
        return _unread_pages(spec, pages, _MAX_PAGES), "text"
    if not provider.can_see_images:
        raise AIUnavailableError(
            f"{filename} has no readable text (it is a scan or image). Reading it needs a vision model: "
            "set GROQ_VISION_MODEL (Groq), or `ollama pull qwen2.5vl:7b` and AI_VISION_MODEL (Ollama)."
        )
    images = pdf_images(data) if is_pdf else [data]
    prompt = (
        f"Drawing file: {filename}. This is a scanned engineering drawing. Read it carefully:\n"
        "1. The title block: drawing number, revision, title, material, standards.\n"
        "2. Every dimension number on the drawing, with its tolerance if one is written next to it. "
        "Units are mm unless the drawing says otherwise.\n"
        "3. Every weld callout.\n"
        "4. Every note, translated to English (notes may be in Chinese).\n"
        "Anything blurred or unclear goes in unreadable; never guess a number."
    )
    spec = _clean(provider.structured(_READ_SYSTEM, prompt, DrawingSpec, images=images[:_VISION_PAGES]))
    return _unread_pages(spec, pages, _VISION_PAGES), "image"


_VISION_PAGES = 3  # Groq's vision model takes at most 3 images per request


def _page_count(data: bytes) -> int:
    try:
        import pymupdf

        with PDF_LOCK, pymupdf.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001 - unknown count: nothing extra to report
        return 1


def _unread_pages(spec: "DrawingSpec", pages: int, read: int) -> "DrawingSpec":
    """A page not read is a page not checked, which must count as a difference, not silence."""
    if pages <= read:
        return spec
    note = f"Pages {read + 1} to {pages} were not read (only {read} at a time); check them by eye."
    return spec.model_copy(update={"unreadable": list(spec.unreadable) + [note]})


# ---------------------------------------------------------------------------
# Deterministic comparison
# ---------------------------------------------------------------------------


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9.]", "", (value or "").upper())


def grade_map() -> Dict[str, str]:
    """Approved Chinese GB -> ASTM grade pairs. Unapproved rows are ignored."""
    path = settings.AI_GRADE_MAP_PATH or (settings.DATA_DIR / "material_grade_map.json")
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("mappings", [])
    except (FileNotFoundError, ValueError):
        return {}
    return {_norm(r["gb"]): r["astm"] for r in rows if r.get("approved") and r.get("gb") and r.get("astm")}


_NUMBER = re.compile(r"\d*\.?\d+")


def _tol(text: str, unit: str = "mm") -> str:
    """A tolerance in mm, so '+/-1 mm', '±1' and '±0.03937 in' compare equal.

    A tolerance written without a unit is in the dimension's unit.
    """
    t = (text or "").upper().replace("+/-", "±").replace("+-", "±")
    if not t.strip():
        return ""
    if re.search(r"(?<![A-Z])IN\b|INCH|\"", t):
        unit = "in"
    elif "MM" in t:
        unit = "mm"
    t = re.sub(r"\s|MM|INCHES|INCH|(?<![A-Z])IN\b|\"", "", t)
    factor = 25.4 if unit == "in" else 1.0
    return _NUMBER.sub(lambda m: f"{round(float(m.group()) * factor, 3):g}", t)


def _mm(d: Dimension) -> float:
    # 0.01 mm: fine enough that a 10.04 -> 10.0 change is reported, coarse
    # enough that an inch value converted to mm (39.37 in) still matches.
    return round(d.value * 25.4 if d.unit == "in" else d.value, 2)


def _row(field: str, master: Any, supplier: Any, status: str, note: str = "") -> Dict[str, Any]:
    return {"field": field, "master": master, "supplier": supplier, "status": status, "note": note}


def _compare_text(field: str, a: str, b: str) -> Dict[str, Any]:
    if not a or not b:
        return _row(field, a or "(not found)", b or "(not found)", "unreadable")
    return _row(field, a, b, "match" if _norm(a) == _norm(b) else "different")


def compare(master: DrawingSpec, supplier: DrawingSpec) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = [
        _compare_text("Drawing number", master.drawing_number, supplier.drawing_number),
        _compare_text("Revision", master.revision, supplier.revision),
    ]

    # Materials: translate approved Chinese grades, then compare as sets.
    gmap = grade_map()
    master_mats = {_norm(m) for m in master.materials}
    unmapped = []
    supplier_mats = set()
    for m in supplier.materials:
        key = _norm(m)
        if key in gmap:
            supplier_mats.add(_norm(gmap[key]))
        else:
            supplier_mats.add(key)
            if re.match(r"^Q\d{3}", key):
                unmapped.append(m)
    if not master.materials or not supplier.materials:
        rows.append(_row("Material", master.materials, supplier.materials, "unreadable"))
    else:
        rows.append(_row(
            "Material", master.materials, supplier.materials,
            "match" if master_mats == supplier_mats else "different",
            f"Chinese grade(s) not in the approved grade table: {', '.join(unmapped)}" if unmapped else "",
        ))

    # Dimensions: every master value must appear on the supplier drawing, and
    # nothing extra may appear. Compared in mm to 0.01, regardless of labels,
    # because two drafters rarely label the same dimension the same way.
    m_dims = Counter(_mm(d) for d in master.dimensions)
    s_dims = Counter(_mm(d) for d in supplier.dimensions)
    missing = sorted((m_dims - s_dims).elements())
    extra = sorted((s_dims - m_dims).elements())
    if not master.dimensions or not supplier.dimensions:
        rows.append(_row("Dimensions", len(master.dimensions), len(supplier.dimensions), "unreadable"))
    else:
        rows.append(_row(
            "Dimensions", f"{sum(m_dims.values())} values", f"{sum(s_dims.values())} values",
            "match" if not (missing or extra) else "different",
            "; ".join(filter(None, [
                f"On master but not supplier (mm): {', '.join(map(str, missing))}" if missing else "",
                f"On supplier but not master (mm): {', '.join(map(str, extra))}" if extra else "",
            ])),
        ))
    # Tolerances are compared per dimension that appears on both drawings, so
    # a general tolerance note does not read as a change to every dimension.
    # The same value can appear more than once with different tolerances, so
    # every tolerance at a value is kept and the lists are compared.
    m_tol: Dict[float, List[str]] = {}
    s_tol: Dict[float, List[str]] = {}
    for dims, by_value in ((master.dimensions, m_tol), (supplier.dimensions, s_tol)):
        for d in dims:
            by_value.setdefault(_mm(d), []).append(_tol(d.tolerance, d.unit))
    tol_diffs = []
    for value in sorted(m_tol.keys() & s_tol.keys()):
        m, s = sorted(m_tol[value]), sorted(s_tol[value])
        # When the counts differ the Dimensions row already reports it; the
        # tolerances then differ only if they are not the same set.
        same = m == s if len(m) == len(s) else set(m) == set(s)
        if not same:
            shown = lambda ts: ", ".join(t or "none" for t in ts)  # noqa: E731
            tol_diffs.append(f"{value} mm: {shown(m)} on master, {shown(s)} on supplier")
    rows.append(_row(
        "Tolerances",
        f"{sum(1 for d in master.dimensions if d.tolerance)} stated",
        f"{sum(1 for d in supplier.dimensions if d.tolerance)} stated",
        "different" if tol_diffs else "match", "; ".join(tol_diffs),
    ))

    m_welds = sorted(_norm(w) for w in master.welds)
    s_welds = sorted(_norm(w) for w in supplier.welds)
    rows.append(_row(
        "Welds", master.welds, supplier.welds,
        "match" if m_welds == s_welds else "different",
        "" if len(m_welds) == len(s_welds) else f"{len(m_welds)} on master, {len(s_welds)} on supplier",
    ))

    m_std = {_norm(s) for s in master.standards}
    s_std = {_norm(s) for s in supplier.standards}
    rows.append(_row("Standards", master.standards, supplier.standards, "match" if m_std == s_std else "different"))

    for side, spec in (("master", master), ("supplier", supplier)):
        if spec.unreadable:
            rows.append(_row(f"Unreadable on {side}", "", "", "unreadable", "; ".join(spec.unreadable)))

    differences = sum(1 for r in rows if r["status"] != "match")
    return {
        "rows": rows,
        "differences": differences,
        # Never "approved": an engineer signs off.
        "verdict": "differences_found" if differences else "no_differences_found",
        "supplier_notes_english": supplier.notes_english,
        "master_notes": master.notes_english,
    }


def check_drawings(master: Tuple[bytes, str], supplier: Tuple[bytes, str], actor: str = "system", reference: Optional[str] = None):
    import time

    provider = get_provider()
    started = time.perf_counter()
    master_spec, master_mode = read_drawing(*master)
    supplier_spec, supplier_mode = read_drawing(*supplier)
    result = compare(master_spec, supplier_spec)
    result.update({
        "master": to_json(master_spec),
        "supplier": to_json(supplier_spec),
        "read_as": {"master": master_mode, "supplier": supplier_mode},
    })
    suggestion_id = store.record(
        "drawing_check",
        f"{master[1]} vs {supplier[1]}: {result['differences']} difference(s)",
        provider=provider.name,
        model=settings.AI_VISION_MODEL if "image" in (master_mode, supplier_mode) else provider.model,
        input={"master_file": master[1], "supplier_file": supplier[1]},
        output=result,
        needs_review=True,  # always: an engineer signs off every drawing
        reference=reference or master_spec.drawing_number,
        duration_ms=int((time.perf_counter() - started) * 1000),
        actor=actor,
    )
    return result, suggestion_id

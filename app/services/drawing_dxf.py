"""Reading a DXF CAD drawing for comparison: a picture of it, its text, and the real-world measurements in millimetres.

A DXF holds the actual lines, circles, text and dimensions, so unlike a PDF it can be compared by what is in it: the overall
size, each hole's diameter, every dimension value, in one unit (millimetres), whatever units the drawing was made in. Any DXF
version is read; a slightly damaged file is repaired where possible. (DWG is a closed format and cannot be read here.)
"""

import math
import os
import tempfile
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw

# $INSUNITS code -> millimetres per drawing unit
_UNITS = {1: ("in", 25.4), 2: ("ft", 304.8), 4: ("mm", 1.0), 5: ("cm", 10.0), 6: ("m", 1000.0), 10: ("yd", 914.4), 13: ("um", 0.001)}
MAX_ENTITIES = 400_000
RENDER_PX = 1700


class DxfError(ValueError):
    """Something a person can read about why this DXF can't be used."""


def _units(doc: Any) -> Tuple[str, float, bool]:
    """(unit name, millimetres per unit, whether the drawing said so). Falls back to its metric/imperial setting."""
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code in _UNITS:
        name, factor = _UNITS[code]
        return name, factor, True
    measurement = doc.header.get("$MEASUREMENT")
    if measurement == 0:
        return "in", 25.4, False
    if measurement == 1:
        return "mm", 1.0, False
    return "unknown", 1.0, False


def _read(data: bytes) -> Any:
    import ezdxf
    from ezdxf import recover

    fd, path = tempfile.mkstemp(suffix=".dxf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            return ezdxf.readfile(path)
        except Exception:  # noqa: BLE001 - try to repair a damaged file before giving up
            try:
                doc, _ = recover.readfile(path)
                return doc
            except Exception as exc:  # noqa: BLE001
                raise DxfError(f"This isn't a DXF I can read ({type(exc).__name__}). Save it again as DXF from your CAD program.")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _flatten(entity: Any) -> List[List[Tuple[float, float]]]:
    """An entity as one or more runs of points (curves are sampled finely enough to draw)."""
    from ezdxf import path as ezpath

    try:
        p = ezpath.make_path(entity)
    except Exception:  # noqa: BLE001 - an entity type with no outline (a point, a viewport): nothing to draw
        return []
    runs: List[List[Tuple[float, float]]] = []
    for sub in p.sub_paths():
        pts = [(v.x, v.y) for v in sub.flattening(0.25)]
        if len(pts) >= 2:
            runs.append(pts)
    return runs


def read(data: bytes) -> Dict[str, Any]:
    """{image, text, units, size_mm, circles_mm, dimensions_mm, counts, entities} for a DXF file."""
    doc = _read(data)
    name, factor, stated = _units(doc)
    runs: List[List[Tuple[float, float]]] = []
    texts: List[str] = []
    text_marks: List[Tuple[float, float, float, int, float]] = []
    dims: List[float] = []
    circles: List[float] = []
    counts: Dict[str, int] = {}
    n = 0

    def walk(entities: Any, depth: int = 0) -> None:
        nonlocal n
        for e in entities:
            n += 1
            if n > MAX_ENTITIES:
                raise DxfError(f"This drawing has more than {MAX_ENTITIES:,} items: too big to compare here. Export a PDF instead.")
            kind = e.dxftype()
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "INSERT":
                for att in getattr(e, "attribs", []) or []:
                    value = (att.dxf.text or "").strip()
                    if value:
                        texts.append(f"{att.dxf.tag}: {value}")
                if depth < 4:
                    try:
                        walk(e.virtual_entities(), depth + 1)
                    except Exception:  # noqa: BLE001 - a broken block reference is skipped, the rest still compares
                        pass
                continue
            if kind in ("TEXT", "MTEXT"):
                s = (e.plain_text() if kind == "MTEXT" else e.dxf.text or "").strip()
                if s:
                    texts.append(s)
                    ins = e.dxf.insert
                    height = float(getattr(e.dxf, "height", 0) or getattr(e.dxf, "char_height", 0) or 2.5)
                    angle = float(getattr(e.dxf, "rotation", 0) or 0) if kind == "TEXT" else float(e.get_rotation() or 0)
                    text_marks.append((ins.x, ins.y, height, len(s), angle))
                continue
            if kind == "DIMENSION":
                try:
                    m = float(e.dxf.get("actual_measurement", 0) or 0)
                except (TypeError, ValueError):
                    m = 0.0
                if m:
                    dims.append(m)
                shown = (e.dxf.get("text", "") or "").strip()
                if shown and shown != "<>":
                    texts.append(shown)
                continue
            if kind == "CIRCLE":
                circles.append(float(e.dxf.radius) * 2)
            runs.extend(_flatten(e))

    walk(doc.modelspace())
    body = [p for run in runs for p in run]
    pts = body + [(x, y) for x, y, _, _, _ in text_marks]
    if not pts:
        raise DxfError("This DXF has no lines or text in its model space (the drawing may be on a layout sheet only). Export it as PDF.")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    # A stray point far from the drawing would shrink everything: size is taken from the middle 99.8% of points.
    lo_x, hi_x = sorted(xs)[int(len(xs) * 0.001)], sorted(xs)[int(len(xs) * 0.999)]
    lo_y, hi_y = sorted(ys)[int(len(ys) * 0.001)], sorted(ys)[int(len(ys) * 0.999)]
    w, h = max(hi_x - lo_x, 1e-9), max(hi_y - lo_y, 1e-9)
    if body:                                                       # the part itself, ignoring notes and dimension text around it
        bx, by = sorted(p[0] for p in body), sorted(p[1] for p in body)
        part_w = max(bx[int(len(bx) * 0.999)] - bx[int(len(bx) * 0.001)], 1e-9)
        part_h = max(by[int(len(by) * 0.999)] - by[int(len(by) * 0.001)], 1e-9)
    else:
        part_w, part_h = w, h
    scale = RENDER_PX / max(w, h)
    img = Image.new("L", (int(w * scale) + 21, int(h * scale) + 21), 255)
    draw = ImageDraw.Draw(img)
    for run in runs:
        xy = [(10 + (x - lo_x) * scale, 10 + (hi_y - y) * scale) for x, y in run]
        draw.line(xy, fill=0, width=1)
    for x, y, height, length, angle in text_marks:                # text as a thin box where it sits (turned as the text is turned)
        wid, hgt = max(length, 1) * height * 0.55, height
        c, s_ = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        corners = [(0, 0), (wid, 0), (wid, hgt), (0, hgt), (0, 0)]
        xy = [(10 + (x + dx * c - dy * s_ - lo_x) * scale, 10 + (hi_y - (y + dx * s_ + dy * c)) * scale) for dx, dy in corners]
        draw.line(xy, fill=0, width=1)
    return {
        "image": img, "text": "\n".join(texts), "units": name, "units_stated": stated, "mm_per_unit": factor,
        "size_mm": [round(part_w * factor, 2), round(part_h * factor, 2)], "circles_mm": sorted(round(c * factor, 2) for c in circles),
        "dimensions_mm": sorted(round(d * factor, 3) for d in dims), "counts": counts, "entities": n,
    }


# ---------------------------------------------------------------------------
# Title block: exact fields from the attributes of the title block insert (no AI)
# ---------------------------------------------------------------------------

_TB_FIELDS = {
    "drawing_number": ("dwg_no", "dwgno", "drawing_no", "drawing_number", "drawingnumber", "number", "part_no", "partno", "dwg", "sheet_no"),
    "revision": ("rev", "revision", "rev_no", "revno"),
    "material": ("material", "mat", "matl"),
    "customer": ("customer", "client", "company", "customer_name", "owner"),
    "title": ("title", "description", "desc", "part_name", "name"),
    "scale": ("scale",),
    "drawn_by": ("drawn", "drawn_by", "drawnby", "designer"),
    "date": ("date", "drawn_date"),
}


def title_block(data: bytes) -> Dict[str, Any]:
    """{attributes: {tag: text}, fields: {drawing_number, revision, material, customer, title, scale, drawn_by, date}, customer_found}: the title block's own attribute text, read exactly (multi-line text keeps its
    lines). A field the file does not carry is simply absent: nothing is guessed. The customer field is what the redaction check looks for."""
    doc = _read(data)
    attrs: Dict[str, str] = {}
    for layout in list(doc.layouts):
        for e in layout:
            if e.dxftype() == "INSERT":
                for a in e.attribs:
                    tag = str(a.dxf.tag or "").strip()
                    text = str(a.dxf.text or "").replace("\P", "\n").strip()
                    if tag and text and tag.lower() not in {k.lower() for k in attrs}:
                        attrs[tag] = text
    lower = {k.lower().replace(" ", "_"): v for k, v in attrs.items()}
    fields: Dict[str, str] = {}
    for name, tags in _TB_FIELDS.items():
        for t in tags:
            if t in lower:
                fields[name] = lower[t]
                break
    return {"attributes": attrs, "fields": fields, "customer_found": "customer" in fields}

"""Compare two CAD drawings: what changed between a customer's drawing and the vendor's, or between revisions.

Built for Allied's two in-house engineers, who compare drawings constantly (many drawings come from China, where the
drawing conventions differ from the US ones). It does not need the AI and sends nothing anywhere. It gives:
  * facts read from each drawing: drawing number, revision, material, scale, units (mm or inches), the projection
    (first-angle, common in China, or third-angle, common in the US), the standards named, and any Chinese drawing
    terms found with their English meaning;
  * warnings about the differences that cause real mistakes: different units, first-angle against third-angle, a
    different revision or material, a page sent sideways;
  * the words and numbers that are on one drawing and not the other;
  * a picture: both drawings laid over each other, red = only on the first, blue = only on the second, grey = the same,
    with the changed areas listed. A drawing that was sent sideways is turned to match, and margins or position
    differences are ignored (each drawing is cropped to its own content first).
Nothing is changed or saved. Translation of the Chinese text is on request (the Translate tool), never added by itself.
"""

import base64
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageFilter, ImageOps

from app.core.logging import logger

CANVAS = 1100                 # longest side, in pixels, both drawings are fitted to
MAX_PAGE_PX = 4000
_CJK = re.compile(r"[⺀-鿿]")

# Chinese terms found on drawings, with what they mean. Only terms that are standard on drawings.
ZH_TERMS = {
    "图号": "drawing number", "图名": "drawing title", "名称": "part name", "材料": "material", "材质": "material",
    "比例": "scale", "公差": "tolerance", "未注公差": "general tolerances (where not otherwise shown)", "技术要求": "technical requirements",
    "热处理": "heat treatment", "表面处理": "surface treatment", "数量": "quantity", "设计": "designed by", "制图": "drawn by",
    "审核": "checked by", "批准": "approved by", "日期": "date", "重量": "weight", "版本": "revision", "版次": "revision",
    "单位": "unit", "毫米": "millimetres", "第一角投影": "first-angle projection", "第三角投影": "third-angle projection",
    "焊接": "welding", "焊缝": "weld seam", "硬度": "hardness", "倒角": "chamfer", "圆角": "fillet / radius", "粗糙度": "surface roughness",
    "去毛刺": "deburr", "锐边倒钝": "break sharp edges", "喷漆": "paint", "镀锌": "galvanised", "调质": "quenched and tempered",
}


class CompareError(ValueError):
    """Something a person can read about why two drawings can't be compared."""


# What can really be read here: PDFs (exported or printed from CAD), pictures, and DXF. DWG is a closed format with no
# free reader; STEP and IGES are 3D models, which need a different kind of comparison. Each gets a plain answer.
SUPPORTED = "PDF, PNG, JPG, TIFF or DXF"


def _format_problem(data: bytes, name: str, label: str) -> str:
    head = data[:300]
    if name.endswith(".dwg") or head[:4] == b"AC10":
        return (f"{label} is a DWG file, and DWG can't be opened here (it is a closed format). In your CAD program use Save As or Export to "
                f"DXF (any version), or print to PDF, then compare that. Formats that work: {SUPPORTED}.")
    if name.endswith((".step", ".stp", ".igs", ".iges")) or b"ISO-10303" in head or (len(data) > 80 and data[72:73] == b"S" and b"IGES" in head.upper()):
        return (f"{label} is a STEP/IGES 3D model, which can't be compared here yet. Export the drawing views from it as DXF or PDF "
                f"(a 2D drawing of the part), then compare those. Formats that work: {SUPPORTED}.")
    return ""


# ----------------------------------------------------------------------------- reading one drawing

def _load(data: bytes, filename: str, page: int) -> Tuple[Image.Image, str, Dict[str, Any]]:
    """The page as a grey picture, its text, and a few facts about the file."""
    if not data:
        raise CompareError(f"{filename or 'A file'} is empty.")
    name = (filename or "").lower()
    meta: Dict[str, Any] = {"filename": filename, "pages": 1, "page": page}
    problem = _format_problem(data, name, filename or "A file")
    if problem:
        raise CompareError(problem)
    if name.endswith(".dxf") or (b"SECTION" in data[:400] and not data.startswith(b"%PDF")):
        from app.services import drawing_dxf

        try:
            dxf = drawing_dxf.read(data)
        except drawing_dxf.DxfError as exc:
            raise CompareError(f"{filename or 'A file'}: {exc}")
        meta.update(format="dxf", geometry={k: dxf[k] for k in ("units", "units_stated", "size_mm", "circles_mm", "dimensions_mm", "counts", "entities")})
        meta["text_chars"] = len(dxf["text"])
        return dxf["image"], dxf["text"], meta
    if data.startswith(b"%PDF") or name.endswith(".pdf"):
        try:
            import pymupdf

            from app.services.ai.drawings import PDF_LOCK

            with PDF_LOCK, pymupdf.open(stream=data, filetype="pdf") as doc:
                if doc.needs_pass:
                    raise CompareError(f"{filename} is password protected.")
                meta["pages"] = len(doc)
                if not 1 <= page <= len(doc):
                    raise CompareError(f"{filename} has {len(doc)} page(s); there is no page {page}.")
                pg = doc[page - 1]
                meta["page_size_pt"] = [round(pg.rect.width), round(pg.rect.height)]
                meta["page_rotation"] = pg.rotation
                text = pg.get_text() or ""
                longest = max(pg.rect.width, pg.rect.height, 1)
                dpi = max(36, min(110, int(MAX_PAGE_PX * 72 / longest)))
                pix = pg.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        except CompareError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CompareError(f"{filename or 'A file'} could not be read as a drawing ({type(exc).__name__}).")
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:  # noqa: BLE001
            raise CompareError(f"{filename or 'A file'} is not a PDF or a picture I can open.")
        img = ImageOps.exif_transpose(img).convert("L")
        text = ""
    if len(re.sub(r"\s", "", text)) < 40:
        text = _ocr(img) or text
    meta["text_chars"] = len(text)
    return img, text, meta


def _ocr(img: Image.Image) -> str:
    """Words from a picture-only drawing, when OCR is installed; empty (not an error) when it isn't."""
    try:
        from app.services import document_scanner_service as _unused  # noqa: F401
        import importlib

        dss = importlib.import_module("app.services.document_scanner_service")
        if dss.init_tesseract() and dss.pytesseract is not None:
            return dss.ocr_upright(img)
    except Exception as exc:  # noqa: BLE001
        logger.info(f"Drawing OCR unavailable ({type(exc).__name__}).")
    return ""


def facts(text: str) -> Dict[str, Any]:
    """What the drawing says about itself, in the words engineers care about."""
    t = text or ""
    up = t.upper()

    def first(*pats: str) -> Optional[str]:
        for p in pats:
            m = re.search(p, t, re.IGNORECASE)
            if m and m.group(1).strip():
                return m.group(1).strip().strip(":：,;")
        return None

    mm = len(re.findall(r"\bMM\b|MILLIMET|毫米|单位\s*[:：]?\s*mm", t, re.IGNORECASE))
    inch = len(re.findall(r"\bINCH|\bINCHES\b|\bIN\.|\bIN\b(?=\s*[\)\.,;]|\s*$)|\d\s*\"", t, re.IGNORECASE))
    units = "mm" if mm > inch else "in" if inch > mm else "unknown"
    projection = ("first-angle" if re.search(r"FIRST[\s\-]*ANGLE|第一角", t, re.IGNORECASE)
                  else "third-angle" if re.search(r"THIRD[\s\-]*ANGLE|第三角", t, re.IGNORECASE) else "not stated")
    standards = sorted({m.upper().replace(" ", "") for m in re.findall(r"\b(?:GB/?T?\s?\d{2,5}|ASME\s?Y14\.?\d*|ASTM\s?[A-Z]?\d{2,4}|AWS\s?D1\.?\d|ISO\s?\d{3,5}|EN\s?\d{3,5})", up)})
    zh = [{"zh": k, "en": v} for k, v in ZH_TERMS.items() if k in t]
    return {
        "drawing_number": first(r"(?:DWG|DRAWING)\s*(?:NO\.?|NUMBER|#)\s*[:：\-]?\s*([A-Z0-9][A-Z0-9\-_/\.]{2,})", r"图号\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/\.]{2,})"),
        "revision": first(r"\bREV(?:ISION)?\.?\s*[:：\-]?\s*([A-Z0-9]{1,3})\b", r"(?:版本|版次)\s*[:：]?\s*([A-Za-z0-9]{1,3})"),
        "material": first(r"MATERIAL\s*[:：\-]?\s*([^\n\r]{2,40})", r"(?:材料|材质)\s*[:：]?\s*([^\n\r]{2,30})"),
        "scale": first(r"SCALE\s*[:：\-]?\s*(\d+\s*[:/]\s*\d+|\d+(?:\.\d+)?\s*[xX])", r"比例\s*[:：]?\s*(\d+\s*[:/]\s*\d+)"),
        "units": units, "projection": projection, "standards": standards, "has_chinese": bool(_CJK.search(t)), "chinese_terms": zh,
    }


# ----------------------------------------------------------------------------- the two drawings side by side

def _content_all(img: Image.Image) -> Image.Image:
    box = ImageOps.invert(img).point(lambda p: 255 if p > 60 else 0).getbbox()
    return img.crop(box) if box else img


def _content(img: Image.Image) -> Image.Image:
    """The drawing without its blank margins, so a different page size or position doesn't read as a change."""
    return _content_all(img)


def _fit(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    img = _content(img)
    scale = min(size[0] / img.width, size[1] / img.height)
    resized = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    canvas = Image.new("L", size, 255)
    canvas.paste(resized, ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2))
    return canvas


def _canvas_size(img: Image.Image) -> Tuple[int, int]:
    c = _content(img)
    scale = CANVAS / max(c.width, c.height)
    return max(1, int(c.width * scale)), max(1, int(c.height * scale))


def _best_turn(a: Image.Image, b: Image.Image) -> Tuple[Image.Image, int]:
    """Turn the second drawing the way that matches the first best (a drawing sent sideways or upside down)."""
    size = _canvas_size(a)
    base = _fit(a, size).resize((96, 96))
    best, best_angle, best_score = b, 0, None
    for angle in (0, 90, 180, 270):
        turned = b if angle == 0 else b.rotate(angle, expand=True, fillcolor=255)
        probe = _fit(turned, size).resize((96, 96))
        score = sum(ImageChops.difference(base, probe).histogram()[i] * i for i in range(256))
        if best_score is None or score < best_score * 0.9:           # a turn must be clearly better than leaving it
            best, best_angle, best_score = turned, angle, score
    return best, best_angle


def _regions(only_a: Image.Image, only_b: Image.Image, size: Tuple[int, int]) -> List[Dict[str, Any]]:
    """Where the changes are: boxes (as % of the drawing) around each cluster of changed pixels, biggest first."""
    cell = 22
    gw, gh = size[0] // cell + 1, size[1] // cell + 1
    changed: Dict[Tuple[int, int], List[int]] = {}
    for which, mask in ((0, only_a), (1, only_b)):
        small = mask.resize((gw, gh), Image.BOX)
        px = small.load()
        for y in range(gh):
            for x in range(gw):
                if px[x, y] > 6:
                    changed.setdefault((x, y), [0, 0])[which] += px[x, y]
    seen, boxes = set(), []
    for start in changed:
        if start in seen:
            continue
        stack, cells = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            cells.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cx + dx, cy + dy)
                    if n in changed and n not in seen:
                        seen.add(n)
                        stack.append(n)
        xs, ys = [c[0] for c in cells], [c[1] for c in cells]
        a_w = sum(changed[c][0] for c in cells)
        b_w = sum(changed[c][1] for c in cells)
        boxes.append({"x": round(100 * min(xs) * cell / size[0], 1), "y": round(100 * min(ys) * cell / size[1], 1),
                      "w": round(100 * ((max(xs) - min(xs) + 1) * cell) / size[0], 1), "h": round(100 * ((max(ys) - min(ys) + 1) * cell) / size[1], 1),
                      "weight": a_w + b_w, "what": "only on the first" if a_w > 1.5 * b_w else "only on the second" if b_w > 1.5 * a_w else "changed"})
    boxes.sort(key=lambda r: -r["weight"])
    for r in boxes:
        r.pop("weight")
    return boxes[:15]


def _overlay(a: Image.Image, b: Image.Image) -> Tuple[Image.Image, float, List[Dict[str, Any]]]:
    size = _canvas_size(a)
    fa, fb = _fit(a, size), _fit(b, size)
    dark_a, dark_b = fa.point(lambda p: 255 if p < 170 else 0), fb.point(lambda p: 255 if p < 170 else 0)
    near_a, near_b = dark_a.filter(ImageFilter.MaxFilter(5)), dark_b.filter(ImageFilter.MaxFilter(5))   # 2 px of slack for a slightly different position
    only_a = ImageChops.subtract(dark_a, near_b)
    only_b = ImageChops.subtract(dark_b, near_a)
    same = ImageChops.multiply(near_a, near_b).filter(ImageFilter.MinFilter(3))
    out = Image.new("RGB", size, (255, 255, 255))
    out.paste((190, 190, 190), mask=ImageChops.multiply(dark_a, near_b).point(lambda p: 255 if p else 0))
    out.paste((190, 190, 190), mask=ImageChops.multiply(dark_b, near_a).point(lambda p: 255 if p else 0))
    out.paste((215, 40, 40), mask=only_a)
    out.paste((40, 90, 230), mask=only_b)
    total = sum(dark_a.histogram()[255:]) + sum(dark_b.histogram()[255:])
    changed = sum(only_a.histogram()[255:]) + sum(only_b.histogram()[255:])
    del same
    return out, (changed / total if total else 0.0), _regions(only_a, only_b, size)


# ----------------------------------------------------------------------------- the comparison

def _lines(text: str) -> List[str]:
    out = []
    for raw in (text or "").splitlines():
        t = re.sub(r"\s+", " ", raw).strip().casefold()
        if len(t) >= 2 and not t.startswith("--- page"):
            out.append(t)
    return out


def _numbers(text: str) -> List[str]:
    return sorted(set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text or "")), key=lambda s: (len(s), s))


def _only(a: List[float], b: List[float], tol: float, relative: bool = False) -> Tuple[List[float], List[float]]:
    """Values in a with no match in b, and in b with no match in a (each match used once), within a tolerance."""
    pool = list(b)
    missing = []
    for x in a:
        near = next((i for i, y in enumerate(pool) if abs(x - y) <= (tol * max(abs(x), 1e-9) if relative else tol)), None)
        if near is None:
            missing.append(x)
        else:
            pool.pop(near)
    return missing, pool


def _geometry(ga: Dict[str, Any], gb: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    """Two DXFs compared by what is in them, with every size converted to millimetres first."""
    notes = []
    if ga["units"] != gb["units"]:
        names = {"in": "inches", "mm": "millimetres", "cm": "centimetres", "m": "metres", "ft": "feet", "unknown": "no stated unit"}
        notes.append(f"The first drawing was made in {names.get(ga['units'], ga['units'])} and the second in {names.get(gb['units'], gb['units'])}; "
                     "sizes below are all converted to millimetres.")
    for side, g in (("first", ga), ("second", gb)):
        if not g["units_stated"]:
            warnings.append(f"The {side} DXF doesn't state its units, so I assumed {g['units']} from its metric/imperial setting; check the sizes.")
    wa, ha = sorted(ga["size_mm"], reverse=True)
    wb, hb = sorted(gb["size_mm"], reverse=True)
    size_changed = abs(wa - wb) > 0.005 * max(wa, wb) or abs(ha - hb) > 0.005 * max(ha, hb)
    if size_changed:
        warnings.append(f"Different overall size: {ga['size_mm'][0]} x {ga['size_mm'][1]} mm against {gb['size_mm'][0]} x {gb['size_mm'][1]} mm "
                        f"(the picture is scaled to match, so shapes can still be compared).")
    holes_a, holes_b = _only(ga["circles_mm"], gb["circles_mm"], 0.05)
    dims_a, dims_b = _only(ga["dimensions_mm"], gb["dimensions_mm"], 0.001, relative=True)
    return {"notes": notes, "size_mm": {"first": ga["size_mm"], "second": gb["size_mm"], "changed": size_changed},
            "circle_diameters_mm": {"only_on_first": holes_a[:60], "only_on_second": holes_b[:60]},
            "dimensions_mm": {"only_on_first": dims_a[:80], "only_on_second": dims_b[:80]},
            "item_counts": {"first": ga["counts"], "second": gb["counts"]}}


def compare(data_a: bytes, name_a: str, data_b: bytes, name_b: str, page_a: int = 1, page_b: int = 1) -> Dict[str, Any]:
    img_a, text_a, meta_a = _load(data_a, name_a, page_a)
    img_b, text_b, meta_b = _load(data_b, name_b, page_b)
    if not _content(img_a).getbbox() or ImageOps.invert(img_a).getbbox() is None:
        raise CompareError(f"{name_a or 'The first file'} looks blank.")
    if ImageOps.invert(img_b).getbbox() is None:
        raise CompareError(f"{name_b or 'The second file'} looks blank.")
    turned_b, angle = _best_turn(img_a, img_b)
    overlay, changed, regions = _overlay(img_a, turned_b)

    fa, fb = facts(text_a), facts(text_b)
    warnings: List[str] = []
    ga, gb = meta_a.pop("geometry", None), meta_b.pop("geometry", None)
    for f, g in ((fa, ga), (fb, gb)):
        if g and g["units_stated"] and g["units"] in ("mm", "in"):
            f["units"] = g["units"]                                  # the file says so: better than guessing from its words
    geometry = _geometry(ga, gb, warnings) if ga and gb else None
    if bool(ga) != bool(gb):
        warnings.append("Only one of the two is a DXF, so sizes can't be compared in millimetres; the picture, text and numbers are compared.")
    if angle:
        warnings.append(f"The second drawing was sent turned {angle} degrees; it has been turned to match before comparing.")
    if fa["units"] != "unknown" and fb["units"] != "unknown" and fa["units"] != fb["units"]:
        names = {"mm": "millimetres", "in": "inches"}
        warnings.append(f"Different units: the first is in {names[fa['units']]}, the second in {names[fb['units']]}. Every dimension will differ until converted.")
    if "not stated" not in (fa["projection"], fb["projection"]) and fa["projection"] != fb["projection"]:
        warnings.append(f"Different projection: the first is {fa['projection']}, the second {fb['projection']}. Views are laid out differently (this is a common China/US difference).")
    for key, label in (("revision", "revision"), ("material", "material"), ("drawing_number", "drawing number"), ("scale", "scale")):
        if fa[key] and fb[key] and fa[key].casefold() != fb[key].casefold():
            warnings.append(f"Different {label}: {fa[key]} against {fb[key]}.")
    if fa["standards"] != fb["standards"] and fa["standards"] and fb["standards"]:
        warnings.append(f"Different standards named: {', '.join(fa['standards'])} against {', '.join(fb['standards'])}.")
    for side, f, m in (("first", fa, meta_a), ("second", fb, meta_b)):
        if m.get("text_chars", 0) < 40:
            warnings.append(f"Little or no text could be read from the {side} drawing, so only the picture comparison is reliable for it.")

    la, lb = _lines(text_a), _lines(text_b)
    only_a_lines = [x for x in dict.fromkeys(la) if x not in set(lb)][:60]
    only_b_lines = [x for x in dict.fromkeys(lb) if x not in set(la)][:60]
    na, nb = _numbers(text_a), _numbers(text_b)
    pct = round(100 * changed, 1)
    verdict = "identical" if pct < 0.5 and not only_a_lines and not only_b_lines else "minor differences" if pct < 5 else "different"
    buf = io.BytesIO()
    overlay.save(buf, "PNG", optimize=True)
    return {"verdict": verdict, "changed_percent": pct, "warnings": warnings, "first": {**meta_a, **fa}, "second": {**meta_b, **fb},
            "turned_second_by": angle, "regions": regions, "geometry": geometry,
            "text": {"only_on_first": only_a_lines, "only_on_second": only_b_lines},
            "numbers": {"only_on_first": [n for n in na if n not in set(nb)][:80], "only_on_second": [n for n in nb if n not in set(na)][:80]},
            "legend": "red = only on the first drawing, blue = only on the second, grey = the same",
            "overlay_png_base64": base64.b64encode(buf.getvalue()).decode("ascii")}


def overlay_png(result: Dict[str, Any]) -> bytes:
    return base64.b64decode(result["overlay_png_base64"])

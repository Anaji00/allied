"""Comparing CAD drawings: changed dimensions, revisions, units, first/third-angle, sideways scans, different margins,
Chinese drawings. Drawings are made in the test (nothing real, nothing external, nothing saved)."""

import base64
import io

import pymupdf
import pytest
from PIL import Image

from app.services import drawing_compare as dc


def _drawing(dim="250", rev="B", material="AR400", units="mm", projection="THIRD ANGLE PROJECTION", extra="", offset=(0, 0), scale=1.0,
             page=(842, 595), rotate=0, chinese=False, hole=True):
    doc = pymupdf.open()
    pg = doc.new_page(width=page[0], height=page[1])
    ox, oy = offset
    s = scale
    pg.draw_rect(pymupdf.Rect(150 * s + ox, 120 * s + oy, 650 * s + ox, 400 * s + oy), width=2)
    if hole:
        pg.draw_circle(pymupdf.Point(400 * s + ox, 260 * s + oy), 40 * s, width=2)
    pg.draw_line(pymupdf.Point(150 * s + ox, 430 * s + oy), pymupdf.Point(650 * s + ox, 430 * s + oy), width=1)
    pg.insert_text((380 * s + ox, 445 * s + oy), dim, fontsize=12)
    if chinese:
        for i, line in enumerate(["图号: D-4410", "材料: AR400", "比例 1:2", "第一角投影", "未注公差 ±0.5", "技术要求 去毛刺"]):
            pg.insert_text((60 + ox, 60 + 18 * i + oy), line, fontsize=11, fontname="china-s")
        pg.insert_text((60 + ox, 200 + oy), "UNIT: mm", fontsize=11)
    else:
        title = [f"DWG NO: D-4410", f"REV: {rev}", f"MATERIAL: {material}", f"UNLESS OTHERWISE SPECIFIED ALL DIMENSIONS IN {'MILLIMETERS MM' if units == 'mm' else 'INCHES IN'}",
                 projection, "SCALE: 1:2", "STANDARD ASME Y14.5", extra]
        for i, line in enumerate(l for l in title if l):
            pg.insert_text((60 + ox, 60 + 16 * i + oy), line, fontsize=10)
    if rotate:
        pg.set_rotation(rotate)
    data = doc.tobytes()
    doc.close()
    return data


def _scan(pdf_bytes, angle):
    """The drawing as a scanned picture, turned (a sideways scan)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=100)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).rotate(angle, expand=True, fillcolor="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _cmp(a, b, na="a.pdf", nb="b.pdf"):
    return dc.compare(a, na, b, nb)


def test_the_same_drawing_compared_with_itself_is_identical():
    a = _drawing()
    out = _cmp(a, a)
    assert out["verdict"] == "identical" and out["changed_percent"] < 0.5 and out["regions"] == [] and out["warnings"] == []


def test_a_changed_dimension_a_new_revision_and_a_new_material_are_all_reported():
    out = _cmp(_drawing("250", "B", "AR400"), _drawing("275", "C", "AR500"))
    assert any("Different revision: B against C" in w for w in out["warnings"]) and any("Different material" in w for w in out["warnings"])
    assert "250" in out["numbers"]["only_on_first"] and "275" in out["numbers"]["only_on_second"]
    assert out["verdict"] != "identical" and out["regions"]
    assert out["first"]["drawing_number"] == "D-4410" and out["first"]["revision"] == "B" and out["second"]["revision"] == "C"


def test_a_moved_hole_shows_up_as_a_changed_region_in_the_picture():
    out = _cmp(_drawing(hole=True), _drawing(hole=False))
    assert out["changed_percent"] > 1 and out["regions"]
    top = out["regions"][0]
    assert 25 < top["x"] + top["w"] / 2 < 75 and top["what"] in ("only on the first", "changed")        # around the middle, where the hole was
    png = Image.open(io.BytesIO(base64.b64decode(out["overlay_png_base64"]))).convert("RGB")
    reds = sum(1 for p in png.getdata() if p[0] > 190 and p[1] < 80)
    assert reds > 100                                                                                  # the missing hole is drawn in red


@pytest.mark.parametrize("angle", [90, 180, 270])
def test_a_drawing_sent_sideways_or_upside_down_is_turned_to_match(angle):
    a = _drawing("250")
    out = _cmp(a, _scan(a, angle), nb="scan.png")
    assert out["turned_second_by"] in (360 - angle, angle if angle == 180 else 360 - angle)
    assert out["changed_percent"] < 8, out["changed_percent"]
    assert any("turned" in w for w in out["warnings"])


def test_different_margins_page_sizes_and_position_are_not_changes():
    a = _drawing()
    b = _drawing(offset=(60, 40), page=(1190, 842), scale=1.0)
    out = _cmp(a, b)
    assert out["changed_percent"] < 8 and out["verdict"] in ("identical", "minor differences")


def test_units_and_projection_differences_are_called_out():
    out = _cmp(_drawing(units="mm", projection="THIRD ANGLE PROJECTION"), _drawing(units="in", projection="FIRST ANGLE PROJECTION"))
    text = " ".join(out["warnings"])
    assert "Different units" in text and "millimetres" in text and "inches" in text
    assert "Different projection" in text and "first-angle" in text and "third-angle" in text


def test_a_chinese_drawing_has_its_terms_explained_in_english_and_nothing_is_translated_automatically():
    out = _cmp(_drawing(), _drawing(chinese=True))
    terms = {t["zh"]: t["en"] for t in out["second"]["chinese_terms"]}
    assert terms["图号"] == "drawing number" and terms["材料"] == "material" and terms["第一角投影"] == "first-angle projection" and terms["未注公差"].startswith("general tolerances")
    assert out["second"]["has_chinese"] and out["second"]["projection"] == "first-angle" and out["second"]["drawing_number"] == "D-4410"
    assert out["first"]["has_chinese"] is False
    assert any("Different projection" in w for w in out["warnings"])


def test_standards_and_scale_are_read():
    f = dc.facts("DWG NO: A-1 REV: C MATERIAL: ASTM A36 SCALE: 1:5 GB/T 1804 ASME Y14.5 ISO 2768")
    assert f["scale"] == "1:5" and {"ASMEY14.5", "ISO2768"} <= set(f["standards"]) and any(s.startswith("GB/T") for s in f["standards"])


@pytest.mark.parametrize("name,data,msg", [("a.pdf", b"", "empty"), ("a.pdf", b"%PDF-1.4 not really", "could not be read"), ("a.png", b"not an image", "not a PDF or a picture")])
def test_broken_files_are_plain_errors(name, data, msg):
    with pytest.raises(dc.CompareError, match=msg):
        dc.compare(data, name, _drawing(), "b.pdf")


def test_a_blank_page_and_a_missing_page_are_plain_errors():
    doc = pymupdf.open()
    doc.new_page()
    blank = doc.tobytes()
    with pytest.raises(dc.CompareError, match="blank"):
        dc.compare(blank, "blank.pdf", _drawing(), "b.pdf")
    with pytest.raises(dc.CompareError, match="no page 3"):
        dc.compare(_drawing(), "a.pdf", _drawing(), "b.pdf", page_b=3)


def test_a_password_protected_drawing_is_a_plain_error():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "DWG NO: X-1")
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with pytest.raises(dc.CompareError, match="password"):
        dc.compare(locked, "locked.pdf", _drawing(), "b.pdf")


def test_the_api_returns_the_comparison_and_the_picture_and_refuses_bad_uploads(client):
    a, b = _drawing("250", "B"), _drawing("275", "C")
    files = {"first": ("a.pdf", a, "application/pdf"), "second": ("b.pdf", b, "application/pdf")}
    r = client.post("/api/v1/drawings/compare", files=files)
    assert r.status_code == 200 and r.json()["verdict"] != "identical" and r.json()["overlay_png_base64"]
    png = client.post("/api/v1/drawings/compare/overlay.png", files=files)
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    bad = client.post("/api/v1/drawings/compare", files={"first": ("a.exe", b"MZ", "application/octet-stream"), "second": ("b.pdf", b, "application/pdf")})
    assert bad.status_code == 400 and "Formats that work" in bad.json()["detail"]
    broken = client.post("/api/v1/drawings/compare", files={"first": ("a.pdf", b"%PDF-1.4 junk", "application/pdf"), "second": ("b.pdf", b, "application/pdf")})
    assert broken.status_code == 400
    assert client.post("/api/v1/drawings/compare", files=files, data={"page_first": "0"}).status_code == 422


def test_viewers_cannot_compare_and_logged_out_callers_cannot_either(auth_enforced, client):
    import uuid

    from tests.conftest import staff_token

    files = {"first": ("a.pdf", _drawing(), "application/pdf"), "second": ("b.pdf", _drawing(), "application/pdf")}
    assert client.post("/api/v1/drawings/compare", files=files).status_code in (401, 403)
    viewer = {"Authorization": f"Bearer {staff_token(f'dv{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', 'VIEWER')}"}
    assert client.post("/api/v1/drawings/compare", files=files, headers=viewer).status_code == 403

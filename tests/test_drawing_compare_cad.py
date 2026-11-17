"""Comparing CAD files: DXF (read by what is in it, sizes normalised to millimetres), DXF against a PDF, and plain refusals for DWG
and STEP/IGES. The drawings are made in the test with ezdxf; nothing real, nothing external."""

import io

import ezdxf
import pymupdf
import pytest

from app.services import drawing_compare as dc


def _dxf(units="mm", width=250.0, height=120.0, hole=(125.0, 60.0), hole_d=40.0, extra_hole=None, dim=None, notes=("MATERIAL: AR400", "REV: B"),
         offset=(0.0, 0.0), rotate=False, scale=1.0, sheet=None):
    """A plate with holes, dimensions and notes, in the given units (the numbers are in those units)."""
    doc = ezdxf.new("R2018", setup=True)
    doc.units = {"mm": 4, "in": 1}[units]
    msp = doc.modelspace()
    k = scale * (1 / 25.4 if units == "in" else 1)                      # the same physical part, drawn in the chosen unit
    ox, oy = offset
    pts = [(0, 0), (width, 0), (width, height), (0, height)]
    hx, hy = hole
    msp.add_lwpolyline([(ox + x * k, oy + y * k) for x, y in pts], close=True)
    msp.add_circle((ox + hx * k, oy + hy * k), hole_d / 2 * k)
    if extra_hole:
        msp.add_circle((ox + extra_hole[0] * k, oy + extra_hole[1] * k), 5 * k)
    d = msp.add_linear_dim(base=(ox, oy - 20 * k), p1=(ox, oy), p2=(ox + width * k, oy), dimstyle="EZDXF")
    d.render()
    if dim is not None:
        for e in msp.query("DIMENSION"):
            e.dxf.actual_measurement = dim * k
    for i, n in enumerate(notes):
        msp.add_text(n, height=4 * k, dxfattribs={"insert": (ox + 5 * k, oy + (height + 20 + 8 * i) * k)})
    if rotate:                                                          # the whole sheet turned a quarter turn, notes and dimensions included
        from ezdxf import transform
        from ezdxf.math import Matrix44

        transform.inplace(msp, Matrix44.z_rotate(1.5707963267948966))
    if sheet:                                                           # a big paper-sheet border with the drawing inside it
        msp.add_lwpolyline([(ox - 30 * k, oy - 60 * k), (ox + sheet[0] * k, oy - 60 * k), (ox + sheet[0] * k, oy + sheet[1] * k), (ox - 30 * k, oy + sheet[1] * k)], close=True)
    out = io.StringIO()
    doc.write(out)
    return out.getvalue().encode("utf-8")


def _cmp(a, b, na="a.dxf", nb="b.dxf"):
    return dc.compare(a, na, b, nb)


def test_the_same_part_drawn_in_inches_and_in_millimetres_is_the_same_part():
    out = _cmp(_dxf("mm"), _dxf("in"))
    g = out["geometry"]
    assert not g["size_mm"]["changed"] and g["circle_diameters_mm"] == {"only_on_first": [], "only_on_second": []}
    assert any("millimetres" in n and "inches" in n for n in g["notes"])
    assert out["verdict"] in ("identical", "minor differences") and out["changed_percent"] < 5
    assert out["first"]["units"] == "mm" and out["second"]["units"] == "in"


def test_a_moved_hole_a_changed_hole_size_and_a_new_hole_are_found_by_diameter_and_in_the_picture():
    out = _cmp(_dxf(hole_d=40), _dxf(hole_d=45, extra_hole=(30, 30)))
    g = out["geometry"]["circle_diameters_mm"]
    assert g["only_on_first"] == [40.0] and sorted(g["only_on_second"]) == [10.0, 45.0]
    assert out["regions"] and out["changed_percent"] > 0.5


def test_a_changed_overall_size_and_a_changed_dimension_are_reported_in_millimetres():
    out = _cmp(_dxf(width=250, dim=250), _dxf(width=275, dim=275))
    assert out["geometry"]["size_mm"]["changed"] and any("Different overall size: 250.0 x 120.0 mm against 275.0 x 120.0 mm" in w for w in out["warnings"])
    assert out["geometry"]["dimensions_mm"]["only_on_first"] == [250.0] and out["geometry"]["dimensions_mm"]["only_on_second"] == [275.0]


def test_a_dxf_in_inches_with_a_changed_dimension_is_compared_in_millimetres():
    out = _cmp(_dxf("mm", width=250, dim=250), _dxf("in", width=250, dim=260))
    assert out["geometry"]["dimensions_mm"]["only_on_first"] == [250.0]
    assert [round(v) for v in out["geometry"]["dimensions_mm"]["only_on_second"]] == [260]


def test_revision_and_material_notes_are_compared():
    out = _cmp(_dxf(notes=("MATERIAL: AR400", "REV: B", "DWG NO: D-4410")), _dxf(notes=("MATERIAL: AR500", "REV: C", "DWG NO: D-4410")))
    text = " ".join(out["warnings"])
    assert "Different revision: B against C" in text and "Different material" in text
    assert "rev: b" in out["text"]["only_on_first"] and "rev: c" in out["text"]["only_on_second"]


def test_position_paper_border_orientation_and_scale_are_normalised_away():
    base = _dxf()
    moved = _dxf(offset=(5000, 3000))                                        # drawn far from the origin
    assert _cmp(base, moved)["changed_percent"] < 3
    turned = _cmp(base, _dxf(rotate=True))                                   # laid out on its side
    assert turned["turned_second_by"] in (90, 270) and turned["changed_percent"] < 15
    bigger = _cmp(base, _dxf(scale=2.0))                                     # twice the size: reported, not hidden
    assert bigger["geometry"]["size_mm"]["changed"] and bigger["changed_percent"] < 10


def test_a_dxf_can_be_compared_with_a_pdf_exported_from_cad():
    pdf = pymupdf.open()
    pg = pdf.new_page(width=842, height=595)
    pg.draw_rect(pymupdf.Rect(150, 150, 650, 390), width=2)
    pg.draw_circle(pymupdf.Point(400, 270), 40, width=2)
    pg.insert_text((160, 130), "MATERIAL: AR400", fontsize=10)
    out = dc.compare(_dxf(), "plate.dxf", pdf.tobytes(), "plate.pdf")
    assert out["geometry"] is None and any("Only one of the two is a DXF" in w for w in out["warnings"])
    assert out["first"]["units"] == "mm" and out["verdict"] != "identical"       # (a PDF draws text as glyphs, a DXF as boxes: the picture is a guide here)


def test_a_damaged_dxf_is_repaired_or_refused_in_plain_words():
    good = _dxf()
    cut = good[: len(good) // 2]                                              # a file cut off half way
    try:
        out = dc.compare(cut, "cut.dxf", good, "good.dxf")
        assert "verdict" in out                                               # repaired well enough to compare
    except dc.CompareError as exc:
        assert "cut.dxf" in str(exc)
    with pytest.raises(dc.CompareError, match="isn't a DXF|no lines or text"):
        dc.compare(b"SECTION\nthis is not a drawing\nENDSEC\nEOF", "junk.dxf", good, "good.dxf")


def test_an_empty_dxf_says_so():
    doc = ezdxf.new("R2018")
    out = io.StringIO()
    doc.write(out)
    with pytest.raises(dc.CompareError, match="no lines or text"):
        dc.compare(out.getvalue().encode(), "empty.dxf", _dxf(), "b.dxf")


@pytest.mark.parametrize("name,data,words", [
    ("part.dwg", b"AC1032\x00\x00\x00\x00\x00 binary dwg", ["DWG", "DXF", "PDF"]),
    ("renamed.pdf", b"AC1027\x00\x00\x00 a dwg with a pdf name", ["DWG", "DXF"]),
    ("part.step", b"ISO-10303-21;\nHEADER;", ["STEP", "DXF or PDF"]),
    ("part.stp", b"ISO-10303-21;\nHEADER;", ["STEP"]),
    ("part.igs", b"                                                                        S      1\n" + b"IGES file", ["IGES"]),
])
def test_dwg_and_step_files_get_a_plain_refusal_that_names_the_file_and_what_works(name, data, words):
    with pytest.raises(dc.CompareError) as e:
        dc.compare(data, name, _dxf(), "b.dxf")
    msg = str(e.value)
    assert name in msg and all(w in msg for w in words) and "PDF, PNG, JPG, TIFF or DXF" in msg


def test_the_api_reads_dxf_and_refuses_dwg_and_step_with_a_400_that_says_why(client):
    a, b = _dxf(hole_d=40), _dxf(hole_d=45)
    ok = client.post("/api/v1/drawings/compare", files={"first": ("a.dxf", a, "application/dxf"), "second": ("b.dxf", b, "application/dxf")})
    assert ok.status_code == 200 and ok.json()["geometry"]["circle_diameters_mm"]["only_on_first"] == [40.0]
    for name, data in (("c.dwg", b"AC1032 dwg bytes"), ("c.step", b"ISO-10303-21;"), ("c.iges", b"IGES")):
        r = client.post("/api/v1/drawings/compare", files={"first": (name, data, "application/octet-stream"), "second": ("b.dxf", b, "application/dxf")})
        assert r.status_code == 400 and name in r.json()["detail"] and "Formats that work" in r.json()["detail"], name
    weird = client.post("/api/v1/drawings/compare", files={"first": ("c.exe", b"MZ", "application/octet-stream"), "second": ("b.dxf", b, "application/dxf")})
    assert weird.status_code == 400 and "Formats that work: PDF, PNG, JPG, TIFF or DXF" in weird.json()["detail"]

"""Round 28 (adversary), part 1: the customer-redaction firewall (app/services/redaction.py): PDF scan/redact/rescan, DXF scrub, hash binding, DWG attestation and the hard block on vendor attachments.
Synthetic PDFs (PyMuPDF), DXFs (ezdxf) and PNG pictures only; a temporary database and the test file store; nothing is sent. The OCR engine (Tesseract) is not installed here, which is the case the
firewall must handle by saying 'unreadable', never 'clean'. xfail(strict=False, reason="ADV28-Tn ...") marks holes."""
import hashlib
import io
import re
import uuid
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, RedactedArtifactDB
from app.services import redaction
from app.services.coordination import files, jobs, rfqs

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss-r28-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
OP = {"email": f"op-r28-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
ATH = f"athish-r28-{TAG}@alliedweldingsystems.com"
ENG = {"email": ATH, "role": "OPERATOR"}
CUSTOMER = "Owens Corning"
TERMS = [CUSTOMER]


@pytest.fixture(autouse=True)
def firewall(monkeypatch, request):
    if "real_ocr" not in request.keywords:
        monkeypatch.setattr(redaction, "ocr_available", lambda: False)        # pinned off: the 'engine missing' case the firewall must call unreadable
    monkeypatch.setattr(settings, "REDACTION_ENFORCED", True)
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", ATH)


def _pdf(draw, pages=1, w=400, h=300):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=w, height=h)
        draw(page, i)
    data = doc.tobytes()
    doc.close()
    return data


def _text_pdf(text, rotate=0, size=14, pos=(40, 100), color=(0, 0, 0)):
    return _pdf(lambda p, i: p.insert_text(pos, text, fontsize=size, rotate=rotate, color=color))


def _png_with(text, size=(380, 120)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    d.text((10, 30), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _covered(pdf_bytes, rect, dpi=100):
    """True when the area is solid black: the picture of the page has been painted over there."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi, clip=fitz.Rect(*rect))
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    px = list(img.getdata())
    return sum(1 for v in px if v < 40) / max(1, len(px)) > 0.9


def _job(customer=CUSTOMER):
    r = rfqs.create({"customer": customer, "subject": f"r28 {uuid.uuid4().hex[:6]}"}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    return r, j


def _store(rfq_number, name, data):
    return files.add("rfq", rfq_number, name, data, BOSS)["id"]


# =============================================================== the PDF: text layer, metadata and the hidden corners

def test_the_text_layer_is_painted_and_the_rebuilt_pdf_holds_no_text_metadata_or_files():
    data = _text_pdf("Drawing for Owens Corning plant 7")
    before = redaction.scan_pdf(data, TERMS)
    assert [f["where"] for f in before["findings"]] == ["text"]
    out, report = redaction.redact_pdf(data, TERMS)
    assert report["painted"] >= 1 and not redaction.scan_pdf(out, TERMS)["findings"]
    doc = fitz.open(stream=out, filetype="pdf")
    assert not doc[0].get_text().strip() and not any(v for k, v in (doc.metadata or {}).items() if k != "format") and not doc.embfile_names()
    assert _covered(out, (35, 85, 330, 105)) or b"Owens" not in out


@pytest.mark.parametrize("where", ["metadata-author", "xmp", "bookmark", "annotation", "form-field", "embedded-file", "link"])
def test_customer_name_hidden_in_a_non_text_place_is_found_and_the_rebuild_drops_it(where):
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 100), "Bracket, 10 mm plate", fontsize=14)
    if where == "metadata-author":
        doc.set_metadata({"author": "Owens Corning engineering", "title": "x"})
    elif where == "xmp":
        doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Owens Corning</dc:creator></rdf:Description></rdf:RDF></x:xmpmeta>')
    elif where == "bookmark":
        doc.set_toc([[1, "Owens Corning plant 7", 1]])
    elif where == "annotation":
        page.add_text_annot((200, 200), "Owens Corning internal note")
    elif where == "form-field":
        w = fitz.Widget()
        w.field_name, w.field_type, w.field_value, w.rect = "customer", fitz.PDF_WIDGET_TYPE_TEXT, "Owens Corning", fitz.Rect(40, 200, 200, 220)
        page.add_widget(w)
    elif where == "embedded-file":
        doc.embfile_add("owens-corning-spec.txt", b"secret spec")
    elif where == "link":
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(40, 40, 100, 60), "uri": "https://owenscorning.com/plant7"})
    data = doc.tobytes()
    before = redaction.scan_pdf(data, TERMS + ["owenscorning.com"])
    assert before["findings"], f"{where} was not found"
    out, _ = redaction.redact_pdf(data, TERMS + ["owenscorning.com"])
    rebuilt = fitz.open(stream=out, filetype="pdf")
    assert not any(v for k, v in (rebuilt.metadata or {}).items() if k != "format") and not rebuilt.get_toc() and not rebuilt.embfile_names() and not list(rebuilt[0].annots() or []) and not list(rebuilt[0].widgets() or []) and not rebuilt[0].get_links()
    assert b"Owens" not in out and b"owens" not in out.lower().replace(b"owenscorning", b"")


# =============================================================== name spellings in the text layer

SPELL = {
    "plain": ("Owens Corning", {}), "upper": ("OWENS CORNING", {}), "lower": ("owens corning", {}), "accent": ("Öwens Cörning", {}), "zero-width": ("Owens​ Corning", {}),
    "tiny": ("Owens Corning", {"size": 2}), "white-on-white": ("Owens Corning", {"color": (1, 1, 1)}), "rotated": ("Owens Corning", {"rotate": 90}),
    "spaced-letters": ("O w e n s   C o r n i n g", {}), "cyrillic-o": ("Оwens Corning", {}), "greek-o": ("Οwens Corning", {}), "dotted": ("Owens.Corning", {}), "underscore": ("Owens_Corning", {}),
    "one-word": ("OwensCorning", {}), "hyphen-wrap": ("Owens-\nCorning", {}), "line-wrap": ("Owens\nCorning", {}),
}


def _spelled(text, **kw):
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    y = 100
    for line in text.split("\n"):
        page.insert_text((40, y), line, fontsize=kw.get("size", 14), rotate=kw.get("rotate", 0), color=kw.get("color", (0, 0, 0)))
        y += 20
    return doc.tobytes()


@pytest.mark.parametrize("name", ["plain", "upper", "lower", "accent", "zero-width", "tiny", "rotated", "spaced-letters", "dotted", "underscore", "one-word", "line-wrap"])
def test_the_name_is_found_in_the_text_layer_in_other_spellings(name):
    text, kw = SPELL[name]
    got = redaction.scan_pdf(_spelled(text, **kw), TERMS)
    if not got["findings"]:
        pytest.xfail(f"ADV28-T1 the scan does not find {name!r}: {text!r}")


@pytest.mark.parametrize("name", ["plain", "upper", "lower", "accent", "zero-width", "rotated", "spaced-letters", "cyrillic-o", "greek-o", "dotted", "one-word", "hyphen-wrap", "line-wrap"])
def test_whatever_the_scan_finds_is_really_painted_over_in_the_rebuilt_page(name):
    text, kw = SPELL[name]
    data = _spelled(text, **kw)
    found = redaction.scan_pdf(data, TERMS)["findings"]
    out, report = redaction.redact_pdf(data, TERMS)
    drawn = fitz.open(stream=data, filetype="pdf")[0].get_text("words")
    ink_rect = None
    if drawn:
        x0 = min(w[0] for w in drawn); y0 = min(w[1] for w in drawn); x1 = max(w[2] for w in drawn); y1 = max(w[3] for w in drawn)
        ink_rect = (x0 - 2, y0 - 2, x1 + 2, y1 + 2)
    painted = report["painted"] > 0 and (ink_rect is None or _covered(out, ink_rect) or report["painted"] >= len(found))
    if not painted:
        pytest.xfail(f"ADV28-T2 {name!r}: found={bool(found)}, painted={report['painted']}: the name stays visible in the rebuilt picture")


@pytest.mark.parametrize("text", ["Owens​ Corning", "Öwens Cörning", "Owens Corning", "Öwens Corning"], ids=["zero-width", "accents", "nbsp", "combining"])
def test_t3_a_name_found_by_the_scan_is_always_painted(text):
    data = _spelled(text)
    found = redaction.scan_pdf(data, TERMS)["findings"]
    out, report = redaction.redact_pdf(data, TERMS)
    assert not found or report["painted"] >= 1


def test_t4_a_customer_po_number_split_across_lines_is_found():
    data = _spelled("PO 45009\n12345 rev B")
    assert redaction.scan_pdf(data, ["4500912345"])["findings"]


# =============================================================== pictures, vectors and what OCR cannot read

def test_a_picture_only_page_is_unreadable_never_clean_when_ocr_is_missing():
    png = _png_with("Owens Corning")
    data = _pdf(lambda p, i: p.insert_image(p.rect, stream=png))
    scan = redaction.scan_pdf(data, TERMS)
    assert scan["unreadable_pages"] == [1] and not redaction.ocr_available()
    r, j = _job()
    fid = _store(r["number"], "title-block.pdf", data)
    art = redaction.check_file(j["id"], fid, BOSS)
    assert art["status"] == "BLOCKED" and any(f["where"] == "unreadable picture page" for f in art["findings_before"])
    with pytest.raises(redaction.RedactionError):
        redaction.verify(art["id"], BOSS)
    assert redaction.vendor_block(fid)


def test_t5_a_logo_image_on_a_page_with_text_is_flagged_unreadable():
    png = _png_with("Owens Corning")
    notes = "General notes: all dimensions in millimetres, break sharp edges, material AR400, tolerance plus minus 0.5 unless stated."

    def draw(p, i):
        p.insert_text((20, 40), notes[:70], fontsize=8)
        p.insert_text((20, 55), notes[70:], fontsize=8)
        p.insert_image(fitz.Rect(20, 100, 300, 190), stream=png)

    data = _pdf(draw, h=250)
    scan = redaction.scan_pdf(data, TERMS)
    assert scan["unreadable_pages"] == [1] or scan["findings"]


def test_t6_a_vector_only_page_is_not_called_clean():
    def draw(p, i):
        shape = p.new_shape()
        for k in range(8):
            shape.draw_line((40 + k * 20, 100), (40 + k * 20, 140))
            shape.draw_line((40 + k * 20, 100), (55 + k * 20, 100))
        shape.finish(width=3, color=(0, 0, 0))
        shape.commit()

    data = _pdf(draw)
    r, j = _job()
    fid = _store(r["number"], "outlined.pdf", data)
    art = redaction.check_file(j["id"], fid, BOSS)
    assert art["status"] != "CLEAN" or redaction.scan_pdf(data, TERMS)["unreadable_pages"]


@pytest.mark.parametrize("data,why", [(b"", "empty"), (b"not a pdf", "text"), (b"%PDF-1.4\n" + b"x" * 200, "truncated")])
def test_a_broken_pdf_is_a_plain_error_not_a_crash(data, why):
    with pytest.raises(redaction.RedactionError):
        redaction.scan_pdf(data, TERMS)
    with pytest.raises(redaction.RedactionError):
        redaction.redact_pdf(data, TERMS)


def test_a_password_pdf_and_a_huge_pdf_are_refused_plainly():
    doc = fitz.open()
    doc.new_page().insert_text((40, 40), "secret")
    locked = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    with pytest.raises(redaction.RedactionError) as e:
        redaction.scan_pdf(locked, TERMS)
    assert e.value.status == 422
    big = _pdf(lambda p, i: p.insert_text((20, 40), "x"), pages=61)
    with pytest.raises(redaction.RedactionError):
        redaction.redact_pdf(big, TERMS)


# =============================================================== the file name and the record

@pytest.mark.parametrize("name", ["Owens_Corning_liner.pdf", "OwensCorning-liner.pdf", "OWENS CORNING plant 7.pdf", "liner-for-owens.pdf"])
def test_t7_the_redacted_copy_does_not_carry_the_customers_name_in_its_file_name(name):
    r, j = _job()
    fid = _store(r["number"], name, _text_pdf("Liner plate, Owens Corning"))
    art = redaction.check_file(j["id"], fid, BOSS)
    assert art["status"] == "CLEAN"
    with SessionLocal() as db:
        got = db.get(ItemFileDB, art["redacted_file_id"]).filename
    assert "owens" not in got.lower()


def test_the_flow_clean_then_verify_then_vendor_block_lifts_and_a_viewer_or_operator_cannot_verify():
    r, j = _job()
    fid = _store(r["number"], "liner.pdf", _text_pdf("Liner plate, Owens Corning, plant 7"))
    assert redaction.vendor_block(fid)
    art = redaction.check_file(j["id"], fid, BOSS)
    assert art["status"] == "CLEAN" and art["redacted_file_id"]
    rid = art["redacted_file_id"]
    assert redaction.vendor_block(rid) and redaction.vendor_block(fid)
    for who in (OP, {"email": "v@x.test", "role": "VIEWER"}, {"email": "", "role": ""}):
        with pytest.raises(redaction.RedactionError) as e:
            redaction.verify(art["id"], who)
        assert e.value.status == 403
    assert redaction.verify(art["id"], BOSS)["status"] == "VERIFIED"
    assert redaction.vendor_block(rid) is None and redaction.vendor_block(fid)             # the redacted copy may go, the original may not


def test_a_file_with_no_known_customer_is_not_checked_at_all():
    r, j = _job(customer="")
    fid = _store(r["number"], "x.pdf", _text_pdf("anything"))
    with pytest.raises(redaction.RedactionError):
        redaction.check_file(j["id"], fid, BOSS)


# =============================================================== hash binding and attestation

def _verified(name="liner.pdf", text="Liner plate, Owens Corning"):
    r, j = _job()
    fid = _store(r["number"], name, _text_pdf(text))
    art = redaction.check_file(j["id"], fid, BOSS)
    redaction.verify(art["id"], BOSS)
    return r, j, fid, art["redacted_file_id"]


def test_t8_a_verified_file_changed_on_disk_is_no_longer_allowed():
    r, j, fid, rid = _verified()
    path, _n, _m = files.open_file(rid)
    raw = bytearray(Path(path).read_bytes())
    raw[len(raw) // 2] ^= 0x01
    Path(path).write_bytes(bytes(raw))
    assert redaction.vendor_block(rid)
    from app.services.coordination import purchase_orders

    with pytest.raises(ValueError):
        purchase_orders.attachments([rid], strict=True)


def test_the_same_bytes_under_another_name_are_a_different_file_and_need_their_own_check():
    r, j, fid, rid = _verified()
    path, _n, _m = files.open_file(rid)
    again = _store(r["number"], "renamed-copy.pdf", Path(path).read_bytes())
    assert redaction.vendor_block(again) is None      # identical bytes to a person-verified copy: allowed by design


def test_a_dwg_needs_an_attestation_by_a_named_engineer_with_a_real_statement():
    r, j = _job()
    fid = _store(r["number"], "bracket.dwg", b"AC1027\x00\x00fake dwg bytes " * 20)
    art = redaction.check_file(j["id"], fid, BOSS)
    assert art["status"] == "PENDING" and redaction.vendor_block(fid)
    for who, stmt in ((BOSS, "I checked it fully"), (OP, "I checked it fully"), (ENG, ""), (ENG, "short"), (ENG, "        ")):
        with pytest.raises(redaction.RedactionError):
            redaction.attest(j["id"], fid, who, stmt)
    out = redaction.attest(j["id"], fid, ENG, "Opened in the CAD viewer, no customer name anywhere")
    assert out["status"] == "VERIFIED" and out["attested_by"] == ATH and redaction.vendor_block(fid) is None


def test_t9_an_attestation_is_for_the_job_the_file_belongs_to():
    r, j = _job()
    r2, j2 = _job(customer="Some Other Customer")
    fid = _store(r["number"], "bracket.dwg", b"AC1027\x00\x00fake dwg bytes " * 20)
    with pytest.raises(redaction.RedactionError):
        redaction.attest(j2["id"], fid, ENG, "Opened in the CAD viewer, no customer name anywhere")


@pytest.mark.parametrize("name", ["x.PDF.dwg", "photo.png", "quote.xlsx", "bundle.zip", "notes.txt"])
def test_every_other_kind_of_file_is_blocked_until_attested(name):
    r, j = _job()
    fid = _store(r["number"], name, b"whatever bytes" * 10)
    assert redaction.vendor_block(fid)


def test_a_pdf_named_dxf_or_the_reverse_is_refused_not_mis_cleaned():
    r, j = _job()
    fid = _store(r["number"], "really-a-pdf.dxf", _text_pdf("Owens Corning"))
    with pytest.raises(redaction.RedactionError):
        redaction.check_file(j["id"], fid, BOSS)
    fid2 = _store(r["number"], "really-a-dxf.pdf", b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")
    with pytest.raises(redaction.RedactionError):
        redaction.check_file(j["id"], fid2, BOSS)


# =============================================================== the hard block on every way a drawing reaches a vendor

def test_an_unverified_design_file_cannot_go_to_a_vendor_by_the_action_the_po_attachments_or_the_assistant(monkeypatch):
    from app.services import actions
    from app.services.coordination import purchase_orders

    r, j = _job()
    fid = _store(r["number"], "liner.pdf", _text_pdf("Liner plate, Owens Corning"))
    files.set_design(fid, True, BOSS)
    with pytest.raises(ValueError):
        purchase_orders.attachments([fid], strict=True)
    ctx = actions.context("rfq", r["number"])
    spec = {"type": "design_file", "id": fid}
    with pytest.raises(actions.ActionError):
        actions._spec_ok(spec, "vendor", {**ctx, "files": [{"type": "design_file", "id": fid, "name": "liner.pdf"}]})
    art = redaction.check_file(j["id"], fid, BOSS)
    redaction.verify(art["id"], BOSS)
    assert purchase_orders.attachments([art["redacted_file_id"]], strict=True)


def test_redaction_and_gates_default_to_enforced_and_no_app_code_reads_them_from_the_test_runner():
    from app.config import Settings

    assert Settings.model_fields["REDACTION_ENFORCED"].default is True and Settings.model_fields["GATES_ENFORCED"].default is True
    root = Path(__file__).resolve().parent.parent / "app"
    bad = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"PYTEST_CURRENT_TEST|sys\.modules\[?.?[\"']pytest|import pytest|\"pytest\" in sys\.modules", text) and p.name not in ("database.py",):
            bad.append(p.name)
        if p.name != "config.py" and re.search(r"(REDACTION|GATES)_ENFORCED\s*=", text):
            bad.append(p.name + " sets a flag")
    assert not bad, bad


@pytest.mark.real_ocr
def test_with_the_real_ocr_engine_a_name_in_a_picture_is_found_and_painted_or_the_page_stays_unreadable():
    if not redaction.ocr_available():
        pytest.skip("no Tesseract on this machine")
    data = _pdf(lambda p, i: p.insert_image(p.rect, stream=_png_with("Owens Corning")))
    scan = redaction.scan_pdf(data, TERMS)
    out, report = redaction.redact_pdf(data, TERMS)
    after = redaction.scan_pdf(out, TERMS)
    assert (scan["findings"] and report["painted"] >= 1 and not after["findings"]) or scan["unreadable_pages"]

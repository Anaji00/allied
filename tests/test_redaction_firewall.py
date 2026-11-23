"""The customer-redaction firewall on SYNTHETIC drawings (made here, never a real file): PDF text, metadata, annotations, embedded files and picture pages; DXF text, attributes and header; the redacted
copy is rebuilt and scanned again; a vendor gets only a verified redacted copy or an engineer-attested DWG whose hash has not changed. Temporary database; no real OCR is needed (it is faked)."""
import hashlib
import io
import uuid

import ezdxf
import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.services import actions, gates, redaction
from app.services.coordination import files, jobs, purchase_orders, rfqs
from tests.conftest import staff_token

BOSS = {"email": "boss.red@alliedweldingsystems.com", "role": "MANAGER"}
ENG = {"email": "athish.red@allied.test", "role": "OPERATOR"}
CUSTOMER = "Owens Corning"
_REAL_OCR_AVAILABLE = redaction.ocr_available      # captured before the tests replace it


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(settings, "REDACTION_ENFORCED", True)
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", ENG["email"])
    monkeypatch.setattr(redaction, "ocr_available", lambda: False)


def _job():
    tag = uuid.uuid4().hex[:4]
    r = rfqs.create({"customer": CUSTOMER, "subject": f"liners {tag}", "contact_email": f"buyer{tag}@owenscorning.test"}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    return j["id"], r["number"], tag


def _pdf(text_lines, author="", annot=None, embed=False):
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in text_lines:
        page.insert_text((72, y), line, fontsize=12)
        y += 20
    if annot:
        page.add_text_annot((300, 72), annot)
    if embed:
        doc.embfile_add("note.txt", b"hello")
    doc.set_metadata({"author": author, "title": "drawing"})
    return doc.tobytes()


def _corrupt(file_id):
    from pathlib import Path

    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB

    with SessionLocal() as db:
        path = Path(db.get(ItemFileDB, file_id).stored_path)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0x01
    path.write_bytes(bytes(raw))


def _add(rfq_number, name, data):
    return files.add("rfq", rfq_number, name, data, BOSS)["id"]


def test_the_scan_finds_the_customer_everywhere_in_a_pdf():
    job_id, rfq, tag = _job()
    terms = redaction.terms_for_job(job_id, [f"4500{tag}"])
    assert CUSTOMER in terms and f"owenscorning.test" in terms and f"4500{tag}" in terms
    data = _pdf([f"Customer: {CUSTOMER}", f"Customer PO 4500{tag}", "Part: LINER-1"], author=CUSTOMER, annot=f"call {CUSTOMER}", embed=True)
    where = {f["where"] for f in redaction.scan_pdf(data, terms)["findings"]}
    assert {"text", "metadata", "annotation", "embedded file"} <= where
    assert redaction.scan_pdf(_pdf(["Part: LINER-1", "Material AR400"]), terms)["findings"] == []


def test_a_pdf_is_painted_over_rebuilt_without_text_metadata_or_embedded_files_and_scans_clean():
    job_id, rfq, tag = _job()
    terms = redaction.terms_for_job(job_id)
    data = _pdf([f"Customer: {CUSTOMER}", "Part: LINER-1"], author=CUSTOMER, annot="x", embed=True)
    out, report = redaction.redact_pdf(data, terms)
    assert report["painted"] >= 1
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc[0].get_text().strip() == "" and not (doc.metadata.get("author") or "") and not doc.embfile_names()
    pix = doc[0].get_pixmap(dpi=100)
    assert any(b < 20 for b in pix.samples[:: pix.n * 8])                                              # something black was painted
    assert redaction.scan_pdf(out, terms)["findings"] == []


def test_a_picture_page_is_read_by_ocr_and_painted_and_without_ocr_it_is_never_called_clean(monkeypatch):
    job_id, rfq, tag = _job()
    terms = redaction.terms_for_job(job_id)
    base = fitz.open()
    pg = base.new_page()
    pg.draw_rect(fitz.Rect(50, 50, 400, 200), fill=(1, 1, 1))
    png = pg.get_pixmap(dpi=150).tobytes("png")
    doc = fitz.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=png)
    picture_only = doc.tobytes()
    s = redaction.scan_pdf(picture_only, terms)
    assert s["unreadable_pages"] == [1] and s["ocr_available"] is False                                 # no OCR: unreadable, not clean
    monkeypatch.setattr(redaction, "ocr_available", lambda: True)
    img_box = {"text": "Owens", "left": 100, "top": 100, "width": 80, "height": 30}, {"text": "Corning", "left": 190, "top": 100, "width": 90, "height": 30}
    monkeypatch.setattr(redaction, "ocr_words", lambda img: list(img_box) if img.size[0] else [])
    seen = redaction.scan_pdf(picture_only, terms)
    assert seen["findings"] and seen["findings"][0]["where"] == "picture text"
    out, report = redaction.redact_pdf(picture_only, terms)
    assert report["painted"] >= 1
    img = Image.open(io.BytesIO(fitz.open(stream=out, filetype="pdf")[0].get_pixmap(dpi=170).tobytes("png"))).convert("L")
    assert img.getpixel((int(140 * 170 / 150), int(115 * 170 / 150))) < 30                              # the box where OCR saw the name is black


def test_a_dxf_is_scrubbed_in_text_attributes_dimensions_and_header_and_scans_clean():
    job_id, rfq, tag = _job()
    terms = redaction.terms_for_job(job_id)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text(f"{CUSTOMER} plant 4")
    msp.add_mtext(f"For {CUSTOMER}\\Pliner")
    blk = doc.blocks.new("TITLE")
    blk.add_attdef("CLIENT", (0, 0), text=CUSTOMER)
    msp.add_blockref("TITLE", (0, 0)).add_auto_attribs({"CLIENT": CUSTOMER})
    doc.header["$PROJECTNAME"] = CUSTOMER
    buf = io.StringIO()
    doc.write(buf)
    data = buf.getvalue().encode()
    found = {f["where"] for f in redaction.scan_dxf(data, terms)["findings"]}
    assert {"text", "mtext", "attdef", "header"} <= found
    out, report = redaction.redact_dxf(data, terms)
    assert report["painted"] >= 3 and redaction.scan_dxf(out, terms)["findings"] == []
    assert redaction.MASK.encode() in out


def test_the_whole_flow_check_store_verify_then_the_vendor_can_have_it_and_the_original_cannot():
    job_id, rfq, tag = _job()
    src = _add(rfq, "drawing.pdf", _pdf([f"Customer: {CUSTOMER}", f"PO 4500{tag}"], author=CUSTOMER))
    assert redaction.vendor_block(src)                                                                  # not checked: blocked
    art = redaction.check_file(job_id, src, BOSS, [f"4500{tag}"])
    assert art["status"] == "CLEAN" and art["redacted_file_id"] and art["findings_before"]
    red = art["redacted_file_id"]
    assert redaction.vendor_block(red)                                                                  # cleaned but not yet verified by a person: still blocked
    with pytest.raises(redaction.RedactionError) as e:
        redaction.verify(art["id"], {"email": "ops@allied.test", "role": "OPERATOR"})
    assert e.value.status == 403
    assert redaction.verify(art["id"], BOSS)["status"] == "VERIFIED"
    assert redaction.vendor_block(red) is None and redaction.vendor_block(src)                          # the redacted copy may go; the original never
    assert purchase_orders.attachments([red], strict=True)
    with pytest.raises(ValueError):
        purchase_orders.attachments([src], strict=True)
    assert redaction.for_job(job_id)[0]["status"] == "VERIFIED"


def test_a_changed_redacted_file_voids_the_verification():
    job_id, rfq, tag = _job()
    src = _add(rfq, "d.pdf", _pdf([f"Customer: {CUSTOMER}"]))
    art = redaction.check_file(job_id, src, BOSS)
    redaction.verify(art["id"], BOSS)
    red = art["redacted_file_id"]
    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB

    _corrupt(red)                                                                                       # the file was replaced after the check
    assert redaction.vendor_block(red) and redaction.for_job(job_id)[0]["status"] == "VOID"


def test_a_dwg_needs_an_engineers_attestation_tied_to_its_hash():
    job_id, rfq, tag = _job()
    dwg = _add(rfq, "drawing.dwg", b"AC1027" + b"\x00" * 200)
    art = redaction.check_file(job_id, dwg, BOSS)
    assert art["status"] == "PENDING" and art["redacted_file_id"] is None and redaction.vendor_block(dwg)
    for who in ({"email": "ops@allied.test", "role": "OPERATOR"}, BOSS):
        with pytest.raises(redaction.RedactionError):
            redaction.attest(job_id, dwg, who, "checked the title block and layers")                 # only the engineer list
    with pytest.raises(redaction.RedactionError):
        redaction.attest(job_id, dwg, ENG, "short")
    assert redaction.attest(job_id, dwg, ENG, "checked the title block and layers")["status"] == "VERIFIED"
    assert redaction.vendor_block(dwg) is None
    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB

    _corrupt(dwg)
    assert redaction.vendor_block(dwg)                                                                  # the file changed: void


def test_a_pdf_that_still_shows_the_customer_after_redaction_is_blocked(monkeypatch):
    job_id, rfq, tag = _job()
    src = _add(rfq, "d.pdf", _pdf([f"Customer: {CUSTOMER}"]))
    monkeypatch.setattr(redaction, "redact", lambda data, ext, terms: (data, {"painted": 0, "pages": 1, "ocr_available": False}))      # a redactor that failed
    art = redaction.check_file(job_id, src, BOSS)
    assert art["status"] == "BLOCKED" and art["redacted_file_id"] is None and redaction.vendor_block(src)


def test_a_job_with_no_customer_known_cannot_be_checked():
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB

    with SessionLocal() as db:
        j = JobDB(ref=f"JOB-NOC-{uuid.uuid4().hex[:5]}", title="t", customer="")
        db.add(j)
        db.commit()
        jid = j.id
    r = rfqs.create({"customer": "Temp", "subject": "tmp " + uuid.uuid4().hex[:4]}, BOSS, announce=False)
    fid = _add(r["number"], "d.pdf", _pdf(["x"]))
    with pytest.raises(redaction.RedactionError) as e:
        redaction.check_file(jid, fid, BOSS)
    assert e.value.status == 422


def test_the_vendor_email_draft_refuses_an_unchecked_drawing(monkeypatch):
    job_id, rfq, tag = _job()
    src = _add(rfq, "d.pdf", _pdf([f"Customer: {CUSTOMER}"]))
    files.set_design(src, True, BOSS)
    ctx = {"files": [{"type": "design_file", "id": src, "name": "d.pdf"}]}
    with pytest.raises(actions.ActionError) as e:
        actions._spec_ok({"type": "design_file", "id": src}, "vendor", ctx)
    assert "redaction check" in str(e.value)
    assert actions._spec_ok({"type": "design_file", "id": src}, "internal", ctx)["id"] == src            # inside Allied it is fine


def test_the_api_check_verify_and_roles(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    h = lambda email, role: {"Authorization": "Bearer " + staff_token(email, role)}  # noqa: E731
    job_id, rfq, tag = _job()
    src = _add(rfq, "d.pdf", _pdf([f"Customer: {CUSTOMER}"]))
    assert c.post("/api/v1/redaction/check", json={"job_id": job_id, "file_id": src}, headers=h("v@allied.test", "VIEWER")).status_code == 403
    r = c.post("/api/v1/redaction/check", json={"job_id": job_id, "file_id": src}, headers=h("ops.red@allied.test", "OPERATOR"))
    assert r.status_code == 200 and r.json()["status"] == "CLEAN"
    aid = r.json()["id"]
    assert c.post(f"/api/v1/redaction/{aid}/verify", headers=h("ops.red@allied.test", "OPERATOR")).status_code == 403
    assert c.post(f"/api/v1/redaction/{aid}/verify", headers=h("boss2.red@allied.test", "MANAGER")).status_code == 200
    got = c.get(f"/api/v1/redaction/job/{job_id}", headers=h("ops.red@allied.test", "OPERATOR")).json()
    assert got["enforced"] is True and got["artifacts"][0]["status"] == "VERIFIED"
    assert CUSTOMER in c.get(f"/api/v1/redaction/job/{job_id}/terms", headers=h("ops.red@allied.test", "OPERATOR")).json()["terms"]


def test_a_picture_only_pdf_with_no_ocr_cannot_be_cleaned_and_says_why():
    job_id, rfq, tag = _job()
    base = fitz.open()
    base.new_page()
    png = base[0].get_pixmap(dpi=100).tobytes("png")
    doc = fitz.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=png)
    src = _add(rfq, "scan.pdf", doc.tobytes())
    art = redaction.check_file(job_id, src, BOSS)
    assert art["status"] == "BLOCKED" and any(f["where"] == "unreadable picture page" for f in art["findings_before"]) and redaction.vendor_block(src)


def test_the_dxf_title_block_is_read_exactly_and_a_missing_field_stays_absent():
    from app.services import drawing_dxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    blk = doc.blocks.new("TITLEBLOCK")
    for tag in ("DWG_NO", "REV", "MATERIAL", "CUSTOMER", "TITLE"):
        blk.add_attdef(tag, (0, 0), text="")
    msp.add_blockref("TITLEBLOCK", (0, 0)).add_auto_attribs({"DWG_NO": "LNR-0042", "REV": "C", "MATERIAL": "AR400 12mm", "CUSTOMER": CUSTOMER, "TITLE": "Chute liner\\Pleft hand"})
    buf = io.StringIO()
    doc.write(buf)
    tb = drawing_dxf.title_block(buf.getvalue().encode())
    assert tb["fields"] == {"drawing_number": "LNR-0042", "revision": "C", "material": "AR400 12mm", "customer": CUSTOMER, "title": "Chute liner\nleft hand"} and tb["customer_found"]
    assert "scale" not in tb["fields"] and "drawn_by" not in tb["fields"]


def test_a_customer_name_in_the_file_name_is_a_finding_and_the_redacted_copy_never_keeps_it():
    job_id, rfq, tag = _job()
    src = _add(rfq, f"{CUSTOMER} chute liner rev B.pdf", _pdf(["Part: LINER-1"]))
    art = redaction.check_file(job_id, src, BOSS)
    assert any(f["where"] == "file name" for f in art["findings_before"]) and art["status"] == "CLEAN"
    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB

    with SessionLocal() as db:
        name = db.get(ItemFileDB, art["redacted_file_id"]).filename
    assert "owens" not in name.lower() and name.startswith("redacted-drawing-")


def test_one_changed_byte_on_disk_after_verification_blocks_the_vendor_even_if_the_record_is_untouched():
    from pathlib import Path

    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB

    job_id, rfq, tag = _job()
    src = _add(rfq, "d.pdf", _pdf([f"Customer: {CUSTOMER}"]))
    art = redaction.check_file(job_id, src, BOSS)
    redaction.verify(art["id"], BOSS)
    red = art["redacted_file_id"]
    assert redaction.vendor_block(red) is None
    with SessionLocal() as db:
        path = Path(db.get(ItemFileDB, red).stored_path)
    raw = bytearray(path.read_bytes())
    raw[-3] ^= 0x01
    path.write_bytes(bytes(raw))
    assert redaction.vendor_block(red) and redaction.for_job(job_id)[0]["status"] == "VOID"


@pytest.mark.skipif(not _REAL_OCR_AVAILABLE(), reason="Tesseract is not installed here")
def test_a_picture_only_drawing_is_read_by_real_ocr_painted_and_comes_out_clean(monkeypatch):
    """No fakes: a page that is only a picture of the title block, read by the local Tesseract."""
    from PIL import ImageDraw, ImageFont

    monkeypatch.setattr(redaction, "ocr_available", _REAL_OCR_AVAILABLE)
    monkeypatch.undo() if False else None
    monkeypatch.setattr(redaction, "ocr_words", redaction.__dict__["ocr_words"])
    job_id, rfq, tag = _job()
    try:
        font = ImageFont.truetype("arial.ttf", 54)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 54)
        except OSError:
            pytest.skip("no font on this machine to draw the test image")
    img = Image.new("RGB", (1500, 500), "white")
    d = ImageDraw.Draw(img)
    d.text((60, 60), "Customer: Owens Corning", fill="black", font=font)
    d.text((60, 200), "Part: LINER-1 Material AR400", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=842, height=300)
    page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes()
    terms = redaction.terms_for_job(job_id)
    before = redaction.scan_pdf(data, terms)
    assert any(f["where"] == "picture text" for f in before["findings"]), before
    out, report = redaction.redact_pdf(data, terms)
    assert report["painted"] >= 1
    after = redaction.scan_pdf(out, terms)
    assert after["findings"] == [] and after["unreadable_pages"] == [] and after["ocr_available"] is True
    src = _add(rfq, "scan.pdf", data)
    art = redaction.check_file(job_id, src, BOSS)
    assert art["status"] == "CLEAN" and art["redacted_file_id"]

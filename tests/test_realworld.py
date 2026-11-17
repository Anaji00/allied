"""Real-world mess: sideways and photographed pages, broken files, purchase orders written every which way, revised POs,
and Chinese vendors' replies. Test data only; OCR and the AI are fakes; nothing external is called."""

import io
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import NotificationDB
from app.models.orders import InboundOrderRequest
import importlib
from app.services.coordination import replies
from app.services.document_scanner_service import DocumentScannerService, find_payment_terms, find_po_number, ocr_upright
from app.services.order_orchestrator import OrderOrchestrator

dss = importlib.import_module("app.services.document_scanner_service")   # (the package also exports an instance of the same name)
SVC = DocumentScannerService()
GOOD = "PURCHASE ORDER\nPO Number: 9921\nCustomer: Owens Corning\nQty 2 Hammermill Screen Allied 300XL\nTotal $820.00\nShip date and terms: Net 30"


def _page(width=1200, height=1600):
    """A picture of a page with a dark block in the top-left corner, so a fake OCR can tell which way up it is."""
    img = Image.new("L", (width, height), 255)
    ImageDraw.Draw(img).rectangle([0, 0, 200, 200], fill=0)
    return img


def _upright(img):
    return img.getpixel((50, 50)) < 80 and img.getpixel((img.width - 50, img.height - 50)) > 200 and img.width < img.height


class FakeOCR:
    """Reads the page only when it is the right way up; sideways it produces the fragments real OCR does."""

    calls = 0

    @classmethod
    def image_to_string(cls, img, *a, **k):
        cls.calls += 1
        return GOOD if _upright(img) else "ll1 Iu |ii ww nn xz q7 mm vv ..\nzq xx cc bb 11 || // \\\\"


@pytest.fixture
def ocr(monkeypatch):
    FakeOCR.calls = 0
    monkeypatch.setattr(dss, "pytesseract", FakeOCR)
    monkeypatch.setattr(dss, "init_tesseract", lambda: "fake-tesseract")
    return FakeOCR


# ------------------------------------------------------------------ sideways, photographed and scanned pages


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_a_page_scanned_any_way_up_is_read(ocr, angle):
    page = _page().rotate(angle, expand=True)
    assert "PO Number: 9921" in ocr_upright(page)


def test_a_page_that_reads_fine_is_only_read_once(ocr):
    ocr_upright(_page())
    assert ocr.calls == 1


def test_a_phone_photo_with_a_rotation_note_is_turned_the_right_way_before_reading(ocr):
    img = _page().rotate(90, expand=True)                                      # the picture as stored: on its side
    exif = Image.Exif()
    exif[0x0112] = 6                                                          # the phone's note: "turn 90 degrees clockwise to see it upright"
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", exif=exif)
    text = SVC.extract_text_from_image(buf.getvalue(), "photo.jpg")
    assert "PO Number: 9921" in text and ocr.calls == 1                        # fixed by the note, no guessing needed


def test_a_tiny_low_resolution_fax_is_enlarged_before_reading(ocr):
    small = _page(300, 400)
    from app.services.document_scanner_service import _prepare

    assert min(_prepare(small).size) >= 1000


def _scanned_pdf(pages, angle=90, set_rotation=None):
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        img = _page().rotate(angle, expand=True)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        page = doc.new_page(width=img.width * 0.5, height=img.height * 0.5)
        page.insert_image(page.rect, stream=buf.getvalue())
        if set_rotation is not None:
            page.set_rotation(set_rotation)
        doc.insert_pdf if False else None
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.parametrize("angle,flag", [(90, None), (180, None), (270, None), (0, 90)])
def test_a_scanned_pdf_sent_sideways_is_read(ocr, angle, flag):
    pdf = _scanned_pdf(1, angle=angle, set_rotation=flag)
    text = SVC.extract_text_from_pdf(pdf, "scan.pdf")
    if flag is None:
        assert "PO Number: 9921" in text
    else:
        assert "NOTICE" not in text or "PO Number" in text                      # a page flagged as rotated is read one way or another


def test_a_long_packet_says_which_pages_were_not_read(ocr):
    scanned = _scanned_pdf(11)
    text = SVC.extract_text_from_pdf(scanned, "packet.pdf")
    assert "A person must check the rest" in text and "[NOTE:" in text and text.count("--- PAGE") == dss._MAX_OCR_PAGES
    import pymupdf

    doc = pymupdf.open()
    for i in range(34):
        doc.new_page().insert_text((72, 72), f"Line items on page {i + 1} " + "x" * 50)
    long_text = SVC.extract_text_from_pdf(doc.tobytes(), "long.pdf")
    assert f"Line items on page {dss._MAX_TEXT_PAGES} " in long_text and f"Line items on page {dss._MAX_TEXT_PAGES + 1} " not in long_text
    assert "34 pages" in long_text and "A person must check the rest" in long_text


@pytest.mark.parametrize("name,data", [
    ("empty.pdf", b""), ("junk.pdf", b"%PDF-1.4\nthis is not really a pdf"), ("photo.jpg", b"\xff\xd8\xff\xe0 not an image"),
    ("renamed.pdf", b"MZ\x90\x00\x03\x00\x00\x00 an executable renamed"), ("nothing.png", b""), ("big.txt", b"x" * 10),
])
def test_broken_and_mislabelled_files_never_crash_the_scan(name, data):
    out = SVC.scan_document(data, name)
    assert out.document_type is not None and out.line_items == []


def test_a_password_protected_pdf_is_reported_not_crashed():
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "PO 9921 secret " + "x" * 60)
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    out = SVC.scan_document(data, "locked.pdf")
    assert "PO Number: 9921" not in out.raw_transcribed_text and out.line_items == []


# ------------------------------------------------------------------ purchase orders written every which way


@pytest.mark.parametrize("text,expected", [
    ("PO 9921", "9921"), ("PO#9922", "9922"), ("P.O. No. 99-23", "99-23"), ("P.O. 99-24 please", "99-24"), ("PO: 9925", "9925"),
    ("Purchase Order: 4500123", "4500123"), ("Purchase Order No. 4500124", "4500124"), ("Your PO - 9926", "9926"),
    ("po number: 9927", "9927"), ("PO 9921 (revised)", "9921"), ("Customer PO # OC-77-12", "OC-77-12"), ("PO-9928", "9928"),
    ("P O 9929", "9929"), ("Ref PO 9930/A", "9930/A"),
])
def test_po_numbers_are_found_however_they_are_written(text, expected):
    assert find_po_number(text) == expected


@pytest.mark.parametrize("text", ["P.O. Box 1234, Edmonton", "PO Box 77", "Allied PO 9262", "ALLIED PO# 9263", "Vendor PO 9264", "Date: 2026-11-06", "no number here",
                                  "Purchase Order for screens", "PO TBD"])
def test_things_that_are_not_the_customers_po_number_are_not_taken_for_one(text):
    assert find_po_number(text) is None


def test_a_missing_po_number_is_never_made_up_and_is_the_same_every_time():
    text = "Owens Corning\nQty 2 Hammermill Screen Allied 300XL $410.00\nPurchase order for the screens, thanks, Karen"
    a, b = SVC.scan_document(text.encode(), "scan.txt"), SVC.scan_document(text.encode(), "scan.txt")
    for r in (a, b):
        f = r.fields["customer_po_number"]
        assert f.raw_value == "NOT FOUND" and f.confidence == 0.0 and not f.normalized_value
        assert any("No customer reference" in n for n in r.compliance_notes)
    assert a.fields["customer_po_number"].raw_value == b.fields["customer_po_number"].raw_value


@pytest.mark.parametrize("text", [
    "PO 9925  Owens Corning\nQty 1000 Hammermill Screen Allied 300XL  $410.00",
    "P.O. No. 99-23\nOWENS CORNING INSULATING SYSTEMS CANADA LP\nQty: 2   Hammermill Screen Allied 300XL   Unit Price $410",
    "PO 9926 Owens Corning\n2 ea Hammermill Screen Allied 300XL @ $410.00"])
def test_a_purchase_order_with_no_heading_is_still_recognised(text):
    out = SVC.scan_document(text.encode(), "scan.txt")
    assert out.document_type.value == "CUSTOMER_PO" and out.fields["customer_po_number"].confidence > 0.9


def test_a_supplier_invoice_that_quotes_a_po_is_still_an_invoice_not_an_order():
    text = "VENDOR INVOICE\nInvoice No: 5531\nRe: PO 9262 Qty 2 liners $410.00\nAmount due $820.00"
    assert SVC.scan_document(text.encode(), "inv.txt").document_type.value == "VENDOR_INVOICE"


def test_a_page_that_merely_mentions_a_po_number_is_not_an_order():
    text = "Mill test report\nHeat number HT-300XL-8819\nChemical analysis C 0.2\nPO 9262"
    assert SVC.scan_document(text.encode(), "mtr.txt").document_type.value != "CUSTOMER_PO"


@pytest.mark.parametrize("text,expected", [("Terms: Net 45", "Net 45 Days"), ("NET30", "Net 30 Days"), ("terms net 60 days", "Net 60 Days"),
                                           ("PO 9260 $1,150.00 total 150 units", None), ("Net 0", None), ("net 999", None)])
def test_payment_terms_come_only_from_what_is_written(text, expected):
    assert find_payment_terms(text) == expected


def test_a_po_number_or_price_containing_150_or_60_does_not_change_the_terms():
    text = "PO 9260\nKnauf Insulation\nQty 2 Hammermill Screen Allied 300XL $1,150.00"
    terms = SVC.scan_document(text.encode(), "scan.txt").fields["payment_terms"]
    assert terms.raw_value not in ("Net 150 Days", "Net 60 Days") or terms.confidence <= 0.5


def test_a_customer_nobody_knows_stays_unknown_and_is_not_turned_into_owens_corning():
    text = "PURCHASE ORDER\nPO 7770\nCustomer: Acme Welding Supply Ltd\nShip to: 12 Main St, Edmonton, AB\nQty 2 Hammermill Screen Allied 300XL $410"
    out = SVC.scan_document(text.encode(), "acme.txt")
    name = out.fields["customer_name"]
    assert name.raw_value == "NOT FOUND" and name.confidence == 0.0 and not out.fields["customer_id"].normalized_value
    assert "Owens" not in str(out.fields["shipping_address"].normalized_value)
    assert any("could not be matched" in n for n in out.compliance_notes)


def test_a_page_with_no_recognisable_items_gets_no_invented_ones():
    text = "PURCHASE ORDER\nPO 7771\nCustomer: Owens Corning\nPlease bring the barbecue and the broken screen protectors to the meeting"
    out = SVC.scan_document(text.encode(), "odd.txt")
    assert out.line_items == [] and any("No line items" in n for n in out.compliance_notes)
    action = out.suggested_actions[0]
    assert action.prefill_data["item_id"] == "" and action.prefill_data["unit_price"] == ""


@pytest.mark.parametrize("line,qty", [
    ("1  Hammermill Screen Allied 300XL   Qty 2   $410.00", 2),
    ("Qty 3  Hammermill Screen Allied 300XL  $410.00", 3),
    ("Hammermill Screen Allied 300XL x 4 @ $410.00", 4),
    ("5 ea Hammermill Screen Allied 300XL $410.00", 5),
    ("Hammermill Screen Allied 300XL   Qty: 1,000   $410.00", 1000),
    ("Hammermill Screen Allied 300XL (6) $410.00", 6),
    ("2 | Hammermill Screen Allied 300XL | $410.00", 2),
])
def test_quantities_are_not_confused_with_prices(line, qty):
    out = SVC.scan_document(f"PURCHASE ORDER\nPO 7780\nOwens Corning\n{line}".encode(), "q.txt")
    assert [l.quantity for l in out.line_items] == [Decimal(qty)], out.line_items


# ------------------------------------------------------------------ a revised PO


def _req(po, qty):
    return InboundOrderRequest(customer_name="Owens Corning", customer_po_number=po, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
                               line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": qty, "unit_price": 410}])


def _alerts(prefix):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB).all() if n.dedup_key.startswith(prefix)]


def test_a_revised_po_is_flagged_instead_of_silently_keeping_the_old_order():
    po = f"REV-{uuid.uuid4().hex[:6].upper()}"
    first = OrderOrchestrator().process_inbound_order(_req(po, 2))
    again = OrderOrchestrator().process_inbound_order(_req(po, 2))
    assert first.sales_order_number == again.sales_order_number and not any("arrived again" in m for m in again.messages)
    assert not _alerts(f"po-revised:{first.sales_order_number}:")
    revised = OrderOrchestrator().process_inbound_order(_req(po, 5))
    assert revised.sales_order_number == first.sales_order_number
    assert any("arrived again with different" in m and "NOT changed" in m for m in revised.messages)
    assert len(_alerts(f"po-revised:{first.sales_order_number}:")) == 1
    OrderOrchestrator().process_inbound_order(_req(po, 5))                                    # the same revision again: still one alert
    assert len(_alerts(f"po-revised:{first.sales_order_number}:")) == 1


# ------------------------------------------------------------------ Chinese vendors' replies


TODAY = date(2026, 11, 6)


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "off")


@pytest.mark.parametrize("body,ship", [
    ("您好，PO 9277 预计2026年11月20日发货，谢谢", date(2026, 11, 20)),
    ("预计11月20日发货", date(2026, 11, 20)),
    ("预计 11月20号 发货", date(2026, 11, 20)),
    ("Expected to ship 2026/11/20", date(2026, 11, 20)),
    ("We will ship on 2026.11.20", date(2026, 11, 20)),
    ("Ships 20 Nov", date(2026, 11, 20)),
    ("ETD 20.11.2026", date(2026, 11, 20)),
])
def test_a_chinese_vendors_ship_dates_are_read_in_the_ways_they_write_them(body, ship):
    got = replies.read("sales@kws.test", "RE: PO 9277", body, TODAY)
    assert got["ship_date"] == ship and not got["unclear"]


def test_a_slash_date_that_could_mean_two_things_goes_to_a_person_not_into_the_order():
    got = replies.read("sales@kws.test", "RE: PO 9277", "Expected ship 12/11", TODAY)               # 12 Nov or 11 Dec?
    assert got["ship_date"] is None and got["unclear"] and got["ambiguous"] == [["2026-12-11", "2026-11-12"]]
    assert replies.read("sales@kws.test", "RE: PO 9277", "Expected ship 20/11", TODAY)["ship_date"] == date(2026, 11, 20)     # 20 can only be the day
    assert replies.read("sales@kws.test", "RE: PO 9277", "Expected ship 11/11", TODAY)["ship_date"] == date(2026, 11, 11)     # same either way


@pytest.mark.parametrize("body", ["春节后发货", "货好了就发", "请问什么时候付款?", "下周三发货", ""])
def test_a_reply_with_no_real_date_is_unclear_and_never_guessed(body):
    got = replies.read("sales@kws.test", "RE: PO 9277", body, TODAY)
    assert got["ship_date"] is None and got["unclear"]


@pytest.mark.parametrize("body,photos", [("货已发出，附照片", True), ("已发货 2026年11月5日，照片见附件", True), ("已出货，请查收图片", True),
                                          ("集装箱已装船，已离港", False), ("The goods were shipped yesterday 2026-11-05, photos attached", True), ("not yet, still welding", False)])
def test_chinese_shipped_and_photo_words_are_understood(body, photos):
    got = replies.read("sales@kws.test", "RE: PO 9277", body, TODAY)
    assert got["photos"] is photos
    assert got["shipped"] is ("not yet" not in body)


def test_a_vendors_date_far_in_the_past_or_future_is_not_trusted():
    assert replies.read("sales@kws.test", "RE: PO 9277", "ship 2019-05-01", TODAY)["ship_date"] is None
    assert replies.read("sales@kws.test", "RE: PO 9277", "ship 2031-05-01", TODAY)["ship_date"] is None


def test_a_long_scan_is_still_parsed_and_says_part_of_it_was_not_read(ocr):
    out = SVC.scan_document(_scanned_pdf(11), "packet.pdf")
    assert out.document_type.value == "CUSTOMER_PO" and out.fields["customer_po_number"].normalized_value == "9921"
    assert any("Only part of this file" in n for n in out.compliance_notes)

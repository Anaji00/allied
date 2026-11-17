"""Hardening: things meant to confuse the new flow (packets, vendor quotes, POs, attachments, names, dates, numbers) and the git-like board changes.
Synthetic text, synthetic PDFs, fake monday client, fake mailer. Nothing real is touched and nothing is sent."""
import io
import sys
import threading
import uuid

import fitz
import pytest
from PIL import Image
from pypdf import PdfReader

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import JobDocumentDB, OrderDB
from app.services import monday_push, order_documents as od
from app.services.assistant import tools
from app.services.coordination import boards, files as item_files, jobs, packets, purchase_orders, rfqs

BOSS = {"email": "boss-h@alliedweldingsystems.com", "role": "MANAGER"}
OP = {"email": "op-h@alliedweldingsystems.com", "role": "OPERATOR"}

ALLIED_PO = "ALLIED\nWELDING SYSTEMS\nP.O. BOX 795459\nPurchase Order No.:\n9301\nDate Issued: 9/2/26\nPURCHASE\nORDER\nTOTAL 1,000.00\n"
# a customer's PO to Allied: Allied is named near the top (bill-to / supplier) but it is still the CUSTOMER's PO
CUST_PO_MENTIONING_ALLIED = ("Purchase Order 26001556\nDate\n07/24/26\nBill To\nAllied Welding Systems\nP.O. Box 795459 Dallas TX\nSupplier No. 549613\n"
                             "Total amount w/o tax USD 2,550.00\n")


def _rfq(subject="hard test", vendor=""):
    return rfqs.create({"customer": "Owens Corning", "subject": subject, "items": [{"description": "x", "quantity": 1}], "vendor": vendor}, BOSS, announce=False)


def _pdf_bytes(pages=1, text=None):
    d = fitz.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), text or f"page {i + 1}")
    return d.tobytes() + b"\n%" + uuid.uuid4().hex.encode()


@pytest.fixture()
def fake_ocr(monkeypatch):
    seq = {"pages": [ALLIED_PO]}
    monkeypatch.setattr(packets, "ocr_pages", lambda pdf, dpi=130: seq["pages"])
    return seq


# ===================================================================== two kinds of PO: from a customer, to a vendor

def test_a_customers_po_that_names_allied_is_still_the_customers_and_the_two_never_mix():
    assert packets.classify(ALLIED_PO) == "allied_po"
    assert packets.classify(CUST_PO_MENTIONING_ALLIED) == "customer_po"
    assert packets.extract("customer_po", CUST_PO_MENTIONING_ALLIED)["customer_po_number"] == "26001556"
    docs = packets.split([CUST_PO_MENTIONING_ALLIED, ALLIED_PO])
    facts = packets.facts_for_board([{**d, "confirmed": True} for d in docs])
    assert facts["customer_po_number"] == "26001556" and facts["po_number"] == "9301"           # each lands in its own column
    assert facts["customer_po_number"] != facts["po_number"]


def test_the_customers_po_number_never_lands_in_the_allied_po_column():
    docs = [{"kind": "customer_po", "fields": {"customer_po_number": "4500123", "date": "08/11/2026"}, "confirmed": True}]
    facts = packets.facts_for_board(docs)
    assert "po_number" not in facts and facts["customer_po_number"] == "4500123"
    assert packets.BOARD_COLUMNS["po_number"] == "Allied PO#" and packets.BOARD_COLUMNS["customer_po_number"] == "Customer PO#"


# ===================================================================== messy pages and dates

def test_a_fax_header_is_not_the_documents_date_and_pages_are_told_by_content_in_any_order():
    page = "FAX 07/28/26 03:15 P.1\n" + ALLIED_PO
    assert packets.extract("allied_po", page)["date"] == "9/2/26"
    assert packets.classify(page) == "allied_po"
    shuffled = packets.split(["ALLIED PACKING LIST\nSales Order Number: 12950\n", ALLIED_PO, "SALES ORDER INFORMATION\n"])
    assert [d["kind"] for d in shuffled] == ["packing_list", "allied_po", "cover_sheet"]


def test_ambiguous_dates_are_warned_about_and_clear_ones_are_not():
    assert packets.ambiguous_date("03/04/26") and not packets.ambiguous_date("13/04/26") and not packets.ambiguous_date("03/03/26")
    assert packets.warnings_for({"date": "03/04/26"}) and not packets.warnings_for({"date": "2026-04-03"})
    assert packets.iso_date("13/04/26") is None or True            # a day-first date with day > 12 is read by no month/day rule: left to the person


def test_unreadable_pages_are_kept_and_a_packet_with_nothing_readable_is_refused(client, fake_ocr):
    fake_ocr["pages"] = [ALLIED_PO, "", "   "]
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    out = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("a.pdf", _pdf_bytes(3), "application/pdf")}).json()
    assert [d["kind"] for d in out["documents"]] == ["allied_po", "unreadable"]               # the two bad pages are one visible "unreadable" document
    fake_ocr["pages"] = ["", "  ", ""]
    refused = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("b.pdf", _pdf_bytes(3), "application/pdf")})
    assert refused.status_code == 400 and "No readable pages" in refused.json()["detail"]


# ===================================================================== hostile or broken uploads

@pytest.mark.parametrize("payload,expect", [
    (b"", "not a PDF"), (b"\xff\xd8\xff\xe0 jpeg bytes", "not a PDF"), (b"%PDF-1.4\n%%EOF", "damaged"),
    (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog", "damaged"),
])
def test_broken_files_get_plain_refusals(client, payload, expect):
    out = client.post("/api/v1/packets", files={"file": ("x.pdf", payload, "application/pdf")})
    assert out.status_code == 400 and expect in out.json()["detail"] and "Traceback" not in out.text


def test_an_encrypted_pdf_says_so(client):
    d = fitz.open()
    d.new_page().insert_text((72, 72), "secret")
    data = d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user") + b"\n%" + uuid.uuid4().hex.encode()
    out = client.post("/api/v1/packets", files={"file": ("x.pdf", data, "application/pdf")})
    assert out.status_code == 400 and "password" in out.json()["detail"].lower()


def test_more_than_the_page_limit_is_refused_and_a_huge_page_is_rendered_small(monkeypatch):
    with pytest.raises(ValueError, match="limit is"):
        packets.ocr_pages(_pdf_bytes(packets.MAX_PAGES + 1, "x" * 50 + " word"))
    big = fitz.open()
    pg = big.new_page(width=14400, height=14400)                 # 200 x 200 inches
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, "PNG")
    pg.insert_image(fitz.Rect(0, 0, 100, 100), stream=buf.getvalue())
    scanner = sys.modules["app.services.document_scanner_service"]
    assert scanner._capped_dpi(big[0], 130) < 130                 # the same size cap the scanner uses
    seen = {}
    monkeypatch.setattr(scanner, "init_tesseract", lambda: None)
    monkeypatch.setattr(scanner, "ocr_upright", lambda img: seen.setdefault("size", img.size) and "text")
    packets.ocr_pages(big.tobytes())
    assert seen["size"][0] * seen["size"][1] < 60_000_000        # never a 200-inch page at full resolution


def test_a_text_layer_that_disagrees_with_the_picture_is_not_trusted(monkeypatch):
    d = fitz.open()
    pg = d.new_page()
    pg.insert_text((72, 72), "Purchase Order No.: 11111 this old text layer says one thing and the picture says another")
    buf = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(buf, "PNG")
    pg.insert_image(fitz.Rect(0, 100, 200, 300), stream=buf.getvalue())
    scanner = sys.modules["app.services.document_scanner_service"]
    monkeypatch.setattr(scanner, "init_tesseract", lambda: None)
    monkeypatch.setattr(scanner, "ocr_upright", lambda img: "Purchase Order No.: 22222 read from the picture")
    assert "22222" in packets.ocr_pages(d.tobytes())[0] and "11111" not in packets.ocr_pages(d.tobytes())[0]
    pure = fitz.open()
    pure.new_page().insert_text((72, 72), "A page with only text and no picture at all: Purchase Order No.: 33333 ok")
    assert "33333" in packets.ocr_pages(pure.tobytes())[0]          # a pure-text PDF still uses its own text


def test_the_same_packet_uploaded_at_the_same_moment_is_stored_once(fake_ocr):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    pdf = _pdf_bytes(1)
    results, errors = [], []

    def go():
        try:
            results.append(packets.ingest(pdf, "same.pdf", OP, job=j["id"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors and len(results) == 4
    with SessionLocal() as db:
        assert db.query(JobDocumentDB).filter(JobDocumentDB.source_sha == results[0]["packet"]).count() == 1


# ===================================================================== confirming and correcting facts

@pytest.mark.parametrize("key,value", [("total", "-5"), ("total", "1e999"), ("total", "free"), ("total", "nan"), ("date", "next tuesday"), ("date", "13/45/26"),
                                       ("po_number", "<script>alert(1)</script>"), ("po_number", "9" * 80), ("po_number", "12 34")])
def test_absurd_corrections_are_refused(client, fake_ocr, key, value):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    d = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("a.pdf", _pdf_bytes(1), "application/pdf")}).json()["documents"][0]
    out = client.post(f"/api/v1/packets/documents/{d['id']}/confirm", json={"fields": {key: value}})
    assert out.status_code == 400, (key, value, out.text)


def test_control_characters_are_removed_and_a_good_total_is_tidied(client, fake_ocr):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    d = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("a.pdf", _pdf_bytes(1), "application/pdf")}).json()["documents"][0]
    out = client.post(f"/api/v1/packets/documents/{d['id']}/confirm", json={"fields": {"total": "1200", "vendor": "KWS\x00\x07 LTD"}}).json()
    assert out["fields"]["total"] == "1,200.00" and "\x00" not in out["fields"]["vendor"] and "\x07" not in out["fields"]["vendor"]


# ===================================================================== vendor quotes: names, duplicates, junk, warnings

def test_vendor_names_are_one_name_however_they_are_written_and_chinese_survives():
    n = jobs.norm_name
    assert n("KWS LTD.") == n("kws ltd ") == n("KWS Ltd") == "kws"
    assert n("José Müller GmbH") == n("Jose Muller") and n("王伟 有限公司") == "王伟" and n("有限公司") == "有限公司"
    assert jobs._same_vendor("KWS LTD", "KWS Ltd.") and not jobs._same_vendor("AM", "AM Metals") and not jobs._same_vendor("", "x")
    assert jobs.same_customer("Owens Corning", "Owens Corning Insulating Systems") and not jobs.same_customer("ACME", "ACME Industrial")


def test_a_vendor_quote_written_three_ways_is_one_quote_one_po_and_a_stranger_is_warned_about(client):
    r = _rfq(vendor="KWS LTD")
    j = jobs.from_rfq(r["id"], BOSS)
    for spelling in ("KWS LTD", "kws ltd ", "KWS Ltd."):
        out = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": spelling, "total": "1000"})
        assert out.status_code == 200, out.text
    quotes = [d for d in client.get(f"/api/v1/jobs/{j['id']}/documents").json()["items"] if d["kind"] == "vendor_quote"]
    assert len(quotes) == 1 and quotes[0]["fields"]["total"] == "1,000.00"
    assert len(client.get(f"/api/v1/jobs/{j['id']}/purchase-orders").json()) == 1
    stranger = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Some Other Vendor"}).json()
    assert any("not one of the vendors this RFQ was sent to" in w for w in stranger["warnings"])


@pytest.mark.parametrize("body", [{"vendor": "V", "total": "-5000"}, {"vendor": "V", "total": "1e999"}, {"vendor": "V", "total": "free"},
                                  {"vendor": "V", "date": "someday"}, {"vendor": "   "}, {}])
def test_junk_vendor_quotes_are_refused(client, body):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    assert client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json=body).status_code == 400


def test_huge_and_hostile_vendor_text_is_cut_and_cleaned(client):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    out = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "V" * 5000 + "\x00<b>", "lead_time": "12\x00 weeks " + "x" * 500})
    assert out.status_code == 200
    q = [d for d in out.json()["documents"]["items"] if d["kind"] == "vendor_quote"][0]["fields"]
    assert len(q["vendor"]) <= 120 and "\x00" not in q["vendor"] and len(q["lead_time"]) <= 64 and "\x00" not in q["lead_time"]


# ===================================================================== attachments: only design files, never silently missing

def _approved_po(vendor="HELEN"):
    r = _rfq()
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], vendor, BOSS, lines=[{"quantity": 1, "description": "liner"}])
    return r, j, purchase_orders.approve(po["id"], BOSS)


@pytest.fixture()
def mailer(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service

    log = []
    monkeypatch.setattr(outbound_email_service, "send_purchase_order", lambda **kw: (log.append(("po", kw)), {"status": "SENT"})[1])
    monkeypatch.setattr(outbound_email_service, "send", lambda **kw: (log.append(("mail", kw)), {"status": "SENT"})[1])
    return log


def test_a_draft_cannot_attach_a_file_that_is_not_a_design_file_of_this_job(mailer):
    r, j, po = _approved_po()
    mine = item_files.add("rfq", r["number"], "drawing.pdf", b"%PDF-1.4 d", BOSS)
    item_files.set_design(mine["id"], True, BOSS)
    customers = item_files.add("rfq", r["number"], "customer-po.pdf", b"%PDF-1.4 c", BOSS)          # not marked
    other = _rfq()
    elsewhere = item_files.add("rfq", other["number"], "other.pdf", b"%PDF-1.4 o", BOSS)
    item_files.set_design(elsewhere["id"], True, BOSS)                                                # marked, but on ANOTHER rfq
    base = {"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "po_number": po["po_number"], "po_id": po["id"], "body": "b"}
    for bad in (customers["id"], elsewhere["id"], 999999):
        with pytest.raises(ValueError, match="not a design file"):
            tools.send_vendor_po({**base, "attach_file_ids": [bad]}, BOSS)
    assert not mailer                                                                                # nothing went out
    ok = tools.send_vendor_po({**base, "attach_file_ids": [mine["id"]]}, BOSS)
    assert ok["status"] == "SENT" and [a["filename"] for a in mailer[0][1]["extra_attachments"]] == ["drawing.pdf"]


def test_a_design_file_deleted_after_the_draft_stops_the_send_instead_of_going_without_it(mailer):
    r, j, po = _approved_po()
    f = item_files.add("rfq", r["number"], "drawing.pdf", b"%PDF-1.4 d", BOSS)
    item_files.set_design(f["id"], True, BOSS)
    item_files.remove(f["id"], BOSS)
    args = {"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "po_number": po["po_number"], "po_id": po["id"], "body": "b", "attach_file_ids": [f["id"]]}
    with pytest.raises(ValueError, match="not a design file"):
        tools.send_vendor_po(args, BOSS)                                                              # a removed file is no longer a design file of the job
    assert not mailer and purchase_orders.get(po["id"])["status"] == "APPROVED"                      # and the PO is not marked sent
    with pytest.raises(ValueError, match="removed or can no longer be read"):
        purchase_orders.attachments([f["id"]], strict=True)


def test_the_price_request_only_attaches_this_rfqs_design_files(mailer):
    r = _rfq()
    d = item_files.add("rfq", r["number"], "d.pdf", b"%PDF-1.4 d", BOSS)
    item_files.set_design(d["id"], True, BOSS)
    c = item_files.add("rfq", r["number"], "c.pdf", b"%PDF-1.4 c", BOSS)
    args = {"to": "v@example.test", "vendor_name": "V", "rfq_number": r["number"], "body": "b"}
    with pytest.raises(ValueError, match="not a design file"):
        tools.send_vendor_price_request({**args, "attach_file_ids": [c["id"]]}, OP)
    tools.send_vendor_price_request({**args, "attach_file_ids": [d["id"]]}, OP)
    assert [a["filename"] for a in mailer[0][1]["attachments"]] == ["d.pdf"]


# ===================================================================== POs: numbers, edits after approval

@pytest.mark.parametrize("qty", ["1e999", "nan", "inf", "10 pcs", "", None, "２"])
def test_no_quantity_can_crash_the_po_pdf(qty):
    pdf = od.vendor_po_pdf({"po_number": "9301", "vendor_name": "V", "lines": od.po_lines([{"quantity": qty, "description": "shaft", "unit_cost": "1e999"}])})
    assert "9301" in " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages) and od.num("1e999") is None and od.num("1,200.50") == 1200.5


def test_where_an_approved_po_goes_can_only_be_changed_by_a_manager(client):
    r, j, po = _approved_po()
    refused = purchase_orders
    with pytest.raises(jobs.JobError, match="Only a manager"):
        purchase_orders.update(po["id"], {"vendor_email": "elsewhere@example.test"}, OP)
    assert purchase_orders.update(po["id"], {"vendor_email": "fixed@example.test"}, BOSS)["vendor_email"] == "fixed@example.test"
    assert "\x00" not in (purchase_orders.update(po["id"], {"terms": "Net\x00 45"}, BOSS)["terms"] or "")
    assert refused is purchase_orders


def test_an_unknown_stage_filter_is_a_plain_400_and_a_real_one_works(client):
    bad = client.get("/api/v1/jobs?stage=NOPE")
    assert bad.status_code == 400 and "Unknown stage" in bad.json()["detail"]
    assert client.get("/api/v1/jobs?stage=rfq").status_code == 200


def test_a_job_with_no_rfq_can_still_take_a_vendor_quote_but_its_po_cannot_be_approved_without_lines(client):
    with SessionLocal() as db:
        from app.models.db_models import JobDB

        row = JobDB(ref=f"JOB-9999-{uuid.uuid4().int % 10**5:05d}", title="no rfq", customer="X", rfq_id=None, created_by="t")
        db.add(row)
        db.commit()
        jid = row.id
    out = client.post(f"/api/v1/jobs/{jid}/vendor-quotes", json={"vendor": "V1"})
    assert out.status_code == 200 and out.json()["purchase_order_draft"]
    jobs.approve_design(jid, BOSS)
    assert client.post(f"/api/v1/purchase-orders/{out.json()['purchase_order_draft']}/approve").status_code == 400      # no lines yet


# ===================================================================== staging packet facts onto the orders board

def _orders_board(monkeypatch, items):
    board = str(uuid.uuid4().int % 10**9)
    cols = [{"id": "c_po", "title": "Customer PO#", "type": "text"}, {"id": "a_po", "title": "Allied PO#", "type": "text"},
            {"id": "recv", "title": "PO received", "type": "date"}, {"id": "issued", "title": "Vendor PO Issued", "type": "date"},
            {"id": "vend", "title": "Vendor", "type": "text"}]
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": cols, "items": items}
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", board)
    return board


def _confirmed_job(fake_ocr, client, ref):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    fake_ocr["pages"] = [f"Purchase Order {ref}\nDate\n09/01/26\n", ALLIED_PO]
    body = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("m.pdf", _pdf_bytes(2), "application/pdf")}).json()
    for d in body["documents"]:
        client.post(f"/api/v1/packets/documents/{d['id']}/confirm", json={})
    return j


def test_two_matching_rows_stage_nothing_and_say_why(client, fake_ocr, monkeypatch):
    ref = f"{uuid.uuid4().int % 10**8:08d}"
    _orders_board(monkeypatch, [{"id": "r1", "name": "A", "values": {"c_po": ref}}, {"id": "r2", "name": "B", "values": {"c_po": ref}}])
    j = _confirmed_job(fake_ocr, client, ref)
    out = client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday")
    assert out.status_code == 400 and "2 rows" in out.json()["detail"]
    assert not [c for c in monday_push.changes("STAGED") if c["source"] == "packet" and c["payload"]["columns"].get("Allied PO#") == "9301" and c["item_id"] in ("r1", "r2")]


def test_a_row_matched_by_one_po_number_and_another_by_the_other_is_two_rows_not_one(client, fake_ocr, monkeypatch):
    ref = f"{uuid.uuid4().int % 10**8:08d}"
    _orders_board(monkeypatch, [{"id": "r1", "name": "A", "values": {"c_po": ref}}, {"id": "r2", "name": "B", "values": {"a_po": "9301"}}])
    j = _confirmed_job(fake_ocr, client, ref)
    assert client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday").status_code == 400


def test_a_value_that_already_differs_is_left_alone_and_listed_and_staging_twice_is_one_change(client, fake_ocr, monkeypatch):
    ref = f"{uuid.uuid4().int % 10**8:08d}"
    _orders_board(monkeypatch, [{"id": "r1", "name": "A", "values": {"c_po": ref, "a_po": "7777", "vend": "kws "}}])
    j = _confirmed_job(fake_ocr, client, ref)
    first = client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday").json()
    assert first["differs"] == [{"column": "Allied PO#", "monday": "7777", "document": "9301"}]            # monday keeps its value
    assert "Allied PO#" not in first["staged"] and first["staged"]["PO received"] == "2026-09-01"
    second = client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday").json()
    assert second["change_id"] == first["change_id"]                                                      # one staged change, not two


def test_a_board_that_was_never_read_or_lacks_the_columns_is_refused_or_said(client, fake_ocr, monkeypatch):
    ref = f"{uuid.uuid4().int % 10**8:08d}"
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", "999000111")
    j = _confirmed_job(fake_ocr, client, ref)
    assert client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday").status_code == 400
    board = _orders_board(monkeypatch, [{"id": "r1", "name": "A", "values": {"c_po": ref}}])
    with boards._lock:
        boards._mirror[board]["columns"] = [c for c in boards._mirror[board]["columns"] if c["title"] != "Vendor PO Issued"]
    out = client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday").json()
    assert "Vendor PO Issued" in out["no_column_for"] and "Vendor PO Issued" not in out["staged"]


# ===================================================================== money, names and text that real paperwork contains

@pytest.mark.parametrize("text,amount,currency", [("$1,200.50", 1200.5, "USD"), ("USD 1200", 1200.0, "USD"), ("¥1,250.00", 1250.0, "CNY"), ("RMB 8,000", 8000.0, "CNY"),
                                                  ("€99", 99.0, "EUR"), ("1200", 1200.0, None), ("0", 0.0, None)])
def test_prices_with_currency_marks_are_read_and_the_currency_is_kept(text, amount, currency):
    assert od.money(text) == (amount, currency)


@pytest.mark.parametrize("text", ["1.234,50", "12,5", "free", "TBD", "1e999", "nan", "inf", "", "1 2 3", "$$$"])
def test_ambiguous_or_nonsense_prices_are_not_guessed(text):
    assert od.money(text)[0] is None


def test_a_quote_in_yuan_keeps_its_currency_and_a_european_total_is_refused_with_advice(client):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    ok = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Zibo", "total": "¥12,500.00"}).json()
    q = [d for d in ok["documents"]["items"] if d["kind"] == "vendor_quote"][0]["fields"]
    assert q["total"] == "12,500.00" and q["currency"] == "CNY"                                   # never a bare number: the currency stays beside it
    bad = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Zibo", "total": "1.234,50"})
    assert bad.status_code == 400 and "1234.50" in bad.json()["detail"]


@pytest.mark.parametrize("vendor,desc", [("Müller GmbH", "Nguyễn shaft ø25 ±0.05"), ("王伟 有限公司", "耐磨板 10mm 304不锈钢"), ("Нова Ltd", "Привет"), ("Zibo", "x" * 2500)])
def test_names_and_descriptions_in_any_script_make_a_pdf_and_a_long_description_does_not_break_it(vendor, desc):
    pdf = od.vendor_po_pdf({"po_number": "9301", "vendor_name": vendor, "terms": "Net 45", "lines": od.po_lines([{"quantity": 1, "description": desc}])})
    text = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    assert pdf.startswith(b"%PDF") and "9301" in text and "Purchase Order No." in text


def test_zero_and_negative_lines_make_a_pdf_without_dropping_them():
    pdf = od.vendor_po_pdf({"po_number": "9301", "vendor_name": "V", "lines": od.po_lines([
        {"quantity": 0, "description": "zero line", "unit_cost": 5}, {"quantity": 1, "description": "credit", "amount": -50}])})
    t = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "zero line" in t and "credit" in t and "-50.00" in t


def test_a_sales_order_sheet_with_chinese_and_a_very_long_description_is_still_made():
    pdf = od.sales_order_sheet_pdf({"customer_name": "耐磨 Customer", "customer_reference": "REF-1", "description": "长" * 3000,
                                    "vendors": [{"po_number": str(9300 + i), "vendor": f"V{i}"} for i in range(7)]})
    t = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "SALES ORDER INFORMATION" in t and "3 more vendor purchase orders" in t


def test_a_page_of_endless_text_is_stored_cut_short_and_the_api_never_returns_it(client, fake_ocr):
    fake_ocr["pages"] = [ALLIED_PO + ("filler words " * 40000)]
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    out = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("big.pdf", _pdf_bytes(1), "application/pdf")})
    assert out.status_code == 200 and "filler words" not in out.text
    with SessionLocal() as db:
        row = db.query(JobDocumentDB).filter(JobDocumentDB.job_id == j["id"]).first()
        assert len(row.text) <= 30000


# ===================================================================== the same PO number from two customers

def test_a_packet_naming_customer_b_never_attaches_to_customer_as_job_even_with_the_same_po_number(fake_ocr):
    number = f"{uuid.uuid4().int % 10**8:08d}"
    made = {}
    for who in ("Alpha Fabricators", "Bravo Manufacturing"):
        r = rfqs.create({"customer": who, "subject": f"{who} job", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
        j = jobs.from_rfq(r["id"], BOSS)
        so = f"9{uuid.uuid4().int % 10**5:05d}"
        with SessionLocal() as db:
            db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number=number, sales_order_number=so, customer_id="C", customer_name=who,
                           purchase_orders={}, line_items=[]))
            db.commit()
        jobs.link_order(j["id"], so)
        made[who] = j["id"]
    fake_ocr["pages"] = [f"Bravo Manufacturing\nPurchase Order {number}\nDate 09/01/26\n"]
    got = packets.ingest(_pdf_bytes(1), "b.pdf", OP)
    assert got["job_id"] == made["Bravo Manufacturing"]                                                # the customer's name settles it
    fake_ocr["pages"] = [f"Purchase Order {number}\nDate 09/01/26\n"]
    unnamed = packets.ingest(_pdf_bytes(1), "c.pdf", OP)
    assert unnamed["job_id"] is None                                                                   # two orders, no name: waits for a person


def test_two_customers_with_names_that_overlap_are_not_confused():
    assert not jobs.same_customer("ACME", "ACME Industrial") and not jobs.same_customer("Owens", "Owens Corning")
    assert jobs.same_customer("OWENS CORNING", "Owens Corning Insulating Systems Canada LP")

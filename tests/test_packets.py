"""Job packets: what each scanned page is, only the facts printed on it, blanks left blank, stored once, attached only when clear. Synthetic text and a
synthetic PDF only (no real customer document); the OCR step is faked."""
import uuid

import fitz
import pytest

from app.services.coordination import jobs, packets, rfqs

BOSS = {"email": "boss-p@alliedweldingsystems.com", "role": "MANAGER"}

PO = "ALLIED\nWELDING SYSTEMS\nP.O. BOX 795459\nPurchase Order No.:\n9301\nDate Issued: 9/2/26\nTo: SOME VENDOR\nPURCHASE\nORDER\nTOTAL 1,000.00\n"
PO2 = "ALLIED\nWELDING SYSTEMS\nP.O. BOX 795459 Continued\nTOTAL 250.50\n"
SO_FILLED = "ALLIED\nSALES ORDER\nSales Order Number: 12950\nSales Order Date: Sep 2, 2026\n"
SO_BLANK = "ALLIED\nSALES ORDER\nSales Order Number:\nSales Order Date:\n"
PACK = "ALLIED PACKING LIST\nSales Order Number: 12950\nSales Order Date: Sep 2, 2026\n"
CUST = "Purchase Order 77001234\nDate\n09/01/26\nTotal amount w/o tax USD 5,000.00\n"
QUOTE = "ALLIED\nQUOTATION\nQuote Number: 09022026-1\nQuote Date: Sep 2, 2026\n"
VQUOTE = "QUOTATION\nSome Vendor LLC\nOrder Number: 0234999\nOrder Date: 9/1/2026\n"
COVER = "SALES ORDER INFORMATION\nCUSTOMER NAME: scribble\n"


def test_each_page_is_told_apart():
    assert packets.classify(COVER) == "cover_sheet" and packets.classify(PO) == "allied_po" and packets.classify(SO_FILLED) == "allied_so"
    assert packets.classify(PACK) == "packing_list" and packets.classify(QUOTE) == "allied_quote" and packets.classify(VQUOTE) == "vendor_quote"
    assert packets.classify(CUST) == "customer_po" and packets.classify("   \n") == "blank"
    assert packets.classify("Gmail  Someone <a@b.test>\nQuote for the job\n3 messages") == "email_printout"


def test_only_printed_facts_are_taken_and_a_blank_stays_blank():
    assert packets.extract("allied_po", PO) == {"po_number": "9301", "date": "9/2/26", "total": "1,000.00"}
    assert packets.extract("allied_so", SO_FILLED) == {"sales_order_number": "12950", "date": "Sep 2, 2026"}
    assert packets.extract("allied_so", SO_BLANK) == {}                                # the form's blank number is not invented
    assert packets.extract("customer_po", CUST)["customer_po_number"] == "77001234" and packets.extract("customer_po", CUST)["date"] == "09/01/26"
    assert packets.extract("allied_quote", QUOTE)["quote_number"] == "09022026-1"
    assert packets.extract("cover_sheet", COVER) == {}                                  # handwriting is not read


def test_neighbouring_pages_of_one_kind_are_one_document_and_later_pages_never_fill_gaps_with_guesses():
    docs = packets.split([COVER, PO, PO2, VQUOTE, CUST, SO_BLANK, PACK, "", QUOTE])
    kinds = [(d["kind"], d["page_from"], d["page_to"]) for d in docs]
    assert kinds == [("cover_sheet", 1, 1), ("allied_po", 2, 3), ("vendor_quote", 4, 4), ("customer_po", 5, 5), ("allied_so", 6, 6), ("packing_list", 7, 7), ("unreadable", 8, 8), ("allied_quote", 9, 9)]    # the blank page is kept, marked unreadable: nothing is dropped silently
    po = docs[1]["fields"]
    assert po["po_number"] == "9301" and po["total"] == "1,000.00"                      # the second page's total did not overwrite the first
    assert docs[4]["fields"] == {}                                                       # the blank sales order stays empty


def _pdf(pages: int) -> bytes:
    d = fitz.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"page {i + 1}")
    return d.tobytes()


@pytest.fixture()
def fake_ocr(monkeypatch):
    seq = {"pages": [COVER, PO, PO2, CUST, SO_BLANK]}
    monkeypatch.setattr(packets, "ocr_pages", lambda pdf, dpi=130: seq["pages"])
    return seq


def test_a_packet_is_read_stored_once_and_shown_on_its_job(client, fake_ocr):
    r = rfqs.create({"customer": "Owens Corning", "subject": "packet test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    pdf = _pdf(5) + b"\n%" + uuid.uuid4().hex.encode()                                  # unique bytes, still a PDF
    out = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("scan.pdf", pdf, "application/pdf")})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["already_read"] is False and [d["kind"] for d in body["documents"]] == ["cover_sheet", "allied_po", "customer_po", "allied_so"]
    again = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("scan.pdf", pdf, "application/pdf")}).json()
    assert again["already_read"] is True and len(again["documents"]) == 4                # the same file twice changes nothing
    shown = client.get(f"/api/v1/jobs/{j['id']}").json()["documents"]
    assert [d["kind"] for d in shown["items"]] == ["cover_sheet", "allied_po", "customer_po", "allied_so"]
    assert shown["found"] == {"po_numbers": ["9301"], "customer_po_number": "77001234"}  # only what is printed: no sales order number, because none is
    assert client.get(f"/api/v1/jobs/{j['id']}/documents").json()["found"]["po_numbers"] == ["9301"]
    assert client.get(f"/api/v1/packets/{body['packet']}/file").status_code == 200


def test_a_packet_with_no_job_waits_then_joins_the_job_whose_order_it_names(client, fake_ocr):
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB

    ref = f"{uuid.uuid4().int % 10**8:08d}"
    fake_ocr["pages"] = [f"Purchase Order {ref}\nDate 09/01/26\n", SO_FILLED]
    pdf = _pdf(2) + b"\n%" + uuid.uuid4().hex.encode()
    first = client.post("/api/v1/packets", files={"file": ("a.pdf", pdf, "application/pdf")}).json()
    assert first["job_id"] is None and any(p["packet"] == first["packet"] for p in client.get("/api/v1/packets").json())
    r = rfqs.create({"customer": "Owens Corning", "subject": "match test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    so = f"9{uuid.uuid4().int % 10**5}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number=ref, sales_order_number=so, customer_id="C1", customer_name="Owens Corning", purchase_orders={}, line_items=[]))
        db.commit()
    jobs.link_order(j["id"], so)
    fake_ocr["pages"] = [f"Owens Corning Insulating\nPurchase Order {ref}\nDate 09/01/26\n", SO_FILLED, "x"]
    pdf2 = _pdf(3) + b"\n%" + uuid.uuid4().hex.encode()
    second = client.post("/api/v1/packets", files={"file": ("b.pdf", pdf2, "application/pdf")}).json()
    assert second["job_id"] == j["id"]                                                   # the printed customer PO number matched exactly one job
    fake_ocr["pages"] = [f"Purchase Order {ref}\nDate 09/01/26\n", SO_FILLED, "y"]       # same numbers but no customer named: never attached on a number alone
    third = client.post("/api/v1/packets", files={"file": ("c.pdf", _pdf(3) + b"\n%" + uuid.uuid4().hex.encode(), "application/pdf")}).json()
    assert third["job_id"] is None
    att = client.post(f"/api/v1/packets/{first['packet']}/attach", json={"job": j["ref"]})
    assert att.status_code == 200 and len(att.json()["documents"]) >= 2
    other = rfqs.create({"customer": "X", "subject": "other", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j2 = jobs.from_rfq(other["id"], BOSS)
    assert client.post(f"/api/v1/packets/{first['packet']}/attach", json={"job": j2["ref"]}).status_code == 400   # not moved from another job


def test_the_internal_sales_order_number_in_a_document_is_for_staff_only(client, fake_ocr):
    r = rfqs.create({"customer": "Owens Corning", "subject": "staff test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    fake_ocr["pages"] = [SO_FILLED]
    client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("s.pdf", _pdf(1) + b"\n%" + uuid.uuid4().hex.encode(), "application/pdf")})
    assert packets.for_job(j["id"], staff=True)[0]["fields"]["sales_order_number"] == "12950"
    assert "sales_order_number" not in packets.for_job(j["id"], staff=False)[0]["fields"]


def test_bad_uploads_are_refused_in_plain_words(client, fake_ocr, monkeypatch):
    assert client.post("/api/v1/packets", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    from app.config import settings

    monkeypatch.setattr(settings, "SUBMISSION_MAX_MB", 0)
    big = client.post("/api/v1/packets", files={"file": ("x.pdf", _pdf(1), "application/pdf")})
    assert big.status_code == 400 and "larger than" in big.json()["detail"]
    assert client.get("/api/v1/packets/" + "0" * 64 + "/file").status_code == 404
    assert client.get("/api/v1/packets/not-a-hash/file").status_code == 404


def test_stages_follow_the_documents_and_a_blank_proves_nothing(client, fake_ocr):
    r = rfqs.create({"customer": "Owens Corning", "subject": "stage test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    assert [s["key"] for s in client.get(f"/api/v1/jobs/{j['id']}").json()["next_steps"]] == ["ask_vendor"]          # nothing yet: ask the vendor for a price
    fake_ocr["pages"] = [COVER, QUOTE, CUST, VQUOTE, PO, SO_BLANK, PACK]
    client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("p.pdf", _pdf(7) + b"\n%" + uuid.uuid4().hex.encode(), "application/pdf")})
    out = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert {"CUSTOMER_QUOTED", "CUSTOMER_ORDERED", "VENDOR_QUOTE", "PO_ISSUED"} <= set(out["done"])
    assert "SO_CREATED" not in out["done"] and "SHIPPED" not in out["done"]            # the sales order page has no number, and a packing list is not a shipment
    assert out["stage"] == "PO_ISSUED" and [s["key"] for s in out["next_steps"]] == ["make_sales_order"]
    so_no = f"9{uuid.uuid4().int % 10**5:05d}"                                              # unique: other tests create orders in the same database
    fake_ocr["pages"] = [f"ALLIED\nSALES ORDER\nSales Order Number: {so_no}\nSales Order Date: Sep 2, 2026\n"]
    client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("q.pdf", _pdf(1) + b"\n%" + uuid.uuid4().hex.encode(), "application/pdf")})
    assert "SO_CREATED" not in client.get(f"/api/v1/jobs/{j['id']}").json()["done"]      # the number is printed, but no such order exists: a misread proves nothing
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB

    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number="X-1", sales_order_number=so_no, customer_id="C1", customer_name="Owens Corning", purchase_orders={}, line_items=[]))
        db.commit()
    after = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert "SO_CREATED" in after["done"] and after["stage"] == "SO_CREATED" and [s["key"] for s in after["next_steps"]] == ["ship"]


def test_recording_a_vendor_quote_keeps_only_what_was_given_and_makes_the_po_draft(client):
    r = rfqs.create({"customer": "Owens Corning", "subject": "quote test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    out = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "HELEN", "quote_order_number": "0234999"})
    assert out.status_code == 200, out.text
    body = out.json()
    doc = [d for d in body["documents"]["items"] if d["kind"] == "vendor_quote"][0]
    assert doc["fields"] == {"vendor": "HELEN", "quote_order_number": "0234999"}        # no price was given, so none is stored
    assert "VENDOR_QUOTE" in body["done"] and body["purchase_order_draft"]
    po = client.get(f"/api/v1/purchase-orders/{body['purchase_order_draft']}").json()
    assert po["status"] == "DRAFT" and po["po_number"] is None
    assert client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "HELEN"}).status_code == 200      # again: still one PO
    assert len(client.get(f"/api/v1/jobs/{j['id']}/purchase-orders").json()) == 1
    assert client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={}).status_code == 400


def test_a_person_confirms_the_facts_and_only_confirmed_ones_are_staged_to_monday(client, fake_ocr, monkeypatch):
    from app.config import settings
    from app.models.db_models import VendorJobDB  # noqa: F401
    from app.services import monday_push
    from app.services.coordination import boards

    r = rfqs.create({"customer": "Owens Corning", "subject": "stage monday test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    ref = f"{uuid.uuid4().int % 10**8:08d}"
    fake_ocr["pages"] = [f"Purchase Order {ref}\nDate\n09/01/26\n", PO]
    body = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("m.pdf", _pdf(2) + b"\n%" + uuid.uuid4().hex.encode(), "application/pdf")}).json()
    board = str(uuid.uuid4().int % 10**9)
    cols = [{"id": "c_po", "title": "Customer PO#", "type": "text"}, {"id": "a_po", "title": "Allied PO#", "type": "text"}, {"id": "recv", "title": "PO received", "type": "date"},
            {"id": "issued", "title": "Vendor PO Issued", "type": "date"}, {"id": "vend", "title": "Vendor", "type": "text"}]
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": cols,
                                 "items": [{"id": "r1", "name": "HECLA", "values": {"c_po": ref, "a_po": "", "recv": "", "issued": "", "vend": "KEEP THIS"}}]}
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", board)
    unconfirmed = client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday")
    assert unconfirmed.status_code == 400 and "Confirm" in unconfirmed.json()["detail"]           # nothing travels on an unchecked OCR read
    for d in body["documents"]:
        assert client.post(f"/api/v1/packets/documents/{d['id']}/confirm", json={}).status_code == 200
    out = client.post(f"/api/v1/jobs/{j['id']}/packets/stage-monday").json()
    assert out["item_id"] == "r1" and out["staged"]["Allied PO#"] == "9301" and out["staged"]["PO received"] == "2026-09-01"
    assert out["staged"]["Vendor PO Issued"] == "2026-09-02" and "Customer PO#" not in out["staged"]      # already filled on the board: left alone
    assert out["differs"] == [] and out["change_id"]
    staged = [c for c in monday_push.changes("STAGED") if c["id"] == out["change_id"]][0]
    assert staged["payload"]["columns"]["a_po"] == "9301"                                    # staged only; never sent


def test_a_misread_value_can_be_corrected_and_an_emptied_one_is_blank_again(client, fake_ocr):
    r = rfqs.create({"customer": "Owens Corning", "subject": "correct test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    fake_ocr["pages"] = [PO]
    d = client.post(f"/api/v1/jobs/{j['id']}/packets", files={"file": ("n.pdf", _pdf(1) + b"\n%" + uuid.uuid4().hex.encode(), "application/pdf")}).json()["documents"][0]
    fixed = client.post(f"/api/v1/packets/documents/{d['id']}/confirm", json={"fields": {"po_number": "9302", "total": ""}}).json()
    assert fixed["fields"]["po_number"] == "9302" and "total" not in fixed["fields"] and fixed["confirmed"] is True
    assert client.post(f"/api/v1/packets/documents/{d['id']}/confirm", json={"fields": {"nonsense": "1"}}).status_code == 400
    assert client.post("/api/v1/packets/documents/999999999/confirm", json={}).status_code == 404


def test_an_oversized_upload_is_refused_as_it_arrives_and_dates_are_read_safely():
    import io

    with pytest.raises(jobs.JobError, match="larger than"):
        packets.read_upload(io.BytesIO(b"x" * (3 * 1024 * 1024)), limit_mb=1)
    assert packets.read_upload(io.BytesIO(b"abc"), limit_mb=1) == b"abc"
    assert packets.iso_date("8/14/26") == "2026-08-14" and packets.iso_date("Aug 14, 2026") == "2026-08-14" and packets.iso_date("2026-08-14") == "2026-08-14"
    assert packets.iso_date("13/45/26") is None and packets.iso_date("soon") is None and packets.iso_date("") is None


def test_a_file_must_be_marked_as_a_design_file_before_it_can_go_to_a_vendor(client):
    from app.services.coordination import files as item_files

    r = rfqs.create({"customer": "Owens Corning", "subject": "design flag test", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    f = item_files.add("rfq", r["number"], "d.pdf", b"%PDF-1.4 x", BOSS)
    assert f["design"] is False
    assert client.patch(f"/api/v1/files/{f['id']}", json={"design": True}).json()["design"] is True
    assert client.patch(f"/api/v1/files/{f['id']}", json={"design": False}).json()["design"] is False
    assert client.patch("/api/v1/files/999999999", json={"design": True}).status_code == 404

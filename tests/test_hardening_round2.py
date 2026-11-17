"""Round 2 hardening, from the senior review: classification, dates, names, vendor quotes, PO numbering and sending. Synthetic data only."""
import threading
import uuid

import pytest

from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services import order_documents as od
from app.services.assistant import tools
from app.services.coordination import jobs, packets, purchase_orders, rfqs
from tests.test_hardening import (ALLIED_PO, BOSS, CUST_PO_MENTIONING_ALLIED, OP, _approved_po, _pdf_bytes, _rfq, fake_ocr, mailer)  # noqa: F401


def test_two_pos_back_to_back_are_two_documents_and_a_continuation_page_is_not():
    po_b = ALLIED_PO.replace("9301", "9302")
    cont = "ALLIED\nWELDING SYSTEMS\nP.O. BOX 795459 Continued\nTOTAL 250.50\n"
    docs = packets.split([ALLIED_PO, cont, po_b])
    assert [(d["kind"], d["fields"].get("po_number")) for d in docs] == [("allied_po", "9301"), ("allied_po", "9302")]
    assert docs[0]["page_to"] == 2
    cust = packets.split([CUST_PO_MENTIONING_ALLIED, CUST_PO_MENTIONING_ALLIED.replace("26001556", "26001557")])
    assert [d["fields"]["customer_po_number"] for d in cust] == ["26001556", "26001557"]


def test_a_customer_po_that_prints_allieds_name_after_a_bill_to_is_never_allieds_po():
    assert packets.classify("Purchase Order No.: 55501\nDate Issued: 9/2/26\nBill To\nAllied Welding Systems\nP.O. Box 795459 Dallas\n") == "customer_po"
    assert packets.classify("Supplier: Allied Welding Systems\nPurchase Order No.: 55501\nDate Issued: 9/2/26\n") == "customer_po"
    assert packets.classify(ALLIED_PO) == "allied_po"


def test_the_word_quotation_in_a_body_is_not_a_vendor_quote_and_titles_decide_who_quoted():
    assert packets.classify("Purchase Order 12345678\nPlease confirm per your quotation Q-9 dated Aug 1\n") == "customer_po"
    assert packets.classify("QUOTATION\nSome Vendor LLC\nOrder Number: 0234999\nQuote Number: 77\n") == "vendor_quote"
    assert packets.classify("ALLIED\nQUOTATION\nQuote Number: 09022026-1\nQuote Date: Sep 2, 2026\n") == "allied_quote"
    assert packets.classify("Gmail  Someone <a@b.test>\nQuote for the job\n3 messages\nper your quotation") == "email_printout"


def test_a_zip_code_is_not_read_as_a_po_number():
    assert "po_number" not in packets.extract("allied_po", "ALLIED\nWELDING SYSTEMS\nShip to TX\n76645\n09/01/26\nTOTAL 5.00\n")


@pytest.mark.parametrize("text,expected", [("2026-13-45", None), ("2/31/26", None), ("2026-02-30", None), ("2025-02-29", None), ("2024-02-29", "2024-02-29"),
                                           ("13/45/26", None), ("12/31/26", "2026-12-31"), ("Feb 30, 2026", None), ("Aug 14, 2026", "2026-08-14"),
                                           ("1/1/1800", None), ("", None), ("2026/09/01", None)])
def test_only_real_calendar_dates_are_dates(text, expected):
    assert packets.iso_date(text) == expected


def test_a_packet_attaches_by_the_customers_name_as_a_phrase_not_one_word(fake_ocr):  # noqa: F811
    number = f"{uuid.uuid4().int % 10**8:08d}"
    r = rfqs.create({"customer": "American Steel", "subject": "x", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    so = f"9{uuid.uuid4().int % 10**5:05d}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number=number, sales_order_number=so, customer_id="C", customer_name="American Steel Fabricators LLC",
                       purchase_orders={}, line_items=[]))
        db.commit()
    jobs.link_order(j["id"], so)
    fake_ocr["pages"] = [f"American Eagle Outfitters\nPurchase Order {number}\nDate 09/01/26\n"]
    assert packets.ingest(_pdf_bytes(1), "a.pdf", OP)["job_id"] is None
    fake_ocr["pages"] = [f"American Steel Fabricators\nPurchase Order {number}\nDate 09/01/26\n"]
    assert packets.ingest(_pdf_bytes(1), "b.pdf", OP)["job_id"] == j["id"]


@pytest.mark.parametrize("text", ["1_000", "1e3", "١٢٣", "1 000", "+-5", "5.", ".5", "0x10", "1,00,000"])
def test_numbers_are_plain_ascii_digits_only(text):
    assert od.num(text) is None


def test_a_missing_unicode_font_still_makes_a_pdf(monkeypatch):
    from app.services import exports

    monkeypatch.setattr(exports, "_pdf_fonts", lambda: (_ for _ in ()).throw(RuntimeError("no fonts here")))
    pdf = od.vendor_po_pdf({"po_number": "9301", "vendor_name": "王伟", "lines": od.po_lines([{"quantity": 1, "description": "Nguyễn"}])})
    assert pdf.startswith(b"%PDF")


def test_one_vendor_rule_the_vendor_lists_own_names_settle_it_and_lookalikes_stay_apart():
    assert jobs._same_vendor("ZIBO", "Zibo Supereal Industrial Ceramic Co. Ltd") and jobs._same_vendor("zibo", "ZIBO ")
    assert not jobs._same_vendor("Shanghai Steel", "Shanghai") and not jobs._same_vendor("Shanghai Steel", "Shanghai Metal") and not jobs._same_vendor("AM", "AM Metals")
    assert jobs.same_vendor_name("KWS LTD", "kws ltd.") and not jobs.same_vendor_name("", "")


def test_zibo_written_two_ways_is_one_quote_and_one_po(client):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "ZIBO", "total": "1000"})
    client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Zibo Supereal Industrial Ceramic Co. Ltd", "total": "1000"})
    docs = client.get(f"/api/v1/jobs/{j['id']}/documents").json()["items"]
    assert len([d for d in docs if d["kind"] == "vendor_quote"]) == 1 and len(client.get(f"/api/v1/jobs/{j['id']}/purchase-orders").json()) == 1


def test_a_revised_price_alerts_the_managers_again_and_the_same_price_does_not(client):
    from app.models.db_models import NotificationDB

    j = jobs.from_rfq(_rfq()["id"], BOSS)
    for total in ("1000", "1000", "900"):
        client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Revise Co", "total": total})
    with SessionLocal() as db:
        keys = [n.dedup_key for n in db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"vendor-quote:{j['id']}:%")).all()]
    assert len(keys) == 2


@pytest.mark.parametrize("email", ["a@x.com, b@y.com", "a b@x.com", "no-at-sign", "a@x.com;b@y.com", "<a@x.com>", "a@@x.com", "a@x"])
def test_the_vendor_address_must_be_one_address(email):
    r, j, po = _approved_po()
    with pytest.raises(jobs.JobError, match="one email address"):
        purchase_orders.update(po["id"], {"vendor_email": email}, BOSS)
    assert purchase_orders.update(po["id"], {"vendor_email": "good@example.test"}, BOSS)["vendor_email"] == "good@example.test"


def test_a_number_the_database_refuses_is_retried_and_never_leaves_a_draft_that_looks_approved(monkeypatch):
    r1, j1, po1 = _approved_po("ONE VENDOR")
    j2 = jobs.from_rfq(_rfq()["id"], BOSS)
    jobs.approve_design(j2["id"], BOSS)
    draft = purchase_orders.create(j2["id"], "TWO VENDOR", BOSS, lines=[{"quantity": 1, "description": "x"}])
    answers = iter([po1["po_number"], "98765432"])
    monkeypatch.setattr(purchase_orders, "_next_number", lambda: next(answers))
    ok = purchase_orders.approve(draft["id"], BOSS)
    assert ok["status"] == "APPROVED" and ok["po_number"] == "98765432"
    j3 = jobs.from_rfq(_rfq()["id"], BOSS)
    jobs.approve_design(j3["id"], BOSS)
    stuck = purchase_orders.create(j3["id"], "THREE VENDOR", BOSS, lines=[{"quantity": 1, "description": "x"}])
    monkeypatch.setattr(purchase_orders, "_next_number", lambda: po1["po_number"])
    with pytest.raises(jobs.JobError, match="could not be given a number"):
        purchase_orders.approve(stuck["id"], BOSS)
    assert purchase_orders.get(stuck["id"])["status"] == "DRAFT"


def test_two_sends_at_the_same_moment_send_the_po_once(mailer):  # noqa: F811
    r, j, po = _approved_po()
    args = {"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "po_number": po["po_number"], "po_id": po["id"], "body": "b"}
    results = []

    def go():
        try:
            results.append(("ok", tools.send_vendor_po(dict(args), BOSS)))
        except ValueError as exc:
            results.append(("refused", str(exc)))

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [x[0] for x in results].count("ok") == 1 and len([m for m in mailer if m[0] == "po"]) == 1
    assert all(("already been sent" in x[1] or "being sent" in x[1]) for x in results if x[0] == "refused")


def test_two_vendor_quotes_recorded_at_the_same_moment_are_one(client):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    ts = [threading.Thread(target=lambda: jobs.record_vendor_quote(j["id"], "Race Vendor", {"total": "10"}, OP)) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    docs = [d for d in client.get(f"/api/v1/jobs/{j['id']}/documents").json()["items"] if d["kind"] == "vendor_quote"]
    assert len(docs) == 1 and len(client.get(f"/api/v1/jobs/{j['id']}/purchase-orders").json()) == 1

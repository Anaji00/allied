"""Less typing: extract-to-form (rules first, nothing saved), prefill, duplicate template, create from text, bulk edit with a dry run, remembered choices, and the keep/change statistics. Synthetic text and
PDFs; temporary database and data folder."""
import io
import uuid

import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB, PurchaseOrderDB, RfqDB
from app.services import assist, extract
from tests.conftest import staff_token


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def H(email, role="OPERATOR"):
    return {"Authorization": "Bearer " + staff_token(email, role)}


def _customer():
    rows = list(extract._customers().values())
    return rows[0] if rows else {"customer_name": "Nibrock", "customer_id": "X1", "email": "buyer@nibrock.example"}


def test_an_rfq_email_gives_fields_with_how_sure_and_where_from_and_saves_nothing(client):
    c = _customer()
    before = _count(RfqDB)
    text = f"Hi,\nPlease quote the following for {c['customer_name']}. RFQ no. RQ-88123, needed by 2026-12-04.\n10 x AR400 liner plate\n4 pcs CHUTE-LINER-12\nThanks"
    r = client.post("/api/v1/extract/rfq-from-email", json={"text": text, "sender": "buyer@somewhere.test", "subject": "Quote request"}, headers=H("ex.o@allied.test")).json()
    f = r["fields"]
    assert f["customer"]["value"] == c["customer_name"] and f["customer"]["method"] == "master data" and f["customer"]["confidence"] == 0.4 and "customer" in r["needs_check"]       # a name only mentioned in the text is a weak match
    assert f["customer_rfq_number"]["value"] == "RQ-88123" and f["customer_rfq_number"]["confidence"] == 0.9 and f["customer_rfq_number"]["source"]["text"]
    assert f["due_date"]["value"] == "2026-12-04" and [i["quantity"] for i in f["items"]["value"]] == ["10", "4"]
    assert {"customer_rfq_number", "due_date"} <= set(r["needs_check"]) and r["ai_used"] is False and r["extraction_id"]
    assert _count(RfqDB) == before


def test_a_blank_stays_blank_and_a_bad_request_is_a_plain_sentence(client):
    r = client.post("/api/v1/extract/rfq-from-email", json={"text": "Hello, are you open on Friday?", "sender": "x@y.example", "subject": "Hi"}, headers=H("ex.o@allied.test")).json()
    assert "customer_rfq_number" not in r["fields"] and "items" not in r["fields"] and any("customer could not be matched" in w for w in r["warnings"])
    bad = client.post("/api/v1/extract/rfq-from-email", json={}, headers=H("ex.o@allied.test"))
    assert bad.status_code == 422 and "email" in bad.json()["detail"].lower()
    assert client.post("/api/v1/extract/rfq-from-email", json={"mail_id": 99999999}, headers=H("ex.o@allied.test")).status_code == 404
    assert client.post("/api/v1/extract/rfq-from-email", json={"text": "x"}, headers=H("ex.v@allied.test", "VIEWER")).status_code == 403


def test_an_order_email_finds_the_po_terms_total_and_lines(client):
    text = "Purchase order PO 4500123456 attached.\nNet 45 terms. Deliver by 12/18/2026.\n5 x HARDFACE PLATE 12MM\nTotal: $12,480.50"
    r = client.post("/api/v1/extract/order-from-email", json={"text": text, "sender": "ap@buyer.test", "subject": "New order"}, headers=H("ex.o@allied.test")).json()
    f = r["fields"]
    assert f["customer_po_number"]["value"] == "4500123456" and f["payment_terms"]["value"] == "Net 45 Days" and f["total"]["value"] == "12480.50" and f["due_date"]["value"] == "12/18/2026"
    assert {"customer_po_number", "total", "payment_terms", "due_date"} <= set(r["needs_check"])


def test_a_po_pdf_is_read_with_page_and_line_and_an_empty_or_broken_file_is_a_plain_error(client):
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(["PURCHASE ORDER", "Purchase Order Number: 4500987654", "Date: 11/18/2026", "Bill To: Allied Welding Systems", "Description  Qty  Price", "Liner plate  2  600.00", "TOTAL 1,200.00"]):
        page.insert_text((50, 60 + i * 18), line, fontsize=11)
    data = doc.tobytes()
    ok = client.post("/api/v1/extract/po-from-pdf", files={"file": ("po.pdf", data, "application/pdf")}, headers=H("ex.o@allied.test"))
    assert ok.status_code == 200
    f = ok.json()["fields"]
    assert f["customer_po_number"]["value"] == "4500987654" and f["customer_po_number"]["source"]["page"] == 1 and "4500987654" in f["customer_po_number"]["source"]["text"]
    assert "customer_po_number" in ok.json()["needs_check"]
    assert client.post("/api/v1/extract/po-from-pdf", files={"file": ("e.pdf", b"", "application/pdf")}, headers=H("ex.o@allied.test")).status_code == 422
    broken = client.post("/api/v1/extract/po-from-pdf", files={"file": ("b.pdf", b"not a pdf", "application/pdf")}, headers=H("ex.o@allied.test"))
    assert broken.status_code == 422 and "damaged" in broken.json()["detail"].lower()


def test_a_vendor_quote_gives_number_total_validity_lead_time_terms_and_weights(client):
    text = ("QUOTATION\nOrder Number: 778812\nOrder Date: 08/24/2026\nLead time: 9-11 weeks\nValid until 09/30/2026\nPayment terms: Net 30\nGross weight: 1,250 kg\nDimensions: 120 x 60 x 45 cm\nTOTAL USD 48,000.00\n")
    r = client.post("/api/v1/extract/vendor-quote", data={"text": text}, headers=H("ex.o@allied.test")).json()
    f = r["fields"]
    assert f["lead_time"]["value"] == "9-11 weeks" and f["weights"]["value"][0]["value"] == "1250" and "lead_time" in r["needs_check"]
    assert client.post("/api/v1/extract/vendor-quote", data={"text": ""}, headers=H("ex.o@allied.test")).status_code == 422


def test_a_call_note_has_the_po_numbers_dates_and_actions_and_ai_is_only_used_when_asked(client):
    text = "Spoke with Li at the foundry. PO 4500123456 will ship on 2026-12-10. We need the photos by 2026-12-05. They will send the packing list tomorrow."
    r = client.post("/api/v1/extract/call-to-job-note", json={"text": text}, headers=H("ex.o@allied.test")).json()
    f = r["fields"]
    assert f["po_numbers"]["value"] == ["4500123456"] and "2026-12-10" in f["dates"]["value"] and f["action_items"]["value"] and r["ai_used"] is False
    assert client.post("/api/v1/extract/call-to-job-note", json={"text": "hi"}, headers=H("ex.o@allied.test")).status_code == 422


def test_feedback_is_counted_per_field_and_the_stats_are_manager_only(client):
    text = "Purchase order PO 4500111222 attached.\nNet 30 terms.\n2 x PLATE"
    r = client.post("/api/v1/extract/order-from-email", json={"text": text, "sender": "a@b.test", "subject": "order"}, headers=H("ex.o@allied.test")).json()
    final = {"customer_po_number": "4500111999", "payment_terms": r["fields"]["payment_terms"]["value"], "total": "100.00"}
    fb = client.post("/api/v1/extract/feedback", json={"extraction_id": r["extraction_id"], "fields": final}, headers=H("ex.o@allied.test")).json()
    assert fb["counted"]["customer_po_number"]["outcome"] == "changed" and fb["counted"]["payment_terms"]["outcome"] == "kept" and fb["counted"]["total"]["outcome"] == "added"
    assert client.post("/api/v1/extract/feedback", json={"extraction_id": "nope", "fields": {}}, headers=H("ex.o@allied.test")).status_code == 404
    assert client.get("/api/v1/ai/extraction-stats", headers=H("ex.o@allied.test")).status_code == 403
    st = client.get("/api/v1/ai/extraction-stats", headers=H("ex.m@allied.test", "MANAGER")).json()
    row = next(x for x in st["fields"] if x["field"] == "order.customer_po_number")
    assert row["changed"] >= 1 and st["forms_reviewed"] >= 1


def test_prefill_uses_the_last_order_and_last_po_and_says_where_each_came_from(client):
    tag = uuid.uuid4().hex[:5]
    cust, vend = f"PrefillCo {tag}", f"PrefillVendor {tag}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{tag}", customer_po_number=f"CP{tag}", sales_order_number=f"S{tag}", customer_id=f"C{tag}", customer_name=cust, currency="CAD", shipping_address="1 Dock Rd, Toledo", payment_terms="Net 45 Days", created_by="olive@allied.test"))
        db.add(PurchaseOrderDB(job_id=1, po_number=f"P{tag}", vendor=vend, vendor_email=f"sales@{tag}.test", lines=[], terms="30/70", ship_via="SEA", incoterm="FOB", incoterm_place="Ningbo", status="SENT"))
        db.commit()
    o = client.get("/api/v1/prefill/order", params={"customer": cust}, headers=H("ex.o@allied.test")).json()["values"]
    assert o["shipping_address"]["value"] == "1 Dock Rd, Toledo" and o["shipping_address"]["source"] == f"last order CP{tag}" and o["currency"]["value"] == "CAD" and o["payment_terms"]["value"] == "Net 45 Days"
    p = client.get("/api/v1/prefill/po", params={"vendor": vend}, headers=H("ex.o@allied.test")).json()["values"]
    assert p["vendor_email"]["value"] == f"sales@{tag}.test" and p["incoterm"]["value"] == "FOB" and p["payment_terms"]["value"] == "30/70" and p["incoterm_place"]["source"].startswith("last PO")
    assert client.get("/api/v1/prefill/order", params={"customer": "Nobody Ever"}, headers=H("ex.o@allied.test")).json()["values"] == {}
    assert client.get("/api/v1/prefill/banana", headers=H("ex.o@allied.test")).status_code == 422


def test_a_duplicate_template_drops_numbers_dates_and_status(client):
    tag = uuid.uuid4().hex[:5]
    with SessionLocal() as db:
        db.add(PurchaseOrderDB(job_id=7, po_number=f"DUP{tag}", vendor=f"DupVendor {tag}", vendor_email="a@dup.test", lines=[{"qty": 1, "item": "x", "unit_cost": "5", "amount": "5"}], terms="50/50", incoterm="EXW", status="SENT"))
        db.commit()
    r = client.get(f"/api/v1/records/po/DUP{tag}/duplicate-template", headers=H("ex.o@allied.test")).json()
    body = r["body"]
    assert body["vendor"] == f"DupVendor {tag}" and body["terms"] == "50/50" and body["incoterm"] == "EXW" and body["lines"]
    assert not {"id", "po_number", "status", "created_at", "sent_at"} & set(body)
    assert client.get("/api/v1/records/po/NOPE/duplicate-template", headers=H("ex.o@allied.test")).status_code == 404


def test_create_from_text_detects_the_kind_and_proposes_a_body_without_saving(client):
    before = _count(RfqDB)
    r = client.post("/api/v1/create/from-text", json={"text": "Please quote 10 x liner plate. RFQ 5521-A. Needed by 2026-12-20.", "sender": "x@y.test"}, headers=H("ex.o@allied.test")).json()
    assert r["kind"] == "rfq" and r["create_url"] == "/api/v1/create/rfq" and r["proposed_body"]["items"] and "Nothing has been saved" in r["message"] and _count(RfqDB) == before
    s = client.post("/api/v1/create/from-text", json={"text": "Container MSKU1234567 on booking BK778899, ETA 2026-12-11, carrier Zibo Freight"}, headers=H("ex.o@allied.test")).json()
    assert s["kind"] == "shipment" and s["proposed_body"]["container_no"] == "MSKU1234567" and s["proposed_body"]["reference"] == "BK778899"
    u = client.post("/api/v1/create/from-text", json={"text": "Good morning, hope you had a nice weekend."}, headers=H("ex.o@allied.test")).json()
    assert u["kind"] is None and u["proposed_body"] is None and u["options"]
    assert client.post("/api/v1/create/from-text", json={"text": "short"}, headers=H("ex.o@allied.test")).status_code == 422


def test_bulk_is_a_dry_run_by_default_limited_to_owner_tags_ball_and_owner_needs_a_manager_to_apply(client):
    from app.models.db_models import JobDB, StaffContactDB
    from app.services.coordination import notify

    tag = uuid.uuid4().hex[:4]
    refs = [f"JOB-BULK-{tag}-{i}" for i in range(3)]
    with SessionLocal() as db:
        for r in refs:
            db.add(JobDB(ref=r, title="t", customer="C"))
        db.merge(StaffContactDB(email=f"owner.{tag}@allied.test", name="Own Er", role_tag="staff", phone=None, channels=["email"], daily_summary=False, active=True))
        db.commit()
    notify._contacts_cache.update(at=0.0, value=None)
    body = {"kind": "job", "refs": refs, "changes": {"add_tags": ["urgent"]}}
    dry = client.post("/api/v1/records/bulk", json=body, headers=H("ex.o@allied.test")).json()
    assert dry["dry_run"] is True and dry["changed"] == 0 and all(r["status"] == "would change" and "add tag urgent" in r["changes"] for r in dry["rows"])
    done = client.post("/api/v1/records/bulk", json={**body, "dry_run": False}, headers=H("ex.o@allied.test")).json()
    assert done["changed"] == 3
    again = client.post("/api/v1/records/bulk", json=body, headers=H("ex.o@allied.test")).json()
    assert "tag urgent already there" in again["rows"][0]["changes"]
    ghost = client.post("/api/v1/records/bulk", json={"kind": "job", "refs": ["JOB-NOPE-1"], "changes": {"add_tags": ["x"]}, "dry_run": False}, headers=H("ex.o@allied.test")).json()
    assert ghost["changed"] == 0 and ghost["rows"][0]["status"].startswith("not found")
    for bad in ({"add_tags": "urgent"}, {"add_tags": [1, 2]}, {"add_tags": [f"t{i}" for i in range(11)]}, {"add_tags": ["x" * 41]}, {"status": "DONE"}, {"owner": "<script>"}, {"owner": "nobody@allied.test"}):
        assert client.post("/api/v1/records/bulk", json={"kind": "job", "refs": refs, "changes": bad}, headers=H("ex.o@allied.test")).status_code == 422, bad
    assert client.post("/api/v1/records/bulk", json={"kind": "quote", "refs": refs, "changes": {"add_tags": ["x"]}}, headers=H("ex.o@allied.test")).status_code == 422
    owner = f"owner.{tag}@allied.test"
    assert client.post("/api/v1/records/bulk", json={"kind": "job", "refs": refs, "changes": {"owner": owner}, "dry_run": False}, headers=H("ex.o@allied.test")).status_code == 403
    ok = client.post("/api/v1/records/bulk", json={"kind": "job", "refs": refs, "changes": {"owner": owner}, "dry_run": False}, headers=H("ex.m@allied.test", "MANAGER"))
    assert ok.status_code == 200 and ok.json()["changed"] == 3
    assert client.post("/api/v1/records/bulk", json={"kind": "job", "refs": [f"r{i}" for i in range(101)], "changes": {"add_tags": ["x"]}}, headers=H("ex.o@allied.test")).status_code == 422
    assert client.post("/api/v1/records/bulk", json={"kind": "job", "refs": [], "changes": {"add_tags": ["x"]}}, headers=H("ex.o@allied.test")).status_code == 422
    assert client.post("/api/v1/records/bulk", json={"kind": "job", "refs": refs, "changes": {"add_tags": ["x"]}}, headers=H("ex.v@allied.test", "VIEWER")).status_code == 403


def test_my_defaults_are_per_person_limited_to_known_keys_and_blank_forgets(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_DIR", tmp_path)
    a, b = H("def.a@allied.test", "VIEWER"), H("def.b@allied.test", "VIEWER")
    assert client.get("/api/v1/me/defaults", headers=a).json() == {}
    assert client.put("/api/v1/me/defaults", json={"last_customer": "Nibrock", "currency": "CAD"}, headers=a).json() == {"last_customer": "Nibrock", "currency": "CAD"}
    assert client.get("/api/v1/me/defaults", headers=b).json() == {}
    assert client.put("/api/v1/me/defaults", json={"currency": ""}, headers=a).json() == {"last_customer": "Nibrock"}
    assert client.put("/api/v1/me/defaults", json={"password": "x"}, headers=a).status_code == 422
    assert client.put("/api/v1/me/defaults", json={"owner": "x" * 200}, headers=a).status_code == 422


def _count(model):
    with SessionLocal() as db:
        return db.query(model).count()


def test_the_p_o_pattern_and_the_address_scan_cannot_be_made_to_crawl_and_a_long_text_is_cut():
    import time

    t0 = time.time()
    assist._detect("p.o." + " " * 60000 + "!")
    extract._emails("a" * 60000)
    extract.order_from_email(text="PO " + " " * 30000 + "x", sender="a@b.test", subject="s")
    assert time.time() - t0 < 3


def test_quantities_and_prices_inside_items_and_weights_are_always_checked_and_a_date_with_two_readings_is_not_filled_in():
    r = extract.order_from_email(text="PO 4500123456\n10 x AR400 plate @ $55.00\nDeliver by 03/04/2026\nTotal weight 1250.00 kg", sender="a@b.test", subject="s")
    assert "items" in r["needs_check"] and "due_date" not in r["fields"] and any("two ways" in w for w in r["warnings"]) and "total" not in r["fields"]
    q = extract.vendor_quote(text="QUOTATION\nGross weight: 100 kg\nDimensions: 1 x 1 x 1 m\nTOTAL USD 10.00")
    assert {"weights", "dimensions"} <= set(q["needs_check"])
    d = extract.rfq_from_email(text="Signed by Jan 5, 2026. Quote needed by 2026-12-04.", sender="a@b.test", subject="s")
    assert d["fields"]["due_date"]["value"] == "2026-12-04"


def test_feedback_counts_once_and_only_for_the_person_who_was_shown_the_extraction(client):
    r = client.post("/api/v1/extract/order-from-email", json={"text": "PO 4500777888 net 30", "sender": "a@b.test", "subject": "o"}, headers=H("fb.one@allied.test")).json()
    body = {"extraction_id": r["extraction_id"], "fields": {"customer_po_number": "4500777888"}}
    assert client.post("/api/v1/extract/feedback", json=body, headers=H("fb.two@allied.test")).status_code == 403
    assert client.post("/api/v1/extract/feedback", json=body, headers=H("fb.one@allied.test")).status_code == 200
    again = client.post("/api/v1/extract/feedback", json=body, headers=H("fb.one@allied.test"))
    assert again.status_code == 409 and "once" in again.json()["detail"]


def test_defaults_survive_simultaneous_saves_and_a_damaged_file_is_not_wiped(client, tmp_path, monkeypatch):
    import json
    import threading

    monkeypatch.setattr(settings, "DB_DIR", tmp_path)
    assist._last_good_defaults.clear()
    people = [f"par{i}@allied.test" for i in range(12)]
    ts = [threading.Thread(target=lambda e=e: assist.put_defaults({"email": e, "role": "VIEWER"}, {"currency": "CAD"})) for e in people]
    [t.start() for t in ts]
    [t.join() for t in ts]
    saved = json.loads((tmp_path / "user_defaults.json").read_text())
    assert all(e in saved for e in people)
    (tmp_path / "user_defaults.json").write_text('{"par1@allied.test": {"currency": "CA', encoding="utf-8")
    assist.put_defaults({"email": "late@allied.test", "role": "VIEWER"}, {"currency": "USD"})
    after = json.loads((tmp_path / "user_defaults.json").read_text())
    assert len(after) >= 12 and (tmp_path / "user_defaults.json.bak").exists()
    assist._last_good_defaults.clear()
    (tmp_path / "user_defaults.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(assist.AssistError) as e:
        assist.put_defaults({"email": "x@allied.test", "role": "VIEWER"}, {"currency": "USD"})
    assert e.value.status == 409


def test_a_text_over_the_cap_is_cut_with_a_warning_and_the_last_5000_characters_are_still_read():
    long_text = "Hello.\n" + ("quoted thread line\n" * 3000) + "Customer PO 4500999111\nTotal $1,234.50\nThanks"
    r = extract.order_from_email(text=long_text, sender="a@b.test", subject="s")
    assert any("last 5,000 characters" in w for w in r["warnings"])
    assert r["fields"]["customer_po_number"]["value"] == "4500999111" and r["fields"]["total"]["value"] == "1234.50"
    short = extract.order_from_email(text="Customer PO 4500999111", sender="a@b.test", subject="s")
    assert not any("last 5,000" in w for w in short["warnings"])
    assert any("last 5,000" in w for w in extract.call_to_job_note("word " * 30000 + "PO 4500123456 ships 2026-12-10.")["warnings"])
    assert any("last 5,000" in w for w in extract.vendor_quote(text="QUOTATION\n" + "x " * 40000 + "\nTOTAL USD 10.00")["warnings"])

"""A job's purchase orders, design files, research and sales order, end to end with fakes. Test data only; nothing external is contacted."""
import io
import uuid

import pytest
from pypdf import PdfReader

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services import lead_agent
from app.services.assistant import agent
from app.services.coordination import files as item_files
from app.services.coordination import jobs, purchase_orders, rfqs
from app.services.sage_sdk_writer import SageSDKWriter
from tests.test_e2e_allied_use_cases import REVIEWER, TEST_INBOX, _drafts, day  # noqa: F401

BOSS = {"email": REVIEWER, "role": "MANAGER"}
OP = {"email": "op-docs@alliedweldingsystems.com", "role": "OPERATOR"}


def _job():
    r = rfqs.create({"customer": "Owens Corning", "subject": f"hey make me this part {uuid.uuid4().hex[:5]}",
                     "items": [{"description": "suction hose liner", "quantity": 2}]}, BOSS, announce=False)
    return r, jobs.from_rfq(r["id"], BOSS)


def _text(pdf: bytes) -> str:
    return " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def test_numbers_start_above_the_floor_the_accountant_sets(monkeypatch):
    monkeypatch.setattr(settings, "PO_NUMBER_FLOOR", 987650)
    monkeypatch.setattr(settings, "SO_NUMBER_FLOOR", 876540)
    SageSDKWriter.reset_sequences()
    try:
        assert int(SageSDKWriter._next_number("PO")) >= 987651 and int(SageSDKWriter._next_number("SO")) >= 876541
    finally:
        monkeypatch.setattr(settings, "PO_NUMBER_FLOOR", 0)
        monkeypatch.setattr(settings, "SO_NUMBER_FLOOR", 0)
        SageSDKWriter.reset_sequences()


def test_a_po_is_one_per_vendor_waits_for_the_design_then_goes_as_a_draft_with_pdf_and_drawings(day, client):  # noqa: F811
    r, j = _job()
    file = item_files.add("rfq", str(r["id"]), "liner-drawing.pdf", b"%PDF-1.4 fake drawing", OP)
    item_files.set_design(file["id"], True, OP)
    po = client.post(f"/api/v1/jobs/{j['id']}/purchase-orders", json={"vendor": "HELEN", "terms": "Net 45 Days"}).json()
    again = client.post(f"/api/v1/jobs/{j['id']}/purchase-orders", json={"vendor": "helen"}).json()
    assert po["status"] == "DRAFT" and po["po_number"] is None and again["id"] == po["id"]          # one PO per vendor per job
    assert po["lines"][0]["description"] == "suction hose liner" and po["vendor_email"] == "helen@zbsupereal.com"

    refused = client.post(f"/api/v1/purchase-orders/{po['id']}/approve")                            # design not approved: a plain 400
    assert refused.status_code == 400 and "not approved" in refused.json()["detail"]
    jobs.approve_design(j["id"], BOSS)
    ok = client.post(f"/api/v1/purchase-orders/{po['id']}/approve").json()
    assert ok["status"] == "APPROVED" and ok["po_number"]
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/approve").json()["po_number"] == ok["po_number"]   # approving twice keeps the number

    pdf = client.get(f"/api/v1/purchase-orders/{po['id']}/pdf")
    assert pdf.status_code == 200 and ok["po_number"] in _text(pdf.content) and "Net 45 Days" in _text(pdf.content)
    assert "Owens" not in _text(pdf.content)

    assert not day["sent"] or all(ok["po_number"] not in s for s, _, _ in day["sent"])             # nothing is sent by making the draft
    queued = client.post(f"/api/v1/purchase-orders/{po['id']}/email-draft").json()
    assert queued["action_id"]
    draft = next(d for d in _drafts("vendor_po") if d.args.get("po_id") == po["id"])
    assert draft.args["attach_file_ids"] == [file["id"]] and draft.tool == "send_vendor_po"
    assert "approved drawings" in draft.args["body"] and [a["name"] for a in (draft.args["_attachments"])] == [f"PO_{ok['po_number']}.pdf", "liner-drawing.pdf"]
    assert any(d["action_id"] == queued["action_id"] if "action_id" in d else d["id"] == queued["action_id"] for d in client.get(f"/api/v1/jobs/{j['id']}").json()["drafts"])
    assert f"PO_{ok['po_number']}.pdf" in pdf.headers["content-disposition"] and po["lines"][0]["quantity"] == 2
    assert agent.decide(draft.id, True, BOSS)["status"] == "DONE"
    assert day["attached"][-1] == [f"PO_{ok['po_number']}.pdf", "liner-drawing.pdf"] and day["sent"][-1][1] == [TEST_INBOX]
    assert client.get(f"/api/v1/purchase-orders/{po['id']}").json()["status"] == "APPROVED"   # (pilot mail goes to the test inbox, so the PO is not marked sent)
    purchase_orders.begin_send(po["id"]); purchase_orders.finish_send(po["id"], True)      # as if it really went to the vendor
    assert client.get(f"/api/v1/purchase-orders/{po['id']}").json()["status"] == "SENT"
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"terms": "x"}).status_code == 400   # sent: no more changes
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/acknowledge").json()["status"] == "ACKNOWLEDGED"
    job = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert "PO_ISSUED" in job["done"] and job["design"]["files"][0]["name"] == "liner-drawing.pdf"


def test_the_sales_order_keeps_the_vendors_existing_po_and_has_an_internal_sheet(client):
    r, j = _job()
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], "HELEN", BOSS)
    po = purchase_orders.approve(po["id"], BOSS)
    ref = f"OC-{uuid.uuid4().hex[:6].upper()}"
    out = client.post(f"/api/v1/jobs/{j['id']}/sales-order", json={
        "customer_reference": ref, "shipping_address": "831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        "lines": [{"description": "OC262064", "quantity": 2, "unit_price": 2480}]})
    assert out.status_code == 200, out.text
    so = out.json()["sales_order"]
    assert so["customer_reference"] == ref and "SO_CREATED" in out.json()["done"]
    assert jobs.po_for_vendor(so["number"], "Helen") == po["po_number"]                               # reused, not renumbered
    with SessionLocal() as db:
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == so["number"]).one()
        vendors_pos = list((order.purchase_orders or {}).values())
    assert all(v == po["po_number"] for v in vendors_pos if v == po["po_number"])                   # (the order never got a second number for her)
    again = client.post(f"/api/v1/jobs/{j['id']}/sales-order", json={"customer_reference": ref, "lines": [{"description": "x"}]})
    assert again.status_code == 400
    sheet = client.get(f"/api/v1/jobs/{j['id']}/sales-order/sheet.pdf")
    t = _text(sheet.content)
    assert sheet.status_code == 200 and "SALES ORDER INFORMATION" in t and ref in t and so["number"] in t and "internal" in t


def test_research_notes_and_the_web_scan_land_on_the_job(client, monkeypatch):
    r, j = _job()
    assert client.post(f"/api/v1/jobs/{j['id']}/research", json={"title": "Who else makes liners", "url": "https://example.test/a", "note": "two US makers"}).status_code == 200
    monkeypatch.setattr(lead_agent, "HAS_DDGS", True)
    monkeypatch.setattr(lead_agent, "DDGS", lambda: type("D", (), {"text": lambda self, q, max_results=5: [{"title": "Maker A", "href": "https://a.test", "body": "liners"}]})())
    out = client.post(f"/api/v1/jobs/{j['id']}/research/search", json={"query": "suction hose liner makers"}).json()
    assert [n["source"] for n in out["research"]] == ["manual", "search"] and "RESEARCH" in out["done"]
    monkeypatch.setattr(lead_agent, "HAS_DDGS", False)
    assert client.post(f"/api/v1/jobs/{j['id']}/research/search", json={"query": "anything at all"}).status_code == 400
    assert client.post(f"/api/v1/jobs/{j['id']}/research", json={}).status_code == 400
    monkeypatch.setattr(lead_agent, "HAS_DDGS", True)
    monkeypatch.setattr(lead_agent, "DDGS", lambda: type("D", (), {"text": lambda self, q, max_results=5: []})())
    empty = client.post(f"/api/v1/jobs/{j['id']}/research/search", json={"query": "nothing matches this"})
    assert empty.status_code == 400 and "found nothing" in empty.json()["detail"]


def test_an_unknown_part_is_explained_with_the_closest_names_and_a_pick_list(client):
    r, j = _job()
    out = client.post(f"/api/v1/jobs/{j['id']}/sales-order", json={"customer_reference": "X-1", "lines": [{"description": "zzzz qqqq unknown thing", "unit_price": "5", "quantity": 1}]})
    assert out.status_code == 400 and "Sage" not in out.json()["detail"] and "catalog" in out.json()["detail"]
    pick = client.get("/api/v1/catalog/items?q=screen").json()
    assert pick and {"item_id", "description"} <= set(pick[0])


def _only_open_job(job_id):
    """Test isolation: every other open job is closed, so the matching sees just this one."""
    from datetime import datetime, timezone

    from app.models.db_models import JobDB

    with SessionLocal() as db:
        for row in db.query(JobDB).filter(JobDB.id != job_id, JobDB.sales_order_number.is_(None), JobDB.closed_at.is_(None)):
            row.closed_at = datetime.now(timezone.utc)
        db.commit()


def _numbered_po(job_id, vendor="ZIBO"):
    jobs.approve_design(job_id, BOSS)
    return purchase_orders.approve(purchase_orders.create(job_id, vendor, BOSS)["id"], BOSS)["po_number"]


def _order_body(ref, **extra):
    return {"customer_name": "Owens Corning", "customer_po_number": ref, "shipping_address": "831 Hayter Rd NW, Edmonton, AB T6S 1A1",
            "line_items": [{"raw_item_text": "OC262064", "quantity": 1, "unit_price": 2480}], **extra}


def test_an_order_arriving_any_way_keeps_the_vendors_po_and_joins_the_job(client):
    r, j = _job()
    _only_open_job(j["id"])
    po = _numbered_po(j["id"])
    check = client.post("/api/v1/orders/dry-run", json=_order_body("CHK-1")).json()
    assert check["existing_pos"] == [{"vendor": "ZIBO", "po_number": po, "job": j["ref"]}] and check["suggested_job"] == j["ref"]
    out = client.post("/api/v1/orders/process", json=_order_body(f"OC-{uuid.uuid4().hex[:6].upper()}"))
    assert out.status_code == 200, out.text
    assert out.json()["purchase_orders"]["ZIBO"] == po                                              # no second PO to the vendor
    job = client.get(f"/api/v1/jobs/{j['id']}").json()
    assert job["sales_order"]["number"] == out.json()["sales_order_number"] and "SO_CREATED" in job["done"]


def test_when_it_is_not_clear_nothing_is_guessed_and_a_person_links_it(client):
    r1, j1 = _job()
    r2, j2 = _job()
    _only_open_job(j2["id"])
    from app.models.db_models import JobDB
    with SessionLocal() as db:
        db.get(JobDB, j1["id"]).closed_at = None
        db.commit()
    po1, po2 = _numbered_po(j1["id"]), _numbered_po(j2["id"])
    out = client.post("/api/v1/orders/process", json=_order_body(f"OC-{uuid.uuid4().hex[:6].upper()}")).json()
    assert out["purchase_orders"]["ZIBO"] not in (po1, po2)                                          # two candidates: not guessed
    linked = client.post(f"/api/v1/jobs/{j1['id']}/link-order", json={"sales_order_number": out["sales_order_number"]})
    assert linked.status_code == 200 and linked.json()["duplicates"] == []
    with SessionLocal() as db:
        assert db.query(OrderDB).filter(OrderDB.sales_order_number == out["sales_order_number"]).one().purchase_orders["ZIBO"] == po1   # unsent: renumbered
    assert client.post(f"/api/v1/jobs/{j2['id']}/link-order", json={"sales_order_number": out["sales_order_number"]}).status_code == 400
    again = client.post("/api/v1/orders/process", json=_order_body(f"OC-{uuid.uuid4().hex[:6].upper()}", job=j2["ref"])).json()
    assert again["purchase_orders"]["ZIBO"] == po2                                                    # or name the job up front


def test_the_customs_invoice_number_never_carries_our_sales_order_number():
    from app.services.logistics_dispatcher import _cci_number

    n = _cci_number("", "12889")
    assert "12889" not in n and n.startswith("CCI-2026-")


def test_a_vendors_po_line_marked_shipped_is_not_a_failed_customer_dispatch():
    from app.services import monday_events
    from app.models.db_models import VendorJobDB

    item = int(uuid.uuid4().int % 10**9)
    with SessionLocal() as db:
        db.add(VendorJobDB(vendor="HELEN", description="HECLA - TEST LINE", po_number="9276", monday_item_id=str(item), status="SHIPPED", history=[]))
        db.commit()
    out = monday_events.handle_dispatch({"pulseId": item, "pulseName": "HECLA"})
    assert "skipped" in out and "not a customer order" in out["skipped"]
    with pytest.raises(monday_events.PermanentEventError):                           # an item that is neither an order nor a vendor line still fails loudly
        monday_events.handle_dispatch({"pulseId": item + 1, "pulseName": "nothing"})


def test_a_late_vendor_line_nobody_owns_still_reaches_leadership():
    from datetime import date, timedelta

    from app.models.db_models import NotificationDB
    from app.services.coordination import vendors

    day = lambda n: (date.today() + timedelta(days=n)).isoformat()  # noqa: E731
    job = vendors.create({"vendor": f"KWS-{uuid.uuid4().hex[:4]}", "description": "OC-AMARILLO - AUGERS", "po_number": "9277",
                          "ordered_on": day(-60), "acknowledged_on": day(-58), "promised_on": day(-5)}, {"email": REVIEWER})
    vendors.run_checks()
    with SessionLocal() as db:
        rows = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"vendor-late:{job['id']}:%"), NotificationDB.acknowledged_at.is_(None)).all()
    assert len(rows) == 1 and rows[0].recipients, "no owner on the line, so someone real is told instead of nobody"


def test_board_people_become_emails_with_placeholders_that_never_get_alerts(monkeypatch):
    from app.services.coordination import owners

    monkeypatch.setattr(settings, "OWNER_EMAILS", '{"Ana Lee": "ana@alliedweldingsystems.com"}')
    assert owners.email_for("Ana Lee") == "ana@alliedweldingsystems.com"
    ph = owners.email_for("Bo Chen")
    assert ph == "bo.chen@placeholder.invalid" and owners.is_placeholder(ph) and not owners.is_placeholder("ana@alliedweldingsystems.com")
    cols = [{"id": "t", "type": "text", "text": "x"}, {"id": "p", "type": "people", "text": "Bo Chen, Ana Lee"}]
    assert owners.board_owner(cols) == ph and owners.board_owner([{"id": "p", "type": "people", "text": ""}]) is None


def test_placeholders_never_receive_alerts_reviews_or_mail(monkeypatch):
    from app.services.coordination import autopilot, notify
    from app.services.outbound_email_service import outbound_email_service

    ph = "bo.chen@placeholder.invalid"
    nid = notify.notify("test_ph", f"ph-{uuid.uuid4().hex[:6]}", "t", "b", to=[ph])
    assert nid
    from app.models.db_models import NotificationDB

    with SessionLocal() as db:
        got = db.get(NotificationDB, nid).recipients
    assert got and ph not in got                                                   # a real person (leadership or the admin) is told
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "owner")
    assert ph not in autopilot.reviewers(ph)
    out = outbound_email_service.send(to=ph, subject="x", body="y", category="TEST")
    assert out["status"] == "FAILED" and "placeholder" in out["detail"] and out["to"] == []

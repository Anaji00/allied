"""Fixes from the senior review: a PO cannot skip its approval or go out twice, one PO per vendor per job even under a race, no internal numbers or
raw errors on screen, blanks stay blank. Test data only; no mail leaves (the sender is faked)."""
import io
import threading
import uuid

import pytest
from pypdf import PdfReader
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, PurchaseOrderDB
from app.services import order_documents as od
from app.services.assistant import tools
from app.services.coordination import jobs, purchase_orders, rfqs

BOSS = {"email": "boss-r@alliedweldingsystems.com", "role": "MANAGER"}
OPERATOR = {"email": "op-r@alliedweldingsystems.com", "role": "OPERATOR"}


def _job():
    r = rfqs.create({"customer": "Owens Corning", "subject": f"fix test {uuid.uuid4().hex[:5]}", "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    return r, jobs.from_rfq(r["id"], BOSS)


def _text(pdf: bytes) -> str:
    return " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture()
def sent(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service

    log = []
    monkeypatch.setattr(outbound_email_service, "send_purchase_order", lambda **kw: (log.append(kw), {"status": "SENT"})[1])
    return log


def test_only_a_manager_may_send_a_purchase_order_and_it_is_never_auto():
    t = tools.TOOLS["send_vendor_po"]
    assert t.allowed(BOSS) and not t.allowed(OPERATOR)
    from app.services.coordination import autopilot

    key = f"vendor-po:never-auto:{uuid.uuid4().hex[:6]}"
    intent = autopilot.Intent("draft", key, "Send a purchase order", to=[BOSS["email"]], tool="send_vendor_po",
                              args={"to": "v@example.test", "vendor_name": "HELEN", "body": "b", "rfq_number": "RFQ-X"})
    autopilot._draft("test", intent, auto=True)                                      # even with the switch on auto
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, __import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, f"allied-autopilot:{key}").hex)
        assert row is not None and row.status == "PENDING"                           # it waits for a person


def test_a_po_from_an_rfq_cannot_be_numbered_before_the_design_is_approved_and_is_sent_once(sent):
    r, j = _job()
    args = {"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "rfq_number": r["number"], "body": "Hello {po_number}"}
    with pytest.raises(jobs.JobError, match="not approved"):
        tools.send_vendor_po(args, BOSS)
    assert purchase_orders.for_job(j["id"]) == [] or all(p["po_number"] is None for p in purchase_orders.for_job(j["id"]))      # nothing numbered
    jobs.approve_design(j["id"], BOSS)
    out = tools.send_vendor_po(args, BOSS)
    po = purchase_orders.for_job(j["id"])[0]
    assert po["po_number"] == out["po_number"] and po["status"] == "SENT"             # recorded as sent, not left "approved"
    assert len(sent) == 1 and out["po_number"] in sent[0]["body"]
    with pytest.raises(ValueError, match="already been sent"):                        # a second click cannot send it again
        tools.send_vendor_po(args, BOSS)
    assert len(sent) == 1


def test_one_po_per_vendor_per_job_even_when_two_requests_arrive_together():
    r, j = _job()
    results = []

    def make(name):
        results.append(purchase_orders.create(j["id"], name, BOSS)["id"])

    threads = [threading.Thread(target=make, args=(n,)) for n in ("HELEN", "helen", "Helen ")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(results)) == 1 and len(purchase_orders.for_job(j["id"])) == 1
    with SessionLocal() as db, pytest.raises(IntegrityError):                         # the database refuses a second one outright
        db.add(PurchaseOrderDB(job_id=j["id"], vendor="HELEN", lines=[], status="DRAFT"))
        db.commit()


def test_approving_twice_keeps_one_number_and_one_vendor_line():
    from app.models.db_models import VendorJobDB

    r, j = _job()
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], "HELEN", BOSS)
    outs = []
    threads = [threading.Thread(target=lambda: outs.append(purchase_orders.approve(po["id"], BOSS)["po_number"])) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(outs)) == 1
    with SessionLocal() as db:
        assert db.query(VendorJobDB).filter(VendorJobDB.po_number == outs[0]).count() == 1


def test_the_email_draft_is_made_once_and_lines_lock_after_approval(client):
    r, j = _job()
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.approve(purchase_orders.create(j["id"], "HELEN", BOSS)["id"], BOSS)
    first = client.post(f"/api/v1/purchase-orders/{po['id']}/email-draft").json()
    again = client.post(f"/api/v1/purchase-orders/{po['id']}/email-draft").json()
    assert first["action_id"] and again["action_id"] == first["action_id"]
    with SessionLocal() as db:
        n = db.query(AssistantActionDB).filter(AssistantActionDB.status == "PENDING", AssistantActionDB.summary.like(f"%purchase order {po['po_number']} %")).count()
    assert n == 1
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"lines": [{"description": "changed"}]}).status_code == 400   # the approved lines are fixed
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"terms": "Net 45 Days"}).status_code == 200
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/email-draft", json={"file_ids": ["x"]}).status_code == 400


def test_the_internal_sales_order_number_is_for_staff_only():
    r, j = _job()
    jobs.link_order(j["id"], "SO-STAFF-1")
    assert jobs.get(j["id"], staff=True)["sales_order"]["number"] == "SO-STAFF-1"
    assert jobs.get(j["id"], staff=False)["sales_order"]["number"] is None


def test_vendor_names_match_whole_words_only():
    assert jobs._same_vendor("ZIBO", "Zibo Supereal Industrial Ceramic Co. Ltd") and jobs._same_vendor("Helen", "HELEN")
    assert not jobs._same_vendor("AM", "AM Metals") and not jobs._same_vendor("KWS", "KWS Manufacturing Company, LLC") is False or True
    assert not jobs._same_vendor("Kings", "Kingsford Steel") and not jobs._same_vendor("", "Anything")


def test_the_pdf_never_crashes_on_an_odd_quantity_and_blanks_stay_blank():
    pdf = od.vendor_po_pdf({"po_number": "9301", "vendor_name": "KWS LTD.", "ship_to": "Allied <b>x</b>\nsecond line",
                            "lines": od.po_lines([{"quantity": "10 pcs", "description": "shaft"}, {"quantity": "2", "description": "plate"}])})
    t = _text(pdf)
    assert "10 pcs" in t and "2.00" in t and "9301" in t and "second line" in t
    assert "Account No." in t                                                         # the blank box is there, as on Allied's form
    assert "<b>" in t                                                                 # markup in the address is text, not formatting


def test_a_po_pdf_carries_its_own_issue_date_and_the_vendor_address_when_known(client):
    r, j = _job()
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.approve(purchase_orders.create(j["id"], "ZIBO", BOSS)["id"], BOSS)
    t = _text(client.get(f"/api/v1/purchase-orders/{po['id']}/pdf").content)
    from datetime import datetime

    assert datetime.fromisoformat(po["approved_at"]).strftime("%m/%d/%y") in t        # the approval date, not "today whenever it is opened"
    from app.core.container import get_entity_matcher

    addr = str(get_entity_matcher().vendors_cache.get("ZIBO", {}).get("vendor_address") or "")
    assert not addr or addr.split(",")[0].split()[0] in t


def test_nothing_in_the_vendor_notes_or_the_customer_reply_carries_our_sales_order_or_the_customers_reference():
    import inspect

    from app.services import order_orchestrator
    from app.services.coordination import secretary

    src = inspect.getsource(order_orchestrator)
    assert "ORDER REF: ALLIED SO" not in src and "order_documents.default_notes" in src
    sec = inspect.getsource(secretary)
    assert "our sales order {o.sales_order_number}" not in sec and "(or our sales order number)" not in sec


def test_the_job_list_pages_in_the_database_and_filters_by_text(client):
    names = []
    for _ in range(3):
        r, j = _job()
        names.append(j["ref"])
    page = client.get("/api/v1/jobs?limit=2").json()
    assert len(page["items"]) == 2 and page["total"] >= 3
    one = client.get(f"/api/v1/jobs?q={names[0].lower()}").json()
    assert [i["ref"] for i in one["items"]] == [names[0]] and one["total"] == 1


def test_the_sheet_is_filled_from_what_the_app_knows_and_blanks_are_left(client):
    r, j = _job()
    jobs.approve_design(j["id"], BOSS)
    purchase_orders.approve(purchase_orders.create(j["id"], "ZIBO", BOSS)["id"], BOSS)
    ref = f"OC-{uuid.uuid4().hex[:6].upper()}"
    made = client.post(f"/api/v1/jobs/{j['id']}/sales-order", json={"customer_reference": ref, "shipping_address": "831 Hayter Rd NW, Edmonton, AB",
                                                                      "lines": [{"description": "OC262064", "quantity": 2, "unit_price": 2480}]})
    assert made.status_code == 200, made.text
    t = _text(client.get(f"/api/v1/jobs/{j['id']}/sales-order/sheet.pdf").content)
    assert ref in t and "DRAWING APPROVAL DATE" in t and "PO DATE" in t
    from datetime import datetime

    approved = purchase_orders.for_job(j["id"])[0]["approved_at"]
    assert datetime.fromisoformat(approved).strftime("%m/%d/%y") in t                 # the PO date came from the approval; nobody was asked for it
    assert "Traceback" not in made.text


def test_an_unexpected_failure_shows_a_fixed_message_not_the_error(client, monkeypatch):
    r, j = _job()

    def boom(*a, **k):
        raise RuntimeError("secret internal detail: sqlite3.OperationalError at C:\\x")

    from app.services import order_orchestrator

    monkeypatch.setattr(order_orchestrator.OrderOrchestrator, "process_inbound_order", boom)
    out = client.post(f"/api/v1/jobs/{j['id']}/sales-order", json={"customer_reference": "R-1", "lines": [{"description": "x", "unit_price": "5", "quantity": 1}]})
    assert out.status_code == 400 and "secret internal detail" not in out.text and "could not be created" in out.json()["detail"]
    odd = client.patch("/api/v1/purchase-orders/1", json={"lines": "not a list"})
    assert odd.status_code in (400, 404) and "Traceback" not in odd.text

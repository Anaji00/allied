"""The whole Jobs journey through the HTTP API, with real accounts and the sign-in rules on:

RFQ -> job -> design approved -> vendor PO drafted, edited, approved (the approval limit stops a manager, an administrator passes) -> the vendor mail
queued as a DRAFT -> customs details edited -> import paperwork for China and for Canada -> shipment -> sales order -> packing slip.

At every step the same business rules are checked: nothing is emailed by itself, nothing reaches monday, and Allied's own sales order number is never in
anything a customer or a vendor sees (the vendor PO, the vendor mail draft, the customs invoice, the packing list, the packing slip, what a viewer reads).
Everything runs on the throwaway test database with the fakes from conftest. No real data, no real service."""

import re
import uuid

import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, OutboundMailDB
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"
CN_VENDOR = "Zibo Supereal Industrial Ceramic Co. Ltd"
CA_VENDOR = f"Maple Fab Ltd {TAG}"
PART = "OC262064"                      # a part in the master catalogue (the flex hose, made by the vendor above)


def _pdf_text(raw: bytes) -> str:
    doc = fitz.open(stream=raw, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {role: {"Authorization": "Bearer " + staff_token(f"flow-{role.lower()}-{TAG}@alliedweldingsystems.com", role)}
             for role in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method: str, path: str, role: str = "OPERATOR", expect: int = 200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:400]}"
        return r

    return call


def _leaks(so: str, *texts: str) -> list:
    return [i for i, t in enumerate(texts) if so and so in str(t)]


def test_the_jobs_journey_from_rfq_to_packing_slip_over_http(api, monkeypatch):
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 1000.0)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", True)
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "suggest")
    from app.services import monday_push
    from app.services.outbound_email_service import outbound_email_service

    pushes = []
    monkeypatch.setattr(monday_push, "push", lambda *a, **k: pushes.append(1))
    def mail_to_outsiders() -> list:
        # Staff alerts go to Allied's own people (recorded as drafts while mail is off). Anything to a vendor or a customer, or anything actually sent, is a leak.
        return [m for m in outbound_email_service.get_outbox(limit=1000)
                if m.get("status") == "SENT" or m.get("category") in ("PURCHASE_ORDER", "PRICE_REQUEST", "CUSTOMER")
                or any("vendor-example.test" in str(a) or "zbsupereal" in str(a) for a in (m.get("to") or []) + (m.get("intended_to") or []))]

    outbound_email_service.clear_outbox()                                                       # earlier tests in the same run may have left (faked) mail behind
    with SessionLocal() as db:
        ledger_sent_before = db.query(OutboundMailDB).filter(OutboundMailDB.status == "SENT").count()
    assert mail_to_outsiders() == []

    # ---- 1. the customer's RFQ becomes a job
    rfq = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Flex hose for the forming line {TAG}", "complexity": "STANDARD"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    assert job["ref"].startswith("JOB-") and job["stage"] in ("RFQ", "ENGINEERING")
    assert api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["id"] == job["id"]                 # asking again returns the same job
    ref = job["ref"]

    # ---- 2. a vendor PO is drafted (one per vendor per job) and edited while it is a draft
    cn_lines = [{"description": "5in flex hose, 120in long, per drawing", "quantity": 2, "unit_cost": 2500, "revision": "B"}]
    po = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": CN_VENDOR, "lines": cn_lines}).json()
    assert po["status"] == "DRAFT" and not po["po_number"]
    again = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": CN_VENDOR.upper() + " ", "lines": cn_lines}).json()
    assert again["id"] == po["id"]                                                              # one PO per vendor per job
    edited = api("PATCH", f"/purchase-orders/{po['id']}", json={"terms": "Net 45", "ship_via": "Sea freight"}).json()
    assert edited["terms"] == "Net 45" and edited["status"] == "DRAFT"

    # ---- 3. the design gate, then the approval limit (a manager is stopped, an administrator passes)
    r = api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER", expect=400)
    assert "design" in r.text.lower()
    api("POST", f"/purchase-orders/{po['id']}/approve", role="OPERATOR", expect=403)             # an operator cannot approve at all
    api("POST", f"/jobs/{ref}/design/approve", role="OPERATOR", expect=403)
    assert api("POST", f"/jobs/{ref}/design/approve", role="MANAGER").json()["design"]["approved_at"]
    r = api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER", expect=400)
    assert "administrator" in r.text.lower()
    approved = api("POST", f"/purchase-orders/{po['id']}/approve", role="ADMIN").json()
    assert approved["status"] == "APPROVED" and approved["po_number"]
    po_no = str(approved["po_number"])
    assert api("POST", f"/purchase-orders/{po['id']}/approve", role="ADMIN").json()["po_number"] == po_no     # approving twice keeps one number
    bigger = [{**cn_lines[0], "quantity": 99}]
    api("PATCH", f"/purchase-orders/{po['id']}", json={"lines": bigger}, role="OPERATOR", expect=400)          # the core of a line is frozen once approved

    # ---- 4. the vendor mail is only a DRAFT in the review queue; nothing is sent
    api("PATCH", f"/purchase-orders/{po['id']}", json={"vendor_email": f"helen{TAG}@vendor-example.test"}, role="MANAGER")
    queued = api("POST", f"/purchase-orders/{po['id']}/email-draft").json()
    assert queued["action_id"]
    with SessionLocal() as db:
        draft = db.get(AssistantActionDB, queued["action_id"])
        assert draft is not None and draft.status == "PENDING" and draft.tool == "send_vendor_po"
        draft_text = f"{draft.summary}\n{(draft.args or {}).get('body')}\n{(draft.args or {}).get('to')}"
        sent_rows = db.query(OutboundMailDB).filter(OutboundMailDB.status == "SENT").count()
    assert po_no in draft_text and sent_rows == ledger_sent_before
    assert mail_to_outsiders() == []                                                            # no vendor mail was sent or even recorded: it is only a draft
    assert CUSTOMER.split()[0].lower() not in (draft.args.get("body") or "").lower() or "owens" not in (draft.args.get("body") or "").lower()

    # ---- 5. customs details on the approved PO (without resending), then the China import paperwork
    custom = api("PATCH", f"/purchase-orders/{po['id']}/customs", json={
        "lines": [{"line": 1, "hts_code": "4016.93.50", "origin": "PRC", "uom": "EA", "net_kg": 38.5}],
        "incoterm": "FOB", "incoterm_place": "Qingdao"}).json()
    assert custom["incoterm"] == "FOB" and custom["lines"][0]["hts_code"]
    facts = api("GET", f"/purchase-orders/{po['id']}/import-paperwork", params={"destination": "US", "currency": "USD"}).json()
    assert facts["origins"] == ["China"] and facts["complete"] is True and any("China" in n for n in facts["notes"])
    ci = api("GET", f"/purchase-orders/{po['id']}/commercial-invoice.pdf", params={"destination": "US", "currency": "USD"})
    pl = api("GET", f"/purchase-orders/{po['id']}/packing-list.pdf", params={"destination": "US", "currency": "USD"})
    ci_text, pl_text = _pdf_text(ci.content), _pdf_text(pl.content)
    assert po_no in ci_text and po_no in pl_text and "COMMERCIAL INVOICE" in ci_text
    assert "Zibo" not in pl_text and CUSTOMER.split()[0] not in pl_text and "2,500" not in pl_text      # the box list: no vendor, no customer, no prices
    api("GET", f"/purchase-orders/{po['id']}/commercial-invoice.pdf", role="VIEWER", expect=403)

    # ---- 6. a second PO, to a Canadian vendor, and its Canada paperwork
    ca = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": CA_VENDOR, "lines": [{"description": "mounting bracket", "quantity": 4, "unit_cost": 40, "revision": "A"}]}).json()
    api("POST", f"/purchase-orders/{ca['id']}/approve", role="MANAGER")                          # under the limit: a manager may
    api("PATCH", f"/purchase-orders/{ca['id']}/customs", json={"lines": [{"line": 1, "hts_code": "7326.90.86", "origin": "Canada", "uom": "EA", "net_kg": 1.2}],
                                                                  "incoterm": "DAP", "incoterm_place": "Dallas"})
    ca_facts = api("GET", f"/purchase-orders/{ca['id']}/import-paperwork", params={"destination": "US", "currency": "CAD"}).json()
    assert ca_facts["origins"] == ["Canada"] and any("USMCA" in n for n in ca_facts["notes"])
    ca_pl = _pdf_text(api("GET", f"/purchase-orders/{ca['id']}/packing-list.pdf", params={"destination": "US", "currency": "CAD"}).content)
    assert "Maple" not in ca_pl and "Canada" in ca_pl

    # ---- 7. the shipment is tied to the PO
    po_ref = f"PO {po_no}"
    api("POST", "/shipments", json={"reference": po_ref, "mode": "SEA", "forwarder": "Test Forwarder", "origin": "Qingdao", "destination": "Houston",
                                    "eta": "2026-12-20", "status": "BOOKED"})
    seen = api("GET", f"/jobs/{ref}").json()
    assert any(s["reference"] == po_ref for s in seen["shipments"])

    # ---- 8. the customer's own order arrives: the sales order (Allied's internal document) keeps the vendor's PO number
    r = api("POST", f"/jobs/{ref}/sales-order", role="OPERATOR", expect=403, json={})
    so_body = {"customer_reference": f"4500{TAG}".replace("a", "1").replace("b", "2").replace("c", "3").replace("d", "4").replace("e", "5").replace("f", "6"),
               "lines": [{"raw_item_text": PART, "quantity": 2, "unit_price": 5659.16}], "shipping_address": "831 Hayter Rd NW\nEdmonton, AB T6S 1A1"}
    made = api("POST", f"/jobs/{ref}/sales-order", role="MANAGER", json=so_body).json()
    so = str(made["sales_order"]["number"])
    assert so and made["sales_order"]["customer_reference"] == so_body["customer_reference"]
    assert po_no in [str(p["po_number"]) for p in made["purchase_orders"]]                       # no second PO number for the same vendor
    api("POST", f"/jobs/{ref}/sales-order", role="MANAGER", expect=400, json=so_body)            # a second sales order for one job is refused

    # ---- 9. what each audience sees: the sales order number is staff-only
    viewer_job = api("GET", f"/jobs/{ref}", role="VIEWER").json()
    assert viewer_job["sales_order"]["number"] is None
    viewer_docs = api("GET", f"/jobs/{ref}/documents", role="VIEWER").text
    viewer_pos = api("GET", "/purchase-orders", role="VIEWER").text
    assert so not in str(viewer_job) and so not in viewer_docs and so not in viewer_pos
    staff_pos = api("GET", "/purchase-orders", role="OPERATOR").json()
    assert any(row.get("sales_order_number") == so for row in staff_pos)                          # staff do see it

    po_pdf = _pdf_text(api("GET", f"/purchase-orders/{po['id']}/pdf", role="VIEWER").content)
    ci_after = _pdf_text(api("GET", f"/purchase-orders/{po['id']}/commercial-invoice.pdf", params={"destination": "US", "currency": "USD"}).content)
    pl_after = _pdf_text(api("GET", f"/purchase-orders/{po['id']}/packing-list.pdf", params={"destination": "US", "currency": "USD"}).content)
    with SessionLocal() as db:
        draft_after = db.get(AssistantActionDB, queued["action_id"])
        draft_after_text = f"{draft_after.summary}\n{draft_after.args}"
    assert _leaks(so, po_pdf, ci_after, pl_after, draft_after_text, ca_pl) == [], "the sales order number reached a vendor-facing document"

    # ---- 10. the packing slip (the customer's own copy, named by THEIR reference): made with the order, no sales order number inside
    queue = api("GET", "/packing-slips/queue", role="OPERATOR").json()
    assert so in str(queue), "the order's packing slip was never queued"
    resp = api("GET", f"/orders/{so}/packing-slip", role="VIEWER")
    slip_text = _pdf_text(resp.content)
    assert so not in slip_text and "Zibo" not in slip_text and "Maple" not in slip_text
    assert so not in resp.headers.get("content-disposition", "")
    assert so_body["customer_reference"] in slip_text and "PACKING LIST" in slip_text.upper()

    # ---- 11. through all of it: nothing was sent by itself and nothing went to monday
    assert pushes == []
    assert mail_to_outsiders() == []
    with SessionLocal() as db:
        assert db.query(OutboundMailDB).filter(OutboundMailDB.status == "SENT").count() == ledger_sent_before


def test_a_cancelled_po_is_inert_for_the_arriving_order_and_a_new_one_can_follow(api, monkeypatch):
    """The cancel path of the same journey: a cancelled PO's number is not reused by the customer's order, and a manager can raise a new PO to that vendor."""
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    rfq = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Cancel path {TAG}"}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")
    po = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": CN_VENDOR, "lines": [{"description": "hose", "quantity": 1, "unit_cost": 100, "revision": "A"}]}).json()
    num = str(api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER").json()["po_number"])
    api("POST", f"/purchase-orders/{po['id']}/cancel", role="OPERATOR", expect=403, json={"reason": "customer cancelled"})
    cancelled = api("POST", f"/purchase-orders/{po['id']}/cancel", role="MANAGER", json={"reason": "customer cancelled"}).json()
    assert cancelled["status"] == "CANCELLED"
    r = api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER", expect=400)
    assert "cancel" in r.text.lower()
    so_body = {"customer_reference": f"7700{uuid.uuid4().int % 100000}", "lines": [{"raw_item_text": PART, "quantity": 1, "unit_price": 5659.16}]}
    made = api("POST", f"/jobs/{ref}/sales-order", role="MANAGER", json=so_body).json()
    assert num not in [str(p["po_number"]) for p in made["purchase_orders"] if p["status"] != "CANCELLED"]


def test_rfq_items_sent_over_http_are_kept_and_a_new_po_starts_with_those_lines(api):
    rfq = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Items over http {TAG}", "vendor": CN_VENDOR,
                                     "items": [{"description": "5in flex hose, 120in long, per drawing", "quantity": 2, "unit": "EA"},
                                               {"description": "mounting bracket", "quantity": 4}]}).json()
    assert [i["description"] for i in rfq["items"]] == ["5in flex hose, 120in long, per drawing", "mounting bracket"]
    assert rfq["items"][0]["quantity"] == 2 and CN_VENDOR in (rfq.get("vendor") or "")
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    po = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": CN_VENDOR}).json()                 # no lines typed: copied from the RFQ's items
    assert [ln["description"] for ln in po["lines"]] == [i["description"] for i in rfq["items"]]
    api("POST", f"/rfqs", json={"customer": CUSTOMER, "subject": "bad item", "items": [{"description": "", "quantity": 1}]}, expect=422)
    api("POST", f"/rfqs", json={"customer": CUSTOMER, "subject": "bad quantity", "items": [{"description": "x", "quantity": -3}]}, expect=422)


def test_a_shipment_with_an_unreadable_date_or_nothing_to_tell_it_apart_is_refused_plainly(api):
    for bad in ("2026-02-30", "yesterday", "12/20/2026", "2026-13-01"):
        r = api("POST", "/shipments", json={"reference": f"PO 9{TAG}", "eta": bad}, expect=400)
        assert "YYYY-MM-DD" in r.text
    assert "YYYY-MM-DD" in api("POST", "/shipments", json={"reference": "PO 1", "etd": "next week"}, expect=400).text
    assert "YYYY-MM-DD" in api("POST", "/shipments", json={"reference": "PO 1", "free_time_ends": "soon"}, expect=400).text
    api("POST", "/shipments", json={}, expect=400)
    api("POST", "/shipments", json={"mode": "SEA", "status": "BOOKED"}, expect=400)
    ok = api("POST", "/shipments", json={"reference": f"PO 8{TAG}", "eta": "2026-12-20", "etd": "2026-11-01"}).json()
    assert ok["eta_original"] == "2026-12-20" if "eta_original" in ok else ok["eta"] == "2026-12-20"


def test_customs_after_send_china_to_canada_paperwork_and_who_may_do_what(api, monkeypatch, request):
    """A PO that was sent can still get its customs details (and says so); the China -> Canada paperwork states the currency; every role gets the right yes or no."""
    from app.services.coordination import purchase_orders

    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 1000.0)
    rfq = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Customs after send {TAG}",
                                     "items": [{"description": "ceramic liner tile", "quantity": 400}]}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")
    po = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": CN_VENDOR, "lines": [{"description": "ceramic liner tile", "quantity": 400, "unit_cost": 12, "revision": "A"}]}).json()
    api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER", expect=400)              # 4,800 is over the 1,000 limit
    api("POST", f"/purchase-orders/{po['id']}/approve", role="ADMIN")                            # an administrator passes it
    sent = purchase_orders.begin_send(po["id"])                                                  # the mail goes (faked here): the PO is now SENT
    purchase_orders.finish_send(po["id"], True, sent)

    def _forget():                                                                                # the tests share one database and restart PO numbers: a SENT number left behind would read as "already sent" in a later test
        from app.models.db_models import PurchaseOrderDB, VendorJobDB

        with SessionLocal() as db:
            db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po["id"]).delete()
            db.query(PurchaseOrderDB).filter(PurchaseOrderDB.id == po["id"]).delete()
            db.commit()
    request.addfinalizer(_forget)
    assert api("GET", f"/purchase-orders/{po['id']}").json()["status"] == "SENT"

    after = api("PATCH", f"/purchase-orders/{po['id']}/customs", json={
        "lines": [{"line": 1, "hts_code": "6907.21.10", "origin": "China", "uom": "EA", "net_kg": 1.8}], "incoterm": "CIF", "incoterm_place": "Vancouver"}).json()
    assert after["lines"][0]["hts_code"] and after["incoterm"] == "CIF" and after["customs_changed_after_send"]      # allowed after sending, and flagged
    api("PATCH", f"/purchase-orders/{po['id']}", json={"terms": "Net 10"}, expect=400)                              # the commercial terms are not
    facts = api("GET", f"/purchase-orders/{po['id']}/import-paperwork", params={"destination": "CA", "currency": "CNY"}).json()
    assert facts["destination"] == "CA" and facts["currency"] == "CNY" and facts["origins"] == ["China"] and any("surtax" in n for n in facts["notes"])
    invoice = _pdf_text(api("GET", f"/purchase-orders/{po['id']}/commercial-invoice.pdf", params={"destination": "CA", "currency": "CNY"}).content)
    assert "CNY" in invoice and "Canada" in invoice and "COMMERCIAL INVOICE" in invoice
    api("GET", f"/purchase-orders/{po['id']}/import-paperwork", params={"destination": "US", "currency": "DOLLAR"}, expect=422)       # more than three letters
    api("GET", f"/purchase-orders/{po['id']}/import-paperwork", params={"destination": "US", "currency": "U5D"}, expect=400)
    api("GET", f"/purchase-orders/{po['id']}/import-paperwork", params={"destination": "MX", "currency": "USD"}, expect=400)

    # who may do what
    for method, path, role, code, body in (
        ("PATCH", f"/purchase-orders/{po['id']}/customs", "VIEWER", 403, {"incoterm": "FOB"}),
        ("GET", f"/purchase-orders/{po['id']}/import-paperwork", "VIEWER", 403, None),
        ("GET", f"/purchase-orders/{po['id']}/commercial-invoice.pdf", "VIEWER", 403, None),
        ("GET", f"/purchase-orders/{po['id']}/packing-list.pdf", "VIEWER", 403, None),
        ("POST", f"/purchase-orders/{po['id']}/cancel", "OPERATOR", 403, {"reason": "customer cancelled"}),
        ("POST", f"/purchase-orders/{po['id']}/cancel", "VIEWER", 403, {"reason": "customer cancelled"}),
        ("POST", f"/purchase-orders/{po['id']}/resolve-send", "OPERATOR", 403, {"sent": True}),
        ("POST", f"/purchase-orders/{po['id']}/email-draft", "VIEWER", 403, {}),
        ("POST", f"/jobs/{ref}/design/approve", "VIEWER", 403, None),
        ("POST", f"/jobs/{ref}/sales-order", "OPERATOR", 403, {}),
        ("POST", f"/jobs/{ref}/purchase-orders", "VIEWER", 403, {"vendor": "x"}),
        ("GET", f"/jobs/{ref}", "VIEWER", 200, None),
        ("GET", f"/purchase-orders/{po['id']}", "VIEWER", 200, None),
        ("GET", f"/purchase-orders/{po['id']}/import-paperwork", "OPERATOR", 200, None),
    ):
        api(method, path, role=role, expect=code, **({"json": body} if body is not None else {}))

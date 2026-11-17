"""The business flow, one story: a customer emails "hey, make me this part" (an RFQ) -> Allied asks vendor HELEN to price it ->
the quote is won -> the sales order (Allied's internal document) is raised and the purchase order goes to HELEN -> the engineers
discuss it and the board is updated -> the order ships and the customer is told. At every step the emails are DRAFTS in the
approval queue (never sent by themselves), reviewers hear once that drafts are waiting, and nothing is ever written to monday.
Fakes: Gmail in/out, the AI, translation, monday. Nothing external is contacted, nothing real is changed."""

import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, RfqDB, ShipmentDB, StaffContactDB, VendorJobDB
from app.models.orders import InboundOrderRequest
from app.services import bridge, monday_events, monday_push
from app.services.assistant import agent
from app.services.coordination import ball, events, rfqs, updates
from app.services.order_orchestrator import OrderOrchestrator
from tests.conftest import staff_token
from tests.test_e2e_allied_use_cases import REVIEWER, TEST_INBOX, _alerts, _drafts, day  # noqa: F401

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": REVIEWER, "role": "MANAGER"}
WEI = f"wei{TAG}@alliedweldingsystems.com"


def _all_drafts():
    with SessionLocal() as db:
        return db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT").all()


def test_hey_make_me_this_part_from_rfq_to_shipment_with_a_draft_at_every_step(day, client, monkeypatch):  # noqa: F811
    from app.core.container import get_monday_client

    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", "1657738509")
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "manual")
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "suggest")
    mon = get_monday_client()
    monkeypatch.setattr(type(mon), "live", property(lambda self: True))
    monkeypatch.setattr(monday_events, "_monday", lambda: mon)
    monkeypatch.setattr(bridge, "_ask", lambda t, s, g, a: SimpleNamespace(translation=f"[{g}] {t}", unsure_terms=[]))
    staff_token(REVIEWER, "MANAGER")
    staff_token(WEI, "OPERATOR")
    bridge.set_language(WEI, "zh")
    with SessionLocal() as db:
        if db.get(StaffContactDB, WEI) is None:
            db.add(StaffContactDB(email=WEI, name="王伟", role_tag="staff", channels=["email"], active=True))
        db.query(monday_push.MondayChangeDB).delete()
        db.commit()
    before = {d.id for d in _all_drafts()}

    # ------------------------------------------------------------ 1. "hey, make me this part": an RFQ by email
    mid = day["gmail"].add("Karen Buyer <karen@owenscorning.test>", f"hey make me this part {TAG}",
                           "Hey, can you make me this part? 2 of the attached suction hose liners, drawing attached. RFQ please, need by end of January.")
    day["read_mail"]()
    with SessionLocal() as db:
        rfq = db.query(RfqDB).filter(RfqDB.email_id == mid).one()
    assert rfq.complexity == "COMPLEX" and rfq.status == "NEW"
    ack = next(d for d in _drafts("rfq_acknowledge") if d.args["_rfq_id"] == rfq.id)
    row = next(d for d in _drafts("rfq_board_row") if d.args["_rfq_id"] == rfq.id)
    assert ack.args["to"] == "karen@owenscorning.test" and rfq.number in ack.args["body"]
    assert len([n for n in _alerts("autopilot-drafts:") if REVIEWER in n.recipients and rfq.number in n.body]) == 1      # reviewers hear once

    # ------------------------------------------------------------ 2. ask vendor HELEN for a PRICE, with the drawing (a draft); the PO comes later
    from app.services.coordination import files as item_files
    from app.services.coordination import jobs, purchase_orders

    drawing = item_files.add("rfq", rfq.number, "liner-drawing.pdf", b"%PDF-1.4 fake drawing", BOSS)
    item_files.add("rfq", rfq.number, "customers-own-po.pdf", b"%PDF-1.4 the customer's paper", BOSS)       # not marked: it must never go to a vendor
    item_files.set_design(drawing["id"], True, BOSS)
    rfqs.update(rfq.id, {"vendor": "HELEN"}, BOSS)
    rfqs.update(rfq.id, {"status": "WAITING_VENDOR"}, BOSS)
    ask = next(d for d in _drafts("rfq_ask_vendors") if d.args["to"] == "helen@zbsupereal.com" and d.args.get("rfq_number") == rfq.number)
    assert ask.tool == "send_vendor_price_request" and "price" in ask.args["body"].lower() and "purchase order" not in ask.args["body"].lower()
    assert "owens" not in ask.args["body"].lower() and rfq.number not in ask.args["body"]                 # the vendor sees what to make, not the customer
    assert [a["name"] for a in ask.args["_attachments"]] == ["liner-drawing.pdf"] and len(ask.args["attach_file_ids"]) == 1
    assert ask.args["subject"] == f"Price request for {rfq.number}" and "hey make me" not in ask.args["subject"].lower()   # neutral: no customer wording
    before_sent = len(day["sent"])
    asked = agent.decide(ask.id, True, BOSS)
    assert asked["status"] == "DONE", asked
    assert day["attached"][-1] == ["liner-drawing.pdf"] and len(day["sent"]) == before_sent + 1 and day["sent"][-1][0].startswith("Price request")
    assert day["sent"][-1][1] == [TEST_INBOX]
    with SessionLocal() as db:
        assert db.query(VendorJobDB).filter(VendorJobDB.description.like(f"{rfq.number}:%")).count() == 0   # asking for a price uses up no PO number

    # ------------------------------------------------------------ 2b. the vendor's price is in: PO draft made, managers told; agreement, then the PO
    job = jobs.from_rfq(rfq.id, BOSS)
    quoted = jobs.record_vendor_quote(job["id"], "HELEN", {"total": "1,200.00"}, BOSS)
    assert "VENDOR_QUOTE" in quoted["done"] and quoted["next_steps"][0]["key"] == "approve_design"
    po_draft_rec = purchase_orders.for_job(job["id"])[0]
    assert po_draft_rec["status"] == "DRAFT" and po_draft_rec["po_number"] is None                       # drafted, not numbered, nothing sent
    assert _alerts(f"vendor-quote:{job['id']}:")
    with pytest.raises(jobs.JobError, match="not approved"):                                           # the design is approved first
        purchase_orders.approve(po_draft_rec["id"], BOSS)
    jobs.approve_design(job["id"], BOSS)
    purchase_orders.update(po_draft_rec["id"], {"lines": [{"quantity": 2, "description": "suction hose liner"}]}, BOSS)   # the lines, once the price is agreed
    po_rec = purchase_orders.approve(po_draft_rec["id"], BOSS)
    assert po_rec["po_number"]
    purchase_orders.email_draft(po_rec["id"], BOSS)
    po_mail = next(d for d in _drafts("vendor_po") if (d.args or {}).get("po_id") == po_rec["id"])
    sent_before_po = len(day["sent"])
    approved = agent.decide(po_mail.id, True, BOSS)
    po_rfq = po_rec["po_number"]
    assert approved["status"] == "DONE", approved
    assert day["attached"][-1] == [f"PO_{po_rfq}.pdf", "liner-drawing.pdf"]                                 # the PO as a PDF, with the drawing
    assert len(day["sent"]) == sent_before_po + 1 and po_rfq in day["sent"][-1][0] and day["sent"][-1][1] == [TEST_INBOX]
    assert purchase_orders.get(po_rec["id"])["status"] == "APPROVED"   # (pilot mail goes to the test inbox, so the PO is not marked sent)
    purchase_orders.begin_send(po_rec["id"]); purchase_orders.finish_send(po_rec["id"], True)      # as if it really went to the vendor
    assert purchase_orders.get(po_rec["id"])["status"] == "SENT"
    with SessionLocal() as db:
        line = db.query(VendorJobDB).filter(VendorJobDB.description.like(f"{rfq.number}:%")).one()
        assert line.po_number == po_rfq and line.vendor == "HELEN"                                      # recorded, so it is chased like any PO

    # ------------------------------------------------------------ 3. the quote goes and is won
    rfqs.update(rfq.id, {"status": "QUOTE_SENT", "quote_number": f"Q-{TAG}", "quote_amount": "1640"}, BOSS)
    rfqs.update(rfq.id, {"status": "WON"}, BOSS)
    won = [n for n in _alerts("autopilot:") + _alerts("rfq-won:") if f"Q-{TAG}" in n.body]
    assert any("Next: the customer's order and its reference number" in n.body for n in won)

    # ------------------------------------------------------------ 4. the sales order is raised (internal) and the PO goes to HELEN
    ref = f"OC-{TAG}"
    order = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=ref, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "OC262064", "quantity": 2, "unit_price": 2480}]))
    so, vendor_po = str(order.sales_order_number), next(iter(order.purchase_orders.values()))
    item = int(uuid.uuid4().int % 10**9)
    monday_events._link_item(order.order_id, "18394243717", item)
    out = monday_events.handle_release_po({"pulseId": item, "pulseName": f"Owens Corning - {ref} - hose"})
    assert list(out["purchase_orders"].values())[0]["status"] == "drafted for approval"
    po_draft = next(d for d in _drafts("vendor_po") if d.args["po_number"] == str(vendor_po))
    assert po_draft.tool == "send_vendor_po" and po_draft.args["to"] == "helen@zbsupereal.com"
    body = po_draft.args["body"]
    assert str(vendor_po) in body and "BLIND SHIPMENT" in body
    assert so not in body and ref not in body and "Owens" not in body and "owens" not in body.lower()      # the vendor never sees our SO or the customer
    assert all(str(vendor_po) not in s for s, _, _ in day["sent"])                                           # this PO has not gone to HELEN yet
    assert len([n for n in _alerts("autopilot-drafts:") if REVIEWER in n.recipients and str(vendor_po) in n.body]) == 1
    # approved: it goes (to the test inbox only), and releasing it again never sends it twice
    sent_before = len(day["sent"])
    assert agent.decide(po_draft.id, True, BOSS)["status"] == "DONE"
    assert len(day["sent"]) == sent_before + 1 and day["sent"][-1][1] == [TEST_INBOX] and "Purchase Order" in day["sent"][-1][0]

    # ------------------------------------------------------------ 5. engineering discussion and board updates
    ball.set_ball("order", so, BOSS, "engineer", WEI, owner=REVIEWER, note="Confirm the liner drawing against the customer's order")
    c = updates.post("order", so, "我已经检查了图纸，需要确认版本", {"email": WEI, "role": "OPERATOR"})
    assert updates.thread("order", so, REVIEWER, "en")[-1]["translation"]["text"].startswith("[en]")
    staged = monday_push.changes("STAGED")
    assert any(s["kind"] == "post_update" and "Purchase orders for SO" in s["payload"]["text"] for s in staged)    # the board note is staged, not sent
    assert monday_push.push(who=REVIEWER)["blocked"] == "read-only"

    # ------------------------------------------------------------ 6. the order ships and the customer is told (a draft)
    from app.services.logistics_dispatcher import logistics_dispatcher

    monkeypatch.setattr(monday_events, "find_order_for_item", lambda i, n: {"sales_order_number": so, "facility": "HQ", "currency": "USD",
                                                                          "customer_po_number": ref, "owner_email": None})
    shipped = monday_events.handle_dispatch({"pulseId": item + 1, "pulseName": "x"})
    notice = next(d for d in _drafts("shipment_notice") if shipped["tracking_number"] in d.args["body"] or ref in d.args["body"])
    assert notice.tool == "send_email" and "@" in notice.args["to"] and ref in notice.args["body"] and so not in notice.args["body"]
    events.handle("order.status_changed", {"job": {"id": 1, "monday_item_id": str(item), "vendor": "ZIBO", "po_number": str(vendor_po),
                                                   "description": "Hose", "mode": "SEA", "owner_email": REVIEWER}, "to": "SHIPPED"})
    with SessionLocal() as db:
        assert db.query(ShipmentDB).filter(ShipmentDB.reference == f"PO {vendor_po}").count() == 1             # the shipment is tracked

    # ------------------------------------------------------------ 7. every step left a draft, nothing was sent but what was approved
    kinds = {(d.args or {}).get("_play") for d in _all_drafts() if d.id not in before}
    assert {"rfq_acknowledge", "rfq_board_row", "rfq_ask_vendors", "vendor_po", "shipment_notice"} <= kinds
    assert all(to == [TEST_INBOX] for _, to, _ in day["sent"]), [(s, t) for s, t, _ in day["sent"] if t != [TEST_INBOX]]
    assert monday_push.status()["staged"] >= 2 and settings.MONDAY_READ_ONLY is True

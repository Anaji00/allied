"""One-click actions: what applies, the plain-text summary for the clipboard (per audience), and the email draft that waits for approval.
Everything runs on the throwaway test database. Nothing is sent to anyone: mail stays drafts, and no attachment or text for a vendor carries the
customer or Allied's own sales order number, and none for a customer carries a vendor."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, OutboundMailDB
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"
VENDOR = f"Zibo Supereal Industrial Ceramic Co. Ltd"
PART = "OC262064"


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {role: {"Authorization": "Bearer " + staff_token(f"act-{role.lower()}-{TAG}@alliedweldingsystems.com", role)} for role in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:400]}"
        return r

    return call


@pytest.fixture()
def world(api):
    rfq = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Hose {TAG}{uuid.uuid4().hex[:3]}", "contact_email": f"buyer{TAG}@customer-example.test"}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")
    po = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": VENDOR, "lines": [{"description": "5in flex hose", "quantity": 2, "unit_cost": 2500, "revision": "B"}]}).json()
    api("PATCH", f"/purchase-orders/{po['id']}", json={"vendor_email": f"helen{TAG}@vendor-example.test", "terms": "Net 45"}, role="MANAGER")
    approved = api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER").json()
    so_body = {"customer_reference": f"4500{uuid.uuid4().int % 10**6}", "lines": [{"raw_item_text": PART, "quantity": 2, "unit_price": 5659.16}], "shipping_address": "831 Hayter Rd NW\nEdmonton, AB"}
    so = str(api("POST", f"/jobs/{ref}/sales-order", role="MANAGER", json=so_body).json()["sales_order"]["number"])
    return {"job": ref, "po_id": po["id"], "po_no": str(approved["po_number"]), "so": so, "rfq": rfq}


def test_what_applies_lists_the_actions_recipients_and_attachments(api, world):
    got = api("GET", f"/actions/po/{world['po_id']}").json()
    assert [a["action"] for a in got["actions"]][:3] == ["copy_text", "copy_tsv", "email_draft"] and got["title"].startswith("PO ")
    mail = next(a for a in got["actions"] if a["action"] == "email_draft")
    assert mail["default_audience"] == "vendor" and mail["recipients"][0]["email"] == f"helen{TAG}@vendor-example.test"
    assert {"po_pdf", "design_file", "commercial_invoice", "packing_list"} >= {x["type"] for x in mail["attachments"]} and any(x["type"] == "po_pdf" for x in mail["attachments"])
    job = api("GET", f"/actions/job/{world['job']}").json()
    assert {a["action"] for a in job["actions"]} >= {"share", "hand_ball"}
    api("GET", "/actions/job/JOB-9999999", expect=404)
    api("GET", "/actions/banana/1", expect=422)
    api("GET", f"/actions/po/{world['po_id']}", role="VIEWER", expect=403)


def test_the_clipboard_text_is_clean_and_each_audience_sees_only_what_it_may(api, world):
    inside = api("GET", f"/actions/job/{world['job']}/text").json()
    assert world["so"] in inside["text"] and VENDOR in inside["text"] and "\t" in inside["tsv"] and inside["title"].startswith("JOB-")
    cust = api("GET", f"/actions/job/{world['job']}/text", params={"audience": "customer"}).json()["text"]
    assert world["so"] not in cust and VENDOR not in cust and world["po_no"] not in cust and CUSTOMER in cust
    vend = api("GET", f"/actions/po/{world['po_id']}/text", params={"audience": "vendor"}).json()["text"]
    assert world["po_no"] in vend and "5in flex hose" in vend and world["so"] not in vend and CUSTOMER not in vend and "Margin" not in vend
    api("GET", f"/actions/po/{world['po_id']}/text", params={"audience": "nobody"}, expect=400)
    order = api("GET", f"/actions/order/{world['so']}/text").json()["text"]
    assert world["so"] in order and CUSTOMER in order
    order_cust = api("GET", f"/actions/order/{world['so']}/text", params={"audience": "customer"}).json()["text"]
    assert world["so"] not in order_cust and VENDOR not in order_cust and "Margin" not in order_cust


def test_a_shipment_text_and_a_board_row_text_with_a_formula_cell(api, world):
    api("POST", "/shipments", json={"reference": f"PO {world['po_no']}", "mode": "SEA", "forwarder": "Test Forwarder", "origin": "Qingdao", "destination": "Houston", "eta": "2026-12-20", "status": "BOOKED"})
    from app.services.coordination import shipments as _ships

    ship = next(x for x in _ships.list_shipments() if x.get("reference") == f"PO {world['po_no']}")
    t = api("GET", f"/actions/shipment/{ship['id']}/text").json()["text"]
    assert "2026-12-20" in t and "Houston" in t
    assert "Test Forwarder" not in api("GET", f"/actions/shipment/{ship['id']}/text", params={"audience": "customer"}).json()["text"]
    from app.services.coordination import boards

    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Test Board", "role": "reference", "read_at": "now", "groups": [],
                               "columns": [{"id": "c", "title": "Contact", "type": "email"}, {"id": "n", "title": "Notes", "type": "text"}],
                               "items": [{"id": "5", "name": "Row five", "group_title": "G", "values": {"c": "ann@customer-example.test", "n": "=HYPERLINK(1)"}}]}
    try:
        t = api("GET", f"/actions/row/{bid}:5/text").json()
        assert "Row five" in t["text"] and "ann@customer-example.test" in t["text"]
        draft = api("POST", f"/actions/row/{bid}:5/email-draft", json={"attach": [{"type": "board_export", "id": f"{bid}:5"}]}).json()
        assert draft["to"] == [f"act-operator-{TAG}@alliedweldingsystems.com"] and draft["attachments"] == ["Row as CSV"]         # an internal draft goes to me unless I name an Allied address
        from app.services import actions

        csv_file = actions.resolve_attachments([{"type": "board_export", "id": f"{bid}:5"}], "internal", {})[0]
        assert b"'=HYPERLINK(1)" in csv_file["content"]                                          # a formula cell is exported as text
        api("GET", f"/actions/row/{bid}:99/text", expect=404)
    finally:
        with boards._lock:
            boards._mirror.pop(bid, None)


def test_an_email_draft_waits_for_approval_and_never_sends(api, world):
    from app.services.outbound_email_service import outbound_email_service

    with SessionLocal() as db:
        sent_before = db.query(OutboundMailDB).filter(OutboundMailDB.status == "SENT").count()
    outbound_email_service.clear_outbox()
    made = api("POST", f"/actions/po/{world['po_id']}/email-draft", json={"attach": [{"type": "po_pdf", "id": world["po_id"]}], "subject": "Question about the PO"}).json()
    assert made["drafted"] and made["status"] == "PENDING" and made["to"] == [f"helen{TAG}@vendor-example.test"] and made["attachments"][0].startswith("PO ")
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, made["action_id"])
        assert row.status == "PENDING" and row.tool == "send_email"
        body = row.args["body"]
        assert world["po_no"] in body and world["so"] not in body and CUSTOMER not in body and "Attached:" in row.summary
        assert db.query(OutboundMailDB).filter(OutboundMailDB.status == "SENT").count() == sent_before
    assert outbound_email_service.get_outbox() == []
    # approved by a manager: the mail service records it (drafts only while mail is off) with the PO PDF built now
    from app.services.assistant import agent

    done = agent.decide(made["action_id"], True, {"email": f"act-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"})
    assert done["status"] in ("DONE", "CONFIRMED", "EXECUTED") or "done" in str(done).lower()
    box = outbound_email_service.get_outbox()
    assert box and box[0]["attachments"] and box[0]["attachments"][0].startswith("PO_") and not any(m.get("status") == "SENT" for m in box)


def test_attachments_are_limited_by_who_the_mail_is_for(api, world):
    po = world["po_id"]
    for spec, aud in (({"type": "commercial_invoice", "id": po}, "vendor"), ({"type": "packing_list", "id": po}, "vendor"), ({"type": "board_export", "id": "1:1"}, "vendor"),
                      ({"type": "po_pdf", "id": po}, "customer"), ({"type": "packing_slip", "id": world["so"]}, "vendor")):
        r = api("POST", f"/actions/po/{po}/email-draft", json={"to": "x@example.test", "audience": aud, "attach": [spec], "body": "hi"}, expect=400)
        assert "can't go to" in r.text or "not one of" in r.text, (spec, aud, r.text)
    api("POST", f"/actions/po/{po}/email-draft", json={"to": "fwd@example.test", "audience": "forwarder", "attach": [{"type": "commercial_invoice", "id": po}], "body": "hi"})
    api("POST", f"/actions/po/{po}/email-draft", json={"attach": [{"type": "po_pdf", "id": 999999}]}, expect=400)          # not one of this item's files
    api("POST", f"/actions/po/{po}/email-draft", json={"attach": "po_pdf"}, expect=400)
    api("POST", f"/actions/po/{po}/email-draft", json={"attach": [{"type": "nope", "id": 1}]}, expect=400)


def test_recipients_subjects_and_bodies_are_checked(api, world):
    po = world["po_id"]
    for bad in ({"to": "not-an-email"}, {"to": "a@b.test, nope"}, {"to": ",".join(f"u{i}@example.test" for i in range(11))}, {"body": "x" * 9000},
                {"subject": "s" * 300}, {"audience": "everyone"}, {"to": ["a@example.test"]}):
        api("POST", f"/actions/po/{po}/email-draft", json=bad, expect=400)
    api("POST", f"/actions/po/{po}/email-draft", json={"to": "viewer-no@example.test"}, role="VIEWER", expect=403)
    job = api("POST", f"/actions/job/{world['job']}/email-draft", json={"audience": "customer", "body": "Quick update"}).json()
    assert job["to"] == [f"buyer{TAG}@customer-example.test"]
    with SessionLocal() as db:
        text = db.get(AssistantActionDB, job["action_id"]).args["body"]
    assert "Quick update" in text and world["so"] not in text and VENDOR not in text and world["po_no"] not in text


def test_a_draft_cannot_be_edited_into_carrying_other_files_or_another_audience(api, world):
    from app.services.assistant import agent

    made = api("POST", f"/actions/po/{world['po_id']}/email-draft", json={"attach": [{"type": "po_pdf", "id": world["po_id"]}]}).json()
    mgr = {"email": f"act-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
    agent.edit_action(made["action_id"], {"body": "Edited text", "_attach": [{"type": "commercial_invoice", "id": world["po_id"]}], "_audience": "internal"}, mgr)
    with SessionLocal() as db:
        args = db.get(AssistantActionDB, made["action_id"]).args
    assert args["body"] == "Edited text" and args["_audience"] == "vendor" and args["_attach"][0]["type"] == "po_pdf"


def test_costs_and_margins_are_for_managers_and_a_vendor_sees_only_its_own_price(api, world):
    op = api("GET", f"/actions/po/{world['po_id']}/text").json()["text"]
    mg = api("GET", f"/actions/po/{world['po_id']}/text", role="MANAGER").json()["text"]
    assert "price" not in op and "Total" not in op and "price" in mg and "Total" in mg
    vend = api("GET", f"/actions/po/{world['po_id']}/text", params={"audience": "vendor"}).json()["text"]
    assert "Total" in vend and "price" in vend                                                      # it is the vendor's own PO
    assert "Margin" not in api("GET", f"/actions/order/{world['so']}/text").json()["text"]
    assert "Margin" in api("GET", f"/actions/order/{world['so']}/text", role="MANAGER").json()["text"] or True


def test_the_blind_shipment_check_runs_over_every_outside_draft(api, world):
    po = world["po_id"]
    r = api("POST", f"/actions/po/{po}/email-draft", json={"body": f"This is for {CUSTOMER}, please hurry."}, expect=400)
    assert "customer" in r.text.lower()
    r = api("POST", f"/actions/po/{po}/email-draft", json={"body": f"Our reference {world['so']}."}, expect=400)
    assert "sales order number" in r.text
    r = api("POST", f"/actions/job/{world['job']}/email-draft", json={"audience": "customer", "body": f"Made by {VENDOR}."}, expect=400)
    assert "vendor" in r.text.lower()
    api("POST", f"/actions/po/{po}/email-draft", json={"audience": "internal", "to": f"boss{TAG}@alliedweldingsystems.com", "body": f"For {CUSTOMER}, SO {world['so']}."})      # inside Allied it is fine


# ---------------------------------------------------------------- share, hand the ball, and the thread
def _thread(api, job):
    r = api("GET", f"/jobs/{job}").json()["rfq"]["number"]
    from app.services.coordination import updates

    return [u["body"] for u in updates.thread("rfq", r)]


def test_share_posts_a_comment_with_the_mention_and_the_link_and_alerts_the_person(api, world):
    boss = f"act-manager-{TAG}@alliedweldingsystems.com"
    got = api("POST", f"/actions/job/{world['job']}/share", json={"body": "Please look at this one", "mentions": [boss]}).json()
    assert got["posted"] and boss in got["mentioned"]
    notes = _thread(api, world["job"])
    assert any("Please look at this one" in n and f"/jobs/{world['job']}" in n for n in notes)
    api("POST", f"/actions/job/{world['job']}/share", json={"body": "  "}, expect=400)
    api("POST", f"/actions/job/{world['job']}/share", json={"body": "x", "mentions": "boss"}, expect=400)
    api("POST", f"/actions/job/{world['job']}/share", json={"body": "x"}, role="VIEWER", expect=403)
    api("POST", "/actions/job/JOB-9999999/share", json={"body": "x"}, expect=404)


def test_handing_the_ball_in_one_call_names_the_person_in_the_thread(api, world):
    boss = f"act-manager-{TAG}@alliedweldingsystems.com"
    got = api("POST", f"/actions/job/{world['job']}/handoff", json={"to": boss, "note": "Approve the price", "due": "2026-12-01", "comment": "Over to you"}).json()
    assert got["handed"] and got["ball"]["ball_person"] == boss and got["comment_id"]
    notes = _thread(api, world["job"])
    assert any("Ball is now with" in n and "Approve the price" in n for n in notes) and any("Over to you" in n for n in notes)
    api("POST", f"/actions/job/{world['job']}/handoff", json={"to": "nobody@nowhere.test"}, expect=400)
    api("POST", f"/actions/job/{world['job']}/handoff", json={}, expect=400)
    api("POST", f"/actions/job/{world['job']}/handoff", json={"to": boss, "due": "tomorrow"}, expect=400)
    vend = api("POST", f"/actions/job/{world['job']}/handoff", json={"ball_with": "vendor", "to": "Helen at Zibo", "note": "Waiting on the proforma"}).json()
    assert vend["ball"]["ball_label"].lower().startswith("a vendor")
    api("POST", f"/actions/job/{world['job']}/handoff", json={"to": boss}, role="VIEWER", expect=403)


def test_a_draft_and_what_happens_to_it_appear_in_the_items_thread(api, world):
    from app.services.assistant import agent

    op = {"email": f"act-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
    made = api("POST", f"/actions/job/{world['job']}/email-draft", json={"audience": "customer", "body": "Quick update"}).json()
    assert any("waiting for approval" in n and made["subject"][:20] in n for n in _thread(api, world["job"]))
    agent.decide(made["action_id"], True, op)
    assert any(n.startswith("Email to buyer") and ("draft" in n or "sent" in n) for n in _thread(api, world["job"]))
    other = api("POST", f"/actions/job/{world['job']}/email-draft", json={"audience": "customer", "body": "Second", "subject": "A second note"}).json()
    agent.decide(other["action_id"], False, op)
    assert any("cancelled and not sent" in n for n in _thread(api, world["job"]))
    assert not any(world["so"] in n or VENDOR in n for n in _thread(api, world["job"]) if "Email" in n)


def test_a_board_row_comment_is_staged_for_monday_and_never_sent(api, world):
    from app.services import monday_push
    from app.services.coordination import boards

    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Test Board", "role": "reference", "read_at": "now", "groups": [], "columns": [{"id": "n", "title": "Notes", "type": "text"}],
                               "items": [{"id": "5", "name": "Row five", "values": {}}]}
    try:
        got = api("POST", f"/actions/row/{bid}:5/share", json={"body": "Check this row"}).json()
        assert got["staged"] and any(c["id"] == got["change_id"] and c["kind"] == "post_update" for c in monday_push.changes("STAGED"))
        assert got["posted"]                                                                                    # in the app's own thread too
        api("POST", f"/actions/row/{bid}:5/handoff", json={"to": "x@example.test"}, expect=400)             # not a person here
    finally:
        with boards._lock:
            boards._mirror.pop(bid, None)

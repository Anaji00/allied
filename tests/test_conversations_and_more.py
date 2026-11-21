"""Conversations (threads with unread per person), the Sage export page in one call, what the app did on its own for a job, and people cells read by name on real and pending rows.
Temporary database and data folder only; nothing is sent."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import board_edit
from tests.conftest import staff_token
from tests.test_board_edit import OP, TODAY, _clean, board  # noqa: F401  (fixtures)

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"cv-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}
    heads["OPERATOR2"] = {"Authorization": "Bearer " + staff_token(f"cv-operator2-{TAG}@alliedweldingsystems.com", "OPERATOR")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


def _job(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Talk {TAG}{uuid.uuid4().hex[:3]}"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    return rfq["ref"], job["ref"]


def _mine(api, role, rfq):
    return next((t for t in api("GET", "/conversations", role=role).json()["items"] if t["item_ref"] == rfq), None)


def test_a_thread_gathers_the_item_with_unread_per_person_and_marking_it_read_clears_it(api):
    rfq, job = _job(api)
    for text in ("first thing", "second thing"):
        api("POST", f"/actions/job/{job}/share", json={"body": text})
    t = _mine(api, "OPERATOR2", rfq)
    assert t["item_type"] == "rfq" and t["job_ref"] == job and t["messages"] == 2 and t["unread"] == 2 and "second thing" in t["last_sentence"] and t["last_by"] and t["link"].endswith(rfq)
    assert _mine(api, "OPERATOR", rfq)["unread"] == 0                                                   # my own messages are never unread for me
    total = api("GET", "/conversations", role="OPERATOR2").json()
    assert total["unread_total"] >= 2 and total["unread_threads"] >= 1
    assert api("POST", f"/conversations/rfq/{rfq}/read", role="OPERATOR2").json()["read"] is True
    assert _mine(api, "OPERATOR2", rfq)["unread"] == 0
    time.sleep(1.1)
    api("POST", f"/actions/job/{job}/share", json={"body": "third thing"})
    t = _mine(api, "OPERATOR2", rfq)
    assert t["unread"] == 1 and "third thing" in t["last_sentence"]                                      # only what came after the read
    assert rfq in [x["item_ref"] for x in api("GET", "/conversations", params={"only_unread": True}, role="OPERATOR2").json()["items"]]
    assert api("POST", "/conversations/read-all", role="OPERATOR2").json()["threads_marked_read"] >= 1
    assert api("GET", "/conversations", role="OPERATOR2").json()["unread_total"] == 0
    api("POST", "/conversations/rfq/RFQ-9999-9999/read", expect=404)
    assert api("GET", "/conversations", role="VIEWER").status_code == 200


def test_a_thread_shows_who_it_waits_on_and_the_drafts_waiting_about_it(api):
    rfq, job = _job(api)
    api("POST", f"/actions/job/{job}/handoff", json={"ball_with": "vendor", "to": f"Helen at Zibo {TAG}", "note": "the proforma"})
    api("POST", f"/actions/job/{job}/email-draft", json={"audience": "internal", "to": f"cv-operator-{TAG}@alliedweldingsystems.com", "body": "Note to self"})
    t = _mine(api, "OPERATOR", rfq)
    assert t["waits_on"]["kind"] == "vendor" and "Helen" in t["waits_on"]["name"] and t["waits_on"]["note"] == "the proforma"
    assert t["drafts_waiting"] == 1 and t["messages"] >= 1
    assert _mine(api, "OPERATOR2", rfq)["drafts_waiting"] == 0                                           # a draft is only shown to the person who may decide it


def test_the_sage_export_page_is_one_call_in_plain_words(api):
    first = api("GET", "/sage/export/overview", role="MANAGER").json()
    assert {"sales_orders", "purchase_orders"} <= set(first["ready_to_send"]) and first["text"] and first["how"]["unmark"]["method"] == "POST"
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(customer_name=CUSTOMER, customer_po_number=f"9{uuid.uuid4().int % 10**6}", shipping_address="4837 Highway 81 South, Starr, SC 29684",
                                                                       line_items=[{"raw_item_text": "OC262064", "quantity": 1, "unit_price": 1385.0}]), actor_email="cv@alliedweldingsystems.com")
    so = str(out.sales_order_number)
    mid = api("GET", "/sage/export/overview", role="MANAGER").json()
    assert mid["ready_to_send"]["sales_orders"] == first["ready_to_send"]["sales_orders"] + 1
    api("GET", "/sage/export/sales-orders", params={"only_new": True, "mark_exported": True}, role="MANAGER")
    after = api("GET", "/sage/export/overview", role="MANAGER").json()
    assert after["ready_to_send"]["sales_orders"] < mid["ready_to_send"]["sales_orders"] and after["last_export"]["marked_sent"] and "sales orders" in after["last_export"]["what"] and any(u["sales_order_number"] == so and u["kind"] == "sales-orders" for u in after["unmark"])
    assert "No file has been made yet" not in after["text"] and "The last file was made" in after["text"]
    api("POST", "/sage/export/unmark", json={"kind": "sales-orders", "sales_order_numbers": [so]}, role="MANAGER")
    back = api("GET", "/sage/export/overview", role="MANAGER").json()
    assert back["ready_to_send"]["sales_orders"] == after["ready_to_send"]["sales_orders"] + 1 and back["history"][0]["what"] == "Orders were put back for the next file"
    api("GET", "/sage/export/overview", role="OPERATOR", expect=403)


def test_the_job_automation_log_lists_what_the_app_did_on_its_own(api):
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB, NotificationDB
    from app.services.coordination import notify

    rfq, job = _job(api)
    with SessionLocal() as db:
        db.add(AssistantActionDB(user_email="autopilot@alliedweldingsystems.com", user_role="AUTOPILOT", tool="send_email", summary=f"Ask Helen for their price on {rfq} (Owens)",
                                 args={"to": "h@vendor-example.test", "subject": "s", "body": "b", "rfq_number": rfq, "_play": "rfq_vendor_chaser"}))
        db.commit()
    notify.notify("test_job", f"job-{TAG}-{uuid.uuid4().hex[:4]}", f"Alert about {job} {TAG}", f"body for {job}", to=[f"cv-operator-{TAG}@alliedweldingsystems.com"])
    got = api("GET", f"/jobs/{job}/automation").json()
    srcs = {i["source"] for i in got["items"]}
    assert {"draft", "alert"} <= srcs and got["count"] == len(got["items"]) and job in got["text"]
    assert any(i["sentence"].startswith("The app drafted: Ask Helen") and "waiting for a person's OK" in i["sentence"] for i in got["items"])
    assert any(i["sentence"].startswith("The app raised an alert: Alert about") for i in got["items"])
    assert got["items"] == sorted(got["items"], key=lambda i: i["at"], reverse=True)
    other_rfq, other_job = _job(api)
    others = api("GET", f"/jobs/{other_job}/automation").json()["items"]
    assert not any(rfq in i["sentence"] or f"Alert about {job}" in i["sentence"] for i in others) and all(other_rfq in i["sentence"] or "waiting for your OK" in i["sentence"] or other_job in i["sentence"] for i in others)    # another job's drafts and alerts are not shown here
    api("GET", "/jobs/JOB-9999-9999/automation", expect=404)
    api("GET", f"/jobs/{job}/automation", role="VIEWER", expect=403)


def test_a_people_cell_is_read_by_name_or_email_on_real_and_pending_rows_and_unknown_or_two_matches_are_refused_with_the_candidates(board, _clean):
    a = board_edit.add_row(board, "People row", "this", {}, OP, TODAY)
    for item in ("1", a["item_id"]):
        got = board_edit.edit_cell(board, item, "Owner", "kim", OP, TODAY)
        assert got["becomes"] == {"personsAndTeams": [{"id": 13, "kind": "person"}]} and "kim.roe@allied.example.test" in got["understood"]
        assert board_edit.edit_cell(board, item, "Owner", "paul.wang@allied.example.test", OP, TODAY)["becomes"]["personsAndTeams"][0]["id"] == 11
        with pytest.raises(ValueError) as two:
            board_edit.edit_cell(board, item, "Owner", "paul", OP, TODAY)                                  # Paul Wang and Paula Jones
        assert "more than one person" in str(two.value) and "paul.wang@allied.example.test" in str(two.value) and "paula.jones@allied.example.test" in str(two.value)
        with pytest.raises(ValueError, match="matches no one"):
            board_edit.edit_cell(board, item, "Owner", "zed", OP, TODAY)
        with pytest.raises(ValueError):
            board_edit.edit_cell(board, item, "Owner", "k", OP, TODAY)                                     # one letter is not a person
    from app.services import monday_push

    created = next(c for c in monday_push.changes("STAGED") if c["kind"] == "create_item")
    assert created["payload"]["fields"]["who"]["personsAndTeams"][0]["id"] in (11, 13)                       # the pending row holds the person, in the same single create

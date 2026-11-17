"""The RFQ flow, start to finish: an RFQ email arrives -> the AI reads it -> a row for the Open RFQs board is queued for review
-> an acknowledgement draft is queued for approval -> ONE alert says the drafts are ready. monday stays read-only throughout:
nothing is sent to monday, to the customer or to any vendor. Fakes for mail, AI and monday."""

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, NotificationDB, RfqDB
from app.services import monday_client
from app.services.assistant import agent
from app.services.coordination import events, rfqs
from tests.test_e2e_allied_use_cases import REVIEWER, _alerts, _drafts, day  # noqa: F401  (the fake mailbox for a working day)


@pytest.fixture(autouse=True)
def _row_settings(monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", "1657738509")
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)


def _rfq_from_email(day, subject="RFQ - D cone liner, 2 pcs", body="Please quote 2 pcs D cone liner per attached drawing. Need by end of January."):
    mid = day["gmail"].add("Buyer <buyer@knauf-shasta.test>", subject, body)
    day["read_mail"]()
    with SessionLocal() as db:
        return db.query(RfqDB).filter(RfqDB.email_id == mid).one()


def test_an_rfq_email_queues_a_row_to_review_and_an_acknowledgement_and_one_alert(day):  # noqa: F811
    rfq = _rfq_from_email(day)
    rows = [d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == rfq.id]
    acks = [d for d in _drafts("rfq_acknowledge") if (d.args or {}).get("_rfq_id") == rfq.id]
    assert len(rows) == 1 and len(acks) == 1
    row = rows[0]
    assert row.tool == "add_rfq_to_monday" and row.status == "PENDING" and row.user_role == "AUTOPILOT"
    assert row.args["board_id"] == "1657738509" and "D cone liner" in row.args["item_name"]
    assert "Knauf" in row.args["customer"] or "knauf" in row.args["customer"].lower() or row.args["customer"]
    assert rfq.number in row.args["notes"] and "buyer@knauf-shasta.test" in row.args["notes"]
    # nothing has gone anywhere yet
    assert not day["sent"] or all("Received" not in s for s, _, _ in day["sent"])
    # exactly one "drafts are ready" alert covers both, and says how many wait
    drafts_alerts = [n for n in _alerts("autopilot-drafts:") if REVIEWER in n.recipients and rfq.number in n.body or "Open RFQs board" in n.body and REVIEWER in n.recipients]
    mine = [n for n in drafts_alerts if rfq.number in n.body or "D cone liner" in n.body]
    assert len(mine) == 1
    drafts_alerts = mine
    assert "Open RFQs board" in drafts_alerts[0].body and "received" in drafts_alerts[0].body.lower() or "Tell" in drafts_alerts[0].body
    assert drafts_alerts[0].link == "/inbox?show=approvals"


def test_the_row_can_be_corrected_in_the_queue_and_approving_it_stages_it_without_sending_anything(day, monkeypatch):  # noqa: F811
    from app.core.container import get_monday_client
    from app.services import monday_push

    rfq = _rfq_from_email(day)
    row = next(d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == rfq.id)
    boss = {"email": REVIEWER, "role": "MANAGER"}
    edited = agent.edit_action(row.id, {"item_name": "D cone liner x2 (Knauf Shasta)", "status": ""}, boss)
    assert edited["args"]["item_name"] == "D cone liner x2 (Knauf Shasta)"
    client = get_monday_client()
    monkeypatch.setattr(type(client), "live", property(lambda self: True))                     # connected, but read-only
    sent = []
    monkeypatch.setattr(monday_client.MondayClient, "create_item", lambda *a, **k: sent.append(a) or "999")
    out = agent.decide(row.id, True, boss)
    assert "Staged (not sent yet)" in str(out) and "read-only" in str(out) and sent == []
    staged = [c for c in monday_push.changes("STAGED") if c["payload"].get("rfq_id") == rfq.id]
    assert len(staged) == 1 and staged[0]["payload"]["item_name"] == "D cone liner x2 (Knauf Shasta)"
    assert rfqs.get(rfq.id).get("monday_item_id") in (None, "")
    assert monday_push.push()["blocked"] == "read-only" and sent == []                         # and pushing while read-only still sends nothing


def test_when_writing_is_on_in_auto_mode_the_reviewed_row_is_created_once_and_linked_to_the_rfq(day, monkeypatch):  # noqa: F811
    rfq = _rfq_from_email(day)
    row = next(d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == rfq.id)
    from app.core.container import get_monday_client

    client = get_monday_client()
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "auto")
    monkeypatch.setattr(type(client), "live", property(lambda self: True))
    calls = []
    monkeypatch.setattr(type(client), "create_item", lambda self, board, name, cols, idempotency_key=None: calls.append((board, name, cols, idempotency_key)) or "555")
    out = agent.decide(row.id, True, {"email": REVIEWER, "role": "MANAGER"})
    assert "Pushed 1 change" in str(out) and len(calls) == 1
    assert calls[0][0] == "1657738509" and "D cone liner" in calls[0][1]
    assert rfqs.get(rfq.id)["monday_item_id"] == "555"


def test_an_rfq_that_came_from_the_board_or_already_has_a_row_gets_no_second_row(day):  # noqa: F811
    r = rfqs.create({"customer": "Vulcan", "subject": "Chute liner", "contact_email": "v@vulcan.test", "monday_item_id": "777001"},
                    {"email": "system", "role": "SYSTEM"}, source="board", announce=False)
    events.handle("rfq.received", {"rfq": r})
    assert not [d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == r["id"]]


def test_the_same_email_read_twice_queues_one_row(day):  # noqa: F811
    rfq = _rfq_from_email(day)
    day["read_mail"]()
    assert len([d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == rfq.id]) == 1


def test_the_review_list_labels_the_row_so_the_screen_can_group_it(day):  # noqa: F811
    rfq = _rfq_from_email(day)
    row = next(d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == rfq.id)
    assert agent._kind(row) == "monday_row"


def test_no_translation_is_ever_added_to_a_customer_or_vendor_message(day, monkeypatch):  # noqa: F811
    """Translation is for the two in-house engineers; vendors (nearly all Chinese, some American) and customers get plain English."""
    from app.services import bridge

    asked = []
    monkeypatch.setattr(bridge, "_ask", lambda *a: asked.append(a) or (_ for _ in ()).throw(AssertionError("translated an outgoing message")))
    rfq = _rfq_from_email(day)
    ack = next(d for d in _drafts("rfq_acknowledge") if (d.args or {}).get("_rfq_id") == rfq.id)
    assert not any(ord(ch) > 0x2e80 for ch in ack.args["body"] + ack.args["subject"])           # no Chinese characters
    assert "[zh]" not in ack.args["body"] and "Mandarin" not in ack.args["body"]
    r = rfqs.get(rfq.id)
    r["vendor"] = "KINGCERA, Duke"
    events.handle("rfq.status_changed", {"rfq": r, "to": "WAITING_VENDOR"})
    for d in _drafts("rfq_ask_vendors"):
        assert not any(ord(ch) > 0x2e80 for ch in d.args.get("body", "")) and "Mandarin" not in d.args.get("body", "")
    assert asked == []

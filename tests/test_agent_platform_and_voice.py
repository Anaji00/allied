"""The full-platform agent and its voice: a multi-part task chained in one go, cards with buttons,
several changes as one plan, drafts edited before sending, monday push that stays blocked, and
voice commands with spoken confirmation. A scripted AI stands in for Groq; test database only."""

import json
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, OrderDB
from app.services.assistant import agent, tools, voice
from app.services.ai import providers

from tests.test_vendors_rfqs_assistant import ADMIN, OPERATOR, VIEWER, scripted  # noqa: F401
from tests.test_e2e_allied_use_cases import REVIEWER  # noqa: F401

MANAGER = {"email": "boss@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables and loads the catalogs."""


def _order(po="PO-316L", so=None, with_slip=True):
    so = so or str(70000 + uuid.uuid4().int % 9999)
    with SessionLocal() as db:
        db.add(OrderDB(order_id=uuid.uuid4().hex[:8].upper(), customer_po_number=po, sales_order_number=so, customer_id="10221",
                       customer_name="Owens Corning Insulating Systems Canada LP", status="STAGED",
                       purchase_orders={"KWS": "9277"}, line_items=[{"raw_item_text": "316L plate"}]))
        db.commit()
    if with_slip:
        folder = settings.OUTPUT_DIR / "packing_slips"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"packing_slip_SO_{so}.pdf").write_bytes(b"%PDF-1.4 test")
    return so


# ---------------------------------------------------------------------------
# The full registry, picked by topic
# ---------------------------------------------------------------------------


def test_every_part_of_the_platform_has_a_tool_and_roles_are_respected():
    names = set(tools.TOOLS)
    assert {"order_detail", "packing_slip", "mtr_lookup", "lead_targets", "exception_queue", "mail_search", "reconciliation_recent",
            "audit_recent", "notes_list", "recipes_list", "toggle_recipe", "add_starter_recipe", "save_email_draft",
            "create_vendor_job", "create_shipment", "update_shipment", "post_engineering_note", "resolve_exception",
            "sage_export_preview", "post_monday_update"} <= names
    viewer = tools.for_user(VIEWER)
    assert "audit_recent" not in viewer and "post_engineering_note" not in viewer and "create_shipment" not in viewer
    assert "audit_recent" in tools.for_user(MANAGER) and "post_engineering_note" in tools.for_user(MANAGER)
    assert tools.TOOLS["post_monday_update"].write and tools.TOOLS["save_email_draft"].write is False


def test_a_multi_part_request_is_offered_the_tools_for_each_part_but_not_all_of_them():
    picked = tools.for_message(OPERATOR, "Set up an RFQ for the 316L order, draft the vendor email, find the packing slip "
                                         "and explain why the MTR was flagged")
    assert {"log_rfq", "save_email_draft", "packing_slip", "mtr_lookup", "order_detail"} <= set(picked)
    assert len(picked) < len(tools.for_user(OPERATOR))                          # still not everything
    assert "toggle_recipe" not in picked and "audit_recent" not in picked
    plain = tools.for_message(OPERATOR, "what is late today")                   # an ordinary question: a small set
    assert len(plain) <= 14 and "mtr_lookup" not in plain
    assert "update_rfq" in tools.for_message(OPERATOR, "move it to pricing")    # a short follow-up still gets the likely tools


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


def test_an_order_lookup_hands_back_open_record_and_print_cards(scripted):
    so = _order()
    scripted(("order_detail", {"search": "PO-316L"}), "SO is staged; the slip is ready.")
    out = agent.chat([{"role": "user", "content": "Where is the 316L order and print its packing slip PO-316L"}], OPERATOR)
    card = next(c for c in out["cards"] if c["kind"] == "record" and c["record"]["id"] == so)
    labels = {b["label"]: b for b in card["buttons"]}
    assert labels["Open Record"]["href"] == f"/orders?so={so}"
    assert labels["Print Packing Slip"]["href"] == f"/api/v1/orders/{so}/packing-slip"


def test_a_saved_draft_sends_nothing_and_can_be_edited_then_sent(scripted, monkeypatch):
    from app.services.outbound_email_service import outbound_email_service

    sent = []
    monkeypatch.setattr(outbound_email_service, "send", lambda *a, **k: sent.append(a) or {"status": "SENT"})
    scripted(("save_email_draft", {"to": "sales@kws.test", "subject": "Photos please", "body": "Please send photos."}),
             "Draft saved.")
    out = agent.chat([{"role": "user", "content": "Draft an email to KWS asking for photos"}], OPERATOR)
    card = next(c for c in out["cards"] if c["kind"] == "draft")
    assert {b["label"] for b in card["buttons"]} == {"Edit & Send Draft", "Cancel"} and sent == []
    aid = card["action_id"]
    edited = agent.edit_action(aid, {"body": "Please send photos with dimensions.", "bogus": "x"}, OPERATOR)
    assert edited["args"]["body"].endswith("dimensions.") and "bogus" not in edited["args"]
    with pytest.raises(ValueError):
        agent.edit_action(aid, {"to": ""}, OPERATOR)                          # can't blank a required field
    with pytest.raises(LookupError):
        agent.edit_action(aid, {"body": "x"}, VIEWER)                         # someone else's draft
    assert agent.decide(aid, True, OPERATOR)["status"] == "DONE" and len(sent) == 1
    with pytest.raises(ValueError):
        agent.edit_action(aid, {"body": "late change"}, OPERATOR)             # already sent


# ---------------------------------------------------------------------------
# Several changes: one plan
# ---------------------------------------------------------------------------


def test_several_changes_in_one_task_form_a_plan_confirmed_together(scripted):
    ai = scripted(("create_shipment", {"reference": "PO 9277 augers", "mode": "SEA", "eta": "2026-12-01"}),
                  ("create_vendor_job", {"vendor": "KWS", "po_number": "9277", "description": "Augers"}),
                  ("create_vendor_job", {"vendor": "KWS", "po_number": "9277", "description": "Augers"}),   # a repeat: ignored
                  "Two changes are lined up.")
    out = agent.chat([{"role": "user", "content": "Add the KWS PO 9277 vendor order and track its shipment, ETA 1 Dec"}], OPERATOR)
    assert [a["tool"] for a in out["actions"]] == ["create_shipment", "create_vendor_job"]
    assert out["plan"]["count"] == 2 and out["plan"]["confirm_all"]
    kinds = [c["kind"] for c in out["cards"]]
    assert kinds.count("proposal") == 2
    assert "Already proposed" in json.dumps(ai.seen[-1][0])
    with SessionLocal() as db:
        assert db.query(AssistantActionDB).filter(AssistantActionDB.id.in_(out["plan"]["action_ids"]),
                                                  AssistantActionDB.status == "PENDING").count() == 2   # nothing done yet


def test_no_more_than_five_changes_are_queued_in_one_turn(scripted, monkeypatch):
    monkeypatch.setattr(settings, "ASSISTANT_MAX_PROPOSALS", 2)
    scripted(*[("create_shipment", {"reference": f"Box {i}"}) for i in range(4)], "Done queueing.")
    out = agent.chat([{"role": "user", "content": "track five shipments"}], OPERATOR)
    assert len(out["actions"]) == 2


# ---------------------------------------------------------------------------
# monday push stays blocked
# ---------------------------------------------------------------------------


def test_push_to_monday_is_explicit_confirmed_and_refused_while_read_only(scripted, monkeypatch):
    from app.core import container

    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    posted = []

    class Guarded:
        live, writable = True, False

        def post_pulse_update(self, *a, **k):
            posted.append(a)
            return False

    monkeypatch.setattr(container, "get_monday_client", lambda: Guarded())
    scripted(("post_monday_update", {"item_id": 123, "text": "Shipped"}), "Waiting for your OK to push.")
    out = agent.chat([{"role": "user", "content": "Push this status to monday: shipped"}], OPERATOR)
    card = next(c for c in out["cards"] if c["kind"] == "push")
    assert card["buttons"][0]["label"] == "Push to Monday" and posted == []          # nothing until clicked
    done = agent.decide(out["actions"][0]["id"], True, OPERATOR)
    assert "read-only" in done["result"]["done"] and "nothing was posted" in done["result"]["done"]


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("said, expected", [
    ("yes", "confirm"), ("Confirm", "confirm"), ("go ahead please", "confirm"), ("xác nhận", "confirm"), ("确认", "confirm"),
    ("no", "cancel"), ("cancel that", "cancel"), ("never mind", "cancel"), ("hủy", "cancel"), ("取消", "cancel"),
    ("print it now", "print"),
    ("yes and also set up a new RFQ for Hecla spool pieces with four flanges and a vendor email", None),
    ("what is late today", None),
])
def test_what_was_said_is_read_as_confirm_cancel_print_or_a_new_request(said, expected):
    assert voice.voice_intent(said) == expected


def test_the_spoken_reply_is_one_short_plain_sentence():
    long = "**Late today:**\n- KWS PO 9277: 9 days late, promised 22 Oct.\n- KINGCERA PO 9234: 31 days late. " + "More detail. " * 40
    said = voice.spoken(long, [])
    assert len(said.split()) <= settings.VOICE_REPLY_MAX_WORDS and "**" not in said and "- " not in said
    table = "Late vendor jobs\n\nJob | Vendor | Days late\n--- | --- | ---\n12 | KWS | 9\n13 | KINGCERA | 31"
    assert voice.spoken(table, []) == "Late vendor jobs The details are on screen."
    lined = voice.spoken("x", [{"summary": "Add shipment PO 9277 augers (SEA), ETA 2026-12-01"}, {"summary": "Add vendor order KWS"}])
    assert lined.startswith("I've lined up 2 changes") and "Say confirm" in lined


def test_a_spoken_task_is_written_out_done_and_answered_with_cards(client, scripted, monkeypatch):
    from app.services.ai import speech

    so = _order(po="PO-VOICE")
    monkeypatch.setattr(speech, "transcribe", lambda audio, suffix=".wav", language=None: {
        "text": "Find the packing slip for PO-VOICE", "language": "en", "duration_seconds": 3})
    scripted(("packing_slip", {"search": "PO-VOICE"}), "The packing slip for that order is ready to print.")
    r = client.post("/api/v1/assistant/voice-command", files={"file": ("cmd.webm", b"\x1aE\xdf\xa3" + b"\x00" * 800, "audio/webm")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["transcript"] == "Find the packing slip for PO-VOICE"
    assert out["spoken"] == {"mode": "browser", "text": out["spoken_text"], "lang": "en-US"} and out["audio_base64"] is None
    assert any(b["label"] == "Print Packing Slip" for c in out["action_cards"] for b in c["buttons"])
    assert out["tool_actions_executed"] and out["tool_actions_executed"][0]["tool"] == "packing_slip"
    assert so in json.dumps(out["action_cards"])


def test_saying_confirm_carries_out_the_waiting_changes_but_a_bare_yes_cannot_send_email_or_push_to_monday(client, scripted):
    ship = agent._propose(tools.TOOLS["create_shipment"], {"reference": "Voice box", "mode": "SEA"}, OPERATOR)
    mail = agent._propose(tools.TOOLS["send_email"], {"to": "v@v.test", "subject": "s", "body": "b"}, OPERATOR)
    push = agent._propose(tools.TOOLS["post_monday_update"], {"item_id": 1, "text": "x"}, OPERATOR)
    ids = [ship["id"], mail["id"], push["id"]]
    spoken_yes = voice.command(OPERATOR, transcript="yes", confirm_ids=ids)
    assert [d["tool"] for d in spoken_yes["held"]] == ["send_email", "post_monday_update"]
    assert "send it" in spoken_yes["reply_text"] and "push to monday" in spoken_yes["reply_text"]
    assert [d["status"] for d in spoken_yes["tool_actions_executed"]] == ["DONE"]          # only the harmless one
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, mail["id"]).status == "PENDING" and db.get(AssistantActionDB, push["id"]).status == "PENDING"
    both = voice.command(OPERATOR, transcript="send it and confirm push to monday", confirm_ids=[mail["id"], push["id"]])
    assert both["held"] == []
    cancelled = voice.command(OPERATOR, transcript="cancel", confirm_ids=[agent._propose(tools.TOOLS["create_shipment"], {"reference": "x"}, OPERATOR)["id"]])
    assert cancelled["tool_actions_executed"][0]["status"] == "CANCELLED"


def test_voice_commands_refuse_other_peoples_changes_empty_audio_and_the_off_switch(client, monkeypatch):
    theirs = agent._propose(tools.TOOLS["create_shipment"], {"reference": "Not yours"}, OPERATOR)
    out = voice.command(VIEWER, transcript="confirm", confirm_ids=[theirs["id"]])
    assert out["tool_actions_executed"][0]["status"] == "SKIPPED"
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, theirs["id"]).status == "PENDING"
    assert client.post("/api/v1/assistant/voice-command", data={"transcript": "  "}).status_code == 400
    assert client.post("/api/v1/assistant/voice-command", files={"file": ("x.exe", b"MZ" + b"\x00" * 50, "application/octet-stream")}).status_code == 400
    monkeypatch.setattr(settings, "VOICE_AGENT_ENABLED", False)
    assert client.post("/api/v1/assistant/voice-command", data={"transcript": "hello"}).status_code == 503


def test_the_edit_endpoint_and_a_dictated_command_work_over_the_api(client, scripted):
    scripted(("save_email_draft", {"to": "sales@kws.test", "subject": "Photos", "body": "Please."}), "Saved.")
    out = client.post("/api/v1/assistant/voice-command", data={"transcript": "Draft an email to KWS asking for photos"}).json()
    aid = next(c["action_id"] for c in out["action_cards"] if c["kind"] == "draft")
    r = client.patch(f"/api/v1/assistant/actions/{aid}", json={"changes": {"subject": "Photos before crating"}})
    assert r.status_code == 200 and r.json()["args"]["subject"] == "Photos before crating"
    assert client.patch(f"/api/v1/assistant/actions/{aid}", json={"changes": {"to": ""}}).status_code == 400


# ---------------------------------------------------------------------------
# Streaming: step lines while it works, then the reply and cards
# ---------------------------------------------------------------------------


def _events(response):
    out = []
    for block in response.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def test_the_stream_sends_step_lines_then_the_whole_reply_with_its_cards(client, scripted):
    so = _order(po="PO-STREAM")
    scripted(("order_detail", {"search": "PO-STREAM"}), ("save_email_draft", {"to": "s@kws.test", "subject": "Hi", "body": "Photos?"}),
             "Found the order and saved a draft.")
    r = client.post("/api/v1/assistant/chat/stream", json={"messages": [
        {"role": "user", "content": "Find the order PO-STREAM and draft an email to KWS"}]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"].startswith("no-cache") and r.headers["x-accel-buffering"] == "no"
    events = _events(r)
    steps = [e["text"] for e in events if e["type"] == "step"]
    assert steps[0] == "Reading your request…" and "Opening the order…" in steps and "Writing the draft…" in steps
    assert all("order_detail" not in t and "save_email_draft" not in t for t in steps)       # plain words, no tool names
    done = events[-1]
    assert done["type"] == "done" and done["reply"].startswith("Found the order")
    assert any(c["kind"] == "record" and c["record"]["id"] == so for c in done["cards"]) and any(c["kind"] == "draft" for c in done["cards"])
    assert [e["type"] for e in events].count("done") == 1


def test_the_stream_says_what_went_wrong_in_plain_words(client):
    providers.use_provider(None)                          # no AI set up in tests
    events = _events(client.post("/api/v1/assistant/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}))
    assert events[0]["type"] == "step" and events[-1]["type"] == "error"
    assert "isn't available" in events[-1]["message"] and "Traceback" not in events[-1]["message"]


def test_the_stream_needs_a_signed_in_person_and_the_plain_chat_still_works(client, scripted, auth_enforced):
    from tests.conftest import staff_token

    assert client.post("/api/v1/assistant/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401
    token = staff_token(f"v{uuid.uuid4().hex[:4]}@alliedweldingsystems.com", "VIEWER")
    scripted("Hello.")
    plain = client.post("/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                        headers={"Authorization": f"Bearer {token}"})
    assert plain.status_code == 200 and plain.json()["reply"] == "Hello." and "cards" in plain.json()

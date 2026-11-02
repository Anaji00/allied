"""Each board's automations in monday's words, the drafts each item waits on, and drafting one now.

Nothing here writes to monday or sends anything: drafts wait for a person."""

import uuid
from datetime import date, timedelta

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, VendorJobDB
from app.services.assistant import agent
from app.services.coordination import autopilot, board_automations, boards, vendors

TODAY = date.today()
OWNER = {"email": "buyer.boards@allied.test", "role": "OPERATOR"}
VIEWER = {"email": "someone.else@allied.test", "role": "VIEWER"}


@pytest.fixture
def board(monkeypatch):
    """A vendor board copy with one late order that has an email on file, and one that doesn't."""
    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_chaser=suggest")
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "owner")
    tag = uuid.uuid4().hex[:6]
    board_id = str(uuid.uuid4().int % 10**9)
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"ZZ{tag}=sales@zz{tag}.test")
    late = vendors.create({"vendor": f"ZZ{tag}", "description": "AR400 liners", "po_number": f"P{tag}",
                           "ordered_on": (TODAY - timedelta(days=40)).isoformat(), "quoted_lead_days": 20}, OWNER)
    nomail = vendors.create({"vendor": f"NOMAIL{tag}", "description": "Chute",
                             "ordered_on": (TODAY - timedelta(days=40)).isoformat(), "quoted_lead_days": 20}, OWNER)
    items = {late["id"]: f"9{tag}1", nomail["id"]: f"9{tag}2"}
    with SessionLocal() as db:
        for job_id, item in items.items():
            row = db.get(VendorJobDB, job_id)
            row.monday_item_id, row.board_id = item, board_id
        db.commit()
    with boards._lock:
        boards._mirror[board_id] = {"board_id": board_id, "name": "2026 Orders", "role": "vendors", "read_at": None,
                                    "columns": [], "groups": [], "items": []}
    yield {"board": board_id, "late": late, "item": items[late["id"]], "nomail_item": items[nomail["id"]]}
    with boards._lock:
        boards._mirror.pop(board_id, None)


def test_recipes_follow_the_autopilot_modes(board, monkeypatch):
    out = board_automations.for_board(board["board"], OWNER)
    names = {r["name"]: r for r in out["recipes"]}
    assert names["vendor_chaser"]["mode"] == "suggest" and names["vendor_chaser"]["can_preview"]
    assert names["order_shipped"]["kind"] == "reaction" and not names["order_shipped"]["can_preview"]
    assert names["vendor_late_chase"]["when"].startswith("When")

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", False)
    out = board_automations.for_board(board["board"], OWNER)
    assert all(r["mode"] == "off" for r in out["recipes"] if r["kind"] != "check")


def test_due_draft_is_listed_drafted_on_request_and_then_shown_against_its_item(board):
    out = board_automations.for_board(board["board"], OWNER)
    due = out["due"][board["item"]]
    assert len(due) == 1 and due[0]["play"] == "vendor_chaser"
    manual = out["due"][board["nomail_item"]]               # no email on file: a message to send by hand
    assert len(manual) == 1 and manual[0]["manual"] and manual[0]["key"] is None and "ship date" in manual[0]["body"]
    assert out["drafts"] == {} and out["waiting"] == 0

    did = board_automations.draft_now(board["board"], board["item"], due[0]["key"])
    assert did["did"][0]["outcome"] == "drafted"
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, did["action_id"])
        assert row.status == "PENDING" and row.user_role == "AUTOPILOT"   # waits for a person; nothing sent

    out = board_automations.for_board(board["board"], OWNER)
    assert board["item"] not in out["due"]                 # drafted, so no longer due
    draft = out["drafts"][board["item"]][0]
    assert draft["status"] == "PENDING" and draft["yours"] and "ship date" in draft["subject"]
    assert out["waiting"] == 1
    assert not board_automations.for_board(board["board"], VIEWER)["drafts"][board["item"]][0]["yours"]

    # The timer won't make it twice, and deciding it on the board is the assistant's usual decide.
    autopilot.run("vendor_chaser")
    with SessionLocal() as db:
        assert db.query(AssistantActionDB).filter(AssistantActionDB.id == did["action_id"]).count() == 1
    assert agent.decide(draft["id"], False, OWNER)["status"] == "CANCELLED"
    assert board_automations.for_board(board["board"], OWNER)["drafts"][board["item"]][0]["status"] == "CANCELLED"


def test_draft_now_refuses_a_key_from_another_item(board):
    key = board_automations.for_board(board["board"], OWNER)["due"][board["item"]][0]["key"]
    with pytest.raises(LookupError):
        board_automations.draft_now(board["board"], board["nomail_item"], key)
    with pytest.raises(LookupError):
        board_automations.draft_now(board["board"], board["item"], "vendor-chase:999999:2026w1")


def test_unknown_board_is_not_found():
    with pytest.raises(LookupError):
        board_automations.for_board("does-not-exist", OWNER)


def test_routes(board, client, auth_enforced):
    from tests.conftest import staff_token

    as_ = lambda email, role: {"Authorization": f"Bearer {staff_token(email, role)}"}  # noqa: E731
    r = client.get(f"/api/v1/boards/{board['board']}/automations", headers=as_(OWNER["email"], "OPERATOR"))
    assert r.status_code == 200, r.text
    key = r.json()["due"][board["item"]][0]["key"]
    url = f"/api/v1/boards/{board['board']}/items/{board['item']}/drafts"
    assert client.post(url, json={"key": key}, headers=as_(VIEWER["email"], "VIEWER")).status_code == 403
    assert client.post(url, json={"key": key}, headers=as_(OWNER["email"], "OPERATOR")).status_code == 200


def test_rfq_waiting_on_a_vendor_can_have_its_price_request_drafted_from_the_board(monkeypatch):
    from app.models.db_models import RfqDB
    from app.services.coordination import rfqs

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"Duke{tag}=duke@d{tag}.test")
    board_id, item = str(uuid.uuid4().int % 10**9), f"8{tag}"
    r = rfqs.create({"customer": "OC-Irving", "subject": "Bottom hopper", "vendor": f"Duke{tag}",
                     "monday_item_id": item}, OWNER, source="monday", announce=False)
    with SessionLocal() as db:
        db.get(RfqDB, r["id"]).status = "WAITING_VENDOR"
        db.commit()
    with boards._lock:
        boards._mirror[board_id] = {"board_id": board_id, "name": "Open RFQs", "role": "rfqs", "read_at": None,
                                    "columns": [], "groups": [], "items": []}
    try:
        due = board_automations.for_board(board_id, OWNER)["due"][item]
        ask = next(d for d in due if d["play"] == "rfq_ask_vendors")
        assert ask["key"] == f"rfq-price-ask:{r['id']}:duke{tag}"      # the key the event itself uses
        assert board_automations.draft_now(board_id, item, ask["key"])["did"][0]["outcome"] == "drafted"
        out = board_automations.for_board(board_id, OWNER)
        assert item not in out["due"] and out["drafts"][item][0]["to"] == f"duke@d{tag}.test"
    finally:
        with boards._lock:
            boards._mirror.pop(board_id, None)


def test_peoples_own_rules_show_on_their_board_and_their_drafts_on_the_row(monkeypatch):
    from app.services.coordination import recipes

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    tag = uuid.uuid4().hex[:6]
    board_id, item, other = str(uuid.uuid4().int % 10**9), f"7{tag}1", f"7{tag}2"
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", board_id)
    soon, later = (TODAY + timedelta(days=2)).isoformat(), (TODAY + timedelta(days=40)).isoformat()
    with boards._lock:
        boards._mirror[board_id] = {
            "board_id": board_id, "name": "2026 Orders", "role": "vendors", "read_at": None,
            "columns": [{"id": "date4", "title": f"Ship {tag}", "type": "date"}], "groups": [],
            "items": [{"id": item, "name": "Liners", "group": None, "values": {"date4": soon}},
                      {"id": other, "name": "Chute", "group": None, "values": {"date4": later}}]}
    rule = recipes.create({"name": f"Ship soon {tag}", "board_role": "vendors", "trigger": "date_within", "column": f"Ship {tag}",
                           "value": "5", "action": "draft_email", "email_to": f"forwarder@f{tag}.test", "mode": "suggest"}, OWNER)
    try:
        out = board_automations.for_board(board_id, OWNER)
        assert any(r["id"] == rule["id"] and r["description"] for r in out["rules"])
        due = [d for d in out["due"].get(item, []) if d["play"] == f"recipe_{rule['id']}"]
        assert len(due) == 1 and due[0]["rule"] == rule["name"]
        assert not any(d["play"] == f"recipe_{rule['id']}" for d in out["due"].get(other, []))   # 40 days away

        assert board_automations.draft_now(board_id, item, due[0]["key"])["did"][0]["outcome"] == "drafted"
        with pytest.raises(LookupError):
            board_automations.draft_now(board_id, other, due[0]["key"])                          # not that row's
        out = board_automations.for_board(board_id, OWNER)
        assert out["drafts"][item][0]["to"] == f"forwarder@f{tag}.test"
        assert not any(d["play"] == f"recipe_{rule['id']}" for d in out["due"].get(item, []))

        # A status recipe's draft is found by the item it names.
        with SessionLocal() as db:
            db.add(AssistantActionDB(id=uuid.uuid4().hex, user_email=OWNER["email"], user_role="AUTOPILOT", tool="send_email",
                                     args={"to": "a@b.test", "subject": "s", "body": "b", "_play": f"recipe_{rule['id']}", "_item": other},
                                     summary="status draft", status="PENDING"))
            db.commit()
        assert board_automations.for_board(board_id, OWNER)["drafts"][other][0]["summary"] == "status draft"
    finally:
        recipes.delete(rule["id"])
        with boards._lock:
            boards._mirror.pop(board_id, None)


def test_after_the_first_follow_up_staff_follow_up_again_from_the_row(board):
    out = board_automations.for_board(board["board"], OWNER)
    f = out["follow_ups"][board["item"]]
    assert f["due"] and f["now_key"] == f"vendor-followup:{board['late']['id']}:now"
    assert out["follow_ups"][board["nomail_item"]]["now_key"] is None           # no email on file: nothing to draft
    first = out["due"][board["item"]][0]["key"]
    board_automations.draft_now(board["board"], board["item"], first)

    after = board_automations.for_board(board["board"], OWNER)
    assert board["item"] not in after["due"]                                        # no automatic repeats
    assert after["follow_ups"][board["item"]]["started"] and not after["follow_ups"][board["item"]]["due"]
    now = after["follow_ups"][board["item"]]["now_key"]
    assert board_automations.draft_now(board["board"], board["item"], now)["did"][0]["outcome"] == "drafted"
    assert board_automations.draft_now(board["board"], board["item"], now)["did"][0]["outcome"] == "already"  # one a day
    assert len(board_automations.for_board(board["board"], OWNER)["drafts"][board["item"]]) == 2

"""Round 3: when things go wrong part-way.

monday refuses or errors, a board comes back empty, the mailbox drops mid-read, the AI
fails, Gmail refuses a send, a check or a reaction crashes. The properties: monday is
never written to, nothing real is lost or deleted, nothing is done twice, the rest of the
run carries on, and a person can see and retry what failed. Temporary database and fakes."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, MailLogDB, NotificationDB, VendorJobDB
from app.services import mailbox_poller as mp
from app.services.ai import features
from app.services.coordination import autopilot, changes, events, rfqs, schedule, vendors
from app.services.monday_client import MondayClient, MondayReadOnlyError, is_mutation
from app.services.outbound_email_service import outbound_email_service

from tests.test_e2e_allied_use_cases import REVIEWER, FakeGmail, _sort, day  # noqa: F401

TODAY = date.today()


# ---------------------------------------------------------------------------
# monday stays read only, whatever asks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", [
    "mutation { create_item (board_id: 1, item_name: \"x\") { id } }",
    "   \n\tmutation{delete_item(item_id: 1){id}}",
    "# a comment\nmutation Foo { archive_board(board_id: 1) { id } }",
    "MUTATION { change_column_value(board_id:1,item_id:1,column_id:\"status\",value:\"{}\") { id } }",
    "query { me { id } } mutation { create_update(item_id: 1, body: \"x\") { id } }",
    "subscription { items { id } }",
    "{ create_item }  ",
    "",
])
def test_every_kind_of_write_is_refused_before_it_leaves(monkeypatch, doc):
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "MONDAY_API_KEY", "test-key", raising=False)
    sent = []
    client = MondayClient(api_key="test-key")
    assert client.live                     # a live client: the guard is all that stands in the way
    monkeypatch.setattr(client, "_http", lambda: type("H", (), {"post": lambda *a, **k: sent.append(k) or None})())
    if doc.strip().startswith("{ create_item }"):
        assert not is_mutation(doc)        # a bare selection is a read; monday would reject the field anyway
        return
    out = client.execute_query(doc)
    assert sent == []                      # nothing went over the wire
    assert out["data"]["read_only"] and client.simulated_calls[-1]["blocked"] == "read-only"   # and it is on record
    with pytest.raises(MondayReadOnlyError):
        client._post_once({"query": doc})  # the last line of defence refuses outright


def test_a_search_for_the_word_mutation_is_still_a_read():
    assert not is_mutation('query { items_page_by_column_values(board_id: 1, columns: [{column_id: "text", column_values: ["mutation"]}]) { items { id } } }')


def test_app_actions_that_would_write_to_monday_are_refused_quietly_and_kept_in_the_app(monkeypatch):
    """A person records a new ship date on a board order: it is kept in the app; the write-back
    to monday is refused by the guard, and the person sees no error."""
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "VENDOR_PROMISED_COLUMN_ID", "date_promise")
    tried = []
    from app.core import container

    class Guarded:
        def update_column_values(self, *a, **k):
            tried.append(a)
            raise MondayReadOnlyError("monday is read-only")

    monkeypatch.setattr(container, "get_monday_client", lambda: Guarded())
    job = vendors.create({"vendor": "KWS", "po_number": "9277", "ordered_on": (TODAY - timedelta(days=30)).isoformat()},
                         {"email": REVIEWER})
    with SessionLocal() as db:
        row = db.get(VendorJobDB, job["id"])
        row.monday_item_id, row.board_id = "18394243717001", "18394243717"
        db.commit()
    out = vendors.update(job["id"], {"promised_on": (TODAY + timedelta(days=9)).isoformat()}, {"email": REVIEWER})
    assert out["promised_on"] == (TODAY + timedelta(days=9)).isoformat() and len(tried) == 1
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


# ---------------------------------------------------------------------------
# monday misbehaving
# ---------------------------------------------------------------------------


def test_a_board_that_comes_back_empty_deletes_nothing(monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", "18394243717")
    job = vendors.create({"vendor": "KINGCERA", "po_number": "9264"}, {"email": REVIEWER})
    with SessionLocal() as db:
        row = db.get(VendorJobDB, job["id"])
        row.monday_item_id, row.board_id, row.source = "item-9264", "18394243717", "monday"
        db.commit()

    class EmptyBoard:
        live, writable = True, False

        def execute_query(self, query, variables=None, idempotency_key=None):
            if "groups" in query or "columns" in query:
                return {"data": {"boards": [{"id": "18394243717", "name": "2026 Orders", "columns": [], "groups": []}]}}
            return {"data": {"boards": [{"items_page": {"cursor": None, "items": []}}]}}

    with pytest.raises(RuntimeError, match="nothing was removed"):
        vendors.sync_vendor_board(EmptyBoard())
    with SessionLocal() as db:
        assert db.get(VendorJobDB, job["id"]) is not None
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


def test_one_board_failing_in_the_change_check_doesnt_stop_the_others(monkeypatch):
    read = []
    monkeypatch.setattr(changes, "watched", lambda: {"A": lambda: (_ for _ in ()).throw(TimeoutError("monday slow")),
                                                     "B": lambda: read.append("B") or {"jobs": 3}})
    monkeypatch.setattr(changes, "changed", lambda client=None: ["A", "B"])
    out = changes.check()
    assert "error" in out["A"] and out["B"] == {"jobs": 3} and read == ["B"]


def test_the_worker_survives_monday_being_down_and_carries_on(monkeypatch):
    worker = schedule.CoordinationWorker()

    def down():
        raise ConnectionError("api.monday.com unreachable")

    worker._step("changes", down)
    worker._step("checks", lambda: {"ok": 1})
    assert "error" in worker.last["changes"] and worker.last["checks"]["result"] == {"ok": 1}


# ---------------------------------------------------------------------------
# Mail going wrong
# ---------------------------------------------------------------------------


def test_the_ai_failing_any_way_falls_back_to_the_plain_reader_not_a_failed_email(day, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("Groq returned something unparseable")

    monkeypatch.setattr(features, "triage_email", broken)
    mid = day["gmail"].add("buyer@knauf.test", "RFQ - chute liners", "Please quote 6 liners")
    day["read_mail"]()
    with SessionLocal() as db:
        log = db.query(MailLogDB).filter(MailLogDB.message_id == mid).one()
    assert log.outcome != "FAILED" and log.category == "quote_request"


def test_the_mailbox_dropping_mid_read_loses_nothing_and_repeats_nothing(day):
    ids = [day["gmail"].add(f"b{i}@cust.test", f"RFQ {i} hopper", "Please quote") for i in range(6)]
    real_fetch = day["gmail"].fetch
    calls = {"n": 0}

    def flaky(nums, spec):
        if "HEADER" not in spec:
            calls["n"] += 1
            if calls["n"] == 4:
                raise ConnectionResetError("imap.gmail.com closed the connection")
        return real_fetch(nums, spec)

    day["gmail"].fetch = flaky
    with pytest.raises(ConnectionResetError):
        day["read_mail"]()                      # the worker's loop catches this and tries again later
    day["read_mail"]()
    with SessionLocal() as db:
        logged = [m for (m,) in db.query(MailLogDB.message_id).filter(MailLogDB.message_id.in_(ids))]
    assert sorted(logged) == sorted(ids) and len(logged) == len(set(logged))


def test_gmail_refusing_a_send_leaves_the_draft_to_retry_and_nothing_marked_done(day, monkeypatch):
    from app.services.assistant import agent

    def refuse(msg, to):
        raise OSError("421 4.7.0 Try again later")

    monkeypatch.setattr(outbound_email_service, "_deliver", refuse)
    r = rfqs.create({"customer": "Vulcan", "subject": "Chute", "contact_email": f"v{uuid.uuid4().hex[:5]}@vulcan.test"},
                    {"email": "intake"}, source="email")
    [ack] = [a for a in agent.pending({"email": REVIEWER, "role": "MANAGER"}) if r["number"] in a["summary"]]
    assert agent.decide(ack["id"], True, {"email": REVIEWER, "role": "MANAGER"})["status"] == "FAILED"
    assert rfqs.get(r["id"])["status"] == "NEW"                       # the customer heard nothing
    # Gmail is back. The next hourly run offers the draft again; a person sends it.
    monkeypatch.setattr(outbound_email_service, "_deliver", lambda msg, to: day["sent"].append(msg["Subject"]))
    events.handle("rfq.received", {"rfq": rfqs.get(r["id"])})
    again = [a for a in agent.pending({"email": REVIEWER, "role": "MANAGER"}) if a["id"] == ack["id"]]
    assert again, "a draft whose send failed must come back for a person to retry"
    assert agent.decide(ack["id"], True, {"email": REVIEWER, "role": "MANAGER"})["status"] == "DONE"
    assert rfqs.get(r["id"])["status"] == "ACKNOWLEDGED" and len(day["sent"]) == 1


# ---------------------------------------------------------------------------
# A check or reaction crashing part-way
# ---------------------------------------------------------------------------


def test_the_vendor_checks_crashing_doesnt_stop_the_rfq_checks(monkeypatch):
    ran = []
    monkeypatch.setattr(vendors, "run_checks", lambda on=None: (_ for _ in ()).throw(KeyError("promised_on")))
    monkeypatch.setattr(rfqs, "run_checks", lambda: ran.append("rfqs") or {"rfq_ack": 0})
    out = schedule.run_checks()
    assert ran == ["rfqs"] and "rfq_ack" in out


def test_one_reaction_crashing_doesnt_stop_the_others_or_the_recipes(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTOPILOT_EVENTS_ASYNC", False)
    from app.services.coordination import recipes

    recipe_ran = []
    monkeypatch.setattr(recipes, "safe_on_event", lambda e, p, dry_run=False: recipe_ran.append(e) or [])
    broken = events.REACTIONS["order_photos"]
    monkeypatch.setitem(events.REACTIONS, "order_photos", events.Reaction(broken.event, lambda e: 1 / 0, "auto", broken.about))
    job = {"id": 1, "vendor": "KWS", "description": "Augers", "po_number": "9277", "customer_due": None,
           "owner_email": REVIEWER, "mode": "SEA"}
    entry = events.handle("order.status_changed", {"job": job, "from": "FABRICATION", "to": "SHIPPED"})
    names = {r["name"]: r for r in entry["reactions"]}
    assert "error" not in names.get("order_shipped", {}) and recipe_ran == ["order.status_changed"]


def test_a_recipe_with_a_column_that_no_longer_exists_is_skipped_not_fatal(monkeypatch):
    from app.services.coordination import boards, recipes

    board = "77"
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "Engineering", "role": "projects", "read_at": "now",
                                 "groups": [], "columns": [{"id": "status", "title": "Status", "type": "status", "labels": {}}],
                                 "items": [{"id": "1", "name": "Splitter", "values": {"status": "Scanned"}}]}
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", board)
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    recipes.create({"name": "Gone column", "board_role": "projects", "trigger": "date_within",
                    "column": "Estimate finish", "value": "3", "action": "notify"}, {"email": REVIEWER})
    assert recipes.run_dates(dry_run=True) == []                      # renamed column on monday: nothing, no crash
    with SessionLocal() as db:
        from app.models.db_models import AutomationRecipeDB

        db.query(AutomationRecipeDB).delete()
        db.commit()

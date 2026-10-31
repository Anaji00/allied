"""Autopilot: plays run on their own, drafts wait for a person, nothing is written to monday."""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, NotificationDB, RfqDB
from app.services.assistant import agent
from app.services.coordination import autopilot, projects, rfqs, vendors, workdays

TODAY = date.today()
OWNER = {"email": "buyer.auto@allied.test", "role": "OPERATOR"}


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


class FakeMonday:
    """Answers every board read with the items given; records any write attempt."""

    live = True
    writable = False

    def __init__(self, items):
        self.items, self.writes = items, []

    def execute_query(self, query, variables=None, idempotency_key=None):
        assert not query.lstrip().startswith("mutation")
        return {"data": {"boards": [{"items_page": {"cursor": None, "items": self.items}}]}}

    def update_column_values(self, *a, **kw):
        self.writes.append(a)
        return True


@pytest.fixture
def only(monkeypatch):
    """Runs just the named plays, in the given modes."""
    def set_modes(**m):
        monkeypatch.setattr(settings, "AUTOPILOT_MODES", ",".join(f"{k}={v}" for k, v in m.items())
                            + "," + ",".join(f"{k}=off" for k in autopilot.PLAYS if k not in m))
    return set_modes


def _drafts(play):
    with SessionLocal() as db:
        return [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT")
                if (r.args or {}).get("_play") == play]


def test_modes_come_from_settings_with_safe_defaults(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_chaser=auto,rfq_board=nonsense,unknown=auto")
    m = autopilot.modes()
    assert m["vendor_chaser"] == "auto"
    assert m["rfq_board"] == "auto" and m["quote_followup"] == "suggest"   # defaults where not (validly) set
    assert "unknown" not in m


def test_open_rfqs_board_is_read_into_the_tracker(monkeypatch, only):
    only(rfq_board="auto")
    item = str(uuid.uuid4().int % 10**9)
    t = lambda x: {"title": x}  # noqa: E731
    board_item = {"id": item, "name": "Bottom Hopper", "created_at": "2026-08-20T15:00:00Z", "group": {"title": "Open RFQs"},
                  "column_values": [
                      {"id": "dropdown", "text": "OC-Irving", "value": None, "column": t("Customer")},
                      {"id": "dropdown5", "text": "Duke, Grace", "value": None, "column": t("Bidding vendors")},
                      {"id": "date_14", "text": "2026-09-01", "value": json.dumps({"date": "2026-09-01"}), "column": t("Last follow up")},
                      {"id": "status", "text": "Waiting on vendor quote", "value": None, "column": t("Status")}]}
    fake = FakeMonday([board_item])
    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", "1657738509")
    monkeypatch.setattr(projects, "_client", lambda: fake)

    preview = autopilot.run("rfq_board", dry_run=True)
    assert preview["plays"]["rfq_board"]["did"][0]["outcome"] == "would do"
    with SessionLocal() as db:
        assert db.query(RfqDB).filter(RfqDB.monday_item_id == item).first() is None   # dry run changed nothing

    autopilot.run("rfq_board")
    with SessionLocal() as db:
        r = db.query(RfqDB).filter(RfqDB.monday_item_id == item).one()
        assert (r.customer, r.subject, r.status, r.vendor) == ("OC-Irving", "Bottom Hopper", "WAITING_VENDOR", "Duke, Grace")
        assert r.acknowledged_at is not None and r.source == "monday"
        # Connecting the board is not "months overdue": its clocks start when it is first read.
        assert workdays.as_utc(r.waiting_since).date() == workdays.now().date()
        assert workdays.as_utc(r.due_at) > workdays.now()
        assert workdays.as_utc(r.received_at).date().isoformat() == "2026-08-20"
        rid = r.id
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key == f"rfq-new:{rid}:").count() == 0  # not announced as new
    rfqs.run_checks()
    with SessionLocal() as db:
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"rfq-overdue:{rid}:%")).count() == 0
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"rfq-vendor:{rid}:%")).count() == 0

    assert autopilot.run("rfq_board")["plays"]["rfq_board"]["did"] == []     # nothing new on the board

    # A change made in the app stands until the board itself changes.
    rfqs.update(rid, {"status": "CLARIFYING"}, {"email": "sales@allied.test", "role": "OPERATOR"})
    assert autopilot.run("rfq_board")["plays"]["rfq_board"]["did"] == []
    assert rfqs.get(rid)["status"] == "CLARIFYING"

    board_item["column_values"][3]["text"] = "Need quote checked"
    autopilot.run("rfq_board")
    now = rfqs.get(rid)
    assert now["status"] == "PRICING" and now["paused_at"] is None           # the pause ended properly

    # A newer follow-up on the board restarts the vendor clock; an item that leaves the board is closed.
    board_item["column_values"][3]["text"] = "Waiting on vendor quote"
    autopilot.run("rfq_board")
    later = (workdays.now() + timedelta(days=1)).date().isoformat()
    board_item["column_values"][2].update(text=later, value=json.dumps({"date": later}))
    autopilot.run("rfq_board")
    assert workdays.as_utc(datetime.fromisoformat(rfqs.get(rid)["waiting_since"])).date().isoformat() == later
    other = dict(board_item, id=str(uuid.uuid4().int % 10**9))
    fake.items = [other]
    autopilot.run("rfq_board")
    assert rfqs.get(rid)["status"] == "NO_BID"
    assert fake.writes == []                                                 # the board is never written


def test_late_vendor_gets_a_drafted_chaser_that_waits_for_its_owner(monkeypatch, only):
    only(vendor_chaser="suggest")
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "owner")
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"ZZ{tag}=sales@zz{tag}.test")
    job = vendors.create({"vendor": f"ZZ{tag}", "description": "AR400 liners", "po_number": f"P{tag}",
                          "ordered_on": _d(-40), "quoted_lead_days": 20}, OWNER)
    nomail = vendors.create({"vendor": f"NOMAIL{tag}", "description": "Chute", "ordered_on": _d(-40),
                             "quoted_lead_days": 20}, OWNER)

    autopilot.run("vendor_chaser")
    mine = [d for d in _drafts("vendor_chaser") if d.args.get("to") == f"sales@zz{tag}.test"]
    assert len(mine) == 1 and mine[0].status == "PENDING" and mine[0].user_email == OWNER["email"]
    assert f"P{tag}" in mine[0].args["subject"] and "20 days late" in mine[0].summary
    # No email on file: no second alert from the autopilot; the vendor-late alert carries the message.
    with SessionLocal() as db:
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"autopilot:vendor-chase:{nomail['id']}:%")).count() == 0
    vendors.run_checks()
    with SessionLocal() as db:
        late = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"vendor-late:{nomail['id']}:%")).one()
        mine_late = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"vendor-late:{job['id']}:%")).one()
    assert "Suggested message" in late.body and f"P{tag}" not in late.body
    assert "drafted for approval" in mine_late.body

    autopilot.run("vendor_chaser")
    assert len([d for d in _drafts("vendor_chaser") if d.args.get("to") == f"sales@zz{tag}.test"]) == 1  # once a week

    # The owner sees it in the assistant, long after a chat proposal would have expired.
    with SessionLocal() as db:
        db.get(AssistantActionDB, mine[0].id).created_at = datetime.now(timezone.utc) - timedelta(hours=5)
        db.commit()
    assert any(p["id"] == mine[0].id and p["from"] == "autopilot" for p in agent.pending(OWNER))
    out = agent.decide(mine[0].id, True, OWNER)
    assert out["status"] == "DONE" and "drafted" in out["result"]["done"].lower()   # no mail server: drafted, not sent
    assert job["id"]


def test_quote_follow_up_in_auto_mode_waits_while_mail_is_off_then_sends_and_retries_failures(monkeypatch, only):
    from app.services.outbound_email_service import OutboundEmailService, outbound_email_service

    only(quote_followup="auto")
    tag = uuid.uuid4().hex[:6]
    to = f"buyer{tag}@knauf.test"
    r = rfqs.create({"customer": "Knauf", "subject": "D cone liner", "contact_email": to}, OWNER)
    with SessionLocal() as db:
        row = db.get(RfqDB, r["id"])
        row.status, row.quoted_at, row.quote_number = "QUOTE_SENT", datetime.now(timezone.utc) - timedelta(days=5), "Q-77"
        db.commit()

    # Mail is off (as in every test): auto can't really send, so it waits for a person.
    autopilot.run("quote_followup")
    [draft] = [d for d in _drafts("quote_followup") if d.args.get("to") == to]
    assert draft.status == "PENDING" and "Q-77" in draft.args["subject"]

    # With mail on (faked: nothing leaves this machine), auto sends; a failure is tried again.
    with SessionLocal() as db:
        db.delete(db.get(AssistantActionDB, draft.id))
        db.commit()
    sent, answers = [], ["FAILED", "SENT"]
    monkeypatch.setattr(OutboundEmailService, "send_enabled", property(lambda self: True))
    monkeypatch.setattr(outbound_email_service, "send", lambda to_, subject, body, **kw: sent.append(body) or {"status": answers.pop(0)})
    autopilot.run("quote_followup")
    assert [d.status for d in _drafts("quote_followup") if d.args.get("to") == to] == ["FAILED"]
    autopilot.run("quote_followup")
    assert [d.status for d in _drafts("quote_followup") if d.args.get("to") == to] == ["DONE"]
    assert len(sent) == 2 and "autopilot@" not in sent[-1]                  # signed as Allied, not a fake person
    autopilot.run("quote_followup")
    assert len(sent) == 2                                                  # sent once, never again

    # Quotes long past the last follow-up step are left alone.
    with SessionLocal() as db:
        db.get(RfqDB, r["id"]).quoted_at = datetime.now(timezone.utc) - timedelta(days=60)
        db.commit()
    assert all(r["number"] not in d["what"] for d in autopilot.run("quote_followup", dry_run=True)["plays"]["quote_followup"]["did"])


def test_orders_watch_lists_unpaid_orders_and_photos_once_a_week(monkeypatch, only):
    only(orders_watch="auto")
    t = lambda x: {"title": x}  # noqa: E731
    row = lambda name, group, status, pay: {"id": uuid.uuid4().hex[:9], "name": name, "group": {"title": group},  # noqa: E731
                                            "column_values": [{"id": "s", "text": status, "value": None, "column": t("Status")},
                                                              {"id": "p", "text": pay, "value": None, "column": t("Payment Status")},
                                                              {"id": "po", "text": "9300", "value": None, "column": t("Allied PO#")}]}
    fake = FakeMonday([row("OC-IRVING", "Payment", "AWAITING INVOICE", "Needs Full Payment"),
                       row("MOSAIC", "Payment", "AWAITING INVOICE", "Paid in Full"),
                       row("HECLA", "Manufacturing", "NEED PICS APPROVED", "")])
    for k, v in {"MONDAY_VENDOR_BOARD_ID": "18394243717", "VENDOR_STATUS_COLUMN_ID": "Status",
                 "VENDOR_PO_COLUMN_ID": "Allied PO#"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(projects, "_client", lambda: fake)
    did = autopilot.run("orders_watch", on=date(2031, 3, 3))["plays"]["orders_watch"]["did"]
    assert [d["outcome"] for d in did] == ["told staff", "told staff"]
    with SessionLocal() as db:
        owing = db.query(NotificationDB).filter(NotificationDB.dedup_key == "autopilot:orders-owing:2031w10").one()
    assert "OC-IRVING" in owing.body and "MOSAIC" not in owing.body
    again = autopilot.run("orders_watch", on=date(2031, 3, 4))["plays"]["orders_watch"]["did"]
    assert [d["outcome"] for d in again] == ["already told", "already told"]


def test_switched_off_does_nothing_and_the_api_defaults_to_a_dry_run(client, monkeypatch, only):
    only(vendor_chaser="suggest")
    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", False)
    assert autopilot.run()["plays"]["vendor_chaser"]["skipped"] == "autopilot is switched off"
    status = client.get("/api/v1/autopilot").json()
    assert {p["name"] for p in status["plays"]} == set(autopilot.PLAYS)
    out = client.post("/api/v1/autopilot/run", json={}).json()
    assert out["dry_run"] is True
    assert client.post("/api/v1/autopilot/run", json={"play": "nope"}).status_code == 404


def test_any_reviewer_can_act_on_a_draft_and_an_acknowledgement_acknowledges(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "management")
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", "lead.one@allied.test,lead.two@allied.test")
    second = {"email": "lead.two@allied.test", "role": "MANAGER"}
    r = rfqs.create({"customer": "Vulcan", "subject": "Chute", "contact_email": f"ack{uuid.uuid4().hex[:6]}@v.test"},
                    OWNER, source="email")
    [draft] = [p for p in agent.pending(second) if p["from"] == "autopilot" and r["number"] in p["summary"]]
    assert agent.decide(draft["id"], True, second)["status"] == "DONE"     # not only the first reviewer
    assert rfqs.get(r["id"])["status"] == "ACKNOWLEDGED"                   # sending it acknowledged the RFQ
    with pytest.raises(LookupError):
        agent.decide(draft["id"], True, {"email": "stranger@allied.test", "role": "ADMIN"})


def test_a_bad_cell_skips_one_order_not_the_whole_board(monkeypatch):
    from tests.test_events import FakeMonday, _orders_item

    board = str(uuid.uuid4().int % 10**9)
    for k, v in {"MONDAY_VENDOR_BOARD_ID": board, "VENDOR_VENDOR_COLUMN_ID": "Vendor", "VENDOR_PO_COLUMN_ID": "Allied PO#",
                 "VENDOR_STATUS_COLUMN_ID": "Status", "VENDOR_LEAD_DAYS_COLUMN_ID": "Lead"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(projects, "_users_cache", {"at": 0.0, "map": {}})
    good, bad = _orders_item("1" + board, "FABRICATION", "G1"), _orders_item("2" + board, "FABRICATION", "B1")
    bad["column_values"].append({"id": "lead", "text": "900", "value": None, "column": {"title": "Lead"}})
    out = vendors.sync_vendor_board(FakeMonday([good, bad]))
    assert out["jobs"] == 2 and out["added"] == 1 and out["skipped"] == 1


def test_drafts_go_to_management_by_default_and_never_to_nobody(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "management")
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", "ops.manager@allied.test")
    assert autopilot.reviewers("engineer@allied.test") == ["ops.manager@allied.test"]
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", "")
    monkeypatch.setattr(autopilot.alerts, "leadership", lambda: [])
    assert autopilot.reviewers() == [settings.ADMIN_EMAIL]                   # nobody set up yet: the admin
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "owner")
    assert autopilot.reviewers("engineer@allied.test") == ["engineer@allied.test"]


def test_board_changes_are_noticed_with_one_small_read(monkeypatch):
    from app.services.coordination import changes

    marks = {"1": ("t1", "i1"), "2": ("t1", "i9")}

    class Watch:
        live = True
        calls = 0

        def execute_query(self, query, variables=None, idempotency_key=None):
            assert not query.lstrip().startswith("mutation") and "__last_updated__" in query
            Watch.calls += 1
            return {"data": {"boards": [{"id": b, "updated_at": t, "items_page": {"items": [{"id": i, "updated_at": t}]}}
                                        for b, (t, i) in marks.items()]}}

    synced = []
    monkeypatch.setattr(changes, "watched", lambda: {"1": lambda: synced.append("1"), "2": lambda: synced.append("2")})
    monkeypatch.setattr(changes, "_seen", {})
    w = Watch()
    changes.check(w)
    assert sorted(synced) == ["1", "2"]                                     # first look: read both
    synced.clear()
    changes.check(w)
    assert synced == []                                                     # nothing moved: nothing read
    marks["2"] = ("t2", "i9")
    changes.check(w)
    assert synced == ["2"] and Watch.calls == 3                             # only the board that changed


def test_vendor_names_on_the_boards_are_matched_to_the_vendor_master():
    assert autopilot.vendor_email("NIBROCK") == "dparker@nibrockinc.com"      # vendor id
    assert autopilot.vendor_email("helen") == "helen@zbsupereal.com"          # the contact, before the @
    assert autopilot.vendor_email("Nobody Here") is None

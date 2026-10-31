"""Event-driven actions: things that happen trigger reactions at once, in their mode, never writing to monday."""

import json
import uuid
from datetime import date, timedelta

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, NotificationDB, ShipmentDB
from app.services.assistant import tools
from app.services.coordination import autopilot, events, projects, rfqs, vendors

USER = {"email": "sales.events@allied.test", "role": "OPERATOR"}


def _uid() -> str:
    return uuid.uuid4().hex[:6]


def _actions(**match):
    with SessionLocal() as db:
        rows = db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT").all()
        return [r for r in rows if all((r.args or {}).get(k) == v for k, v in match.items())]


def _alerts(prefix: str):
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{prefix}%")).all()


def test_a_new_rfq_by_email_gets_an_acknowledgement_drafted(monkeypatch):
    tag = _uid()
    r = rfqs.create({"customer": f"Knauf {tag}", "subject": "D cone liner", "contact_email": f"buyer{tag}@knauf.test",
                     "owner_email": USER["email"]}, USER, source="email")
    ack = _actions(to=f"buyer{tag}@knauf.test")
    assert len(ack) == 1 and ack[0].status == "PENDING" and ack[0].user_email == autopilot.reviewers()[0]  # management
    assert r["number"] in ack[0].args["body"] and ack[0].args["_play"] == "rfq_acknowledge"
    assert events.recent[0]["event"] == "rfq.received" and r["number"] in events.recent[0]["summary"]

    # Read from the monday board: already being handled, so no acknowledgement.
    rfqs.create({"customer": "OC", "subject": "x", "contact_email": f"board{tag}@oc.test"}, USER, source="monday", announce=False)
    assert _actions(to=f"board{tag}@oc.test") == []

    monkeypatch.setattr(settings, "AUTOPILOT_REACTIONS", "rfq_acknowledge=off")
    rfqs.create({"customer": "Off", "subject": "y", "contact_email": f"off{tag}@x.test"}, USER, source="email")
    assert _actions(to=f"off{tag}@x.test") == []


def test_waiting_on_a_vendor_asks_for_the_price_and_a_win_tells_the_team(monkeypatch):
    tag = _uid()
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"Duke{tag}=duke@{tag}.test")
    r = rfqs.create({"customer": f"OC-Irving {tag}", "subject": "Bottom Hopper"}, USER)
    rfqs.update(r["id"], {"vendor": f"Duke{tag}, Nobody{tag}", "status": "WAITING_VENDOR"}, USER)
    ask = _actions(to=f"duke@{tag}.test")
    assert len(ask) == 1 and "Price request" in ask[0].args["subject"]
    told = _alerts(f"autopilot:rfq-price-ask:{r['id']}:no-email")
    assert len(told) == 1 and f"Nobody{tag}" in told[0].title and "Suggested message" in told[0].body  # one note, by hand

    rfqs.update(r["id"], {"quote_number": f"Q-{tag}", "status": "QUOTE_SENT"}, USER)
    rfqs.update(r["id"], {"status": "WON"}, USER)
    won = _alerts(f"autopilot:rfq-won:{r['id']}")
    assert len(won) == 1 and "Won:" in won[0].title


def _orders_item(item, status, po):
    t = lambda x: {"title": x}  # noqa: E731
    return {"id": item, "name": "OC-IRVING", "group": {"title": "Manufacturing"}, "column_values": [
        {"id": "v", "text": "KWS", "value": None, "column": t("Vendor")},
        {"id": "po", "text": po, "value": None, "column": t("Allied PO#")},
        {"id": "s", "text": status, "value": None, "column": t("Status")},
        {"id": "d", "text": "Hopper liners", "value": None, "column": t("Description")}]}


class FakeMonday:
    live, writable = True, False

    def __init__(self, items):
        self.items = items

    def execute_query(self, query, variables=None, idempotency_key=None):
        assert not query.lstrip().startswith("mutation")
        if "users" in query:
            return {"data": {"users": []}}
        return {"data": {"boards": [{"items_page": {"cursor": None, "items": self.items}}]}}


def test_status_changes_on_the_orders_board_start_tracking_and_photo_checks(monkeypatch):
    tag, item = _uid(), str(uuid.uuid4().int % 10**9)
    for k, v in {"MONDAY_VENDOR_BOARD_ID": str(uuid.uuid4().int % 10**9), "VENDOR_VENDOR_COLUMN_ID": "Vendor",
                 "VENDOR_PO_COLUMN_ID": "Allied PO#", "VENDOR_STATUS_COLUMN_ID": "Status",
                 "VENDOR_DESCRIPTION_COLUMN_ID": "Description", "VENDOR_DONE_LABELS": "SHIPPED,DONE"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(projects, "_users_cache", {"at": 0.0, "map": {}})
    fake = FakeMonday([_orders_item(item, "FABRICATION", tag)])
    vendors.sync_vendor_board(fake)
    fake.items[0]["column_values"][2]["text"] = "NEED PICS APPROVED"
    vendors.sync_vendor_board(fake)
    assert len(_alerts("autopilot:order-photos:")) >= 1
    assert any(e["event"] == "order.status_changed" and "NEED PICS APPROVED" in e["summary"] for e in events.recent)

    fake.items[0]["column_values"][2]["text"] = "SHIPPED"
    vendors.sync_vendor_board(fake)
    vendors.sync_vendor_board(fake)                                   # a second read changes nothing
    with SessionLocal() as db:
        ships = db.query(ShipmentDB).filter(ShipmentDB.reference == f"PO {tag}").all()
    assert len(ships) == 1 and ships[0].status == "DEPARTED"


def test_a_vendor_order_turning_late_is_chased_once_not_again_by_the_timer(monkeypatch):
    tag = _uid()
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"LATE{tag}=late@{tag}.test")
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_chaser=suggest")
    vendors.create({"vendor": f"LATE{tag}", "description": "Chute", "po_number": f"L{tag}",
                    "ordered_on": (date.today() - timedelta(days=30)).isoformat(), "quoted_lead_days": 10}, USER)
    vendors.run_checks()
    assert len(_actions(to=f"late@{tag}.test")) == 1                  # drafted the moment it turned late
    outcomes = [d["outcome"] for d in autopilot.run("vendor_chaser")["plays"]["vendor_chaser"]["did"]
                if f"L{tag}" in d["what"]]
    assert outcomes == ["already"]


def test_milestone_changes_are_recorded_and_everything_shows_in_the_assistant_and_api(client, monkeypatch):
    events.handle("milestone.status_changed", {"milestone": {"name": "Splitter Box"}, "from": "Scanned", "to": "Working on it"},
                  "Splitter Box: Scanned -> Working on it")
    assert events.recent[0]["event"] == "milestone.status_changed"
    text = tools.automation_status({}, {"email": "admin@allied.test", "role": "ADMIN"})
    assert "rfq_acknowledge" in text and "vendor_chaser" in text and "Splitter Box" in text
    body = client.get("/api/v1/autopilot/events").json()
    assert {r["name"] for r in body["reactions"]} == set(events.REACTIONS)
    assert client.get("/api/v1/autopilot").json()["events"]["reactions"]


def test_a_failing_reaction_never_breaks_the_thing_that_happened(monkeypatch):
    def boom(e):
        raise RuntimeError("bad reaction")

    monkeypatch.setitem(events.REACTIONS, "rfq_acknowledge", events.Reaction("rfq.received", boom, "suggest", "x"))
    r = rfqs.create({"customer": "Still logged", "subject": "z", "contact_email": "a@b.test"}, USER, source="email")
    assert rfqs.get(r["id"])["number"] == r["number"]
    assert events.recent[0]["reactions"][0]["error"] == "bad reaction"
    assert json.dumps(events.status())                                 # the log stays serialisable

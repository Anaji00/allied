"""Allied's starter recipes and the "sat in one status for N days" trigger.

Boards are copies built in the test; nothing reads or writes monday and no mail leaves."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, AutomationRecipeDB, NotificationDB
from app.services.coordination import activity, boards, recipes

BOSS = {"email": "boss@allied.test", "role": "MANAGER"}
COLS = [{"id": "status", "title": "Status", "type": "status", "labels": {"WAITING ON PI": "", "FABRICATION": ""}},
        {"id": "text", "title": "Description", "type": "text"}, {"id": "po", "title": "Allied PO#", "type": "text"},
        {"id": "vendor", "title": "Vendor", "type": "text"}]


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as db:
        db.query(AutomationRecipeDB).delete()
        db.commit()


def _orders_board(monkeypatch, items, moves):
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now",
                                 "groups": [], "columns": COLS, "items": items}
    with activity._lock:
        activity._logs[board] = {"last": None, "entries": {
            str(i): {"id": str(i), "event": "update_column_value", "item_id": item, "item": item, "column_id": "status",
                     "to": to, "at": at} for i, (item, to, at) in enumerate(moves)}}
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", board)
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    return board


def _ago(days):
    """Midday, Allied's time, that many calendar days back: the hour of the day (and a clock change
    in between) can't shift which day it was."""
    from zoneinfo import ZoneInfo

    local = datetime.now(ZoneInfo(settings.BUSINESS_TZ)).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=days)
    return local.astimezone(timezone.utc).isoformat()


def test_an_order_sitting_too_long_in_one_status_is_flagged_once(monkeypatch):
    _orders_board(monkeypatch, [
        {"id": "a", "name": "OC-AMARILLO", "values": {"status": "WAITING ON PI", "text": "Chute liner", "po": "9101", "vendor": "HELEN"}},
        {"id": "b", "name": "MOSAIC", "values": {"status": "WAITING ON PI", "text": "Cyclone", "po": "9264", "vendor": "KINGCERA"}},
        {"id": "c", "name": "NOL-TEC", "values": {"status": "FABRICATION", "text": "Pipe", "po": "9300", "vendor": "KWS"}}],
        [("a", "FABRICATION", _ago(90)), ("a", "WAITING ON PI", _ago(80)),   # 80 days: flagged
         ("b", "WAITING ON PI", _ago(2)),                                      # 2 days: not yet
         ("c", "WAITING ON PI", _ago(60)), ("c", "FABRICATION", _ago(1))])     # moved on: not flagged
    seen = {s["slug"]: s["today"] for s in recipes.starters(with_preview=True)}
    assert seen["orders-pi-stuck"] == [{"item_id": "a", "item": "OC-AMARILLO", "detail": "80 days"}]
    assert {x["item"] for x in seen["orders-ask-pi"]} == {"OC-AMARILLO", "MOSAIC"}
    recipes.add_starter("orders-pi-stuck", {}, BOSS)
    first = recipes.run_dates()
    assert [d["what"] for d in first] == ["Proforma still missing after 5 days: OC-AMARILLO"]
    assert recipes.run_dates()[0]["outcome"] == "already told"
    with SessionLocal() as db:
        n = db.query(NotificationDB).filter(NotificationDB.dedup_key.like("autopilot:recipe-%:a:%")).one()
        assert n.title == "OC-AMARILLO Chute liner: no proforma after 80 days (PO 9101, HELEN)"


def test_a_starter_email_uses_the_boards_own_columns(monkeypatch):
    from app.services.coordination import autopilot

    _orders_board(monkeypatch, [{"id": "m", "name": "MOSAIC", "values": {
        "status": "EXPECTING PICS", "text": "DILUTE MEDIA CYCLONE", "po": "9264", "vendor": "KINGCERA"}}], [])
    monkeypatch.setattr(autopilot, "vendor_email", lambda name: "sales@kingcera.test" if name == "KINGCERA" else None)
    recipes.add_starter("orders-ask-photos", {}, BOSS)
    did = recipes.on_event("order.status_changed", {"job": {"id": 1, "monday_item_id": "m", "vendor": "KINGCERA"},
                                                    "to": "EXPECTING PICS"})
    assert did[0]["outcome"] == "drafted"
    with SessionLocal() as db:
        row = (db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT")
               .order_by(AssistantActionDB.created_at.desc()).first())
    assert row.args["to"] == "sales@kingcera.test" and row.args["subject"] == "Photos before crating: our PO 9264"
    assert "DILUTE MEDIA CYCLONE (our PO 9264)" in row.args["body"] and "{" not in row.args["body"]


def test_starters_say_why_and_are_added_once(client):
    listed = client.get("/api/v1/autopilot/recipes/starters").json()
    assert len(listed) >= 10 and all(s["why"] and s["saves"] and s["recipe"]["description"] for s in listed)
    for s in listed:   # each starter is a valid recipe as it stands
        recipes._check({"mode": "suggest", **s["recipe"], **({"recipients": "x@allied.test"} if s.get("needs") else {})})
    assert client.post("/api/v1/autopilot/recipes/starters/orders-send-to-jane", json={}).status_code == 400
    jane = client.post("/api/v1/autopilot/recipes/starters/orders-send-to-jane", json={"recipients": "jane@allied.test"})
    assert jane.status_code == 201 and jane.json()["recipients"] == "jane@allied.test"
    assert client.post("/api/v1/autopilot/recipes/starters/orders-send-to-jane",
                       json={"recipients": "jane@allied.test"}).status_code == 400   # already added
    added = {s["slug"]: s["added"] for s in client.get("/api/v1/autopilot/recipes/starters").json()}
    assert added["orders-send-to-jane"] and not added["orders-ask-pi"]
    assert client.post("/api/v1/autopilot/recipes/starters/nope", json={}).status_code == 404


def test_suggestions_load_before_the_board_history_is_read(client, monkeypatch):
    _orders_board(monkeypatch, [{"id": "z", "name": "X", "values": {"status": "WAITING ON PI"}}], [])
    with activity._lock:
        activity._logs.pop(settings.MONDAY_VENDOR_BOARD_ID, None)   # history not read yet
    got = client.get("/api/v1/autopilot/recipes/starters?preview=true")
    assert got.status_code == 200
    row = {s["slug"]: s for s in got.json()}["orders-pi-stuck"]
    assert row["today"] == [] and row["live"] is None and row["why_as_of"] == "2026-10-31"


def test_starter_reasons_are_worked_out_from_the_board_now(monkeypatch):
    _orders_board(monkeypatch, [{"id": "a", "name": "A", "values": {"status": "FABRICATION"}}],
                  [("a", "WAITING ON PI", _ago(10)), ("a", "FABRICATION", _ago(6)),
                   ("b", "WAITING ON PI", _ago(9)), ("b", "FABRICATION", _ago(7))])
    live = {s["slug"]: s["live"] for s in recipes.starters(with_preview=True)}
    assert live["orders-ask-pi"] == "In the last ~90 days, items left WAITING ON PI 2 times, after a median of 3.0 days (longest 4.0)."


def test_the_rule_form_gets_every_choice_from_the_board(client, monkeypatch):
    cols = COLS + [{"id": "formula_ets", "title": "VENDOR ETS", "type": "formula"},
                   {"id": "notes", "title": "Notes", "type": "text"}]
    board = _orders_board(monkeypatch, [{"id": "a", "name": "MOSAIC", "values": {"status": "WAITING ON PI",
                                                                                "formula_ets": "2026-11-02", "notes": "asked"}}], [])
    with boards._lock:
        boards._mirror[board]["columns"] = cols
    out = client.get("/api/v1/autopilot/recipes/options", params={"board_role": "vendors"}).json()
    assert out["board_read"] and [l["label"] for l in out["labels"]] == ["WAITING ON PI", "FABRICATION"]
    assert {"id": "formula_ets", "title": "VENDOR ETS"} in out["dates"]                     # a formula that holds dates
    assert all(d["title"] != "Notes" for d in out["dates"])
    assert "Allied PO#" in out["fields"] and out["email_to"] == [{"value": "vendor", "label": "The order's vendor"}]
    assert {p["value"] for p in out["people"]} >= {"owner", "reviewers", "leadership"}
    assert client.get("/api/v1/autopilot/recipes/options", params={"board_role": "nope"}).status_code == 400


def test_a_rule_can_be_tried_before_it_is_saved(client, monkeypatch):
    _orders_board(monkeypatch, [{"id": "a", "name": "OC-AMARILLO", "values": {"status": "WAITING ON PI"}}],
                  [("a", "WAITING ON PI", _ago(12))])
    out = client.post("/api/v1/autopilot/recipes/preview", json={
        "board_role": "vendors", "trigger": "status_for", "column": "WAITING ON PI", "value": "10", "action": "notify"}).json()
    assert out["today"] == [{"item_id": "a", "item": "OC-AMARILLO", "detail": "12 days"}]
    assert out["description"].startswith("When an item has been WAITING ON PI for 10 days")
    with SessionLocal() as db:
        assert db.query(AutomationRecipeDB).count() == 0                                      # nothing saved
    bad = client.post("/api/v1/autopilot/recipes/preview", json={"board_role": "vendors", "trigger": "status_for",
                                                                  "column": "X", "value": "soon", "action": "notify"})
    assert bad.status_code == 400

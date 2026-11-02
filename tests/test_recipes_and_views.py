"""People's own recipes, the event pool, the risk digest, drawing links, the RFQ form and board views.

Every board here is a copy built in the test; nothing reads or writes monday, and no
mail goes anywhere (conftest switches mail, push and texts off)."""

import json
import uuid
from datetime import date, timedelta

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import (AssistantActionDB, AutomationRecipeDB, MondayMilestoneDB, NotificationDB,
                                  VendorJobDB)
from app.services.coordination import boards

BOSS = {"email": "boss@allied.test", "role": "MANAGER"}


def _board(role, items, columns, name="Board"):
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": name, "role": role, "read_at": "now", "groups": [],
                                 "columns": columns, "items": items}
    return board


STATUS = {"id": "status", "title": "Status", "type": "status",
          "labels": {"Stuck": "#e2445c", "Working on it": "#fdab3d", "Done": "#00c875"}}


@pytest.fixture(autouse=True)
def _clean():
    yield
    with SessionLocal() as db:
        db.query(AutomationRecipeDB).delete()
        db.commit()


def _alerts(prefix):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB) if n.dedup_key.startswith(prefix)]


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------


def test_a_recipe_is_checked_before_it_is_saved(client):
    bad = client.post("/api/v1/autopilot/recipes", json={"name": "x", "board_role": "vendors", "trigger": "date_within",
                                                         "value": "soon", "action": "notify"})
    assert bad.status_code == 400 and "number of days" in bad.json()["detail"]
    no_to = client.post("/api/v1/autopilot/recipes", json={"name": "x", "board_role": "vendors", "trigger": "status_is",
                                                           "value": "Stuck", "action": "draft_email"})
    assert no_to.status_code == 400
    ok = client.post("/api/v1/autopilot/recipes", json={"name": "Stuck orders", "board_role": "vendors",
                                                        "trigger": "status_is", "value": "Stuck", "action": "notify",
                                                        "recipients": "ops@allied.test"})
    assert ok.status_code == 201
    r = ok.json()
    assert r["board_role"] == "vendors" and "board_id" in r and r["description"].startswith("When status becomes Stuck")
    off = client.patch(f"/api/v1/autopilot/recipes/{r['id']}", json={"enabled": False}).json()
    assert off["enabled"] is False and off["name"] == "Stuck orders"
    assert [x["id"] for x in client.get("/api/v1/autopilot/recipes?board_role=vendors").json()] == [r["id"]]
    assert client.delete(f"/api/v1/autopilot/recipes/{r['id']}").status_code == 204
    assert client.delete(f"/api/v1/autopilot/recipes/{r['id']}").status_code == 404


def test_a_status_recipe_runs_on_the_status_change_even_with_no_built_in_reaction(monkeypatch):
    from app.services.coordination import events, recipes

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    recipes.create({"name": "Drawing stuck", "board_role": "projects", "trigger": "status_is", "value": "stuck",
                    "action": "notify", "recipients": "owner"}, BOSS)
    mid = f"m{uuid.uuid4().hex[:8]}"
    payload = {"milestone": {"item_id": mid, "name": "Hopper drawing", "owner_emails": ["eng@allied.test"]},
               "from": "Working on it", "to": "Stuck"}
    events.emit("milestone.status_changed", payload, "Hopper: Working on it -> Stuck")
    told = _alerts("autopilot:recipe-")
    assert told and any(mid in n.dedup_key and n.recipients == ["eng@allied.test"] for n in told)
    entry = events.recent[0]
    assert entry["event"] == "milestone.status_changed" and entry["reactions"][-1]["name"] == "recipes"
    # Another label does nothing.
    before = len(_alerts("autopilot:recipe-"))
    events.emit("milestone.status_changed", {**payload, "to": "Done"}, "")
    assert len(_alerts("autopilot:recipe-")) == before


def test_an_email_recipe_drafts_to_the_vendor_and_waits_for_a_person(monkeypatch):
    from app.services.coordination import autopilot, recipes

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    monkeypatch.setattr(autopilot, "vendor_email", lambda name: "sales@kws.test" if name == "KWS" else None)
    recipes.create({"name": "Ask for photos", "board_role": "vendors", "trigger": "status_is", "value": "Crating",
                    "action": "draft_email", "email_to": "vendor", "subject": "Photos for {item}",
                    "body": "Please send photos of {item} before it is crated.", "mode": "auto"}, BOSS)
    did = recipes.on_event("order.status_changed", {"job": {"id": 7, "vendor": "KWS", "description": "Hopper"},
                                                    "to": "Crating"})
    assert did and did[0]["outcome"] == "drafted"   # auto, but mail is off: it waits for a person
    with SessionLocal() as db:
        row = (db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT")
               .order_by(AssistantActionDB.created_at.desc()).first())
        assert row.args["to"] == "sales@kws.test" and row.args["subject"] == "Photos for Hopper"
        assert row.status == "PENDING" and row.args["_play"].startswith("recipe_") and row.args["_item"] == "7"
    from app.services.assistant.tools import TOOLS

    sent = TOOLS["send_email"].run(dict(row.args), autopilot.SYSTEM)   # the auto path passes _ keys through
    assert sent["status"] != "FAILED"


def test_a_date_recipe_fires_once_per_item_and_date(monkeypatch):
    from app.services.coordination import recipes

    soon = (date.today() + timedelta(days=2)).isoformat()
    later = (date.today() + timedelta(days=40)).isoformat()
    cols = [STATUS, {"id": "date_1", "title": "Estimate finish", "type": "date"}]
    board = _board("projects", [
        {"id": "1", "name": "Splitter", "values": {"status": "Working on it", "date_1": soon}},
        {"id": "2", "name": "Divider", "values": {"status": "Working on it", "date_1": later}},
        {"id": "3", "name": "Old", "values": {"status": "Done", "date_1": soon}}], cols)
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", board)
    monkeypatch.setattr(settings, "MILESTONE_DONE_LABELS", "Done")
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    recipes.create({"name": "Due soon", "board_role": "projects", "trigger": "date_within", "column": "Estimate finish",
                    "value": "3", "action": "notify", "recipients": "ops@allied.test",
                    "subject": "{item} due {date} ({days} days)"}, BOSS)
    dry = recipes.run_dates(dry_run=True)
    assert [d["what"] for d in dry] == ["Due soon: Splitter"]
    first, again = recipes.run_dates(), recipes.run_dates()
    assert first[0]["outcome"] == "told staff" and again[0]["outcome"] == "already told"
    assert any(n.title == f"Splitter due {soon} (2 days)" for n in _alerts("autopilot:recipe-"))


# ---------------------------------------------------------------------------
# Events, scorecard
# ---------------------------------------------------------------------------


def test_events_go_to_a_small_worker_pool_not_a_thread_each(monkeypatch):
    from app.services.coordination import events

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTOPILOT_EVENTS_ASYNC", True)
    import threading

    sent, inline = [], []
    monkeypatch.setattr(events, "_waiting", threading.BoundedSemaphore(40))   # a small cap for the test
    monkeypatch.setattr(events._pool, "submit", lambda fn, *a: sent.append(a))
    monkeypatch.setattr(events, "handle", lambda *a, **k: inline.append(a))
    for i in range(50):
        events.emit("rfq.status_changed", {"rfq": {"id": i}, "to": "LOST"}, "")
    assert events._pool._max_workers == 1
    assert len(sent) == 40 and len(inline) == 10   # past the cap the caller runs it, nothing is dropped


def test_the_scorecard_says_which_drafts_people_send_and_which_they_cancel(monkeypatch):
    from app.services.coordination import autopilot

    monkeypatch.setattr(settings, "AUTOPILOT_SCORECARD_MIN", 3)
    good, noisy = f"ack_{uuid.uuid4().hex[:6]}", f"follow_{uuid.uuid4().hex[:6]}"  # other tests draft too
    monkeypatch.setattr(autopilot, "modes", lambda: {good: "suggest", noisy: "suggest"})
    with SessionLocal() as db:
        for i, st in enumerate(["DONE"] * 9 + ["CANCELLED"]):
            db.add(AssistantActionDB(id=uuid.uuid4().hex, user_email="b@allied.test", user_role="AUTOPILOT",
                                     tool="send_email", args={"_play": good}, summary="s", status=st))
        for st in ["CANCELLED"] * 4 + ["DONE"]:
            db.add(AssistantActionDB(id=uuid.uuid4().hex, user_email="b@allied.test", user_role="AUTOPILOT",
                                     tool="send_email", args={"_play": noisy}, summary="s", status=st))
        db.commit()
    card = {a["name"]: a for a in autopilot.scorecard()["automations"]}
    assert card[good]["send_rate"] == 0.9 and "auto" in card[good]["advice"]
    assert card[noisy]["send_rate"] == 0.2 and "switch it off" in card[noisy]["advice"]


# ---------------------------------------------------------------------------
# Vendor orders: risk digest, drawing links
# ---------------------------------------------------------------------------


def _job(**kw):
    from app.services.coordination import vendors

    return vendors.create({"vendor": "KWS", **kw}, {"email": "b@allied.test"})


def _drop(*ids):
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def test_unlinked_orders_at_risk_go_in_one_daily_list_per_person():
    from app.services.coordination import vendors

    soon = (date.today() + timedelta(days=5)).isoformat()
    late_promise = (date.today() + timedelta(days=30)).isoformat()
    jobs = [_job(description=f"Part {i}", owner_email="buyer@allied.test", promised_on=late_promise, customer_due=soon,
                 ordered_on=date.today().isoformat()) for i in range(3)]
    try:
        vendors.run_checks()
        per_job = [n for n in _alerts("vendor-risk:") if any(n.dedup_key.startswith(f"vendor-risk:{j['id']}:") for j in jobs)]
        digest = _alerts("vendor-risk-digest:buyer@allied.test:")
        assert not per_job and len(digest) == 1
        assert all(f"Part {i}" in digest[0].body for i in range(3)) and digest[0].recipients == ["buyer@allied.test"]
    finally:
        _drop(*[j["id"] for j in jobs])


def test_an_order_waiting_on_an_unfinished_drawing_says_so_and_hears_when_it_is_done(monkeypatch):
    from app.services.coordination import events, vendors

    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    mid = f"m{uuid.uuid4().hex[:8]}"
    past = (date.today() - timedelta(days=3)).isoformat()
    with SessionLocal() as db:
        db.add(MondayMilestoneDB(item_id=mid, project_item_id="g1-x", board_id="1", name="Hopper drawing",
                                 owner_emails=[], status="Working on it", due_date=past, done=False))
        db.commit()
    job = _job(description="Hopper", project_item_id=mid, owner_email="buyer@allied.test")
    try:
        found = {j["id"]: j for j in vendors.overview()["jobs"]}[job["id"]]
        assert found["blocked_by"]["name"] == "Hopper drawing" and found["blocked_by"]["overdue"]
        assert any("waits on drawing Hopper drawing" in r for r in found["reasons"])
        events.emit("milestone.status_changed", {"milestone": {"item_id": mid, "name": "Hopper drawing", "done": True},
                                                 "from": "Working on it", "to": "Sent to Shop"}, "")
        told = _alerts(f"autopilot:drawing-ready:{mid}:")
        assert told and told[0].recipients == ["buyer@allied.test"] and "KWS" in told[0].body
    finally:
        _drop(job["id"])
        with SessionLocal() as db:
            db.query(MondayMilestoneDB).filter(MondayMilestoneDB.item_id == mid).delete()
            db.commit()


def test_orders_with_no_drawing_get_link_suggestions_by_customer_and_words(client, monkeypatch):
    ids = [f"m{uuid.uuid4().hex[:8]}" for _ in range(2)]
    cols = [STATUS, {"id": "dropdown", "title": "Customer", "type": "dropdown"}]
    board = _board("projects", [{"id": ids[0], "name": "Splitter box rev B", "values": {"status": "Working on it", "dropdown": "Owens Corning"}},
                                {"id": ids[1], "name": "Bin vent", "values": {"status": "Working on it", "dropdown": "Knauf"}}], cols)
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", board)
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    with SessionLocal() as db:
        for mid, name in zip(ids, ["Splitter box rev B", "Bin vent"]):
            db.add(MondayMilestoneDB(item_id=mid, project_item_id="g1-y", board_id=board, name=name, owner_emails=[], done=False))
        db.commit()
    job = _job(description="Owens Corning - Splitter Box")
    other = _job(description="Knauf - Splitter Box")   # same part, another customer: Owens' drawing is not offered
    try:
        got = {s["job_id"]: s for s in client.get("/api/v1/vendors/link-suggestions").json()}
        top = got[job["id"]]["suggestions"][0]
        assert top["item_id"] == ids[0] and top["customer"] == "Owens Corning" and "splitter" in top["shared"]
        assert all(s["item_id"] != ids[1] for s in got[job["id"]]["suggestions"])
        assert other["id"] not in got or all(s["item_id"] != ids[0] for s in got[other["id"]]["suggestions"])
    finally:
        _drop(job["id"], other["id"])
        with SessionLocal() as db:
            db.query(MondayMilestoneDB).filter(MondayMilestoneDB.item_id.in_(ids)).delete(synchronize_session=False)
            db.commit()


# ---------------------------------------------------------------------------
# Customer RFQ form
# ---------------------------------------------------------------------------


def test_the_customer_form_needs_its_token_and_logs_an_rfq(client, monkeypatch):
    from app.api.v1.endpoints import coordination
    from app.services.coordination import rfqs

    form = {"company": "Owens Corning", "email": "buyer@oc.test", "subject": "Wear liners", "details": "40 pcs AR400",
            "quantity": "40"}
    monkeypatch.setattr(settings, "RFQ_FORM_TOKEN", "")
    assert client.post("/api/v1/rfqs/form", json=form, headers={"X-Form-Token": "x"}).status_code == 404     # switched off
    monkeypatch.setattr(settings, "RFQ_FORM_TOKEN", "s3cret")
    monkeypatch.setattr(settings, "RFQ_FORM_PER_HOUR", 2)
    monkeypatch.setattr(coordination, "_form_hits", {})
    assert client.post("/api/v1/rfqs/form", json=form, headers={"X-Form-Token": "wrong"}).status_code == 403
    assert client.post("/api/v1/rfqs/form?token=s3cret", json=form).status_code == 403   # never in the URL
    assert client.get("/api/v1/rfqs/form", headers={"X-Form-Token": "s3cret"}).status_code == 200
    assert client.post("/api/v1/rfqs/form", json={**form, "email": "not-an-email"}, headers={"X-Form-Token": "s3cret"}).status_code == 422
    bot = client.post("/api/v1/rfqs/form", json={**form, "website": "http://spam"}, headers={"X-Form-Token": "s3cret"}).json()
    assert bot == {"received": True, "reference": None}
    ok = client.post("/api/v1/rfqs/form", json=form, headers={"X-Form-Token": "s3cret"})
    assert ok.status_code == 201
    r = rfqs.get(ok.json()["reference"])
    assert r["source"] == "form" and r["contact_email"] == "buyer@oc.test" and "Quantity: 40" in r["summary"]
    client.post("/api/v1/rfqs/form", json=form, headers={"X-Form-Token": "s3cret"})
    assert client.post("/api/v1/rfqs/form", json=form, headers={"X-Form-Token": "s3cret"}).status_code == 429
    # Each customer has their own limit even though all come through the same proxy.
    other = client.post("/api/v1/rfqs/form", json=form, headers={"X-Form-Token": "s3cret", "X-Forwarded-For": "203.0.113.9"})
    assert other.status_code == 201
    # A faked first hop doesn't buy a new limit: the proxy's own (last) hop is the one used.
    faked = {"X-Form-Token": "s3cret", "X-Forwarded-For": "1.2.3.4, 203.0.113.9"}
    client.post("/api/v1/rfqs/form", json=form, headers=faked)
    assert client.post("/api/v1/rfqs/form", json=form, headers=faked).status_code == 429


# ---------------------------------------------------------------------------
# Kanban, Gantt, calendar
# ---------------------------------------------------------------------------


def test_a_board_reads_as_kanban_gantt_and_calendar(client, monkeypatch):
    soon = (date.today() + timedelta(days=3)).isoformat()
    start = (date.today() - timedelta(days=20)).isoformat()
    target = (date.today() + timedelta(days=1)).isoformat()
    cols = [STATUS, {"id": "date0", "title": "Vendor PO Issued", "type": "date"},
            {"id": "formula_ets", "title": "VENDOR ETS", "type": "formula"},
            {"id": "formula_target", "title": "Target Date", "type": "formula"}]
    board = _board("vendors", [
        {"id": "1", "name": "Hopper", "group_title": "KWS", "values": {"status": "Working on it", "date0": start, "formula_ets": soon, "formula_target": target}},
        {"id": "2", "name": "Liner", "group_title": "KWS", "values": {"status": "Stuck"}},
        {"id": "3", "name": "Gone", "values": {"status": "Done", "formula_ets": soon}}], cols, name="2026 Orders")
    monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", board)
    monkeypatch.setattr(settings, "VENDOR_PROMISED_COLUMN_ID", "formula_ets")
    monkeypatch.setattr(settings, "VENDOR_CUSTOMER_DUE_COLUMN_ID", "formula_target")
    monkeypatch.setattr(settings, "VENDOR_DONE_LABELS", "Done")
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    t = client.get(f"/api/v1/boards/{board}/timeline").json()
    assert [lane["status"] for lane in t["kanban"]] == ["Stuck", "Working on it", "Done"]
    assert [c["name"] for c in t["kanban"][0]["items"]] == ["Liner"] and t["kanban"][2]["items"] == []
    bar = t["gantt"]["bars"][0]
    assert (bar["name"], bar["start"], bar["end"], bar["target"]) == ("Hopper", start, soon, target)
    assert t["gantt"]["start_column"] == "Vendor PO Issued" and len(t["gantt"]["bars"]) == 1
    assert {(e["column"], e["name"]) for e in t["calendar"]["entries"]} == {("VENDOR ETS", "Hopper"), ("Target Date", "Hopper")}
    assert client.get("/api/v1/boards/404/timeline").status_code == 404


def test_status_labels_keep_mondays_order():
    raw = {"settings_str": json.dumps({"labels": {"0": "Working", "1": "Done", "2": "Stuck", "5": ""},
                                       "labels_positions_v2": {"0": 1, "1": 2, "2": 0},
                                       "labels_colors": {"1": {"color": "#00c875"}}})}
    assert list(boards._labels(raw)) == ["Stuck", "Working", "Done"]


def test_an_rfq_recipe_matches_the_boards_label_and_a_move_counts_once(monkeypatch):
    from app.services.coordination import recipes

    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {"Need markup": ""}},
            {"id": "cust", "title": "Customer", "type": "text"}]
    board = _board("rfqs", [{"id": "r1", "name": "Liners", "values": {"status": "Need markup", "cust": "Knauf"}}], cols)
    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", board)
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    recipes.create({"name": "Markup", "board_role": "rfqs", "trigger": "status_is", "value": "Need markup",
                    "action": "notify", "recipients": "ops@allied.test", "subject": "{item} for {Customer}"}, BOSS)
    event = {"rfq": {"id": "x1", "monday_item_id": "r1"}, "from": "WAITING_VENDOR", "to": "PRICING"}
    first = recipes.on_event("rfq.status_changed", event)
    again = recipes.on_event("rfq.status_changed", event)       # the same move handled twice
    assert first[0]["outcome"] == "told staff" and again[0]["outcome"] == "already told"


def test_yesterdays_risk_list_is_closed_when_todays_goes_out():
    from app.services.coordination import notify

    who = f"buyer{uuid.uuid4().hex[:6]}@allied.test"
    old = notify.notify("vendor_risk", f"vendor-risk-digest:{who}:2026-01-01", "old list", "b", to=[who])
    gone = notify.notify("vendor_risk", f"vendor-risk-digest:nobody{who}:2026-01-01", "x", "b", to=[who])
    today = f"vendor-risk-digest:{who}:{date.today().isoformat()}"
    notify.notify("vendor_risk", today, "today", "b", to=[who])
    notify.resolve_others("vendor-risk-digest:", {today})
    with SessionLocal() as db:
        state = {n.id: n.acknowledged_by for n in db.query(NotificationDB).filter(NotificationDB.id.in_([old, gone]))}
        assert set(state.values()) == {notify.RESOLVED_BY}
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key == today).one().acknowledged_at is None


def test_the_form_limit_sees_the_real_customer_through_caddy_and_the_frontend():
    from starlette.requests import Request

    from app.api.v1.endpoints.coordination import _client

    def req(xff):
        return Request({"type": "http", "headers": [(b"x-forwarded-for", xff.encode())], "client": ("172.18.0.4", 1)})

    assert _client(req("203.0.113.9, 172.18.0.3")) == "203.0.113.9"            # internet -> Caddy -> Next
    assert _client(req("192.168.12.40, 172.18.0.3")) == "192.168.12.40"        # an office phone
    assert _client(req("1.2.3.4, 203.0.113.9, 172.18.0.3")) == "203.0.113.9"   # a faked first entry is ignored
    assert _client(req("203.0.113.9")) == "203.0.113.9"                        # straight to the frontend

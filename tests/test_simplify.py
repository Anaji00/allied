"""K.I.S.S.: alerts as a few groups, a "set these first" list, and one answer to "who owns this". Test data only."""
import uuid

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import NotificationDB
from app.services import monday_push
from app.services.coordination import boards, notify, owners
from tests.conftest import staff_token

BOSS = {"email": "boss-s@alliedweldingsystems.com", "role": "MANAGER"}


def _alert(kind, title, severity="warning", to=("simp@alliedweldingsystems.com",)):
    with SessionLocal() as db:
        row = NotificationDB(dedup_key=f"simp:{uuid.uuid4().hex}", kind=kind, severity=severity, title=title, body="b", recipients=list(to))
        db.add(row)
        db.commit()
        return row.id


def test_alert_kinds_fold_into_a_few_plain_groups():
    assert notify.group_of("vendor_late") == "Vendors" and notify.group_of("vendor_risk") == "Vendors"
    assert notify.group_of("rfq_overdue") == "RFQs" and notify.group_of("autopilot_rfq_ask_vendors") == "RFQs"
    assert notify.group_of("autopilot_drafts") == "Drafts to approve" and notify.group_of("autopilot_recipe_3") == "Automations"
    assert notify.group_of("daily_summary") == "Briefs" and notify.group_of("something_new") == "Other"


def test_the_rollup_counts_ranks_and_can_close_a_group(client):
    who = f"roll{uuid.uuid4().hex[:5]}@alliedweldingsystems.com"
    ids = [_alert("vendor_late", "Late A", to=(who,)), _alert("vendor_risk", "Risk B", "critical", to=(who,)), _alert("rfq_due", "RFQ C", "info", to=(who,))]
    out = {g["group"]: g for g in notify.rollup(who)}
    assert out["Vendors"]["count"] == 2 and out["Vendors"]["worst"] == "critical" and out["Vendors"]["items"][0]["title"] == "Risk B"
    assert out["RFQs"]["count"] == 1
    assert [g["group"] for g in notify.rollup(who)][0] == "Vendors"                       # the worst group first
    assert notify.acknowledge_group("Vendors", who) == 2
    assert [g["group"] for g in notify.rollup(who)] == ["RFQs"]
    assert notify.acknowledge_group("Vendors", "someone-else@alliedweldingsystems.com") == 0   # not theirs to close
    got = client.get("/api/v1/alerts/rollup")
    assert got.status_code == 200 and isinstance(got.json(), list)
    assert client.post("/api/v1/alerts/rollup/acknowledge", json={}).status_code == 400


def test_the_setup_list_says_what_is_blank_and_never_shows_a_value(client, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_SECRET_VALUE_123")
    monkeypatch.setattr(settings, "PO_NUMBER_FLOOR", 0)
    out = client.get("/api/v1/health/setup")
    assert out.status_code == 200
    body = out.json()
    assert "gsk_SECRET_VALUE_123" not in out.text
    by = {i["key"]: i for i in body["items"]}
    assert by["ai"]["ok"] is True and by["po_floor"]["ok"] is False and "clash" in by["po_floor"]["if_blank"]
    assert all(i["how_to_fix"] and i["if_blank"] for i in body["items"]) and body["safety"]["monday_read_only"] is True
    assert body["needs_setting"] >= 1 and "po_floor" in body["needed_first"]


def test_one_answer_to_who_owns_this(monkeypatch):
    from app.services.coordination import ball

    monkeypatch.setattr(ball, "get", lambda t, r: {"owner_email": "app-owner@alliedweldingsystems.com"})
    assert owners.owner_of("vendor_job", "1", "board@alliedweldingsystems.com") == "app-owner@alliedweldingsystems.com"      # the app's owner wins
    monkeypatch.setattr(ball, "get", lambda t, r: None)
    assert owners.owner_of("vendor_job", "1", "board@alliedweldingsystems.com") == "board@alliedweldingsystems.com"
    assert owners.owner_of("vendor_job", "1", None) is None
    monkeypatch.setattr(ball, "get", lambda t, r: {"owner_email": "bo.chen@placeholder.invalid"})
    assert owners.owner_of("vendor_job", "1", "real@alliedweldingsystems.com") == "real@alliedweldingsystems.com"        # a placeholder never wins


def test_an_owner_can_be_staged_for_monday_and_nothing_is_sent(monkeypatch):
    board = str(uuid.uuid4().int % 10**9)
    cols = [{"id": "status", "title": "Status", "type": "status"}, {"id": "owner", "title": "Owner", "type": "people"}]
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": cols,
                                 "items": [{"id": "77", "name": "HECLA LINE", "values": {}}]}

    class Client:
        live = True

    monkeypatch.setattr("app.services.monday_client.fetch_user_emails", lambda c: {"555": "ana@alliedweldingsystems.com"})
    cid = owners.stage_owner_to_monday(board, "77", "Ana@alliedweldingsystems.com", BOSS, client=Client())
    staged = [c for c in monday_push.changes("STAGED") if c["id"] == cid][0]
    assert staged["kind"] == "update_columns" and staged["payload"]["columns"]["owner"] == {"personsAndTeams": [{"id": 555, "kind": "person"}]}
    row = next(r for r in monday_push.preview([cid]))
    assert row["board_id"] == board and row["item_id"] == "77" and row["changes"][0]["column_id"] == "owner" and row["changes"][0]["column"] == "Owner"
    assert monday_push.push(who="x")["blocked"] in ("read-only", "simulation")                         # staged, never sent while read-only
    import pytest

    with pytest.raises(ValueError, match="no monday account"):
        owners.stage_owner_to_monday(board, "77", "nobody@alliedweldingsystems.com", BOSS, client=Client())
    Client.live = False
    with pytest.raises(ValueError, match="not connected"):
        owners.stage_owner_to_monday(board, "77", "ana@alliedweldingsystems.com", BOSS, client=Client())
    nocol = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[nocol] = {"board_id": nocol, "name": "X", "role": "vendors", "read_at": "now", "groups": [], "columns": cols[:1], "items": []}
    with pytest.raises(ValueError, match="no People column"):
        owners.stage_owner_to_monday(nocol, "1", "ana@alliedweldingsystems.com", BOSS, client=Client())


def test_every_automation_is_in_one_list_with_one_switch(client):
    from app.services.coordination import autopilot, events

    rows = client.get("/api/v1/automation/list").json()
    kinds = {r["kind"] for r in rows}
    assert {"play", "reaction", "check"} <= kinds and all({"id", "when", "then", "who_reviews", "mode", "switchable"} <= set(r) for r in rows)
    play = next(r for r in rows if r["id"] == "play:vendor_chaser")
    assert play["mode"] in ("off", "suggest", "auto") and play["switchable"]
    try:
        out = client.put("/api/v1/automation/mode", json={"id": "play:vendor_chaser", "mode": "off"})
        assert out.status_code == 200 and out.json()["mode"] == "off" and autopilot.modes()["vendor_chaser"] == "off"    # no restart needed
        out = client.put("/api/v1/automation/mode", json={"id": "reaction:rfq_won", "mode": "suggest"})
        assert out.status_code == 200 and events.modes()["rfq_won"] == "suggest"
        assert client.put("/api/v1/automation/mode", json={"id": "check:vendors", "mode": "off"}).status_code == 400   # the checks always run
        assert client.put("/api/v1/automation/mode", json={"id": "play:nope", "mode": "off"}).status_code == 404
        assert client.put("/api/v1/automation/mode", json={"id": "play:vendor_chaser", "mode": "sometimes"}).status_code == 400
    finally:
        from app.models.db_models import AutomationModeDB

        with SessionLocal() as db:
            db.query(AutomationModeDB).delete()
            db.commit()


def test_a_recipe_is_switched_off_and_back_with_the_same_switch(client):
    from app.services.coordination import recipes

    r = recipes.create({"name": "Simplify test", "board_role": "vendors", "trigger": "status_is", "value": "FABRICATION", "action": "notify",
                        "recipients": "owner", "mode": "suggest"}, BOSS)
    off = client.put("/api/v1/automation/mode", json={"id": f"recipe:{r['id']}", "mode": "off"}).json()
    assert off["mode"] == "off"
    on = client.put("/api/v1/automation/mode", json={"id": f"recipe:{r['id']}", "mode": "auto"}).json()
    assert on["mode"] == "auto"
    recipes.delete(r["id"])


def test_a_vendor_line_points_at_the_one_po_record(client):
    from app.models.db_models import VendorJobDB
    from app.services.coordination import jobs, purchase_orders, rfqs

    rfq = rfqs.create({"customer": "Owens Corning", "subject": "link test", "items": [{"description": "liner", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(rfq["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.approve(purchase_orders.create(j["id"], "HELEN", BOSS)["id"], BOSS)
    with SessionLocal() as db:
        line = db.query(VendorJobDB).filter(VendorJobDB.po_number == po["po_number"]).one()
        assert line.purchase_order_id == po["id"]
        unlinked = VendorJobDB(vendor="HELEN", description="from the board", po_number=po["po_number"] + "X", history=[])
        board_line = VendorJobDB(vendor="HELEN", description="board line", po_number=po["po_number"], history=[])
        db.add(board_line)
        db.commit()
        bid = board_line.id
    assert purchase_orders.link_vendor_lines() >= 1
    with SessionLocal() as db:
        assert db.get(VendorJobDB, bid).purchase_order_id == po["id"]
    shown = client.get(f"/api/v1/jobs/{j['id']}").json()["purchase_orders"]
    assert [p["po_number"] for p in shown].count(po["po_number"]) == 1                  # listed once, not once per line


def test_the_old_copilot_routes_say_they_are_replaced_by_the_assistant(client):
    out = client.get("/api/v1/chat/sources")
    assert out.headers.get("Deprecation") == "true" and "/api/v1/assistant" in out.headers.get("Link", "")

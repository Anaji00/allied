"""The monday push queue ("git push" for monday): changes are staged, reviewed and pushed only when a manager says so,
and nothing is ever sent while monday is read-only. monday is a fake here; the real one is never contacted."""

import json
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayChangeDB
from app.services import monday_push
from app.services.coordination import boards, rfqs
from app.services.monday_client import MondayClient
from tests.conftest import staff_token


class FakeHTTP:
    """The network: records every request and answers like monday would."""

    def __init__(self, fail_on=None):
        self.sent, self.fail_on = [], fail_on

    def post(self, url, json=None, headers=None):
        query = json["query"]
        self.sent.append((query, json.get("variables")))
        fail = self.fail_on and self.fail_on in query

        class R:
            status_code = 200
            headers = {}
            text = ""

            def json(self_inner):
                if fail:
                    return {"errors": [{"message": "nope"}]}
                if "create_item" in query:
                    return {"data": {"create_item": {"id": "7771"}}}
                return {"data": {"change_multiple_column_values": {"id": "1"}, "create_update": {"id": "2"}}}

        return R()


@pytest.fixture
def mon(monkeypatch, client):
    """A connected monday client (fake network), writing OFF, manual mode: the default and the safe state."""
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "manual")
    c = MondayClient(api_key="test-token", api_url="https://monday.invalid/v2")
    http = FakeHTTP()
    monkeypatch.setattr(c, "_http", lambda: http)
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: c)
    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()
    yield c, http
    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()


def _mutations(http):
    return [q for q, _ in http.sent if "mutation" in q]


def _row(cid):
    with SessionLocal() as db:
        return db.get(MondayChangeDB, cid)


# ------------------------------------------------------------------ staging: nothing is sent


def test_every_write_is_staged_and_nothing_reaches_monday_while_read_only(mon):
    c, http = mon
    assert c.post_pulse_update(555, "Shipped 12 Nov") is True and c.update_column_values(1, 555, {"status": {"label": "Done"}}) is True
    assert http.sent == []                                                                    # not one request, not even a blocked one
    got = monday_push.changes("STAGED")
    assert {g["kind"] for g in got} == {"post_update", "update_columns"} and all(g["item_id"] == "555" for g in got)
    s = monday_push.status()
    assert s["staged"] == 2 and s["read_only"] and not s["can_push"] and "read-only" in s["detail"]


def test_staging_the_same_thing_twice_leaves_one_change_with_the_latest_values(mon):
    c, http = mon
    c.update_column_values(1, 555, {"status": {"label": "Working"}})
    c.update_column_values(1, 555, {"status": {"label": "Done"}, "date4": {"date": "2026-11-20"}})
    c.update_column_values(1, 556, {"status": {"label": "Stuck"}})
    c.post_pulse_update(555, "same comment")
    c.post_pulse_update(555, "same comment")
    c.post_pulse_update(555, "a different comment")
    staged = monday_push.changes("STAGED")
    cols = [g for g in staged if g["kind"] == "update_columns"]
    assert len(cols) == 2 and next(g for g in cols if g["item_id"] == "555")["payload"]["columns"] == {"status": {"label": "Done"}, "date4": {"date": "2026-11-20"}}
    assert len([g for g in staged if g["kind"] == "post_update"]) == 2


def test_nothing_is_staged_when_monday_is_not_connected(monkeypatch, client):
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", True)
    c = MondayClient(api_key="test-token")
    assert c.post_pulse_update(555, "x") is True
    assert not [g for g in monday_push.changes("STAGED") if g["item_id"] == "555"]


def test_if_staging_is_impossible_the_normal_guarded_path_still_applies(mon, monkeypatch):
    c, http = mon
    monkeypatch.setattr(monday_push, "stage", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such table: monday_changes")))
    assert c.post_pulse_update(555, "hello") is True                                           # blocked by the read-only guard, as before
    assert http.sent == [] and any(x.get("blocked") == "read-only" for x in c.simulated_calls)


# ------------------------------------------------------------------ looking before pushing


def test_the_preview_shows_what_monday_shows_now_and_what_would_change(mon):
    c, http = mon
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "2026-11-07T08:00:00",
                                 "groups": [], "columns": [{"id": "status", "title": "Status", "type": "status"}, {"id": "eta", "title": "ETA", "type": "text"}],
                                 "items": [{"id": "555", "name": "KINGCERA cyclone", "values": {"status": "FABRICATION", "eta": "11/12"}}]}
    try:
        c.update_column_values(board, 555, {"status": {"label": "SHIPPED"}, "eta": "11/20"})
        c.post_pulse_update(555, "KINGCERA says it ships 20 Nov")
        p = {x["kind"]: x for x in monday_push.preview()}
        change = p["update_columns"]
        assert change["board"] == "2026 Orders" and change["item"] == "KINGCERA cyclone"
        assert {x["column"]: (x["now"], x["becomes"]) for x in change["changes"]} == {"Status": ("FABRICATION", "SHIPPED"), "ETA": ("11/12", "11/20")}
        assert p["post_update"]["comment"].startswith("KINGCERA says")
    finally:
        with boards._lock:
            boards._mirror.pop(board, None)
    assert http.sent == []


# ------------------------------------------------------------------ pushing


def test_a_push_while_read_only_sends_nothing_says_so_and_changes_nothing_else(mon):
    c, http = mon
    cid = monday_push.stage("post_update", "", 555, {"text": "hi"}, "hi")
    out = monday_push.push(who="boss@allied.test")
    assert out["ok"] is False and out["blocked"] == "read-only" and out["pushed"] == 0 and out["staged"] >= 1 and "read-only" in out["message"]
    assert http.sent == [] and _row(cid).status == "STAGED" and settings.MONDAY_READ_ONLY is True        # and the switch is exactly as it was


def test_a_push_when_writing_is_on_sends_the_staged_changes_in_order_and_marks_them_pushed(mon, monkeypatch):
    c, http = mon
    c.update_column_values(1, 555, {"status": {"label": "Done"}})
    c.post_pulse_update(555, "done and shipped")
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    dry = monday_push.push(dry_run=True)
    assert dry["dry_run"] and len(dry["would_push"]) == 2 and http.sent == []
    out = monday_push.push(who="boss@allied.test")
    assert out["ok"] and out["pushed"] == 2 and out["failed"] == 0
    assert len(_mutations(http)) == 2 and "change_multiple_column_values" in _mutations(http)[0] and "create_update" in _mutations(http)[1]
    assert all(g["status"] == "PUSHED" for g in monday_push.changes() if g["item_id"] == "555")
    assert monday_push.push()["message"] == "Nothing is staged."                                          # a second push has nothing left to send


def test_a_failed_change_does_not_stop_the_others_and_can_be_retried_or_skipped(mon, monkeypatch):
    c, http = mon
    http.fail_on = "create_update"
    a = monday_push.stage("post_update", "", 555, {"text": "will fail"}, "will fail")
    b = monday_push.stage("update_columns", 1, 555, {"columns": {"status": {"label": "Done"}}}, "set status")
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    out = monday_push.push()
    assert out["pushed"] == 1 and out["failed"] == 1 and not out["ok"]
    assert _row(a).status == "FAILED" and _row(a).error and _row(b).status == "PUSHED"
    http.fail_on = None
    monday_push.retry(a)
    assert monday_push.push()["pushed"] == 1 and _row(a).status == "PUSHED"
    d = monday_push.stage("post_update", "", 9, {"text": "drop me"}, "drop me")
    assert monday_push.skip(d)["status"] == "SKIPPED" and monday_push.push()["message"] == "Nothing is staged."
    with pytest.raises(ValueError):
        monday_push.skip(d)
    with pytest.raises(ValueError):
        monday_push.retry(b)


def test_a_chosen_few_can_be_pushed_and_the_rest_stay_staged(mon, monkeypatch):
    c, http = mon
    a = monday_push.stage("post_update", "", 1, {"text": "one"}, "one")
    b = monday_push.stage("post_update", "", 2, {"text": "two"}, "two")
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    assert monday_push.push([a])["pushed"] == 1 and _row(a).status == "PUSHED" and _row(b).status == "STAGED"


def test_auto_mode_sends_straight_away_when_writing_is_on_and_stages_when_it_is_off(mon, monkeypatch):
    c, http = mon
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "auto")
    c.post_pulse_update(555, "while read-only")
    assert http.sent == [] and monday_push.status()["staged"] == 1                                  # read-only: kept, not lost, not sent
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    c.post_pulse_update(556, "writing is on")
    assert len(_mutations(http)) == 1 and "create_update" in _mutations(http)[0]


def test_old_staged_changes_expire(mon):
    from datetime import datetime, timedelta, timezone

    cid = monday_push.stage("post_update", "", 555, {"text": "old"}, "old")
    fresh = monday_push.stage("post_update", "", 556, {"text": "fresh"}, "fresh")
    with SessionLocal() as db:
        db.get(MondayChangeDB, cid).created_at = datetime.now(timezone.utc) - timedelta(days=settings.MONDAY_STAGE_DAYS + 2)
        db.commit()
    assert monday_push.expire_old() == 1 and _row(cid).status == "EXPIRED" and _row(fresh).status == "STAGED"


# ------------------------------------------------------------------ the RFQ row goes through the same queue


def test_the_reviewed_rfq_row_is_staged_then_pushed_and_linked_to_its_rfq(mon, monkeypatch):
    from app.services.assistant import tools

    c, http = mon
    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", "1657738509")
    r = rfqs.create({"customer": "Knauf Shasta", "subject": "D cone liner x2", "contact_email": "buyer@knauf.test"}, {"email": "intake", "role": "SYSTEM"}, source="email", announce=False)
    out = tools.add_rfq_to_monday({"item_name": "D cone liner x2", "customer": "Knauf Shasta", "status": "", "notes": "Please quote 2"}, {"email": "boss@allied.test"})
    assert "Staged (not sent yet)" in out["done"] and "read-only" in out["done"] and http.sent == []
    from app.services.coordination.autopilot import after_done

    after_done({"_play": "rfq_board_row", "_rfq_id": r["id"]}, {"email": "boss@allied.test", "role": "MANAGER"}, out)
    staged = monday_push.changes("STAGED")[0]
    assert staged["kind"] == "create_item" and staged["payload"]["rfq_id"] == r["id"] and staged["payload"]["item_name"] == "D cone liner x2"
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    assert monday_push.push()["pushed"] == 1
    assert rfqs.get(r["id"])["monday_item_id"] == "7771"
    sent_vars = json.loads(next(v for q, v in http.sent if "create_item" in q)["columnValues"])
    assert any("Knauf" in json.dumps(v) for v in sent_vars.values())                               # the customer went in as a dropdown/label value


def test_the_row_text_is_returned_when_monday_is_not_connected_so_it_can_be_added_by_hand(monkeypatch, client):
    from app.services.assistant import tools

    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", True)
    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", "1657738509")
    out = tools.add_rfq_to_monday({"item_name": "Liner", "customer": "Vulcan"}, {"email": "x@y.z"})
    assert "isn't connected" in out["done"] and "Liner" in out["done"] and "change_id" not in out


# ------------------------------------------------------------------ the API


def test_the_api_roles_and_a_push_while_read_only(mon, auth_enforced, client):
    def tok(role, n):
        return {"Authorization": f"Bearer {staff_token(f'{n}{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', role)}"}

    c, http = mon
    v, o, m = tok("VIEWER", "pv"), tok("OPERATOR", "po"), tok("MANAGER", "pm")
    cid = monday_push.stage("post_update", "", 555, {"text": "hi"}, "hi")
    assert client.get("/api/v1/monday-push/status").status_code in (401, 403)
    assert client.get("/api/v1/monday-push/status", headers=v).json()["staged"] >= 1
    assert client.get("/api/v1/monday-push/changes", params={"state": "STAGED"}, headers=v).json()[0]["id"] == cid
    assert client.get("/api/v1/monday-push/changes", params={"state": "nonsense"}, headers=v).status_code == 422
    assert client.get("/api/v1/monday-push/preview", headers=v).status_code == 403
    assert client.get("/api/v1/monday-push/preview", params={"ids": str(cid)}, headers=o).json()[0]["id"] == cid
    assert client.get("/api/v1/monday-push/preview", params={"ids": "a,b"}, headers=o).status_code == 400
    assert client.post("/api/v1/monday-push/push", json={}, headers=o).status_code == 403
    r = client.post("/api/v1/monday-push/push", json={}, headers=m)
    assert r.status_code == 200 and r.json()["blocked"] == "read-only" and http.sent == []
    assert client.post("/api/v1/monday-push/push", json={"ids": "all"}, headers=m).status_code == 400
    assert client.post(f"/api/v1/monday-push/changes/{cid}/retry", headers=o).status_code == 403
    assert client.post(f"/api/v1/monday-push/changes/{cid}/retry", headers=m).status_code == 400
    assert client.post(f"/api/v1/monday-push/changes/{cid}/skip", headers=o).status_code == 403          # an operator may skip only what they staged themselves
    assert client.post(f"/api/v1/monday-push/changes/{cid}/skip", headers=m).json()["status"] == "SKIPPED"
    assert client.post("/api/v1/monday-push/changes/99999999/skip", headers=o).status_code == 404
    assert settings.MONDAY_READ_ONLY is True

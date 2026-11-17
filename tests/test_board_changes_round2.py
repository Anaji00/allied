"""Round 2 hardening of the git-like board changes: nothing is overwritten, nothing is sent twice. Fake monday client throughout."""
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest  # noqa: F401

from app.core.database import SessionLocal
from app.services import monday_push
from app.services.coordination import boards
from tests.test_board_changes import FakeMonday, OP, _empty_staging, _two_boards, live  # noqa: F401


def test_an_edit_staged_by_any_code_remembers_the_cell_as_the_app_saw_it_so_a_later_fill_is_a_conflict(live):  # noqa: F811
    a, _ = _two_boards()
    with boards._lock:
        boards._mirror[a]["items"][0]["values"]["notes"] = ""
    cid = monday_push.stage("update_columns", a, "1", {"columns": {"Notes": "from a packet"}}, "packet fact", source="packet")
    ch = [c for c in monday_push.changes("STAGED") if c["id"] == cid][0]
    assert ch["payload"]["base"] == {"notes": ""}
    live.live_values = {("1", "notes"): "someone typed this on monday meanwhile"}
    out = monday_push.push(who="boss")
    assert out["conflicts"] == 1 and live.sent == []


def test_an_old_staged_edit_with_no_base_is_treated_as_made_against_a_blank_cell(live):  # noqa: F811
    a, _ = _two_boards()
    cid = monday_push.stage_edit(a, "1", {"Notes": "old style"}, OP)
    with SessionLocal() as db:
        row = db.get(monday_push.MondayChangeDB, cid)
        row.payload = {"columns": row.payload["columns"]}
        db.commit()
    live.live_values = {("1", "notes"): "already filled"}
    assert monday_push.push(who="boss")["conflicts"] == 1 and live.sent == []
    monday_push.rebase(cid)
    live.live_values = {("1", "notes"): ""}
    assert monday_push.push(who="boss")["pushed"] == 1
    cid2 = monday_push.stage_edit(a, "1", {"Notes": "same as monday"}, OP)
    with SessionLocal() as db:
        row = db.get(monday_push.MondayChangeDB, cid2)
        row.payload = {"columns": row.payload["columns"]}
        db.commit()
    live.live_values = {("1", "notes"): "same as monday"}
    assert monday_push.push(who="boss")["conflicts"] == 0


def test_two_pushes_at_the_same_moment_send_each_change_once(live):  # noqa: F811
    a, _ = _two_boards()
    monday_push.stage_edit(a, "1", {"Notes": "once only"}, OP)
    monday_push.stage_new_item(a, "ONE ROW", {"Notes": "n"}, OP)
    live.live_values = {("1", "notes"): "old note"}
    orig = live.update_column_values

    def slow(*args, **kw):
        time.sleep(0.2)
        return orig(*args, **kw)

    live.update_column_values = slow
    outs = []
    ts = [threading.Thread(target=lambda: outs.append(monday_push.push(who="boss"))) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(o["pushed"] for o in outs) == 2
    assert len([s for s in live.sent if s[0] == "update"]) == 1 and len([s for s in live.sent if s[0] == "create"]) == 1


def test_a_push_that_died_half_way_is_failed_after_ten_minutes_so_it_can_be_retried(live):  # noqa: F811
    a, _ = _two_boards()
    cid = monday_push.stage_edit(a, "1", {"Notes": "stuck"}, OP)
    with SessionLocal() as db:
        row = db.get(monday_push.MondayChangeDB, cid)
        row.status, row.updated_at = "PUSHING", datetime.now(timezone.utc) - timedelta(minutes=30)
        db.commit()
    new = monday_push.stage_edit(a, "1", {"Status": "Need markup"}, OP)
    assert new != cid                                                       # a new edit never merges into a row that is mid-push
    live.live_values = {("1", "status"): "Stuck"}
    monday_push.push(who="boss")
    assert [c for c in monday_push.changes("FAILED") if c["id"] == cid][0]["error"].startswith("A push was interrupted")


def test_a_conflict_can_be_dropped_and_old_ones_expire(live):  # noqa: F811
    a, _ = _two_boards()
    cid = monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "theirs"}
    monday_push.push(who="boss")
    assert monday_push.skip(cid, "boss")["status"] == "SKIPPED"
    cid2 = monday_push.stage_edit(a, "1", {"Notes": "mine again"}, OP)
    monday_push.push(who="boss")
    with SessionLocal() as db:
        db.get(monday_push.MondayChangeDB, cid2).created_at = datetime.now(timezone.utc) - timedelta(days=90)
        db.commit()
    assert monday_push.expire_old() >= 1 and [c for c in monday_push.changes("EXPIRED") if c["id"] == cid2]


def test_a_double_click_on_new_row_is_one_row_and_odd_values_are_refused(client):
    _, b = _two_boards()
    for _ in range(3):
        assert client.post(f"/api/v1/boards/{b}/items", json={"item_name": "Same Row", "fields": {"Scan Date": "2026-12-01"}}).status_code == 200
    assert len([c for c in monday_push.changes("STAGED") if c["kind"] == "create_item" and c["payload"].get("item_name") == "Same Row"]) == 1
    for bad in ([1, 2], {"k": "v" * 5000}, {str(i): i for i in range(11)}):
        assert client.post(f"/api/v1/boards/{b}/items/9/changes", json={"columns": {"Scan Date": bad}}).status_code == 400
    assert client.post(f"/api/v1/boards/{b}/items/9/changes", json={"columns": {"Scan Date": 20261201}}).status_code == 400      # a number is not a date
    assert client.post(f"/api/v1/boards/{b}/items/9/changes", json={"columns": {"Scan Date": "2026-12-02"}}).status_code == 200


def test_spacing_differences_between_the_app_and_monday_are_not_false_conflicts(live):  # noqa: F811
    a, _ = _two_boards()
    with boards._lock:
        boards._mirror[a]["items"][0]["values"]["notes"] = "two   words"
    monday_push.stage_edit(a, "1", {"Notes": "new"}, OP)
    live.live_values = {("1", "notes"): "two words "}
    assert monday_push.push(who="boss")["pushed"] == 1

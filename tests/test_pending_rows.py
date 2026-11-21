"""A pending new row is a row: it sits in its group, takes every edit, and merges them into its single staged create. Fake monday, MONDAY_READ_ONLY on, temporary database."""
import time

import pytest

from app.config import settings
from app.services import actions, board_edit, monday_push
from app.services.coordination import boards
from tests.conftest import staff_token
from tests.test_board_edit import OP, TODAY, _Sender, _clean, board  # noqa: F401  (fixtures)

OP2 = {"email": "op2-pending@alliedweldingsystems.com", "role": "OPERATOR"}


def _creates():
    return [c for c in monday_push.changes("STAGED") if c["kind"] == "create_item"]


def test_a_new_row_comes_back_with_an_id_and_sits_inside_its_group_in_order(board):
    a = board_edit.add_row(board, "First new", "this", {"Status": "stuck"}, OP, TODAY)
    b = board_edit.add_row(board, "Second new", "this", {}, OP, TODAY)
    c = board_edit.add_row(board, "In done", "done and", {}, OP, TODAY)
    assert a["item_id"] == f"new:{a['staged_id']}" and a["staged_id"] == a["change_id"] and a["pending"]
    view = board_edit.board_items(board)
    ids = [r["id"] for r in view["items"]]
    assert ids == ["1", "2", a["item_id"], b["item_id"], c["item_id"]] and view["pending_rows"] == 3
    first = next(r for r in view["items"] if r["id"] == a["item_id"])
    assert first["pending"] is True and first["group"] == "g1" and first["values"]["status"] == "Stuck" and first["staged_id"] == a["staged_id"]
    assert next(r for r in view["items"] if r["id"] == "1")["pending"] is False


def test_a_cell_edit_on_a_pending_row_merges_into_its_single_create_and_is_validated_like_a_real_row(board):
    a = board_edit.add_row(board, "New", "this", {"Status": "stuck"}, OP, TODAY)
    r = board_edit.edit_cell(board, a["item_id"], "Status", "done", OP, TODAY)
    assert r["pending_row"] and r["becomes"] == "Done" and r["was"] == "Stuck" and r["item_id"] == a["item_id"]
    board_edit.edit_cell(board, a["item_id"], "Due", "fri", OP, TODAY)                              # a column the row did not have when it was made
    board_edit.edit_cell(board, a["item_id"], "Qty", "$1,200", OP, TODAY)
    assert len(monday_push.changes("STAGED")) == 1 and len(_creates()) == 1
    assert _creates()[0]["payload"]["fields"] == {"status": "Done", "due": "2026-11-20", "qty": "1200"}
    for col, text in (("Status", "banana"), ("Due", "someday"), ("Qty", "12,50")):
        with pytest.raises(ValueError):
            board_edit.edit_cell(board, a["item_id"], col, text, OP, TODAY)
    assert _creates()[0]["payload"]["fields"]["status"] == "Done"                                     # a refused edit changed nothing
    with pytest.raises(LookupError):
        board_edit.edit_cell(board, "new:999999", "Status", "done", OP, TODAY)
    with pytest.raises(LookupError):
        board_edit.edit_cell(board, "new:abc", "Status", "done", OP, TODAY)


def test_the_same_cell_twice_is_one_change_and_undo_steps_back_one_edit_at_a_time(board):
    a = board_edit.add_row(board, "Undo me", "this", {}, OP, TODAY)
    for text in ("stuck", "work", "done"):
        board_edit.edit_cell(board, a["item_id"], "Status", text, OP, TODAY)
    assert len(monday_push.changes("STAGED")) == 1 and _creates()[0]["payload"]["fields"]["status"] == "Done"
    board_edit.undo_last(OP, board)
    assert _creates()[0]["payload"]["fields"]["status"] == "Working on it"                           # back one edit, not the whole row
    board_edit.undo_last(OP, board)
    assert _creates()[0]["payload"]["fields"]["status"] == "Stuck"
    board_edit.undo_last(OP, board)
    assert "status" not in _creates()[0]["payload"]["fields"] and len(_creates()) == 1
    board_edit.undo_last(OP, board)                                                                   # nothing of mine left to step back on it: the row itself goes
    assert _creates() == []
    with pytest.raises(LookupError):
        board_edit.undo_last(OP, board)


def test_real_rows_undo_one_edit_at_a_time_too(board):
    for text in ("stuck", "work", "done"):
        board_edit.edit_cell(board, "2", "Status", text, OP, TODAY)
    board_edit.edit_cell(board, "2", "Due", "fri", OP, TODAY)
    board_edit.undo_last(OP, board)
    assert monday_push.changes("STAGED")[0]["payload"]["columns"] == {"status": "Done"}
    board_edit.undo_last(OP, board)
    assert monday_push.changes("STAGED")[0]["payload"]["columns"] == {"status": "Working on it"}


def test_each_person_undoes_only_their_own_edits_on_a_shared_pending_row(board):
    a = board_edit.add_row(board, "Shared", "this", {}, OP, TODAY)
    board_edit.edit_cell(board, a["item_id"], "Status", "stuck", OP, TODAY)
    board_edit.edit_cell(board, a["item_id"], "Notes", "from the second person", OP2, TODAY)
    board_edit.undo_last(OP2, board)
    f = _creates()[0]["payload"]["fields"]
    assert "notes" not in f and f["status"] == "Stuck"
    board_edit.undo_last(OP, board)
    assert _creates()[0]["payload"]["fields"] == {}


def test_paste_and_fill_down_walk_through_pending_rows_and_write_into_their_create(board):
    a = board_edit.add_row(board, "P1", "this", {}, OP, TODAY)
    b = board_edit.add_row(board, "P2", "this", {}, OP, TODAY)
    got = board_edit.paste(board, "2", "Status", [["done", "fri"], ["stuck", "+3d"], ["work", "nov 30"]], OP, today=TODAY)            # a real row, then two pending ones
    assert got["staged"] and got["rows_touched"] == 3
    by = {c["payload"]["item_name"]: c["payload"]["fields"] for c in _creates()}
    assert by["P1"] == {"status": "Stuck", "due": "2026-11-20"} and by["P2"] == {"status": "Working on it", "due": "2026-11-30"}
    board_edit.edit_cell(board, a["item_id"], "Notes", "hello", OP, TODAY)
    f = board_edit.fill_down(board, "Notes", a["item_id"], OP, through_item=b["item_id"], today=TODAY)
    assert f["staged"] and {c["payload"]["item_name"]: c["payload"]["fields"].get("notes") for c in _creates()} == {"P1": "hello", "P2": "hello"}
    assert len(_creates()) == 2


def test_duplicate_move_archive_and_comment_work_on_a_pending_row_and_undo_brings_a_dropped_row_back(board):
    a = board_edit.add_row(board, "Mover", "this", {"Status": "stuck"}, OP, TODAY)
    m = board_edit.move_row(board, a["item_id"], "done and", OP)
    assert m["group"] == "Done and paid" and _creates()[0]["payload"]["group_id"] == "g3" and len(_creates()) == 1
    d = board_edit.duplicate_row(board, a["item_id"], False, OP)
    assert d["item_id"].startswith("new:") and d["item_id"] != a["item_id"] and len(_creates()) == 2
    assert [c["payload"]["fields"] for c in _creates()] == [{"status": "Stuck"}] * 2
    c = board_edit.comment_row(board, a["item_id"], "Please check", OP)
    mover = next(x for x in _creates() if x["payload"]["item_name"] == "Mover")
    assert c["pending_row"] and "Please check" in mover["payload"]["comments"][0] and len(monday_push.changes("STAGED")) == 2
    gone = board_edit.archive_row(board, d["item_id"], OP)
    assert gone["dropped"] and len(_creates()) == 1 and board_edit.board_items(board)["pending_rows"] == 1
    with pytest.raises(LookupError, match="dropped"):
        board_edit.edit_cell(board, d["item_id"], "Status", "done", OP, TODAY)
    back = board_edit.undo_last(OP, board)
    assert back["item_id"] == d["item_id"] and len(_creates()) == 2 and board_edit.board_items(board)["pending_rows"] == 2


def test_add_rows_in_a_batch_and_adding_the_same_name_again_makes_another_row(board):
    out = board_edit.add_rows(board, ["Alpha", "Beta", "Alpha"], "this", {"Status": "stuck"}, OP, TODAY)
    assert out["count"] == 3 and len({r["item_id"] for r in out["rows"]}) == 3 and all(r["item_id"].startswith("new:") for r in out["rows"]) and len(_creates()) == 3
    one = board_edit.add_row(board, "Same", None, {}, OP, TODAY)
    twin = board_edit.add_row(board, "Same", None, {}, OP, TODAY)                                       # a double click: the same row
    assert twin["item_id"] == one["item_id"]
    keyed = [board_edit.add_row(board, "K", None, {}, OP, TODAY, client_key="abc")["item_id"] for _ in range(2)]
    assert keyed[0] == keyed[1]
    time.sleep(3.2)
    later = board_edit.add_row(board, "Same", None, {}, OP, TODAY)                                      # later, on purpose: another row
    assert later["item_id"] != one["item_id"]
    for bad in ([], "", ["ok", " "], ["x"] * 101, [1]):
        with pytest.raises(ValueError):
            board_edit.add_rows(board, bad, None, {}, OP, TODAY)
    with pytest.raises(ValueError):
        board_edit.add_rows(board, ["Fine"], "zzz", {}, OP, TODAY)                                      # a bad group stages none
    with pytest.raises(ValueError):
        board_edit.add_rows(board, ["Fine"], None, {"Status": "banana"}, OP, TODAY)


def test_a_pending_row_can_be_commented_shared_and_put_on_another_board(board):
    a = board_edit.add_row(board, "To share", "this", {"Status": "stuck"}, OP, TODAY)
    ref = f"{board}:{a['item_id']}"
    ctx = actions.context("row", ref)
    assert ctx["title"].endswith("To share") and any(v == "Stuck" for _l, v, _a in ctx["rows"])
    out = actions.share("row", ref, "Look at this row", None, OP)
    assert out["staged"] and "Look at this row" in _creates()[0]["payload"]["comments"][0]
    from app.services import create_anything

    other = "9" + board[1:]
    with boards._lock:
        boards._mirror[other] = {"board_id": other, "name": "Other", "role": "reference", "read_at": "now", "groups": [], "columns": [{"id": "t", "title": "Row", "type": "text"}], "items": []}
    try:
        plan = create_anything.to_monday_stage("row", ref, other, None, OP, dry_run=True)
        assert plan["dry_run"] and plan["name"].endswith("To share")
    finally:
        with boards._lock:
            boards._mirror.pop(other, None)


def test_a_pushed_pending_row_is_replaced_by_the_real_id_and_its_comments_go_on_the_real_row(board, monkeypatch):
    class Sender(_Sender):
        def post_pulse_update(self, item, text, idempotency_key=None):
            self.sent.append(("comment", str(item), text))
            return True

        def create_item(self, board_id, name, cols, key=None, group_id=None):
            self.sent.append(("create", name, cols, group_id))
            return "777001"

    sender = Sender()
    a = board_edit.add_row(board, "Pushed", "this", {"Status": "stuck"}, OP, TODAY)
    board_edit.edit_cell(board, a["item_id"], "Qty", "5", OP, TODAY)
    board_edit.comment_row(board, a["item_id"], "Note for the real row", OP)
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)                                      # the fake sender only: the real setting is never touched
    out = monday_push.push(None, who="boss@alliedweldingsystems.com", board_id=board)
    res = next(r for r in out["results"] if r.get("monday_item_id"))
    assert out["ok"] and res["monday_item_id"] == "777001" and res["staged_item_id"] == a["item_id"]
    assert ("create", "Pushed", {"status": {"label": "Stuck"}, "qty": "5"}, "g1") in sender.sent
    assert any(x[0] == "comment" and x[1] == "777001" and "Note for the real row" in x[2] for x in sender.sent)
    rows = board_edit.board_items(board)["items"]
    real = next(r for r in rows if r["id"] == "777001")
    assert real["pending"] is False and real["name"] == "Pushed" and real["group"] == "g1" and real["values"]["qty"] == "5" and not any(r["pending"] for r in rows)
    assert board_edit._item(board, a["item_id"])["id"] == "777001"                                   # the old id still finds the row, now the real one
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    got = board_edit.edit_cell(board, a["item_id"], "Status", "done", OP, TODAY)                     # and an edit through it is a normal staged edit of the real row
    assert got["item_id"] == "777001" and not got["pending_row"]


def test_over_http_the_items_list_and_the_board_view_carry_pending_rows_and_roles_hold(client, auth_enforced, board):
    H = lambda e, r: {"Authorization": f"Bearer {staff_token(e, r)}"}                              # noqa: E731
    op, viewer = H("o-pend@alliedweldingsystems.com", "OPERATOR"), H("v-pend@alliedweldingsystems.com", "VIEWER")
    url = f"/api/v1/board-edit/boards/{board}"
    made = client.post(f"{url}/items", json={"names": ["One", "Two"], "group": "this"}, headers=op).json()
    assert made["count"] == 2 and made["rows"][0]["item_id"].startswith("new:")
    single = client.post(f"{url}/items", json={"name": "Three", "group": "this", "client_key": "k1"}, headers=op).json()
    assert single["item_id"].startswith("new:") and client.post(f"{url}/items", json={"name": "Three", "client_key": "k1"}, headers=op).json()["item_id"] == single["item_id"]
    r = client.post(f"{url}/items/{single['item_id']}/cell", json={"column": "Status", "text": "done"}, headers=op).json()
    assert r["pending_row"] and r["becomes"] == "Done"
    assert client.post(f"{url}/items/{single['item_id']}/comment", json={"text": "hi"}, headers=op).status_code == 200
    assert client.post(f"{url}/items/{single['item_id']}/comment", json={"text": "hi"}, headers=viewer).status_code == 403
    listed = client.get(f"{url}/items", headers=viewer).json()
    assert listed["pending_rows"] == 3 and [i["pending"] for i in listed["items"]] == [False, False, True, True, True]
    view = client.get(f"/api/v1/boards/{board}", headers=viewer).json()
    assert [i["id"] for i in view["items"]][:2] == ["1", "2"] and sum(1 for i in view["items"] if i.get("pending")) == 3 and view["pending_rows"] == 3
    assert client.post(f"{url}/items/{single['item_id']}/archive", headers=op).json()["dropped"] is True
    assert client.post(f"{url}/items/new:999999/cell", json={"column": "Status", "text": "done"}, headers=op).status_code == 404
    assert client.post("/api/v1/board-edit/undo", json={"board_id": board}, headers=op).json()["item_id"] == single["item_id"]

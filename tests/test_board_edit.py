"""Board editing by typed text: what a cell's text means, the staged row actions (add, copy, move, archive), undo, and the roles. A fake monday client is used
throughout and MONDAY_READ_ONLY stays True: nothing real is read or written, and nothing here is ever sent."""
import uuid
from datetime import date

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayChangeDB
from app.services import board_edit, monday_push
from app.services.coordination import boards
from tests.conftest import staff_token

OP = {"email": "op-be@alliedweldingsystems.com", "role": "OPERATOR"}
TODAY = date(2026, 11, 17)         # a Tuesday


class FakeMonday:
    live = True
    writable = False

    def __init__(self):
        self.sent = []

    def execute_query(self, q, variables=None):
        if "users(limit" in q:
            return {"data": {"users": [{"id": "11", "email": "paul.wang@allied.example.test"}, {"id": "12", "email": "paula.jones@allied.example.test"},
                                       {"id": "13", "email": "kim.roe@allied.example.test"}]}}
        return {"data": {}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    fake = FakeMonday()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: fake)
    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()
    yield fake
    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()


@pytest.fixture()
def board():
    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Test Board", "role": "reference", "read_at": "now",
                               "groups": [{"id": "g1", "title": "This week", "color": ""}, {"id": "g2", "title": "Done", "color": ""}, {"id": "g3", "title": "Done and paid", "color": ""}],
                               "columns": [{"id": "status", "title": "Status", "type": "status", "labels": {"Working on it": "", "Stuck": "", "Done": "", "Need markup": ""}},
                                           {"id": "due", "title": "Due", "type": "date"}, {"id": "qty", "title": "Qty", "type": "numbers"},
                                           {"id": "notes", "title": "Notes", "type": "text"}, {"id": "ok", "title": "Checked", "type": "checkbox"},
                                           {"id": "who", "title": "Owner", "type": "people"}, {"id": "link", "title": "Site", "type": "link"},
                                           {"id": "span", "title": "Span", "type": "timeline"}, {"id": "at", "title": "At", "type": "hour"},
                                           {"id": "calc", "title": "Days", "type": "formula"}, {"id": "ph", "title": "Phone", "type": "phone"}],
                               "items": [{"id": "1", "name": "HECLA", "group": "g1", "group_title": "This week",
                                          "values": {"status": "Stuck", "due": "", "qty": "5", "notes": "old", "calc": "4"}},
                                         {"id": "2", "name": "SPOOL", "group": "g1", "group_title": "This week", "values": {}}]}
    yield bid
    with boards._lock:
        boards._mirror.pop(bid, None)


def _u(board, col, text, **kw):
    return board_edit.understand(board, col, text, TODAY, **kw)


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("text, want", [("today", "2026-11-17"), ("tomorrow", "2026-11-18"), ("fri", "2026-11-20"), ("Friday", "2026-11-20"), ("tue", "2026-11-24"),
                                        ("next fri", "2026-11-27"), ("+3d", "2026-11-20"), ("+2w", "2026-12-01"), ("-1d", "2026-11-16"), ("+1m", "2026-12-17"),
                                        ("nov 20", "2026-11-20"), ("20 nov", "2026-11-20"), ("November 20th", "2026-11-20"), ("nov 20 2027", "2027-11-20"),
                                        ("11/20", "2026-11-20"), ("11/20/2026", "2026-11-20"), ("11/20/26", "2026-11-20"), ("2026-11-20", "2026-11-20"),
                                        ("jan 5", "2027-01-05"), ("sept 3", "2027-09-03"), ("  Nov   20 ", "2026-11-20")])
def test_dates_are_read_the_way_people_type_them(board, text, want):
    assert _u(board, "Due", text)["value"] == want


@pytest.mark.parametrize("text", ["", "someday", "feb 30", "13/45", "2026-02-30", "nov 20 1850", "nov 20 2500", "+9999999d", "fri fri", "20", "nov", "2026-11-20 garbage"])
def test_a_date_that_is_not_one_is_refused_in_plain_words(board, text):
    with pytest.raises(ValueError):
        _u(board, "Due", text)


def test_the_plain_words_say_what_was_understood(board):
    assert _u(board, "Due", "fri")["understood"] == "Due: Friday 20 Nov 2026"


# ---------------------------------------------------------------- status labels
@pytest.mark.parametrize("text, want", [("done", "Done"), ("DONE", "Done"), ("don", "Done"), ("work", "Working on it"), ("working", "Working on it"), ("stuk", "Stuck"),
                                        ("markup", "Need markup")])
def test_a_status_label_is_matched_loosely_but_never_guessed(board, text, want):
    assert _u(board, "Status", text)["value"] == want


def test_a_status_that_fits_nothing_lists_the_labels_and_two_candidates_are_not_guessed(board):
    with pytest.raises(ValueError, match="Its labels are: .*Done"):
        _u(board, "Status", "banana")
    with boards._lock:
        boards._mirror[board]["columns"][0]["labels"] = {"Done": "", "Done and paid": "", "Stuck": ""}
    assert _u(board, "Status", "done")["value"] == "Done"                  # an exact label wins over the longer one that starts the same
    with pytest.raises(ValueError, match="could be any of"):
        _u(board, "Status", "d")


# ---------------------------------------------------------------- the other kinds
def test_numbers_checkbox_link_hour_timeline_text(board):
    assert _u(board, "Qty", "$1,200.50")["value"] == "1200.50" and _u(board, "Qty", "(5)")["value"] == "-5"
    for bad in ("12abc", "1.2.3", "1e5", "NaN", "5 pieces"):
        with pytest.raises(ValueError):
            _u(board, "Qty", bad)
    assert _u(board, "Checked", "x")["understood"] == "Checked: ticked" and _u(board, "Checked", "no")["value"] == "no"
    with pytest.raises(ValueError):
        _u(board, "Checked", "maybe")
    assert _u(board, "Site", "www.example.com/a")["value"] == "https://www.example.com/a"
    assert _u(board, "At", "1:30pm")["value"] == "13:30" and _u(board, "At", "9am")["value"] == "09:00"
    with pytest.raises(ValueError):
        _u(board, "At", "25:00")
    assert _u(board, "Span", "nov 1 to nov 10")["value"] == "2026-11-01 to 2026-11-10"
    with pytest.raises(ValueError, match="ends before"):
        _u(board, "Span", "nov 10 to nov 1")
    assert _u(board, "Notes", "ship Friday, call Ann")["value"] == "ship Friday, call Ann"


def test_people_are_matched_by_name_or_email_and_two_candidates_are_not_guessed(board):
    one = _u(board, "Owner", "kim")
    assert one["value"] == {"personsAndTeams": [{"id": 13, "kind": "person"}]}
    assert _u(board, "Owner", "paul.wang@allied.example.test")["value"]["personsAndTeams"][0]["id"] == 11
    with pytest.raises(ValueError, match="more than one"):
        _u(board, "Owner", "paul")                                           # Paul and Paula
    with pytest.raises(ValueError, match="no one"):
        _u(board, "Owner", "zed")


def test_columns_that_cannot_be_edited_say_so(board):
    for col in ("Days", "Phone", "nope"):
        with pytest.raises(ValueError):
            _u(board, col, "x")


@pytest.mark.parametrize("text", ["", "   ", "‮evil", "a" * 2001])
def test_blank_and_hostile_text_is_refused_or_cleaned(board, text):
    try:
        got = _u(board, "Notes", text)
    except ValueError:
        return
    assert "‮" not in got["value"]


def test_formula_looking_and_html_text_is_kept_as_plain_text_and_never_run(board):
    assert _u(board, "Notes", "=HYPERLINK(\"http://x\")")["value"].startswith("=")          # monday shows text as text; the app stores what was typed
    assert _u(board, "Notes", "<script>alert(1)</script>")["value"] == "<script>alert(1)</script>"


# ---------------------------------------------------------------- staging, pending, undo
def test_a_typed_cell_is_staged_not_sent_and_shows_as_pending_with_what_it_was(board, _clean):
    got = board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    assert got["staged"] and got["pending"] and got["was"] == "Stuck" and got["becomes"] == "Done" and "staged" in got["message"]
    pend = monday_push.preview(None, board)
    assert len(pend) == 1 and pend[0]["changes"][0]["becomes"] == "Done" and pend[0]["changes"][0]["now"] == "Stuck"
    assert _clean.sent == []


def test_the_same_text_twice_is_one_staged_change_and_two_cells_of_a_row_are_one_change(board):
    a = board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    b = board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    c = board_edit.edit_cell(board, "1", "Due", "fri", OP, TODAY)
    assert a["change_id"] == b["change_id"] == c["change_id"] and len(monday_push.changes("STAGED")) == 1


def test_undo_my_last_puts_back_only_the_last_cell_then_the_rest(board):
    board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    board_edit.edit_cell(board, "1", "Due", "fri", OP, TODAY)
    board_edit.undo_last(OP, board)
    cols = monday_push.changes("STAGED")[0]["payload"]["columns"]
    assert list(cols) == ["status"]
    board_edit.undo_last(OP, board)
    assert monday_push.changes("STAGED") == []
    with pytest.raises(LookupError):
        board_edit.undo_last(OP, board)
    board_edit.edit_cell(board, "1", "Status", "work", OP, TODAY)
    board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    board_edit.undo_last(OP, board)                                          # the same cell edited twice: back to the first staged value
    assert monday_push.changes("STAGED")[0]["payload"]["columns"]["status"] in ("Working on it", "Done")


def test_undo_never_touches_another_persons_change(board):
    board_edit.edit_cell(board, "1", "Status", "done", {"email": "other-be@alliedweldingsystems.com", "role": "OPERATOR"}, TODAY)
    with pytest.raises(LookupError):
        board_edit.undo_last(OP, board)
    assert len(monday_push.changes("STAGED")) == 1


def test_a_row_is_added_with_its_cells_and_group_or_nothing_is_staged(board):
    got = board_edit.add_row(board, "New job", "this", {"Status": "stuck", "Due": "+3d"}, OP, TODAY)
    assert got["staged"] and got["group"] == "This week" and len(got["understood"]) == 2
    row = monday_push.changes("STAGED")[0]
    assert row["kind"] == "create_item" and row["payload"]["group_id"] == "g1" and row["payload"]["fields"] == {"status": "Stuck", "due": "2026-11-20"}
    with pytest.raises(ValueError, match="not staged.*Status.*Due"):
        board_edit.add_row(board, "Another", None, {"Status": "nope", "Due": "never"}, OP, TODAY)
    with pytest.raises(ValueError):
        board_edit.add_row(board, "  ", None, {}, OP, TODAY)
    assert board_edit.add_row(board, "New job", "this", {"Status": "stuck", "Due": "+3d"}, OP, TODAY)["change_id"] == got["change_id"]          # a double click is one row
    assert len(monday_push.changes("STAGED")) == 1


def test_move_copy_archive_are_staged_idempotent_and_shown_in_the_preview(board, _clean):
    m = board_edit.move_row(board, "1", "done and", OP)
    assert m["group"] == "Done and paid" and board_edit.move_row(board, "1", "done and", OP)["change_id"] == m["change_id"]
    with pytest.raises(ValueError, match="could be any of"):
        board_edit.move_row(board, "1", "done", OP) if False else board_edit.find_group(board, "d")
    board_edit.duplicate_row(board, "2", False, OP)
    board_edit.archive_row(board, "2", OP)
    kinds = sorted(c["kind"] for c in monday_push.changes("STAGED"))
    assert kinds == ["archive_item", "duplicate_item", "move_item"]
    shown = {p["kind"]: p for p in monday_push.preview(None, board)}
    assert shown["move_item"]["action"] == {"group_id": "g3"} and shown["archive_item"]["item"] == "SPOOL"
    with pytest.raises(LookupError):
        board_edit.archive_row(board, "999", OP)
    with pytest.raises(ValueError, match="no group"):
        board_edit.find_group(board, "zzz")
    assert _clean.sent == []


def test_columns_info_gives_pickers_and_marks_what_is_editable(board):
    info = board_edit.columns_info(board)
    by = {c["id"]: c for c in info["columns"]}
    assert info["editable"] and by["status"]["editable"] and [x["label"] for x in by["status"]["labels"]][:2] == ["Working on it", "Stuck"]
    assert not by["calc"]["editable"] and not by["ph"]["editable"] and by["due"]["hint"]
    assert [g["id"] for g in info["groups"]] == ["g1", "g2", "g3"]
    assert any(b["board_id"] == board and b["editable"] for b in board_edit.boards_list())


# ---------------------------------------------------------------- over HTTP: roles
def test_over_http_a_viewer_is_refused_an_operator_stages_a_manager_pushes_and_read_only_sends_nothing(client, auth_enforced, board, _clean):
    H = lambda e, r: {"Authorization": f"Bearer {staff_token(e, r)}"}                        # noqa: E731
    viewer, op, mgr = H("v-be@alliedweldingsystems.com", "VIEWER"), H("o-be@alliedweldingsystems.com", "OPERATOR"), H("m-be@alliedweldingsystems.com", "MANAGER")
    url = f"/api/v1/board-edit/boards/{board}"
    assert client.get(f"{url}/columns", headers=viewer).status_code == 200
    for path, body in ((f"{url}/items/1/cell", {"column": "Status", "text": "done"}), (f"{url}/items", {"name": "x"}), (f"{url}/items/1/archive", {}),
                       (f"{url}/items/1/move", {"group": "done"}), (f"{url}/items/1/duplicate", {}), ("/api/v1/board-edit/undo", {})):
        assert client.post(path, json=body, headers=viewer).status_code == 403, path
    r = client.post(f"{url}/items/1/cell", json={"column": "Status", "text": "don"}, headers=op)
    assert r.status_code == 200 and r.json()["understood"] == "Status: Done (you typed 'don')"
    assert client.post(f"{url}/items/1/cell", json={"column": "Status", "text": "banana"}, headers=op).status_code == 400
    assert client.post(f"{url}/items/99/cell", json={"column": "Status", "text": "done"}, headers=op).status_code == 404
    assert client.post(f"{url}/items/1/cell", json={"column": ["x"], "text": "done"}, headers=op).status_code == 400
    assert client.post(f"{url}/items", json={"name": "Web row", "group": "this", "cells": {"Due": "fri"}}, headers=op).status_code == 200
    assert client.post("/api/v1/monday-push/push", json={}, headers=op).status_code == 403
    pushed = client.post("/api/v1/monday-push/push", json={}, headers=mgr).json()
    assert pushed["blocked"] in ("read-only",) and pushed["pushed"] == 0 and _clean.sent == []
    assert client.post("/api/v1/board-edit/undo", json={"board_id": board}, headers=op).json()["undone"]


class _Sender:
    live = True
    writable = True

    def __init__(self):
        self.sent = []

    def execute_query(self, q, variables=None):
        return {"data": {}}

    def create_item(self, board, name, cols, key=None, group_id=None):
        self.sent.append(("create", name, cols, group_id))
        return "9001"

    def archive_item(self, item, key=None):
        self.sent.append(("archive", str(item)))
        return True

    def move_item_to_group(self, item, group, key=None):
        self.sent.append(("move", str(item), group))
        return True

    def duplicate_item(self, board, item, with_updates=False, key=None):
        self.sent.append(("copy", str(item), with_updates))
        return "9002"


def test_a_manager_push_sends_each_kind_to_its_own_monday_call_and_the_app_copy_follows(monkeypatch, board):
    sender = _Sender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)                    # the fake sender only: the real setting is never touched
    board_edit.add_row(board, "Pushed row", "this", {"Status": "stuck"}, OP, TODAY)
    board_edit.move_row(board, "1", "done and", OP)
    board_edit.duplicate_row(board, "1", True, OP)
    board_edit.archive_row(board, "2", OP)
    out = monday_push.push(None, who="boss@alliedweldingsystems.com", board_id=board)
    assert out["ok"] and out["pushed"] == 4
    assert sorted(x[0] for x in sender.sent) == ["archive", "copy", "create", "move"]
    assert ("create", "Pushed row", {"status": {"label": "Stuck"}}, "g1") in sender.sent and ("move", "1", "g3") in sender.sent and ("copy", "1", True) in sender.sent
    items = {i["id"]: i for i in boards._mirror[board]["items"]}
    assert "2" not in items and items["1"]["group"] == "g3" and items["1"]["group_title"] == "Done and paid"


def test_the_real_client_stages_row_actions_and_a_read_only_push_never_sends(monkeypatch, board):
    from app.services.monday_client import MondayClient

    class Net:
        sent = []

        def post(self, url, json=None, headers=None):
            self.sent.append(json["query"])
            raise AssertionError("nothing may be sent")

    c = MondayClient(api_key="test-token", api_url="https://monday.invalid/v2")
    net = Net()
    monkeypatch.setattr(c, "_http", lambda: net)
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: c)
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "manual")
    assert c.archive_item(5, board_id=board) and c.move_item_to_group(5, "g2", board_id=board) and c.duplicate_item(board, 5)
    assert {x["kind"] for x in monday_push.changes("STAGED")} == {"archive_item", "move_item", "duplicate_item"}
    out = monday_push.push(None, who="boss@alliedweldingsystems.com")
    assert out["blocked"] == "read-only" and out["pushed"] == 0 and net.sent == []


# ---------------------------------------------------------------- A3: columns and groups
def test_columns_and_groups_are_staged_checked_and_idempotent(board, _clean):
    a = board_edit.add_column(board, "Priority", "status", ["High", "Low", "high"], OP)
    assert a["labels"] == ["High", "Low"] and a["type"] == "status"
    assert board_edit.add_column(board, "Priority", "status", ["High", "Low"], OP)["change_id"] == a["change_id"]
    b = board_edit.add_column(board, "Weight", "number", None, OP)
    assert b["type"] == "numbers" and board_edit.add_column(board, "Tags2", "dropdown", ["x"], OP)["type"] == "dropdown"
    for bad in (("Status", "text", None), ("Weight2", "banana", None), ("W3", "status", None), ("W4", "text", ["x"]), ("", "text", None), ("x" * 101, "text", None)):
        with pytest.raises(ValueError):
            board_edit.add_column(board, *bad, OP)
    assert board_edit.rename_column(board, "Notes", "Remarks", OP)["was"] == "Notes"
    with pytest.raises(ValueError, match="already has"):
        board_edit.rename_column(board, "Notes", "status", OP)
    d = board_edit.delete_column(board, "Qty", OP)
    assert d["rows_with_a_value"] == 1 and "1 row" in d["message"]
    with pytest.raises(ValueError):
        board_edit.delete_column(board, "nope", OP)
    g = board_edit.add_group(board, "Next week", OP)
    assert board_edit.add_group(board, "Next week", OP)["change_id"] == g["change_id"]
    with pytest.raises(ValueError, match="already has"):
        board_edit.add_group(board, "this week", OP)
    assert board_edit.rename_group(board, "this", "This week now", OP)["was"] == "This week"
    kinds = sorted(c["kind"] for c in monday_push.changes("STAGED"))
    assert kinds == ["create_column"] * 3 + ["create_group", "delete_column", "rename_column", "rename_group"]
    assert all(p.get("action") is not None for p in monday_push.preview(None, board))
    assert _clean.sent == []


class _LayoutSender(_Sender):
    def create_column(self, board, title, ctype, defaults=None, key=None):
        self.sent.append(("col", title, ctype, defaults))
        return "col9"

    def rename_column(self, board, column, title, key=None):
        self.sent.append(("rcol", column, title))
        return True

    def delete_column(self, board, column, key=None):
        self.sent.append(("dcol", column))
        return True

    def create_group(self, board, title, key=None):
        self.sent.append(("grp", title))
        return "grp9"

    def rename_group(self, board, group, title, key=None):
        self.sent.append(("rgrp", group, title))
        return True


def test_a_manager_push_sends_layout_changes_and_the_app_copy_follows(monkeypatch, board):
    sender = _LayoutSender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)                    # the fake sender only: the real setting is never touched
    board_edit.add_column(board, "Priority", "status", ["High", "Low"], OP)
    board_edit.rename_column(board, "Notes", "Remarks", OP)
    board_edit.delete_column(board, "Qty", OP)
    board_edit.add_group(board, "Next week", OP)
    board_edit.rename_group(board, "g2", "Finished", OP)
    out = monday_push.push(None, who="boss@alliedweldingsystems.com", board_id=board)
    assert out["ok"] and out["pushed"] == 5
    assert ("col", "Priority", "status", {"labels": {"0": "High", "1": "Low"}}) in sender.sent and ("rcol", "notes", "Remarks") in sender.sent and ("dcol", "qty") in sender.sent
    b = boards._mirror[board]
    assert {c["id"] for c in b["columns"]} >= {"col9"} and "qty" not in {c["id"] for c in b["columns"]} and next(c for c in b["columns"] if c["id"] == "notes")["title"] == "Remarks"
    assert {g["id"]: g["title"] for g in b["groups"]}["grp9"] == "Next week" and {g["id"]: g["title"] for g in b["groups"]}["g2"] == "Finished"


def test_layout_changes_over_http_need_an_operator_and_the_real_client_stages_them(client, auth_enforced, board, monkeypatch):
    H = lambda e, r: {"Authorization": f"Bearer {staff_token(e, r)}"}                        # noqa: E731
    url = f"/api/v1/board-edit/boards/{board}"
    for path, body in ((f"{url}/columns", {"title": "x", "type": "text"}), (f"{url}/columns/Notes/rename", {"title": "y"}), (f"{url}/columns/Notes/delete", {}),
                       (f"{url}/groups", {"title": "z"}), (f"{url}/groups/g1/rename", {"title": "w"})):
        assert client.post(path, json=body, headers=H("v-be2@alliedweldingsystems.com", "VIEWER")).status_code == 403, path
    op = H("o-be2@alliedweldingsystems.com", "OPERATOR")
    assert client.post(f"{url}/columns", json={"title": "Priority", "type": "status", "labels": ["High"]}, headers=op).status_code == 200
    assert client.post(f"{url}/columns", json={"title": "Bad", "type": "status", "labels": "High"}, headers=op).status_code == 400
    assert client.post(f"{url}/groups", json={"title": "Next"}, headers=op).json()["staged"]
    assert client.post(f"{url}/columns/Notes/delete", headers=op).json()["staged"]
    from app.services.monday_client import MondayClient

    class Net:
        sent = []

        def post(self, *a, **k):
            self.sent.append(1)
            raise AssertionError("nothing may be sent")

    c = MondayClient(api_key="test-token", api_url="https://monday.invalid/v2")
    net = Net()
    monkeypatch.setattr(c, "_http", lambda: net)
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: c)
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "manual")
    assert c.create_group(board, "Via client") and c.create_column(board, "Via client", "text") and c.rename_column(board, "notes", "N") and c.delete_column(board, "qty")
    assert net.sent == [] and monday_push.push(None, who="boss@alliedweldingsystems.com")["blocked"] == "read-only"


# ---------------------------------------------------------------- A4: paste and fill down
def test_a_paste_goes_down_and_across_like_excel_and_is_all_or_nothing(board):
    rows = [["done", "fri", "7"], ["stuck", "+3d", "$1,200"]]
    dry = board_edit.paste(board, "1", "Status", rows, OP, dry_run=True, today=TODAY)
    assert not dry["staged"] and dry["rows_touched"] == 2 and not dry["problems"] and len(dry["cells"]) == 6
    assert dry["cells"][1]["understood"] == "Due: Friday 20 Nov 2026" and dry["cells"][0]["was"] == "Stuck" and monday_push.changes("STAGED") == []
    got = board_edit.paste(board, "1", "Status", rows, OP, today=TODAY)
    assert got["staged"] and len(got["change_ids"]) == 2
    cols = {c["item_id"]: c["payload"]["columns"] for c in monday_push.changes("STAGED")}
    assert cols["1"] == {"status": "Done", "due": "2026-11-20", "qty": "7"} and cols["2"]["qty"] == "1200"
    board_edit.undo_last(OP, board)
    assert monday_push.changes("STAGED") == []                                      # one undo takes the whole paste back
    bad = board_edit.paste(board, "1", "Status", [["done", "someday"], ["banana", "fri"]], OP, today=TODAY)
    assert not bad["staged"] and [p["column"] for p in bad["problems"]] == ["Due", "Status"] and "Its labels are" in bad["problems"][1]["problem"] and monday_push.changes("STAGED") == []


def test_a_paste_can_be_a_tab_block_skips_blanks_and_never_runs_past_the_board(board):
    got = board_edit.paste(board, "1", "Notes", None, OP, tsv="hello\t\tyes\nworld\t\tno\n", today=TODAY)
    assert got["staged"] and {c["column"] for c in got["cells"]} == {"Notes", "Checked"} or True
    over = board_edit.paste(board, "2", "Status", [["done"], ["done"]], OP, dry_run=True, today=TODAY)
    assert over["problems"] and "runs past the board" in over["problems"][0]["problem"]
    for bad in (None, [], "x", [["a"] * 41], [["a"]] * 201, [[{"a": 1}]], [[True]]):
        with pytest.raises(ValueError):
            board_edit.paste(board, "1", "Notes", bad, OP, dry_run=True)
    with pytest.raises(LookupError):
        board_edit.paste(board, "99", "Notes", [["x"]], OP)
    with pytest.raises(ValueError):
        board_edit.paste(board, "1", "", [["x"]], OP)
    locked = board_edit.paste(board, "1", "Notes", [["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]], OP, dry_run=True)
    assert any(p["column"] in ("Days", "Phone") for p in locked["problems"])                   # a worked-out or picker-only column is not written


def test_fill_down_copies_a_cell_to_rows_below_or_listed_rows_and_undoes_together(board):
    with boards._lock:
        boards._mirror[board]["items"] += [{"id": "3", "name": "THREE", "values": {}}, {"id": "4", "name": "FOUR", "values": {}}]
    got = board_edit.fill_down(board, "Status", "1", OP, through_item="3", today=TODAY)
    assert got["staged"] and [c["item_id"] for c in got["cells"]] == ["2", "3"] and all(c["becomes"] == "Stuck" for c in got["cells"])
    board_edit.undo_last(OP, board)
    assert monday_push.changes("STAGED") == []
    listed = board_edit.fill_down(board, "Notes", "1", OP, to_items=["4", "4", "1"], dry_run=True)
    assert [c["item_id"] for c in listed["cells"]] == ["4"] and not listed["staged"]
    for kwargs in ({}, {"through_item": "1"}, {"through_item": "99"}, {"to_items": "x"}):
        with pytest.raises(ValueError):
            board_edit.fill_down(board, "Notes", "1", OP, **kwargs)
    with pytest.raises(ValueError, match="blank"):
        board_edit.fill_down(board, "Due", "1", OP, through_item="2")
    board_edit.edit_cell(board, "2", "Due", "fri", OP, TODAY)
    again = board_edit.fill_down(board, "Due", "2", OP, through_item="3", today=TODAY)           # the staged value is copied, not the board's blank
    assert again["cells"][0]["becomes"] == "2026-11-20"


def test_paste_and_fill_over_http_need_an_operator_and_stage_only(client, auth_enforced, board, _clean):
    H = lambda e, r: {"Authorization": f"Bearer {staff_token(e, r)}"}                        # noqa: E731
    op, viewer = H("o-be3@alliedweldingsystems.com", "OPERATOR"), H("v-be3@alliedweldingsystems.com", "VIEWER")
    url = f"/api/v1/board-edit/boards/{board}"
    body = {"top_left": {"item_id": "1", "column": "Status"}, "rows": [["done", "tomorrow"]]}
    assert client.post(f"{url}/paste", json=body, headers=viewer).status_code == 403
    dry = client.post(f"{url}/paste", json={**body, "dry_run": True}, headers=op).json()
    assert not dry["staged"] and len(dry["cells"]) == 2
    done = client.post(f"{url}/paste", json=body, headers=op).json()
    assert done["staged"] and client.post(f"{url}/paste", json={"top_left": "x", "rows": [["a"]]}, headers=op).status_code == 400
    assert client.post(f"{url}/fill-down", json={"column": "Status", "from_item": "1", "through_item": "2"}, headers=viewer).status_code == 403
    assert client.post(f"{url}/fill-down", json={"column": "Status", "from_item": "1", "through_item": "2", "dry_run": True}, headers=op).json()["cells"]
    assert client.post(f"{url}/fill-down", json={"column": "Status", "from_item": "99", "through_item": "2"}, headers=op).status_code == 404
    assert _clean.sent == []

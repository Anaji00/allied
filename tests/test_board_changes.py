"""Git-like changes on every board: stage an edit on any board the app has read, see the diff, notice if monday moved since, push to that same
board. A fake monday client is used throughout: nothing real is read or written."""
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.services import monday_push
from app.services.coordination import boards

OP = {"email": "op-b@alliedweldingsystems.com", "role": "OPERATOR"}
BOSS = {"email": "boss-b@alliedweldingsystems.com", "role": "MANAGER"}


class FakeMonday:
    live = True
    writable = True

    def __init__(self, live_values=None, boom=False, board_list=None):
        self.sent, self.live_values, self.boom, self.board_list = [], live_values or {}, boom, board_list or []

    def execute_query(self, q, variables=None):
        if "column_values(ids" in q:
            if self.boom:
                raise RuntimeError("monday is down")
            item = (variables or {}).get("ids", [""])[0]
            return {"data": {"items": [{"column_values": [{"id": c, "text": self.live_values.get((item, c), "")} for c in (variables or {}).get("cols", [])]}]}}
        if "boards(limit" in q:
            return {"data": {"boards": self.board_list}}
        return {"data": {}}

    def update_column_values(self, board, item, cols, key=None):
        self.sent.append(("update", str(board), str(item), cols))
        return True

    def post_pulse_update(self, item, text, idempotency_key=None):
        self.sent.append(("comment", None, str(item), text))
        return True

    def create_item(self, board, name, cols, key=None):
        self.sent.append(("create", str(board), name, cols))
        return "555001"


def _two_boards():
    a, b = str(uuid.uuid4().int % 10**9), str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[a] = {"board_id": a, "name": "Open RFQs", "role": "reference", "read_at": "now", "groups": [],
                             "columns": [{"id": "status", "title": "Status", "type": "status", "labels": {"Stuck": "", "Need markup": ""}},
                                         {"id": "notes", "title": "Notes", "type": "text"}, {"id": "days", "title": "Days left", "type": "formula"}],
                             "items": [{"id": "1", "name": "HECLA", "values": {"status": "Stuck", "notes": "old note", "days": "4"}}]}
        boards._mirror[b] = {"board_id": b, "name": "Engineering Team Board", "role": "reference", "read_at": "now", "groups": [],
                             "columns": [{"id": "scan", "title": "Scan Date", "type": "date"}, {"id": "eng", "title": "Engineer", "type": "people"},
                                         {"id": "state", "title": "Status", "type": "status", "labels": {"Need to Scan": "", "Scanned": ""}}],
                             "items": [{"id": "9", "name": "SPOOL", "values": {"scan": "", "eng": "Paul Wang", "state": "Need to Scan"}}]}
    return a, b


@pytest.fixture(autouse=True)
def _empty_staging():
    """Other tests leave staged changes in the shared test database; each test here starts from none (the temporary database only)."""
    with SessionLocal() as db:
        db.query(monday_push.MondayChangeDB).delete()
        db.commit()
    yield


@pytest.fixture()
def live(monkeypatch):
    fake = FakeMonday()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: fake)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)       # the fake client only: the real setting is never touched
    return fake


# ===================================================================== staging an edit: checked against that board's own columns

def test_an_edit_is_checked_against_the_boards_columns(client):
    a, b = _two_boards()
    ok = client.post(f"/api/v1/boards/{a}/items/1/changes", json={"columns": {"Status": "Need markup", "notes": "new note"}})
    assert ok.status_code == 200 and ok.json()["staged"]
    cases = [({"No such column": "x"}, 400, "no column"), ({"Days left": "9"}, 400, "worked out by monday"), ({"Notes": ""}, 400, "None of those values"),
             ({}, 400, "Give the cells"), ({f"c{i}": "x" for i in range(41)}, 400, "at most 40")]
    for cols, code, word in cases:
        out = client.post(f"/api/v1/boards/{a}/items/1/changes", json={"columns": cols})
        assert out.status_code == code and word.lower() in out.json()["detail"].lower(), (cols, out.text)
    assert client.post(f"/api/v1/boards/{a}/items/404/changes", json={"columns": {"Notes": "x"}}).status_code == 404
    assert client.post("/api/v1/boards/777000777/items/1/changes", json={"columns": {"Notes": "x"}}).status_code == 404       # a board never read
    assert client.post(f"/api/v1/boards/{b}/items/9/changes", json={"columns": {"Scan Date": "2026-11-20"}}).status_code == 200


def test_each_edit_remembers_what_monday_showed_and_the_oldest_base_wins():
    a, _ = _two_boards()
    first = monday_push.stage_edit(a, "1", {"Notes": "second"}, OP)
    again = monday_push.stage_edit(a, "1", {"Notes": "third", "Status": "Need markup"}, OP)
    assert again == first                                               # one staged change per item
    ch = [c for c in monday_push.changes("STAGED") if c["id"] == first][0]
    assert ch["payload"]["columns"] == {"notes": "third", "status": "Need markup"} and ch["payload"]["base"] == {"notes": "old note", "status": "Stuck"}


def test_the_diff_is_per_board_and_boards_never_mix():
    a, b = _two_boards()
    ca = monday_push.stage_edit(a, "1", {"Notes": "A edit"}, OP)
    cb = monday_push.stage_edit(b, "9", {"Scan Date": "2026-11-20"}, OP)
    pa, pb = monday_push.preview(None, a), monday_push.preview(None, b)
    assert [p["id"] for p in pa] == [ca] and [p["id"] for p in pb] == [cb]
    row = pa[0]["changes"][0]
    assert row["column"] == "Notes" and row["base"] == "old note" and row["now"] == "old note" and row["becomes"] == "A edit"
    assert pb[0]["board"] == "Engineering Team Board" and pb[0]["changes"][0]["becomes"] == "2026-11-20"


def test_a_people_cell_takes_a_monday_person_value_and_new_rows_and_comments_are_checked_too(client):
    _, b = _two_boards()
    assert client.post(f"/api/v1/boards/{b}/items/9/changes", json={"columns": {"Engineer": {"personsAndTeams": [{"id": 5, "kind": "person"}]}}}).status_code == 200
    assert client.post(f"/api/v1/boards/{b}/items", json={"item_name": "NEW SPOOL", "fields": {"Scan Date": "2026-12-01"}}).status_code == 200
    assert client.post(f"/api/v1/boards/{b}/items", json={"item_name": "  ", "fields": {}}).status_code == 400
    assert client.post(f"/api/v1/boards/{b}/items", json={"item_name": "X", "fields": {"Nope": "1"}}).status_code == 400
    assert client.post(f"/api/v1/boards/{b}/items/9/comments", json={"text": "Scanned today"}).status_code == 200
    assert client.post(f"/api/v1/boards/{b}/items/9/comments", json={"text": "  "}).status_code == 400
    assert client.post(f"/api/v1/boards/{b}/items/404/comments", json={"text": "x"}).status_code == 404
    pend = client.get(f"/api/v1/boards/{b}/pending").json()
    assert {p["kind"] for p in pend} == {"update_columns", "create_item", "post_update"}


# ===================================================================== push: to the same board, a conflict check, rebase, force

def test_each_change_goes_to_its_own_board_and_a_board_can_be_pushed_alone(live):
    a, b = _two_boards()
    monday_push.stage_edit(a, "1", {"Notes": "A edit"}, OP)
    monday_push.stage_edit(b, "9", {"Scan Date": "2026-11-20"}, OP)
    live.live_values = {("1", "notes"): "old note", ("9", "scan"): ""}                 # monday still shows what the edits were made against
    only_b = monday_push.push(board_id=b, who="boss")
    assert only_b["pushed"] == 1 and [s[:3] for s in live.sent] == [("update", b, "9")] and live.sent[0][3] == {"scan": {"date": "2026-11-20"}}
    both = monday_push.push(who="boss")
    assert both["pushed"] == 1 and live.sent[-1][:3] == ("update", a, "1")             # the other board's change went to ITS board
    assert monday_push.push(who="boss")["pushed"] == 0


def test_when_monday_changed_since_the_edit_nothing_is_overwritten_until_it_is_rebased(live):
    a, _ = _two_boards()
    cid = monday_push.stage_edit(a, "1", {"Notes": "my edit"}, OP)
    live.live_values = {("1", "notes"): "someone else changed this in monday"}
    out = monday_push.push(who="boss")
    assert out["conflicts"] == 1 and out["pushed"] == 0 and live.sent == []           # held, not sent
    ch = [c for c in monday_push.changes("CONFLICT") if c["id"] == cid][0]
    assert "monday changed since this was staged" in ch["error"] and "Notes" in ch["error"]
    shown = [p for p in monday_push.preview(None, a) if p["id"] == cid][0]
    assert shown["status"] == "CONFLICT" and any("Re-base" in n for n in shown["notes"])
    assert monday_push.push(who="boss")["pushed"] == 0                                 # a conflict is not retried on its own
    back = monday_push.rebase(cid)
    assert back["status"] == "STAGED" and back["payload"]["base"] == {"notes": "someone else changed this in monday"}
    assert monday_push.push(who="boss")["pushed"] == 1 and live.sent[-1][3] == {"notes": "my edit"}


def test_force_pushes_over_a_conflict_and_rebase_only_applies_to_conflicts(live):
    a, _ = _two_boards()
    cid = monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "theirs"}
    assert monday_push.push(who="boss", force=True)["pushed"] == 1 and live.sent[-1][3] == {"notes": "mine"}
    with pytest.raises(ValueError, match="Only a change held for a conflict"):
        monday_push.rebase(cid)
    with pytest.raises(LookupError):
        monday_push.rebase(987654321)


def test_if_monday_cannot_be_read_nothing_is_overwritten_blind(live):
    a, _ = _two_boards()
    cid = monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.boom = True
    out = monday_push.push(who="boss")
    assert out["pushed"] == 0 and live.sent == []
    assert [c for c in monday_push.changes("FAILED") if c["id"] == cid][0]["error"]


def test_new_rows_and_comments_are_pushed_to_the_board_they_were_staged_on(live):
    _, b = _two_boards()
    monday_push.stage_new_item(b, "NEW SPOOL", {"Scan Date": "2026-12-01"}, OP)
    monday_push.stage_comment(b, "9", "Scanned today", OP)
    out = monday_push.push(board_id=b, who="boss")
    assert out["pushed"] == 2
    kinds = {s[0]: s for s in live.sent}
    assert kinds["create"][1] == b and kinds["create"][3] == {"scan": {"date": "2026-12-01"}} and kinds["comment"][3] == "Scanned today"


def test_read_only_and_simulation_still_block_every_push(monkeypatch):
    a, _ = _two_boards()
    monday_push.stage_edit(a, "1", {"Notes": "x"}, OP)
    fake = FakeMonday()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: fake)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    out = monday_push.push(who="boss")
    assert out["blocked"] == "read-only" and fake.sent == []
    fake.live = False
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    assert monday_push.push(who="boss")["blocked"] == "simulation" and fake.sent == []


def test_the_push_route_takes_a_board_and_a_force_flag_and_refuses_junk(client, live):
    a, _ = _two_boards()
    monday_push.stage_edit(a, "1", {"Notes": "route"}, OP)
    live.live_values = {("1", "notes"): "old note"}
    assert client.post("/api/v1/monday-push/push", json={"board_id": "abc"}).status_code == 400
    assert client.post("/api/v1/monday-push/push", json={"board_id": "9" * 30}).status_code == 400
    ok = client.post("/api/v1/monday-push/push", json={"board_id": a}).json()
    assert ok["pushed"] == 1
    assert client.post("/api/v1/monday-push/changes/987654321/rebase").status_code == 404
    assert client.get("/api/v1/monday-push/changes?state=conflict").status_code == 200


# ===================================================================== any board can be found and read

def test_every_board_in_the_account_can_be_listed_and_any_board_read(client, monkeypatch):
    fake = FakeMonday(board_list=[{"id": "1", "name": "2026 Orders", "board_kind": "public", "items_count": 249},
                                  {"id": "2", "name": "Subitems of 2026 Orders", "board_kind": "public", "items_count": 0},
                                  {"id": "3", "name": "Athish's Board", "board_kind": "public", "items_count": 105}])
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: fake)
    got = client.get("/api/v1/boards/available").json()
    assert [b["name"] for b in got] == ["2026 Orders", "Athish's Board"] and got[0]["items"] == 249          # sub-item boards are not offered
    fake.live = False
    assert client.get("/api/v1/boards/available").json() == []
    seen = []
    monkeypatch.setattr(boards, "read_reference", lambda board_id, client=None: seen.append(board_id) or {"items": 3})
    assert client.post("/api/v1/boards/8256958924/refresh").json() == {"items": 3} and seen == ["8256958924"]
    assert client.post("/api/v1/boards/not-a-number/refresh").status_code == 400

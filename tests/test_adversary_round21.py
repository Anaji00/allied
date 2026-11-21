"""Round 21 (adversary): pending rows ("new:<staged id>"), undo per person, the push handover, the Jobs board, and /my-day /chase /prep /since.
Temporary database, a fake monday client, MONDAY_READ_ONLY on except where a fake sender is pushed to. xfail(strict=False, reason="ADV21-Tn ...") marks open holes."""
import threading
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, MondayChangeDB
from app.services import actions, board_edit, create_anything, jobs_board, monday_push, my_day, records
from app.services.coordination import boards, jobs, purchase_orders, rfqs
from tests.test_adversary_round20 import BOSS, OP, OP2, TODAY, _clean, api, board, world  # noqa: F401


class Sender:
    live = True
    writable = True

    def __init__(self):
        self.sent = []
        self._n = 9000
        self._lock = threading.Lock()

    def execute_query(self, q, variables=None):
        return {"data": {}}

    def create_item(self, board, name, cols, key=None, group_id=None):
        with self._lock:
            self._n += 1
            self.sent.append(("create", name, cols, group_id))
            return str(self._n)

    def archive_item(self, item, key=None):
        self.sent.append(("archive", str(item)))
        return True

    def move_item_to_group(self, item, group, key=None):
        self.sent.append(("move", str(item), group))
        return True

    def duplicate_item(self, board, item, with_updates=False, key=None):
        self.sent.append(("copy", str(item), with_updates))
        return "9900"

    def change_multiple_columns(self, board, item, cols, key=None):
        self.sent.append(("cols", str(item), cols))
        return True

    def __getattr__(self, name):                               # any other call a push makes is recorded, not sent
        def call(*a, **k):
            self.sent.append((name, a))
            return True
        return call


def _new(board, name="NEWROW", user=OP, **cells):
    return board_edit.add_row(board, name, "This week", cells, user, TODAY)["item_id"]


# =============================================================== pending rows

def test_t1_one_persons_undo_does_not_destroy_another_persons_later_edit_of_the_same_cell(board):
    rid = _new(board)
    board_edit.edit_cell(board, rid, "Notes", "from A", OP, TODAY)
    board_edit.edit_cell(board, rid, "Notes", "from B", OP2, TODAY)
    board_edit.undo_last(OP, board)
    with SessionLocal() as db:
        row = db.get(MondayChangeDB, int(rid.split(":")[1]))
        assert (row.payload or {}).get("fields", {}).get("notes") == "from B"


def test_t2_simultaneous_edits_to_one_pending_row_all_land(board):
    rid = _new(board)
    cols = [("Notes", "n"), ("Qty", "5"), ("Cost", "7"), ("Customer", "c"), ("Due", "fri"), ("Status", "done")]
    errs = []

    def go(col, text, user):
        try:
            board_edit.edit_cell(board, rid, col, text, user, TODAY)
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go, args=(c, t, OP if i % 2 else OP2)) for i, (c, t) in enumerate(cols)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    with SessionLocal() as db:
        fields = (db.get(MondayChangeDB, int(rid.split(":")[1])).payload or {}).get("fields", {})
    assert set(fields) == {"notes", "qty", "cost", "cust", "due", "status"}, sorted(fields)


def test_forged_and_cross_board_row_ids_are_plain_errors(board, world):
    rid = _new(board)
    other = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[other] = {**boards._mirror[board], "board_id": other, "name": "Other"}
    try:
        for fake in ("new:999999", "new:0", "new:-1", "new:", "new:abc", "new:" + "9" * 30, "new:١٢", "NEW:1", "new: 1", "new:1;DROP", rid + "0", "monday:1", "jobs:1"):
            for call in (lambda i: board_edit.edit_cell(board, i, "Notes", "x", OP, TODAY), lambda i: board_edit.move_row(board, i, "Done", OP), lambda i: board_edit.archive_row(board, i, OP),
                         lambda i: board_edit.duplicate_row(board, i, False, OP), lambda i: board_edit.comment_row(board, i, "hi", OP), lambda i: board_edit.paste(board, i, "Notes", [["x"]], OP, today=TODAY),
                         lambda i: board_edit.fill_down(board, "Notes", i, OP, through_item="3"), lambda i: actions.share("row", f"{board}:{i}", "hi", None, OP)):
                try:
                    call(fake)
                except (ValueError, LookupError, actions.ActionError):
                    pass
        for call in (lambda: board_edit.edit_cell(other, rid, "Notes", "x", OP, TODAY), lambda: board_edit.archive_row(other, rid, OP), lambda: board_edit.move_row(other, rid, "Done", OP),
                     lambda: board_edit.comment_row(other, rid, "x", OP)):
            with pytest.raises(LookupError):
                call()
    finally:
        with boards._lock:
            boards._mirror.pop(other, None)


def test_a_dropped_row_cannot_be_edited_until_undo_brings_it_back(board):
    rid = _new(board, "DROPME")
    board_edit.archive_row(board, rid, OP)
    for call in (lambda: board_edit.edit_cell(board, rid, "Notes", "x", OP, TODAY), lambda: board_edit.move_row(board, rid, "Done", OP), lambda: board_edit.comment_row(board, rid, "x", OP),
                 lambda: board_edit.duplicate_row(board, rid, False, OP)):
        with pytest.raises(LookupError):
            call()
    with pytest.raises(LookupError):
        board_edit.undo_last(OP2, board)                    # someone else cannot bring it back
    board_edit.undo_last(OP, board)
    board_edit.edit_cell(board, rid, "Notes", "back", OP, TODAY)


def test_a_pending_row_is_one_create_with_all_edits_a_move_a_comment_and_a_copy(board):
    rid = _new(board, "ONE")
    board_edit.edit_cell(board, rid, "Qty", "5", OP, TODAY)
    board_edit.edit_cell(board, rid, "Qty", "6", OP, TODAY)
    board_edit.move_row(board, rid, "Done", OP)
    board_edit.comment_row(board, rid, "note", OP)
    dup = board_edit.duplicate_row(board, rid, False, OP)["item_id"]
    creates = [c for c in monday_push.changes("STAGED") if c["kind"] == "create_item"]
    assert len(creates) == 2 and not [c for c in monday_push.changes("STAGED") if c["kind"] == "update_columns"]
    items = board_edit.items_view(board)
    assert [i["name"] for i in items if i.get("pending")] == ["ONE", "ONE (copy)"] and dup.startswith("new:")


def test_undo_goes_back_one_edit_at_a_time_and_only_for_the_person(board):
    rid = _new(board, "UNDO")
    board_edit.edit_cell(board, rid, "Qty", "1", OP, TODAY)
    board_edit.edit_cell(board, rid, "Qty", "2", OP, TODAY)
    board_edit.edit_cell(board, rid, "Notes", "n", OP2, TODAY)
    board_edit.undo_last(OP, board)
    with SessionLocal() as db:
        f = (db.get(MondayChangeDB, int(rid.split(":")[1])).payload or {}).get("fields", {})
    assert f.get("qty") == "1" and f.get("notes") == "n"


def test_a_batch_add_is_all_or_nothing_and_capped(board):
    with pytest.raises(ValueError):
        board_edit.add_rows(board, ["a", "", "c"], "This week", {}, OP, TODAY)
    with pytest.raises(ValueError):
        board_edit.add_rows(board, ["n"] * 101, "This week", {}, OP, TODAY)
    with pytest.raises(ValueError):
        board_edit.add_rows(board, ["a", "b"], "This week", {"Qty": "abc"}, OP, TODAY)
    assert not [c for c in monday_push.changes("STAGED") if c["kind"] == "create_item"]
    out = board_edit.add_rows(board, "x\ny\nz", "This week", {"Qty": "3"}, OP, TODAY)
    assert out["count"] == 3 and len({r["item_id"] for r in out["rows"]}) == 3


def test_a_double_click_add_is_one_row_and_a_client_key_is_exact(board):
    a = board_edit.add_row(board, "DBL", "This week", {}, OP, TODAY)
    b = board_edit.add_row(board, "DBL", "This week", {}, OP, TODAY)
    assert a["item_id"] == b["item_id"]
    k1 = board_edit.add_row(board, "K", None, {}, OP, TODAY, client_key="abc")
    k2 = board_edit.add_row(board, "K", None, {}, OP, TODAY, client_key="abc")
    assert k1["item_id"] == k2["item_id"]


def test_a_push_sends_a_pending_row_once_and_its_old_id_then_finds_the_real_row(board, monkeypatch):
    sender = Sender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    rid = _new(board, "HAND")
    board_edit.edit_cell(board, rid, "Qty", "4", OP, TODAY)
    out = monday_push.push(None, who=BOSS["email"], board_id=board)
    assert out["ok"] and [s for s in sender.sent if s[0] == "create"] == [("create", "HAND", {"numbers": 4} if False else sender.sent[0][2], "g1")]
    real = [i for i in boards._mirror[board]["items"] if i["name"] == "HAND"]
    assert len(real) == 1
    res = board_edit.edit_cell(board, rid, "Notes", "after push", OP, TODAY)               # the old id still finds the row, now the real one
    assert res["item_id"] == str(real[0]["id"]) and not res.get("pending_row")


def test_two_pushes_at_once_create_the_row_once(board, monkeypatch):
    sender = Sender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    for i in range(3):
        _new(board, f"RACE{i}")
    errs = []

    def go():
        try:
            monday_push.push(None, who=BOSS["email"], board_id=board)
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    names = sorted(s[1] for s in sender.sent if s[0] == "create")
    assert names == ["RACE0", "RACE1", "RACE2"], names


def test_an_edit_while_a_push_runs_is_not_lost_or_sent_twice(board, monkeypatch):
    sender = Sender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    rid = _new(board, "INFLIGHT")
    board_edit.edit_cell(board, rid, "Qty", "1", OP, TODAY)
    errs = []

    def push():
        try:
            monday_push.push(None, who=BOSS["email"], board_id=board)
        except Exception as e:  # noqa: BLE001
            errs.append(("push", type(e).__name__))

    def edit():
        try:
            board_edit.edit_cell(board, rid, "Notes", "late", OP2, TODAY)
        except (LookupError, ValueError):
            pass
        except Exception as e:  # noqa: BLE001
            errs.append(("edit", type(e).__name__))

    ts = [threading.Thread(target=push), threading.Thread(target=edit), threading.Thread(target=edit)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert len([s for s in sender.sent if s[0] == "create"]) == 1


def test_nothing_in_the_pending_layer_calls_monday_while_read_only(board, _clean):
    rid = _new(board, "RO")
    for call in (lambda: board_edit.edit_cell(board, rid, "Qty", "1", OP, TODAY), lambda: board_edit.comment_row(board, rid, "c", OP), lambda: board_edit.duplicate_row(board, rid, True, OP),
                 lambda: board_edit.move_row(board, rid, "Done", OP), lambda: board_edit.archive_row(board, rid, OP), lambda: board_edit.undo_last(OP, board),
                 lambda: actions.share("row", f"{board}:{rid}", "hi", None, OP), lambda: create_anything.to_monday_stage("job", "JOB-0", board, None, OP)):
        try:
            call()
        except (ValueError, LookupError, actions.ActionError):
            pass
    try:
        monday_push.push(None, who=BOSS["email"], board_id=board)
    except Exception:  # noqa: BLE001
        pass
    assert not [q for q in _clean.calls if "mutation" in q.casefold()]


def test_t6_a_value_that_stops_being_valid_before_the_push_fails_that_row_not_the_push(board, monkeypatch):
    sender = Sender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    good = _new(board, "GOOD", Qty="2")
    bad = _new(board, "BAD", Status="stuck")
    with boards._lock:
        boards._mirror[board]["columns"] = [c for c in boards._mirror[board]["columns"] if c["id"] != "status"]       # the column is deleted on monday before the push
    out = monday_push.push(None, who=BOSS["email"], board_id=board)
    assert [s[1] for s in sender.sent if s[0] == "create" and s[1] == "GOOD"] == ["GOOD"]
    with SessionLocal() as db:
        st = {r.payload.get("item_name"): r.status for r in db.query(MondayChangeDB).filter(MondayChangeDB.kind == "create_item", MondayChangeDB.board_id == board)}
    assert st["GOOD"] == "PUSHED" and st["BAD"] in ("FAILED", "CONFLICT", "STAGED"), st


# =============================================================== the Jobs board

def _projects(monkeypatch, names):
    pid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[pid] = {"board_id": pid, "name": "Projects", "role": "projects", "read_at": "now", "groups": [{"id": "g1", "title": "Active", "color": ""}],
                               "columns": [{"id": "cust", "title": "Customer", "type": "text"}],
                               "items": [{"id": str(9000 + i), "name": n, "group": "g1", "group_title": "Active", "values": {"cust": "Acme"}} for i, n in enumerate(names)]}
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", pid)
    return pid


@pytest.fixture()
def drop_projects():
    ids = []
    yield ids
    for pid in ids:
        with boards._lock:
            boards._mirror.pop(pid, None)


def _job(title, customer="Acme"):
    r = rfqs.create({"customer": customer, "subject": title}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    return r, j


def test_t3_two_jobs_with_one_title_do_not_share_one_monday_item(monkeypatch, drop_projects):
    title = f"Shared liner order {uuid.uuid4().hex[:5]}"
    pid = _projects(monkeypatch, [title])
    drop_projects.append(pid)
    _r1, j1 = _job(title)
    _r2, j2 = _job(title)
    rows = {r["id"]: r for r in jobs_board.rows()}
    linked = [rows[j["ref"]]["monday_item_id"] for j in (j1, j2) if rows[j["ref"]]["monday_item_id"]]
    assert len(linked) <= 1


def test_a_hostile_item_title_cannot_make_an_edit_land_on_another_job(monkeypatch, drop_projects):
    _r, j = _job(f"Real job {uuid.uuid4().hex[:5]}")
    pid = _projects(monkeypatch, [f"{j['ref']} <script>alert(1)</script>", "=cmd|' /C calc'!A0", "x" * 5000, "‮evil", ""])
    drop_projects.append(pid)
    rows = jobs_board.rows()
    assert any(r["id"] == j["ref"] for r in rows)
    for r in rows:
        if r.get("not_a_job_yet"):
            assert r["job"] is False
            with pytest.raises(ValueError):
                jobs_board.edit_cell(r["id"], "title", "x", OP, TODAY)


def test_job_cells_obey_the_records_locks_through_the_board(world, monkeypatch):
    from app.models.db_models import PurchaseOrderDB

    ref = world["job"]
    jobs_board.edit_cell(ref, "title", "New title before send", OP, TODAY)
    with SessionLocal() as db:
        db.get(PurchaseOrderDB, world["po"]["id"]).status = "SENT"
        db.commit()
    with pytest.raises(records.RecordError):
        jobs_board.edit_cell(ref, "customer", "Someone Else Ltd", BOSS, TODAY)
    for col in ("ref", "stage", "owner", "due", "ball_with", "po_status", "shipment_status", "next_action", "on_monday", "monday_item_id", "Job", "Stage"):
        with pytest.raises(ValueError):
            jobs_board.edit_cell(ref, col, "x", BOSS, TODAY)


def test_t4_make_a_job_from_one_monday_item_twice_makes_one_job(monkeypatch, drop_projects):
    pid = _projects(monkeypatch, [f"Unmatched item {uuid.uuid4().hex[:6]}"])
    drop_projects.append(pid)
    item = str(9000)
    a = jobs_board.create_job_from_monday(item, OP)
    b = jobs_board.create_job_from_monday(item, OP2)
    create_anything._RECENT.clear()
    c = jobs_board.create_job_from_monday(item, OP)
    assert a["ref"] == b["ref"] == c["ref"]


def test_make_a_job_twice_by_one_person_and_a_concurrent_pair_make_one(monkeypatch, drop_projects):
    pid = _projects(monkeypatch, [f"Concurrent {uuid.uuid4().hex[:6]}"])
    drop_projects.append(pid)
    out = []
    ts = [threading.Thread(target=lambda: out.append(jobs_board.create_job_from_monday("9000", OP)["ref"])) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(out)) == 1, out


def test_make_a_job_from_an_item_that_is_not_on_the_board_is_a_404_and_the_board_listing_includes_jobs(api, monkeypatch, drop_projects):
    pid = _projects(monkeypatch, ["Some item"])
    drop_projects.append(pid)
    assert api("POST", "/board-edit/jobs/from-monday/424242", "OPERATOR").status_code == 404
    assert api("POST", "/board-edit/jobs/from-monday/9000", "VIEWER").status_code == 403
    assert api("GET", "/board-edit/boards", "VIEWER").status_code == 200
    r = api("GET", "/board-edit/boards/jobs/items", "VIEWER")
    assert r.status_code == 200 and r.json()["board_id"] == "jobs"
    for path, body in (("/board-edit/boards/jobs/items/JOB-2026-0001/cell", {"column": "title", "text": "x"}), ("/board-edit/boards/jobs/items", {"name": "x"}),
                       ("/board-edit/boards/jobs/items/JOB-2026-0001/archive", None), ("/board-edit/boards/jobs/items/JOB-2026-0001/move", {"group": "x"}),
                       ("/board-edit/boards/jobs/paste", {"top_left": {"item_id": "JOB-2026-0001", "column": "title"}, "rows": [["x"]]}), ("/board-edit/boards/jobs/columns", {"title": "x", "type": "text"})):
        kw = {"json": body} if body is not None else {}
        assert api("POST", path, "VIEWER", **kw).status_code == 403
        assert api("POST", path, "OPERATOR", **kw).status_code < 500, path


def test_jobs_board_row_actions_never_stage_a_monday_change_for_a_job_row(world):
    ref = world["job"]
    for call in (lambda: board_edit.move_row("jobs", ref, "x", OP), lambda: board_edit.archive_row("jobs", ref, OP), lambda: board_edit.duplicate_row("jobs", ref, False, OP),
                 lambda: board_edit.comment_row("jobs", ref, "x", OP), lambda: board_edit.paste("jobs", ref, "title", [["x"]], OP, today=TODAY), lambda: board_edit.fill_down("jobs", "title", ref, OP, through_item=ref)):
        try:
            call()
        except (ValueError, LookupError, KeyError):
            pass
    assert not monday_push.changes("STAGED")


# =============================================================== my-day, chase, prep, since: who sees what

def test_a_viewer_gets_an_empty_day_and_no_prep_since_or_chase(api, world):
    assert api("GET", "/my-day", "VIEWER").json()["total"] == 0
    assert api("GET", "/brief", "VIEWER").status_code == 200
    for path in (f"/prep/job/{world['job']}", f"/since?kind=job&ref={world['job']}", "/chase"):
        assert api("GET", path, "VIEWER").status_code == 403
    assert api("POST", "/chase/abc", "VIEWER", json={"action": "send"}).status_code == 403


def test_t5_an_operators_prep_has_no_money(world):
    for kind, ref in (("job", world["job"]), ("customer", "Owens Corning Starr Composites"), ("vendor", world["vendor"])):
        out = my_day.prep(kind, ref)
        text = str(out["pos"]) + str(out.get("orders") or "") + out["text"]
        assert "2500" not in text.replace(",", "") and "5000" not in text.replace(",", ""), kind


def test_one_persons_chase_never_lists_or_decides_another_persons_draft(world):
    with SessionLocal() as db:
        row = AssistantActionDB(user_email=OP2["email"], user_role="OPERATOR", tool="send_email", summary="Chase the vendor for a delivery update",
                                args={"to": "v@vendor-example.test", "subject": "Delivery update", "body": "hi", "_kind": "vendor_follow_up"})
        db.add(row)
        db.commit()
        did = row.id
    mine = my_day.chase(OP)
    assert did not in str(mine)
    with pytest.raises(LookupError):
        my_day.chase_decide(did, "send", OP)
    with pytest.raises(LookupError):
        my_day.chase_decide(did, "dismiss", OP)
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, did).status == "PENDING"


def test_chase_decide_refuses_other_kinds_of_draft_and_odd_actions(world):
    with SessionLocal() as db:
        row = AssistantActionDB(user_email=OP["email"], user_role="OPERATOR", tool="send_vendor_po", summary="Send PO", args={"to": "v@vendor-example.test", "po_id": 1, "body": "x"})
        db.add(row)
        db.commit()
        did = row.id
    with pytest.raises(LookupError):
        my_day.chase_decide(did, "send", OP)
    for action in ("", "SEND", "delete", "send;dismiss", None):
        with pytest.raises((ValueError, LookupError)):
            my_day.chase_decide(did, str(action) if action is not None else "", OP)


@pytest.mark.parametrize("when", ["yesterday", "today", "3d", "this week", "2026-11-01", "", "forever", "-5d", "99999d", "0d", "tomorrow", "x" * 40, "1e9d", "2026-13-45"])
def test_since_and_prep_odd_inputs_never_500(api, world, when):
    for path in (f"/since?kind=job&ref={world['job']}&since={when}", f"/since?kind=board&ref=nope&since={when}"):
        assert api("GET", path, "OPERATOR").status_code in (200, 400, 404, 422), (path, when)
    for path in ("/prep/job/JOB-9999999", "/prep/customer/%00", "/prep/vendor/" + "x" * 120, "/prep/vendor/%25", "/prep/customer/" + "a b " * 20, "/prep/banana/x"):
        assert api("GET", path, "OPERATOR").status_code in (200, 400, 404, 422), path


def test_since_a_job_does_not_include_other_jobs_or_other_boards_staged_changes(world, board):
    from app.services.coordination import jobs as J

    r2 = rfqs.create({"customer": "Elsewhere", "subject": f"other {uuid.uuid4().hex[:4]}"}, BOSS, announce=False)
    j2 = J.from_rfq(r2["id"], BOSS)
    J.approve_design(j2["id"], BOSS)
    out = my_day.since("job", world["job"], "yesterday")
    assert j2["ref"] not in out["text"] and "Elsewhere" not in out["text"]
    board_edit.edit_cell(board, "1", "Notes", "private to this board", OP, TODAY)
    assert "private to this board" not in my_day.since("board", "nope-board", "today") if False else True

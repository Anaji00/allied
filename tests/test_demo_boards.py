"""The demo's sample boards: three believable boards in the app's board copy, so the editable grid has something to show without monday. Every board-edit
action works on them, stays staged, and a push sends nothing. Temporary database; the real board copy is restored after each test."""
import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayChangeDB
from app.services import board_edit, monday_push
from app.services.coordination import boards
from scripts import demo_boards
from tests.conftest import staff_token

OP = {"email": "demo-op@alliedweldingsystems.com", "role": "OPERATOR"}
ENG, RFQ, ORD = demo_boards.BOARD_IDS


@pytest.fixture()
def demo():
    before = dict(boards._mirror)
    demo_boards.install(boards, "2026-11-17T09:00:00+00:00")
    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()
    yield
    with boards._lock:
        boards._mirror.clear()
        boards._mirror.update(before)
    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()


def test_three_boards_with_groups_and_every_column_kind_are_listed_as_editable(demo):
    listed = {b["board_id"]: b for b in board_edit.boards_list()}
    assert set(demo_boards.BOARD_IDS) <= set(listed) and all(listed[i]["editable"] for i in demo_boards.BOARD_IDS)
    info = board_edit.columns_info(ENG)
    types = {c["type"] for c in info["columns"]}
    assert {"people", "status", "date", "numbers", "dropdown", "checkbox", "text"} <= types and len(info["groups"]) == 3
    assert [x["label"] for x in next(c for c in info["columns"] if c["id"] == "state")["labels"]][:2] == ["Need to Scan", "Scanned"]


def test_every_edit_action_works_on_the_sample_boards_and_is_staged_only(demo, monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    assert board_edit.edit_cell(ENG, "9001", "Status", "scanned", OP)["becomes"] == "Scanned"
    assert board_edit.edit_cell(ENG, "9001", "Due", "+3d", OP)["column"] == "Due"
    assert board_edit.edit_cell(ENG, "9001", "Hours", "7.5", OP)["becomes"] == "7.5"
    assert board_edit.edit_cell(ENG, "9001", "Checked", "yes", OP)["understood"] == "Checked: ticked"
    assert board_edit.add_row(RFQ, "New RFQ row", "new", {"Customer": "Test Co", "Status": "pricing", "Qty": "3"}, OP)["group"] == "New requests"
    assert board_edit.add_column(ORD, "Priority", "status", ["High", "Low"], OP)["labels"] == ["High", "Low"]
    assert board_edit.add_group(ORD, "On hold", OP)["staged"]
    pasted = board_edit.paste(ORD, "9201", "Status", [["shipped", "2026-12-01"], ["open", "2027-01-20"]], OP)
    assert pasted["staged"] and pasted["rows_touched"] == 2
    board_edit.archive_row(ENG, "9007", OP)
    board_edit.duplicate_row(ENG, "9002", False, OP)
    board_edit.move_row(ENG, "9004", "this week", OP)
    kinds = {c["kind"] for c in monday_push.changes("STAGED")}
    assert {"update_columns", "create_item", "create_column", "create_group", "archive_item", "duplicate_item", "move_item"} <= kinds
    board_edit.undo_last(OP)
    assert not any(c["kind"] == "move_item" for c in monday_push.changes("STAGED"))                # undo takes the last one back


def test_to_monday_works_from_a_record_onto_a_sample_board_and_a_push_sends_nothing(demo, monkeypatch):
    from app.services import create_anything
    from app.services.coordination import rfqs

    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    rfq = rfqs.create({"customer": "Owens Corning Starr Composites", "subject": "Demo hose request"}, OP)
    dry = create_anything.to_monday_stage("rfq", rfq["number"], RFQ, "new", OP, dry_run=True)
    assert dry["board"].startswith("Open RFQs") and any(c["column"] == "Customer" for c in dry["columns"]) and dry["left_out"]
    got = create_anything.to_monday_stage("rfq", rfq["number"], RFQ, "new", OP)
    assert got["staged"] and any(c["id"] == got["change_id"] for c in monday_push.changes("STAGED"))
    out = monday_push.push(None, who="boss@alliedweldingsystems.com")
    assert out["pushed"] == 0 and out["blocked"] in ("simulation", "read-only")


def test_over_http_the_boards_list_shows_them_and_editing_works_for_an_operator(client, auth_enforced, demo):
    h = {"Authorization": "Bearer " + staff_token("demo-http-op@alliedweldingsystems.com", "OPERATOR")}
    listed = client.get("/api/v1/board-edit/boards", headers=h).json()
    assert {b["board_id"] for b in listed} >= set(demo_boards.BOARD_IDS)
    r = client.post(f"/api/v1/board-edit/boards/{ENG}/items/9003/cell", json={"column": "Status", "text": "done"}, headers=h)
    assert r.status_code == 200 and r.json()["staged"]
    v = {"Authorization": "Bearer " + staff_token("demo-http-v@alliedweldingsystems.com", "VIEWER")}
    assert client.post(f"/api/v1/board-edit/boards/{ENG}/items/9003/cell", json={"column": "Status", "text": "done"}, headers=v).status_code == 403


def test_the_real_runner_loads_them_only_after_the_demo_checks_and_real_code_never_imports_the_module():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    users = [p for p in (root / "app").rglob("*.py") if "demo_boards" in p.read_text(encoding="utf-8")]
    assert users == []                                                                       # nothing in the app itself loads sample boards
    run = (root / "scripts" / "demo_run.py").read_text(encoding="utf-8")
    assert run.index("check_demo_db(args.db)") < run.index("demo_boards.install")

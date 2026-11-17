"""Board health: the plain list of what is wrong with a board's data. Reads the app's copy only. Test data only."""
import uuid
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayItemStateDB
from app.services.coordination import board_health, boards

COLS = [{"id": "status", "title": "Status", "type": "status", "labels": {"FABRICATION": "", "SHIPPED": ""}},
        {"id": "po", "title": "Allied PO#", "type": "text"}, {"id": "owner", "title": "Owner", "type": "people"},
        {"id": "promised", "title": "Promised", "type": "date"}]


def _board(monkeypatch, items, columns=COLS):
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": columns, "items": items}
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    monkeypatch.setattr(settings, "VENDOR_PO_COLUMN_ID", "po")
    return board


def test_it_lists_what_is_wrong_in_plain_words(monkeypatch):
    items = [{"id": "a1", "name": "OK ITEM", "values": {"status": "FABRICATION", "po": "9001", "owner": "Ana Lee", "promised": "2026-12-01"}},
             {"id": "a2", "name": "NO OWNER", "values": {"status": "FABRICATION", "po": "9002", "owner": "", "promised": "2026-12-01"}},
             {"id": "a3", "name": "NO DATE", "values": {"status": "FABRICATION", "po": "9003", "owner": "Ana Lee", "promised": ""}},
             {"id": "a4", "name": "NO STATUS", "values": {"status": "", "po": "9004", "owner": "Ana Lee", "promised": "2026-12-01"}},
             {"id": "a5", "name": "DUP ONE", "values": {"status": "FABRICATION", "po": "9005", "owner": "Ana Lee", "promised": "2026-12-01"}},
             {"id": "a6", "name": "DUP TWO", "values": {"status": "FABRICATION", "po": "9005", "owner": "Ana Lee", "promised": "2026-12-01"}},
             {"id": "a7", "name": "FINISHED", "values": {"status": "SHIPPED", "po": "", "owner": "", "promised": ""}}]
    board = _board(monkeypatch, items)
    with SessionLocal() as db:
        db.add(MondayItemStateDB(item_id="a1", board_id=board, label="FABRICATION", item_name="OK ITEM", changed_at=datetime.now(timezone.utc) - timedelta(days=40)))
        db.commit()
    out = board_health.health(board)
    by = {(i["rule"], i["item"]) for i in out["issues"]}
    assert ("no_owner", "NO OWNER") in by and ("no_date", "NO DATE") in by and ("no_status", "NO STATUS") in by
    assert ("duplicate_po", "DUP ONE") in by and ("duplicate_po", "DUP TWO") in by and ("stale", "OK ITEM") in by
    assert not any(i["item"] == "FINISHED" for i in out["issues"])                                   # finished items are not nagged
    assert out["open_items"] == 6 and out["items_with_issues"] == 6 - 0 - 0 and 0 <= out["clean_pct"] <= 100
    assert out["issues"][0]["severity"] == "high" and all(i["suggestion"] and i["message"] for i in out["issues"])


def test_a_board_with_no_people_column_is_told_so_once(monkeypatch):
    cols = [c for c in COLS if c["type"] != "people"]
    board = _board(monkeypatch, [{"id": "b1", "name": "X", "values": {"status": "FABRICATION", "po": "1", "promised": "2026-12-01"}}], cols)
    out = board_health.health(board)
    assert [i["rule"] for i in out["issues"]] == ["no_owner_column"] and out["issues"][0]["item_id"] is None


def test_the_route_serves_it_and_a_missing_board_is_a_404(client, monkeypatch):
    board = _board(monkeypatch, [{"id": "c1", "name": "NO OWNER", "values": {"status": "FABRICATION", "po": "7", "owner": "", "promised": "2026-12-01"}}])
    got = client.get(f"/api/v1/boards/{board}/health")
    assert got.status_code == 200 and got.json()["counts"]["no_owner"] == 1
    assert client.get("/api/v1/boards/999999999/health").status_code == 404

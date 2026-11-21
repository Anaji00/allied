"""The automations gallery: one call, every starter with what it would act on today, its mode and counts. Temporary database; nothing is added or sent."""
from fastapi.testclient import TestClient

from tests.test_contract_screens import api  # noqa: F401


def test_the_gallery_lists_every_starter_in_one_call_and_changes_nothing(api):
    from app.services.coordination import recipes

    before = len(recipes.list_recipes())
    rows = api("GET", "/autopilot/gallery", role="VIEWER").json()
    assert len(rows) == len(recipes.STARTERS) and len(recipes.list_recipes()) == before
    one = rows[0]
    assert {"slug", "title", "description", "added", "mode", "would_act_on_today", "last_run", "waiting_for_ok", "failed"} <= set(one)
    assert one["mode"] in ("off", "suggest", "auto") and isinstance(one["would_act_on_today"], int) and one["waiting_for_ok"] == 0 and one["failed"] == 0


def test_a_timeline_cell_says_its_start_and_end():
    import uuid

    from app.services import board_edit
    from app.services.coordination import boards

    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "T", "role": "projects", "read_at": "now", "groups": [], "columns": [{"id": "tl", "title": "Plan", "type": "timeline"}],
                               "items": [{"id": "1", "name": "A", "values": {"tl": "2026-11-01 - 2026-11-10"}}, {"id": "2", "name": "B", "values": {"tl": ""}}]}
    rows = {r["id"]: r for r in board_edit.board_items(bid)["items"]}
    assert rows["1"]["cells"]["tl"]["start"] == "2026-11-01" and rows["1"]["cells"]["tl"]["end"] == "2026-11-10" and rows["1"]["cells"]["tl"]["type"] == "timeline"
    assert rows["2"]["cells"]["tl"]["start"] is None and rows["2"]["cells"]["tl"]["end"] is None

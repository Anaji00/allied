"""Notices monday board changes within a minute or two, without a webhook.

One small read-only query asks every watched board for its last change (the
board's own time, which also moves when items are deleted, and its most
recently updated item). Only boards whose answer moved are read in full and
synced, and each sync raises its events. That replaces reading every board on a
fixed timer: changes are seen in about MONDAY_CHANGE_CHECK_SECONDS during work
hours, at a fraction of the API calls.

API budget (monday allows about 1,000 calls a day on the Standard plan): every
90 seconds for a 9-hour working day plus every 15 minutes outside it is about
420 checks a day, one call each, plus one read per board that actually changed.
"""

import threading
from typing import Callable, Dict, List, Tuple

from app.config import settings
from app.core.logging import logger
from app.services.coordination import notify as alerts
from app.services.coordination import workdays

_QUERY = ("query ($ids: [ID!]) { boards(ids: $ids) { id updated_at items_page(limit: 1, query_params: "
          "{order_by: [{column_id: \"__last_updated__\", direction: desc}]}) { items { id updated_at } } } }")
_seen: Dict[str, Tuple[str, str]] = {}
_lock = threading.Lock()


def watched() -> Dict[str, Callable[[], object]]:
    """Each configured board and the sync that reads it."""
    from app.services.coordination import autopilot, projects, vendors

    out: Dict[str, Callable[[], object]] = {}
    for board, sync in ((settings.MONDAY_PROJECTS_BOARD_ID, projects.sync_projects),
                        (settings.MONDAY_VENDOR_BOARD_ID, vendors.sync_vendor_board),
                        (settings.MONDAY_RFQ_BOARD_ID, lambda: autopilot.run("rfq_board"))):
        board = (board or "").strip()
        if board and board not in out:
            out[board] = sync
    from app.services.coordination import boards

    for board in boards._ids(settings.MONDAY_REFERENCE_BOARD_IDS):
        out.setdefault(board, lambda b=board: boards.read_reference(b))
    for doc in boards._ids(settings.MONDAY_DOC_IDS):
        out.setdefault(doc, lambda d=doc: boards.read_doc(d))
    return out


def interval() -> int:
    """Seconds until the next check: short in working hours, long outside them."""
    now = alerts.now_local()
    working = workdays.is_workday(now.date()) and settings.BUSINESS_DAY_START <= now.hour < settings.BUSINESS_DAY_END
    return max(30, settings.MONDAY_CHANGE_CHECK_SECONDS if working else settings.MONDAY_CHANGE_CHECK_OFF_HOURS_SECONDS)


def changed(client=None) -> List[str]:
    """Boards whose last change moved since the previous check (every board on the first check)."""
    boards = list(watched())
    if not boards:
        return []
    from app.services.coordination.projects import _client

    client = client or _client()
    if not client.live:
        return []
    data = (client.execute_query(_QUERY, {"ids": boards}).get("data") or {}).get("boards") or []
    out = []
    with _lock:
        for b in data:
            items = (b.get("items_page") or {}).get("items") or [{}]
            mark = (str(b.get("updated_at") or ""), f"{items[0].get('id')}@{items[0].get('updated_at')}")
            if _seen.get(str(b["id"])) != mark:
                out.append(str(b["id"]))
            _seen[str(b["id"])] = mark
    return out


def check(client=None) -> Dict[str, object]:
    """Syncs only the boards that changed. Returns what each sync did."""
    syncs = watched()
    results: Dict[str, object] = {}
    for board in changed(client):
        try:
            results[board] = syncs[board]()
        except Exception as exc:  # noqa: BLE001 - one board failing must not stop the others
            results[board] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
            # Not forgotten: re-reading a failing board every check would burn the API budget.
            # The hourly full read tries it again.
            logger.error(f"Sync after a change on board {board} failed: {exc}")
    return results

"""monday features rebuilt from the boards, read only: activity, time in status, updates, dashboard.

- Activity: monday's activity log for each watched board (who changed what, and when),
  kept for the last ~90 days (monday's API serves no further back) and refreshed
  every time the board is read.
- Time in status: from the status changes in that log, how long items spend in each
  status, and which items have been sitting longest where they are now. monday shows
  this only through a paid column type; here it comes from the log.
- Updates: an item's comment thread and its files, read live when someone opens it.
- Dashboard: monday's battery (items per status, in monday's colours), each person's
  open items, and the dates coming up or already passed, from the board copy.

Nothing is stored in the database and nothing is written to monday.
"""

import json
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.logging import logger

_LOGS = ("query ($b: [ID!], $f: ISO8601DateTime, $p: Int!) { boards(ids: $b) { "
         "activity_logs(from: $f, limit: 500, page: $p) { id event created_at user_id data } } }")
_USERS = "query { users(limit: 500) { id name } }"
_UPDATES = ("query ($i: [ID!]) { items(ids: $i) { id name updates(limit: 50) { id created_at text_body "
            "creator { name } replies { created_at text_body creator { name } } "
            "assets { id name file_extension file_size created_at } } } }")
_KEEP_DAYS = 89
# Changes worth showing; following an item, settings tweaks and the like are left out.
_MEANINGFUL = {"update_column_value", "create_pulse", "delete_pulse", "move_pulse_from_group", "move_pulse_into_group",
               "update_name", "archive_pulse", "batch_delete_pulses", "batch_move_pulses_into_group"}
_logs: Dict[str, Dict[str, Any]] = {}
_users: Dict[str, Any] = {"at": 0.0, "names": {}}
_updates_cache: Dict[str, Any] = {}
_lock = threading.Lock()


def _parse(raw: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    try:
        data = json.loads(raw.get("data") or "{}")
    except ValueError:
        data = {}

    def shown(v: Any) -> Optional[str]:
        if not isinstance(v, dict):
            return None if v in (None, "") else str(v)
        label = v.get("label")
        if isinstance(label, dict):
            return label.get("text")
        if v.get("date"):
            return v["date"]
        if "personsAndTeams" in v:
            return ", ".join(names.get(str(p.get("id")), str(p.get("id"))) for p in v["personsAndTeams"] or [])
        for key in ("text", "value", "name"):
            if v.get(key) not in (None, ""):
                return str(v[key])
        return None

    return {
        "id": raw.get("id"), "event": raw.get("event"), "user": names.get(str(raw.get("user_id")), str(raw.get("user_id") or "")),
        "at": _when(raw.get("created_at")), "item_id": str(data.get("pulse_id") or ""), "item": data.get("pulse_name") or "",
        "column_id": data.get("column_id"), "column": data.get("column_title"), "column_type": data.get("column_type"),
        "from": shown(data.get("previous_value")), "to": shown(data.get("value")),
        "group": (data.get("dest_group") or data.get("group") or {}).get("title") if isinstance(data.get("dest_group") or data.get("group"), dict) else None,
    }


def _when(value: Any) -> Optional[str]:
    """monday sends created_at as a 17-digit count of 100-nanosecond ticks since 1970."""
    try:
        return datetime.fromtimestamp(int(value) / 10_000_000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return str(value) if value else None


def _names(client) -> Dict[str, str]:
    with _lock:
        if time.monotonic() - _users["at"] < 3600 and _users["names"]:
            return _users["names"]
    found = {str(u["id"]): u["name"] for u in ((client.execute_query(_USERS).get("data") or {}).get("users") or [])}
    with _lock:
        _users.update(at=time.monotonic(), names=found)
    return found


def refresh(board_id: str, client) -> int:
    """Adds the board's activity since the last read (the last ~90 days the first time)."""
    board_id = str(board_id)
    with _lock:
        known = _logs.get(board_id, {"entries": {}, "last": None})
    since = known["last"] or (datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS)).isoformat()
    since = (datetime.fromisoformat(since) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = _names(client)
    entries = dict(known["entries"])
    for page in range(1, 21):
        boards = (client.execute_query(_LOGS, {"b": [board_id], "f": since, "p": page}).get("data") or {}).get("boards") or []
        got = boards[0].get("activity_logs") if boards else []
        for raw in got or []:
            entries[str(raw.get("id"))] = _parse(raw, names)  # keyed by id: a page boundary can't double one
        if len(got or []) < 500:
            break
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS)).isoformat()
    entries = {k: v for k, v in entries.items() if (v["at"] or "") >= cutoff}
    with _lock:
        _logs[board_id] = {"entries": entries, "last": datetime.now(timezone.utc).isoformat()}
    return len(entries)


def _entries(board_id: str) -> List[Dict[str, Any]]:
    with _lock:
        b = _logs.get(str(board_id))
        if b is None:
            raise LookupError(f"No activity read for board {board_id} yet.")
        return sorted(b["entries"].values(), key=lambda e: e["at"] or "", reverse=True)


def feed(board_id: str, days: int = 7, item_id: Optional[str] = None) -> Dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [e for e in _entries(board_id) if (e["at"] or "") >= since and e["event"] in _MEANINGFUL
            and (not item_id or e["item_id"] == str(item_id))]
    return {"board_id": str(board_id), "days": days, "changes": rows[:500]}


def status_times(board_id: str, status_column: str = "status", done: Optional[set] = None) -> Dict[str, Any]:
    """How long items spend in each status, from the status changes in the log. Items in a
    finished status (done) are measured but not listed as "sitting longest"."""
    now = datetime.now(timezone.utc)
    changes: Dict[str, List[Dict[str, Any]]] = {}
    for e in _entries(board_id):
        if e["event"] == "update_column_value" and e["column_id"] == status_column:
            changes.setdefault(e["item_id"], []).append(e)
    spent: Dict[str, List[float]] = {}
    current: List[Dict[str, Any]] = []
    for item_id, rows in changes.items():
        rows.sort(key=lambda e: e["at"])
        for here, nxt in zip(rows, rows[1:] + [None]):
            if not here["to"]:
                continue
            start = datetime.fromisoformat(here["at"])
            end = datetime.fromisoformat(nxt["at"]) if nxt else now
            days = (end - start).total_seconds() / 86400
            if nxt:
                spent.setdefault(here["to"], []).append(days)
            elif here["to"].strip().casefold() not in (done or set()):
                current.append({"item_id": item_id, "item": here["item"], "status": here["to"], "since": here["at"],
                                "days": round(days, 1)})
    by_status = [{"status": s, "times_left": len(v), "median_days": round(statistics.median(v), 1),
                  "longest_days": round(max(v), 1)} for s, v in spent.items()]
    by_status.sort(key=lambda r: -r["median_days"])
    current.sort(key=lambda r: -r["days"])
    return {"board_id": str(board_id), "window_days": _KEEP_DAYS, "by_status": by_status, "sitting_longest": current[:50],
            "note": "From status changes in the last ~90 days; an item that hasn't moved since then isn't measured."}


def in_status_since(board_id: str, status_column: str = "status") -> Dict[str, Dict[str, Any]]:
    """Each item's current status and when it moved there, from the log. An item whose last
    move is older than the log (~90 days) is not in it: its time in status is unknown."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in sorted(_entries(board_id), key=lambda e: e["at"] or ""):
        if e["event"] == "update_column_value" and e["column_id"] == status_column and e["item_id"]:
            out[e["item_id"]] = {"status": e["to"], "since": e["at"]}
    return out


def updates(item_id: str, client) -> Dict[str, Any]:
    """An item's comment thread and files, read live (cached for five minutes)."""
    item_id = str(item_id)
    with _lock:
        hit = _updates_cache.get(item_id)
        if hit and time.monotonic() - hit[0] < 300:
            return hit[1]
    items = (client.execute_query(_UPDATES, {"i": [item_id]}).get("data") or {}).get("items") or []
    if not items:
        raise LookupError(f"No monday item {item_id}.")
    it = items[0]
    out = {"item_id": item_id, "item": it.get("name"), "updates": [{
        "at": u.get("created_at"), "by": (u.get("creator") or {}).get("name"), "text": u.get("text_body") or "",
        "replies": [{"at": r.get("created_at"), "by": (r.get("creator") or {}).get("name"), "text": r.get("text_body") or ""}
                    for r in u.get("replies") or []],
        "files": [{"id": a.get("id"), "name": a.get("name"), "type": (a.get("file_extension") or "").lstrip("."),
                   "size": a.get("file_size")} for a in u.get("assets") or []],
    } for u in it.get("updates") or []]}
    with _lock:
        _updates_cache[item_id] = (time.monotonic(), out)
    return out


def dashboard(board_id: str, days_ahead: int = 30) -> Dict[str, Any]:
    """monday's battery, workload and calendar for one board, from the board copy."""
    from app.services.coordination import boards

    view = boards.view(board_id)
    status_id = view["status_column"]
    colours = next((c.get("labels") or {} for c in view["columns"] if c["id"] == status_id), {})
    done = boards._done_labels(view["role"])
    battery: Dict[str, int] = {}
    for r in view["items"]:
        label = r["values"].get(status_id) or "(no status)"
        battery[label] = battery.get(label, 0) + 1
    people_cols = [c["id"] for c in view["columns"] if c["type"] in ("people", "multiple-person")]
    workload: Dict[str, Dict[str, Any]] = {}
    for r in (view["items"] if people_cols else []):  # a board with no person column has no workload
        if (r["values"].get(status_id) or "").strip().casefold() in done:
            continue
        names = [n.strip() for c in people_cols for n in (r["values"].get(c) or "").split(",") if n.strip()] or ["(nobody)"]
        state = (r.get("app") or {}).get("state") or "not tracked"
        for name in names:
            w = workload.setdefault(name, {"open": 0, "by_state": {}})
            w["open"] += 1
            w["by_state"][state] = w["by_state"].get(state, 0) + 1
    today = datetime.now(timezone.utc).date()
    due = _due_columns(view["role"])
    date_cols = [c for c in view["columns"] if c["id"] in due or c["title"] in due]
    calendar = []
    for r in view["items"]:
        if (r["values"].get(status_id) or "").strip().casefold() in done:
            continue
        for c in date_cols:
            value = (r["values"].get(c["id"]) or "")[:10]
            try:
                day = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day <= today + timedelta(days=days_ahead):
                calendar.append({"date": value, "column": c["title"], "item_id": r["id"], "item": r["name"],
                                 "overdue": day < today})
    calendar.sort(key=lambda e: e["date"])
    return {"board_id": str(board_id), "name": view["name"],
            "battery": [{"status": k, "items": v, "color": colours.get(k)} for k, v in sorted(battery.items(), key=lambda kv: -kv[1])],
            "workload": dict(sorted(workload.items(), key=lambda kv: -kv[1]["open"])),
            "calendar": calendar[:300]}


# Where a bar starts on each board, by column title (the first one the board has wins).
_START_TITLES = {"projects": ("scan date", "start date", "start"),
                 "vendors": ("lead start", "vendor po issued", "po issued", "po date", "order date")}


def _col(columns: List[Dict[str, Any]], wanted) -> Optional[Dict[str, Any]]:
    for w in wanted:
        if not w:
            continue
        hit = next((c for c in columns if c["id"] == w or c["title"].strip().casefold() == str(w).casefold()), None)
        if hit:
            return hit
    return None


def _day(value: Any) -> Optional[str]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def timeline(board_id: str, hide_done: bool = True, start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """The board three ways, as monday's Kanban, Gantt and Calendar views show it:

    - kanban: one column per status label, in monday's order and colours;
    - gantt: one bar per item from its start date to its deadline (vendor orders also
      carry the customer's target date, so a bar running past it shows the gap);
    - calendar: every deadline between start and end (default: this month and the next two)."""
    from app.config import settings
    from app.services.coordination import boards

    view = boards.view(board_id, hide_done=hide_done)
    role, status_id, cols = view["role"], view["status_column"], view["columns"]
    labels = next((c.get("labels") or {} for c in cols if c["id"] == status_id), {})
    lanes: Dict[str, Dict[str, Any]] = {k: {"status": k, "color": v, "items": []} for k, v in labels.items()}
    first = _col(cols, _START_TITLES.get(role, ()))
    finish = _col(cols, {"projects": [settings.MILESTONE_DUE_COLUMN_ID, settings.PROJECT_DUE_COLUMN_ID],
                         "vendors": [settings.VENDOR_PROMISED_COLUMN_ID]}.get(role, []))
    target = _col(cols, [settings.VENDOR_CUSTOMER_DUE_COLUMN_ID]) if role == "vendors" else None
    today = datetime.now(timezone.utc).date()
    lo = _day(start) or today.replace(day=1).isoformat()
    hi = _day(end) or (today.replace(day=1) + timedelta(days=92)).isoformat()
    due = [c for c in cols if c["id"] in _due_columns(role) or c["title"] in _due_columns(role)]
    bars, calendar = [], []
    for r in view["items"]:
        app = r.get("app") or {}
        label = r["values"].get(status_id) or "(no status)"
        card = {"id": r["id"], "name": r["name"], "group": r.get("group_title"), "state": app.get("state")}
        lanes.setdefault(label, {"status": label, "color": None, "items": []})["items"].append(card)
        begin = _day(r["values"].get(first["id"])) if first else None
        stop = _day(r["values"].get(finish["id"])) if finish else None
        if role == "rfqs":
            begin, stop = _day(r.get("created_at")), _day(app.get("due_at"))
        if begin or stop:
            bars.append({**card, "status": label, "color": labels.get(label), "start": begin or stop, "end": stop or begin,
                         "target": _day(r["values"].get(target["id"])) if target else None,
                         "late": bool(stop and stop < today.isoformat())})
        for c in due:
            day = _day(r["values"].get(c["id"]))
            if day and lo <= day <= hi:
                calendar.append({"date": day, "column": c["title"], **card, "overdue": day < today.isoformat()})
        if role == "rfqs" and stop and lo <= stop <= hi:
            calendar.append({"date": stop, "column": "Quote due", **card, "overdue": stop < today.isoformat()})
    bars.sort(key=lambda b: (b["start"] or "", b["end"] or ""))
    calendar.sort(key=lambda e: e["date"])
    return {"board_id": str(board_id), "name": view["name"], "role": role, "hide_done": hide_done,
            "kanban": [lane for lane in lanes.values() if lane["items"] or lane["status"] in labels],
            "gantt": {"start_column": first["title"] if first else ("received" if role == "rfqs" else None),
                      "end_column": finish["title"] if finish else ("quote due" if role == "rfqs" else None),
                      "target_column": target["title"] if target else None, "bars": bars[:1000]},
            "calendar": {"from": lo, "to": hi, "entries": calendar[:1000]}}


def _due_columns(role: str) -> set:
    """The dates that are deadlines (Estimate finish, VENDOR ETS, Target Date), not history (PO received)."""
    from app.config import settings

    return {c for c in {
        "projects": [settings.MILESTONE_DUE_COLUMN_ID, settings.PROJECT_DUE_COLUMN_ID],
        "vendors": [settings.VENDOR_PROMISED_COLUMN_ID, settings.VENDOR_CUSTOMER_DUE_COLUMN_ID, settings.VENDOR_REQUESTED_COLUMN_ID],
    }.get(role, []) if c}


def safe_refresh(board_id: str, client) -> None:
    try:
        refresh(board_id, client)
    except Exception as exc:  # noqa: BLE001 - the board copy is still good without its activity
        logger.warning(f"Could not read activity for board {board_id}: {exc}")

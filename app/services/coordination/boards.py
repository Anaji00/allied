"""The watched monday boards, exactly as monday shows them.

Every time the app reads a board (projects, vendor orders, RFQs), it also keeps a
copy of the whole board: its columns in monday's order with their types and status
colours, its groups in order, and every item with every column's text. Screens show
that copy, so what people see in the app lines up with monday column for column, and
each row also carries what the app makes of it (a job's state, a vendor order's
state, an RFQ's number and deadline).

The copy is rebuilt on every read (on start, on each change, and hourly), so nothing
here is stored in the database and nothing is ever written to monday.
"""

import json
import threading
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger
from app.services.coordination import notify as alerts

_META = ("query ($ids: [ID!]) { boards(ids: $ids) { id name columns { id title type width archived settings_str } "
         "groups { id title position color archived deleted } } }")
_mirror: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
ROLES = {"projects": "Projects", "vendors": "Vendor orders", "rfqs": "Quote requests",
         "reference": "For reference (not tracked)", "doc": "monday Doc"}
_DOC = "query ($id: ID!, $p: Int!) { docs(object_ids: [$id]) { name blocks(limit: 100, page: $p) { id type parent_block_id content } } }"


def _ids(raw: str) -> list:
    return [b.strip() for b in (raw or "").split(",") if b.strip()]


def read_reference(board_id: str, client=None) -> dict:
    """Reads a board that is only shown, not tracked."""
    from app.services.coordination.projects import _client
    from app.services.monday_client import fetch_projects_board

    client = client or _client()
    if not client.live:
        return {"skipped": "monday is in simulation"}
    items = fetch_projects_board(client, int(board_id), with_subitems=False)
    remember(board_id, items, client, "reference")
    return {"items": len(items)}


def read_doc(object_id: str, client=None) -> dict:
    """Reads a monday Doc and shows each of its tables as a board (header row = columns)."""
    from app.services.coordination.projects import _client

    client = client or _client()
    if not client.live:
        return {"skipped": "monday is in simulation"}
    blocks, name, page = [], "", 1
    while page <= 20:
        docs = (client.execute_query(_DOC, {"id": str(object_id), "p": page}).get("data") or {}).get("docs") or []
        if not docs:
            break
        name = docs[0].get("name") or name
        got = docs[0].get("blocks") or []
        blocks += got
        if len(got) < 100:
            break
        page += 1
    children: Dict[Optional[str], List[Dict[str, Any]]] = {}
    for b in blocks:
        children.setdefault(b.get("parent_block_id"), []).append(b)

    def text(block_id: str) -> str:
        parts = []
        for child in children.get(block_id, []):
            try:
                ops = json.loads(child.get("content") or "{}").get("deltaFormat") or []
            except ValueError:
                ops = []
            parts.append("".join(op["insert"] for op in ops if isinstance(op.get("insert"), str)))
            parts.append(text(child["id"]))
        return " ".join(p.strip() for p in parts if p and p.strip())

    rows: List[List[str]] = []
    for table in (b for b in blocks if b.get("type") == "table"):
        cells = json.loads(table.get("content") or "{}").get("cells") or []
        grid = [[text(c.get("blockId")) for c in row] for row in cells]
        if grid:
            rows = grid  # the doc's (last) table; Allied's tracker has one
    header = rows[0] if rows else []
    columns = [{"id": f"c{i}", "title": title or f"Column {i + 1}", "type": "text"} for i, title in enumerate(header)]
    items = [{"id": f"{object_id}-{n}", "name": row[0] if row else "", "created_at": None, "group": "doc", "group_title": name,
              "values": {f"c{i}": v for i, v in enumerate(row)}} for n, row in enumerate(rows[1:], 1) if any(row)]
    with _lock:
        _mirror[str(object_id)] = {"board_id": str(object_id), "name": name or str(object_id), "role": "doc",
                                   "read_at": alerts.now_local().isoformat(), "columns": columns,
                                   "groups": [{"id": "doc", "title": name or "Doc", "color": None}], "items": items}
    return {"rows": len(items)}


def read_all_reference(client=None) -> dict:
    """Every reference board and doc, read once (start-up and the hourly full read)."""
    out = {}
    for board in _ids(settings.MONDAY_REFERENCE_BOARD_IDS):
        try:
            out[board] = read_reference(board, client)
        except Exception as exc:  # noqa: BLE001 - one board failing must not stop the others
            out[board] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    for doc in _ids(settings.MONDAY_DOC_IDS):
        try:
            out[doc] = read_doc(doc, client)
        except Exception as exc:  # noqa: BLE001
            out[doc] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def _labels(column: Dict[str, Any]) -> Dict[str, str]:
    """A status column's label -> colour, as monday paints it."""
    try:
        s = json.loads(column.get("settings_str") or "{}")
    except ValueError:
        return {}
    colours = s.get("labels_colors") or {}
    order = s.get("labels_positions_v2") or {}
    keys = sorted((s.get("labels") or {}), key=lambda k: (int(order.get(k, 999)), int(k) if str(k).isdigit() else 999))
    # In the order monday lists them (labels_positions_v2), which is how Kanban lanes run.
    return {s["labels"][k]: (colours.get(k) or {}).get("color", "") for k in keys if s["labels"][k]}


def _formula(value: Any) -> str:
    """A formula's result as monday shows it: whole days, and dates without a time."""
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(round(number))) if abs(number - round(number)) > 1e-9 or "." in text else text


def remember(board_id: str, items: List[Dict[str, Any]], client, role: str) -> None:
    """Keeps a copy of a board just read. The column and group layout is one small read."""
    board_id = str(board_id)
    meta: Dict[str, Any] = {}
    try:
        boards = (client.execute_query(_META, {"ids": [board_id]}).get("data") or {}).get("boards") or []
        meta = boards[0] if boards else {}
    except Exception as exc:  # noqa: BLE001 - keep the last layout; the items are still worth showing
        logger.warning(f"Could not read the layout of board {board_id}: {exc}")
    with _lock:
        old = _mirror.get(board_id, {})
        columns = [
            {"id": c["id"], "title": c["title"], "type": c["type"], "width": c.get("width"),
             **({"labels": _labels(c)} if c["type"] in ("status", "color") else {})}
            for c in meta.get("columns") or [] if not c.get("archived") and c["type"] != "subtasks"
        ] or old.get("columns") or []
        groups = sorted(
            [{"id": g["id"], "title": g["title"], "color": g.get("color")}
             for g in meta.get("groups") or [] if not g.get("archived") and not g.get("deleted")],
            key=lambda g: float(next((x.get("position") or 0 for x in meta["groups"] if x["id"] == g["id"]), 0)),
        ) or old.get("groups") or []
        _mirror[board_id] = {
            "board_id": board_id, "name": meta.get("name") or old.get("name") or board_id, "role": role,
            "read_at": alerts.now_local().isoformat(), "columns": columns, "groups": groups,
            "items": [{
                "id": str(i["id"]), "name": i.get("name") or "", "created_at": i.get("created_at"),
                "group": (i.get("group") or {}).get("id"), "group_title": (i.get("group") or {}).get("title"),
                "values": {c["id"]: c.get("text") or _formula(c.get("display_value")) for c in i.get("column_values") or []},
            } for i in items],
        }
    if role in ("projects", "vendors", "rfqs"):
        from app.services.coordination import activity

        activity.safe_refresh(board_id, client)  # who changed what since the last read


def boards() -> List[Dict[str, Any]]:
    with _lock:
        return [{"board_id": b["board_id"], "name": b["name"], "role": b["role"], "label": ROLES.get(b["role"], b["role"]),
                 "read_at": b["read_at"], "items": len(b["items"])} for b in _mirror.values()]


def _done_labels(role: str) -> set:
    raw = {"projects": settings.MILESTONE_DONE_LABELS, "vendors": settings.VENDOR_DONE_LABELS}.get(role, "")
    return {p.strip().casefold() for p in (raw or "").split(",") if p.strip()}


def _app_view(role: str) -> Dict[str, Dict[str, Any]]:
    """What the app makes of each item, keyed by the monday item id."""
    if role == "projects":
        from app.services.coordination import projects

        return {m["item_id"]: {"state": m["state"], "days": m["days"], "owners": m.get("owner_emails") or []}
                for p in projects.overview()["projects"] for m in p["milestones"]}
    if role == "vendors":
        from app.services.coordination import vendors

        return {j["monday_item_id"]: {"job_id": j["id"], "state": j["state"], "expected": j.get("expected"),
                                      "need_by": j.get("need_by"), "reasons": j.get("reasons") or []}
                for j in vendors.overview(include_shipped=True)["jobs"] if j.get("monday_item_id")}
    if role == "rfqs":
        from app.services.coordination import rfqs

        return {r["monday_item_id"]: {"rfq_id": r["id"], "number": r["number"], "state": r.get("state"),
                                      "status": r["status"], "due_at": r.get("effective_due_at") or r.get("due_at")}
                for r in rfqs.list_rfqs(open_only=False) if r.get("monday_item_id")}
    return {}


def view(board_id: str, hide_done: bool = False) -> Dict[str, Any]:
    """The board as monday shows it, with the app's view beside each row.

    hide_done leaves out finished items (monday's "Not Complete" view on the
    Engineering board; on 2026 Orders, orders that left the vendor)."""
    with _lock:
        b = _mirror.get(str(board_id))
        if b is None:
            raise LookupError(f"Board {board_id} has not been read yet (is it set up, and is monday out of simulation?).")
        b = json.loads(json.dumps(b))
    app = _app_view(b["role"])
    done = _done_labels(b["role"])
    status_col = {"projects": settings.MILESTONE_STATUS_COLUMN_ID, "vendors": settings.VENDOR_STATUS_COLUMN_ID,
                  "rfqs": settings.RFQ_BOARD_STATUS_COLUMN_ID}.get(b["role"]) or "status"
    status_id = next((c["id"] for c in b["columns"] if status_col in (c["id"], c["title"])), "status")
    rows = []
    for item in b["items"]:
        if hide_done and (item["values"].get(status_id) or "").strip().casefold() in done:
            continue
        rows.append({**item, "app": app.get(item["id"])})
    return {**{k: v for k, v in b.items() if k != "items"}, "label": ROLES.get(b["role"], b["role"]),
            "status_column": status_id, "hide_done": hide_done, "items": rows}

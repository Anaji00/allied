"""Board health: what is wrong with the data on a board, in plain words, with what to do about it.

monday takes whatever people type. This reads the app's copy of a board and lists what makes it hard to run: an item with nobody on it,
no deadline, no status, a stale item nobody has touched, one PO number on two open lines. Each issue says what to fix and where. Nothing here
changes monday or the app's data; fixes made in the app go to monday through the staged push, and a fix made in monday shows up on the next read."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayItemStateDB
from app.services.coordination import boards

SEVERITY = {"no_owner": "high", "duplicate_po": "high", "no_status": "high", "no_date": "medium", "stale": "medium", "no_owner_column": "high"}


def _issue(rule: str, item: Optional[Dict[str, Any]], message: str, suggestion: str) -> Dict[str, Any]:
    return {"rule": rule, "severity": SEVERITY.get(rule, "low"), "item_id": item["id"] if item else None, "item": item["name"] if item else None,
            "message": message, "suggestion": suggestion}


def _col(columns: List[Dict[str, Any]], ident: str) -> Optional[str]:
    return next((c["id"] for c in columns if ident and ident in (c["id"], c["title"])), None)


def health(board_id: str, today: Optional[datetime] = None) -> Dict[str, Any]:
    from app.services.coordination.activity import _due_columns

    v = boards.view(board_id)
    cols = v["columns"]
    role = v.get("role") or ""
    done = boards._done_labels(role)
    now = today or datetime.now(timezone.utc)
    people = [c["id"] for c in cols if c["type"] in ("people", "multiple-person")]
    due_ids = {c["id"] for c in cols if c["id"] in _due_columns(role) or c["title"] in _due_columns(role)}
    dates = list(due_ids) or [c["id"] for c in cols if c["type"] in ("date", "timeline")]
    po_col = _col(cols, settings.VENDOR_PO_COLUMN_ID) if role == "vendors" else None
    status_id = v["status_column"]

    issues: List[Dict[str, Any]] = []
    if not people:
        issues.append(_issue("no_owner_column", None, "This board has no People column, so no item can have an owner.",
                             "Add a People column named Owner in monday. Until then the app can only track an owner on its own side."))
    with SessionLocal() as db:
        changed = {r.item_id: r.changed_at for r in db.query(MondayItemStateDB).filter(MondayItemStateDB.board_id == str(board_id)).all()}

    open_items = [i for i in v["items"] if (i["values"].get(status_id) or "").strip().casefold() not in done]
    pos: Dict[str, List[Dict[str, Any]]] = {}
    for item in open_items:
        vals = item["values"]
        if people and not any((vals.get(p) or "").strip() for p in people):
            issues.append(_issue("no_owner", item, "Nobody is on this item.", "Put the person who is responsible in the owner column."))
        if not (vals.get(status_id) or "").strip():
            issues.append(_issue("no_status", item, "It has no status.", "Set its status so it shows up in the right list."))
        if dates and not any((vals.get(d) or "").strip() for d in dates):
            issues.append(_issue("no_date", item, "It has no deadline.", "Add the date it is due or promised."))
        at = changed.get(item["id"])
        if at is not None:
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            days = (now - at).days
            if days >= settings.BOARD_STALE_DAYS:
                issues.append(_issue("stale", item, f"Its status has not changed in {days} days.", "Check whether it is stuck, and update its status or add a note."))
        if po_col and (vals.get(po_col) or "").strip():
            pos.setdefault(vals[po_col].strip(), []).append(item)
    for po, rows in pos.items():
        if len(rows) > 1:
            for item in rows:
                issues.append(_issue("duplicate_po", item, f"PO {po} is on {len(rows)} open lines.", "One PO number should belong to one line. Check which is right."))

    flagged = {i["item_id"] for i in issues if i["item_id"]}
    counts: Dict[str, int] = {}
    for i in issues:
        counts[i["rule"]] = counts.get(i["rule"], 0) + 1
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i["rule"], i["item"] or ""))
    return {"board_id": str(board_id), "label": v["label"], "open_items": len(open_items), "items_with_issues": len(flagged),
            "clean_pct": round(100 * (len(open_items) - len(flagged)) / len(open_items)) if open_items else 100, "counts": counts, "issues": issues}


def summary(board_id: str, email: str = "") -> Dict[str, Any]:
    """{"items": {item_id: {...}}}: one small record per board item in one call, so a board table shows its row badges without asking for each row:
    drafts waiting for a person's OK, engineering notes this person has not read, problems (with their rules), automations that apply to the item,
    and who holds the ball. Items with nothing to show are absent."""
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB, EngineeringNoteDB
    from app.services.coordination import ball, recipes

    v = boards.view(board_id)
    items: Dict[str, Dict[str, Any]] = {}

    def at(item_id: Any) -> Dict[str, Any]:
        return items.setdefault(str(item_id), {"drafts_waiting": 0, "unread_notes": 0, "issues": 0, "issue_rules": [], "automations_on": 0, "ball": None})

    for i in health(board_id)["issues"]:
        if i["item_id"]:
            e = at(i["item_id"])
            e["issues"] += 1
            e["issue_rules"].append(i["rule"])

    role = v.get("role") or ""
    by_app: Dict[str, str] = {}                      # the app's own ids for an item -> its board item id
    for item in v["items"]:
        for key in ("rfq_id", "job_id"):
            if (item.get("app") or {}).get(key) is not None:
                by_app[f"{key}:{(item['app'])[key]}"] = str(item["id"])
    with SessionLocal() as db:
        for a in db.query(AssistantActionDB).filter(AssistantActionDB.status == "PENDING", AssistantActionDB.user_role == "AUTOPILOT").limit(1000):
            args = a.args or {}
            target = str(args["_item"]) if args.get("_item") is not None else (by_app.get(f"rfq_id:{args['_rfq_id']}") if args.get("_rfq_id") is not None
                                                                              else by_app.get(f"job_id:{args['_job_id']}") if args.get("_job_id") is not None else None)
            if target and any(str(i["id"]) == target for i in v["items"]):
                at(target)["drafts_waiting"] += 1
        me = (email or "").strip().lower()
        if me:
            for n in db.query(EngineeringNoteDB).filter(EngineeringNoteDB.board_id == str(board_id)).all():
                if me in [r.lower() for r in (n.recipients or [])] and me not in {k.lower() for k in (n.read_by or {})}:
                    at(n.item_id)["unread_notes"] += 1

    status_id = v["status_column"]
    live = [r for r in recipes.list_recipes(role) if r.get("enabled", True)] if role in recipes.ROLES else []
    if live:
        for item in v["items"]:
            vals = item["values"]
            n_on = 0
            for r in live:
                if r["trigger"] == "status_is" and (vals.get(status_id) or "").strip().casefold() == (r.get("value") or "").strip().casefold():
                    n_on += 1
                elif r["trigger"] == "date_within" and (vals.get(r.get("column") or "") or "").strip():
                    n_on += 1
            if n_on:
                at(item["id"])["automations_on"] = n_on

    kind = {"vendors": ("vendor_job", "job_id"), "rfqs": ("rfq", "rfq_id")}.get(role)
    if kind:
        for item in v["items"]:
            ref = (item.get("app") or {}).get(kind[1])
            if ref is None:
                continue
            try:
                b = ball.get(kind[0], str(ref))
            except Exception:  # noqa: BLE001 - an item with no ball is simply left without one
                b = None
            if b and not b.get("resolved"):
                at(item["id"])["ball"] = {"with": b["ball_label"], "person": b["ball_name"], "days_waiting": b["days_waiting"], "status": b["status"]}
    return {"items": items}

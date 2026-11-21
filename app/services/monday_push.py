"""Staged monday changes: write now, push later, like git.

Whenever the app wants to change something on monday (post an update, set columns on an item, add a row) and monday is
connected, the change is STAGED here instead of being sent: a saved to-do with a plain summary. Staging itself never
touches monday. A person looks over what is staged (`preview`: what it would change, with what monday shows now) and
pushes it (`push`), all of it or a chosen few, when they decide to. Nothing is pushed while `MONDAY_READ_ONLY` is on
(a push then says so and sends nothing), and this module never changes that switch: only the owner can.

Same item, same columns staged twice: one change with the latest values, like a file edited twice before a commit.
The same comment twice is one comment. A change nobody pushed for `MONDAY_STAGE_DAYS` days expires, because monday
will have moved on. Set `MONDAY_PUSH_MODE=auto` to send changes as they are made (still blocked while read-only, and
then staged instead of lost).

Where monday is not connected (simulation mode, tests) nothing is staged and nothing changes.
"""

import contextlib
import contextvars
import hashlib
import json
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import MondayChangeDB

KINDS = ("create_item", "update_columns", "post_update", "archive_item", "move_item", "duplicate_item",
         "create_column", "rename_column", "delete_column", "create_group", "rename_group")
_PUSHING: contextvars.ContextVar[bool] = contextvars.ContextVar("monday_pushing", default=False)


@contextlib.contextmanager
def pushing():
    """Inside this block the client sends changes for real (a push is in progress) instead of staging them."""
    token = _PUSHING.set(True)
    try:
        yield
    finally:
        _PUSHING.reset(token)


def should_stage(client: Any) -> bool:
    """True when a change should be saved for a later push rather than sent now."""
    return bool(client.live) and not _PUSHING.get() and (settings.MONDAY_PUSH_MODE != "auto" or settings.MONDAY_READ_ONLY)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


# ----------------------------------------------------------------------------- staging

def _col_id(board_id: str, key: str) -> str:
    """The column id for a column named by id or by title on this board (the key itself when the board is not known)."""
    cols = _mirror(board_id).get("columns", [])
    hit = next((c for c in cols if c["id"] == key), None)
    if hit is None:
        titled = [c for c in cols if c["title"].casefold() == str(key).casefold()]
        hit = titled[0] if len(titled) == 1 else None            # two columns with one title are not guessed between
    return hit["id"] if hit else str(key)


def _auto_base(board_id: str, item_id: str, columns: Dict[str, Any]) -> Dict[str, Any]:
    """What the app's copy of the board shows for these cells now. This is what an edit is made against: every staged edit carries it, so a push can
    tell whether someone changed the cell on monday in between (a cell the app saw blank is a blank base, and a value that appears later is a conflict)."""
    item = next((i for i in _mirror(board_id).get("items", []) if str(i.get("id")) == str(item_id)), None)
    if item is None:
        return {}
    return {_col_id(board_id, k): item.get("values", {}).get(_col_id(board_id, k)) or "" for k in columns}


_stage_lock = threading.RLock()      # two people staging cells of one row at once must both be kept


def stage(kind: str, board_id: Any, item_id: Any, payload: Dict[str, Any], summary: str, key: str = "", source: str = "app", by: str = "app") -> int:
    """Save a change for a later push and return its number. Repeats are folded into the one already waiting."""
    if kind not in KINDS:
        raise ValueError(f"Unknown change kind: {kind}")
    board_id = str(board_id or "")
    item_id = str(item_id or "")
    with _stage_lock, SessionLocal() as db:
        waiting = db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.kind == kind,
                                                  MondayChangeDB.board_id == board_id, MondayChangeDB.item_id == item_id)
        if kind == "update_columns":
            payload = {**payload, "columns": {_col_id(board_id, k): v for k, v in payload.get("columns", {}).items()}}      # always by column id, never by title
            payload["base"] = {**_auto_base(board_id, item_id, payload["columns"]), **(payload.get("base") or {})}
            row = waiting.filter(MondayChangeDB.staged_by == by[:256]).first()          # each person's edits to a row are their own staged change, so each can be undone on its own
            if row:
                old = row.payload or {}
                row.payload = {"columns": {**old.get("columns", {}), **payload.get("columns", {})},
                               "base": {**payload.get("base", {}), **old.get("base", {})}}      # the earliest base wins: it is what the edit was made against
                row.summary, row.updated_at = summary, _now()
                db.commit()
                return row.id
            dedupe = _digest(kind, board_id, item_id)
        elif kind == "post_update":
            dedupe = key or _digest(kind, item_id, payload.get("text"))
            row = waiting.filter(MondayChangeDB.dedupe_key == dedupe).first()
            if row:
                return row.id
        else:
            dedupe = key or _digest(kind, board_id, payload.get("item_name"))
            row = waiting.filter(MondayChangeDB.dedupe_key == dedupe).first()
            if row:
                row.payload, row.summary, row.updated_at = payload, summary, _now()
                db.commit()
                return row.id
        row = MondayChangeDB(kind=kind, board_id=board_id, item_id=item_id, payload=payload, summary=summary[:500], source=source[:40],
                             status="STAGED", dedupe_key=dedupe, staged_by=by[:256], attempts=0)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def attach(change_id: int, **extra: Any) -> None:
    """Add facts to a waiting change (the RFQ it belongs to, so the new row can be linked to it when pushed)."""
    with SessionLocal() as db:
        row = db.get(MondayChangeDB, change_id)
        if row is not None and row.status == "STAGED":
            row.payload = {**(row.payload or {}), **extra}
            db.commit()


# ----------------------------------------------------------------------------- looking at what is staged

def _as_dict(r: MondayChangeDB) -> Dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "board_id": r.board_id, "item_id": r.item_id or None, "summary": r.summary, "source": r.source,
            "status": r.status, "payload": r.payload or {}, "staged_by": r.staged_by, "attempts": r.attempts, "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None, "result": r.result}


def changes(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    _settle_stale_pushes()
    with SessionLocal() as db:
        q = db.query(MondayChangeDB)
        if status:
            q = q.filter(MondayChangeDB.status == status.upper())
        return [_as_dict(r) for r in q.order_by(MondayChangeDB.created_at.desc()).limit(limit)]


def status() -> Dict[str, Any]:
    from app.core.container import get_monday_client

    client = get_monday_client()
    counts: Dict[str, int] = {}
    with SessionLocal() as db:
        for (st,) in db.query(MondayChangeDB.status):
            counts[st] = counts.get(st, 0) + 1
    ready = bool(client.live) and not settings.MONDAY_READ_ONLY
    detail = ("Simulation mode: monday is not connected, nothing is staged or sent." if not client.live else
              "monday is read-only: changes are staged and nothing is pushed until the owner turns writing on (MONDAY_READ_ONLY)." if settings.MONDAY_READ_ONLY
              else "Writing is on: staged changes can be pushed.")
    return {"connected": bool(client.live), "read_only": bool(settings.MONDAY_READ_ONLY), "can_push": ready, "mode": settings.MONDAY_PUSH_MODE,
            "staged": counts.get("STAGED", 0), "failed": counts.get("FAILED", 0), "counts": counts, "detail": detail}


def _mirror(board_id: str) -> Dict[str, Any]:
    from app.services.coordination import boards

    return boards._mirror.get(str(board_id)) or {}


def _label(board_id: str, item_id: str) -> Dict[str, Any]:
    b = _mirror(board_id)
    item = next((i for i in b.get("items", []) if str(i.get("id")) == str(item_id)), None) if item_id else None
    return {"board": b.get("name") or board_id or "?", "item": (item or {}).get("name"), "board_read_at": b.get("read_at"), "item_found": item is not None, "_item": item, "_b": b}


def preview(ids: Optional[List[int]] = None, board_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """What each waiting change would do, with what monday shows now (from the app's last read of the board). `board_id` limits it to one board."""
    out = []
    with SessionLocal() as db:
        q = db.query(MondayChangeDB).filter(MondayChangeDB.status.in_(("STAGED", "FAILED", "CONFLICT")))
        if ids:
            q = q.filter(MondayChangeDB.id.in_(ids))
        if board_id:
            q = q.filter(MondayChangeDB.board_id == str(board_id))
        rows = [_as_dict(r) for r in q.order_by(MondayChangeDB.created_at).all()]
    for c in rows:
        info = _label(c["board_id"], c["item_id"] or "")
        cols = {col["id"]: col["title"] for col in info["_b"].get("columns", [])}
        notes: List[str] = []
        entry = {"id": c["id"], "kind": c["kind"], "summary": c["summary"], "board": info["board"], "board_id": str(c["board_id"] or ""), "item": info["item"],
                 "item_id": str(c["item_id"] or "") or None, "staged_at": c["created_at"],
                 "status": c["status"], "error": c["error"], "notes": notes}
        if c["kind"] == "update_columns":
            fields = c["payload"].get("columns", {})
            before = (info["_item"] or {}).get("values", {})
            base = c["payload"].get("base") or {}
            entry["changes"] = [{"column": cols.get(k, k), "column_id": k, "base": base.get(k), "now": before.get(k), "becomes": _show(v)} for k, v in fields.items()]
            if c["status"] == "CONFLICT":
                notes.append("monday changed since this edit was made. Re-base it onto what monday shows now to keep your change, or skip it.")
            if c["item_id"] and not info["item_found"]:
                notes.append("This item isn't in the app's copy of the board (it may be new, archived or the board needs reading again).")
        elif c["kind"] == "post_update":
            entry["comment"] = c["payload"].get("text")
        elif c["kind"] == "create_item":
            entry["new_row"] = {"name": c["payload"].get("item_name"), "fields": c["payload"].get("fields", {}), "group_id": c["payload"].get("group_id")}
        else:
            entry["action"] = {k: v for k, v in c["payload"].items() if k != "base"}
        if info["board_read_at"]:
            notes.append(f"Compared with the board as the app last read it ({str(info['board_read_at'])[:16].replace('T', ' ')}).")
        made = datetime.fromisoformat(c["created_at"]) if c["created_at"] else _now()
        age = (_now() - (made if made.tzinfo else made.replace(tzinfo=timezone.utc))).days
        if age >= 3:
            notes.append(f"Staged {age} days ago: check monday hasn't changed since.")
        out.append(entry)
    return out


def _show(v: Any) -> Any:
    if isinstance(v, dict):
        return v.get("label") or v.get("labels") or v.get("text") or v.get("date") or v
    return v


# ----------------------------------------------------------------------------- pushing

def _shape(board_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Column values the way monday wants them, using the app's last read of the board's columns (by title or id)."""
    cols = _mirror(board_id).get("columns", [])
    by_title = {c["title"].casefold(): c for c in cols}
    by_id = {c["id"]: c for c in cols}
    out: Dict[str, Any] = {}
    for key, value in fields.items():
        if value in (None, ""):
            continue
        col = by_id.get(key) or by_title.get(str(key).casefold())
        cid, ctype = (col["id"], col["type"]) if col else (key, "")
        if isinstance(value, dict):
            out[cid] = value
        elif ctype in ("status", "color"):
            out[cid] = {"label": str(value)}
        elif ctype == "dropdown":
            out[cid] = {"labels": [str(value)]}
        elif ctype == "checkbox":
            out[cid] = {"checked": "true" if (value is True or str(value).strip().casefold() in _YES) else "false"}
        elif ctype == "rating":
            if re.fullmatch(r"[1-5]", str(value).strip(), re.ASCII):
                out[cid] = {"rating": int(str(value).strip())}
        elif ctype == "email":
            out[cid] = {"email": str(value).strip(), "text": str(value).strip()}
        elif ctype == "link":
            out[cid] = {"url": str(value).strip(), "text": str(value).strip()}
        elif ctype == "timeline":
            tl = _timeline(str(value))
            if tl:
                out[cid] = {"from": tl[0], "to": tl[1]}
        elif ctype == "hour":
            hm = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(value).strip(), re.ASCII)
            if hm:
                out[cid] = {"hour": int(hm.group(1)), "minute": int(hm.group(2))}
        elif ctype == "week":
            wk = re.fullmatch(r"(\d{4})-W(\d{2})", str(value).strip(), re.ASCII)
            if wk:
                try:
                    start = datetime.fromisocalendar(int(wk.group(1)), int(wk.group(2)), 1)
                    out[cid] = {"week": {"startDate": start.date().isoformat(), "endDate": (start + timedelta(days=6)).date().isoformat()}}
                except ValueError:
                    pass
        elif ctype in ("phone", "tags"):
            continue
        elif ctype == "date":
            out[cid] = {"date": str(value)[:10]}
        elif ctype == "long_text":
            out[cid] = {"text": str(value)}
        elif ctype in ("people", "board_relation", "formula", "mirror", "subtasks"):
            continue
        else:
            out[cid] = str(value)
    return out


def _live_values(client: Any, item_id: Any, col_ids: List[str]) -> Dict[str, str]:
    """What monday shows right now for these columns of one item (a read; nothing is written)."""
    data = client.execute_query("query ($ids: [ID!], $cols: [String!]) { items(ids: $ids) { column_values(ids: $cols) { id text } } }",
                                {"ids": [str(item_id)], "cols": list(col_ids)})
    items = ((data or {}).get("data") or {}).get("items") or []
    return {cv["id"]: (cv.get("text") or "").strip() for cv in ((items[0] if items else {}).get("column_values") or [])}


def _plain(v: Any) -> str:
    """A cell's value as comparable text: a dict (a label, a date) by its visible part, spaces collapsed, nothing as ''."""
    if isinstance(v, dict):
        v = _show(v)
    return " ".join(str(v if v is not None else "").split())


def _moved(base: Dict[str, Any], live: Dict[str, str], target: Optional[Dict[str, Any]] = None) -> List[str]:
    """The columns whose value on monday is no longer the one the edit was made against. A column that already shows what the edit would set is
    not a conflict (nothing would change)."""
    target = target or {}
    return [k for k, v in base.items() if _plain(v) != _plain(live.get(k, "")) and _plain(live.get(k, "")) != _plain(target.get(k))]


def _settle_stale_pushes() -> None:
    """A push that died half-way leaves changes marked PUSHING: after ten minutes they are FAILED (and flagged maybe_sent), so a person can look and decide."""
    with SessionLocal() as db:
        db.query(MondayChangeDB).filter(MondayChangeDB.status == "PUSHING", MondayChangeDB.updated_at < _now() - timedelta(minutes=10)).update(
            {MondayChangeDB.status: "FAILED", MondayChangeDB.error: "A push was interrupted while sending this. Check monday, then retry.",
             MondayChangeDB.result: {"maybe_sent": True}}, synchronize_session=False)
        db.commit()


def push(ids: Optional[List[int]] = None, who: str = "", dry_run: bool = False, board_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Send waiting changes to monday (all, those listed, or one board's), each to its own board. Before an edit goes, monday is read again: if
    someone changed a column since the edit was made, that change is held as a CONFLICT (nothing is overwritten) unless `force` says to. Sends
    nothing while monday is read-only or not connected."""
    from app.core.container import get_monday_client

    client = get_monday_client()
    _settle_stale_pushes()
    with SessionLocal() as db:
        q = db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED")
        if ids:
            q = q.filter(MondayChangeDB.id.in_(ids))
        if board_id:
            q = q.filter(MondayChangeDB.board_id == str(board_id))
        waiting = [r.id for r in q.order_by(MondayChangeDB.created_at)]
    if not waiting:
        return {"ok": True, "pushed": 0, "failed": 0, "message": "Nothing is staged."}
    if not client.live:
        return {"ok": False, "pushed": 0, "failed": 0, "blocked": "simulation", "staged": len(waiting),
                "message": "monday isn't connected (simulation mode), so nothing was sent. The changes stay staged."}
    if settings.MONDAY_READ_ONLY:
        return {"ok": False, "pushed": 0, "failed": 0, "blocked": "read-only", "staged": len(waiting), "would_push": preview(waiting),
                "message": f"monday is read-only (MONDAY_READ_ONLY), so nothing was sent. {len(waiting)} change(s) stay staged "
                           "until the owner turns writing on."}
    if dry_run:
        return {"ok": True, "pushed": 0, "failed": 0, "dry_run": True, "would_push": preview(waiting), "message": f"{len(waiting)} change(s) would be sent."}
    pushed = failed = 0
    results = []
    for cid in waiting:
        with SessionLocal() as db:       # claim it: of two pushes at once (two managers, a double click) exactly one sends this change
            won = (db.query(MondayChangeDB).filter(MondayChangeDB.id == cid, MondayChangeDB.status == "STAGED")
                   .update({MondayChangeDB.status: "PUSHING", MondayChangeDB.updated_at: _now()}, synchronize_session=False))
            db.commit()
            if won != 1:
                continue
            row = db.get(MondayChangeDB, cid)
            kind, board, item, payload, key = row.kind, row.board_id, row.item_id, dict(row.payload or {}), row.dedupe_key
        error, result = None, {}
        columns = payload.get("columns", {}) if kind == "update_columns" else {}
        base = {_col_id(board, k): "" for k in columns}                      # an edit with no recorded base is checked against a BLANK cell: only blanks are filled
        base.update(payload.get("base") or {})
        if kind in ("update_columns", "create_item"):    # the board's labels may have changed since staging: never send a label the board no longer has, and send the board's own spelling
            try:
                by_col = {c["id"]: c for c in _mirror(board).get("columns", [])}
                key_name = "columns" if kind == "update_columns" else "fields"
                checked = {}
                for k, v in payload.get(key_name, {}).items():
                    col = by_col.get(_col_id(board, k))
                    if col is None and by_col:                                              # the column was deleted on monday (or renamed away) since the cell was staged: this row is not sent
                        raise ValueError(f"The column '{str(k)[:40]}' is no longer on the board.")
                    checked[k] = check_cell(board, col["id"], v, new_row=(kind == "create_item"))[1] if col else v      # the full check, whoever staged it and however it got here (retry, rebase, old rows, force)
                payload[key_name] = checked
                columns = checked if kind == "update_columns" else columns
            except ValueError as exc:
                error = f"Not sent: {exc} Skip this change and stage it again."
        if kind == "update_columns" and not force and not error:
            try:
                target = {_col_id(board, k): v for k, v in columns.items()}
                moved = _moved(base, _live_values(client, item, list(base)), target)
            except Exception as exc:  # noqa: BLE001 - if monday cannot be read, nothing is overwritten blind
                moved, error = [], f"Could not check monday before sending ({type(exc).__name__}); try again."
            if moved:
                cols = {c["id"]: c["title"] for c in _mirror(board).get("columns", [])}
                with SessionLocal() as db:
                    row = db.get(MondayChangeDB, cid)
                    row.status, row.error = "CONFLICT", "monday changed since this was staged: " + ", ".join(cols.get(k, k) for k in moved)
                    row.result = {"conflict": moved}
                    db.commit()
                failed += 1
                results.append({"id": cid, "ok": False, "conflict": moved, "error": "monday changed since this was staged"})
                continue
        try:
            if error:
                raise RuntimeError(error)
            with pushing():
                if kind == "post_update":
                    ok = client.post_pulse_update(int(item), payload.get("text", ""), idempotency_key=f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the comment."
                elif kind == "update_columns":
                    ok = client.update_column_values(int(board), int(item), _shape(board, payload.get("columns", {})), f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the column changes."
                elif kind == "archive_item":
                    ok = client.archive_item(int(item), f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the archive."
                elif kind == "move_item":
                    ok = client.move_item_to_group(int(item), payload.get("group_id", ""), f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the move."
                elif kind == "duplicate_item":
                    new_id = client.duplicate_item(board, int(item), bool(payload.get("with_updates")), f"push-{cid}-{key}")
                    result = {"ok": bool(new_id), "monday_item_id": new_id if new_id is not True else None}
                    error = None if new_id else "monday did not accept the copy."
                elif kind == "create_column":
                    new_id = client.create_column(board, payload.get("title", ""), payload.get("column_type", "text"), payload.get("defaults") or None, f"push-{cid}-{key}")
                    result = {"ok": bool(new_id), "monday_column_id": new_id if new_id is not True else None}
                    error = None if new_id else "monday did not accept the new column."
                elif kind == "rename_column":
                    ok = client.rename_column(board, payload.get("column_id", ""), payload.get("title", ""), f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the new column name."
                elif kind == "delete_column":
                    ok = client.delete_column(board, payload.get("column_id", ""), f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the column delete."
                elif kind == "create_group":
                    new_id = client.create_group(board, payload.get("title", ""), f"push-{cid}-{key}")
                    result = {"ok": bool(new_id), "monday_group_id": new_id if new_id is not True else None}
                    error = None if new_id else "monday did not accept the new group."
                elif kind == "rename_group":
                    ok = client.rename_group(board, payload.get("group_id", ""), payload.get("title", ""), f"push-{cid}-{key}")
                    result = {"ok": bool(ok)}
                    error = None if ok else "monday did not accept the new group name."
                else:
                    new_id = client.create_item(board, payload.get("item_name", ""), _shape(board, payload.get("fields", {})), f"push-{cid}-{key}", **({"group_id": payload["group_id"]} if payload.get("group_id") else {}))
                    result = {"ok": bool(new_id), "monday_item_id": new_id, "staged_item_id": f"new:{cid}"}          # the screen swaps "new:<staged id>" for the real id
                    error = None if new_id else "monday did not accept the new row."
                    if new_id and payload.get("link_job"):                                      # a job put on this board: the new item is now its link
                        from app.services import jobs_board

                        jobs_board.store_link_after_push(str(payload["link_job"]), str(board), str(new_id), who or "monday-push")
                    for text in (payload.get("comments") or []) if new_id else []:           # comments made on the row while it was waiting go on the real row now
                        client.post_pulse_update(int(new_id), str(text), idempotency_key=f"push-{cid}-c-{abs(hash(text)) % 10**8}")
        except Exception as exc:  # noqa: BLE001 - one failing change must not stop the others
            error = f"{type(exc).__name__}: {exc}"[:300]
        if not error and kind == "update_columns":      # the app's copy of the board now shows what it just wrote, so the next edit is made against it
            try:
                from app.services.coordination import boards as _boards

                with _boards._lock:                  # the shared copy is changed under the board lock, as every other writer does
                    it = _label(board, item)["_item"]
                    for k, v in columns.items():
                        if it is not None:
                            it.setdefault("values", {})[_col_id(board, k)] = _show(v) if isinstance(v, dict) else v
            except Exception:  # noqa: BLE001 - a convenience; the push itself is done
                pass
        if not error and kind in ("create_column", "rename_column", "delete_column", "create_group", "rename_group"):      # the app's copy of the layout follows
            try:
                from app.services.coordination import boards as _boards

                with _boards._lock:
                    b = _mirror(board)
                    if kind == "rename_column":
                        for c in b.get("columns", []):
                            if c["id"] == payload.get("column_id"):
                                c["title"] = payload.get("title", c["title"])
                    elif kind == "delete_column":
                        b["columns"] = [c for c in b.get("columns", []) if c["id"] != payload.get("column_id")]
                    elif kind == "rename_group":
                        for g in b.get("groups", []):
                            if g["id"] == payload.get("group_id"):
                                g["title"] = payload.get("title", g["title"])
                    elif kind == "create_group" and result.get("monday_group_id"):
                        b.setdefault("groups", []).append({"id": result["monday_group_id"], "title": payload.get("title", ""), "color": None})
                    elif kind == "create_column" and result.get("monday_column_id"):
                        b.setdefault("columns", []).append({"id": result["monday_column_id"], "title": payload.get("title", ""), "type": payload.get("column_type", "text")})
            except Exception:  # noqa: BLE001 - a convenience; the push itself is done
                pass
        if not error and kind == "create_item" and result.get("monday_item_id"):          # the real row takes the pending one's place in the app's copy of the board
            try:
                from app.services.coordination import boards as _boards

                with _boards._lock:
                    b = _mirror(board)
                    g = next((x for x in b.get("groups", []) if x["id"] == payload.get("group_id")), None)
                    if b and not any(str(i.get("id")) == str(result["monday_item_id"]) for i in b.get("items", [])):
                        b.setdefault("items", []).append({"id": str(result["monday_item_id"]), "name": payload.get("item_name", ""), "created_at": None, "group": g["id"] if g else None,
                                                          "group_title": g["title"] if g else None, "values": {_col_id(board, k): _show(v) if isinstance(v, dict) else v for k, v in (payload.get("fields") or {}).items()}})
            except Exception:  # noqa: BLE001 - a convenience; the push itself is done
                pass
        if not error and kind in ("move_item", "archive_item"):      # the app's copy follows what was just done
            try:
                from app.services.coordination import boards as _boards

                with _boards._lock:
                    b = _mirror(board)
                    if kind == "archive_item":
                        b["items"] = [i for i in b.get("items", []) if str(i.get("id")) != str(item)]
                    else:
                        it = next((i for i in b.get("items", []) if str(i.get("id")) == str(item)), None)
                        g = next((x for x in b.get("groups", []) if x["id"] == payload.get("group_id")), None)
                        if it is not None and g is not None:
                            it["group"], it["group_title"] = g["id"], g["title"]
            except Exception:  # noqa: BLE001 - a convenience; the push itself is done
                pass
        if not error and kind == "create_item" and payload.get("rfq_id") and result.get("monday_item_id"):
            try:
                from app.services.coordination import rfqs

                rfqs.update(payload["rfq_id"], {"monday_item_id": result["monday_item_id"]}, {"email": who or "monday-push", "role": "SYSTEM"})
            except Exception as exc:  # noqa: BLE001 - the row exists; linking is a convenience
                logger.warning(f"Pushed an RFQ row ({result['monday_item_id']}) but could not link it: {exc}")
        with SessionLocal() as db:
            row = db.get(MondayChangeDB, cid)
            row.attempts += 1
            row.status, row.error, row.result = ("FAILED", error, result) if error else ("PUSHED", None, result)
            row.pushed_at = None if error else _now()
            db.commit()
        pushed, failed = pushed + (not error), failed + bool(error)
        results.append({"id": cid, "ok": not error, "error": error, **({"monday_item_id": result.get("monday_item_id"), "staged_item_id": result.get("staged_item_id")} if result.get("monday_item_id") else {})})
    audit_log("MONDAY_PUSH", "monday_changes", ",".join(str(r["id"]) for r in results)[:60], actor_email=who or "system",
              detail=f"pushed {pushed}, failed {failed}" + (" (forced past conflict checks)" if force else ""))
    return {"ok": failed == 0, "pushed": pushed, "failed": failed, "conflicts": sum(1 for r in results if r.get("conflict")), "results": results,
            "message": f"Pushed {pushed} change(s) to monday" + (f"; {failed} failed and stay listed to retry." if failed else ".")}


def skip(change_id: int, who: str = "", role: Optional[str] = None) -> Dict[str, Any]:
    """Drop a staged change without sending it (like discarding an edit). When the caller's `role` is given, only the person who staged it, or a
    manager or admin, may drop it."""
    with SessionLocal() as db:
        row = db.get(MondayChangeDB, change_id)
        if row is None:
            raise LookupError("There is no such staged change.")
        if role is not None and role not in ("MANAGER", "ADMIN") and (row.staged_by or "").strip().casefold() != (who or "").strip().casefold():
            raise PermissionError("Only the person who staged this change, or a manager, can skip it.")
        if row.status not in ("STAGED", "FAILED", "CONFLICT"):
            raise ValueError(f"That change is already {row.status.lower()}.")
        won = (db.query(MondayChangeDB).filter(MondayChangeDB.id == change_id, MondayChangeDB.status.in_(("STAGED", "FAILED", "CONFLICT")))
               .update({MondayChangeDB.status: "SKIPPED"}, synchronize_session=False))      # not if a push claimed it a moment ago
        db.commit()
        if won != 1:
            raise ValueError("That change is being sent right now, so it cannot be skipped.")
        db.refresh(row)
        out = _as_dict(row)
    audit_log("MONDAY_CHANGE_SKIPPED", "monday_changes", str(change_id), actor_email=who or "system")
    return out


def retry(change_id: int) -> Dict[str, Any]:
    """Put a failed change back in the staged list so the next push tries it again."""
    _settle_stale_pushes()
    with SessionLocal() as db:
        row = db.get(MondayChangeDB, change_id)
        if row is None:
            raise LookupError("There is no such staged change.")
        if row.status != "FAILED":
            raise ValueError("Only a failed change can be retried.")
        if row.kind in ("create_item", "post_update") and (row.result or {}).get("maybe_sent"):
            raise ValueError("This change may already be on monday (a push was interrupted while sending it). Look on the board: if it is not there, skip this one and stage it again.")
        row.status, row.error = "STAGED", None
        db.commit()
        return _as_dict(row)


def expire_old() -> int:
    """Staged changes nobody pushed for MONDAY_STAGE_DAYS days are dropped: monday will have moved on."""
    cutoff = _now() - timedelta(days=max(1, settings.MONDAY_STAGE_DAYS))
    with SessionLocal() as db:
        n = (db.query(MondayChangeDB).filter(MondayChangeDB.status.in_(("STAGED", "CONFLICT")), MondayChangeDB.created_at < cutoff)
             .update({MondayChangeDB.status: "EXPIRED"}, synchronize_session=False))
        db.commit()
    return n


# ----------------------------------------------------------------------------- editing any board, like a working copy

READ_ONLY_TYPES = {"formula", "mirror", "subtasks", "board_relation", "button", "creation_log", "last_updated", "item_id", "auto_number", "file", "name"}


def _column(board: Dict[str, Any], key: str) -> Dict[str, Any]:
    cols = board.get("columns", [])
    hit = next((c for c in cols if c["id"] == key), None)
    if hit is None:
        titled = [c for c in cols if c["title"].casefold() == str(key).casefold()]
        if len(titled) > 1:
            raise ValueError(f"More than one column on {board.get('name') or 'this board'} is called '{str(key)[:40]}'. Use the column's id.")
        hit = titled[0] if titled else None
    if hit is None:
        raise ValueError(f"There is no column '{str(key)[:40]}' on {board.get('name') or 'this board'}.")
    if hit["type"] in READ_ONLY_TYPES:
        raise ValueError(f"'{hit['title']}' is worked out by monday and cannot be changed here.")
    return hit


def _board(board_id: str) -> Dict[str, Any]:
    b = _mirror(board_id)
    if not b:
        raise LookupError(f"Board {board_id} has not been read yet. Refresh it first.")
    return b


_ODD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏ -‮⁠⁦-⁩﻿]")      # control and text-direction override characters


def _strip_odd(text: str) -> str:
    return _ODD.sub("", text).strip()


def _label_names(labels: Any) -> Dict[str, str]:
    """{lowercase name: the board's own spelling} for a column's labels. The app keeps {label: colour}; monday's own shape is {index: label}, where
    the index ("1") is NOT a label and a colour name is not one either. A list or a comma-separated string is also read; anything else has none."""
    own: Dict[str, str] = {}
    if isinstance(labels, (list, tuple, set)):
        labels = {str(x): "" for x in labels}
    elif isinstance(labels, str):
        labels = {x.strip(): "" for x in labels.split(",") if x.strip()}
    if not isinstance(labels, dict):
        return own
    pairs = [(str(k).strip(), str(v or "").strip()) for k, v in labels.items()]
    # monday's own shape is {index: label}: every key a number and every value a name. {label: colour} (even numeric labels like "1": "red") has colours.
    vals = [v for _, v in pairs if v]
    indexed = bool(pairs) and all(k.isdigit() for k, _ in pairs) and bool(vals)
    if indexed and all(re.fullmatch(r"#[0-9a-fA-F]{3,8}", v) for v in vals):
        indexed = False                                          # hex colours: label names with colours
    elif indexed and any(_is_colour(v) for v in vals) and "0" not in {k for k, _ in pairs}:
        indexed = False                                          # colour words and no slot 0 (monday's own indexes start at 0): label names with colours
    for k, v in pairs:
        name = (v if indexed else k)           # an empty slot in an {index: label} board is no label at all
        if name:
            own.setdefault(unicodedata.normalize("NFC", name).casefold(), name)
            own.setdefault(name, name)                 # the exact spelling too, so "Done" and "DONE" on one board stay two labels
    return own


_COLOUR_WORDS = {"red", "green", "orange", "yellow", "blue", "purple", "pink", "grey", "gray", "brown", "black", "white", "sky", "navy", "lime", "peach", "teal",
                 "lavender", "sunset", "chili", "aqua", "indigo", "royal", "river", "winter", "dark", "light", "bright", "explosive", "egg", "american", "working",
                 "stuck", "done", "wolf", "berry", "sail", "mud", "gold", "silver", "burgundy", "lipstick", "coral", "steel", "storm"}


def _is_colour(v: str) -> bool:
    """monday's colour of a label: a hex code, or names like "red", "done-green", "working_orange"."""
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", v.strip()):
        return True
    if v.strip() != v.strip().lower():             # monday writes colours in lower case ("grass-green"); a capitalised word is a label name
        return False
    v = v.strip().lower()
    if v in ("done", "working", "stuck"):          # a bare word of that kind is a label name, not a colour
        return False
    return bool(re.fullmatch(r"#[0-9a-f]{3,8}", v)) or (bool(re.fullmatch(r"[a-z]+(?:[-_][a-z]+)*", v)) and all(w in _COLOUR_WORDS for w in re.split(r"[-_]", v)))


def _check_label(col: Dict[str, Any], value: Any) -> Any:
    """A status or dropdown cell can only take a label the board has, and takes it spelled the way the board spells it. Returns that value."""
    labels = col.get("labels") or {}
    if col.get("type") not in ("status", "dropdown", "color") or not labels:
        return value
    own = _label_names(labels)
    if not own:
        return value
    shown = ", ".join(list(dict.fromkeys(own.values()))[:12])
    if isinstance(value, dict) and "labels" in value and "label" not in value:          # a dropdown's {"labels": [...]}: each one must be a label the board has
        picked = value["labels"] if isinstance(value["labels"], list) else None
        if not picked or len(picked) > 40 or not all(isinstance(x, str) for x in picked):
            raise ValueError(f"A value for the {col.get('title')} column must list its labels as text: " + shown + ".")
        fixed = []
        for x in picked:
            k = unicodedata.normalize("NFC", x.strip()).casefold()
            if x.strip() in own and own[x.strip()] == x.strip():
                k = x.strip()
            if k not in own:
                raise ValueError(f"'{x[:40]}' is not a label on the {col.get('title')} column. Its labels are: " + shown + ".")
            fixed.append(own[k])
        return {**value, "labels": fixed}
    text = value.get("label") if isinstance(value, dict) else value
    if isinstance(value, dict) and "label" not in value:
        raise ValueError(f"A value for the {col.get('title')} column must name one of its labels: " + shown + ".")
    if isinstance(text, bool) or text is None or (isinstance(text, str) and not text.strip()):
        return value
    text = str(text).strip()
    key = unicodedata.normalize("NFC", text).casefold()
    if own.get(text) == text:                     # a label spelled exactly as the board spells it wins over a look-alike in another case
        key = text
    if key not in own:
        raise ValueError(f"'{text[:40]}' is not a label on the {col.get('title')} column. Its labels are: " + shown + ".")
    return {**value, "label": own[key]} if isinstance(value, dict) else own[key]


def _clean_value(v: Any) -> Any:
    """A value that may be sent to monday: text up to 2,000 characters, a finite number, a yes/no, or a small monday value (a dict of at most ten
    keys and 2,000 characters). Anything else (lists, huge or odd objects) is refused."""
    import json
    import math

    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("A number must be a real number.")
        return v
    if isinstance(v, str):
        v = _strip_odd(v)
        if len(v) > 2000:
            raise ValueError("That value is longer than 2,000 characters. Shorten it; it is not cut for you.")
        return v
    if isinstance(v, dict) and len(v) <= 10:
        try:
            if len(json.dumps(v)) <= 2000:
                return v
        except (TypeError, ValueError):
            pass
    raise ValueError("A cell value must be text, a number, yes/no, or a small monday value.")


_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})", re.ASCII)
_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]{2,}", re.ASCII)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I | re.ASCII)
_PLAIN_NUMBER = re.compile(r"-?\d+(?:\.\d+)?", re.ASCII)
_YES = {"yes", "true", "checked", "1", "y", "x"}
_NO = {"no", "false", "unchecked", "0", "n"}


def _iso_ok(text: str) -> bool:
    try:
        return bool(_ISO.fullmatch(text.strip())) and bool(datetime.strptime(text.strip(), "%Y-%m-%d"))
    except ValueError:
        return False


def _timeline(text: str) -> Any:
    m = re.fullmatch(r"\s*(\S+)\s+(?:to|-)\s+(\S+)\s*", str(text), re.ASCII)
    return (m.group(1), m.group(2)) if m and _iso_ok(m.group(1)) and _iso_ok(m.group(2)) and m.group(1) <= m.group(2) else None


def _check_inside(ctype: Any, title: Any, v: Dict[str, Any]) -> None:
    """The INSIDE of a monday object value, with the same rules as the plain text forms."""
    def bad(what: str) -> ValueError:
        return ValueError(f"The {title} column was given {what}.")

    if ctype == "email" and not _EMAIL.fullmatch(str(v.get("email") or "")):
        raise bad("something that is not an email address")
    if ctype == "link" and not _URL.fullmatch(str(v.get("url") or "")):
        raise bad("a web address that does not start with http:// or https://")
    if ctype == "rating" and not re.fullmatch(r"[1-5]", str(v.get("rating")), re.ASCII):
        raise bad("a rating that is not 1 to 5")
    if ctype == "timeline" and not (_iso_ok(str(v.get("from") or "")) and _iso_ok(str(v.get("to") or "")) and str(v["from"]) <= str(v["to"])):
        raise bad("a timeline without two real dates in order")
    if ctype == "hour" and not (re.fullmatch(r"\d{1,2}", str(v.get("hour")), re.ASCII) and int(str(v["hour"])) < 24
                                and re.fullmatch(r"\d{1,2}", str(v.get("minute", 0)), re.ASCII) and int(str(v.get("minute", 0))) < 60):
        raise bad("a time that is not a real hour and minute")
    if ctype == "checkbox" and str(v.get("checked")).casefold() not in ("true", "false"):
        raise bad("a tick that is not true or false")
    if ctype == "people":
        ppl = v.get("personsAndTeams")
        if not isinstance(ppl, list) or len(ppl) > 50 or not all(isinstance(x, dict) and isinstance(x.get("id"), int) and x.get("kind") in ("person", "team") for x in ppl):
            raise bad("people that are not a list of {id, kind: person or team}")
    if ctype == "date" and v.get("time") is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", str(v["time"]), re.ASCII):
        raise bad("a time that is not HH:MM")


def _check_type(col: Dict[str, Any], value: Any) -> None:
    """A cell must hold what its column takes, written the plain way (a date as 2026-11-20, a number as 12.5, an email as an address, a link as
    http(s)://...), so nothing fails or is cut at push time. Phone and tags cells need monday's own pickers and are not set from here."""
    import math

    ctype, title = col.get("type"), col.get("title")
    if isinstance(value, dict):
        if not value:
            raise ValueError(f"An empty value can't be put in the {title} column.")
        if ctype in ("text", "long_text", "numbers", "numeric"):
            raise ValueError(f"The {title} column takes plain {'text' if 'text' in ctype else 'numbers'}, not a monday value object.")
        if ctype == "date" and (set(value) - {"date", "time"} or not _iso_ok(str(value.get("date") or ""))):
            raise ValueError(f"'{str(value.get('date'))[:30]}' is not a date for the {title} column. Write it as YYYY-MM-DD, for example 2026-11-20.")
        known = {"people": {"personsAndTeams"}, "status": {"label", "index"}, "color": {"label", "index"}, "dropdown": {"labels", "ids"}, "checkbox": {"checked"},
                 "email": {"email", "text"}, "link": {"url", "text"}, "rating": {"rating"}, "timeline": {"from", "to"}, "hour": {"hour", "minute"}}.get(ctype)
        if known is not None and set(value) - known:                  # only the shapes monday knows for this kind of column
            raise ValueError(f"That value is not a shape the {title} column takes (it takes: {', '.join(sorted(known))}).")
        _check_inside(ctype, title, value)
        return
    if isinstance(value, bool) or value is None or (isinstance(value, str) and not value.strip()):
        if isinstance(value, bool) and ctype not in ("checkbox", "text", "long_text", "status", "color", "dropdown", "people"):
            raise ValueError(f"Yes/no can't be put in the {title} column.")
        return
    text = str(value).strip()
    bad = lambda what: ValueError(f"'{text[:30]}' is not {what} for the {title} column.")  # noqa: E731
    if ctype == "date":
        if not _iso_ok(text):
            raise bad("a date") from None
    elif ctype in ("numbers", "numeric"):
        if not _PLAIN_NUMBER.fullmatch(text) or not math.isfinite(float(text)):
            raise bad("a number (write a plain number like 12.5)")
    elif ctype == "checkbox":
        if text.casefold() not in _YES | _NO:
            raise bad("yes or no")
    elif ctype == "rating":
        if not re.fullmatch(r"[1-5]", text, re.ASCII):
            raise bad("a rating from 1 to 5")
    elif ctype == "email":
        if not _EMAIL.fullmatch(text):
            raise bad("an email address")
    elif ctype == "link":
        if not _URL.fullmatch(text):
            raise bad("a web address (it must start with http:// or https://)")
    elif ctype == "timeline":
        if not _timeline(text):
            raise bad("a timeline (write it like 2026-11-01 to 2026-11-10)")
    elif ctype == "hour":
        if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", text, re.ASCII):
            raise bad("a time (write it like 13:30)")
    elif ctype == "week":
        m = re.fullmatch(r"(\d{4})-W(\d{2})", text, re.ASCII)
        try:
            if not m:
                raise ValueError
            datetime.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            raise bad("a week (write it like 2026-W46)") from None
    elif ctype in ("phone", "tags"):
        raise ValueError(f"The {title} column can't be set from here (monday needs its own picker for it). Set it in monday.")


def _stale_note(b: Dict[str, Any]) -> str:
    """When the app last read the board long ago, a label it does not know may simply be new on monday: say so."""
    try:
        read = datetime.fromisoformat(str(b.get("read_at")))
        read = read if read.tzinfo else read.replace(tzinfo=timezone.utc)
        if _now() - read > timedelta(days=1):
            return f" (The app's copy of this board was last read {read:%Y-%m-%d}; if the label is new on monday, refresh the board first.)"
    except (TypeError, ValueError):
        pass
    return ""


def check_cell(board_id: str, key: str, value: Any, new_row: bool = False) -> Any:
    """The one check for a cell value, used when staging and when the copilot proposes: the column exists and can be changed, the value is clean,
    a label is one the board has, a date or number is real, and monday can really be sent it. Returns (column, value). A blank in a NEW row is
    simply left out; a blank anywhere else would not clear the cell, so it is refused."""
    b = _board(board_id)
    col = _column(b, key)
    try:
        value = _check_label(col, _clean_value(value))
    except ValueError as exc:
        raise ValueError(str(exc) + _stale_note(b)) from None
    _check_type(col, value)
    if not (new_row and value in (None, "")) and not _shape(board_id, {col["id"]: value}):
        raise ValueError(f"None of those values can be sent: '{col['title']}' can't be set to that from here (a blank, or a kind of column that is changed another way, such as people). Nothing was staged.")
    return col, value


def stage_edit(board_id: str, item_id: str, edits: Dict[str, Any], user: Dict[str, Any]) -> int:
    """Stage changes to cells of one item on ANY board the app has read. Each is checked against that board's own columns, and the value monday
    showed is remembered, so a later push can tell if someone else changed it meanwhile. Nothing is sent."""
    b = _board(board_id)
    item = next((i for i in b.get("items", []) if str(i["id"]) == str(item_id)), None)
    if item is None:
        raise LookupError("That item is not on the board as the app last read it. Refresh the board first.")
    if not isinstance(edits, dict) or not edits or len(edits) > 40:
        raise ValueError("Give the cells to change as {column: value} (at most 40).")
    columns: Dict[str, Any] = {}
    base: Dict[str, Any] = {}
    for key, value in edits.items():
        col, value = check_cell(board_id, key, value)
        columns[col["id"]] = value
        base[col["id"]] = item["values"].get(col["id"])
    return stage("update_columns", board_id, item_id, {"columns": columns, "base": base},
                 f"Edit {item['name']}: " + ", ".join(f"{k} = {_show(v)}" for k, v in columns.items())[:300], source="edit", by=user.get("email", "app"))


def stage_new_item(board_id: str, name: str, fields: Dict[str, Any], user: Dict[str, Any], group_id: Optional[str] = None, key: Optional[str] = None) -> int:
    b = _board(board_id)
    if group_id and not any(g["id"] == group_id for g in b.get("groups", [])):
        raise ValueError(f"There is no group '{str(group_id)[:40]}' on {b.get('name') or board_id}.")
    name = _strip_odd(str(name or ""))[:255]
    if not name:
        raise ValueError("Give the new row a name.")
    clean = {}
    for field_key, value in (fields or {}).items():
        col, val = check_cell(board_id, field_key, value, new_row=True)
        clean[col["id"]] = val
    from app.services.coordination import jobs

    # one staged row per board and name: a double click is one row, not two
    return stage("create_item", board_id, "", {"item_name": name, "fields": clean, **({"group_id": group_id} if group_id else {})}, f"New row on {b.get('name') or board_id}: {name}",
                 key=key or f"new-{board_id}-{' '.join(name.split()).casefold()}", source="edit", by=user.get("email", "app"))


def stage_comment(board_id: str, item_id: str, text: str, user: Dict[str, Any]) -> int:
    b = _board(board_id)
    if not any(str(i["id"]) == str(item_id) for i in b.get("items", [])):
        raise LookupError("That item is not on the board as the app last read it. Refresh the board first.")
    text = _strip_odd(str(text or ""))
    if len(text) > 4000:
        raise ValueError("That comment is longer than 4,000 characters. Shorten it; it is not cut for you.")
    if not text:
        raise ValueError("Write the comment first.")
    return stage("post_update", board_id, item_id, {"text": text}, f"Comment on {item_id}", source="edit", by=user.get("email", "app"))


def rebase(change_id: int) -> Dict[str, Any]:
    """After a CONFLICT: keep this edit, but make it against what monday shows now. It goes back to STAGED."""
    from app.core.container import get_monday_client

    with SessionLocal() as db:
        row = db.get(MondayChangeDB, change_id)
        if row is None:
            raise LookupError("No such change.")
        if row.status != "CONFLICT":
            raise ValueError("Only a change held for a conflict can be re-based.")
        board, item, payload, kind = row.board_id, row.item_id, dict(row.payload or {}), row.kind
    if kind == "update_columns" and _mirror(board):          # an edit that is no longer valid is said so now, not at the next push
        by_col = {c["id"]: c for c in _mirror(board).get("columns", [])}
        for k, v in (payload.get("columns") or {}).items():
            if _col_id(board, k) in by_col:
                check_cell(board, _col_id(board, k), v)
    live = _live_values(get_monday_client(), item, list((payload.get("base") or {}).keys()))
    payload["base"] = {k: live.get(k, "") for k in payload.get("base", {})}
    with SessionLocal() as db:
        won = (db.query(MondayChangeDB).filter(MondayChangeDB.id == change_id, MondayChangeDB.status == "CONFLICT")
               .update({MondayChangeDB.payload: payload, MondayChangeDB.status: "STAGED", MondayChangeDB.error: None, MondayChangeDB.result: None,
                        MondayChangeDB.updated_at: _now()}, synchronize_session=False))      # not if someone skipped it while monday was being read
        db.commit()
        if won != 1:
            raise ValueError("That change is no longer waiting on a conflict (it may have been skipped).")
        return _as_dict(db.get(MondayChangeDB, change_id))

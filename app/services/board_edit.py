"""Editing a board the way monday does it: type what you mean in a cell, and the app works out the value.

Everything here STAGES (see monday_push): nothing reaches monday until a manager pushes AND the owner has switched writing on. A staged edit shows as
pending straight away and can be undone until it is pushed. This module never calls monday and never touches the read-only guard.

`understand(board, column, text)` is the reader of plain text: "fri", "+3d", "nov 20" for a date; "don" for the status label "Done"; "$1,200.50" for a
number. It returns the value to stage and, in plain words, what it understood, so the person can see "Due date: Friday 20 Nov 2026" before they push.
"""

import difflib
import re
import threading
import uuid
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import MondayChangeDB
from app.services import monday_push

YEAR_FROM, YEAR_TO = 1990, 2100
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MONTH_FULL = {"january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9, "sept": 9, "october": 10, "november": 11, "december": 12}
_DAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
         "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
NOT_EDITABLE = monday_push.READ_ONLY_TYPES | {"phone", "tags"}


def _last_read(read_at):
    from app.services.coordination import boards

    return boards.last_read(read_at)


def _today() -> date:
    from app.services.coordination import notify as alerts

    return alerts.now_local().date()


def _slash_note(raw: str) -> str:
    """"03/04" is 3 April here (month first), but forwarders and many vendors write 4 March: said, so the person sees the other reading and can type the month name."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\s*", raw or "", re.ASCII)
    if m and int(m.group(1)) <= 12 and int(m.group(2)) <= 12 and m.group(1) != m.group(2):
        return " (read month first; it could be day first: type the month name, like 4 mar, if you meant that)"
    return ""


def _say(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b %Y')}"


def _real(y: int, m: int, d: int, said: str) -> date:
    if not YEAR_FROM <= y <= YEAR_TO:
        raise ValueError(f"'{said[:30]}': the year {y} is outside {YEAR_FROM}-{YEAR_TO}.")
    try:
        return date(y, m, d)
    except ValueError:
        raise ValueError(f"'{said[:30]}' is not a real date.") from None


def parse_date(text: str, today: Optional[date] = None) -> date:
    """A date from what a person types. Today, tomorrow, a weekday, +3d, nov 20, 20 nov, 11/20, 11/20/2026 or 2026-11-20. A weekday means the next one
    after today; a month and day without a year means this year, or next year if that day passed more than two months ago."""
    today = today or _today()
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or "")).strip().casefold().rstrip("."))
    if not t:
        raise ValueError("No date was given.")
    if t in ("today", "now"):
        return today
    if t in ("tomorrow", "tmrw", "tom"):
        return today + timedelta(days=1)
    if t == "yesterday":
        return today - timedelta(days=1)
    m = re.fullmatch(r"([+-])\s*(\d{1,4})\s*([dwm]?)", t, re.ASCII)
    if m:
        n = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        if m.group(3) == "w":
            return today + timedelta(weeks=n)
        if m.group(3) == "m":
            months = today.month - 1 + n
            y, mo = today.year + months // 12, months % 12 + 1
            day = min(today.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
            return _real(y, mo, day, t)
        return today + timedelta(days=n)
    m = re.fullmatch(r"(next )?([a-z]+)", t, re.ASCII)
    if m and m.group(2) in _DAYS:
        ahead = (_DAYS[m.group(2)] - today.weekday()) % 7 or 7
        if m.group(1):                                              # "next fri": the one in the following week, not this week's
            ahead = (_DAYS[m.group(2)] - today.weekday()) % 7 + 7 if (_DAYS[m.group(2)] - today.weekday()) % 7 else 7
        return today + timedelta(days=ahead)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t, re.ASCII)
    if m:
        return _real(int(m.group(1)), int(m.group(2)), int(m.group(3)), t)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?", t, re.ASCII)
    if m:
        y = int(m.group(3)) if m.group(3) else None
        if y is not None and y < 100:
            y += 2000
        return _guess_year(int(m.group(1)), int(m.group(2)), y, today, t)
    m = re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", t, re.ASCII)
    if m and _month_name(m.group(1)):
        return _guess_year(_month_name(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None, today, t)
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+)(?:,? (\d{4}))?", t, re.ASCII)
    if m and _month_name(m.group(2)):
        return _guess_year(_month_name(m.group(2)), int(m.group(1)), int(m.group(3)) if m.group(3) else None, today, t)
    raise ValueError(f"'{str(text)[:30]}' is not a date. Try 'tomorrow', 'fri', '+3d', 'nov 20' or 2026-11-20.")


def _month_name(word: str) -> Optional[int]:
    w = word.casefold()
    if w in _MONTH_FULL:
        return _MONTH_FULL[w]
    if w in _MONTHS or w == "sept":
        return _MONTHS.get(w[:3])
    return None


def _guess_year(month: int, day: int, year: Optional[int], today: date, said: str) -> date:
    if year is not None:
        return _real(year, month, day, said)
    d = _real(today.year, month, day, said)
    return _real(today.year + 1, month, day, said) if (today - d).days > 60 else d


# ----------------------------------------------------------------------------- the readers, one per kind of column

def _labels_of(col: Dict[str, Any]) -> List[str]:
    own = monday_push._label_names(col.get("labels") or {})
    return list(dict.fromkeys(own.values()))


_NEGATED = re.compile(r"^(?:un|not|non|in|dis|no)[\s_-]?\w", re.I)


def match_label(text: str, labels: List[str], title: str) -> str:
    """A status label from what was typed: exact (any case), then the one label it starts, then the one that contains it, then a close spelling. Never a guess
    between two."""
    said = unicodedata.normalize("NFC", str(text or "")).strip()
    if not said:
        raise ValueError(f"No label was given for {title}.")
    low = said.casefold()
    exact = [x for x in labels if x.casefold() == low]
    if len(exact) == 1:
        return exact[0]
    for n, test in enumerate((lambda x: x.casefold().startswith(low), lambda x: low in x.casefold())):
        if n == 1 and _NEGATED.match(low):                               # "undone", "not approved": the opposite of a label is never matched to it by looseness
            raise ValueError(f"'{said[:30]}' sounds like the opposite of a label, so it was not matched. Its labels are: {', '.join(labels[:12])}.")
        hits = [x for x in labels if test(x)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"'{said[:30]}' could be any of: {', '.join(hits[:8])}. Type a little more of the one you mean.")
    if _NEGATED.match(low):
        raise ValueError(f"'{said[:30]}' sounds like the opposite of a label, so it was not matched. Its labels are: {', '.join(labels[:12])}.")
    close = difflib.get_close_matches(low, [x.casefold() for x in labels], n=2, cutoff=0.8)
    if close and (len(close) == 1 or difflib.SequenceMatcher(None, low, close[0]).ratio() - difflib.SequenceMatcher(None, low, close[1]).ratio() > 0.1):
        return next(x for x in labels if x.casefold() == close[0])
    raise ValueError(f"'{said[:30]}' is not a label on {title}. Its labels are: {', '.join(labels[:12]) or '(none read yet)'}.")


def _number(text: str, title: str) -> str:
    t = re.sub(r"\s+", "", str(text))
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace("$", "")
    if "," in t:                                                         # thousands only: 1,234 or 1,234.50. "12,50" and "1,2" are decimal commas, which would be read as a different number
        if not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", t, re.ASCII):
            raise ValueError(f"'{str(text)[:30]}' has a comma that is not a thousands separator, so it was not read. Write the number with a point, like 12.50 or 1,234.50.")
        t = t.replace(",", "")
    if t.startswith("+"):
        t = t[1:]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", t, re.ASCII):
        raise ValueError(f"'{str(text)[:30]}' is not a plain number for {title} (write it like 1200.50).")
    return ("-" + t.lstrip("-")) if neg else t


def _hour(text: str, title: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?\s*", str(text).casefold(), re.ASCII)
    if not m:
        raise ValueError(f"'{str(text)[:30]}' is not a time for {title} (write it like 13:30 or 1:30pm).")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            raise ValueError(f"'{str(text)[:30]}' is not a time for {title}.")
        h = h % 12 + (12 if ap.startswith("p") else 0)
    if h > 23 or mi > 59:
        raise ValueError(f"'{str(text)[:30]}' is not a time for {title}.")
    return f"{h:02d}:{mi:02d}"


def _people(text: str, title: str, client: Any = None) -> Dict[str, Any]:
    """People from names or emails, matched against the monday accounts (which is where the ids live). A name that fits two people is not guessed."""
    from app.core.container import get_monday_client
    from app.services.monday_client import fetch_user_emails

    client = client or get_monday_client()
    if not client.live:
        raise ValueError(f"monday isn't connected right now, so people can't be matched to their accounts for {title}.")
    users = {uid: e.casefold() for uid, e in fetch_user_emails(client).items() if e}
    found: List[Dict[str, Any]] = []
    for part in [p.strip() for p in re.split(r"[,;]| and ", str(text)) if p.strip()][:20]:
        low = part.casefold().lstrip("@")
        hit = [u for u, e in users.items() if e == low]
        if not hit:
            words = [w for w in re.split(r"[\s._-]+", low) if w]
            hit = [u for u, e in users.items() if words and all(w in re.split(r"[._@+-]+", e.split("@")[0]) or (len(w) >= 3 and e.split("@")[0].startswith(w)) for w in words)]
        if len(hit) != 1:
            raise ValueError(f"'{part[:40]}' " + ("matches no one on monday." if not hit else "matches more than one person: " + ", ".join(users[u] for u in hit[:5]) + ". Use the email."))
        found.append({"id": int(hit[0]), "kind": "person", "_email": users[hit[0]]})
    if not found:
        raise ValueError(f"No one was given for {title}.")
    return {"personsAndTeams": [{"id": f["id"], "kind": "person"} for f in found], "_words": ", ".join(f["_email"] for f in found)}


def understand(board_id: str, column: str, text: str, today: Optional[date] = None, client: Any = None) -> Dict[str, Any]:
    """What a typed text means for one column: {column, column_id, type, value, understood}. Raises ValueError (in plain words) when it cannot be read."""
    b = monday_push._board(board_id)
    col = monday_push._column(b, column)
    if col["type"] in NOT_EDITABLE:
        raise ValueError(f"The {col['title']} column can't be set from here.")
    title, ctype = col["title"], col["type"]
    raw = monday_push._strip_odd(str(text if text is not None else ""))
    if not raw:
        raise ValueError(f"Type something for {title}. Clearing a cell is not done from here yet.")
    if len(raw) > 2000:
        raise ValueError("That is longer than 2,000 characters. Shorten it; it is not cut for you.")
    value: Any = raw
    said = raw
    if ctype in ("status", "color"):
        labels = _labels_of(col)
        value = match_label(raw, labels, title) if labels else raw
        said = f"{value}" + (f" (you typed '{raw}')" if value.casefold() != raw.casefold() else "")
    elif ctype == "dropdown":
        parts = [p.strip() for p in raw.split(",") if p.strip()][:20]
        value = {"labels": parts}
        said = ", ".join(parts)
    elif ctype == "date":
        d = parse_date(raw, today)
        value, said = d.isoformat(), _say(d) + _slash_note(raw)
    elif ctype == "timeline":
        m = re.split(r"\s+(?:to|until|-|–|—)\s+", raw, maxsplit=1)
        if len(m) != 2:
            raise ValueError(f"A timeline for {title} needs two dates, like 'nov 1 to nov 10'.")
        a, z = parse_date(m[0], today), parse_date(m[1], today)
        if a > z:
            raise ValueError(f"The timeline for {title} ends before it starts.")
        value, said = f"{a.isoformat()} to {z.isoformat()}", f"{_say(a)} to {_say(z)}"
    elif ctype in ("numbers", "numeric"):
        value = said = _number(raw, title)
    elif ctype == "checkbox":
        low = raw.casefold()
        if low in monday_push._YES or low in ("tick", "ticked", "✓", "✔"):
            value, said = "yes", "ticked"
        elif low in monday_push._NO or low in ("untick", "unticked"):
            value, said = "no", "not ticked"
        else:
            raise ValueError(f"'{raw[:30]}' is not yes or no for {title}.")
    elif ctype == "rating":
        stars = raw.count("★") if set(raw) <= {"★", "☆", " "} else None
        value = str(stars) if stars else raw
        said = f"{value} of 5"
    elif ctype == "email":
        value = said = raw.strip("<>")
    elif ctype == "link":
        value = raw if re.match(r"https?://", raw, re.I) else ("https://" + raw if re.fullmatch(r"(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?", raw, re.ASCII) else raw)
        said = value
    elif ctype == "hour":
        value = said = _hour(raw, title)
    elif ctype == "people":
        ppl = _people(raw, title, client)
        said = ppl.pop("_words")
        value = ppl
    # text, long_text and anything else plain: as typed
    col_done, value = monday_push.check_cell(board_id, col["id"], value)           # the one check every staged cell goes through
    return {"column": col["title"], "column_id": col["id"], "type": ctype, "value": value, "understood": f"{title}: {said}"}


# ----------------------------------------------------------------------------- what the screen needs to draw pickers

def boards_list() -> List[Dict[str, Any]]:
    from app.services import jobs_board
    from app.services.coordination import boards

    return [jobs_board.listing_entry()] + [{**b, "editable": b["role"] != "doc" and bool(boards._mirror.get(b["board_id"], {}).get("columns"))} for b in boards.boards()]


def columns_info(board_id: str) -> Dict[str, Any]:
    from app.services import jobs_board

    if jobs_board.is_jobs(board_id):
        return jobs_board.info()
    b = monday_push._board(board_id)
    cols = []
    for c in b.get("columns", []):
        item = {"id": c["id"], "title": c["title"], "type": c["type"], "editable": c["type"] not in NOT_EDITABLE}
        if c["type"] in ("status", "color"):
            item["labels"] = [{"label": k, "color": v} for k, v in (c.get("labels") or {}).items()] if isinstance(c.get("labels"), dict) else [{"label": x, "color": ""} for x in _labels_of(c)]
        item["hint"] = {"date": "Type: fri, tomorrow, +3d, nov 20, 11/20", "status": "Type the start of a label", "numbers": "Type a number, $ and commas are fine",
                        "people": "Type a name or email", "checkbox": "yes / no", "timeline": "nov 1 to nov 10", "hour": "1:30pm or 13:30"}.get(c["type"], "")
        cols.append(item)
    return {"board_id": str(board_id), "name": b.get("name"), "role": b.get("role"), "editable": b.get("role") != "doc" and bool(cols), "read_at": b.get("read_at"), "last_read": _last_read(b.get("read_at")),
            "columns": cols, "groups": b.get("groups", []), "items": len(b.get("items", []))}


NEW = "new:"


def _staged_id(item_id: Any) -> Optional[int]:
    t = str(item_id or "")
    return int(t[len(NEW):]) if t.startswith(NEW) and t[len(NEW):].isdigit() and len(t) < 20 else None


def _pending_view(row: MondayChangeDB, b: Dict[str, Any]) -> Dict[str, Any]:
    p = row.payload or {}
    g = next((x for x in b.get("groups", []) if x["id"] == p.get("group_id")), None)
    return {"id": f"{NEW}{row.id}", "staged_id": row.id, "pending": True, "name": p.get("item_name", ""), "group": g["id"] if g else None, "group_title": g["title"] if g else None,
            "created_at": row.created_at.isoformat() if row.created_at else None, "created_by": row.staged_by,
            "values": {k: monday_push._show(v) for k, v in (p.get("fields") or {}).items()}, "comments": list(p.get("comments") or [])}


def _item(board_id: str, item_id: str) -> Dict[str, Any]:
    """A row of the board by its id: a real one, or a pending new row ("new:<staged id>"), which is a row like any other. Once a pending row has been pushed its old id still
    finds the real row."""
    b = monday_push._board(board_id)
    sid = _staged_id(item_id)
    if sid is not None:
        with SessionLocal() as db:
            row = db.get(MondayChangeDB, sid)
            if row is None or row.kind != "create_item" or str(row.board_id) != str(board_id):
                raise LookupError("That new row is not on this board.")
            if row.status == "STAGED":
                return _pending_view(row, b)
            if row.status == "PUSHED" and (row.result or {}).get("monday_item_id"):
                item_id = str((row.result or {})["monday_item_id"])
            else:
                raise LookupError("That new row was dropped, so it can't be changed. Undo brings it back while you are still on this board.")
    it = next((i for i in b.get("items", []) if str(i["id"]) == str(item_id)), None)
    if it is None:
        raise LookupError("That item is not on the board as the app last read it. Refresh the board first.")
    return it


def items_view(board_id: str) -> List[Dict[str, Any]]:
    """The board's rows as one working list: the real rows, with each pending new row inside its group (after the group's last row), in the order they were made. Pending rows carry
    pending=true and an id the edit routes accept."""
    b = monday_push._board(board_id)
    with SessionLocal() as db:
        pend = [_pending_view(r, b) for r in db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.kind == "create_item", MondayChangeDB.board_id == str(board_id)).order_by(MondayChangeDB.id)]
    real = [{**i, "pending": False, "staged_id": None} for i in b.get("items", [])]
    out = list(real)
    for p in pend:
        at = max((n for n, i in enumerate(out) if i.get("group") == p["group"] and p["group"] is not None), default=None)
        if at is None and p["group"] is None:
            out.append(p)
        elif at is None:                                                           # a group with no rows yet: after the last row of the group before it, else at the end
            order = [g["id"] for g in b.get("groups", [])]
            before = [g for g in order[:order.index(p["group"])]] if p["group"] in order else []
            idx = max((n for n, i in enumerate(out) if i.get("group") in before), default=len(out) - 1)
            out.insert(idx + 1, p)
        else:
            out.insert(at + 1, p)
    return out


def _staged_overlay(board_id: str):
    """What is waiting to be pushed on this board's real rows: {item id: {column id: {value, by, was}}} for cell edits, and {item id: {archive, move_to, copy}} for row actions. Read-only, so
    the screen can show an unsaved mark on each cell without guessing."""
    cells: Dict[str, Dict[str, Any]] = {}
    rows: Dict[str, Dict[str, Any]] = {}
    mirror = {str(i["id"]): i for i in monday_push._mirror(board_id).get("items", [])}
    with SessionLocal() as db:
        for r in db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.board_id == str(board_id), MondayChangeDB.kind.in_(("update_columns", "archive_item", "move_item", "duplicate_item"))).order_by(MondayChangeDB.updated_at):
            iid = str(r.item_id)
            if r.kind == "update_columns":
                for col, val in ((r.payload or {}).get("columns") or {}).items():
                    cells.setdefault(iid, {})[col] = {"value": monday_push._show(val), "by": r.staged_by, "was": (mirror.get(iid, {}).get("values") or {}).get(col, "")}
            else:
                rows.setdefault(iid, {})[{"archive_item": "archive", "move_item": "move_to", "duplicate_item": "copy"}[r.kind]] = (r.payload or {}).get("group_id") if r.kind == "move_item" else True
    return cells, rows


def board_items(board_id: str, limit: int = 60, offset: int = 0) -> Dict[str, Any]:
    """The working list of rows, each with ALL the board's columns as `cells` ({column id: {title, type, editable, labels?, value}}), so the screen can show and fill every cell of a real
    row or a pending new row alike. A column added to the board later appears on pending rows too, because the cells come from the board's columns as they are now."""
    from app.services import jobs_board

    if jobs_board.is_jobs(board_id):
        return jobs_board.board_items(limit, offset)
    info = columns_info(board_id)
    all_rows = items_view(board_id)
    limit, offset = max(1, min(int(limit), 200)), max(0, min(int(offset), 10**9))
    rows = all_rows[offset:offset + limit]                                          # every board is paged, pending rows included in the order they show
    staged_cells, staged_rows = _staged_overlay(board_id)
    for r in rows:
        mine = staged_cells.get(str(r["id"]), {})
        r["cells"] = {c["id"]: {"title": c["title"], "type": c["type"], "editable": bool(c["editable"]), "value": r["values"].get(c["id"], ""), **({"labels": c["labels"]} if c.get("labels") else {}),
                                "staged": c["id"] in mine, **({"staged_value": mine[c["id"]]["value"], "staged_by": mine[c["id"]]["by"], "was": mine[c["id"]]["was"]} if c["id"] in mine else {})}
                      for c in info["columns"]}
        for c in info["columns"]:                                                           # a timeline cell also says its start and end (ISO dates, null when not set) for a Gantt view
            if c["type"] == "timeline":
                found = re.findall(r"\d{4}-\d{2}-\d{2}", str(r["cells"][c["id"]]["value"] or ""))
                r["cells"][c["id"]].update({"start": found[0] if found else None, "end": found[-1] if found else None})
        r["staged"] = staged_rows.get(str(r["id"]), {}) if not r.get("pending") else {"new_row": True}
        r["has_staged_changes"] = bool(mine) or bool(r["staged"])
    empty = [c["id"] for c in info["columns"] if all_rows and all(not str(r["values"].get(c["id"]) or "").strip() for r in all_rows)]       # blank in the whole board, not just this page
    return {**{k: info[k] for k in ("board_id", "name", "role", "editable", "read_at", "last_read", "columns", "groups")}, "items": rows, "pending_rows": sum(1 for r in all_rows if r["pending"]),
            "empty_columns": empty, "total_rows": len(all_rows), "offset": offset, "more": offset + limit < len(all_rows)}


_pending_lock = threading.RLock()          # one person's edit to a pending row is read, changed and saved before the next one starts: six people at once lose nothing


def _merge_pending(board_id: str, staged_id: int, edits: Dict[str, Any], user: Dict[str, Any], extra: Optional[Dict[str, Any]] = None, log: bool = True) -> Dict[str, Any]:
    with _pending_lock:
        return _merge_pending_locked(board_id, staged_id, edits, user, extra, log)


def _merge_pending_locked(board_id: str, staged_id: int, edits: Dict[str, Any], user: Dict[str, Any], extra: Optional[Dict[str, Any]] = None, log: bool = True) -> Dict[str, Any]:
    """Edits to a pending new row go into that row's single create payload (never a second change). Each cell's old value is kept so undo can step back one edit at a time."""
    with SessionLocal() as db:
        row = db.get(MondayChangeDB, staged_id)
        if row is None or row.kind != "create_item" or row.status != "STAGED" or str(row.board_id) != str(board_id):
            raise LookupError("That new row is no longer waiting.")
        payload = dict(row.payload or {})
        fields = dict(payload.get("fields") or {})
        hist = list(payload.get("history") or [])
        lastw = dict(payload.get("last_writer") or {})
        seq = int(payload.get("seq") or 0)
        for k, v in edits.items():
            if log:
                seq += 1
                hist.append({"by": (user.get("email") or "app").casefold(), "column": k, "had": k in fields, "previous": fields.get(k), "wrote": v, "seq": seq})
                lastw[k] = seq                                                          # who wrote the cell last, by order: the same text typed by two people is still two edits
            fields[k] = v
        payload["fields"], payload["history"], payload["last_writer"], payload["seq"] = fields, hist[-200:], lastw, seq
        payload.update(extra or {})
        row.payload, row.updated_at = payload, datetime.now(timezone.utc)
        db.commit()
        return payload


def find_group(board_id: str, group: str) -> Dict[str, Any]:
    """A group by id or by what was typed of its title (never a guess between two)."""
    b = monday_push._board(board_id)
    groups = b.get("groups", [])
    low = str(group or "").strip().casefold()
    if not low:
        raise ValueError("Say which group.")
    for test in (lambda g: g["id"] == group, lambda g: g["title"].casefold() == low, lambda g: g["title"].casefold().startswith(low), lambda g: low in g["title"].casefold()):
        hits = [g for g in groups if test(g)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError(f"'{str(group)[:30]}' could be any of: {', '.join(g['title'] for g in hits[:8])}.")
    raise ValueError(f"There is no group '{str(group)[:30]}' on {b.get('name') or board_id}. Groups: {', '.join(g['title'] for g in groups[:12])}.")


# ----------------------------------------------------------------------------- the staged edits

def _waiting_cell(board_id: str, item_id: str, col_id: str, who: str = "") -> Tuple[bool, Any]:
    with SessionLocal() as db:
        q = (db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.kind == "update_columns", MondayChangeDB.board_id == str(board_id),
                                             MondayChangeDB.item_id == str(item_id)))
        if who:
            q = q.filter(MondayChangeDB.staged_by == who[:256])
        row = q.first()
        cols = (row.payload or {}).get("columns", {}) if row else {}
        return col_id in cols, cols.get(col_id)


def _waiting_payload(board_id: str, item_id: str, who: str) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = (db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.kind == "update_columns", MondayChangeDB.board_id == str(board_id),
                                               MondayChangeDB.item_id == str(item_id), MondayChangeDB.staged_by == who[:256]).first())
        return dict(row.payload or {}) if row else {}


def edit_cell(board_id: str, item_id: str, column: str, text: str, user: Dict[str, Any], today: Optional[date] = None, client: Any = None) -> Dict[str, Any]:
    """Change one cell from typed text, on a real row or a pending new row alike. Staged (nothing sent), shown as pending, undoable one edit at a time. A pending row's edit goes into its
    single create; a real row's goes into the person's one staged change for it. The same cell twice keeps the latest value in one change."""
    from app.services import jobs_board

    if jobs_board.is_jobs(board_id):                                           # a cell of the Jobs board is a field of the job
        return jobs_board.edit_cell(item_id, column, text, user, today)
    item = _item(board_id, item_id)
    item_id = str(item["id"])
    got = understand(board_id, column, text, today, client)
    was = item["values"].get(got["column_id"]) or ""
    who = user.get("email", "app")
    if item.get("pending"):
        _merge_pending(board_id, item["staged_id"], {got["column_id"]: got["value"]}, user)
        change_id = item["staged_id"]
    else:
        prior = _waiting_payload(board_id, item_id, who)
        had, previous = got["column_id"] in (prior.get("columns") or {}), (prior.get("columns") or {}).get(got["column_id"])
        change_id = monday_push.stage_edit(board_id, item_id, {got["column_id"]: got["value"]}, user)
        monday_push.attach(change_id, history=(list(prior.get("history") or [])[-199:] + [{"column": got["column_id"], "had": had, "previous": previous}]),
                           last={"column": got["column_id"], "had": had, "previous": previous})
    audit_log("BOARD_CELL_STAGED", "monday_changes", str(change_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"{item['name'][:60]} / {got['column']}: {got['understood'][:80]}")
    return {"staged": True, "pending": True, "pending_row": bool(item.get("pending")), "change_id": change_id, "staged_id": item.get("staged_id") or change_id if item.get("pending") else None,
            "board_id": str(board_id), "item_id": item_id, "item": item["name"], "column": got["column"], "column_id": got["column_id"], "type": got["type"], "understood": got["understood"],
            "was": was, "becomes": monday_push._show(got["value"]), "message": f"{item['name']} · {got['understood']} (staged, not sent to monday yet)"}


def add_row(board_id: str, name: str, group: Optional[str], cells: Dict[str, str], user: Dict[str, Any], today: Optional[date] = None, client: Any = None,
            client_key: Optional[str] = None) -> Dict[str, Any]:
    """A new row from a name, a group and cells typed as text. Every problem is listed together and nothing is staged if there is one. The row is a pending row from this moment:
    it comes back with an id ("new:<staged id>") that every edit route accepts. Adding the same name again makes another row, except a repeat within three seconds (a double click) or with
    the same client_key, which is the same row."""
    from app.services import jobs_board

    if jobs_board.is_jobs(board_id):                                           # a new row on the Jobs board is a new job
        from app.services import create_anything

        c = {str(k).casefold(): v for k, v in (cells or {}).items()}
        made = create_anything.create("job", {"customer": str(c.get("customer") or c.get("customer name") or "(not set)"), "title": str(name or ""), "subject": str(name or ""),
                                              **({"client_key": str(client_key)} if client_key else {})}, user)
        return {"staged": False, "saved": True, "pending": False, "item_id": made["ref"], "name": str(name or ""), "group": None, "message": made["message"], "ref": made["ref"], "link": made["link"]}
    b = monday_push._board(board_id)
    g = find_group(board_id, group) if group else None
    fields, understood, problems = {}, [], []
    if not isinstance(cells, dict) or len(cells) > 40:
        raise ValueError("Give the cells as {column: text} (at most 40).")
    for col, text in (cells or {}).items():
        try:
            got = understand(board_id, str(col), str(text), today, client)
            fields[got["column_id"]] = got["value"]
            understood.append(got["understood"])
        except ValueError as exc:
            problems.append(f"{str(col)[:40]}: {exc}")
    if problems:
        raise ValueError("The row was not staged. " + " ".join(problems))
    clean = " ".join(str(name or "").split())[:255]
    me = (user.get("email") or "app")
    key = None
    with SessionLocal() as db:
        twins = [r for r in db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.kind == "create_item", MondayChangeDB.board_id == str(board_id))
                 if str((r.payload or {}).get("item_name", "")).casefold() == clean.casefold()]
        now = datetime.now(timezone.utc)
        for r in twins:
            at = r.created_at if (r.created_at and r.created_at.tzinfo) else (r.created_at.replace(tzinfo=timezone.utc) if r.created_at else now)
            if r.staged_by == me and (now - at).total_seconds() < 3 and (r.payload or {}).get("group_id") == (g["id"] if g else None):
                key = r.dedupe_key                                                       # a double click: the same row
        n_same = len(twins)
    if client_key:
        key = f"new-{board_id}-k-{str(client_key)[:60]}"
    key = key or f"new-{board_id}-{clean.casefold()}-{n_same + 1}-{uuid.uuid4().hex[:6]}"
    change_id = monday_push.stage_new_item(board_id, name, fields, user, group_id=g["id"] if g else None, key=key)
    audit_log("BOARD_ROW_STAGED", "monday_changes", str(change_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"new row {str(name)[:60]} on {b.get('name')}")
    return {"staged": True, "pending": True, "change_id": change_id, "staged_id": change_id, "item_id": f"{NEW}{change_id}", "board_id": str(board_id), "name": clean,
            "group": g["title"] if g else None, "group_id": g["id"] if g else None, "understood": understood,
            "message": f"New row '{clean[:80]}' staged on {b.get('name')}" + (f" in {g['title']}" if g else "")}


def add_rows(board_id: str, names: Any, group: Optional[str], cells: Dict[str, str], user: Dict[str, Any], today: Optional[date] = None, client: Any = None) -> Dict[str, Any]:
    """Several rows at once (one per name, one name per line), in the same group, each a pending row. Nothing is staged if any name is bad."""
    if isinstance(names, str):
        names = [n for n in names.splitlines() if n.strip()]
    if not isinstance(names, list) or not names or len(names) > 100 or not all(isinstance(n, str) and n.strip() for n in names):
        raise ValueError("Give the names of the rows as a list of text (at most 100).")
    if group:
        find_group(board_id, group)
    rows = [add_row(board_id, n, group, cells, user, today, client, client_key=f"batch-{uuid.uuid4().hex[:8]}-{i}") for i, n in enumerate(names)]
    return {"staged": True, "pending": True, "count": len(rows), "rows": [{"item_id": r["item_id"], "staged_id": r["staged_id"], "name": r["name"], "group": r["group"]} for r in rows],
            "message": f"{len(rows)} new rows staged on the board (not sent to monday yet)"}


def _row_action(kind: str, board_id: str, item_id: str, payload: Dict[str, Any], summary: str, user: Dict[str, Any]) -> int:
    change_id = monday_push.stage(kind, board_id, item_id, payload, summary, key=f"{kind}-{board_id}-{item_id}-{payload.get('group_id', '')}", source="edit", by=user.get("email", "app"))
    audit_log("BOARD_ROW_ACTION_STAGED", "monday_changes", str(change_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=summary[:120])
    return change_id


def move_row(board_id: str, item_id: str, group: str, user: Dict[str, Any]) -> Dict[str, Any]:
    item = _item(board_id, item_id)
    item_id = str(item["id"])
    g = find_group(board_id, group)
    if item.get("pending"):                                                       # the new row simply goes in the other group: still one create
        _merge_pending(board_id, item["staged_id"], {}, user, extra={"group_id": g["id"]}, log=False)
        return {"staged": True, "pending": True, "pending_row": True, "change_id": item["staged_id"], "item_id": item_id, "item": item["name"], "group": g["title"], "message": f"{item['name']} is now in {g['title']} (staged)"}
    cid = _row_action("move_item", board_id, item_id, {"group_id": g["id"]}, f"Move {item['name']} to {g['title']}", user)
    return {"staged": True, "pending": True, "change_id": cid, "item_id": item_id, "item": item["name"], "group": g["title"], "message": f"{item['name']} will move to {g['title']} (staged)"}


def duplicate_row(board_id: str, item_id: str, with_updates: bool, user: Dict[str, Any]) -> Dict[str, Any]:
    item = _item(board_id, item_id)
    item_id = str(item["id"])
    if item.get("pending"):                                                       # a copy of a pending row is another pending row, with the same cells
        with SessionLocal() as db:
            src = db.get(MondayChangeDB, item["staged_id"])
            fields, gid = dict((src.payload or {}).get("fields") or {}), (src.payload or {}).get("group_id")
        cid = monday_push.stage_new_item(board_id, f"{item['name']} (copy)", fields, user, group_id=gid, key=f"new-{board_id}-copy-{uuid.uuid4().hex[:8]}")
        return {"staged": True, "pending": True, "pending_row": True, "change_id": cid, "staged_id": cid, "item_id": f"{NEW}{cid}", "item": item["name"], "message": f"A copy of {item['name']} is staged"}
    cid = _row_action("duplicate_item", board_id, item_id, {"with_updates": bool(with_updates)}, f"Copy {item['name']}", user)
    return {"staged": True, "pending": True, "change_id": cid, "item_id": item_id, "item": item["name"], "message": f"A copy of {item['name']} will be made (staged)"}


def archive_row(board_id: str, item_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Delete = archive: monday keeps an archived row for 30 days, and until a manager pushes this it is only a staged change you can undo. A pending new row is dropped (its staged create is
    set aside, and undo brings it back)."""
    item = _item(board_id, item_id)
    item_id = str(item["id"])
    if item.get("pending"):
        with SessionLocal() as db:
            row = db.get(MondayChangeDB, item["staged_id"])
            payload = dict(row.payload or {})
            payload.update(archived_by=(user.get("email") or "app").casefold(), archived_at=datetime.now(timezone.utc).isoformat())
            row.payload, row.status, row.updated_at = payload, "SKIPPED", datetime.now(timezone.utc)
            db.commit()
        audit_log("BOARD_ROW_ACTION_STAGED", "monday_changes", str(item["staged_id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"drop new row {item['name'][:60]}")
        return {"staged": True, "pending": True, "pending_row": True, "dropped": True, "change_id": item["staged_id"], "item_id": item_id, "item": item["name"], "message": f"{item['name']} was dropped (undo brings it back)"}
    cid = _row_action("archive_item", board_id, item_id, {}, f"Archive {item['name']}", user)
    return {"staged": True, "pending": True, "change_id": cid, "item_id": item_id, "item": item["name"], "message": f"{item['name']} will be archived (staged; undo it until it is pushed)"}


def comment_row(board_id: str, item_id: str, text: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """A comment on a row. On a pending new row it waits inside the create and is posted on the real row when it is pushed."""
    item = _item(board_id, item_id)
    clean = monday_push._strip_odd(str(text or ""))
    if not clean:
        raise ValueError("Write the comment first.")
    if len(clean) > 4000:
        raise ValueError("That comment is longer than 4,000 characters. Shorten it; it is not cut for you.")
    if item.get("pending"):
        with _pending_lock, SessionLocal() as db:
            row = db.get(MondayChangeDB, item["staged_id"])
            payload = dict(row.payload or {})
            payload["comments"] = list(payload.get("comments") or []) + [f"{clean}\n\n({user.get('email')}, via Allied Operations)"]
            row.payload, row.updated_at = payload, datetime.now(timezone.utc)
            db.commit()
        return {"staged": True, "pending": True, "pending_row": True, "change_id": item["staged_id"], "item_id": str(item["id"]), "message": "The comment will be posted on the row once it is pushed."}
    cid = monday_push.stage_comment(board_id, str(item["id"]), clean, user)
    return {"staged": True, "pending": True, "change_id": cid, "item_id": str(item["id"]), "message": "Comment staged for monday."}


def undo_last(user: Dict[str, Any], board_id: Optional[str] = None) -> Dict[str, Any]:
    with _pending_lock:
        return _undo_dispatch(user, board_id)


def _undo_dispatch(user: Dict[str, Any], board_id: Optional[str] = None) -> Dict[str, Any]:
    from app.services import jobs_board

    if jobs_board.is_jobs(board_id):                                           # the Jobs board's undo is the records layer's undo
        from app.services import records

        return records.undo_last(user)
    return _undo_last(user, board_id)


def _undo_last(user: Dict[str, Any], board_id: Optional[str] = None) -> Dict[str, Any]:
    """Takes back the person's most recent staged edit, one edit at a time: a cell goes back to what it was before that edit (on a real row or a pending new row), a new row they made is
    dropped, a row they dropped comes back, a paste or fill goes back as a whole. Only their own, and only before it is pushed."""
    me = (user.get("email") or "").strip().casefold()
    with SessionLocal() as db:
        q = db.query(MondayChangeDB).filter(MondayChangeDB.status.in_(("STAGED", "SKIPPED")), MondayChangeDB.source.in_(("edit", "owner", "app")))
        if board_id:
            q = q.filter(MondayChangeDB.board_id == str(board_id))
        cands = []
        for r in q.order_by(MondayChangeDB.updated_at.desc(), MondayChangeDB.id.desc()).limit(300):
            p = r.payload or {}
            mine_hist = [h for h in (p.get("history") or []) if h.get("by") == me] if r.kind == "create_item" else []
            if r.status == "SKIPPED":
                if p.get("archived_by") == me:
                    cands.append(r)
            elif (r.staged_by or "").strip().casefold() == me or mine_hist:
                cands.append(r)
        if not cands:
            raise LookupError("You have no staged changes to undo.")
        row = cands[0]
        cid, kind, payload, summary, status_ = row.id, row.kind, dict(row.payload or {}), row.summary, row.status
    if status_ == "SKIPPED":                                                         # a dropped new row comes back
        payload.pop("archived_by", None)
        payload.pop("archived_at", None)
        with SessionLocal() as db:
            r = db.get(MondayChangeDB, cid)
            r.payload, r.status, r.updated_at = payload, "STAGED", datetime.now(timezone.utc)
            db.commit()
        what = f"Brought back the row you dropped ({payload.get('item_name', '')})"
        audit_log("BOARD_EDIT_UNDONE", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=what[:200])
        return {"undone": True, "change_id": cid, "item_id": f"{NEW}{cid}", "message": what}
    if kind == "create_item":                                                        # a pending new row: the person's last cell edit, else the row itself
        hist = list(payload.get("history") or [])
        mine = next((n for n in range(len(hist) - 1, -1, -1) if hist[n].get("by") == me), None)
        if mine is not None:
            h = hist.pop(mine)
            fields = dict(payload.get("fields") or {})
            lastw = dict(payload.get("last_writer") or {})
            overwritten = (h.get("seq") is not None and lastw.get(h["column"]) != h["seq"]) or (h.get("seq") is None and "wrote" in h and fields.get(h["column"]) != h["wrote"])
            if overwritten:                                                             # someone else has written this cell since (even the same text): their value stays
                payload["history"] = hist
                with SessionLocal() as db:
                    r = db.get(MondayChangeDB, cid)
                    r.payload, r.updated_at = payload, datetime.now(timezone.utc)
                    db.commit()
                what = f"Someone changed {h['column']} since your edit, so it was left as it is"
                audit_log("BOARD_EDIT_UNDONE", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=what[:200])
                return {"undone": False, "changed_since": True, "change_id": cid, "item_id": f"{NEW}{cid}", "message": what + "."}
            if h.get("had"):
                fields[h["column"]] = h.get("previous")
            else:
                fields.pop(h["column"], None)
            earlier = next((e["seq"] for e in reversed(hist) if e.get("column") == h["column"] and e.get("seq") is not None), None)
            if earlier is None:
                lastw.pop(h["column"], None)
            else:
                lastw[h["column"]] = earlier                                            # the cell is back to what the previous writer wrote
            payload["fields"], payload["history"], payload["last_writer"] = fields, hist, lastw
            with SessionLocal() as db:
                r = db.get(MondayChangeDB, cid)
                r.payload, r.updated_at = payload, datetime.now(timezone.utc)
                db.commit()
            what = f"Put back the last cell you changed on the new row ({h['column']})"
            audit_log("BOARD_EDIT_UNDONE", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=what[:200])
            return {"undone": True, "change_id": cid, "item_id": f"{NEW}{cid}", "message": what}
        monday_push.skip(cid, user.get("email", ""), role=user.get("role", "OPERATOR"))
        what = f"Dropped the new row you made: {summary}"
        audit_log("BOARD_EDIT_UNDONE", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=what[:200])
        return {"undone": True, "change_id": cid, "message": what}
    if kind == "update_columns" and payload.get("batch"):                       # a paste or a fill: the whole batch goes back together
        with SessionLocal() as db:
            mates = [r.id for r in db.query(MondayChangeDB).filter(MondayChangeDB.status == "STAGED", MondayChangeDB.kind == "update_columns")
                     if (r.payload or {}).get("batch") == payload["batch"] and (r.staged_by or "").strip().casefold() == me]
        for mid in mates:
            monday_push.skip(mid, user.get("email", ""), role=user.get("role", "OPERATOR"))
        what = f"Took back your paste or fill ({len(mates)} row(s))"
        audit_log("BOARD_EDIT_UNDONE", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=what[:200])
        return {"undone": True, "change_id": cid, "rows": len(mates), "message": what}
    hist = list(payload.get("history") or []) if kind == "update_columns" else []
    last = hist[-1] if hist else (payload.get("last") if kind == "update_columns" else None)
    if last and last.get("column") in (payload.get("columns") or {}):
        cols, base = dict(payload["columns"]), dict(payload.get("base") or {})
        if last.get("had"):
            cols[last["column"]] = last.get("previous")
        else:
            cols.pop(last["column"], None)
            base.pop(last["column"], None)
        if cols:
            payload = {"columns": cols, "base": base, "history": hist[:-1] if hist else []}
            with SessionLocal() as db:
                r = db.get(MondayChangeDB, cid)
                r.payload = payload
                r.summary = (summary.split(":")[0] + ": " + ", ".join(f"{k} = {monday_push._show(v)}" for k, v in cols.items()))[:500]
                r.updated_at = datetime.now(timezone.utc)
                db.commit()
            what = f"Put back the last cell you changed ({last['column']})"
        else:
            monday_push.skip(cid, user.get("email", ""), role=user.get("role", "OPERATOR"))
            what = f"Dropped your staged change: {summary}"
    else:
        monday_push.skip(cid, user.get("email", ""), role=user.get("role", "OPERATOR"))
        what = f"Dropped your staged change: {summary}"
    audit_log("BOARD_EDIT_UNDONE", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=what[:200])
    return {"undone": True, "change_id": cid, "message": what}


# ----------------------------------------------------------------------------- the layout: columns and groups

COLUMN_TYPES = {"text": "text", "long text": "long_text", "long_text": "long_text", "number": "numbers", "numbers": "numbers", "status": "status", "date": "date",
                "people": "people", "person": "people", "dropdown": "dropdown", "checkbox": "checkbox", "checkmark": "checkbox", "link": "link", "url": "link",
                "email": "email", "timeline": "timeline", "rating": "rating", "hour": "hour", "time": "hour"}


def _title(value: Any, what: str) -> str:
    t = " ".join(monday_push._strip_odd(str(value or "")).split())
    if not t:
        raise ValueError(f"Give the {what} a name.")
    if len(t) > 100:
        raise ValueError(f"A {what} name can be at most 100 characters.")
    return t


def _layout_action(kind: str, board_id: str, payload: Dict[str, Any], summary: str, key: str, user: Dict[str, Any]) -> int:
    cid = monday_push.stage(kind, board_id, "", payload, summary, key=key, source="edit", by=user.get("email", "app"))
    audit_log("BOARD_LAYOUT_STAGED", "monday_changes", str(cid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=summary[:160])
    return cid


def add_column(board_id: str, title: str, kind: str, labels: Optional[List[str]], user: Dict[str, Any]) -> Dict[str, Any]:
    """A new column of a kind from a short list; a status or dropdown column takes its labels now."""
    b = monday_push._board(board_id)
    title = _title(title, "column")
    ctype = COLUMN_TYPES.get(" ".join(str(kind or "").casefold().split()))
    if ctype is None:
        raise ValueError(f"'{str(kind)[:30]}' is not a column type here. Choose one of: {', '.join(sorted(set(COLUMN_TYPES.values())))}.")
    if any(c["title"].casefold() == title.casefold() for c in b.get("columns", [])):
        raise ValueError(f"{b.get('name')} already has a column called '{title}'. Two columns with one name make edits ambiguous.")
    defaults: Dict[str, Any] = {}
    if labels:
        if ctype not in ("status", "dropdown"):
            raise ValueError("Labels only belong on a status or dropdown column.")
        clean: List[str] = []
        for x in labels:
            x = _title(x, "label")
            if x.casefold() not in {y.casefold() for y in clean}:
                clean.append(x)
        clean = clean[:40]
        defaults = {"labels": {str(i): x for i, x in enumerate(clean)}} if ctype == "status" else {"settings": {"labels": [{"id": i + 1, "label": x} for i, x in enumerate(clean)]}}
    elif ctype in ("status", "dropdown"):
        raise ValueError(f"A {ctype} column needs its labels (a list of names).")
    cid = _layout_action("create_column", board_id, {"title": title, "column_type": ctype, "defaults": defaults}, f"Add a {ctype} column '{title}' to {b.get('name')}",
                         f"create_column-{board_id}-{title.casefold()}", user)
    return {"staged": True, "pending": True, "change_id": cid, "title": title, "type": ctype, "labels": list((defaults.get("labels") or {}).values()) or
            [x["label"] for x in (defaults.get("settings") or {}).get("labels", [])], "message": f"Column '{title}' ({ctype}) will be added to {b.get('name')} (staged)"}


def rename_column(board_id: str, column: str, title: str, user: Dict[str, Any]) -> Dict[str, Any]:
    b = monday_push._board(board_id)
    cols = b.get("columns", [])
    col = next((c for c in cols if c["id"] == column), None)
    if col is None:
        hits = [c for c in cols if c["title"].casefold() == str(column).casefold()]
        if len(hits) != 1:
            raise ValueError(f"There is no single column '{str(column)[:30]}' on {b.get('name')}. Use its id.")
        col = hits[0]
    title = _title(title, "column")
    if any(c["title"].casefold() == title.casefold() and c["id"] != col["id"] for c in cols):
        raise ValueError(f"{b.get('name')} already has a column called '{title}'.")
    cid = _layout_action("rename_column", board_id, {"column_id": col["id"], "title": title}, f"Rename column '{col['title']}' to '{title}' on {b.get('name')}",
                         f"rename_column-{board_id}-{col['id']}", user)
    return {"staged": True, "pending": True, "change_id": cid, "column_id": col["id"], "was": col["title"], "title": title, "message": f"'{col['title']}' will be called '{title}' (staged)"}


def delete_column(board_id: str, column: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a column (staged). It takes every value in it on every row, so the message says so; until a manager pushes it, undo drops it."""
    b = monday_push._board(board_id)
    cols = b.get("columns", [])
    col = next((c for c in cols if c["id"] == column), None)
    if col is None:
        hits = [c for c in cols if c["title"].casefold() == str(column).casefold()]
        if len(hits) != 1:
            raise ValueError(f"There is no single column '{str(column)[:30]}' on {b.get('name')}. Use its id.")
        col = hits[0]
    if col["type"] in ("name", "item_id"):
        raise ValueError(f"The {col['title']} column of a board can't be deleted.")
    filled = sum(1 for i in b.get("items", []) if (i.get("values") or {}).get(col["id"]))
    cid = _layout_action("delete_column", board_id, {"column_id": col["id"], "title": col["title"]}, f"Delete column '{col['title']}' from {b.get('name')}",
                         f"delete_column-{board_id}-{col['id']}", user)
    return {"staged": True, "pending": True, "change_id": cid, "column_id": col["id"], "title": col["title"], "rows_with_a_value": filled,
            "message": f"Column '{col['title']}' will be deleted, with the value in {filled} row(s) (staged; undo it until it is pushed)"}


def add_group(board_id: str, title: str, user: Dict[str, Any]) -> Dict[str, Any]:
    b = monday_push._board(board_id)
    title = _title(title, "group")
    if any(g["title"].casefold() == title.casefold() for g in b.get("groups", [])):
        raise ValueError(f"{b.get('name')} already has a group called '{title}'.")
    cid = _layout_action("create_group", board_id, {"title": title}, f"Add a group '{title}' to {b.get('name')}", f"create_group-{board_id}-{title.casefold()}", user)
    return {"staged": True, "pending": True, "change_id": cid, "title": title, "message": f"Group '{title}' will be added to {b.get('name')} (staged)"}


def rename_group(board_id: str, group: str, title: str, user: Dict[str, Any]) -> Dict[str, Any]:
    b = monday_push._board(board_id)
    g = find_group(board_id, group)
    title = _title(title, "group")
    if any(x["title"].casefold() == title.casefold() and x["id"] != g["id"] for x in b.get("groups", [])):
        raise ValueError(f"{b.get('name')} already has a group called '{title}'.")
    cid = _layout_action("rename_group", board_id, {"group_id": g["id"], "title": title}, f"Rename group '{g['title']}' to '{title}' on {b.get('name')}",
                         f"rename_group-{board_id}-{g['id']}", user)
    return {"staged": True, "pending": True, "change_id": cid, "group_id": g["id"], "was": g["title"], "title": title, "message": f"Group '{g['title']}' will be called '{title}' (staged)"}


# ----------------------------------------------------------------------------- bulk: paste from Excel, fill down

MAX_PASTE_ROWS, MAX_PASTE_COLS = 200, 40


def _grid_from(tsv: Any, rows: Any) -> List[List[str]]:
    if rows is None and isinstance(tsv, str):
        rows = [line.split("\t") for line in tsv.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n")]
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) and all((isinstance(c, (str, int, float)) and not isinstance(c, bool)) or c is None for c in r) for r in rows):
        raise ValueError("Give the pasted cells as rows of text (or as a tab-separated block).")
    if len(rows) > MAX_PASTE_ROWS or any(len(r) > MAX_PASTE_COLS for r in rows):
        raise ValueError(f"A paste can be at most {MAX_PASTE_ROWS} rows by {MAX_PASTE_COLS} columns. Paste it in parts.")
    return [["" if c is None else str(c) for c in r] for r in rows]


def _stage_cells(board_id: str, per_item: Dict[str, Dict[str, Any]], user: Dict[str, Any], what: str) -> List[int]:
    import uuid as _uuid

    batch = _uuid.uuid4().hex[:12]
    ids = []
    for item_id, edits in per_item.items():
        sid = _staged_id(item_id)
        if sid is not None:                                                          # a pending new row: the cells go into its one create
            _merge_pending(board_id, sid, edits, user)
            ids.append(sid)
            continue
        cid = monday_push.stage_edit(board_id, item_id, edits, user)
        monday_push.attach(cid, batch=batch, last=None)
        ids.append(cid)
    audit_log("BOARD_BULK_STAGED", "monday_changes", ",".join(str(i) for i in ids)[:60], actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"{what}: {len(ids)} row(s)")
    return ids


def paste(board_id: str, top_left_item: str, top_left_column: str, rows: Any, user: Dict[str, Any], dry_run: bool = False, tsv: Any = None,
          today: Optional[date] = None, client: Any = None) -> Dict[str, Any]:
    """Cells pasted from Excel: the first goes into (item, column) and the rest follow down the board's rows and across its columns, as Excel would. A blank
    cell changes nothing. Each cell is read like typed text. Every problem is listed with its cell; if there is any, nothing is staged (so the paste is all or
    nothing), and `dry_run` shows the result without staging."""
    b = monday_push._board(board_id)
    grid = _grid_from(tsv, rows)
    items, cols = items_view(board_id), b.get("columns", [])
    top_left_item = str(_item(board_id, top_left_item)["id"]) if _staged_id(top_left_item) is not None else top_left_item
    start_r = next((i for i, x in enumerate(items) if str(x["id"]) == str(top_left_item)), None)
    if start_r is None:
        raise LookupError("The row you pasted into is not on the board as the app last read it. Refresh the board first.")
    anchor = monday_push._column(b, top_left_column) if top_left_column else None
    start_c = next((i for i, c in enumerate(cols) if anchor and c["id"] == anchor["id"]), None)
    if start_c is None:
        raise ValueError("Say which column the paste starts in.")
    cells, problems, per_item = [], [], {}
    for r, line in enumerate(grid):
        for c, said in enumerate(line):
            if not said.strip():
                continue
            where = {"row": r + 1, "col": c + 1}
            if start_r + r >= len(items) or start_c + c >= len(cols):
                problems.append({**where, "text": said[:40], "problem": "There is no row or column here: the paste runs past the board. Rows are not added by a paste."})
                continue
            item, col = items[start_r + r], cols[start_c + c]
            try:
                got = understand(board_id, col["id"], said, today, client)
            except ValueError as exc:
                problems.append({**where, "item": item["name"], "column": col["title"], "text": said[:40], "problem": str(exc)})
                continue
            cells.append({**where, "item": item["name"], "item_id": str(item["id"]), "column": col["title"], "column_id": col["id"], "text": said[:40], "understood": got["understood"],
                          "was": item["values"].get(col["id"]) or "", "becomes": monday_push._show(got["value"])})
            per_item.setdefault(str(item["id"]), {})[col["id"]] = got["value"]
    out = {"dry_run": bool(dry_run), "cells": cells, "problems": problems, "rows_touched": len(per_item), "staged": False, "change_ids": []}
    if problems:
        out["message"] = f"{len(problems)} cell(s) can't be read, so nothing was staged. Fix them (or clear them) and paste again."
    elif not cells:
        out["message"] = "Nothing to change: every pasted cell was blank."
    elif dry_run:
        out["message"] = f"{len(cells)} cell(s) in {len(per_item)} row(s) would be staged."
    else:
        out["change_ids"] = _stage_cells(board_id, per_item, user, f"paste of {len(cells)} cell(s)")
        out["staged"] = True
        out["message"] = f"{len(cells)} cell(s) in {len(per_item)} row(s) staged (not sent to monday yet). Undo takes the whole paste back."
    return out


def fill_down(board_id: str, column: str, from_item: str, user: Dict[str, Any], through_item: Optional[str] = None, to_items: Optional[List[str]] = None,
              dry_run: bool = False, today: Optional[date] = None, client: Any = None) -> Dict[str, Any]:
    """Copy one cell down: the cell of `from_item` (what it will be once staged edits are in, else what it shows) into the rows after it down to `through_item`,
    or into the listed rows."""
    b = monday_push._board(board_id)
    col = monday_push._column(b, column)
    items = items_view(board_id)
    src_i = next((i for i, x in enumerate(items) if str(x["id"]) == str(from_item)), None)
    if src_i is None:
        raise LookupError("The row to copy from is not on the board as the app last read it. Refresh the board first.")
    if to_items is not None:
        if not isinstance(to_items, list) or len(to_items) > MAX_PASTE_ROWS or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in to_items):
            raise ValueError(f"Give the rows to fill as a short list (at most {MAX_PASTE_ROWS}).")
        wanted = {str(t) for t in to_items}
        targets = [x for x in items if str(x["id"]) in wanted and str(x["id"]) != str(from_item)]
    elif through_item is not None:
        end = next((i for i, x in enumerate(items) if str(x["id"]) == str(through_item)), None)
        if end is None or end <= src_i:
            raise ValueError("The last row to fill must be below the row you copy from.")
        targets = items[src_i + 1:end + 1]
        if len(targets) > MAX_PASTE_ROWS:
            raise ValueError(f"At most {MAX_PASTE_ROWS} rows at once.")
    else:
        raise ValueError("Say where to fill down to (through_item) or which rows (to_items).")
    if not targets:
        return {"dry_run": bool(dry_run), "cells": [], "problems": [], "rows_touched": 0, "staged": False, "change_ids": [], "message": "There are no rows to fill."}
    if items[src_i].get("pending"):
        had, staged = False, None
    else:
        had, staged = _waiting_cell(board_id, str(from_item), col["id"], user.get("email", "app"))
    source = staged if had else items[src_i]["values"].get(col["id"])
    if isinstance(source, dict) and "personsAndTeams" in source:
        raise ValueError("A people cell can't be copied down yet. Type the name in each row.")
    shown = monday_push._show(source) if isinstance(source, dict) else source
    if not str(shown or "").strip():
        raise ValueError(f"The {col['title']} cell you copy from is blank.")
    text_in = ", ".join(shown) if isinstance(shown, list) else str(shown)
    # one pasted cell per row, written into the same column: each row is staged on its own item, not walked across the board
    per_item, cells, problems = {}, [], []
    for n, item in enumerate(targets, 1):
        try:
            got = understand(board_id, col["id"], text_in, today, client)
        except ValueError as exc:
            problems.append({"row": n, "item": item["name"], "column": col["title"], "text": text_in[:40], "problem": str(exc)})
            continue
        cells.append({"row": n, "item": item["name"], "item_id": str(item["id"]), "column": col["title"], "column_id": col["id"], "text": text_in[:40], "understood": got["understood"],
                      "was": item["values"].get(col["id"]) or "", "becomes": monday_push._show(got["value"])})
        per_item[str(item["id"])] = {col["id"]: got["value"]}
    out = {"dry_run": bool(dry_run), "cells": cells, "problems": problems, "rows_touched": len(per_item), "staged": False, "change_ids": []}
    if problems:
        out["message"] = f"'{text_in[:40]}' can't be read for {col['title']}, so nothing was staged."
    elif dry_run:
        out["message"] = f"{len(cells)} row(s) would be filled."
    else:
        out["change_ids"] = _stage_cells(board_id, per_item, user, f"fill down of {col['title']}")
        out["staged"] = True
        out["message"] = f"{col['title']} filled in {len(cells)} row(s) (staged, not sent to monday yet). Undo takes the whole fill back."
    return out


# ----------------------------------------------------------------------------- the Jobs board is edited cell by cell (and rows are made as jobs)

def _no_virtual(fn, what: str):
    def wrapped(board_id, *args, **kwargs):
        from app.services import jobs_board

        if jobs_board.is_jobs(board_id):
            raise ValueError(f"The Jobs board is edited cell by cell, and a row is a job. {what} is not done here: use the job's own screen, or put the job on a monday board.")
        return fn(board_id, *args, **kwargs)

    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    return wrapped


move_row = _no_virtual(move_row, "Moving a row")
duplicate_row = _no_virtual(duplicate_row, "Copying a row")
archive_row = _no_virtual(archive_row, "Deleting a row")
comment_row = _no_virtual(comment_row, "A board comment")
paste = _no_virtual(paste, "Pasting")
fill_down = _no_virtual(fill_down, "Filling down")
add_rows = _no_virtual(add_rows, "Adding several rows")
add_column = _no_virtual(add_column, "Adding a column")
rename_column = _no_virtual(rename_column, "Renaming a column")
delete_column = _no_virtual(delete_column, "Deleting a column")
add_group = _no_virtual(add_group, "Adding a group")
rename_group = _no_virtual(rename_group, "Renaming a group")

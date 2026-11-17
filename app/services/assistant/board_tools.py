"""Copilot tools for any monday board, jobs and purchase orders: say it in your own words, the assistant finds the board, the row and the column.

Board changes are STAGED only (the staged-changes list, like a git diff): nothing here ever calls monday_push.push, so a manager still pushes. A write
shows the confirm card first, whose text reads back the board, the row, the column, the value it has now and the value it will have. Reads never guess:
several matches come back as a short list the assistant asks about, and no match says so. All numbers (ids) come from the app's own copy of the boards.
"""
import difflib
import re
from typing import Any, Dict, List, Optional

from app.services.assistant.tools import Tool, _lines, _s, add_card


def _norm(text: Any) -> str:
    """Words for comparing: accents folded, case folded, punctuation gone; Chinese and other letters are kept."""
    import unicodedata

    t = unicodedata.normalize("NFKD", str(text or "").casefold())
    return re.sub(r"[\W_]+", " ", "".join(ch for ch in t if not unicodedata.combining(ch))).strip()


def _boards() -> List[Dict[str, Any]]:
    from app.services.coordination import boards

    return boards.boards()


def resolve_board(text: str) -> str:
    """A board id from what a person says: its number, its name, part of its name or its role ("rfqs", "orders"). Several matches or none: an error
    that names the choices, never a guess."""
    raw = str(text or "").strip()
    rows = _boards()
    if not rows:
        raise ValueError("No monday board has been read into the app yet.")
    if raw.isdigit() and any(str(b["board_id"]) == raw for b in rows):
        return raw
    want = _norm(raw)
    if not want:
        raise ValueError("Say which board. The boards are: " + ", ".join(b["name"] for b in rows[:12]) + (f" and {len(rows) - 12} more." if len(rows) > 12 else "."))
    names = {b["board_id"]: _norm(b["name"]) for b in rows}
    roles = {b["board_id"]: _norm(b.get("label") or b.get("role")) for b in rows}
    for pool in ([i for i, n in names.items() if n == want],
                 [i for i in names if (len(want) >= 3 and (want in names[i] or want in roles[i])) or (len(names[i]) >= 3 and names[i] in want)]):
        if len(pool) == 1:
            return str(pool[0])
        if len(pool) > 1:
            names = [next(b["name"] for b in rows if b["board_id"] == i) for i in pool]
            raise ValueError("Which board? " + ", ".join(names[:8]) + (f" and {len(names) - 8} more. Say more of the name." if len(names) > 8 else "."))
    close = difflib.get_close_matches(want, list(names.values()), n=2, cutoff=0.6)      # a close spelling is only offered, never picked
    maybe = " Did you mean " + " or ".join(next(b["name"] for b in rows if _norm(b["name"]) == n) for n in close) + "?" if close else ""
    raise ValueError(f"I don't know a board called '{raw[:40]}'.{maybe} The boards are: " + ", ".join(b["name"] for b in rows[:12]) + (f" and {len(rows) - 12} more." if len(rows) > 12 else "."))


def _mirror(board_id: str) -> Dict[str, Any]:
    from app.services.coordination import boards

    with boards._lock:
        return boards._mirror.get(str(board_id)) or {}


def _cols(b: Dict[str, Any]) -> Dict[str, str]:
    return {c["id"]: c["title"] for c in b.get("columns", [])}


def _status_id(b: Dict[str, Any]) -> Optional[str]:
    return next((c["id"] for c in b.get("columns", []) if c.get("type") == "status" or c["id"] == "status"), None)


def _row_text(b: Dict[str, Any], item: Dict[str, Any]) -> str:
    cols = _cols(b)
    vals = [f"{cols.get(k, k)}: {str(v)[:60]}" for k, v in (item.get("values") or {}).items() if str(v or "").strip()][:3]
    return f"{item['name']} (id {item['id']})" + (" - " + "; ".join(vals) if vals else "")


def _ranked(board_id: str, text: str, limit: int = 5) -> List[tuple]:
    """Rows of this board that match what was said: an exact id, the name, a value in any column (a customer, a PO), or a close spelling. Best first."""
    b = _mirror(board_id)
    want = _norm(text)
    if not want:
        return []
    scored = []
    for it in b.get("items", []):
        name = _norm(it.get("name"))
        if str(it.get("id")) == str(text).strip():
            score = 100.0
        elif name == want:
            score = 90.0
        elif want in name:
            score = 70.0 + len(want) / max(len(name), 1)
        elif name and name in want:
            score = 60.0
        elif any(want in _norm(v) for v in (it.get("values") or {}).values() if v):
            score = 50.0
        else:
            ratio = difflib.SequenceMatcher(None, want, name).ratio() if name else 0.0
            score = ratio * 40 if ratio >= 0.7 else 0.0
        if score:
            scored.append((score, it))
    scored.sort(key=lambda p: -p[0])
    return scored[:limit]


def find_items(board_id: str, text: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Rows of this board that match what was said, best first."""
    return [it for _, it in _ranked(board_id, text, limit)]


def one_item(board_id: str, ref: Any) -> Dict[str, Any]:
    """The one row meant: an id that exists, or text that matches exactly one row clearly. Otherwise an error listing candidates."""
    b = _mirror(board_id)
    found = next((i for i in b.get("items", []) if str(i.get("id")) == str(ref).strip()), None)
    if found:
        return found
    ranked = _ranked(board_id, str(ref))
    if not ranked:
        raise ValueError(f"No row on {b.get('name')} matches '{str(ref)[:40]}'.")
    hits = [it for _, it in ranked]
    top_score, top = ranked[0]
    clear = len(ranked) == 1 or (top_score >= 90 and ranked[1][0] < 90)
    strong = top_score >= 60 or (top_score >= 50 and len(_norm(ref)) >= 5)       # a name part, or a specific value (a PO number); not "ok" or a near spelling
    if clear and strong:
        return top
    raise ValueError(("Is it this row? " if len(hits) == 1 else "Which row? ") + " | ".join(_row_text(b, h) for h in hits[:5]))


# ------------------------------------------------------------------ reads

def list_boards(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    rows = _boards()
    return _lines([f"- {b['name']} (board {b['board_id']}, {b['items']} rows)" for b in rows], "No board has been read into the app yet.")


def find_board_items(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    board = resolve_board(str(args.get("board") or ""))
    b = _mirror(board)
    hits = find_items(board, str(args.get("text") or ""))
    if not hits:
        return f"Nothing on {b.get('name')} matches '{str(args.get('text') or '')[:40]}'. Do not guess an id: ask the user."
    head = f"On {b.get('name')} (board {board}):" + (" several match, ask which one." if len(hits) > 1 else "")
    return head + "\n" + "\n".join("- " + _row_text(b, h) for h in hits) + "\nColumns: " + ", ".join(_cols(b).values())


def monday_staged(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services import monday_push

    board = resolve_board(str(args["board"])) if args.get("board") else None
    rows = monday_push.preview(None, board)
    for r in rows[:1]:
        add_card("info", f"{len(rows)} change(s) staged", "Nothing is sent until a manager pushes.", [{"label": "Open staged changes", "action": "open", "href": "/health?tab=changes"}])
    out = []
    for r in rows[:15]:
        if r.get("changes"):
            what = "; ".join(f"{c['column']}: {c['now'] or '(blank)'} -> {c['becomes']}" for c in r["changes"])
        elif r.get("comment"):
            what = f"comment: {str(r['comment'])[:80]}"
        else:
            what = f"new row {((r.get('new_row') or {}).get('name'))}"
        out.append(f"- #{r['id']} {r['board']} / {r.get('item') or 'new'}: {what} [{r['status']}]")
    return _lines(out, "Nothing is staged.", len(rows) - 15)


# ------------------------------------------------------------------ board writes (staged only)

def _summary_edit(a: Dict[str, Any]) -> str:
    try:
        board = resolve_board(str(a.get("board") or ""))
        b = _mirror(board)
        item = one_item(board, a.get("item_id") or a.get("item"))
        from app.services import monday_push

        col = monday_push._column(b, str(a.get("column") or ""))
        old = (item.get("values") or {}).get(col["id"])
        return (f"Board: {b.get('name')}\nRow: {item['name']}\nColumn: {col['title']}\n"
                f"{old if str(old or '').strip() else '(blank)'} -> {a.get('value')}\n(staged, not sent to monday)")
    except Exception as exc:  # noqa: BLE001 - the card still shows what was asked; the run will say why it failed
        return f"Edit {a.get('column')} to {a.get('value')} on {a.get('board')} / {a.get('item_id') or a.get('item')} ({exc})"


def _check_edit(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import monday_push

    board = resolve_board(str(a.get("board") or ""))
    item = one_item(board, a.get("item_id") or a.get("item"))
    col, _val = monday_push.check_cell(board, str(a.get("column") or ""), a.get("value"))
    a["board"], a["item_id"], a["column"] = board, str(item["id"]), col["id"]            # pinned: the card and the change are about the same row


def _check_row(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import monday_push

    board = resolve_board(str(a.get("board") or ""))
    if not str(a.get("item_name") or "").strip():
        raise ValueError("Give the new row a name.")
    fields = {}
    for k, v in (a.get("fields") if isinstance(a.get("fields"), dict) else {}).items():
        col, _val = monday_push.check_cell(board, str(k), v, new_row=True)
        fields[col["id"]] = v
    a["board"] = board
    if fields:
        a["fields"] = fields


def _check_comment(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    board = resolve_board(str(a.get("board") or ""))
    item = one_item(board, a.get("item_id") or a.get("item"))
    if not str(a.get("text") or "").strip():
        raise ValueError("Write the comment first.")
    a["board"], a["item_id"] = board, str(item["id"])


def _summary_row(a: Dict[str, Any]) -> str:
    try:
        b = _mirror(resolve_board(str(a.get("board") or "")))
        cols = _cols(b)
        lines = "\n".join(f"{cols.get(k, k)}: {v}" for k, v in (a.get("fields") or {}).items())
        return f"New row on {b.get('name')}: {a.get('item_name')}\n{lines}\n(staged, not sent to monday)"
    except Exception:  # noqa: BLE001 - the run will say why it failed
        return f"New row on {a.get('board')}: {a.get('item_name')} (staged, not sent to monday)"


def stage_cell_edit(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import monday_push

    board = resolve_board(str(args.get("board") or ""))
    item = one_item(board, args.get("item_id") or args.get("item"))
    cid = monday_push.stage_edit(board, str(item["id"]), {str(args.get("column") or ""): args.get("value")}, user)
    return {"done": f"Staged as change #{cid} (not sent to monday): {item['name']}, {args.get('column')} -> {args.get('value')}. A manager pushes it.", "change_id": cid}


def stage_new_row(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import monday_push

    board = resolve_board(str(args.get("board") or ""))
    fields = args.get("fields") if isinstance(args.get("fields"), dict) else {}
    cid = monday_push.stage_new_item(board, str(args.get("item_name") or ""), fields, user)
    return {"done": f"Staged as change #{cid} (not sent to monday): new row '{str(args.get('item_name'))[:80]}' on {_mirror(board).get('name')}. A manager pushes it.", "change_id": cid}


def stage_comment(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import monday_push

    board = resolve_board(str(args.get("board") or ""))
    item = one_item(board, args.get("item_id") or args.get("item"))
    cid = monday_push.stage_comment(board, str(item["id"]), str(args.get("text") or ""), user)
    return {"done": f"Staged as change #{cid} (not sent to monday): comment on {item['name']}. A manager pushes it.", "change_id": cid}


def skip_staged_change(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import monday_push

    out = monday_push.skip(int(args["change_id"]), user.get("email", ""), role=user.get("role", "VIEWER"))
    return {"done": f"Change #{out['id']} skipped; it will not be sent."}


# ------------------------------------------------------------------ jobs and purchase orders

def _job_line(j: Dict[str, Any]) -> str:
    nxt = (j.get("next_steps") or [{}])[0].get("text", "")
    return f"- {j['ref']} {j['title']} ({j.get('customer') or 'no customer'}): {j['stage']}" + (f". Next: {nxt}" if nxt else "")


def list_jobs(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import jobs

    out = jobs.list_jobs(stage=str(args.get("stage") or ""), q=str(args.get("search") or ""), limit=15)
    rows = out["items"] if isinstance(out, dict) else out
    return _lines([_job_line(j) for j in rows], "No job matches.")


def job_detail(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import jobs

    staff = user.get("role") in ("OPERATOR", "MANAGER", "ADMIN")
    try:
        j = jobs.get(str(args["job"]), staff=staff)
    except LookupError:
        hits = jobs.list_jobs(q=str(args["job"]), limit=5)["items"]
        if len(hits) != 1:
            return "Which job? " + " | ".join(h["ref"] + " " + h["title"] for h in hits) if hits else f"No job matches '{str(args['job'])[:40]}'."
        j = jobs.get(hits[0]["id"], staff=staff)
    pos = ", ".join(f"{p['vendor']} PO {p.get('po_number') or '(draft)'} {p['status']}" for p in j["purchase_orders"]) or "none"
    checks = "; ".join(c["text"] for c in j.get("checks", [])) or "none"
    add_card("record", f"{j['ref']} {j['title']}", f"Stage: {j['stage']}", [{"label": "Open job", "action": "open", "href": f"/jobs/{j['ref']}"}])
    return (f"{j['ref']} {j['title']} for {j.get('customer') or 'no customer'}. Stage: {j['stage']}. Done: {', '.join(j['done'])}. "
            f"Next: {'; '.join(s['text'] for s in j['next_steps']) or 'nothing'}. Purchase orders: {pos}. Warnings: {checks}.")


def _job_ref(text: Any) -> str:
    """The one job meant (its reference, RFQ number or id), or an error that lists candidates. Never a guess."""
    from app.services.coordination import jobs

    if len(_norm(text)) < 3:
        raise ValueError("Which job? Give its reference or a few words from its title.")
    try:
        return jobs.get(str(text))["ref"]
    except (LookupError, jobs.JobError):
        hits = jobs.list_jobs(q=str(text), limit=5)["items"]
        if len(hits) == 1:
            return hits[0]["ref"]
        raise ValueError(("Which job? " + " | ".join(f"{h['ref']} {h['title']}" for h in hits)) if hits else f"No job matches '{str(text)[:40]}'.")


def _check_job(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    a["job"] = _job_ref(a.get("job"))


def _check_po(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services.coordination import purchase_orders

    purchase_orders.get(int(a["po_id"]))


def _summary_po(a: Dict[str, Any]) -> str:
    from app.services.coordination import jobs, purchase_orders

    try:
        po = purchase_orders.get(int(a["po_id"]))
        j = jobs.get(po["job_id"])
        total = sum((float(ln.get("amount") or 0) for ln in po["lines"]), 0.0)
        return (f"Approve purchase order for {po['vendor']}\nJob: {j['ref']} {j['title']}\nStatus now: {po['status']}"
                + (f"\nLines: {len(po['lines'])}, total {total:,.2f}" if po["lines"] else "") + "\n(it gets its number; it is not sent)")
    except Exception as exc:  # noqa: BLE001
        return f"Approve purchase order {a.get('po_id')} ({exc})"


def _summary_so(a: Dict[str, Any]) -> str:
    lines = "\n".join(f"- {ln.get('quantity', 1)} x {ln.get('description') or ln.get('raw_item_text')} @ {ln.get('unit_price', '?')}" for ln in (a.get("lines") or [])[:10] if isinstance(ln, dict))
    return f"Make the sales order for job {a.get('job')}\nCustomer's PO: {a.get('customer_reference')}\n{lines}"


def _summary_skip(a: Dict[str, Any]) -> str:
    from app.services import monday_push

    row = next((r for r in monday_push.preview() if r["id"] == a.get("change_id")), None)
    return f"Skip staged change #{a.get('change_id')}: {row['summary']}\n(it will never be sent)" if row else f"Skip staged change #{a.get('change_id')} (it will never be sent)"


def approve_design(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import jobs

    j = jobs.approve_design(str(args["job"]), user)
    return {"done": f"Design approved on {j['ref']}. Purchase orders can now be approved."}


def record_vendor_quote(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import jobs

    j = jobs.record_vendor_quote(str(args["job"]), str(args["vendor"]), {k: args.get(k) for k in ("total", "date", "lead_time", "valid_until", "quote_order_number", "currency")}, user)
    warn = (" Warnings: " + "; ".join(j.get("warnings", []))) if j.get("warnings") else ""
    return {"done": f"Recorded {args['vendor']}'s quotation on {j['ref']}; a purchase order draft is ready.{warn}"}


def draft_po(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import purchase_orders

    po = purchase_orders.create(str(args["job"]), str(args["vendor"]), user)
    return {"done": f"Purchase order draft for {po['vendor']} is ready (id {po['id']}, no number until a manager approves it).", "po_id": po["id"]}


def approve_po(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import purchase_orders

    po = purchase_orders.approve(int(args["po_id"]), user)
    return {"done": f"Purchase order {po['po_number']} approved for {po['vendor']}. It is not sent: that is a separate step a person approves."}


def create_sales_order(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import jobs

    data = {"customer_reference": args.get("customer_reference"), "lines": args.get("lines"), "shipping_address": args.get("shipping_address")}
    j = jobs.create_sales_order(str(args["job"]), data, user)
    return {"done": f"Sales order made for {j['ref']}."}


_ITEM = {"item_id": _s("The row's id from find_board_items (or its exact name)")}
_BOARD = _s("Board name, like 'Open RFQs' or '2026 Orders'")

TOOLS: List[Tool] = [
    Tool("list_boards", "The monday boards the app can see, with their names.", {}, list_boards),
    Tool("find_board_items", "Find rows on a board from words (a customer, a part, a PO). Always use this before changing a row: if several match, ask the user which one. Never invent an id.",
         {"board": _BOARD, "text": _s("Words to find")}, find_board_items, required=["board", "text"]),
    Tool("monday_staged", "Board changes waiting to be pushed to monday (nothing is sent by the assistant).",
         {"board": _s("Board name; blank for all")}, monday_staged),
    Tool("stage_cell_edit", "Change one cell on a monday board row. It is only STAGED for a manager to push, never sent. Find the row first with find_board_items; use the column's real title.",
         {"board": _BOARD, **_ITEM, "column": _s("Column title, from find_board_items"), "value": _s("The new value (for a status, one of its labels)")},
         stage_cell_edit, write=True, min_role="OPERATOR", required=["board", "item_id", "column", "value"], summarize=_summary_edit, check=_check_edit),
    Tool("stage_new_row", "Add a new row to a monday board. Only STAGED for a manager to push. fields = {column title: value}; leave out anything the user did not say.",
         {"board": _BOARD, "item_name": _s("The new row's name"), "fields": {"type": "object", "description": "{column title: value}"}},
         stage_new_row, write=True, min_role="OPERATOR", required=["board", "item_name"], check=_check_row,
         summarize=lambda a: _summary_row(a)),
    Tool("stage_comment", "Comment on a monday row. Only STAGED for a manager to push. Find the row first with find_board_items.",
         {"board": _BOARD, **_ITEM, "text": _s("The comment")}, stage_comment, write=True, min_role="OPERATOR", required=["board", "item_id", "text"], check=_check_comment,
         summarize=lambda a: f"Comment on {a.get('board')} / {a.get('item_id')}:\n{a.get('text')}\n(staged, not sent to monday)"),
    Tool("skip_staged_change", "Drop a staged board change so it is never sent.", {"change_id": {"type": "integer", "description": "Change number from monday_staged"}},
         skip_staged_change, write=True, min_role="OPERATOR", required=["change_id"], summarize=_summary_skip),
    Tool("list_jobs", "Jobs (a customer's request followed from RFQ to shipment), optionally by stage or words.",
         {"search": _s("Customer, title or reference"), "stage": _s("A stage, like VENDOR_QUOTE")}, list_jobs),
    Tool("job_detail", "One job: stage, what is done, what to do next, purchase orders and warnings.",
         {"job": _s("Job reference, RFQ number, or words from its title")}, job_detail, required=["job"]),
    Tool("approve_design", "A manager approves the engineers' design on a job (needed before a purchase order).",
         {"job": _s("Job reference")}, approve_design, write=True, min_role="MANAGER", required=["job"], summarize=lambda a: f"Approve the design on job {a.get('job')}\nPurchase orders can then be approved.", check=_check_job),
    Tool("record_vendor_quote", "Record that a vendor's quotation is in for a job (only facts the user gave). Makes the purchase order draft.",
         {"job": _s("Job reference"), "vendor": _s("Vendor"), "total": _s("Total price, like 1200.00"), "date": _s("Quote date"),
          "valid_until": _s("Quote valid until"), "currency": _s("Three-letter currency if the user said it, like USD or CNY"), "lead_time": _s("Lead time as said"), "quote_order_number": _s("Vendor's quote or order number")},
         record_vendor_quote, write=True, min_role="OPERATOR", required=["job", "vendor"], check=_check_job,
         summarize=lambda a: f"Record {a.get('vendor')}'s quotation on {a.get('job')}" + (f": total {a['total']}" if a.get("total") else "") + (f", valid until {a['valid_until']}" if a.get("valid_until") else "")),
    Tool("draft_po", "Draft the purchase order for one vendor on a job (no number, nothing sent).",
         {"job": _s("Job reference"), "vendor": _s("Vendor")}, draft_po, write=True, min_role="OPERATOR", required=["job", "vendor"], check=_check_job,
         summarize=lambda a: f"Draft a purchase order to {a.get('vendor')} for {a.get('job')}"),
    Tool("approve_po", "A manager approves a purchase order draft: it gets its number. It is not sent.",
         {"po_id": {"type": "integer", "description": "The purchase order's id from job_detail"}}, approve_po, write=True, min_role="MANAGER", required=["po_id"],
         summarize=_summary_po, check=_check_po),
    Tool("create_sales_order", "Make Allied's internal sales order for a job, from the customer's own PO reference.",
         {"job": _s("Job reference"), "customer_reference": _s("The customer's own PO number"),
          "lines": {"type": "array", "items": {"type": "object"}, "description": "What was ordered: description, quantity, unit_price (only what the user said)"},
          "shipping_address": _s("Ship-to address if given")},
         create_sales_order, write=True, min_role="MANAGER", required=["job", "customer_reference", "lines"], check=_check_job, summarize=_summary_so),
]

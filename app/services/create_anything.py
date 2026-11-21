"""Add anything on a dime: a job, an RFQ, a product, an order, a purchase order, a shipment or a board row from plain fields, and (optionally) stage the same
thing as a new row on a monday board.

Creating a record here is the app's own normal work (the same services the screens already call, with the same checks). Putting it on monday is only ever
STAGED (see monday_push): nothing reaches monday until a manager pushes and the owner has switched writing on.
"""

import hashlib
import json
import threading
import time
from typing import Any, Dict, List, Optional

from app.core.audit import audit_log
from app.services import actions, board_edit, monday_push

KINDS = ("job", "rfq", "product", "order", "po", "shipment", "board_row")


class CreateError(ValueError):
    pass


def _text(fields: Dict[str, Any], key: str, limit: int = 256) -> str:
    v = fields.get(key)
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise CreateError(f"'{key}' must be text.")
    return " ".join(str(v).split())[:limit]


def _items(fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What is asked for, from a list of {description, quantity, unit} or from plain text (one line each: '2 x flex hose')."""
    raw = fields.get("items")
    if isinstance(raw, str):
        raw = [{"description": line.split(" x ", 1)[-1].strip(), "quantity": line.split(" x ", 1)[0].strip() if " x " in line else "1"} for line in raw.splitlines() if line.strip()]
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or len(raw) > 100 or not all(isinstance(x, dict) for x in raw):
        raise CreateError("'items' must be a short list of {description, quantity, unit}.")
    return raw


def _rfq(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import rfqs

    data = {"customer": _text(fields, "customer"), "subject": _text(fields, "subject") or _text(fields, "title"), "contact_email": _text(fields, "contact_email") or None,
            "summary": _text(fields, "summary", 2000) or None, "notes": _text(fields, "notes", 2000) or None, "items": _items(fields), "vendor": _text(fields, "vendor") or None}
    if fields.get("complexity"):
        data["complexity"] = _text(fields, "complexity").upper()
    r = rfqs.create({k: v for k, v in data.items() if v not in (None, "", [])}, user)
    return {"kind": "rfq", "ref": r["number"], "id": r["id"], "title": f"{r['number']} {r.get('subject') or ''}".strip(), "link": f"/orders?kind=rfq&ref={r['number']}", "message": f"RFQ {r['number']} added."}


def _job(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import jobs

    if not _text(fields, "customer"):
        raise CreateError("Say which customer the job is for.")
    r = _rfq(fields, user)
    j = jobs.from_rfq(r["ref"], user)
    return {"kind": "job", "ref": j["ref"], "id": j["id"], "title": f"{j['ref']} {j['title']}".strip(), "link": f"/jobs/{j['ref']}", "rfq": r["ref"], "message": f"Job {j['ref']} started (RFQ {r['ref']})."}


def _product(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    import csv
    import io

    from app.services.csv_import_service import csv_import_service

    item_id = _text(fields, "item_id") or _text(fields, "part_number") or _text(fields, "name")
    if not item_id:
        raise CreateError("Give the part number.")
    if item_id in csv_import_service.matcher.items_cache:
        raise CreateError(f"{item_id} is already in the catalogue. Edit it instead.")
    row = {"item_id": item_id, "description": _text(fields, "description") or item_id}
    for key in ("base_price", "standard_cost", "preferred_vendor", "uom", "category"):
        if _text(fields, key):
            row[key] = _text(fields, key)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(row), lineterminator="\n")
    w.writeheader()
    w.writerow(row)
    res = csv_import_service.import_csv(buf.getvalue(), "ITEMS", actor_email=user.get("email", "system"), actor_role=user.get("role", "SYSTEM"))
    if res.records_created != 1:
        raise CreateError("; ".join(res.errors)[:300] or "The part was not added.")
    return {"kind": "product", "ref": item_id, "title": f"{item_id} {row['description']}", "link": f"/orders?kind=product&ref={item_id}", "message": f"Part {item_id} added to the catalogue."}


def _real_part(text: str) -> str:
    """An order line must be a part in the catalogue: the exact id (any case) is used as the catalogue spells it; anything else is refused with the closest matches."""
    from app.core.container import get_entity_matcher
    from app.services.coordination import jobs

    said = " ".join(str(text or "").split())
    if not said:
        raise CreateError("Every line needs a part.")
    cache = get_entity_matcher().items_cache
    hit = next((k for k in cache if k.casefold() == said.casefold()), None)
    if hit:
        return hit
    import difflib

    low = said.casefold()
    ranked = sorted(cache.items(), key=lambda kv: (-(1.0 if low in kv[0].casefold() else difflib.SequenceMatcher(None, low, kv[0].casefold()).ratio()), kv[0]))        # by how much the part number looks like what was said
    close = [{"item_id": k, "description": str((v or {}).get("description") or "")} for k, v in ranked[:5]]
    raise CreateError(f"'{said[:60]}' is not a part in the catalogue. Closest: " + "; ".join(f"{c['item_id']} ({c['description'][:40]})" for c in close) + ". Pick one, or add the part first.")


def _order(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    lines = []
    for x in _items(fields):
        qty = x.get("quantity", 1)
        try:
            ok = not isinstance(qty, bool) and float(qty) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok and qty not in (None, ""):
            raise CreateError("Every line needs a quantity above zero.")
        lines.append({"raw_item_text": _real_part(str(x.get("part") or x.get("description") or x.get("raw_item_text") or "")), "quantity": x.get("quantity") or 1, **({"unit_price": x["unit_price"]} if x.get("unit_price") not in (None, "") else {})})
    for key in ("customer", "customer_po_number", "shipping_address"):
        if not _text(fields, key, 600):
            raise CreateError({"customer": "Say which customer.", "customer_po_number": "Give the customer's PO number.", "shipping_address": "Give the ship-to address."}[key])
    if not lines:
        raise CreateError("Give at least one line (what and how many).")
    req = InboundOrderRequest(customer_name=_text(fields, "customer"), customer_po_number=_text(fields, "customer_po_number", 64), shipping_address=_text(fields, "shipping_address", 600),
                              line_items=lines, payment_terms=_text(fields, "payment_terms") or None, job=_text(fields, "job") or None)
    out = OrderOrchestrator().process_inbound_order(req, actor_email=user.get("email", "system"))
    so = str(out.sales_order_number)
    return {"kind": "order", "ref": so, "title": f"Order {so} · {req.customer_name}", "link": f"/orders?kind=so&ref={so}", "message": f"Order {so} made for PO {req.customer_po_number}."}


def _po(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import purchase_orders

    job, vendor = _text(fields, "job"), _text(fields, "vendor")
    if not job:
        raise CreateError("A purchase order belongs to a job. Say which (for example JOB-2026-0001).")
    po = purchase_orders.create(job, vendor, user, lines=[{"description": str(x.get("description") or ""), "quantity": x.get("quantity") or 1, "unit_cost": x.get("unit_cost") or x.get("unit_price"),
                                                          **({"revision": x["revision"]} if x.get("revision") else {})} for x in _items(fields)] or None,
                                terms=_text(fields, "terms"), ship_via=_text(fields, "ship_via"))
    ref = po["po_number"] or str(po["id"])
    return {"kind": "po", "ref": str(po["id"]), "id": po["id"], "title": f"PO {ref} · {po['vendor']}", "link": f"/orders?kind=po&ref={po['id']}", "message": f"Purchase order for {po['vendor']} drafted (approve it before it can be sent)."}


def _shipment(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import shipments

    keep = {k: _text(fields, k) for k in ("reference", "container_no", "awb", "forwarder", "carrier", "origin", "destination", "etd", "eta", "free_time_ends", "mode", "status") if _text(fields, k)}
    s = shipments.create(keep, user)
    return {"kind": "shipment", "ref": str(s["id"]), "id": s["id"], "title": f"Shipment {s.get('reference') or s.get('container_no') or s['id']}", "link": f"/jobs?shipment={s['id']}", "message": "Shipment added."}


def _board_row(fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    board = _text(fields, "board_id")
    if not board:
        raise CreateError("Say which board.")
    cells = fields.get("cells") or {}
    if not isinstance(cells, dict):
        raise CreateError("'cells' must be {column: text}.")
    got = board_edit.add_row(board, _text(fields, "name"), _text(fields, "group") or None, cells, user)
    return {"kind": "board_row", "ref": f"{board}:new", "title": got["name"], "link": "/boards", "change_id": got["change_id"], "staged": True, "message": got["message"]}


_MAKE = {"job": _job, "rfq": _rfq, "product": _product, "order": _order, "po": _po, "shipment": _shipment, "board_row": _board_row}


_RECENT: Dict[str, Any] = {}
_RECENT_LOCK = threading.RLock()
_ONCE_SECONDS = 120


def _once(kind: str, fields: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """A job, RFQ or shipment asked for twice in a row by the same person (a double click, a retry) is one. The same facts within two minutes give back the one already made;
    a different fact is a different one. A client_key in the fields makes the same promise exactly."""
    if kind not in ("job", "rfq", "shipment"):
        return _MAKE[kind](fields, user)
    body = {k: v for k, v in fields.items() if k != "client_key"}
    key = f"{user.get('email', '')}|{kind}|" + (str(fields.get("client_key")) if fields.get("client_key") else hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest())
    with _RECENT_LOCK:
        now = time.monotonic()
        for k in [k for k, (_m, at) in _RECENT.items() if now - at > _ONCE_SECONDS]:
            _RECENT.pop(k, None)
        if key in _RECENT:
            return {**_RECENT[key][0], "message": "Already added a moment ago: " + _RECENT[key][0]["message"], "duplicate": True}
        made = _MAKE[kind](fields, user)
        _RECENT[key] = (made, now)
        return made


def create(kind: str, fields: Dict[str, Any], user: Dict[str, Any], to_monday: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make one thing from plain fields. Returns {kind, ref, title, link, message}; with to_monday also stages the same thing as a new row on that board."""
    if kind not in _MAKE:
        raise CreateError(f"I can add: {', '.join(KINDS)}.")
    if not isinstance(fields, dict):
        raise CreateError("Give the fields as {name: value}.")
    if to_monday is not None and (not isinstance(to_monday, dict) or not str(to_monday.get("board_id") or "").strip()):
        raise CreateError("'to_monday' must say which board: {board_id, group?}.")
    if to_monday and kind == "board_row":
        raise CreateError("A board row is already on a board.")
    made = _once(kind, fields, user)
    audit_log("RECORD_CREATED", kind, str(made["ref"])[:60], actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=made["title"][:120])
    if to_monday:
        try:
            made["monday"] = to_monday_stage(kind, made["ref"], str(to_monday["board_id"]), str(to_monday.get("group") or "") or None, user)
        except (ValueError, LookupError) as exc:               # the record is made; only the board step failed, and it can be done again
            made["monday"] = {"staged": False, "problem": str(exc).strip("'\"")}
            made["message"] += f" It was not staged for monday: {made['monday']['problem']}"
    return made


def to_monday_stage(kind: str, ref: str, board_id: str, group: Optional[str], user: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Stage this record as a new row on a board: its name, and a cell for each of its facts whose label matches a column title on that board and reads cleanly.
    What cannot be placed is listed, never guessed. Staged only; asking again is the same staged row."""
    if not str(board_id or "").strip():
        raise ValueError("Say which board.")
    if kind == "job":
        from app.services import jobs_board
        from app.services.coordination import jobs as _jobs

        have = jobs_board.stored_links().get(_jobs.get(ref, staff=True)["ref"])
        if have:
            raise ValueError(f"This job is already on monday (item {have['item_id']}). Putting it there again would make a second item: link it instead, or unlink it first.")
    ctx = actions.context(kind, ref)
    b = monday_push._board(board_id)
    if group:
        board_edit.find_group(board_id, group)
    by_title = {c["title"].casefold(): c for c in b.get("columns", [])}
    cells, left_out, shown = {}, [], []
    for label, value, aud in ctx["rows"]:
        if "internal" not in aud:
            continue
        col = by_title.get(label.casefold())
        if col is None or col["type"] in board_edit.NOT_EDITABLE:
            left_out.append({"fact": label, "reason": "no matching column" if col is None else "that column can't be set from here"})
            continue
        try:
            got = board_edit.understand(board_id, col["id"], value)
            cells[col["id"]] = got["value"]
            shown.append({"fact": label, "column": col["title"], "value": monday_push._show(got["value"])})
        except ValueError as exc:
            left_out.append({"fact": label, "reason": str(exc)[:120]})
    name = ctx["title"]
    g = board_edit.find_group(board_id, group) if group else None
    gid = g["id"] if g else None
    if dry_run:
        return {"staged": False, "dry_run": True, "board": b.get("name"), "group": g["title"] if g else None, "name": name, "columns": shown, "left_out": left_out,
                "message": f"'{name}' would be a new row on {b.get('name')}" + (f" in {g['title']}" if g else "") + f", with {len(shown)} cell(s) filled."}
    cid = monday_push.stage_new_item(board_id, name, cells, user, group_id=gid)
    if kind == "job":                                                                  # when this row is pushed, the real monday item becomes the job's link
        from app.services.coordination import jobs as _jobs

        monday_push.attach(cid, link_job=_jobs.get(ref, staff=True)["ref"])
    audit_log("RECORD_TO_MONDAY_STAGED", kind, str(ref)[:60], actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"board {board_id}: {name[:80]}")
    return {"staged": True, "change_id": cid, "board": b.get("name"), "group": g["title"] if g else None, "name": name, "cells": len(cells), "columns": shown, "left_out": left_out,
            "message": f"'{name}' is staged as a new row on {b.get('name')} (nothing is sent until a manager pushes)."}


_FACT = {"contact_email": "Contact", "owner_email": "Owner", "customer_po_number": "Customer's PO", "customer_name": "Customer", "payment_terms": "Terms", "shipping_address": "Ship to"}


def predict_monday(kind: str, fields: Dict[str, Any], board_id: str, group: Optional[str]) -> Dict[str, Any]:
    """For a confirm card BEFORE the record exists: the board, the group and which of the given facts would land in a column of that board (by the same title) and which
    would not. The record's own facts are named like its fields ("Customer", "Subject"), so this is what the staging will do, not a guess about columns."""
    b = monday_push._board(board_id)
    g = board_edit.find_group(board_id, group) if group else None
    by_title = {c["title"].casefold(): c for c in b.get("columns", [])}
    fills, left = [], []
    for k, v in (fields or {}).items():
        if v in (None, "", [], {}) or isinstance(v, (dict, list)):
            continue
        label = _FACT.get(k, k.replace("_", " ").title())
        col = by_title.get(label.casefold())
        if col is None or col["type"] in board_edit.NOT_EDITABLE:
            left.append(label)
        else:
            fills.append(f"{label} -> {col['title']}")
    return {"board": b.get("name"), "group": g["title"] if g else None, "fills": fills, "left_out": left}

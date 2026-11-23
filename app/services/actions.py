"""One-click actions on a job, purchase order, shipment, order or board row: what applies, a clean plain-text summary for the clipboard (and a TSV),
an email draft that waits for approval like every other email, a comment with an @mention, and handing the ball.

Nothing here sends anything. An email draft is a proposal in the same approval queue as the assistant's drafts (a person confirms it, and the mail
service still decides whether it is delivered, redirected or only drafted). A comment is posted in the app's own thread, or staged for monday.

Who may see what: every line of a summary is tagged with the audiences it is fit for. The summary for yourself shows all of it; a draft to a vendor
never carries the customer or Allied's own sales order number, and a draft to a customer never carries a vendor's name or our PO numbers (blind
shipment). The attachments allowed also depend on the audience, and they are chosen again when the draft is approved.
"""

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal

KINDS = ("job", "rfq", "po", "shipment", "order", "row", "product")
AUDIENCES = ("internal", "vendor", "customer", "forwarder")
_ALL = frozenset(AUDIENCES)
_INT = frozenset({"internal"})
_COST = frozenset({"internal$"})                  # costs and margins: Allied managers only (and a vendor sees the price on its own PO)
# which attachment kinds may go to which audience
ATTACH_FOR = {"po_pdf": {"internal", "vendor"}, "design_file": {"internal", "vendor"}, "commercial_invoice": {"internal", "forwarder"},
              "packing_list": {"internal", "forwarder"}, "packing_slip": {"internal", "customer"}, "board_export": {"internal"}, "drawing_overlay": {"internal", "vendor"},
              "payment_doc": {"internal", "vendor"}, "po_file": {"internal", "vendor"}}
_ATTACH_LABEL = {"po_pdf": "Purchase order PDF", "design_file": "Drawing", "commercial_invoice": "Commercial invoice", "packing_list": "Packing list",
                 "packing_slip": "Packing slip", "board_export": "This row as a CSV file", "drawing_overlay": "Drawing comparison overlay", "payment_doc": "Wire copy or proforma", "po_file": "File on this PO's job"}


class ActionError(ValueError):
    pass


def _plain(v: Any) -> str:
    return " ".join(str(v if v is not None else "").split())


def _row(label: str, value: Any, aud: frozenset = _INT) -> Optional[Tuple[str, str, frozenset]]:
    v = _plain(value)
    return (label, v, aud) if v else None


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


# ----------------------------------------------------------------------------- what each kind of thing is, as facts

def _overlay_pairs(job_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The comparisons that can be attached: each file against the one before it (oldest to newest), as one overlay picture each."""
    ok = sorted((f for f in job_files if str(f["name"]).lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".dxf"))), key=lambda f: f["id"])
    return [{"type": "drawing_overlay", "id": f"{a['id']}:{b['id']}", "name": f"Drawing comparison: {a['name']} against {b['name']}"} for a, b in zip(ok, ok[1:])][-5:]


def _job(ref: str) -> Dict[str, Any]:
    from app.services.coordination import jobs, rfqs

    j = jobs.get(ref, staff=True)
    rows: List[Any] = [_row("Job", f"{j['ref']} {j['title']}", _ALL), _row("Customer", j["customer"], frozenset({"internal", "customer"})),
                       _row("Stage", j["stage_word"], _ALL),
                       _row("Sales order", (j.get("sales_order") or {}).get("number"), _INT),
                       _row("Customer's PO", (j.get("sales_order") or {}).get("customer_reference"), frozenset({"internal", "customer"}))]
    if j.get("ball"):
        rows.append(_row("Who has the ball", f"{j['ball'].get('ball_name') or j['ball'].get('ball_with')}" + (f" ({j['ball']['note']})" if j["ball"].get("note") else ""), _INT))
    for w in j.get("waiting", []):
        rows.append(_row("Waiting", f"{w.get('what')}" + (f" ({w.get('who')})" if w.get("who") else ""), _INT))
    for n in j.get("next_steps", []):
        rows.append(_row("Next", n.get("text"), _INT))
    for po in (j.get("purchase_orders") or []):
        rows.append(_row("PO", f"{po.get('po_number')} · {po.get('vendor')} · {po.get('status')}", _INT))
    for s in (j.get("shipments") or []):
        rows.append(_row("Shipment", f"{s.get('status')} · ETA {s.get('eta') or 'not known'}", frozenset({"internal", "customer"})))
    contact, thread = None, None
    if j.get("rfq"):
        r = rfqs.get(j["rfq"]["number"])
        contact, thread = r.get("contact_email"), ("rfq", j["rfq"]["number"])
    files = [{"type": "design_file", "id": f["id"], "name": f["name"]} for f in j["design"]["files"] if f.get("design")]
    files += [{"type": "po_pdf", "id": p["id"], "name": f"PO {p['po_number']}"} for p in (j.get("purchase_orders") or []) if p.get("id")]
    files += _overlay_pairs(j["design"]["files"])
    return {"title": f"{j['ref']} {j['title']}".strip(), "job_id": j["id"], "so_number": (j.get("sales_order") or {}).get("number"), "rows": [r for r in rows if r], "to": {"customer": contact}, "thread": thread, "files": files,
            "default_audience": "internal", "link": f"/jobs/{j['ref']}", "owner": (j.get("ball") or {}).get("owner_email")}


def _po(ref: str) -> Dict[str, Any]:
    from app.services.coordination import jobs, purchase_orders

    po = purchase_orders.by_number(str(ref)) or (purchase_orders.get(int(ref)) if str(ref).isdigit() and int(ref) < 10**6 else None)         # the number people read first, then the id
    if not po:
        raise LookupError(f"No purchase order {ref}.")
    job = jobs.get(po["job_id"], staff=True)
    rows: List[Any] = [_row("Purchase order", po["po_number"] or "(not numbered yet)", frozenset({"internal", "vendor"})), _row("Vendor", po["vendor"], frozenset({"internal", "vendor"})),
                       _row("Status", po["status_word"], _INT), _row("Job", job["ref"], _INT), _row("Customer", job["customer"], _INT)]
    for i, ln in enumerate(po["lines"], 1):
        if isinstance(ln, dict):
            rows.append(_row(f"Line {i}", f"{ln.get('qty') or ''} x {ln.get('description') or ''}" + (f" (drawing revision {ln['revision']})" if ln.get("revision") else ""),
                             frozenset({"internal", "vendor"})))
            rows.append(_row(f"Line {i} price", _money(ln.get("unit_price") or ln.get("unit_cost")), frozenset({"internal$", "vendor"})))
    rows += [_row("Total", _money(po["total"]) if po["total"] else "", frozenset({"internal$", "vendor"})), _row("Terms", po["terms"], frozenset({"internal", "vendor"})),
             _row("Ship via", po["ship_via"], frozenset({"internal", "vendor"})), _row("Good through", po["good_thru"], frozenset({"internal", "vendor"})),
             _row("Approved by", po["approved_by"], _INT), _row("Sent", po["sent_at"], _INT)]
    files = [{"type": "po_pdf", "id": po["id"], "name": f"PO {po['po_number'] or po['id']}"}]
    files += [{"type": "design_file", "id": f["id"], "name": f["name"]} for f in job["design"]["files"] if f.get("design")]
    files += _overlay_pairs(job["design"]["files"])
    files += [{"type": "commercial_invoice", "id": po["id"], "name": "Commercial invoice"}, {"type": "packing_list", "id": po["id"], "name": "Packing list"}]
    return {"title": f"PO {po['po_number'] or po['id']} · {po['vendor']}", "job_id": job["id"], "so_number": (job.get("sales_order") or {}).get("number"), "rows": [r for r in rows if r], "to": {"vendor": po["vendor_email"]},
            "thread": ("rfq", job["rfq"]["number"]) if job.get("rfq") else None, "files": files, "default_audience": "vendor", "link": f"/jobs/{job['ref']}"}


def _shipment(ref: str) -> Dict[str, Any]:
    from app.services.coordination import shipments

    s = next((x for x in shipments.list_shipments() if str(x["id"]) == str(ref) or str(x.get("reference") or "").casefold() == str(ref).casefold()
              or str(x.get("container_no") or "").casefold() == str(ref).casefold()), None)
    if s is None:
        raise LookupError(f"No shipment {ref}.")
    rows = [_row("Shipment", s.get("reference") or s.get("container_no") or s["id"], _ALL), _row("Status", s.get("status_word"), _ALL), _row("Mode", s.get("mode"), _ALL),
            _row("Forwarder", s.get("forwarder"), frozenset({"internal", "forwarder"})), _row("Carrier", s.get("carrier"), frozenset({"internal", "forwarder"})),
            _row("Container", s.get("container_no"), frozenset({"internal", "forwarder"})), _row("AWB", s.get("awb"), frozenset({"internal", "forwarder"})),
            _row("From", s.get("origin"), _ALL), _row("To", s.get("destination"), _ALL), _row("Departs", s.get("etd"), _ALL),
            _row("ETA", s.get("eta_current") or s.get("eta_original"), _ALL), _row("Originally due", s.get("eta_original") if s.get("eta_current") != s.get("eta_original") else "", _ALL),
            _row("Last free day at the port", s.get("free_time_ends"), frozenset({"internal", "forwarder"})), _row("Flags", "; ".join(s.get("flags") or []), _INT)]
    if s.get("events"):
        e = s["events"][0]
        rows.append(_row("Latest", f"{(e.get('at') or '')[:10]} {e.get('description') or ''}", _INT))
    return {"title": f"Shipment {s.get('reference') or s.get('container_no') or s['id']}", "rows": [r for r in rows if r], "to": {}, "thread": ("shipment", str(s["id"])), "files": [],
            "default_audience": "internal", "link": f"/shipments?id={s['id']}", "owner": s.get("owner_email")}


def _order(ref: str) -> Dict[str, Any]:
    from app.services.database_service import DatabaseService

    with SessionLocal() as db:
        o = DatabaseService.get_order_by_so(db, str(ref))
    if not o:
        raise LookupError(f"No order {ref}.")
    rows: List[Any] = [_row("Sales order", o["sales_order_number"], _INT), _row("Customer", o["customer_name"], frozenset({"internal", "customer"})),
                       _row("Customer's PO", o["customer_po_number"], frozenset({"internal", "customer"})), _row("Status", o["status_word"], _ALL),
                       _row("Total", _money(o["total_revenue"]), frozenset({"internal", "customer"})), _row("Margin", f"{o['gross_margin_pct']}%" if o.get("gross_margin_pct") is not None else "", _COST),
                       _row("Ship to", o["shipping_address"], frozenset({"internal", "customer"})), _row("Terms", o["payment_terms"], frozenset({"internal", "customer"}))]
    for i, ln in enumerate(o["line_items"] or [], 1):
        if isinstance(ln, dict):
            rows.append(_row(f"Line {i}", f"{ln.get('quantity') or ln.get('qty') or ''} x {ln.get('description') or ln.get('raw_item_text') or ln.get('item_id') or ''}", frozenset({"internal", "customer"})))
    for vendor, po in (o["purchase_orders"] or {}).items():
        rows.append(_row("Vendor PO", f"{po} · {vendor}", _INT))
    files = [{"type": "packing_slip", "id": o["sales_order_number"], "name": "Packing slip"}] if o.get("packing_slip_path") else []
    return {"title": f"Order {o['sales_order_number']} · {o['customer_name']}", "safe_title": f"Order for PO {o['customer_po_number']}", "vendor_title": "Order enquiry", "so_number": o["sales_order_number"], "rows": [r for r in rows if r], "to": {}, "thread": ("order", o["sales_order_number"]), "files": files,
            "default_audience": "internal", "link": "/orders"}


def _rfq(ref: str) -> Dict[str, Any]:
    from app.services.coordination import rfqs

    r = rfqs.get(ref)
    rows: List[Any] = [_row("RFQ", r["number"], _ALL), _row("Customer", r.get("customer"), frozenset({"internal", "customer"})), _row("Subject", r.get("subject"), frozenset({"internal", "customer"})),
                       _row("Status", r.get("status"), _INT), _row("Contact", r.get("contact_email"), _INT), _row("Owner", r.get("owner_email"), _INT),
                       _row("Quote", r.get("quote_number"), frozenset({"internal", "customer"})), _row("Notes", r.get("notes"), _INT)]
    for i, it in enumerate(r.get("items") or [], 1):
        if isinstance(it, dict):
            rows.append(_row(f"Item {i}", f"{it.get('quantity') or ''} {it.get('unit') or ''} {it.get('description') or ''}", frozenset({"internal", "customer"})))
    return {"title": f"{r['number']} {r.get('subject') or ''}".strip(), "rows": [x for x in rows if x], "to": {"customer": r.get("contact_email")}, "thread": ("rfq", r["number"]), "files": [],
            "default_audience": "internal", "link": f"/orders?kind=rfq&ref={r['number']}"}


def _product(ref: str) -> Dict[str, Any]:
    from app.core.container import get_entity_matcher

    it = get_entity_matcher().items_cache.get(str(ref)) or next((v for k, v in get_entity_matcher().items_cache.items() if k.casefold() == str(ref).casefold()), None)
    if it is None:
        raise LookupError(f"No part {ref} in the catalogue.")
    rows = [_row("Part", it.get("item_id") or ref, _INT), _row("Description", it.get("description"), _INT), _row("Price", _money(it.get("base_price")) if it.get("base_price") else "", _INT),
            _row("Vendor", it.get("preferred_vendor"), _INT), _row("Unit", it.get("uom"), _INT), _row("Category", it.get("category"), _INT),
            _row("Cost", _money(it.get("standard_cost")) if it.get("standard_cost") else "", _COST), _row("On hand", it.get("stock_on_hand"), _INT)]
    return {"title": f"Part {it.get('item_id') or ref} {it.get('description') or ''}".strip(), "rows": [r for r in rows if r], "to": {}, "thread": None, "files": [],
            "default_audience": "internal", "link": f"/orders?kind=product&ref={ref}"}


def _board_row(ref: str) -> Dict[str, Any]:
    from app.services import board_edit, monday_push

    board_id, _, item_id = str(ref).partition(":")
    b = monday_push._mirror(board_id)
    if not b:
        raise LookupError("That row is not on the board as the app last read it. Refresh the board first.")
    it = board_edit._item(board_id, item_id)                 # a real row, or a pending new row ("new:<staged id>")
    item_id = str(it["id"])
    cols = {c["id"]: c for c in b.get("columns", [])}
    rows: List[Any] = [_row("Board", b.get("name"), _ALL), _row("Row", it["name"], _ALL), _row("Group", it.get("group_title"), _ALL)]
    contact = None
    for cid, v in (it.get("values") or {}).items():
        c = cols.get(cid)
        if c and _plain(v):
            rows.append(_row(c["title"], v, _INT))
            if c["type"] == "email" and not contact:
                contact = _plain(v).split()[-1]
    return {"title": f"{b.get('name')} · {it['name']}", "rows": [r for r in rows if r], "to": {"customer": contact}, "thread": ("board_item", item_id),
            "files": [{"type": "board_export", "id": ref, "name": "Row as CSV"}], "default_audience": "internal", "link": "/boards", "board": (board_id, item_id)}


_BUILD = {"job": _job, "product": _product, "rfq": _rfq, "po": _po, "shipment": _shipment, "order": _order, "row": _board_row}


def context(kind: str, ref: str) -> Dict[str, Any]:
    if kind not in _BUILD:
        raise ActionError(f"Actions work on: {', '.join(KINDS)}.")
    ref = str(ref or "").strip()
    if not ref or len(ref) > 80:
        raise ActionError("Say which one.")
    return {"kind": kind, "ref": ref, **_BUILD[kind](ref)}


# ----------------------------------------------------------------------------- the lists, text and TSV

def _lines(ctx: Dict[str, Any], audience: str, manager: bool = False) -> List[Tuple[str, str]]:
    """The facts fit for this audience. An operator's own summary leaves out costs and margins; a manager's has them."""
    return [(label, value) for label, value, aud in ctx["rows"] if audience in aud or (audience == "internal" and manager and "internal$" in aud)]


def _title(ctx: Dict[str, Any], audience: str) -> str:
    """The heading: for anyone outside Allied, one with no internal numbers in it, and for a vendor none that names the customer's own reference."""
    if audience == "internal":
        return ctx["title"]
    if audience == "vendor" and ctx.get("vendor_title"):
        return ctx["vendor_title"]
    return ctx.get("safe_title", ctx["title"])


def text(kind: str, ref: str, audience: str = "internal", manager: bool = False) -> Dict[str, Any]:
    """A clean plain-text summary for the clipboard, and the same as TSV (label, value) for a spreadsheet. `audience` says whom it is fit to show."""
    if audience not in AUDIENCES:
        raise ActionError(f"The audience must be one of: {', '.join(AUDIENCES)}.")
    ctx = context(kind, ref)
    pairs = _lines(ctx, audience, manager)
    width = max((len(k) for k, _ in pairs), default=0)
    body = "\n".join(f"{k.ljust(width)}  {v}" for k, v in pairs)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    for k, v in pairs:
        w.writerow([k, v])
    return {"kind": kind, "ref": ref, "audience": audience, "title": _title(ctx, audience), "text": f"{_title(ctx, audience)}\n{'-' * min(len(_title(ctx, audience)), 60)}\n{body}", "tsv": buf.getvalue(), "link": ctx["link"]}


def available(kind: str, ref: str) -> Dict[str, Any]:
    """What can be done with this one: each action with the request that does it, who it can go to and what can be attached."""
    ctx = context(kind, ref)
    base = f"/api/v1/actions/{kind}/{ctx['ref']}"
    audience = ctx["default_audience"]
    recipients = [{"audience": a, "email": e} for a, e in ctx["to"].items() if e]
    attachments = [{"type": f["type"], "id": f["id"], "name": f["name"], "label": _ATTACH_LABEL[f["type"]], "audiences": sorted(ATTACH_FOR[f["type"]])} for f in ctx["files"]]
    acts = [{"action": "copy_text", "label": "Copy as text", "method": "GET", "path": base + "/text"},
            {"action": "copy_tsv", "label": "Copy for a spreadsheet", "method": "GET", "path": base + "/text", "field": "tsv"},
            {"action": "email_draft", "label": "Draft an email", "method": "POST", "path": base + "/email-draft", "default_audience": audience, "recipients": recipients, "attachments": attachments}]
    if ctx.get("thread"):
        acts += [{"action": "share", "label": "Comment and tell someone", "method": "POST", "path": base + "/share"},
                 {"action": "hand_ball", "label": "Hand the ball", "method": "POST", "path": base + "/handoff"}]
    elif kind == "row":
        acts.append({"action": "share", "label": "Comment (staged for monday)", "method": "POST", "path": base + "/share"})
    return {"kind": kind, "ref": ref, "title": ctx["title"], "actions": acts, "link": ctx["link"]}


# ----------------------------------------------------------------------------- the email draft

_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]{2,}", re.ASCII)


def _spec_ok(spec: Any, audience: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(spec, dict) or spec.get("type") not in ATTACH_FOR:
        raise ActionError("An attachment must say its type: " + ", ".join(ATTACH_FOR) + ".")
    if audience not in ATTACH_FOR[spec["type"]]:
        raise ActionError(f"A {_ATTACH_LABEL[spec['type']].lower()} can't go to a {audience} (it holds what that person is not meant to see).")
    hit = next((f for f in ctx["files"] if f["type"] == spec["type"] and str(f["id"]) == str(spec.get("id"))), None)
    if hit is None:
        raise ActionError(f"That {_ATTACH_LABEL[spec['type']].lower()} is not one of this item's files.")
    if hit["type"] == "design_file" and audience == "vendor":
        from app.services import redaction

        blocked = redaction.vendor_block(hit["id"])
        if blocked:
            raise ActionError(blocked)
    if hit["type"] == "drawing_overlay" and audience != "internal":
        from app.services import drawing_files

        if not drawing_files.both_design_files_of_one_item(str(hit["id"])):
            raise ActionError("A drawing comparison can go to a vendor only when both drawings are marked as design files of the same job.")
    return {"type": hit["type"], "id": hit["id"], "name": hit["name"]}


def email_draft(kind: str, ref: str, user: Dict[str, Any], to: str = "", subject: str = "", body: str = "", audience: str = "", attach: Optional[List[Dict[str, Any]]] = None,
                include_summary: bool = True) -> Dict[str, Any]:
    """A draft email in the approval queue. To, subject and body are filled in if not given (the recipient from the item: the vendor of a PO, the customer
    of a job). Nothing is sent: a person opens the draft, can edit it, and confirms."""
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    ctx = context(kind, ref)
    aud = audience or ctx["default_audience"]
    if aud not in AUDIENCES:
        raise ActionError(f"The audience must be one of: {', '.join(AUDIENCES)}.")
    recipient = _plain(to) or (user.get("email", "") if aud == "internal" else next((e for a, e in ctx["to"].items() if e and a == aud), ""))     # an internal draft never defaults to a vendor or customer
    addrs = [a.strip() for a in re.split(r"[;,]", recipient) if a.strip()]
    if aud == "internal":
        domain = "@" + str(settings.EMAIL_FROM).rsplit("@", 1)[-1].casefold()
        outside = [a for a in addrs if not a.casefold().endswith(domain)]
        if outside:
            raise ActionError(f"An internal draft can only go to Allied addresses ({domain}), not {outside[0]}. For a vendor or customer, choose that audience so the blind-shipment check runs.")
    if not _plain(to) and aud == "customer":                  # a draft is never quietly addressed to an accounts address: say so, and let a person give the right one
        from app.services import contacts as _contacts

        accounts = [a for a in addrs if _contacts.purpose_of(a) == "invoices"]
        if accounts:
            raise ActionError(f"No quote contact on file: the only address known is an accounts address ({accounts[0]}). Give the address to send this to.")
    if not addrs or not all(_EMAIL.fullmatch(a) for a in addrs):
        raise ActionError("Say who it goes to (an email address): none is on file for this one.")
    if len(addrs) > 10:
        raise ActionError("At most ten recipients.")
    specs = [_spec_ok(s, aud, ctx) for s in (attach or [])]
    if len(specs) > 10:
        raise ActionError("At most ten attachments.")
    if not _plain(subject):
        subject = {"po": f"Purchase order {ctx['title'].split(' · ')[0].removeprefix('PO ')} - Allied Welding Systems", "job": f"{_title(ctx, aud)} - update"}.get(kind, _title(ctx, aud))[:200]
    pairs = _lines(ctx, aud, user.get("role") in ("MANAGER", "ADMIN"))
    text_body = str(body or "").strip()
    if include_summary and pairs and not text_body:
        text_body = "Hello,\n\n" + "\n".join(f"{k}: {v}" for k, v in pairs) + "\n\nThank you,\nAllied Welding Systems"
    elif include_summary and pairs and body:
        text_body = text_body + "\n\n" + "\n".join(f"{k}: {v}" for k, v in pairs)
    if not text_body:
        raise ActionError("Write the message.")
    if len(text_body) > 8000 or len(_plain(subject)) > 250:
        raise ActionError("That message is too long.")
    args = {"to": ", ".join(addrs), "subject": _plain(subject), "body": text_body, "_audience": aud, "_attach": specs, "_attachments": [{"name": s["name"], "size": None} for s in specs],
            "_origin": {"kind": kind, "ref": ctx["ref"], "thread": list(ctx["thread"]) if ctx.get("thread") else None}}
    args["_job_id"] = ctx.get("job_id")
    if aud != "internal":
        from app.services.coordination import blind

        problems = (blind.check("send_vendor_po", {**args, "to": ", ".join(addrs)}) if aud == "vendor"        # a vendor never sees the customer
                    else blind.check("send_email", {**args, "to": ", ".join(addrs)}) if aud == "customer" else [])         # a customer never sees a vendor
        so = ctx.get("so_number")
        if so and so in " ".join([args["subject"], text_body] + [s["name"] for s in specs]):
            problems.append("This draft carries Allied's own sales order number, which nobody outside Allied may see.")
        if problems:
            raise ActionError(" ".join(problems))
    action = agent._propose(TOOLS["send_email"], args, user)
    _thread_note(args["_origin"], f"Email draft to {args['to'][:80]} is waiting for approval: {args['subject'][:80]}" + (f" ({len(specs)} attachment(s))" if specs else ""), user)
    audit_log("ACTION_EMAIL_DRAFTED", "assistant_action", str(action["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"{kind} {ctx['ref']} to {args['to'][:80]} ({aud}); {len(specs)} attachment(s)")
    return {"drafted": True, "action_id": action["id"], "status": "PENDING", "to": addrs, "subject": args["subject"], "audience": aud, "attachments": [s["name"] for s in specs],
            "message": "Draft saved for approval. Nothing was sent.", "summary": action["summary"]}


def free_email_draft(user: Dict[str, Any], to: str, subject: str, body: str, audience: str = "internal", link_kind: str = "", link_ref: str = "") -> Dict[str, Any]:
    """A draft email not tied to a record, waiting in the same approval queue as every other draft. Nothing is sent: a person opens it, may edit it, and confirms. With link_kind and link_ref it is
    the record's own draft (recipient, attachments and checks as for /actions/{kind}/{ref}/email-draft). Without them the audience decides the checks: an internal draft may go only to Allied
    addresses; a vendor draft never carries the customer and a customer draft never carries a vendor or our sales order number."""
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    if link_kind or link_ref:
        if not (link_kind and link_ref):
            raise ActionError("Give both the kind and the reference of the record, or neither.")
        return email_draft(link_kind, link_ref, user, to=to, subject=subject, body=body, audience=audience, include_summary=False)
    aud = audience or "internal"
    if aud not in AUDIENCES:
        raise ActionError(f"The audience must be one of: {', '.join(AUDIENCES)}.")
    addrs = [a.strip() for a in re.split(r"[;,]", _plain(to)) if a.strip()]
    if not addrs or not all(_EMAIL.fullmatch(a) for a in addrs):
        raise ActionError("Say who it goes to (an email address).")
    if len(addrs) > 10:
        raise ActionError("At most ten recipients.")
    if aud == "internal":
        domain = "@" + str(settings.EMAIL_FROM).rsplit("@", 1)[-1].casefold()
        outside = [a for a in addrs if not a.casefold().endswith(domain)]
        if outside:
            raise ActionError(f"An internal draft can only go to Allied addresses ({domain}), not {outside[0]}. For a vendor or customer, choose that audience so the blind-shipment check runs.")
    text_body, subj = str(body or "").strip(), _plain(subject)
    if not text_body:
        raise ActionError("Write the message.")
    if not subj:
        raise ActionError("Give the draft a subject.")
    if len(text_body) > 8000 or len(subj) > 250:
        raise ActionError("That message is too long.")
    args = {"to": ", ".join(addrs), "subject": subj, "body": text_body, "_audience": aud, "_attach": [], "_attachments": [], "_origin": None}
    if aud != "internal":
        from app.services.coordination import blind

        problems = (blind.check("send_vendor_po", dict(args)) if aud == "vendor" else blind.check("send_email", dict(args)) if aud == "customer" else [])
        if re.search(r"\b(?:SO|sales order)[\s#:-]*\d{4,}\b", subj + " " + text_body, re.I):
            problems.append("This draft carries what looks like Allied's own sales order number, which nobody outside Allied may see.")
        if problems:
            raise ActionError(" ".join(problems))
    action = agent._propose(TOOLS["send_email"], args, user)
    audit_log("ACTION_EMAIL_DRAFTED", "assistant_action", str(action["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"free draft to {args['to'][:80]} ({aud})")
    return {"drafted": True, "action_id": action["id"], "status": "PENDING", "to": addrs, "subject": subj, "audience": aud, "attachments": [],
            "message": "Draft saved for approval. Nothing was sent.", "summary": action["summary"]}


def resolve_attachments(specs: Any, audience: str, user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The files for an approved draft, built now (the PO as it stands), and only the kinds the audience may have."""
    from app.services import import_paperwork
    from app.services.coordination import jobs, purchase_orders

    out: List[Dict[str, Any]] = []
    for spec in (specs or []):
        if not isinstance(spec, dict) or spec.get("type") not in ATTACH_FOR or audience not in ATTACH_FOR[spec["type"]]:
            raise ActionError("This draft carries an attachment its audience may not have. It was not sent.")
        t, i = spec["type"], spec.get("id")
        if t == "po_pdf":
            po = purchase_orders.get(int(i))
            out.append({"filename": f"PO_{po['po_number'] or po['id']}.pdf", "content": purchase_orders.pdf(int(i)), "mime": "application/pdf"})
        elif t == "design_file":
            got = purchase_orders.attachments([int(i)], strict=True)
            job_files = {f["id"] for j in _jobs_with_file(int(i)) for f in j["design"]["files"] if f.get("design")}
            if int(i) not in job_files:
                raise ActionError("A drawing on this draft is no longer marked as a design file. It was not sent.")
            out += got
        elif t == "commercial_invoice":
            out.append({"filename": f"CI_{i}.pdf", "content": import_paperwork.commercial_invoice_pdf(import_paperwork.build(int(i))), "mime": "application/pdf"})
        elif t == "packing_list":
            out.append({"filename": f"PL_{i}.pdf", "content": import_paperwork.packing_list_pdf(import_paperwork.build(int(i))), "mime": "application/pdf"})
        elif t == "packing_slip":
            out.append(_packing_slip(str(i)))
        elif t == "board_export":
            out.append(_row_csv(str(i)))
        elif t == "payment_doc":
            from app.services import vendor_payments

            try:
                out.append(vendor_payments.payment_doc_bytes(int(i)))
            except Exception as exc:  # noqa: BLE001
                raise ActionError(str(getattr(exc, "message", exc)))
        elif t == "po_file":
            from app.services import vendor_payments

            try:
                out.append(vendor_payments.po_file_bytes(int(i), int(spec.get("po"))))
            except Exception as exc:  # noqa: BLE001
                raise ActionError(str(getattr(exc, "message", exc)))
        elif t == "drawing_overlay":
            from app.services import drawing_files

            if audience != "internal" and not drawing_files.both_design_files_of_one_item(str(i)):
                raise ActionError("A drawing in this comparison is no longer marked as a design file. It was not sent.")
            out.append({"filename": "drawing_comparison.png", "content": drawing_files.overlay_for_pair(str(i)), "mime": "image/png"})
    return out


def _jobs_with_file(file_id: int) -> List[Dict[str, Any]]:
    from app.models.db_models import ItemFileDB, JobDB, RfqDB
    from app.services.coordination import jobs

    with SessionLocal() as db:
        f = db.get(ItemFileDB, file_id)
        if f is None or f.item_type != "rfq":
            return []
        ids = [j.id for j in db.query(JobDB).join(RfqDB, JobDB.rfq_id == RfqDB.id).filter(RfqDB.number == f.item_ref)]
    return [jobs.get(j, staff=True) for j in ids]


def _packing_slip(so: str) -> Dict[str, Any]:
    from pathlib import Path

    from app.config import settings
    from app.services.database_service import DatabaseService

    with SessionLocal() as db:
        o = DatabaseService.get_order_by_so(db, so)
    path = Path(str((o or {}).get("packing_slip_path") or ""))
    root = Path(settings.OUTPUT_DIR).resolve()
    if not o or not path.is_file() or root not in path.resolve().parents:
        raise ActionError("The packing slip is not made yet, so it was not attached.")
    return {"filename": path.name, "content": path.read_bytes(), "mime": "application/pdf"}


def _row_csv(ref: str) -> Dict[str, Any]:
    ctx = _board_row(ref)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for label, value, _aud in ctx["rows"]:
        if "internal" not in _aud:
            continue
        w.writerow([label, "'" + value if value[:1] in "=+-@" else value])
    return {"filename": "board_row.csv", "content": buf.getvalue().encode("utf-8"), "mime": "text/csv"}


# ----------------------------------------------------------------------------- the item's thread: drafts, comments and the ball

def _thread_note(origin: Any, line: str, user: Dict[str, Any]) -> None:
    """A line in the item's comment thread about what happened to a draft. The thread is a convenience: a failure to write it never touches the mail."""
    from app.core.logging import logger
    from app.services.coordination import updates

    try:
        t = (origin or {}).get("thread")
        if t:
            updates.post(t[0], t[1], line, user)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not note a draft in the item's thread: {type(exc).__name__}")


def note_sent(origin: Any, to: str, subject: str, status: str, user: Dict[str, Any]) -> None:
    word = {"SENT": "sent", "DRAFTED": "saved as a draft (mail is off)", "REDIRECTED": "sent to the test address"}.get(str(status).upper(), str(status or "recorded").lower())
    _thread_note(origin, f"Email to {to[:80]} {word}: {subject[:80]}", user)


def note_cancelled(origin: Any, user: Dict[str, Any]) -> None:
    _thread_note(origin, "An email draft was cancelled and not sent.", user)


def share(kind: str, ref: str, body: str, mentions: Optional[List[str]], user: Dict[str, Any]) -> Dict[str, Any]:
    """A comment on the item with an @mention and a link to it. On a board row it is a comment staged for monday (monday itself is not touched)."""
    from app.services import monday_push
    from app.services.coordination import updates

    ctx = context(kind, ref)
    text = str(body or "").strip()
    if not text:
        raise ActionError("Write the comment first.")
    if mentions is not None and (not isinstance(mentions, list) or not all(isinstance(m, str) for m in mentions)):
        raise ActionError("The people to tell must be a list of email addresses.")
    full = f"{text}\n{ctx['link']}"
    if ctx.get("thread"):
        posted = updates.post(ctx["thread"][0], ctx["thread"][1], full, user, mentions=mentions or None)
        staged = None
        if kind == "row":                                       # the same words are staged as a monday comment too (nothing is sent until a manager pushes)
            from app.services import board_edit

            staged = board_edit.comment_row(ctx["board"][0], ctx["board"][1], full, user)["change_id"]
        return {"posted": True, "staged": staged is not None, "change_id": staged, "comment_id": posted.get("id"), "mentioned": [m.get("email") if isinstance(m, dict) else m for m in (posted.get("mentions") or [])],
                "unresolved": posted.get("unresolved") or [], "message": "Comment posted."}
    if kind == "row":
        from app.services import board_edit

        board_id, item_id = ctx["board"]
        cid = board_edit.comment_row(board_id, item_id, full, user)["change_id"]
        return {"posted": False, "staged": True, "change_id": cid, "message": "Comment staged for monday (nothing is sent until a manager pushes)."}
    raise ActionError("This one has no comment thread.")


def handoff(kind: str, ref: str, user: Dict[str, Any], to: str = "", ball_with: str = "", note: str = "", due: str = "", comment: str = "") -> Dict[str, Any]:
    """Hand the ball in one call: who has it now (a person here, or a vendor, the customer or a carrier by name), with a note and a due date. The thread
    gets the line and the person is alerted; `comment` adds your own words with an @mention."""
    from app.services.coordination import ball, updates

    ctx = context(kind, ref)
    if not ctx.get("thread"):
        raise ActionError("The ball can't be handed on this one.")
    person = str(to or "").strip()
    who = str(ball_with or "").strip() or ("us" if "@" in person else "")
    if not who:
        raise ActionError("Say whose turn it is, or give the person's email.")
    view = ball.set_ball(ctx["thread"][0], ctx["thread"][1], user, ball_with=who, ball_person=person or None, note=note or None, due=due or None)
    posted = None
    if str(comment or "").strip():
        posted = updates.post(ctx["thread"][0], ctx["thread"][1], str(comment).strip(), user, mentions=[person] if "@" in person else None)
    audit_log("ACTION_BALL_HANDED", "item_ball", f"{ctx['thread'][0]}:{ctx['thread'][1]}", actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"{kind} {ref} to {person or who}")
    return {"handed": True, "ball": view, "comment_id": (posted or {}).get("id"), "message": f"The ball is with {view.get('ball_name')}."}

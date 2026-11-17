"""What the assistant can look up and what it can propose.

Read tools run straight away, as the signed-in user. Write tools never run
when the model asks: they become a pending action that the user confirms
with a click, and the role is checked again then. Tools are narrow on
purpose (no free-form query or fetch-any-URL tool), and every result is
plain text sized for Groq's free-tier token limits.
"""

import contextvars
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from typing import Any, Callable, Dict, List, Optional

from app.config import settings
from app.core.logging import logger
from app.services.coordination import notify as alerts

ROLE_RANK = {"VIEWER": 0, "OPERATOR": 1, "MANAGER": 2, "ADMIN": 3}

# Cards the tools hand back beside their text, for the screen to draw as buttons: Open Record,
# Print Packing Slip, Edit and Send, Push to Monday. Collected per chat turn.
_cards: "contextvars.ContextVar" = contextvars.ContextVar("assistant_cards", default=None)


def start_cards() -> None:
    _cards.set([])


def add_card(kind: str, title: str, summary: str = "", buttons: Optional[List[Dict[str, Any]]] = None, **extra: Any) -> None:
    """One card for the reply. kind: record, document, draft, proposal, push or info. Each button is
    {label, action: open|download|confirm|cancel|edit|undo|push, href?, action_id?}."""
    cards = _cards.get()
    if cards is not None:
        cards.append({"kind": kind, "title": title[:200], "summary": summary[:600], "buttons": buttons or [], **extra})


def take_cards() -> List[Dict[str, Any]]:
    return list(_cards.get() or [])


@dataclass
class Tool:
    name: str
    description: str
    params: Dict[str, Any]
    run: Callable[[Dict[str, Any], Dict[str, Any]], Any]
    write: bool = False
    min_role: str = "VIEWER"
    connector: str = "allied"
    summarize: Optional[Callable[[Dict[str, Any]], str]] = None
    required: List[str] = field(default_factory=list)
    offered: bool = True  # False: never shown to the model; only screens propose it (it still needs a click)
    check: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None  # run when a change is proposed: raises ValueError/LookupError so the model can ask, not propose a bad change

    def spec(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required},
        }}

    def allowed(self, user: Dict[str, Any]) -> bool:
        return ROLE_RANK.get(user.get("role") or "", -1) >= ROLE_RANK.get(self.min_role, 9)


def _s(desc: str, **extra: Any) -> Dict[str, Any]:
    return {"type": "string", "description": desc, **extra}


def _lines(rows: List[str], empty: str, more: int = 0) -> str:
    if not rows:
        return empty
    return "\n".join(rows) + (f"\n(+{more} more)" if more > 0 else "")


def _local(iso: Optional[str]) -> str:
    if not iso:
        return "-"
    return datetime.fromisoformat(iso).astimezone(alerts.now_local().tzinfo).strftime("%a %d %b %H:%M")


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def schedule_status(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import projects

    data = projects.overview()
    term = (args.get("project") or "").casefold()
    if term:
        found = [p for p in data["projects"] if term in (p["name"] or "").casefold()]
        if not found:
            return f"No project on the board matches '{args['project']}'."
        out = []
        for p in found[:3]:
            out.append(f"Project {p['name']} (due {p.get('due_date') or 'no date'}, owners {', '.join(p['owner_emails']) or 'none'})"
                       + (" PAST DUE" if p["overdue"] else ""))
            for m in p["milestones"][:15]:
                days = m["days"]
                when = "" if days is None else (f", {-days} days late" if days < 0 else f", due in {days} days")
                out.append(f"  - {m['name']}: {m['state'].replace('_', ' ')} (due {m.get('due_date') or '-'}{when})"
                           + (" - design docs missing" if m.get("missing_docs") else ""))
        return "\n".join(out)
    rows = []
    for p in data["projects"]:
        if p.get("done"):
            continue
        late = [m for m in p["milestones"] if m["state"] == "overdue"]
        if late or p["overdue"]:
            rows.append(f"- {p['name']}: " + (", ".join(f"{m['name']} {-m['days']}d late" for m in late[:4]) or "past its due date"))
    c = data["counts"]
    head = f"{c['overdue']} milestones late, {c['due_soon']} due soon, {c['waiting_review']} waiting for review."
    return head + "\n" + _lines(rows[:12], "Nothing is behind schedule.", len(rows) - 12)


def vendor_status(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import vendors

    term = args.get("search") or ""
    jobs = vendors.find(term) if term else [j for j in vendors.overview()["jobs"] if j["state"] not in ("on_track", "no_dates")]
    rows = [
        f"- job {j['id']} {j['vendor']}: {j['description'] or j.get('po_number') or ''}"
        f"{' / ' + j['project_name'] if j.get('project_name') else ''} | {j['state'].replace('_', ' ')}"
        f" | promised {j.get('promised_on') or '-'} (first {j.get('promised_original') or '-'}, slipped {j.get('slips') or 0}x)"
        f" | must ship by {j.get('need_by') or '-'} | expected {j.get('expected') or '-'}"
        + (f" | {'; '.join(j['reasons'])}" if j.get("reasons") else "")
        for j in jobs[:12]
    ]
    return _lines(rows, "No vendor jobs match." if term else "No vendor job is late or at risk.", len(jobs) - 12)


def vendor_scorecard(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import vendors

    cards = vendors.scorecard()
    term = (args.get("vendor") or "").casefold()
    rows = []
    for v in cards.values():
        if term and term not in v["vendor"].casefold():
            continue
        otd = f"{v['otd_promise']:.0%}" if v["otd_promise"] is not None else "no shipped jobs yet"
        rows.append(
            f"- {v['vendor']}: on time vs first promise {otd} (target {settings.VENDOR_OTD_TARGET:.0%}), "
            f"avg {v['avg_days_late']} days late when late, lead time median {v['lead_median'] or '-'} / "
            f"80% within {v['lead_p80'] or '-'} days (quoted {v['quoted_median'] or '-'}), "
            f"{v['shipped']} shipped, {v['open']} open, {v['slips']} date slips"
        )
    return _lines(rows, "No vendor history yet.")


def rfq_status(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import rfqs

    if args.get("rfq"):
        r = rfqs.get(args["rfq"])
        hist = "\n".join(f"  {_local(h['at'])} {h['by']}: {h['change']}" for h in (r.get("history") or [])[-6:])
        return (f"{r['number']} {r['customer']} - {r['subject']}\nStatus {r['status']} ({r['state']}), owner "
                f"{r['owner_email'] or 'none'}, complexity {r['complexity']}, received {_local(r['received_at'])}, "
                f"quote due {_local(r['effective_due_at'])}, vendor {r.get('vendor') or '-'}, quote "
                f"{r.get('quote_number') or '-'} {r.get('quote_amount') or ''}\n"
                f"Summary: {r.get('summary') or '-'}\nHistory:\n{hist}")
    rows = rfqs.list_rfqs(open_only=True, owner=user.get("email") if args.get("only_mine") else None)
    m = rfqs.metrics()
    head = (f"{m['open']} open RFQs, {m['overdue']} overdue. Last 90 days: median quote time "
            f"{m['turnaround_median_days'] if m['turnaround_median_days'] is not None else '-'} business days, "
            f"on time {m['on_time_pct']:.0%}" if m["on_time_pct"] is not None else f"{m['open']} open RFQs, {m['overdue']} overdue.")
    lines = [f"- {r['number']} {r['customer'] or r['subject']}: {r['status']} / {r['state'].replace('_', ' ')}, due "
             f"{_local(r['effective_due_at'])}, owner {r['owner_email'] or 'none'}" for r in rows[:15]]
    return head + "\n" + _lines(lines, "No open RFQs.", len(rows) - 15)


def shipment_status(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import shipments

    term = (args.get("search") or "").casefold()
    rows = shipments.list_shipments(include_finished=bool(term))
    if term:
        rows = [s for s in rows if any(term in str(s.get(k) or "").casefold()
                                       for k in ("container_no", "awb", "reference", "forwarder", "origin", "destination"))]
    else:
        rows = [s for s in rows if s["flags"]]
    out = [f"- {shipments._label(s)}: {s['status']}, ETA {s.get('eta_current') or '-'}"
           + (f" | {', '.join(s['flags'])}" if s["flags"] else "") for s in rows[:12]]
    return _lines(out, "No shipments match." if term else "No shipment needs attention.", len(rows) - 12)


def my_alerts(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    items = alerts.inbox(user.get("email"), open_only=True, limit=15)
    return _lines([f"- [{n['severity']}] {n['title']}" for n in items], "You have no open alerts.")


def automation_status(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    """What runs on its own: timed plays, event reactions, what happened lately, drafts waiting for this user."""
    from app.services.coordination import autopilot, events

    ap, ev = autopilot.status(), events.status()
    lines = [f"Autopilot {'on' if ap['enabled'] else 'OFF'}; every {ap['every_seconds'] // 60} min. "
             f"{ap['drafts_waiting']} draft(s) waiting for someone to confirm."]
    lines += [f"- play {p['name']} [{p['mode']}]: {p['about']}"
              + (f" Last run: {len(p['last']['did'])} action(s)." if p.get("last") else "") for p in ap["plays"]]
    lines += [f"- when {r['event']}: {r['name']} [{r['mode']}]: {r['about']}" for r in ev["reactions"]]
    for e in ev["recent"][:8]:
        acts = "; ".join(f"{d['outcome']}: {d['what']}" for r in e["reactions"] for d in r.get("did", []))
        lines.append(f"- {e['at'][:16]} {e['event']} {e['summary']}" + (f" -> {acts}" if acts else ""))
    from app.services.assistant.agent import pending

    mine = [p for p in pending(user) if p.get("from") == "autopilot"]
    lines += [f"- waiting for you: {p['summary']}" for p in mine[:10]]
    return "\n".join(lines)


def run_autopilot(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import autopilot

    out = autopilot.run(args.get("play") or None)
    did = [d for e in out["plays"].values() for d in e["did"]]
    return {"done": f"Autopilot ran: {len(did)} action(s)" + (": " + "; ".join(f"{d['outcome']} {d['what']}" for d in did[:6]) if did else "."),
            "link": "/automation"}


def _chat_action(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.api.v1.endpoints.rag_chat import run_action

    out = run_action(args["action_type"], dict(args.get("payload") or {}), user)
    return {"done": f"{args['action_type'].replace('_', ' ').title()}: done.", "data": out.get("data", out)}


_CHAT_ACTION_WORDS = {
    "EXECUTE_END_TO_END_ORDER": "Raise a Sage sales order and its vendor POs",
    "EXECUTE_PACKING_LIST_DISPATCH": "Dispatch an order (packing list, carrier booking)",
    "RESOLVE_EXCEPTION": "Resolve a review-queue item",
    "EXECUTE_INGEST_RFQ_EMAIL": "Process an email as an incoming order or RFQ",
    "EXECUTE_CSV_IMPORT": "Import a CSV into the master data",
    "EXECUTE_CRM_FORM_REQUEST": "Email a lead a form request",
    "EXECUTE_THREE_WAY_MATCH": "Match a vendor invoice against its PO and receipt",
    "EXECUTE_WEAR_SCAN": "Scan customer equipment for wear and send alerts",
}


def _chat_action_summary(a: Dict[str, Any]) -> str:
    payload = json.dumps(a.get("payload") or {}, indent=1, default=str)
    return f"{_CHAT_ACTION_WORDS.get(a.get('action_type'), a.get('action_type'))}\n{payload[:4000]}"


def find_orders(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.core.database import SessionLocal
    from app.services.database_service import DatabaseService

    term = (args.get("search") or "").casefold()
    with SessionLocal() as db:
        rows = DatabaseService.get_all_orders(db, limit=500)
    hits = [o for o in rows if not term or any(term in str(o.get(k) or "").casefold()
                                                for k in ("customer_name", "customer_po_number", "sales_order_number", "order_id"))]
    staff = user.get("role") in ("OPERATOR", "MANAGER", "ADMIN")        # Allied's own sales order number is internal: a viewer sees the order id instead
    out = [f"- {(o.get('sales_order_number') if staff else None) or o['order_id']} {o.get('customer_name')}: PO {o.get('customer_po_number') or '-'}, "
           f"status {o.get('status') or '-'}, created {str(o.get('created_at') or '')[:10]}" for o in hits[:10]]
    return _lines(out, "No orders match.", len(hits) - 10)


def search_knowledge(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.models.rag_chat import RAGQueryRequest
    from app.services.rag_service import rag_service

    answer = rag_service.answer_query(RAGQueryRequest(query=args.get("question") or "", dev_bypass=True))
    cites = ", ".join(c.source_id for c in answer.citations[:5])
    return f"{answer.answer}\nSources: {cites or 'none'}"


def sage_lookup(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.core.container import get_entity_matcher
    from app.services.sage_odbc import SageODBCReader

    reader = SageODBCReader(get_entity_matcher())
    kind, ident = args.get("kind"), (args.get("id") or "").strip()
    found = {"customer": reader.get_customer, "vendor": reader.get_vendor, "item": reader.get_inventory_item}.get(kind, lambda _: None)(ident)
    if found is None:
        return f"Sage has no {kind} '{ident}'."
    data = found.model_dump() if hasattr(found, "model_dump") else dict(found)
    return "\n".join(f"{k}: {v}" for k, v in data.items() if v not in (None, "", [], {}))


# ---------------------------------------------------------------------------
# Write tools (each becomes a confirmation card)
# ---------------------------------------------------------------------------


def log_rfq(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import rfqs

    r = rfqs.create({k: args.get(k) for k in ("customer", "contact_email", "subject", "summary", "complexity", "owner_email")}
                    | {"complexity": (args.get("complexity") or "STANDARD").upper()}, user, source="assistant")
    return {"done": f"Logged {r['number']}; quote due {_local(r['due_at'])}.", "link": f"/rfqs?id={r['id']}"}


def update_rfq(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import rfqs

    changes = {k: v for k, v in args.items() if k != "rfq" and v not in (None, "")}
    r = rfqs.update(args["rfq"], changes, user)
    return {"done": f"{r['number']} is now {r['status']}.", "link": f"/rfqs?id={r['id']}"}


def update_vendor_job(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import vendors

    changes = {k: v for k, v in args.items() if k != "job_id" and v not in (None, "")}
    j = vendors.update(int(args["job_id"]), changes, user)
    return {"done": f"Updated {j['vendor']} job {j['id']}.", "link": f"/vendors?job={j['id']}"}


def send_email(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.outbound_email_service import outbound_email_service

    to = [a.strip() for a in str(args.get("to") or "").replace(";", ",").split(",") if a.strip()]
    if not to or not all("@" in a for a in to):
        raise ValueError("Give at least one email address.")
    who = "" if user.get("role") == "ADMIN" and user.get("email", "").startswith("autopilot@") else f"Sent by {user.get('email')} from "
    body = f"{args.get('body') or ''}\n\n-- \n{who}Allied Operations"
    entry = outbound_email_service.send(to, args.get("subject") or "(no subject)", body, category="ASSISTANT",
                                        reference=user.get("email"))
    return {"done": f"Email {entry.get('status', 'recorded').lower()} to {', '.join(to)}.", "status": entry.get("status")}


def post_monday_update(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.core.container import get_monday_client
    from app.services import monday_push

    client = get_monday_client()
    text = f"{args.get('text')}\n\n({user.get('email')}, via Allied Operations)"
    if not client.live:
        return {"done": "monday isn't connected (simulation), so nothing was posted or staged."}
    cid = monday_push.stage("post_update", "", int(args["item_id"]), {"text": text}, f"Post a comment on item {args['item_id']}: {str(args.get('text'))[:80]}",
                            by=user.get("email", "app"), source="assistant")
    if settings.MONDAY_PUSH_MODE == "auto" and not settings.MONDAY_READ_ONLY and user.get("role") in ("MANAGER", "ADMIN"):      # only a manager's confirm can push
        out = monday_push.push([cid], who=user.get("email", ""))
        return {"done": out["message"], "change_id": cid}
    return {"done": f"Staged (not sent yet): the comment is saved as change #{cid} and goes to monday when you push. "
                    + ("monday is read-only right now." if settings.MONDAY_READ_ONLY else ""), "change_id": cid}


def design_hold(args: Dict[str, Any], user: Dict[str, Any]) -> None:
    """A PO drafted from an RFQ waits for a manager's design approval. Raised before the draft is claimed, so it stays waiting."""
    if str(args.get("po_number") or "").strip() or not args.get("rfq_number"):
        return
    from app.services.coordination import jobs

    job = jobs.from_rfq(str(args["rfq_number"]), user)
    if not jobs.design_approved(job["id"]):
        raise ValueError(f"The design for {job['ref']} is not approved yet. A manager approves it on the job first; this purchase order stays waiting.")


def send_vendor_po(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    return _send_vendor_po(args, user)          # the PO claims itself for sending (begin_send), so two approvals at once cannot send twice


def _send_vendor_po(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Sends a reviewed purchase order email to a vendor (only ever from the review queue, and only by a manager). A PO drafted from an RFQ has
    no number yet: it is numbered and approved now, once. The PDF is built from the saved record, so it is the one that was previewed, and a
    PO that was already sent is never sent again."""
    from app.services import order_documents
    from app.services.coordination import purchase_orders
    from app.services.coordination.jobs import same_vendor_name
    from app.services.outbound_email_service import outbound_email_service

    po = str(args.get("po_number") or "").strip()
    po_id = args.get("po_id")
    if not po:
        if not args.get("rfq_number"):
            raise ValueError("This purchase order has no number and no RFQ to take one for.")
        made = purchase_orders.number_for_rfq(str(args["rfq_number"]), str(args.get("vendor_name") or ""), user)
        po, po_id = made["po_number"], made["id"]
    if not po_id and po:                                  # a draft with a number but no record id: find the record by its number
        have = purchase_orders.by_number(po)
        if have:
            po_id = have["id"]
    if po_id:
        rec = purchase_orders.get(int(po_id))
        if rec["status"] in ("SENT", "ACKNOWLEDGED"):
            raise ValueError(f"Purchase order {rec['po_number'] or po} has already been sent to the vendor.")
        if str(po) and rec["po_number"] and str(po) != str(rec["po_number"]):
            raise ValueError(f"This draft says purchase order {po} but the record is {rec['po_number']}. It was not sent.")
        if args.get("vendor_name") and rec["vendor"] and not same_vendor_name(str(args["vendor_name"]), rec["vendor"]):
            raise ValueError(f"This draft is addressed to {args['vendor_name']} but the purchase order is for {rec['vendor']}. It was not sent.")
        po, args = str(rec["po_number"] or po), {**args, "vendor_name": rec["vendor"]}      # the record decides the number and the vendor
    body = str(args.get("body") or "").replace("{po_number}", po)
    ids = list(args.get("attach_file_ids") or [])
    if po_id and ids:
        from app.services.coordination import jobs

        job = jobs.get(purchase_orders.get(int(po_id))["job_id"])
        allowed = {f["id"] for f in job["design"]["files"] if f.get("design")}
        if any(i not in allowed for i in ids):
            raise ValueError("This draft attaches a file that is not a design file of this job. Only files marked as design files can go to a vendor.")
    extra = purchase_orders.attachments(ids, strict=True)
    token = purchase_orders.begin_send(int(po_id)) if po_id else None          # claimed first: from here no edit can change what the PDF says
    try:
        if po_id:
            # The address is read AFTER the claim (a manager's correction cannot slip in between) and must be the one the reviewer saw on the card.
            on_file = str(purchase_orders.get(int(po_id)).get("vendor_email") or "").strip()
            drafted = str(args.get("to") or "").strip()
            if on_file and drafted.lower() != on_file.lower():
                raise ValueError(f"This draft goes to {drafted or 'no address'} but the purchase order's email is {on_file}. It was not sent; draft it again so the right address is shown.")
            if not on_file:
                args = {**args, "to": purchase_orders._one_email(drafted)}
                if not args["to"]:
                    raise ValueError("This purchase order has no vendor email. Add it to the purchase order first.")
            else:
                args = {**args, "to": on_file}
            pdf = purchase_orders.pdf(int(po_id))
        else:   # a PO that came from an order (no record of its own): built from the draft as reviewed
            purchase_orders.check_lines_limit(order_documents.po_lines(args.get("lines") or []), user)          # the approval limit holds here too
            pdf = order_documents.vendor_po_pdf({
                "po_number": po, "vendor_name": args.get("vendor_name") or "", "terms": args.get("terms") or "", "ship_via": args.get("ship_via") or "",
                "good_thru": args.get("good_thru") or "", "lines": order_documents.po_lines(args.get("lines") or [])})
        if not po_id:                                      # a PO built from an order has no record to take its address from: one valid address only
            args = {**args, "to": purchase_orders._one_email(args.get("to"))}
            if not args["to"]:
                raise ValueError("Give the vendor's email address.")
        entry = outbound_email_service.send_purchase_order(vendor_name=str(args.get("vendor_name") or ""), vendor_email=str(args["to"]), po_number=po,
                                                           body=body, pdf_bytes=pdf, extra_attachments=extra)
    except Exception:
        if po_id:
            purchase_orders.finish_send(int(po_id), False, token)
        raise
    went = entry.get("status") == "SENT" and not entry.get("redirected")       # a pilot mail redirected to the test inbox did not reach the vendor
    if po_id:
        purchase_orders.finish_send(int(po_id), went, token)          # SENT only when the mail really went (not when it was only recorded as a draft)
    note = "" if went else (" The mail went to the pilot test inbox, not the vendor, so the purchase order is not marked sent." if entry.get("redirected")
                            else " Mail did not go out (it was only recorded), so the purchase order is not marked sent.")
    return {"done": f"Purchase order {po} {entry.get('status', 'recorded').lower()} to {args['to']}.{note}", "status": entry.get("status"), "po_number": po}


def send_vendor_price_request(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Sends a reviewed price request to a vendor with the chosen drawings attached (only ever from the review queue)."""
    from app.services.coordination import purchase_orders
    from app.services.outbound_email_service import outbound_email_service

    from app.services.coordination import files as item_files

    allowed = purchase_orders.design_file_ids(str(args["rfq_number"])) if args.get("rfq_number") else set()
    ids = list(args.get("attach_file_ids") or [])
    if any(i not in allowed for i in ids):          # editing a draft cannot attach a file that is not a design file of this RFQ
        raise ValueError("This draft attaches a file that is not a design file of this RFQ. Only files marked as design files can go to a vendor.")
    entry = outbound_email_service.send(to=str(args["to"]), subject=str(args.get("subject") or "Price request"), body=str(args.get("body") or ""),
                                        category="PRICE_REQUEST", reference=str(args.get("rfq_number") or "") or None,
                                        attachments=purchase_orders.attachments(ids, strict=True) or None)
    return {"done": f"Price request {entry.get('status', 'recorded').lower()} to {args['to']}.", "status": entry.get("status")}


def add_rfq_to_monday(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Approves the reviewed row for the Open RFQs board: it is STAGED (saved, not sent) and goes to monday when someone pushes.
    With writing on and MONDAY_PUSH_MODE=auto it is sent at once. Nothing is ever sent while monday is read-only."""
    from app.config import settings
    from app.core.container import get_monday_client
    from app.services import monday_push

    board = str(args.get("board_id") or settings.MONDAY_RFQ_BOARD_ID or "")
    row = {k: str(args.get(k) or "") for k in ("item_name", "customer", "status", "notes")}
    as_text = "\n".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in row.items() if v)
    if not board:
        return {"done": f"No Open RFQs board is set up (MONDAY_RFQ_BOARD_ID), so nothing was staged. The row, to add by hand:\n{as_text}"}
    if not get_monday_client().live:
        return {"done": f"monday isn't connected (simulation), so nothing was staged. The row, to add by hand:\n{as_text}"}
    fields = {settings.RFQ_BOARD_CUSTOMER_COLUMN_ID: row["customer"], settings.RFQ_BOARD_STATUS_COLUMN_ID: row["status"],
              settings.RFQ_BOARD_NOTES_COLUMN_ID: row["notes"]}
    cid = monday_push.stage("create_item", board, "", {"item_name": row["item_name"], "fields": fields},
                            f"Add a row to the Open RFQs board: {row['item_name']} ({row['customer'] or 'no customer'})",
                            key=f"rfq-row-{board}-{row['item_name']}", by=user.get("email", "app"), source="rfq_row")
    if settings.MONDAY_PUSH_MODE == "auto" and not settings.MONDAY_READ_ONLY:
        out = monday_push.push([cid], who=user.get("email", ""))
        made = next((r.get("monday_item_id") for r in out.get("results", []) if r.get("id") == cid), None)
        return {"done": out["message"], "change_id": cid, **({"monday_item_id": made} if made else {})}
    why = "monday is read-only right now, so " if settings.MONDAY_READ_ONLY else ""
    return {"done": f"Staged (not sent yet): the row is saved as change #{cid}. {why}it goes to monday when you push.", "change_id": cid}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_DATE = "YYYY-MM-DD"

TOOLS: Dict[str, Tool] = {t.name: t for t in [
    Tool("schedule_status", "Projects and milestones from monday: what is late or due. Give a project name for its milestones.",
         {"project": _s("Part of a project name; blank for everything behind")}, schedule_status),
    Tool("vendor_status", "Vendor jobs (POs) and whether each vendor's date still lets its project finish on time. "
         "Blank search lists the late and at-risk ones.",
         {"search": _s("Vendor, PO, job or project name; blank for problems only")}, vendor_status),
    Tool("vendor_scorecard", "Each vendor's on-time record and real lead times compared with what they quoted.",
         {"vendor": _s("Vendor name; blank for all")}, vendor_scorecard),
    Tool("rfq_status", "Customer requests for quotes: open ones with deadlines, or one RFQ in detail.",
         {"rfq": _s("RFQ number like RFQ-2026-0001; blank for the open list"),
          "only_mine": {"type": "boolean", "description": "Only RFQs I own"}}, rfq_status),
    Tool("shipment_status", "Freight on the way: blank lists problems; or search by container, AWB, forwarder or port.",
         {"search": _s("Container, AWB, booking, forwarder or port")}, shipment_status),
    Tool("my_alerts", "My open alerts.", {}, my_alerts),
    Tool("automation_status", "What Allied's system does on its own: timed plays, reactions to events, what it did "
         "lately, and drafted messages waiting for me to confirm.", {}, automation_status, min_role="MANAGER"),
    Tool("find_orders", "Find sales orders by customer, PO or SO number.",
         {"search": _s("Customer, PO or SO number")}, find_orders, required=["search"]),
    Tool("search_knowledge", "Search Allied's specs, procedures, price lists and past quotes.",
         {"question": _s("What to look up")}, search_knowledge, required=["question"]),
    Tool("sage_lookup", "Look up a customer, vendor or item in Sage 50 by its Sage id.",
         {"kind": _s("customer, vendor or item", enum=["customer", "vendor", "item"]), "id": _s("Sage id")},
         sage_lookup, connector="sage", required=["kind", "id"]),
    Tool("log_rfq", "Log a new customer request for quote (the user confirms first).",
         {"customer": _s("Customer company"), "subject": _s("What they want quoted"), "summary": _s("Details"),
          "contact_email": _s("Customer contact email"), "owner_email": _s("Allied person who will quote it"),
          "complexity": _s("SIMPLE (repeat part), STANDARD, or COMPLEX (new design / vendor pricing)",
                           enum=["SIMPLE", "STANDARD", "COMPLEX"])},
         log_rfq, write=True, min_role="OPERATOR", required=["customer", "subject"],
         summarize=lambda a: (f"Log an RFQ for {a.get('customer')}: {a.get('subject')} ({(a.get('complexity') or 'STANDARD').upper()})"
                              + (f", owner {a['owner_email']}" if a.get("owner_email") else "")
                              + (f"\nContact: {a['contact_email']}" if a.get("contact_email") else "")
                              + (f"\nDetails: {a['summary']}" if a.get("summary") else ""))),
    Tool("update_rfq", "Change an RFQ: status, owner, vendor asked for pricing, quote number/amount, or a note. "
         "LOST and NO_BID need a reason; QUOTE_SENT needs a quote number or amount.",
         {"rfq": _s("RFQ number"), "status": _s("New status", enum=["ACKNOWLEDGED", "CLARIFYING", "WAITING_VENDOR", "PRICING",
                                                                       "QUOTE_SENT", "WON", "LOST", "NO_BID"]),
          "owner_email": _s("New owner"), "vendor": _s("Vendor asked for pricing"), "quote_number": _s("Quote number"),
          "quote_amount": _s("Quote total in USD"), "reason": _s("Why lost / no bid"), "note": _s("Note to add")},
         update_rfq, write=True, min_role="OPERATOR", required=["rfq"],
         summarize=lambda a: f"Update {a.get('rfq')}: " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in a.items()
                                                                     if k != "rfq" and v not in (None, ""))),
    Tool("update_vendor_job", "Record a vendor date on a vendor job: confirmed, new promised ship date, or shipped.",
         {"job_id": {"type": "integer", "description": "Vendor job id"}, "acknowledged_on": _s(_DATE),
          "promised_on": _s(f"New promised ship date {_DATE}"), "shipped_on": _s(f"Actually shipped {_DATE}"),
          "note": _s("What the vendor said")},
         update_vendor_job, write=True, min_role="OPERATOR", required=["job_id"],
         summarize=lambda a: f"Vendor job {a.get('job_id')}: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in a.items()
                                                                            if k != "job_id" and v not in (None, ""))),
    Tool("send_email", "Send an email from Allied (the user sees the exact text and confirms first).",
         {"to": _s("Addresses, comma-separated"), "subject": _s("Subject"), "body": _s("Plain-text body")},
         send_email, write=True, min_role="OPERATOR", connector="email", required=["to", "subject", "body"],
         summarize=lambda a: f"Send email to {a.get('to')}\nSubject: {a.get('subject')}\n\n{a.get('body')}"),
    Tool("run_autopilot", "Run the autopilot now: every play, or one (rfq_board, orders_watch, vendor_chaser, "
         "rfq_vendor_chaser, quote_followup). Messages it makes still wait for a person unless the play is on auto.",
         {"play": _s("One play's name; blank for all")}, run_autopilot, write=True, min_role="ADMIN",
         summarize=lambda a: f"Run the autopilot now ({a.get('play') or 'every play'})"),
    Tool("chat_action", "A change asked for on the Copilot chat screen.", {}, lambda args, user: _chat_action(args, user),
         write=True, min_role="OPERATOR", offered=False,
         summarize=lambda a: _chat_action_summary(a)),
    Tool("send_vendor_price_request", "Email a vendor asking for a price, with the drawings attached. Only ever from the review queue after a person has checked it.",
         {"to": _s("Vendor email"), "vendor_name": _s("Vendor"), "rfq_number": _s("The RFQ it is about"), "subject": _s("Subject"), "body": _s("The message"),
          "attach_file_ids": {"type": "array", "items": {"type": "integer"}, "description": "Drawing files to attach"}},
         send_vendor_price_request, write=True, min_role="OPERATOR", connector="email", required=["to", "body"], offered=False,
         summarize=lambda a: f"Ask {a.get('vendor_name') or a.get('to')} for a price"),
    Tool("send_vendor_po", "Email a purchase order to a vendor. Only ever from the review queue after a person has checked it.",
         {"to": _s("Vendor email"), "vendor_name": _s("Vendor"), "po_number": _s("Allied's PO number; blank for a new one"),
          "rfq_number": _s("The RFQ it was drafted from"), "body": _s("The message"),
          "lines": {"type": "array", "items": {"type": "object"}, "description": "What to make: quantity, item, description, unit_cost"},
          "po_id": {"type": "integer", "description": "The purchase order record"},
          "attach_file_ids": {"type": "array", "items": {"type": "integer"}, "description": "Design files to attach"}, "terms": _s("Payment terms"), "ship_via": _s("Ship via"), "good_thru": _s("Good thru date")},
         send_vendor_po, write=True, min_role="MANAGER", connector="email", required=["to", "body"], offered=False,
         summarize=lambda a: (f"Send purchase order {a.get('po_number')} to {a.get('vendor_name') or a.get('to')}" if a.get("po_number")
                              else f"Send a purchase order to {a.get('vendor_name') or a.get('to')} (number assigned when sent)")),
    Tool("add_rfq_to_monday", "Add a reviewed request-for-quote row to the Open RFQs board on monday. Only ever from the "
         "review queue after a person has checked the row.",
         {"item_name": _s("What is being quoted"), "customer": _s("Customer"), "status": _s("Status label that exists on the board; blank for none"),
          "notes": _s("Details for the Notes column"), "board_id": _s("Board id; blank for the Open RFQs board")},
         add_rfq_to_monday, write=True, min_role="OPERATOR", connector="monday", required=["item_name"], offered=False,
         summarize=lambda a: f"Add to the Open RFQs board: {a.get('item_name')} ({a.get('customer') or 'no customer'})"),
    Tool("post_monday_update", "Push a comment to a monday item. ONLY when the user explicitly asks to push, sync or post "
         "something to monday; never on your own.",
         {"item_id": {"type": "integer", "description": "monday item id"}, "text": _s("Comment text")},
         post_monday_update, write=True, min_role="OPERATOR", connector="monday", required=["item_id", "text"],
         summarize=lambda a: f"Post on monday item {a.get('item_id')}: {a.get('text')}"),
]}


# The rest of the platform (orders, packing slips, MTRs, leads, exceptions, mail, automations, notes, drafts).
from app.services.assistant import platform_tools  # noqa: E402  (after the helpers it uses)

TOOLS.update({t.name: t for t in platform_tools.TOOLS})

from app.services.assistant import board_tools  # noqa: E402

TOOLS.update({t.name: t for t in board_tools.TOOLS})


def all_tools() -> Dict[str, Tool]:
    from app.services.assistant import mcp

    out = dict(TOOLS)
    out.update(mcp.tools())
    return out


# Which tools to show the AI for a conversation. All of them would use most of the free tier's
# token allowance on every question, so a few core tools are always offered and each topic adds
# its own when the user's words touch it; a multi-part task gets several topics together.
_CORE = ("find_orders", "vendor_status", "rfq_status", "shipment_status", "my_alerts", "schedule_status", "save_email_draft")
_TOPICS = [
    (("order", "packing", "slip", "dispatch", "sales order", "so-", "sage", "export"),
     ("order_detail", "packing_slip", "sage_export_preview", "sage_lookup")),
    (("mtr", "heat", "cert", "ncr", "material test"), ("mtr_lookup",)),
    (("lead", "target", "prospect", "outreach"), ("lead_targets",)),
    (("exception", "review queue", "flagged", "stuck", "held", "bill", "invoice", "reconcil", "dispute", "mismatch"),
     ("exception_queue", "reconciliation_recent", "resolve_exception")),
    (("mail", "email", "inbox", "wrote", "reply", "complain"), ("mail_search",)),
    (("automation", "recipe", "rule", "autopilot", "switch on", "switch off", "turn on", "turn off"),
     ("recipes_list", "recipes_preview", "toggle_recipe", "add_starter_recipe", "run_recipes_now", "automation_status", "run_autopilot")),
    (("note", "engineer", "drawing", "scan", "milestone"), ("notes_list", "post_engineering_note")),
    (("audit", "who changed", "history"), ("audit_recent",)),
    (("vendor", "supplier", "po ", "lead time", "kws", "kingcera", "helen"), ("create_vendor_job", "update_vendor_job", "vendor_scorecard")),
    (("shipment", "container", "freight", "eta", "vessel", "forwarder", "tracking"), ("create_shipment", "update_shipment")),
    (("rfq", "quote", "quoting", "request for", "pricing", "priced", "won", "lost", "no bid"), ("log_rfq", "update_rfq")),
    (("spec", "procedure", "weld", "price list", "how do", "how to", "standard"), ("search_knowledge",)),
    (("send", "email", "draft", "write to"), ("send_email",)),
    (("monday", "push", "sync"), ("post_monday_update", "monday_staged", "skip_staged_change")),
]
# Board and job topics match whole words only ("status" or "item" alone would pull the tools into every question about an order).
_WORD_TOPICS = [
    (re.compile(r"\b(?:boards?|rows?|cells?|columns?|staged|set the status|change the status|update the status|add a comment|comment on)\b"),
     ("list_boards", "find_board_items", "stage_cell_edit", "stage_new_row", "stage_comment", "monday_staged", "skip_staged_change")),
    (re.compile(r"\b(?:jobs?|approve|approved|design|quotation|draft po|purchase orders?|sales order|packets?)\b"),
     ("list_jobs", "job_detail", "approve_design", "record_vendor_quote", "draft_po", "approve_po", "create_sales_order")),
]


def for_message(user: Dict[str, Any], text: str) -> Dict[str, Tool]:
    """The tools to offer for this conversation: the core, the topics the words touch, and any
    outside (MCP) tools. With nothing recognised, the core plus the common ones."""
    allowed = for_user(user)
    low = (text or "").casefold()
    names = set(_CORE)
    hit = False
    for words, tools_ in _TOPICS:
        if any(w in low for w in words):
            names |= set(tools_)
            hit = True
    for pattern, tools_ in _WORD_TOPICS:
        if pattern.search(low):
            names |= set(tools_)
            hit = True
    if not hit:
        # Nothing recognised (a short follow-up like "yes, do that"): the commonly used reads and changes.
        names |= {"order_detail", "mail_search", "recipes_list", "log_rfq", "update_rfq", "update_vendor_job", "send_email"}
    return {n: t for n, t in allowed.items() if n in names or n not in TOOLS}


def for_user(user: Dict[str, Any]) -> Dict[str, Tool]:
    """What the model may call for this user (screen-only tools are left out)."""
    return {n: t for n, t in all_tools().items() if t.allowed(user) and t.offered}

"""What the assistant can look up and what it can propose.

Read tools run straight away, as the signed-in user. Write tools never run
when the model asks: they become a pending action that the user confirms
with a click, and the role is checked again then. Tools are narrow on
purpose (no free-form query or fetch-any-URL tool), and every result is
plain text sized for Groq's free-tier token limits.
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any, Callable, Dict, List, Optional

from app.config import settings
from app.services.coordination import notify as alerts

ROLE_RANK = {"VIEWER": 0, "OPERATOR": 1, "MANAGER": 2, "ADMIN": 3}


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
    out = [f"- {o.get('sales_order_number') or o['order_id']} {o.get('customer_name')}: PO {o.get('customer_po_number') or '-'}, "
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

    client = get_monday_client()
    ok = client.post_pulse_update(int(args["item_id"]), f"{args.get('text')}\n\n({user.get('email')}, via Allied Operations)")
    if not client.writable:
        why = "read-only (MONDAY_READ_ONLY)" if client.live else "in simulation"
        return {"done": f"monday is {why}; nothing was posted."}
    return {"done": "Posted to monday." if ok else "monday did not accept the comment."}


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
    Tool("post_monday_update", "Post a comment on a monday item.",
         {"item_id": {"type": "integer", "description": "monday item id"}, "text": _s("Comment text")},
         post_monday_update, write=True, min_role="OPERATOR", connector="monday", required=["item_id", "text"],
         summarize=lambda a: f"Post on monday item {a.get('item_id')}: {a.get('text')}"),
]}


def all_tools() -> Dict[str, Tool]:
    from app.services.assistant import mcp

    out = dict(TOOLS)
    out.update(mcp.tools())
    return out


def for_user(user: Dict[str, Any]) -> Dict[str, Tool]:
    """What the model may call for this user (screen-only tools are left out)."""
    return {n: t for n, t in all_tools().items() if t.allowed(user) and t.offered}

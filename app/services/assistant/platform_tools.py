"""The rest of the platform for the assistant: what a person can do on the screens, it can do.

Reads (orders, packing slips, material certificates, leads, the review queue, mail, bill
matches, the audit trail, notes, automations) run at once. Changes (a vendor job, a shipment,
an engineering note, a review item, an automation) are proposals the user confirms, as they
always have been. Saving an email draft runs at once because it sends nothing: it only puts
a draft in the user's queue, with buttons to edit and send it. Pushing to monday stays the
explicit, confirmed, read-only-guarded post that already existed.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.assistant.tools import Tool, _lines, _s, add_card

_DATE = "YYYY-MM-DD"


def _slip_path(so: str) -> Optional[Path]:
    clean = "".join(c for c in str(so).replace("SO-", "") if c.isalnum() or c in "-_")
    path = (settings.OUTPUT_DIR / "packing_slips" / f"packing_slip_SO_{clean}.pdf").resolve()
    return path if path.is_file() and path.parent == (settings.OUTPUT_DIR / "packing_slips").resolve() else None


def _find_order(term: str) -> List[Dict[str, Any]]:
    from app.core.database import SessionLocal
    from app.services.database_service import DatabaseService

    term = (term or "").strip().casefold()
    with SessionLocal() as db:
        rows = DatabaseService.get_all_orders(db, limit=500)
    return [o for o in rows if term and any(term in str(o.get(k) or "").casefold()
                                            for k in ("customer_name", "customer_po_number", "sales_order_number", "order_id"))]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def order_detail(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    hits = _find_order(args.get("search") or "")
    if not hits:
        return "No order matches."
    out = []
    for o in hits[:3]:
        so = o.get("sales_order_number") or o["order_id"]
        pos = ", ".join(f"{v}: PO {n}" for v, n in (o.get("purchase_orders") or {}).items()) or "none"
        out.append(f"SO {so} for {o.get('customer_name')}: customer PO {o.get('customer_po_number')}, status {o.get('status')}, "
                   f"{len(o.get('line_items') or [])} lines, vendor POs {pos}, created {str(o.get('created_at') or '')[:10]}")
        buttons = [{"label": "Open Record", "action": "open", "href": f"/orders?so={so}"}]
        if _slip_path(so):
            buttons.append({"label": "Print Packing Slip", "action": "download", "href": f"/api/v1/orders/{so}/packing-slip"})
        add_card("record", f"SO {so} · {o.get('customer_name')}", f"PO {o.get('customer_po_number')} · {o.get('status')}", buttons,
                 record={"type": "order", "id": str(so)})
    return "\n".join(out) + (f"\n(+{len(hits) - 3} more)" if len(hits) > 3 else "")


def packing_slip(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    hits = _find_order(args.get("search") or "")
    if not hits:
        return "No order matches."
    o = hits[0]
    so = o.get("sales_order_number") or o["order_id"]
    if not _slip_path(so):
        return f"SO {so} has no packing slip yet. It is made when the order is raised or dispatched."
    add_card("document", f"Packing slip · SO {so}", f"{o.get('customer_name')} (no costs or vendors on it)",
             [{"label": "Print Packing Slip", "action": "download", "href": f"/api/v1/orders/{so}/packing-slip"},
              {"label": "Open Record", "action": "open", "href": f"/orders?so={so}"}], record={"type": "order", "id": str(so)})
    return f"The packing slip for SO {so} is ready; the card has the print button."


def mtr_lookup(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.mtr_service import mtr_service

    rec = mtr_service.get_mtr_by_heat((args.get("heat_number") or "").strip())
    if rec is None:
        return f"No certified MTR on file for heat {args.get('heat_number')}."
    data = rec.model_dump()
    return "\n".join(f"{k}: {v}" for k, v in data.items() if v not in (None, "", [], {}))[:1800]


def lead_targets(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.lead_agent import lead_agent

    term = (args.get("search") or "").casefold()
    rows = []
    for a in lead_agent.get_target_accounts():
        d = a.model_dump()
        line = " | ".join(str(v) for v in list(d.values())[:5] if v not in (None, "", [], {}))
        if not term or term in line.casefold():
            rows.append(f"- {line}")
    return _lines(rows[:10], "No target accounts match.", len(rows) - 10)


def exception_queue(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.exception_queue_service import exception_queue_service

    items = exception_queue_service.list_exceptions(status_filter=(args.get("status") or "OPEN").upper())
    term = (args.get("search") or "").casefold()
    items = [i for i in items if not term or term in f"{i.title} {i.reference_id} {i.vendor_or_customer}".casefold()]
    rows = [f"- {i.exception_id} [{i.severity}] {i.title} ({i.vendor_or_customer}, ref {i.reference_id}); "
            f"actions: {', '.join(a.get('action', '') for a in i.available_actions) or 'resolve by hand'}" for i in items[:10]]
    for i in items[:3]:
        add_card("record", i.title, f"{i.severity} · {i.vendor_or_customer}",
                 [{"label": "Open Record", "action": "open", "href": f"/exceptions?id={i.exception_id}"}],
                 record={"type": "exception", "id": i.exception_id})
    return _lines(rows, "The review queue is clear.", len(items) - 10)


def mail_search(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services import mail_log

    data = mail_log.list_mail(category=args.get("category") or None, needs_action=True if args.get("needs_action") else None,
                              q=args.get("search") or None, days=int(args.get("days") or 14), limit=12,
                              tag=args.get("tag") or None)
    rows = [f"- {m['received_at'][:10] if m['received_at'] else '-'} {m['category']}{' (needs a person)' if m['needs_action'] and not m['handled_at'] else ''}: "
            f"{m['subject']} (from {m['sender']}) {', '.join(m['tags'][:3])}" for m in data["mail"]]
    return f"{data['waiting']} emails waiting on a person.\n" + _lines(rows, "No emails match.")


def reconciliation_recent(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.reconciliation_service import reconciliation_service

    rows = []
    for r in reconciliation_service.get_reconciliation_history(limit=10):
        d = r if isinstance(r, dict) else r.model_dump()
        rows.append(f"- {d.get('invoice_number')} {d.get('vendor_name') or ''}: {d.get('reconciliation_status')}"
                    + (f", over by {d.get('cost_variance_amount')}" if d.get("cost_variance_amount") else ""))
    return _lines(rows, "No bills have been matched yet.")


def audit_recent(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.core.database import SessionLocal
    from app.services.database_service import DatabaseService

    with SessionLocal() as db:
        entries, total = DatabaseService.get_audit_log(db, q=args.get("search") or None, limit=12, with_total=True)
    rows = [f"- {str(e.get('timestamp') or '')[:16]} {e.get('actor_email')}: {e.get('action')} {e.get('resource_type')} "
            f"{e.get('resource_id')}" for e in entries]
    return f"{total} matching entries; newest:\n" + _lines(rows, "Nothing logged.")


def notes_list(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import eng_notes

    notes = eng_notes.list_notes(item_id=args.get("item_id") or None, days=60)[:8]
    return _lines([f"- {n['created_at'][:10]} {n['item_name']} from {n['author']}{' (URGENT)' if n['urgent'] else ''}: "
                   f"{n['text'][:160]} [unread by {len(n['unread'])}]" for n in notes], "No engineering notes.")


def recipes_list(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import recipes

    rows = [f"- #{r['id']} {r['name']} ({'on' if r['enabled'] else 'off'}, {r['mode']}): {r['description']}" for r in recipes.list_recipes()]
    starters = [f"{s['slug']}: {s['recipe']['name']}" for s in recipes.starters() if not s["added"]]
    return _lines(rows, "No automations added yet.") + ("\nReady to add: " + "; ".join(starters[:8]) if starters else "")


def recipes_preview(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.coordination import recipes

    did = recipes.run_dates(dry_run=True)
    return _lines([f"- {d['outcome']}: {d['what']}" for d in did[:12]], "Nothing would fire right now.", len(did) - 12)


def sage_export_preview(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.sage_export_service import sage_export_service

    _csv, orders = sage_export_service.export_sales_orders(only_new=True, mark_exported=False)
    return (f"{len(orders)} sales orders are waiting for the next Sage import file: " + ", ".join(map(str, orders[:15]))
            if orders else "No new sales orders are waiting for Sage.")
    # The file itself (and marking orders as sent) is made on the Sage export screen, so a person decides when.


# ---------------------------------------------------------------------------
# Drafts: saved at once, because nothing is sent until a person presses send
# ---------------------------------------------------------------------------


def save_email_draft(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    draft = {k: args.get(k) for k in ("to", "subject", "body")}
    if not all(draft.values()):
        return "A draft needs a recipient, a subject and a body."
    action = agent._propose(TOOLS["send_email"], draft, user)
    add_card("draft", f"Draft to {draft['to']}", f"{draft['subject']}",
             [{"label": "Edit & Send Draft", "action": "edit", "action_id": action["id"]},
              {"label": "Cancel", "action": "undo", "action_id": action["id"]}], action_id=action["id"])
    return f"Draft saved for you to check (action {action['id']}). Nothing was sent."


# ---------------------------------------------------------------------------
# Changes (proposals)
# ---------------------------------------------------------------------------


def create_vendor_job(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import vendors

    j = vendors.create({k: v for k, v in args.items() if v not in (None, "")}, user)
    return {"done": f"Added {j['vendor']} order {j.get('po_number') or j['id']}.", "link": f"/vendors?job={j['id']}"}


def create_shipment(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import shipments

    s = shipments.create({k: v for k, v in args.items() if v not in (None, "")}, user)
    return {"done": f"Added shipment {s.get('reference') or s['id']}.", "link": f"/shipments?id={s['id']}"}


def update_shipment(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import shipments

    changes = {k: v for k, v in args.items() if k != "shipment_id" and v not in (None, "")}
    s = shipments.update(int(args["shipment_id"]), changes, user.get("email", "unknown"))
    return {"done": f"Updated shipment {s.get('reference') or s['id']}.", "link": f"/shipments?id={s['id']}"}


def post_engineering_note(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import eng_notes

    n = eng_notes.post(str(args["item_id"]), args["text"], user, args.get("languages") or [], bool(args.get("urgent")))
    return {"done": f"Note sent to {', '.join(n['recipients'])}.", "link": f"/?board={n['board_id']}&item={n['item_id']}"}


def resolve_exception(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.exception_queue_service import ResolveExceptionRequest, exception_queue_service

    out = exception_queue_service.resolve_exception(
        args["exception_id"], ResolveExceptionRequest(action_type=args["action_type"], resolution_notes=args.get("notes") or "Done from the assistant."),
        user.get("email", "unknown"))
    return {"done": str(out.get("message") or out.get("status") or "Resolved.")}


def toggle_recipe(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import recipes

    r = recipes.update(int(args["recipe_id"]), {"enabled": bool(args["enabled"])}, user)
    return {"done": f"{r['name']} is now {'on' if r['enabled'] else 'off'}."}


def add_starter_recipe(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import recipes

    r = recipes.add_starter(args["slug"], {"recipients": args.get("recipients")} if args.get("recipients") else {}, user)
    return {"done": f"Added the automation \"{r['name']}\". Its emails are drafts a person sends."}


def run_recipes_now(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import recipes

    did = recipes.run_dates(dry_run=False)
    return {"done": f"Ran the date and time-in-status automations: {len(did)} action(s)."}


_PEOPLE = "Who to ask: ask the user if they have not said."

TOOLS: List[Tool] = [
    Tool("order_detail", "One customer order in detail: status, lines, vendor POs. Gives Open Record and Print Packing Slip buttons.",
         {"search": _s("Customer, customer PO or sales order number")}, order_detail, required=["search"]),
    Tool("packing_slip", "Find an order's packing slip and give the user a Print button.",
         {"search": _s("Sales order, customer PO or customer")}, packing_slip, required=["search"]),
    Tool("mtr_lookup", "A certified Material Test Report by heat number.", {"heat_number": _s("Heat number")}, mtr_lookup,
         required=["heat_number"]),
    Tool("lead_targets", "Target plant accounts for sales outreach.", {"search": _s("Industry, company or place")}, lead_targets),
    Tool("exception_queue", "The review queue: stuck or flagged items (unmatched parts, bill differences, held orders).",
         {"search": _s("Part of a title, customer or reference"), "status": _s("OPEN or RESOLVED", enum=["OPEN", "RESOLVED"])},
         exception_queue),
    Tool("mail_search", "Emails the system has read: what each was, and which wait on a person.",
         {"search": _s("Words in subject, sender or summary"), "category": _s("order, quote_request, vendor_reply, delivery_question, complaint, supplier_invoice, internal"),
          "tag": _s("A tag such as rfq, invoice, urgent, photos"), "needs_action": {"type": "boolean", "description": "Only ones waiting on a person"},
          "days": {"type": "integer", "description": "How many days back (default 14)"}}, mail_search),
    Tool("reconciliation_recent", "Recent supplier bill matches against their POs, and which have differences.", {}, reconciliation_recent),
    Tool("audit_recent", "The audit trail: who changed what (managers only).", {"search": _s("Record id, person or action")},
         audit_recent, min_role="MANAGER"),
    Tool("notes_list", "Engineering notes from management, with how many engineers have not read them.",
         {"item_id": _s("The job's monday item id, blank for all")}, notes_list),
    Tool("recipes_list", "The automations people have added, and the ready-made ones that can be added.", {}, recipes_list),
    Tool("recipes_preview", "What the date and time-in-status automations would do right now, without doing it.", {}, recipes_preview),
    Tool("sage_export_preview", "Which sales orders are waiting for the next Sage 50 import file.", {}, sage_export_preview),
    Tool("save_email_draft", "Write an email as a DRAFT for the user to edit and send. Nothing is sent. Use this for any email, reply, "
         "proposal or RFQ the user wants written.",
         {"to": _s("Recipient address"), "subject": _s("Subject"), "body": _s("Plain-text body")}, save_email_draft,
         min_role="OPERATOR", required=["to", "subject", "body"]),
    Tool("create_vendor_job", "Add a vendor order (PO) to track.",
         {"vendor": _s("Vendor name"), "description": _s("What it is"), "po_number": _s("Allied PO number"),
          "ordered_on": _s(f"PO date {_DATE}"), "quoted_lead_days": {"type": "integer", "description": "Vendor's quoted lead time in days"},
          "owner_email": _s("Who owns it"), "project_item_id": _s("The drawing job it belongs to (monday item id)")},
         create_vendor_job, write=True, min_role="OPERATOR", required=["vendor"],
         summarize=lambda a: f"Add vendor order: {a.get('vendor')} PO {a.get('po_number') or '-'} {a.get('description') or ''}"),
    Tool("create_shipment", "Add a shipment to track.",
         {"reference": _s("Reference, e.g. PO 9277"), "mode": _s("SEA or AIR or TRUCK", enum=["SEA", "AIR", "TRUCK"]),
          "status": _s("Where it is now"), "eta": _s(f"Arrival {_DATE}"), "etd": _s(f"Departure {_DATE}"),
          "container_no": _s("Container number"), "forwarder": _s("Freight forwarder")},
         create_shipment, write=True, min_role="OPERATOR", required=["reference"],
         summarize=lambda a: f"Add shipment {a.get('reference')} ({a.get('mode') or 'SEA'}), ETA {a.get('eta') or '-'}"),
    Tool("update_shipment", "Change a shipment's status or dates.",
         {"shipment_id": {"type": "integer", "description": "Shipment id"}, "status": _s("New status"), "eta": _s(f"New ETA {_DATE}"),
          "free_time_ends": _s(f"Last free day {_DATE}"), "note": _s("Note")},
         update_shipment, write=True, min_role="OPERATOR", required=["shipment_id"],
         summarize=lambda a: f"Update shipment {a.get('shipment_id')}: " + ", ".join(f"{k} {v}" for k, v in a.items() if k != "shipment_id")),
    Tool("post_engineering_note", "Send a note from management to the engineers on a job, optionally with Mandarin and Vietnamese.",
         {"item_id": _s("The job's monday item id"), "text": _s("The note, in English"),
          "languages": {"type": "array", "items": {"type": "string", "enum": ["zh", "vi"]}, "description": "Translations to attach"},
          "urgent": {"type": "boolean", "description": "Also text and push the engineers"}},
         post_engineering_note, write=True, min_role="MANAGER", required=["item_id", "text"],
         summarize=lambda a: f"Send engineering note on job {a.get('item_id')}{' (URGENT)' if a.get('urgent') else ''}: {a.get('text')}"),
    Tool("resolve_exception", "Resolve a review-queue item with one of the actions it offers.",
         {"exception_id": _s("Exception id, e.g. EXC-1A2B3C"), "action_type": _s("One of the item's actions, or MANUALLY_RESOLVED"),
          "notes": _s("What was checked or decided")},
         resolve_exception, write=True, min_role="OPERATOR", required=["exception_id", "action_type"],
         summarize=lambda a: f"Resolve {a.get('exception_id')} with {a.get('action_type')}: {a.get('notes') or ''}"),
    Tool("toggle_recipe", "Switch one of the automations on or off.",
         {"recipe_id": {"type": "integer", "description": "Automation number"}, "enabled": {"type": "boolean", "description": "true = on"}},
         toggle_recipe, write=True, min_role="MANAGER", required=["recipe_id", "enabled"],
         summarize=lambda a: f"Turn automation #{a.get('recipe_id')} {'on' if a.get('enabled') else 'off'}"),
    Tool("add_starter_recipe", "Add one of the ready-made automations (see recipes_list for the names).",
         {"slug": _s("The ready-made automation's name"), "recipients": _s("Who to tell, if it asks for someone")},
         add_starter_recipe, write=True, min_role="MANAGER", required=["slug"],
         summarize=lambda a: f"Add the automation {a.get('slug')}"),
    Tool("run_recipes_now", "Run the date and time-in-status automations now.", {}, run_recipes_now, write=True, min_role="MANAGER",
         summarize=lambda a: "Run the date and time-in-status automations now"),
]

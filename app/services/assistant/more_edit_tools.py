"""More things the assistant can change, each one a card a person confirms (nothing happens before the click): a draft purchase order's lines, what an open RFQ
asks for, an alert (hide it, hand it on) and the ball. Customers and vendors are edited with edit_record (kinds "customer" and "vendor"). A sent or approved
purchase order stays locked; the services behind each tool keep their own rules."""

from typing import Any, Dict, List

from app.services.assistant import record_tools
from app.services.assistant.tools import Tool, _s

record_tools._RECORD_WORDS.update({"customer": "customer", "client": "customer", "vendor": "vendor", "supplier": "vendor"})


def _check_po_lines(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import records
    from app.services.coordination import purchase_orders

    po = records._read("po", str(a.get("ref") or ""))
    if po["status"] != "DRAFT":
        raise ValueError("The lines of a purchase order can only change while it is a draft, before it is approved.")
    lines = a.get("lines")
    if not isinstance(lines, list) or not lines or len(lines) > 100:
        raise ValueError("Give the lines as a list of {description, quantity, unit_cost}.")
    purchase_orders._typed_lines(lines)                      # a bad line is refused now, so a card is only made for lines that will be saved
    a["ref"] = str(po["id"])


def edit_po_lines(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import purchase_orders

    po = purchase_orders.update(int(args["ref"]), {"lines": args["lines"]}, user)
    return {"done": f"Purchase order {po['po_number'] or po['id']} now has {len(po['lines'])} line(s), total {po['total']:,.2f}.", "ref": str(po["id"])}


def _check_rfq_items(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import records

    r = records._read("rfq", str(a.get("ref") or ""))
    if r.get("closed_at"):
        raise ValueError("This RFQ is closed, so what it asks for can't change.")
    items = a.get("items")
    if not isinstance(items, list) or len(items) > 100 or not all(isinstance(i, dict) and str(i.get("description") or "").strip() for i in items):
        raise ValueError("Give the items as a list of {description, quantity, unit}.")
    for i in items:
        try:
            q = float(str(i.get("quantity", 1)).replace(",", "")) if i.get("quantity") not in (None, "") else 1.0
        except ValueError:
            raise ValueError(f"'{str(i.get('quantity'))[:20]}' is not a quantity.") from None
        if not (0 < q < 1e9):
            raise ValueError("A quantity must be more than zero.")


def edit_rfq_items(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import rfqs

    r = rfqs.update(str(args["ref"]), {"items": args["items"]}, user)
    return {"done": f"{r['number']} now asks for {len(r.get('items') or [])} item(s).", "ref": r["number"]}


def _alert_result(res: Any, what: str) -> Dict[str, Any]:
    said = {"missing": "There is no such alert.", "done": "That alert is already closed.", "forbidden": "That alert is not yours to change.", "bad": "That was not valid."}
    if isinstance(res, str) and res in said:
        raise ValueError(said[res])
    return {"done": what}


def snooze_alert(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import notify

    hours = float(args.get("hours") or 24)
    res = notify.snooze(int(args["alert_id"]), user.get("email", ""), hours=hours, any_alert=user.get("role") in ("MANAGER", "ADMIN"))
    return _alert_result(res, f"Alert {args['alert_id']} is hidden for {hours:g} hour(s).")


def assign_alert(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import notify

    res = notify.assign(int(args["alert_id"]), user.get("email", ""), str(args.get("to") or ""), any_alert=user.get("role") in ("MANAGER", "ADMIN"))
    return _alert_result(res, f"Alert {args['alert_id']} handed to {args.get('to')}.")


def _check_ball(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import actions

    a["kind"] = record_tools._kind(a.get("kind"), {**record_tools._RECORD_WORDS, "rfq": "rfq"})
    if a["kind"] not in ("job", "rfq", "po", "shipment", "order"):
        raise ValueError("The ball can be handed on a job, RFQ, PO, shipment or order.")
    ctx = actions.context(a["kind"], str(a.get("ref") or ""))
    if not ctx.get("thread"):
        raise ValueError("The ball can't be handed on this one.")
    if not str(a.get("to") or "").strip() and not str(a.get("ball_with") or "").strip():
        raise ValueError("Say whose turn it is (a person's email, or a vendor, the customer or a carrier by name).")


def hand_ball(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import actions

    out = actions.handoff(args["kind"], str(args["ref"]), user, to=str(args.get("to") or ""), ball_with=str(args.get("ball_with") or ""),
                          note=str(args.get("note") or ""), due=str(args.get("due") or ""))
    return {"done": out["message"]}


def _lines_summary(a: Dict[str, Any]) -> str:
    return f"Set the lines of purchase order {a.get('ref')}\n" + "\n".join(
        f"  {x.get('quantity', 1)} x {str(x.get('description'))[:60]}" + (f" @ {x['unit_cost']}" if x.get("unit_cost") not in (None, "") else "") for x in (a.get("lines") or []) if isinstance(x, dict))


def _items_summary(a: Dict[str, Any]) -> str:
    return f"Set what {a.get('ref')} asks for\n" + "\n".join(f"  {x.get('quantity', 1)} x {str(x.get('description'))[:60]}" for x in (a.get("items") or []) if isinstance(x, dict))


TOOLS: List[Tool] = [
    Tool("edit_po_lines", "Replace the lines of a purchase order that is still a DRAFT (what to make, quantity, unit cost, drawing revision). After approval the lines are locked.",
         {"ref": _s("The purchase order (id or number)"), "lines": {"type": "array", "items": {"type": "object"}, "description": "[{description, quantity, unit_cost, revision}] - the whole list"}},
         edit_po_lines, write=True, min_role="OPERATOR", required=["ref", "lines"], check=_check_po_lines, summarize=_lines_summary),
    Tool("edit_rfq_items", "Replace what an open RFQ asks for (description, quantity, unit). The whole list is replaced.",
         {"ref": _s("The RFQ number"), "items": {"type": "array", "items": {"type": "object"}, "description": "[{description, quantity, unit}] - the whole list"}},
         edit_rfq_items, write=True, min_role="OPERATOR", required=["ref", "items"], check=_check_rfq_items, summarize=_items_summary),
    Tool("snooze_alert", "Hide one of my alerts for a number of hours (at most 30 days).",
         {"alert_id": {"type": "integer", "description": "The alert number"}, "hours": {"type": "number", "description": "Hours to hide it"}},
         snooze_alert, write=True, min_role="OPERATOR", required=["alert_id"], summarize=lambda a: f"Hide alert {a.get('alert_id')} for {a.get('hours') or 24} hour(s)"),
    Tool("assign_alert", "Hand one of my alerts to another person (their email).",
         {"alert_id": {"type": "integer", "description": "The alert number"}, "to": _s("Their email")},
         assign_alert, write=True, min_role="OPERATOR", required=["alert_id", "to"], summarize=lambda a: f"Hand alert {a.get('alert_id')} to {a.get('to')}"),
    Tool("hand_ball", "Hand the ball on a job, RFQ, PO, shipment or order: to a person (email) or a vendor, the customer or a carrier by name, with a note and a due date.",
         {"kind": _s("job, rfq, po, shipment or order"), "ref": _s("Its reference"), "to": _s("A person's email (optional)"), "ball_with": _s("Or who has it: a vendor, the customer, a carrier"),
          "note": _s("What they must do"), "due": _s("Due date (optional)")},
         hand_ball, write=True, min_role="OPERATOR", required=["kind", "ref"], check=_check_ball,
         summarize=lambda a: f"Hand the ball on {a.get('kind')} {a.get('ref')} to {a.get('to') or a.get('ball_with')}" + (f"\n{a.get('note')}" if a.get("note") else "")),
]

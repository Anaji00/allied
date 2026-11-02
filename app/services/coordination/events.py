"""Event-driven actions: when something happens, the matching reactions run straight away.

The code where a thing happens calls `emit()`: an RFQ logged or moved on, a
status changed on a monday board (seen by the board reader), a vendor order
turning late, a milestone changing. Each reaction has a mode like the
autopilot's plays (AUTOPILOT_REACTIONS: off / suggest / auto) and is carried out
the same way: messages to people outside Allied are drafted for a person to
confirm unless the reaction is on auto, and nothing is written to monday.

Reactions run on a small pool of background workers (AUTOPILOT_EVENTS_ASYNC) so the request or
sync that raised the event is never slowed down or broken by them. The keys
are shared with the hourly plays, so an event and the timer never do the same
thing twice.
"""

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List

from app.config import settings
from app.core.logging import logger
from app.services.coordination import autopilot
from app.services.coordination import notify as alerts
from app.services.coordination.autopilot import Intent, reviewers, vendor_email

recent: Deque[Dict[str, Any]] = deque(maxlen=200)
_lock = threading.Lock()
# One background worker, not a thread per event. Reactions run one at a time anyway (they
# share drafts and alerts), so more workers would only wait on each other; one keeps events
# in the order they happened. The queue is capped: past _MAX_WAITING, the caller (a board
# read) runs the reaction itself, which slows that read instead of growing memory.
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="event")
_MAX_WAITING = 500
_waiting = threading.BoundedSemaphore(_MAX_WAITING)


@dataclass
class Reaction:
    event: str
    run: Callable[[Dict[str, Any]], List[Intent]]
    default: str
    about: str


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


def acknowledge_rfq(e: Dict[str, Any]) -> List[Intent]:
    """A new request by email or phone: tell the customer it arrived and when the quote is due."""
    r = e["rfq"]
    if r.get("source") == "monday" or not r.get("contact_email"):
        return []
    due = datetime.fromisoformat(r["due_at"]).astimezone(alerts.now_local().tzinfo).strftime("%A %d %B")
    body = (f"Hello,\n\nThank you for your request{(' for ' + r['subject']) if r.get('subject') else ''}. "
            f"It is logged as {r['number']}, and we will have a quote to you by {due}. If we need anything "
            f"else to price it, we will ask straight away.\n\nThank you,\nAllied Welding Systems")
    return [Intent("draft", f"rfq-ack:{r['id']}", f"Tell {r['customer'] or r['contact_email']} we received {r['number']}",
                   to=reviewers(r.get("owner_email")), tool="send_email",
                   args={"to": r["contact_email"], "subject": f"Received: {r['subject'] or r['number']}", "body": body,
                         "_rfq_id": r["id"]})]


def ask_vendors_for_price(e: Dict[str, Any]) -> List[Intent]:
    """An RFQ moved to waiting on a vendor: ask each bidding vendor at once, not two days later."""
    r = e["rfq"]
    if e.get("to") != "WAITING_VENDOR":
        return []
    out, missing = [], []
    ask = (f"Could you price {r['subject'] or 'the attached request'} for us (our ref {r['number']})? "
           "Please include lead time and freight to our customer.\n\nThank you,\nAllied Welding Systems")
    for name in [n.strip() for n in (r.get("vendor") or "").split(",") if n.strip()]:
        email = vendor_email(name)
        if not email:
            missing.append(name)
            continue
        out.append(Intent("draft", f"rfq-price-ask:{r['id']}:{name.casefold()}",
                          f"Ask {name} to price {r['number']} ({r['customer']})",
                          to=reviewers(r.get("owner_email")), tool="send_email",
                          args={"to": email, "subject": f"Price request: {r['subject'] or r['number']}",
                                "body": f"Hello {name},\n\n{ask}"}))
    if missing:  # one note for the RFQ, not one per vendor
        out.append(Intent("alert", f"rfq-price-ask:{r['id']}:no-email",
                          f"Ask {', '.join(missing)} by hand (no email on file)",
                          to=reviewers(r.get("owner_email")), title=f"Ask {', '.join(missing)} to price {r['number']}",
                          body=f"No email on file for {', '.join(missing)}, so nothing was drafted.\n\n"
                               f"Suggested message:\nHello,\n\n{ask}"))
    return out


def rfq_won(e: Dict[str, Any]) -> List[Intent]:
    r = e["rfq"]
    if e.get("to") != "WON":
        return []
    amount = f" ({r['quote_amount']})" if r.get("quote_amount") else ""
    return [Intent("alert", f"rfq-won:{r['id']}", f"{r['number']} won: tell the team to raise the order",
                   to=reviewers(r.get("owner_email")), leadership=True,
                   title=f"Won: {r['customer']} - {r['subject'] or r['number']}{amount}",
                   body=f"{r['customer']} accepted {r.get('quote_number') or 'the quote'}. Next: the customer's PO, "
                        "the sales order, and the vendor PO on the orders board.")]


def track_shipped_order(e: Dict[str, Any]) -> List[Intent]:
    """An order went to SHIPPED on the orders board: start tracking its shipment."""
    j = e["job"]
    if (e.get("to") or "").casefold() != "shipped":
        return []
    ref = f"PO {j.get('po_number') or j['id']}"
    from app.services.coordination import shipments

    def start() -> None:
        if any(s.get("reference") == ref for s in shipments.list_shipments(include_finished=True)):
            return
        due = j.get("customer_due")
        eta = due if due and due > alerts.today().isoformat() else None  # a past date would read as late at once
        shipments.create({"reference": ref, "mode": j.get("mode") or "SEA", "status": "DEPARTED",
                          "etd": alerts.today().isoformat(), "eta": eta,
                          "origin": j.get("vendor"), "destination": (j.get("description") or "").split(" - ")[0] or None,
                          "owner_email": j.get("owner_email") or reviewers()[0]}, autopilot.SYSTEM)

    return [Intent("work", f"ship-track:{ref}", f"Track the shipment for {ref} ({j.get('description') or j['vendor']})",
                   work=start)]


def photos_waiting(e: Dict[str, Any]) -> List[Intent]:
    j = e["job"]
    if (e.get("to") or "").casefold() not in autopilot._labels(settings.ORDERS_PHOTO_LABELS):
        return []
    return [Intent("alert", f"order-photos:{j['id']}:{e['to']}", f"Photos to check for {j.get('description') or j['vendor']}",
                   to=reviewers(j.get("owner_email")), title=f"{e['to'].title()}: {j.get('description') or j['vendor']}",
                   body=f"{j['vendor']} order PO {j.get('po_number') or '-'} is now {e['to']}. Check the photos "
                        "against the drawing before it is crated.")]


def chase_late_vendor(e: Dict[str, Any]) -> List[Intent]:
    """Only once the order's wait after its PO is over; the same draft as the timer's, so never twice."""
    chase = autopilot.vendor_chase(e["job"], alerts.today())
    return [chase] if chase else []


def drawing_ready(e: Dict[str, Any]) -> List[Intent]:
    """A drawing job finished: tell whoever runs the vendor orders waiting on it."""
    m = e["milestone"]
    if not m.get("done"):
        return []
    from app.services.coordination import vendors

    waiting = [j for j in vendors.overview()["jobs"] if j.get("project_item_id") == m.get("item_id")]
    if not waiting:
        return []
    lines = "\n".join(f"- {j['vendor']} PO {j.get('po_number') or '-'}: {j.get('description') or ''}" for j in waiting)
    owners = sorted({j["owner_email"] for j in waiting if j.get("owner_email")}) or reviewers()
    return [Intent("alert", f"drawing-ready:{m['item_id']}:{e.get('to')}", f"Drawing {m.get('name')} is {e.get('to')}",
                   to=owners, title=f"Drawing ready: {m.get('name')} ({e.get('to')})",
                   body=f"The drawing these orders wait on is now {e.get('to')}. Send it to the vendor:\n{lines}")]


REACTIONS: Dict[str, Reaction] = {
    "rfq_acknowledge": Reaction("rfq.received", acknowledge_rfq, "suggest",
                                "Tells the customer a request arrived and when the quote is due."),
    "rfq_ask_vendors": Reaction("rfq.status_changed", ask_vendors_for_price, "suggest",
                                "When an RFQ goes to waiting on a vendor, asks each bidding vendor for a price."),
    "rfq_won": Reaction("rfq.status_changed", rfq_won, "auto", "When a quote is won, tells the team and leadership."),
    "order_shipped": Reaction("order.status_changed", track_shipped_order, "auto",
                              "When an order goes to SHIPPED on the orders board, starts tracking the shipment."),
    "order_photos": Reaction("order.status_changed", photos_waiting, "auto",
                             "When an order is waiting on photos, tells staff to check them."),
    "vendor_late_chase": Reaction("vendor_job.late", chase_late_vendor, "suggest",
                                  "When an order turns late and its wait after the PO is over, drafts the follow-up."),
    "drawing_ready": Reaction("milestone.status_changed", drawing_ready, "auto",
                              "When a drawing job is finished, tells the owners of the vendor orders linked to it."),
}
EVENTS = sorted({r.event for r in REACTIONS.values()} | {"milestone.status_changed"})


def modes() -> Dict[str, str]:
    out = {name: r.default for name, r in REACTIONS.items()}
    for part in (settings.AUTOPILOT_REACTIONS or "").split(","):
        name, _, mode = part.partition("=")
        if name.strip() in REACTIONS and mode.strip().lower() in autopilot.MODES:
            out[name.strip()] = mode.strip().lower()
    return out


# ---------------------------------------------------------------------------
# Emitting
# ---------------------------------------------------------------------------


def emit(event: str, payload: Dict[str, Any], summary: str = "") -> None:
    """Records an event and runs its reactions. Never raises into the caller."""
    if not settings.AUTOPILOT_ENABLED:
        return
    from app.services.coordination import recipes

    chosen = modes()
    try:
        has_recipes = recipes.wanted(event)
    except Exception as exc:  # noqa: BLE001 - the built-in reactions still run
        logger.error(f"Could not read recipes for {event}: {exc}")
        has_recipes = False
    if not has_recipes and not any(r.event == event and chosen[n] != "off" for n, r in REACTIONS.items()):
        recent.appendleft({"at": alerts.now_local().isoformat(), "event": event, "summary": summary, "reactions": []})
        return
    if settings.AUTOPILOT_EVENTS_ASYNC and _waiting.acquire(blocking=False):
        _pool.submit(_queued, event, payload, summary)
    else:
        handle(event, payload, summary)


def _queued(event: str, payload: Dict[str, Any], summary: str) -> None:
    try:
        handle(event, payload, summary)
    finally:
        _waiting.release()


def handle(event: str, payload: Dict[str, Any], summary: str = "", dry_run: bool = False) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"at": alerts.now_local().isoformat(), "event": event, "summary": summary, "reactions": []}
    chosen = modes()
    did_all: List[Dict[str, Any]] = []
    try:
        with _lock:
            for name, r in REACTIONS.items():
                if r.event != event or chosen[name] == "off":
                    continue
                try:
                    did = autopilot.carry_out(name, chosen[name], r.run(payload), dry_run)
                except Exception as exc:  # noqa: BLE001 - one reaction failing must not stop the rest
                    logger.error(f"Reaction {name} to {event} failed: {exc}")
                    entry["reactions"].append({"name": name, "mode": chosen[name], "error": str(exc)[:200]})
                    continue
                if did:
                    entry["reactions"].append({"name": name, "mode": chosen[name], "did": did})
                    did_all += did
            from app.services.coordination import recipes

            did = recipes.safe_on_event(event, payload, dry_run)
            if did:
                entry["reactions"].append({"name": "recipes", "mode": "per recipe", "did": did})
                did_all += did
        if not dry_run:
            autopilot.tell_reviewers(did_all)
            recent.appendleft(entry)
    except Exception as exc:  # noqa: BLE001 - events are best effort; the thing itself already happened
        logger.error(f"Event {event} failed: {exc}")
    return entry


def status() -> Dict[str, Any]:
    chosen = modes()
    return {"async": settings.AUTOPILOT_EVENTS_ASYNC, "events": EVENTS,
            "reactions": [{"name": n, "event": r.event, "mode": chosen[n], "about": r.about} for n, r in REACTIONS.items()],
            "recent": list(recent)[:50]}

"""Secretary work: replies a person would otherwise type, drafted for them to send.

- A customer asks where an order is: a reply with the order's real status, its sales order,
  and the vendor's ship date if there is one, taken from Allied's records. Without a PO
  number to find, the reply asks for one.
- A customer complains: an acknowledgement saying who has it and when they will hear back.
- Each morning: a short personal brief for each person with something waiting on them.

The facts in a reply come only from the records; there is nothing for an AI to invent. Every
reply is a draft in the sender's queue: a person reads it and presses Send. Replies are only
drafted to a customer's own, verified address, so nothing about an order goes to a stranger.
"""

import re
from typing import Any, Dict, List, Optional

from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import OrderDB, VendorJobDB
from app.services.coordination import notify as alerts

_SIGN = "\n\nKind regards,\nAllied Welding Systems"
_STATUS = {"RECEIVED": "received and being entered", "STAGED": "entered and with our suppliers",
           "PROCESSED": "in production with our supplier", "DISPATCHED": "shipped"}


def _orders_named(text: str, customer_domain: str) -> List[OrderDB]:
    """Orders whose customer PO or sales order number appears in the email, for this customer."""
    tokens = {t.upper().strip(".,:;#") for t in re.findall(r"[A-Za-z0-9#-]{3,}", text or "")}
    if not tokens:
        return []
    with SessionLocal() as db:
        rows = db.query(OrderDB).filter(
            (OrderDB.customer_po_number.in_(tokens)) | (OrderDB.sales_order_number.in_(tokens))
            | (OrderDB.customer_po_number.in_({f"PO-{t}" for t in tokens}))).limit(5).all()
        db.expunge_all()
    # The order must belong to the customer writing: never tell one customer about another's order.
    from app.core.container import get_entity_matcher

    matcher = get_entity_matcher()
    out = []
    for o in rows:
        rec = matcher.customers_cache.get(str(o.customer_id)) or {}
        on_file = str(rec.get("email") or "").rsplit("@", 1)[-1].lower()
        if on_file and (customer_domain == on_file or customer_domain.endswith("." + on_file)):
            out.append(o)
    return out


def _vendor_dates(order: OrderDB) -> List[str]:
    """What the vendor side says, in the customer's terms: shipped, or expected to ship."""
    pos = [str(p) for p in (order.purchase_orders or {}).values() if p]
    if not pos:
        return []
    with SessionLocal() as db:
        jobs = db.query(VendorJobDB).filter(VendorJobDB.po_number.in_(pos)).all()
        facts = []
        for j in jobs:
            if j.shipped_on:
                facts.append(f"left our supplier on {j.shipped_on}")
            elif j.promised_on:
                facts.append(f"is due to leave our supplier on {j.promised_on}")
    return facts


def delivery_reply(email_id: str, sender: str, subject: str, body: str) -> Optional[Dict[str, Any]]:
    """Drafts the answer to "where is my order?" for a person to send."""
    from app.services.coordination import autopilot
    from app.services.coordination.autopilot import Intent, reviewers

    domain = sender.rsplit("@", 1)[-1].lower()
    orders = _orders_named(f"{subject}\n{body}", domain)
    if orders:
        lines = []
        for o in orders:
            where = _STATUS.get((o.status or "").upper(), (o.status or "in progress").lower())
            dates = _vendor_dates(o)
            lines.append(f"- Your PO {o.customer_po_number} (our sales order {o.sales_order_number}) is {where}"
                         + (f"; it {' and '.join(dates)}" if dates else "") + ".")
        text = ("Hello,\n\nThank you for checking in. Here is where things stand:\n\n" + "\n".join(lines)
                + "\n\nWe will let you know as soon as anything changes." + _SIGN)
        why = [f"From {sender}, about {', '.join(o.customer_po_number for o in orders)}",
               "Every fact above is from our records (order status and the vendor's dates); check before sending"]
        to_review = reviewers()
    else:
        text = ("Hello,\n\nThank you for your message. Could you send the PO number (or our sales order number) "
                "so we can give you an exact update?" + _SIGN)
        why = [f"From {sender}; no PO or sales order number found in the email, so the reply asks for one"]
        to_review = reviewers()
    intent = Intent("draft", f"mail-reply:{email_id}", f"Reply to {sender}: where their order is",
                    to=to_review, tool="send_email",
                    args={"to": sender, "subject": f"Re: {subject}"[:200], "body": text, "_why": why})
    did = autopilot.carry_out("delivery_reply", "suggest", [intent])
    autopilot.tell_reviewers(did)
    return did[0] if did else None


def complaint_ack(email_id: str, sender: str, subject: str, summary: str) -> Optional[Dict[str, Any]]:
    """Drafts a prompt, human acknowledgement of a complaint for a person to send."""
    from app.services.coordination import autopilot, workdays
    from app.services.coordination.autopilot import Intent, reviewers

    by = workdays.add_days(workdays.now(), 1).astimezone(alerts.now_local().tzinfo)
    text = ("Hello,\n\nThank you for letting us know, and I'm sorry for the trouble. We are looking into it now "
            f"and will come back to you by the end of {by:%A %d %B} with what happened and what we will do." + _SIGN)
    intent = Intent("draft", f"mail-ack:{email_id}", f"Acknowledge {sender}'s complaint", to=reviewers(),
                    tool="send_email", args={"to": sender, "subject": f"Re: {subject}"[:200], "body": text,
                                             "_why": [f"Complaint from {sender}: {summary[:200]}",
                                                      "Promises a full answer within one working day"]})
    did = autopilot.carry_out("complaint_ack", "suggest", [intent])
    autopilot.tell_reviewers(did)
    return did[0] if did else None


def on_mail(category: str, email_id: str, sender: str, subject: str, body: str, summary: str,
            trusted: bool) -> None:
    """Called by the mailbox reader for each new email it has sorted."""
    if not trusted:
        return
    try:
        if category == "delivery_question":
            delivery_reply(email_id, sender, subject, body)
        elif category == "complaint":
            complaint_ack(email_id, sender, subject, summary)
    except Exception as exc:  # noqa: BLE001 - the email is logged and alerted either way
        logger.warning(f"Secretary draft for {email_id} failed: {exc}")


# ---------------------------------------------------------------------------
# Personal morning brief
# ---------------------------------------------------------------------------


def brief_for(email: str) -> List[str]:
    """What is waiting on this person this morning, one line each; empty if nothing is."""
    from app.services import mail_log
    from app.services.coordination import autopilot, projects, vendors

    email = email.lower()
    lines = []
    n = autopilot.waiting_for(email)
    if n:
        lines.append(f"{n} message{'s' if n != 1 else ''} waiting for your OK in your Inbox")
    for j in vendors.overview()["jobs"]:
        if (j.get("owner_email") or "").lower() == email and j["state"] in ("late", "will_miss"):
            lines.append(f"{j['vendor']} PO {j.get('po_number') or j['id']}: {j['reasons'][0] if j['reasons'] else j['state']}")
    for p in projects.overview()["projects"]:
        for m in p["milestones"]:
            if email in [e.lower() for e in m.get("owner_emails") or []] and m["state"] == "overdue":
                lines.append(f"Drawing overdue: {m['name']} ({p['name']})")
    if email in [r.lower() for r in autopilot.reviewers()]:
        waiting_mail = mail_log.for_summary(days=2)
        if waiting_mail:
            lines.append(f"{len(waiting_mail)} email{'s' if len(waiting_mail) != 1 else ''} waiting on a person")
    return lines[:20]


def send_briefs() -> int:
    """One brief per person who has something waiting, once a day."""
    from app.services.coordination import autopilot, projects, vendors

    people = {e.lower() for e in autopilot.reviewers()}
    people |= {(j.get("owner_email") or "").lower() for j in vendors.overview()["jobs"] if j.get("owner_email")}
    for p in projects.overview()["projects"]:
        for m in p["milestones"]:
            people |= {e.lower() for e in m.get("owner_emails") or []}
    sent = 0
    today = alerts.today().isoformat()
    for email in sorted(e for e in people if "@" in e):
        lines = brief_for(email)
        if lines and alerts.notify("personal_brief", f"brief:{email}:{today}", f"Your day: {len(lines)} thing(s) waiting",
                                   "\n".join(f"- {line}" for line in lines), to=[email], link="/inbox"):
            sent += 1
    return sent

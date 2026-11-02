"""Every email the mailbox reader sees, recorded once, with what it was and what was done.

The log is how the reader knows a message was already handled, so nothing has to be
marked read in Gmail (staff still see new mail as new). It is also the team's view of
everything that came in: orders, requests for quote, vendor replies, delivery questions,
complaints, supplier invoices, internal mail, and the rest, each with a one-line summary.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MailLogDB

# What each outcome of the inbound reader means: (category, needs a person).
_OUTCOMES = {
    "PROCESSED_ORDER_CREATED": ("order", False),
    "FLAGGED_FOR_REVIEW": ("order", True),
    "RFQ_STAGED": ("quote_request", False),
    "VENDOR_REPLY": ("vendor_reply", False),
    "VENDOR_REPLY_UNMATCHED": ("vendor_reply", True),
    "SHIPMENT_UPDATE": ("shipment", False),
    "SHIPMENT_UPDATE_NEEDS_REVIEW": ("shipment", True),
}
# Sorted mail that needs a person: someone asked something, complained, or billed us.
_ACT_ON = {"delivery_question", "complaint", "supplier_invoice"}


def our_domains() -> set:
    raw = settings.MAIL_OUR_DOMAINS or settings.EMAIL_FROM.rsplit("@", 1)[-1]
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def direction(sender: str, folder: str) -> str:
    """out: sent by us (a Sent folder, or the mailbox itself); internal: from a colleague; in: from outside."""
    sender = (sender or "").lower()
    if "sent" in folder.lower() or sender == (settings.IMAP_USERNAME or "").lower():
        return "out"
    return "internal" if sender.rsplit("@", 1)[-1] in our_domains() else "in"


def classify(status: str) -> tuple:
    """(category, needs_action) for an outcome of the inbound reader."""
    if status in _OUTCOMES:
        return _OUTCOMES[status]
    if status.startswith("SORTED_"):
        category = status[len("SORTED_"):].lower()
        return category, category in _ACT_ON
    return "other", False


def known(message_ids: List[str]) -> set:
    if not message_ids:
        return set()
    with SessionLocal() as db:
        return {m for (m,) in db.query(MailLogDB.message_id).filter(MailLogDB.message_id.in_(message_ids))}


def record(message_id: str, folder: str, sender: str, sender_name: str, recipients: str, subject: str,
           received_at: Optional[datetime], direction_: str, category: str, summary: str, outcome: str,
           needs_action: bool, tags: Optional[List[str]] = None, thread_key: Optional[str] = None) -> bool:
    """Writes one message. False if it was already there (another poll got it first)."""
    with SessionLocal() as db:
        db.add(MailLogDB(message_id=message_id[:512], folder=folder[:128], sender=(sender or "unknown")[:320],
                         sender_name=(sender_name or "")[:320], recipients=(recipients or "")[:2000],
                         subject=(subject or "(no subject)")[:500], received_at=received_at, direction=direction_,
                         category=category[:32], summary=(summary or "")[:4000], outcome=outcome[:64],
                         needs_action=needs_action, tags=",".join(dict.fromkeys(tags or []))[:300] or None,
                         thread_key=(thread_key or message_id)[:200]))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def _as_dict(r: MailLogDB) -> Dict[str, Any]:
    return {"id": r.id, "message_id": r.message_id, "folder": r.folder, "sender": r.sender, "sender_name": r.sender_name,
            "recipients": r.recipients, "subject": r.subject,
            "received_at": r.received_at.isoformat() if r.received_at else None, "direction": r.direction,
            "category": r.category, "tags": [t for t in (r.tags or "").split(",") if t], "thread_key": r.thread_key,
            "summary": r.summary, "outcome": r.outcome, "needs_action": r.needs_action,
            "handled_by": r.handled_by, "handled_at": r.handled_at.isoformat() if r.handled_at else None}


def list_mail(category: Optional[str] = None, needs_action: Optional[bool] = None, q: Optional[str] = None,
              direction_: Optional[str] = None, days: int = 30, limit: int = 200, tag: Optional[str] = None,
              include_quiet: bool = False) -> Dict[str, Any]:
    """The feed. Noise (newsletters, auto-replies, bounces, invites, notifications) and the
    backlog logged on first start are left out unless include_quiet or asked for by category."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        query = db.query(MailLogDB).filter(MailLogDB.created_at >= since)
        if category:
            query = query.filter(MailLogDB.category == category)
        elif not include_quiet:
            query = query.filter(MailLogDB.category.notin_(QUIET))
        if tag:
            query = query.filter(or_(MailLogDB.tags == tag, MailLogDB.tags.like(f"{tag},%"),
                                     MailLogDB.tags.like(f"%,{tag}"), MailLogDB.tags.like(f"%,{tag},%")))
        if direction_:
            query = query.filter(MailLogDB.direction == direction_)
        if needs_action is not None:
            query = query.filter(MailLogDB.needs_action.is_(needs_action), MailLogDB.handled_at.is_(None) if needs_action else True)
        if q:
            like = f"%{q.strip()[:100]}%"
            query = query.filter(or_(MailLogDB.subject.ilike(like), MailLogDB.sender.ilike(like), MailLogDB.summary.ilike(like)))
        rows = query.order_by(MailLogDB.received_at.desc().nullslast(), MailLogDB.id.desc()).limit(limit).all()
        counts: Dict[str, int] = {}
        tag_counts: Dict[str, int] = {}
        for cat, tags in db.query(MailLogDB.category, MailLogDB.tags).filter(MailLogDB.created_at >= since):
            counts[cat] = counts.get(cat, 0) + 1
            for t in (tags or "").split(","):
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        waiting = db.query(MailLogDB).filter(MailLogDB.needs_action.is_(True), MailLogDB.handled_at.is_(None)).count()
    return {"mail": [_as_dict(r) for r in rows], "counts": counts, "tags": tag_counts, "waiting": waiting, "days": days,
            "quiet_hidden": not include_quiet and not category}


def mark_handled(mail_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(MailLogDB, mail_id)
        if row is None:
            raise LookupError("No such email.")
        row.handled_by, row.handled_at = user.get("email"), datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return _as_dict(row)


# Logged quietly: noise, sent mail and the backlog from before the reader started.
QUIET = ("noise", "sent", "earlier")
_ALERT_TITLE = {"delivery_question": "Customer asks about a delivery", "complaint": "Complaint",
                "supplier_invoice": "Supplier invoice to check", "urgent": "Urgent email"}
TITLED = {"delivery_question", "complaint", "supplier_invoice"}


def alert_now(category: str, tags: List[str]) -> bool:
    """Only what can't wait raises an alert at once (MAIL_ALERT_NOW: categories or tags).
    The rest that needs a person waits in the feed and the daily summary."""
    now = {p.strip() for p in (settings.MAIL_ALERT_NOW or "").split(",") if p.strip()}
    return category in now or bool(now & set(tags or []))


def alert(category: str, thread_key: str, sender: str, subject: str, summary: str) -> Optional[int]:
    """One alert per conversation per day: a thread of five replies is one alert, not five."""
    from app.services.coordination import notify
    from app.services.coordination.autopilot import reviewers

    return notify.notify(f"mail_{category}", f"mail:{thread_key}:{notify.today().isoformat()}"[:200],
                         f"{_ALERT_TITLE.get(category, 'Urgent email')}: {subject[:150]}",
                         f"From {sender}.\n{summary or ''}", severity="critical" if category == "complaint" else "warning",
                         to=reviewers(), include_leadership=category == "complaint", link="/mail")


def for_summary(days: int = 3) -> List[str]:
    """Emails waiting on a person, one line per conversation, for the daily summary."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        rows = (db.query(MailLogDB).filter(MailLogDB.needs_action.is_(True), MailLogDB.handled_at.is_(None),
                                           MailLogDB.created_at >= since)
                .order_by(MailLogDB.received_at.desc().nullslast()).all())
    out, threads = [], set()
    for r in rows:
        if r.thread_key in threads:
            continue
        threads.add(r.thread_key)
        out.append(f"{r.category.replace('_', ' ')}: {r.subject[:90]} (from {r.sender})")
    return out

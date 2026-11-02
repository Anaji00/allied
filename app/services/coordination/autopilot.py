"""Autopilot: what Allied's system does on its own, on a timer.

Each play looks at the boards and the trackers and decides what needs doing.
Its mode (AUTOPILOT_MODES) says how far it may go:

  off      nothing
  suggest  work inside Allied (reading a board, telling staff) is done; anything
           that would leave the building (an email to a vendor or customer) is
           drafted into the confirm queue for a person to approve or cancel
  auto     drafts are carried out at once. Email still only goes out when a mail
           server is set up and sending is switched on; otherwise it is drafted.

No play writes to monday. The client refuses that anyway while MONDAY_READ_ONLY
is on, and monday automations are never used: monday holds the data only.

A dry run (`run(dry_run=True)`) says what each play would do and changes nothing.
"""

import csv
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import AssistantActionDB, NotificationDB, RfqDB, VendorJobDB
from app.services.coordination import notify as alerts
from app.services.coordination import rfqs, vendors, workdays

MODES = ("off", "suggest", "auto")
SYSTEM = {"email": "autopilot@allied", "role": "ADMIN", "name": "Allied autopilot"}
_lock = threading.Lock()
last_run: Dict[str, Dict[str, Any]] = {}


@dataclass
class Intent:
    """One thing a play wants done. `kind` is draft (outside Allied), alert (to staff) or work (internal)."""

    kind: str
    key: str
    summary: str
    to: List[str] = field(default_factory=list)          # who approves a draft / who is told
    tool: str = ""                                       # draft: the assistant tool that carries it out
    args: Dict[str, Any] = field(default_factory=dict)
    title: str = ""                                      # alert
    body: str = ""
    leadership: bool = False
    work: Optional[Callable[[], Any]] = None             # internal work, run unless dry run


def modes() -> Dict[str, str]:
    out = {name: play.default for name, play in PLAYS.items()}
    for part in (settings.AUTOPILOT_MODES or "").split(","):
        name, _, mode = part.partition("=")
        if name.strip() in PLAYS and mode.strip().lower() in MODES:
            out[name.strip()] = mode.strip().lower()
    return out


def _week(on: date) -> str:
    year, week, _ = on.isocalendar()
    return f"{year}w{week}"


def reviewers(owner: Optional[str] = None) -> List[str]:
    """Who confirms a draft. Never empty: a draft nobody can see would never be sent or cancelled."""
    if owner and (settings.AUTOPILOT_REVIEW_BY or "").strip().lower() == "owner":
        return [owner]
    named = [e.strip() for e in (settings.AUTOPILOT_REVIEWER_EMAILS or "").split(",") if e.strip()]
    return named or alerts.leadership() or [settings.ADMIN_EMAIL]


def _at(value: Any) -> Optional[datetime]:
    """A stored or board timestamp (datetime or ISO text, "Z" allowed) as UTC."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return workdays.as_utc(value)


def _labels(raw: str) -> set:
    return {p.strip().casefold() for p in (raw or "").split(",") if p.strip()}


# ---------------------------------------------------------------------------
# Vendor contacts
# ---------------------------------------------------------------------------


def vendor_email(name: str) -> Optional[str]:
    """The boards name vendors loosely: "HELEN" (a contact), "NIBROCK" (an id), "Duke"."""
    key = (name or "").strip().casefold()
    if not key:
        return None
    for part in (settings.VENDOR_ALIASES or "").split(","):
        alias, _, vendor = part.partition("=")
        if alias.strip().casefold() == key and vendor.strip():
            key = vendor.strip().casefold()
            break
    for part in (settings.VENDOR_EMAILS or "").split(","):
        n, _, email = part.partition("=")
        if n.strip().casefold() == key and "@" in email:
            return email.strip()
    return _vendor_master().get(key)


_master: Dict[str, Any] = {"stamp": None, "map": {}}


def _vendor_master() -> Dict[str, str]:
    """Vendor id, full name and mailbox (the part before the @) -> email. A name that
    points at two different vendors is left out rather than guessed."""
    import os

    path = settings.MASTER_VENDORS_PATH
    try:
        stamp = (str(path), os.path.getmtime(path))
    except OSError:
        return {}
    if _master["stamp"] != stamp:
        found: Dict[str, set] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip()
                if "@" not in email:
                    continue
                for name in ((row.get("vendor_id") or ""), (row.get("vendor_name") or ""), email.split("@")[0]):
                    if name.strip():
                        found.setdefault(name.strip().casefold(), set()).add(email)
        _master.update(stamp=stamp, map={k: next(iter(v)) for k, v in found.items() if len(v) == 1})
    return _master["map"]


# ---------------------------------------------------------------------------
# Plays
# ---------------------------------------------------------------------------


def play_rfq_board(on: date) -> List[Intent]:
    """Reads the monday Open RFQs board into the RFQ tracker, so its deadlines and chasers apply.

    With change checks on, the board is read when it changes (changes.py), so the
    hourly run skips it rather than reading it again."""
    board = (settings.MONDAY_RFQ_BOARD_ID or "").strip()
    if not board:
        return []
    from app.services.coordination.projects import _client

    client = _client()
    if not client.live:
        return []
    from app.services.monday_client import fetch_projects_board

    items = fetch_projects_board(client, int(board), with_subitems=False)
    if items:
        from app.services.coordination import boards

        boards.remember(board, items, client, "rfqs")
    return rfq_board_intents(items)


def _last_board_note(history: List[Dict[str, Any]]) -> Optional[str]:
    for entry in reversed(history or []):
        change = entry.get("change") or ""
        if change.startswith("monday board:"):
            return change.split(" (was ")[0]
    return None


def rfq_board_intents(items: List[Dict[str, Any]]) -> List[Intent]:
    rows = [read_rfq_item(i) for i in items]
    with SessionLocal() as db:
        linked = {r.monday_item_id: (r.id, r.status, _last_board_note(r.history))
                  for r in db.query(RfqDB).filter(RfqDB.monday_item_id.isnot(None))}
    out = []
    for row in rows:
        known = linked.get(row["monday_item_id"])
        if known and known[2] == _board_note(row):
            continue  # nothing new on the board since the last read; edits made here stand
        verb = "Update" if known else "Add"
        out.append(Intent("work", f"rfq-board:{row['monday_item_id']}:{_board_note(row)}",
                          f"{verb} RFQ from monday: {row['customer']} - {row['subject']} ({row['status']})",
                          work=lambda row=row: upsert_board_rfq(row)))
    on_board = {r["monday_item_id"] for r in rows}
    if rows:  # an empty answer is far likelier an API problem than an empty board
        for item_id, (rid, status, _) in linked.items():
            if item_id not in on_board and status in rfqs.OPEN:
                out.append(Intent("work", f"rfq-board-gone:{item_id}", f"Close RFQ {rid}: it left the monday board",
                                  work=lambda rid=rid: rfqs.update(rid, {"status": "NO_BID", "reason":
                                      "Left the monday Open RFQs board; the outcome was not recorded here"}, SYSTEM)))
    return out


def read_rfq_item(item: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination.projects import _cols, _due, _text

    cols = _cols(item)
    label = _text(cols.get(settings.RFQ_BOARD_STATUS_COLUMN_ID)).casefold()
    mapping = {}
    for part in (settings.RFQ_BOARD_STATUS_MAP or "").split(","):
        k, _, v = part.partition("=")
        if k.strip() and v.strip().upper() in rfqs.OPEN:
            mapping[k.strip().casefold()] = v.strip().upper()
    return {
        "monday_item_id": str(item["id"]), "subject": item.get("name") or "",
        "customer": _text(cols.get(settings.RFQ_BOARD_CUSTOMER_COLUMN_ID)),
        "vendor": _text(cols.get(settings.RFQ_BOARD_VENDORS_COLUMN_ID)) or None,
        "followed_up": _due(cols.get(settings.RFQ_BOARD_FOLLOWUP_COLUMN_ID)),
        "status": mapping.get(label, "ACKNOWLEDGED"), "label": label,
        "created_at": item.get("created_at"),
    }


def _board_note(row: Dict[str, Any]) -> str:
    return (f"monday board: {row['label'] or 'no status'}; follow-up {row['followed_up'] or 'none'}; "
            f"vendors {row['vendor'] or 'none'}")


def upsert_board_rfq(row: Dict[str, Any]) -> Dict[str, Any]:
    """Adds or refreshes the RFQ for one board item. The board is the record; nothing goes back to it.

    A first read imports the item as already acknowledged (it is on the board, so
    someone has it) with its quote deadline counted from now, so connecting the
    board doesn't raise an "overdue" alert for every open RFQ. Later board changes
    go through rfqs.update, which keeps the pause and vendor-wait clocks right and
    raises the usual events."""
    now = workdays.now()
    with SessionLocal() as db:
        existing = db.query(RfqDB).filter(RfqDB.monday_item_id == row["monday_item_id"]).first()
        rid = existing.id if existing else None
    if rid is None:
        created = rfqs.create({"customer": row["customer"], "subject": row["subject"], "vendor": row["vendor"],
                               "received_at": _at(row["created_at"]), "monday_item_id": row["monday_item_id"],
                               "notes": "Read from the monday Open RFQs board."}, SYSTEM, source="monday", announce=False)
        rid = created["id"]
        with SessionLocal() as db:
            r = db.get(RfqDB, rid)
            r.status, r.acknowledged_at = row["status"], now
            r.ack_due_at, r.due_at = now, workdays.add_days(now, rfqs._quote_days(r.complexity))
            r.waiting_since = now if r.status == "WAITING_VENDOR" else None
            r.paused_at = now if r.status == "CLARIFYING" else None
            rfqs._log(r, SYSTEM["email"], _board_note(row))
            db.commit()
            return {"id": rid, "status": r.status}
    with SessionLocal() as db:
        r = db.get(RfqDB, rid)
        status, old_note = r.status, _last_board_note(r.history)
    old_label = (old_note or "").split(";")[0]
    if status in rfqs.OPEN and old_label != _board_note(row).split(";")[0] and row["status"] != status:
        try:
            rfqs.update(rid, {"status": row["status"], "vendor": row["vendor"]}, SYSTEM)
        except ValueError as exc:
            logger.info(f"RFQ {rid}: board status not applied ({exc})")
    followed = _at(row["followed_up"] + "T17:00:00+00:00") if row["followed_up"] else None
    with SessionLocal() as db:
        r = db.get(RfqDB, rid)
        r.vendor = row["vendor"] or r.vendor
        r.customer, r.subject = row["customer"] or r.customer, row["subject"] or r.subject
        if r.status == "WAITING_VENDOR" and followed and followed > (workdays.as_utc(r.waiting_since) or followed - timedelta(1)):
            r.waiting_since = followed  # the vendor was chased again; its clock restarts
        rfqs._log(r, SYSTEM["email"], _board_note(row))
        db.commit()
        return {"id": rid, "status": r.status}


def play_vendor_chaser(on: date) -> List[Intent]:
    """Orders not shipped their wait after the PO: the first follow-up, once per PO. Later ones
    are staff's call, from the board's "Follow up now"."""
    return [i for i in (vendor_chase(j, on) for j in vendors.overview(on)["jobs"] if (j.get("follow_up") or {}).get("due")) if i]


def vendor_chase_body(j: Dict[str, Any], on: date) -> str:
    what = j.get("description") or "your order"
    po = j.get("po_number") or str(j["id"])
    sent = f"We sent the PO on {j['ordered_on']} and have" if j.get("ordered_on") else "We have"
    return (f"Hello,\n\nCould you confirm the ship date for Allied PO {po} ({what})? "
            f"{sent} not seen it ship yet.\n\nPlease reply with the date it will ship, and send "
            f"photos of the finished parts and the crating before it goes.\n\nThank you,\nAllied Welding Systems")


def chaser_note(j: Dict[str, Any], on: date) -> str:
    """The line the vendor-late alert ends with: when the vendor is followed up, or here is what to send."""
    if vendor_email(j["vendor"]):
        first = vendors.first_follow_up(j)
        if first is None:
            return "There is no PO date, so the vendor isn't followed up automatically; use Follow up now on the board."
        if first > on:
            return f"The vendor is followed up on {first.isoformat()} if it hasn't shipped by then."
        return "A follow-up to the vendor is drafted for approval; after it, use Follow up now on the board."
    return (f"There is no email on file for {j['vendor']}, so nothing was drafted. Suggested message:\n\n"
            + vendor_chase_body(j, on))


def vendor_chase(j: Dict[str, Any], on: date, manual: bool = False) -> Optional[Intent]:
    """A follow-up to an order's vendor, or None with no email on file (the vendor-late alert then
    carries the suggested message) or before the order's wait after its PO is over.

    The automatic one is keyed per PO, so the timer and the "turned late" event make it once between
    them; one asked for from the board (manual) is keyed per day, so a double press makes one."""
    # One rule, worked out once in vendors._follow_up (overview); a bare job is worked out here.
    follow = j.get("follow_up") or vendors._follow_up(j, {}, on) or {}
    if not follow.get("first") or (not manual and (follow["first"] > on.isoformat() or follow.get("started"))):
        return None  # not due yet, or staff already followed this PO up
    email = vendor_email(j["vendor"])
    if not email:
        return None
    po = j.get("po_number") or str(j["id"])
    why = list(j.get("reasons") or []) + [
        f"Customer needs it shipped by {j['need_by']} ({j.get('need_source')})" if j.get("need_by") else "",
        f"PO sent {j['ordered_on']}; follow up after {follow['wait_days']} days ({follow['wait_basis']})",
        f"Last chaser sent {follow['last']}" if follow.get("last") else "No chaser sent yet",
        "Asked for from the board" if manual else "",
        f"Order: {j.get('description') or '-'}, PO {po}, {j['vendor']}"]
    waited = (on - date.fromisoformat(j["ordered_on"])).days
    key = f"vendor-followup:{j['id']}:{on.isoformat() if manual else j['ordered_on']}"
    return Intent("draft", key, f"Ask {j['vendor']} when PO {po} ships (PO sent {waited} days ago)",
                  to=reviewers(j.get("owner_email")), tool="send_email",
                  args={"to": email, "subject": f"Allied PO {po}: ship date?", "body": vendor_chase_body(j, on),
                        "_job_id": j["id"], "_po_date": j["ordered_on"], "_manual": manual,
                        "_why": [w for w in why if w]})


def play_rfq_vendor_chaser(on: date) -> List[Intent]:
    """RFQs waiting on a vendor's price too long: ask each bidding vendor for it."""
    out = []
    for r in rfqs.list_rfqs(open_only=True):
        if r["status"] != "WAITING_VENDOR" or not r.get("waiting_since"):
            continue
        since = _at(r["waiting_since"])
        if workdays.days_between(since, workdays.now()) < settings.RFQ_VENDOR_CHASE_DAYS:
            continue
        for name in [n.strip() for n in (r.get("vendor") or "").split(",") if n.strip()]:
            email = vendor_email(name)
            body = (f"Hello {name},\n\nWe are still waiting on your price for {r['subject'] or 'our request'} "
                    f"(our ref {r['number']}), asked on {since.date().isoformat()}. Our customer is waiting on us. "
                    f"Could you send the price and lead time, or tell us when you will have it?\n\n"
                    f"Thank you,\nAllied Welding Systems")
            if email:  # with none on file, the RFQ's own "chase vendor price" alert names the contact
                why = [f"{r['number']} for {r['customer']}: waiting on vendor prices since {since.date().isoformat()}",
                       f"Bidding vendors: {r.get('vendor')}",
                       f"Quote due to the customer {(r.get('effective_due_at') or r.get('due_at') or '')[:10]}"]
                out.append(Intent("draft", f"rfq-vendor-chase:{r['id']}:{name.casefold()}:{_week(on)}",
                                  f"Ask {name} for their price on {r['number']} ({r['customer']})",
                                  to=reviewers(r.get("owner_email")), tool="send_email",
                                  args={"to": email, "subject": f"Price for {r['subject'] or r['number']}?", "body": body,
                                        "_why": why}))
    return out


def play_quote_followup(on: date) -> List[Intent]:
    """Quotes sent and not answered: a follow-up to the customer after 2 and 7 working days."""
    steps = sorted({int(d) for d in (settings.QUOTE_FOLLOWUP_DAYS or "").split(",") if d.strip().isdigit()})
    out = []
    with SessionLocal() as db:
        sent = [rfqs._as_dict(r) for r in db.query(RfqDB).filter(RfqDB.status == "QUOTE_SENT", RfqDB.quoted_at.isnot(None))]
    for r in sent:
        waited = workdays.days_between(_at(r["quoted_at"]), workdays.now())
        due = [s for s in steps if waited >= s]
        # Quotes long past the last step were followed up by hand, or are dead; don't start now.
        if not due or not r.get("contact_email") or waited > (steps[-1] if steps else 0) + 5:
            continue
        step = due[-1]
        quote = f"quote {r['quote_number']}" if r.get("quote_number") else "our quote"
        body = (f"Hello,\n\nI wanted to check you received {quote} for {r['subject'] or 'your request'}, sent "
                f"{_at(r['quoted_at']).date().isoformat()}. Is there anything you need from us to "
                f"move ahead, or any question on price or lead time?\n\nThank you,\nAllied Welding Systems")
        out.append(Intent("draft", f"quote-follow:{r['id']}:{step}",
                          f"Follow up {r['customer'] or r['contact_email']} on {r['number']} (sent {int(waited)} working days ago)",
                          to=reviewers(r.get("owner_email")), tool="send_email",
                          args={"to": r["contact_email"], "subject": f"Following up: {quote}", "body": body}))
    return out


def play_orders_watch(on: date) -> List[Intent]:
    """Once a week: orders still owing payment, and vendor photos waiting for approval."""
    board = (settings.MONDAY_VENDOR_BOARD_ID or "").strip()
    if not board:
        return []
    from app.services.coordination.projects import _client, _cols, _text
    from app.services.monday_client import fetch_projects_board

    client = _client()
    if not client.live:
        return []
    groups, paid, photos = _labels(settings.ORDERS_PAYMENT_GROUPS), _labels(settings.ORDERS_PAID_LABELS), _labels(settings.ORDERS_PHOTO_LABELS)
    # The vendor sync reads this board every hour or on each change; use that read when it is recent.
    cached = vendors.last_board
    fresh = cached["board"] == board and time.monotonic() - cached["at"] < 2 * max(300, settings.AUTOPILOT_SECONDS)
    items = cached["items"] if fresh else fetch_projects_board(client, int(board), with_subitems=False)
    with SessionLocal() as db:
        job_ids = {j.monday_item_id: j.id for j in db.query(VendorJobDB).filter(VendorJobDB.board_id == board)}
        told = {k for (k,) in db.query(NotificationDB.dedup_key).filter(NotificationDB.dedup_key.like("autopilot:order-photos:%"))}
    owing, waiting = [], []
    for item in items:
        cols = _cols(item)
        line = " - ".join(x for x in (item.get("name"), _text(cols.get(settings.VENDOR_DESCRIPTION_COLUMN_ID)),
                                       f"PO {_text(cols.get(settings.VENDOR_PO_COLUMN_ID))}"
                                       if _text(cols.get(settings.VENDOR_PO_COLUMN_ID)) else "") if x)
        pay = _text(cols.get(settings.ORDERS_PAYMENT_STATUS_COLUMN_ID))
        if ((item.get("group") or {}).get("title") or "").casefold() in groups and pay.casefold() not in paid:
            owing.append(f"- {line}: {pay or 'payment status not set'}")
        status = _text(cols.get(settings.VENDOR_STATUS_COLUMN_ID))
        jid = job_ids.get(str(item["id"]))
        if status.casefold() in photos and f"autopilot:order-photos:{jid}:{status}" not in told:  # not already told on the change
            waiting.append(f"- {line}: {_text(cols.get(settings.VENDOR_STATUS_COLUMN_ID))}")
    out = []
    if owing:
        out.append(Intent("alert", f"orders-owing:{_week(on)}", f"{len(owing)} order(s) waiting on payment",
                          to=reviewers(), title=f"{len(owing)} order(s) waiting on customer payment",
                          body="Shipped orders not yet paid in full:\n" + "\n".join(owing[:60])))
    if waiting:
        out.append(Intent("alert", f"orders-photos:{_week(on)}", f"{len(waiting)} order(s) waiting on vendor photos",
                          to=reviewers(), title=f"{len(waiting)} order(s) waiting on vendor photos",
                          body="Photos to chase or approve before these ship:\n" + "\n".join(waiting)))
    return out


@dataclass
class Play:
    run: Callable[[date], List[Intent]]
    default: str
    about: str


PLAYS: Dict[str, Play] = {
    "rfq_board": Play(play_rfq_board, "auto", "Reads the monday Open RFQs board into the RFQ tracker."),
    "orders_watch": Play(play_orders_watch, "auto", "Weekly list of unpaid orders and vendor photos waiting."),
    "vendor_chaser": Play(play_vendor_chaser, "suggest", "Follows up vendors once an order hasn't shipped its wait after the PO."),
    "rfq_vendor_chaser": Play(play_rfq_vendor_chaser, "suggest", "Asks bidding vendors for prices that are overdue."),
    "quote_followup": Play(play_quote_followup, "suggest", "Follows up customers on quotes they haven't answered."),
}


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _draft(play: str, intent: Intent, auto: bool) -> str:
    """Puts a draft in the confirm queue of the first person in `to`, or carries it out (auto)."""
    from sqlalchemy.exc import IntegrityError

    from app.services.outbound_email_service import outbound_email_service

    action_id = uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{intent.key}").hex
    owner = (intent.to or reviewers())[0]
    # Auto only carries out what can really go: with mail off an email would only be drafted
    # and then never sent, so it waits in the queue for a person instead.
    auto = auto and (intent.tool != "send_email" or outbound_email_service.send_enabled)
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, action_id)
        stale = row is not None and (row.status == "EXPIRED" or (
            row.status == "PENDING" and workdays.as_utc(row.created_at) < workdays.now() - timedelta(hours=settings.AUTOPILOT_DRAFT_HOURS)))
        if row is not None and row.status == "FAILED" and not auto:
            # A person sent it and the mail server refused (or it errored): offer it again so
            # someone can retry or cancel it, rather than leave it failed and forgotten.
            won = (db.query(AssistantActionDB)
                   .filter(AssistantActionDB.id == action_id, AssistantActionDB.status == "FAILED")
                   .update({AssistantActionDB.status: "PENDING", AssistantActionDB.created_at: workdays.now(),
                            AssistantActionDB.decided_at: None}, synchronize_session=False))
            db.commit()
            return "drafted" if won else "already"
        if stale and not auto:
            # Nobody acted on it in time: offer it again rather than leave it hidden for good.
            row.status, row.created_at, row.decided_at = "PENDING", workdays.now(), None
            db.commit()
            return "drafted"
        if row is not None and not stale and not (auto and row.status == "FAILED"):
            return "already"
        if row is None:
            db.add(AssistantActionDB(id=action_id, user_email=owner, user_role="AUTOPILOT", tool=intent.tool,
                                     args={**intent.args, "_play": play}, summary=intent.summary,
                                     status="RUNNING" if auto else "PENDING"))
        else:
            # A failed or expired one is tried again: claimed in one conditional update, so two
            # runs at the same moment can't both send it.
            won = (db.query(AssistantActionDB)
                   .filter(AssistantActionDB.id == action_id, AssistantActionDB.status == row.status)
                   .update({AssistantActionDB.status: "RUNNING"}, synchronize_session=False))
            if won != 1:
                db.rollback()
                return "already"
        created = row is None
        try:
            db.commit()
        except IntegrityError:  # the timer and an event made the same draft at the same moment
            db.rollback()
            return "already"
    if not auto:
        if created:
            from app.services.assistant import quick

            quick.push_draft(action_id, intent.summary, owner)  # Send / Review buttons on the reviewer's phone
        return "drafted"
    from app.services.assistant.tools import TOOLS

    status, result = "DONE", None
    try:
        result = TOOLS[intent.tool].run(dict(intent.args), SYSTEM)
        if isinstance(result, dict) and result.get("status") == "FAILED":
            status = "FAILED"
    except Exception as exc:  # noqa: BLE001 - recorded on the action and in the log
        status, result = "FAILED", {"error": str(exc)[:300]}
    if status == "DONE":
        after_done({**intent.args, "_play": play}, SYSTEM, result)
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, action_id)
        row.status, row.decided_at = status, workdays.now()
        row.result = json.loads(json.dumps(result if isinstance(result, dict) else {"done": str(result)}, default=str))
        db.commit()
    audit_log(action="AUTOPILOT", resource_type="ACTION", resource_id=action_id, actor_email=SYSTEM["email"],
              actor_role="SYSTEM", detail=f"{play}: {intent.summary} -> {status.lower()}")
    return status.lower()


def after_done(args: Dict[str, Any], user: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> None:
    """What a carried-out draft settles here: an acknowledgement really sent means the RFQ is
    acknowledged. One only drafted (mail off) or redirected to a test address reached nobody,
    so the RFQ stays NEW and its "not acknowledged" alert still fires."""
    if (result or {}).get("status") not in (None, "SENT"):
        return
    if args.get("_play") == "rfq_acknowledge" and args.get("_rfq_id"):
        try:
            if rfqs.get(args["_rfq_id"])["status"] == "NEW":
                rfqs.update(args["_rfq_id"], {"status": "ACKNOWLEDGED", "note": "acknowledgement sent"}, user)
        except (LookupError, ValueError) as exc:
            logger.info(f"Acknowledgement sent but RFQ {args['_rfq_id']} not updated: {exc}")


def carry_out(name: str, mode: str, intents: List[Intent], dry_run: bool = False) -> List[Dict[str, Any]]:
    """Does what a play or a reaction decided, as far as its mode allows. One failing step
    is recorded and the rest still run."""
    did = []
    for it in intents:
        try:
            outcome = _one(name, mode, it, dry_run)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Autopilot {name}: {it.summary} failed: {exc}")
            outcome = f"failed: {exc}"[:200]
        did.append({"what": it.summary, "outcome": outcome, "to": it.to[:1]})
    return did


def _one(name: str, mode: str, it: Intent, dry_run: bool) -> str:
    if dry_run:
        outcome = {"draft": "would draft" if mode == "suggest" else "would send",
                   "alert": "would tell staff", "work": "would do"}[it.kind]
    elif it.kind == "draft":
        outcome = _draft(name, it, auto=mode == "auto")
    elif it.kind == "alert":
        nid = alerts.notify(f"autopilot_{name}", f"autopilot:{it.key}", it.title, it.body,
                            to=it.to, include_leadership=it.leadership, link="/automation")
        outcome = "told staff" if nid else "already told"
    else:
        it.work()
        outcome = "done"
    return outcome


def run(play: Optional[str] = None, dry_run: bool = False, on: Optional[date] = None) -> Dict[str, Any]:
    """Runs every play (or one), each in its mode. Returns what each did, or would do."""
    on = on or alerts.today()
    chosen = modes()
    names = [play] if play else list(PLAYS)
    if play and play not in PLAYS:
        raise LookupError(f"No play called {play}.")
    report: Dict[str, Any] = {}
    with _lock:
        for name in names:
            mode = chosen[name]
            entry: Dict[str, Any] = {"mode": mode, "about": PLAYS[name].about, "did": [], "at": alerts.now_local().isoformat()}
            report[name] = entry
            if mode == "off" or (not settings.AUTOPILOT_ENABLED and not dry_run):
                entry["skipped"] = "off" if mode == "off" else "autopilot is switched off"
                continue
            if name == "rfq_board" and not play and settings.MONDAY_CHANGE_CHECK_SECONDS > 0:
                entry["skipped"] = "read when the board changes"
                continue
            try:
                intents = PLAYS[name].run(on)
            except Exception as exc:  # noqa: BLE001 - one play failing must not stop the rest
                entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
                logger.error(f"Autopilot {name} failed: {exc}")
                continue
            entry["did"] = carry_out(name, mode, intents, dry_run)
            if not dry_run:
                last_run[name] = entry
    if not dry_run:
        tell_reviewers([d for e in report.values() for d in e["did"]])
    return {"dry_run": dry_run, "modes": chosen, "plays": report}


def tell_reviewers(did: List[Dict[str, Any]]) -> None:
    """One alert per person per day when new drafts are waiting for them."""
    new: Dict[str, int] = {}
    for d in did:
        if d["outcome"] == "drafted" and d["to"]:
            new[d["to"][0]] = new.get(d["to"][0], 0) + 1
    for email, n in new.items():
        alerts.notify("autopilot_drafts", f"autopilot-drafts:{email}:{alerts.today()}",
                      f"{n} message(s) drafted for you to check",
                      "Allied's autopilot drafted messages that need your OK before they go out. "
                      "Open the assistant to read, send or cancel each one.", to=[email], link="/automation")


def drafts_waiting() -> List[str]:
    """Autopilot drafts nobody has sent or cancelled yet, for the daily summary."""
    cutoff = workdays.now() - timedelta(hours=settings.AUTOPILOT_DRAFT_HOURS)
    with SessionLocal() as db:
        rows = (db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT",
                                                   AssistantActionDB.status == "PENDING")
                .order_by(AssistantActionDB.created_at).all())
        return [f"{r.summary} (for {r.user_email})" for r in rows if workdays.as_utc(r.created_at) >= cutoff]


def scorecard(days: int = 90) -> Dict[str, Any]:
    """For each play, reaction and recipe: how many drafts people sent, cancelled or let lapse,
    and what that suggests. A draft nearly always sent is ready for auto; one mostly
    cancelled is noise to fix or switch off."""
    since = workdays.now() - timedelta(days=days)
    rows: Dict[str, Dict[str, int]] = {}
    with SessionLocal() as db:
        for r in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT",
                                                    AssistantActionDB.created_at >= since):
            name = (r.args or {}).get("_play") or "unknown"
            c = rows.setdefault(name, {"drafted": 0, "sent": 0, "cancelled": 0, "lapsed": 0, "failed": 0, "waiting": 0})
            c["drafted"] += 1
            c[{"DONE": "sent", "CANCELLED": "cancelled", "REJECTED": "cancelled", "EXPIRED": "lapsed",
               "FAILED": "failed", "PENDING": "waiting", "RUNNING": "waiting"}.get(r.status, "waiting")] += 1
    from app.services.coordination import events

    current = {**modes(), **events.modes()}
    out = []
    for name, c in sorted(rows.items(), key=lambda kv: -kv[1]["drafted"]):
        decided = c["sent"] + c["cancelled"] + c["lapsed"]
        rate = round(c["sent"] / decided, 2) if decided else None
        mode = current.get(name, "per recipe" if name.startswith("recipe_") else None)
        if decided < settings.AUTOPILOT_SCORECARD_MIN:
            advice = f"Too few decided yet ({decided}) to judge."
        elif rate >= 0.9 and mode == "suggest":
            advice = "People send nearly every draft: consider auto."
        elif rate < 0.4:
            advice = "Most drafts are cancelled or left: change its wording or timing, or switch it off."
        elif c["lapsed"] > c["sent"]:
            advice = "Drafts lapse unread: check who reviews them."
        else:
            advice = "Working as set."
        out.append({"name": name, "mode": mode, **c, "send_rate": rate, "advice": advice})
    return {"days": days, "automations": out, "alerts": alert_scorecard(days)}


def alert_scorecard(days: int = 90, ignored_after_days: int = 3) -> List[Dict[str, Any]]:
    """For each kind of alert: how many were raised, how many a person marked handled, how many
    cleared on their own (the problem went away), and how many sat unread. An alert nobody acts
    on is noise; the advice says so."""
    since = workdays.now() - timedelta(days=days)
    stale = workdays.now() - timedelta(days=ignored_after_days)
    rows: Dict[str, Dict[str, int]] = {}
    with SessionLocal() as db:
        for kind, created, acked_at, acked_by in db.query(
                NotificationDB.kind, NotificationDB.created_at, NotificationDB.acknowledged_at,
                NotificationDB.acknowledged_by).filter(NotificationDB.created_at >= since):
            created = workdays.as_utc(created)
            c = rows.setdefault(kind, {"raised": 0, "handled": 0, "cleared": 0, "ignored": 0, "open": 0})
            c["raised"] += 1
            if acked_at is None:
                c["ignored" if created < stale else "open"] += 1
            elif acked_by == alerts.RESOLVED_BY:
                c["cleared"] += 1
            else:
                c["handled"] += 1
    out = []
    for kind, c in sorted(rows.items(), key=lambda kv: -kv[1]["raised"]):
        judged = c["handled"] + c["ignored"]
        rate = round(c["handled"] / judged, 2) if judged else None
        if judged < settings.AUTOPILOT_SCORECARD_MIN:
            advice = f"Too few handled or left yet ({judged}) to judge."
        elif rate < 0.3:
            advice = (f"Mostly left unread for {ignored_after_days}+ days: send it to someone who acts on it, "
                      "make it rarer (a longer wait, a daily list), or switch it off.")
        elif c["cleared"] > c["handled"] + c["ignored"]:
            advice = "Usually clears on its own before anyone acts: consider waiting longer before raising it."
        else:
            advice = "People act on it."
        out.append({"kind": kind, **c, "handled_rate": rate, "advice": advice})
    return out


def status() -> Dict[str, Any]:
    chosen = modes()
    waiting = len(drafts_waiting())
    from app.services.coordination import events

    return {"enabled": settings.AUTOPILOT_ENABLED, "every_seconds": settings.AUTOPILOT_SECONDS,
            "drafts_waiting": waiting, "events": events.status(),
            "plays": [{"name": n, "mode": chosen[n], "about": p.about, "last": last_run.get(n)} for n, p in PLAYS.items()]}

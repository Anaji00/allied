"""One way to tell staff something: the in-app inbox, then email, text or push.

Every alert is stored first (the inbox is the record), then delivered. Rules
that keep people reading them rather than filtering them out:

- Deduplication: an alert key is sent once. It can fire again only after the
  system closed it (the condition cleared) and the cooldown has passed.
- Severity decides the channel: info -> inbox + email; warning adds phone
  push; critical adds a text message. People choose their own channels.
- Quiet hours: texts and pushes wait until morning unless critical, then
  go out (release_held).
- Leadership is told about escalations and gets one daily summary, instead of
  every individual reminder.
"""

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import String, cast, func, or_

from app.config import settings
from app.core.database import SessionLocal
from app.core import status_words
from app.core.logging import logger
from app.models.db_models import NotificationDB, StaffContactDB

SEVERITIES = ("info", "warning", "critical")
RESOLVED_BY = "system: resolved"
HELD = "held for quiet hours"
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")
_lock = threading.Lock()


def now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.BUSINESS_TZ))


def today() -> date:
    return now_local().date()


def _quiet(at: Optional[datetime] = None) -> bool:
    at = at or now_local()
    if settings.QUIET_WEEKENDS and at.weekday() >= 5:
        return True
    hour = at.hour
    start, end = settings.QUIET_HOURS_START, settings.QUIET_HOURS_END
    return (hour >= start or hour < end) if start > end else (start <= hour < end)


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------


_contacts_cache: Dict[str, Any] = {"at": 0.0, "value": None}


def forget_contacts() -> None:
    """Drops the cached contact list, so a change on the Alerts screen applies at once."""
    _contacts_cache["value"] = None


def contacts() -> Dict[str, Dict[str, Any]]:
    """Who gets told, and how. Read at most every 30 seconds: one check pass can raise hundreds
    of alerts, and each needs the list."""
    import time as _time

    hit = _contacts_cache["value"]
    if hit is not None and _time.monotonic() - _contacts_cache["at"] < 30:
        return hit
    with SessionLocal() as db:
        value = {
            c.email.lower(): {
                "email": c.email, "name": c.name, "role_tag": c.role_tag, "phone": c.phone,
                "channels": list(c.channels or ["email"]), "daily_summary": c.daily_summary, "active": c.active,
            }
            for c in db.query(StaffContactDB).all()
        }
    _contacts_cache.update(at=_time.monotonic(), value=value)
    return value


PLACEHOLDER_DOMAIN = "placeholder.invalid"      # reserved TLD (RFC 2606): a stand-in for a person whose email is not known yet


def is_placeholder(email: Optional[str]) -> bool:
    return bool(email) and str(email).strip().lower().endswith("@" + PLACEHOLDER_DOMAIN)


def leadership() -> List[str]:
    people = [c["email"] for c in contacts().values() if c["active"] and c["role_tag"] == "leadership"]
    people += [e.strip() for e in (settings.LEADERSHIP_EMAILS or "").split(",") if e.strip()]
    return _unique(people)


def summary_recipients() -> List[str]:
    people = [c["email"] for c in contacts().values() if c["active"] and c["daily_summary"]]
    return _unique(people or leadership())


def _unique(emails: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for e in emails:
        key = (e or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(e.strip())
    return out


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def notify(
    kind: str,
    key: str,
    title: str,
    body: str,
    *,
    severity: str = "info",
    to: Iterable[str] = (),
    include_leadership: bool = False,
    link: Optional[str] = None,
    project_item_id: Optional[str] = None,
    wait: bool = False,
    replaces: Iterable[str] = (),
) -> Optional[int]:
    """Stores and delivers an alert. Returns its id, or None if it was a repeat.

    replaces: key prefixes of earlier alerts about the same problem (the same drawing on an
    older due date, the same order on an older promise). Raising this one closes them, so a
    moving deadline leaves one live alert, not a pile. If the problem moves back to a date
    already alerted, that alert simply reopens; nobody is told twice."""
    severity = severity if severity in SEVERITIES else "info"
    recipients = [e for e in _unique(list(to) + (leadership() if include_leadership else [])) if not is_placeholder(e)]
    if not recipients:
        # Never drop an alert because a monday column was empty or its owner has no real email yet; with no leadership
        # configured either, the admin is told, so an alert is never left with nobody.
        recipients = leadership() or [settings.ADMIN_EMAIL]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.NOTIFY_COOLDOWN_HOURS)
    with _lock, SessionLocal() as db:
        # Still open, handled by a person, or recent: all count as already said.
        repeat = (
            db.query(NotificationDB.id, NotificationDB.acknowledged_at, NotificationDB.acknowledged_by)
            .filter(
                NotificationDB.dedup_key == key[:200],
                or_(
                    NotificationDB.acknowledged_at.is_(None),
                    NotificationDB.acknowledged_by != RESOLVED_BY,
                    NotificationDB.created_at >= cutoff,
                ),
            )
            .first()
        )
        family = [p for p in replaces if p]
        if repeat:
            if family:
                changed = 0
                if repeat[1] is not None and repeat[2] == RESOLVED_BY:
                    changed += db.query(NotificationDB).filter(NotificationDB.id == repeat[0]).update(
                        {NotificationDB.acknowledged_at: None, NotificationDB.acknowledged_by: None},
                        synchronize_session=False)                   # the date came back: live again
                # Older siblings still open (left from before, or a race) are closed too; the usual
                # repeat finds none, and then nothing is written.
                changed += _close_family(db, family, key)
                if changed:
                    db.commit()
            return None
        _close_family(db, family, key)
        row = NotificationDB(
            dedup_key=key[:200], kind=kind, severity=severity, title=title[:300], body=body,
            link=link, project_item_id=project_item_id, recipients=recipients, deliveries={},
        )
        db.add(row)
        db.commit()
        nid = row.id
    future = _pool.submit(_deliver, nid)
    future.add_done_callback(_log_failure)
    if wait:
        future.result(timeout=60)
    return nid


def _close_family(db, prefixes: List[str], keep: str) -> int:
    """Closes the open alerts under these prefixes, except `keep` (the one being raised)."""
    if not prefixes:
        return 0
    return (db.query(NotificationDB)
            .filter(NotificationDB.acknowledged_at.is_(None), NotificationDB.dedup_key != keep[:200],
                    or_(*[NotificationDB.dedup_key.like(f"{p}%") for p in prefixes]))
            .update({NotificationDB.acknowledged_at: datetime.now(timezone.utc),
                     NotificationDB.acknowledged_by: RESOLVED_BY}, synchronize_session=False))


def _log_failure(future) -> None:
    exc = future.exception()
    if exc is not None:
        logger.error("Alert delivery failed", exc_info=exc)


# Notices that come often and are answered in the app: in-app and phone push only, no email
# (the daily summary lists what is waiting).
_APP_AND_PHONE_ONLY = {"autopilot_drafts"}


def _deliver(nid: int) -> None:
    with SessionLocal() as db:
        row = db.get(NotificationDB, nid)
        if row is None:
            return
        n = {"title": row.title, "body": row.body, "severity": row.severity, "link": row.link,
             "recipients": list(row.recipients or []), "kind": row.kind}
    people = contacts()
    deliveries: Dict[str, Any] = {}
    quiet = _quiet() and n["severity"] != "critical"
    url = _url(n)
    wants_push = False

    for email in n["recipients"]:
        contact = people.get(email.lower(), {"channels": ["email"], "phone": None, "active": True})
        if not contact.get("active", True):
            continue
        channels = set(contact.get("channels") or ["email"])
        result: Dict[str, str] = {}
        # The person's own choices (when they saved any): which channels this group may use, their own quiet hours, and instant or digest. The alert itself is always in their in-app list; a critical alert
        # ignores quiet hours and digest. With nothing saved, nothing here changes.
        mine = None
        try:
            from app.services import my_prefs

            mine = my_prefs.saved_prefs(email)
            if mine is not None:
                channels &= set(mine["groups"].get(my_prefs.pref_group(n["kind"]), ["app", "email"]))
        except Exception as exc:  # noqa: BLE001 - a problem reading choices never stops an alert
            logger.warning(f"Notification choices not read for {email}: {type(exc).__name__}")
        outward_off = ""
        if mine is not None and n["severity"] != "critical":
            from app.services import my_prefs as _mp

            if _mp.in_quiet_hours(mine, now_local()):
                outward_off = "skipped: your quiet hours (it is in your alerts)"
            elif mine.get("delivery") == "digest":
                outward_off = "skipped: you chose the daily digest (it is in your alerts)"
        if "email" in channels and n["kind"] not in _APP_AND_PHONE_ONLY:
            result["email"] = outward_off or _email(email, n, url)
        if "push" in channels and n["severity"] in ("warning", "critical") and not outward_off:
            wants_push = True
        if "sms" in channels and n["severity"] == "critical" and contact.get("phone"):
            result["sms"] = HELD if quiet else _sms(contact["phone"], n, url)
        deliveries[email] = result

    # The ntfy topic is shared, so one push reaches every subscriber once.
    if wants_push:
        deliveries["_push"] = HELD if quiet else _push(n, url)

    if n["severity"] in ("warning", "critical") and n["kind"] != "test":
        try:
            from app.services.outbound_webhook_service import outbound_webhooks

            deliveries["_team_chat"] = outbound_webhooks.send_sync(
                title=n["title"], message=f"{n['body']}\n{url}", severity=n["severity"].upper()
            )
        except Exception as exc:  # noqa: BLE001
            deliveries["_team_chat"] = f"failed: {exc}"

    with SessionLocal() as db:
        row = db.get(NotificationDB, nid)
        if row is not None:
            row.deliveries = deliveries
            db.commit()


def _url(n: Dict[str, Any]) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}{n['link']}" if n.get("link") else settings.APP_BASE_URL


def release_held(max_age_hours: int = 72) -> int:
    """Sends the pushes and texts that were held overnight, once quiet hours are over.

    Alerts already acknowledged or resolved are dropped instead: nobody needs a
    text at 7am about something that was dealt with at 10pm.
    """
    if _quiet():
        return 0
    since = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    with SessionLocal() as db:
        rows = (
            db.query(NotificationDB)
            .filter(NotificationDB.created_at >= since, cast(NotificationDB.deliveries, String).like(f"%{HELD}%"))
            .all()
        )
        pending = [(r.id, r.acknowledged_at is not None, {
            "title": r.title, "body": r.body, "severity": r.severity, "link": r.link, "kind": r.kind,
        }, json.loads(json.dumps(r.deliveries or {}))) for r in rows]
    if not pending:
        return 0
    people = contacts()
    sent = 0
    for nid, closed, n, deliveries in pending:
        url = _url(n)
        for target, result in deliveries.items():
            if target == "_push" and result == HELD:
                deliveries["_push"] = "dropped (already handled)" if closed else _push(n, url)
                sent += 0 if closed else 1
            elif isinstance(result, dict) and result.get("sms") == HELD:
                phone = (people.get(target.lower()) or {}).get("phone")
                result["sms"] = "dropped (already handled)" if closed or not phone else _sms(phone, n, url)
                sent += 0 if closed or not phone else 1
        with SessionLocal() as db:
            row = db.get(NotificationDB, nid)
            if row is not None:
                row.deliveries = deliveries
                db.commit()
    return sent


_SUBJECT_TAG = {"info": "", "warning": "[Behind] ", "critical": "[URGENT] "}


def _email(address: str, n: Dict[str, Any], url: str) -> str:
    from app.services.outbound_email_service import outbound_email_service

    body = f"{n['body']}\n\nOpen: {url}\n\n— Allied Operations (automatic message)"
    result = outbound_email_service.send(
        to=address, subject=f"{_SUBJECT_TAG[n['severity']]}{n['title']}", body=body,
        category="STAFF_ALERT", reference=n["kind"],
    )
    return str(result.get("status", "recorded")).lower()


def _push(n: Dict[str, Any], url: str) -> str:
    if not (settings.NTFY_URL and settings.NTFY_TOPIC):
        return "off"
    # Published as JSON: HTTP headers must be ASCII, so a title with an accent or a
    # dash (common in project names) used to raise before anything was sent.
    payload = {
        "topic": settings.NTFY_TOPIC, "title": n["title"][:200], "message": n["body"][:3500],
        "priority": 5 if n["severity"] == "critical" else 4, "click": url,
        "tags": ["rotating_light" if n["severity"] == "critical" else "warning"],
    }
    headers = {"Authorization": f"Bearer {settings.NTFY_TOKEN}"} if settings.NTFY_TOKEN else {}
    try:
        r = httpx.post(settings.NTFY_URL.rstrip("/"), json=payload, headers=headers, timeout=10)
        return "sent" if r.status_code < 300 else f"error {r.status_code}"
    except Exception as exc:  # noqa: BLE001 - a failed push is recorded, never raised
        return f"failed: {type(exc).__name__}"


def _sms(phone: str, n: Dict[str, Any], url: str) -> str:
    if settings.SMS_PROVIDER != "twilio" or not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM):
        return "off"
    text = f"Allied: {n['title']}. {url}"[:300]
    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
            data={"From": settings.TWILIO_FROM, "To": phone, "Body": text},
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            timeout=15,
        )
        return "sent" if r.status_code < 300 else f"error {r.status_code}"
    except Exception as exc:  # noqa: BLE001 - a failed text is recorded, never raised
        return f"failed: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


def _as_dict(r: NotificationDB) -> Dict[str, Any]:
    return {
        "id": r.id, "kind": r.kind, "severity": r.severity, "title": r.title, "body": r.body,
        "link": r.link, "project_item_id": r.project_item_id, "recipients": r.recipients,
        "deliveries": r.deliveries, "created_at": r.created_at.isoformat() if r.created_at else None,
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        "acknowledged_by": r.acknowledged_by,
        "severity_word": status_words.word("alert", r.severity),
        "snoozed_until": r.snoozed_until.isoformat() if r.snoozed_until else None,
        "assignee": r.assignee,
    }


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """A time with no zone is read as local business time (so 9am typed in Chicago is 9am Chicago, not UTC)."""
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=ZoneInfo(settings.BUSINESS_TZ))


def _like_safe(text: str) -> str:
    """The text with LIKE wildcards made literal (escape character is a backslash), so an address with _ or % matches only itself."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def inbox(email: Optional[str] = None, open_only: bool = False, limit: int = 100, include_snoozed: bool = False) -> List[Dict[str, Any]]:
    """Alerts, newest first. Snoozed ones are left out until their time passes (include_snoozed shows them)."""
    with SessionLocal() as db:
        q = db.query(NotificationDB).order_by(NotificationDB.id.desc())
        if open_only:
            q = q.filter(NotificationDB.acknowledged_at.is_(None))
        if not include_snoozed:
            q = q.filter(or_(NotificationDB.snoozed_until.is_(None), NotificationDB.snoozed_until <= datetime.now(timezone.utc)))
        if email:
            # Narrow in SQL (the JSON list as text, case-insensitive), then check exactly below.
            q = q.filter(or_(cast(NotificationDB.recipients, String).ilike('%"' + _like_safe(email.strip()) + '"%', escape="\\"), func.lower(NotificationDB.assignee) == email.strip().lower()))
        rows_ = q.limit(limit).all()
        hidden = {r.id for r in rows_ if email and email.strip().lower() in {str(x).lower() for x in ((r.deliveries or {}).get("_hidden_for") or [])}}      # "dismiss for me": gone from this person's list only
        out = [_as_dict(r) for r in rows_ if r.id not in hidden]
    if email:
        e = email.strip().lower()
        out = [n for n in out if e in {r.lower() for r in n["recipients"] or []} or (n["assignee"] or "").lower() == e]
    return out


def group_of(kind: str) -> str:
    """The plain-words group an alert kind belongs to (about eight groups instead of twenty kinds)."""
    k = kind or ""
    if k == "autopilot_drafts":
        return "Drafts to approve"
    for prefix, group in (("vendor_", "Vendors"), ("rfq_", "RFQs"), ("autopilot_rfq", "RFQs"), ("shipment_", "Shipments"), ("ship-", "Shipments"),
                          ("milestone_", "Projects"), ("project", "Projects"), ("autopilot_order", "Orders"), ("order", "Orders"),
                          ("daily_summary", "Briefs"), ("personal_brief", "Briefs"), ("autopilot_", "Automations")):
        if k.startswith(prefix):
            return group
    return "Other"


def rollup(email: Optional[str] = None, top: int = 5) -> List[Dict[str, Any]]:
    """The open alerts as a few groups: how many, the worst severity, how old the oldest is, and the first few. Worst and oldest first."""
    now = datetime.now(timezone.utc)
    rank = {"critical": 0, "warning": 1, "info": 2}
    groups: Dict[str, Dict[str, Any]] = {}
    for n in inbox(email, open_only=True, limit=500):
        g = groups.setdefault(group_of(n["kind"]), {"group": group_of(n["kind"]), "count": 0, "worst": "info", "oldest_days": 0, "items": []})
        g["count"] += 1
        if rank.get(n["severity"], 2) < rank[g["worst"]]:
            g["worst"] = n["severity"]
        made = datetime.fromisoformat(n["created_at"]) if n["created_at"] else now
        made = made if made.tzinfo else made.replace(tzinfo=timezone.utc)
        age = max(0, (now - made).days)
        g["oldest_days"] = max(g["oldest_days"], age)
        g["items"].append({"id": n["id"], "title": n["title"], "severity": n["severity"], "link": n["link"], "age_days": age, "assignee": n["assignee"]})
    for g in groups.values():
        g["items"].sort(key=lambda i: (rank.get(i["severity"], 2), -i["age_days"]))
        g["items"] = g["items"][:top]
    return sorted(groups.values(), key=lambda g: (rank[g["worst"]], -g["count"]))


MAX_SNOOZE_HOURS = 24 * 30
MAX_CRITICAL_SNOOZE_HOURS = 24
_PLAIN_ADDRESS = re.compile(r"[^@\s;,<>\"()\[\]\\\x00-\x1f\x7f]+@[^@\s;,<>\"()\[\]\\\x00-\x1f\x7f]+\.[^@\s;,<>\"()\[\]\\\x00-\x1f\x7f.]+")


def _may_touch(row: NotificationDB, email: str, any_alert: bool) -> bool:
    mine = {r.lower() for r in row.recipients or []} | ({row.assignee.lower()} if row.assignee else set())
    return any_alert or (email or "").lower() in mine


def snooze(nid: int, email: str, until: Optional[datetime] = None, hours: Optional[float] = None, any_alert: bool = False) -> Any:
    """Hides an open alert until a time (a date and time, or hours from now, at most 30 days). Returns the time, or "missing", "done", "forbidden", "bad"."""
    now = datetime.now(timezone.utc)
    if isinstance(hours, bool):
        return "bad"
    if until is None and hours is not None:
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            return "bad"
        if not (hours > 0) or hours > MAX_SNOOZE_HOURS:
            return "bad"
        until = now + timedelta(hours=hours)
    until = _aware(until)
    until = until.astimezone(timezone.utc) if until is not None else None          # stored as UTC: SQLite keeps no zone, and the inbox compares against UTC
    if until is None or until <= now or until > now + timedelta(hours=MAX_SNOOZE_HOURS):
        return "bad"
    with SessionLocal() as db:
        row = db.get(NotificationDB, nid)
        if row is None:
            return "missing"
        if row.acknowledged_at is not None:
            return "done"
        if not _may_touch(row, email, any_alert):
            return "forbidden"
        if row.severity == "critical" and until > now + timedelta(hours=MAX_CRITICAL_SNOOZE_HOURS):
            return "bad"          # snooze is shared by everyone the alert went to, so an urgent one can only be put off briefly
        row.snoozed_until = until
        db.commit()
    return until


def assign(nid: int, email: str, to: str, any_alert: bool = False) -> str:
    """Hands an open alert to one person. Returns "ok", "missing", "done", "forbidden" or "bad" (not a real address)."""
    to = (to or "").strip().lower()
    if len(to) > 254 or not _PLAIN_ADDRESS.fullmatch(to) or is_placeholder(to):
        return "bad"
    from app.core.security import _account
    with SessionLocal() as db:
        row = db.get(NotificationDB, nid)
        if row is None:
            return "missing"
        if row.acknowledged_at is not None:
            return "done"
        if not _may_touch(row, email, any_alert):
            return "forbidden"
        found = _account(to)
        if found is None or not found[0]:
            return "bad"          # only an active staff account can be handed an alert
        row.assignee = to
        row.snoozed_until = None          # handing it over wakes it up for them
        db.commit()
    return "ok"


def _close_old(rows, why: str) -> int:
    """Closes these alerts (acknowledged by "system") and writes one audit line for each."""
    from app.core.audit import audit_log

    now = datetime.now(timezone.utc)
    n = 0
    with SessionLocal() as db:
        for r in rows(db):
            r.acknowledged_at, r.acknowledged_by = now, "system"
            n += 1
            try:
                audit_log("ALERT_AUTO_CLOSED", "notification", str(r.id), actor_email="system", actor_role="SYSTEM", detail=f"{why}: {str(r.title)[:120]}")
            except Exception:  # noqa: BLE001 - housekeeping never stops on a log line
                pass
        db.commit()
    return n


def expire_tests(hours: int = 24) -> int:
    """A test alert (the "send me a test" button) closes itself after a day, so one never lingers in someone's list. Returns how many were closed; each gets an audit line."""
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    return _close_old(lambda db: db.query(NotificationDB).filter(NotificationDB.kind == "test", NotificationDB.acknowledged_at.is_(None), NotificationDB.created_at < cut).limit(500).all(),
                      f"A test alert older than {hours} hours closed itself")


def expire_old_info(days: Optional[int] = None) -> int:
    """An information-only alert nobody closed within NOTICE_AUTOCLOSE_DAYS (default 14) closes itself, so "older notices" clears on its own. Warnings and urgent alerts are never closed this way."""
    days = settings.NOTICE_AUTOCLOSE_DAYS if days is None else days
    if not days or days <= 0:
        return 0
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    return _close_old(lambda db: db.query(NotificationDB).filter(NotificationDB.severity == "info", NotificationDB.acknowledged_at.is_(None), NotificationDB.created_at < cut).limit(500).all(),
                      f"An information-only alert older than {days} days closed itself")


def acknowledge_group(group: str, email: str, any_alert: bool = False) -> int:
    """Closes every open alert in a group that this person may close. Returns how many."""
    closed = 0
    for n in inbox(None if any_alert else email, open_only=True, limit=500):
        if group_of(n["kind"]) == group and acknowledge(n["id"], email, any_alert) == "ok":
            closed += 1
    return closed


def acknowledge(nid: int, email: str, any_alert: bool = False) -> str:
    """Marks an alert handled. People can close their own alerts; managers can close anyone's.

    Returns "ok", "missing", "done" (already acknowledged) or "forbidden".
    """
    with SessionLocal() as db:
        row = db.get(NotificationDB, nid)
        if row is None:
            return "missing"
        if row.acknowledged_at is not None:
            return "done"
        if not _may_touch(row, email, any_alert):
            return "forbidden"
        row.acknowledged_at = datetime.now(timezone.utc)
        row.acknowledged_by = email
        db.commit()
    return "ok"


def dismiss_for_me(nid: int, email: str) -> str:
    """Takes an alert off THIS person's list. An alert sent to several people stays open for the others (it is remembered as hidden for this person); an alert only they hold is closed as usual.
    Returns "ok", "missing", "done" or "forbidden"."""
    e = (email or "").strip().lower()
    with SessionLocal() as db:
        row = db.get(NotificationDB, nid)
        if row is None:
            return "missing"
        if row.acknowledged_at is not None:
            return "done"
        holders = {str(r).lower() for r in (row.recipients or [])} | ({row.assignee.lower()} if row.assignee else set())
        if e not in holders:
            return "forbidden"
        if len(holders) <= 1:
            row.acknowledged_at, row.acknowledged_by = datetime.now(timezone.utc), email
        else:
            d = dict(row.deliveries or {})
            d["_hidden_for"] = sorted(set(d.get("_hidden_for") or []) | {e})
            row.deliveries = d
            if holders <= set(d["_hidden_for"]):                                   # everyone has dismissed it: now it is closed
                row.acknowledged_at, row.acknowledged_by = datetime.now(timezone.utc), email
        db.commit()
    return "ok"


def resolve(key_prefixes: Iterable[str]) -> int:
    """Closes open alerts whose condition has cleared (milestone done, shipment delivered)."""
    prefixes = [p for p in key_prefixes if p]
    if not prefixes:
        return 0
    with SessionLocal() as db:
        n = (
            db.query(NotificationDB)
            .filter(NotificationDB.acknowledged_at.is_(None), or_(*[NotificationDB.dedup_key.like(f"{p}%") for p in prefixes]))
            .update({NotificationDB.acknowledged_at: datetime.now(timezone.utc),
                     NotificationDB.acknowledged_by: RESOLVED_BY}, synchronize_session=False)
        )
        if n:  # most calls close nothing; a commit that writes nothing still waits on the disk
            db.commit()
    return n


def resolve_missing(prefixes: Iterable[str], keep: Iterable[str]) -> int:
    """Closes open alerts like "<prefix><id>:..." whose id is no longer in `keep`
    (a milestone or project deleted from the board)."""
    keep = set(keep)
    stale = []
    with SessionLocal() as db:
        for prefix in prefixes:
            for (key,) in db.query(NotificationDB.dedup_key).filter(
                NotificationDB.acknowledged_at.is_(None), NotificationDB.dedup_key.like(f"{prefix}%")
            ):
                item = key[len(prefix):].split(":", 1)[0]
                if item not in keep:
                    stale.append(f"{prefix}{item}:")
    return resolve(stale)


def resolve_others(prefix: str, keep_keys: Iterable[str]) -> int:
    """Closes open alerts starting with `prefix` except the exact keys in keep_keys (yesterday's
    daily list once today's is out, or a list for someone with nothing left on it)."""
    keep = set(keep_keys)
    with SessionLocal() as db:
        stale = [k for (k,) in db.query(NotificationDB.dedup_key).filter(
            NotificationDB.acknowledged_at.is_(None), NotificationDB.dedup_key.like(f"{prefix}%")) if k not in keep]
        if stale:
            db.query(NotificationDB).filter(NotificationDB.dedup_key.in_(stale), NotificationDB.acknowledged_at.is_(None)).update(
                {NotificationDB.acknowledged_at: datetime.now(timezone.utc), NotificationDB.acknowledged_by: RESOLVED_BY},
                synchronize_session=False)
            db.commit()
    return len(stale)


def channel_status() -> Dict[str, Any]:
    from app.services.outbound_email_service import outbound_email_service

    return {
        "email": outbound_email_service.status(),
        "push": {"on": bool(settings.NTFY_URL and settings.NTFY_TOPIC), "detail": "ntfy phone push" if settings.NTFY_URL else "Set NTFY_URL and NTFY_TOPIC."},
        "sms": {"on": settings.SMS_PROVIDER == "twilio" and bool(settings.TWILIO_ACCOUNT_SID), "detail": "Twilio" if settings.SMS_PROVIDER == "twilio" else "Off. Set SMS_PROVIDER=twilio and the Twilio keys."},
        "team_chat": {"on": bool(settings.SLACK_WEBHOOK_URL or settings.TEAMS_WEBHOOK_URL)},
        "leadership": leadership(),
        "quiet_hours": f"{settings.QUIET_HOURS_START}:00-{settings.QUIET_HOURS_END}:00 {settings.BUSINESS_TZ}",
        "daily_summary_hour": settings.DIGEST_HOUR,
    }


def close_orphaned_item_alerts() -> int:
    """Closes open alerts about a monday board row that is no longer on any board the app has read (a row removed or moved away). A catch-all beside the per-check clean-ups: it does nothing until at least
    one board has been read, so a board that was simply not loaded yet never closes anything. Each closed alert gets an audit line."""
    from app.services.coordination import boards

    with boards._lock:
        present = {str(i["id"]) for b in boards._mirror.values() for i in b.get("items", [])}
        have_boards = any(b.get("items") for b in boards._mirror.values())
    if not have_boards:
        return 0
    return _close_old(lambda db: [r for r in db.query(NotificationDB).filter(NotificationDB.acknowledged_at.is_(None), NotificationDB.project_item_id.isnot(None)).limit(2000).all()
                                  if str(r.project_item_id) not in present][:500], "The board row this alert was about is no longer on the board")

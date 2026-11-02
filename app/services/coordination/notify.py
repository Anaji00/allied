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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import String, cast, or_

from app.config import settings
from app.core.database import SessionLocal
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
    recipients = _unique(list(to) + (leadership() if include_leadership else []))
    if not recipients:
        # Never drop an alert because a monday column was empty.
        recipients = leadership()
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
        if "email" in channels:
            result["email"] = _email(email, n, url)
        if "push" in channels and n["severity"] in ("warning", "critical"):
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
    }


def inbox(email: Optional[str] = None, open_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(NotificationDB).order_by(NotificationDB.id.desc())
        if open_only:
            q = q.filter(NotificationDB.acknowledged_at.is_(None))
        if email:
            # Narrow in SQL (the JSON list as text, case-insensitive), then check exactly below.
            q = q.filter(cast(NotificationDB.recipients, String).ilike(f'%"{email.strip()}"%'))
        out = [_as_dict(r) for r in q.limit(limit).all()]
    if email:
        e = email.strip().lower()
        out = [n for n in out if e in {r.lower() for r in n["recipients"] or []}]
    return out


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
        if not any_alert and email.lower() not in {r.lower() for r in row.recipients or []}:
            return "forbidden"
        row.acknowledged_at = datetime.now(timezone.utc)
        row.acknowledged_by = email
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

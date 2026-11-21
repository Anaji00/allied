"""My counts in one call, and my own notification choices. Only the signed-in person's own data."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.database import SessionLocal
from app.models.db_models import NotificationPrefsDB

CHANNELS = ("app", "email", "push", "sms")
GROUPS = ("approvals", "drafts", "alerts", "ball", "mentions", "mail", "follows", "leads")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULT = {"groups": {g: ["app", "email"] for g in GROUPS}, "quiet_start": None, "quiet_end": None, "delivery": "instant", "digest_time": None}


class PrefsError(Exception):
    pass


def counts(user: Dict[str, Any]) -> Dict[str, Any]:
    """Unread alerts, mail needing action and drafts waiting for my OK: the same numbers as /my-day and /me/needs (one source), plus the badge number."""
    from app.services import mail_log, my_day
    from app.services.coordination import needs

    email = (user.get("email") or "").strip().lower()
    n = needs.for_person(user)["counts"]
    mail = len(mail_log.list_mail(needs_action=True, days=60, limit=200, mine=email)["mail"]) if email else 0
    alerts, drafts = int(n.get("alerts", 0)), int(n.get("drafts", 0))
    from app.services.assistant import agent

    email_drafts = sum(1 for d in agent.pending(user) if d.get("tool") == "send_email")
    return {"alerts_unread": alerts, "mail_needing_action": mail, "drafts_waiting": drafts, "email_drafts_waiting": email_drafts, "total": alerts + mail + drafts, "need_action": my_day.items(user)["need_action"]}


def get_prefs(email: str) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(NotificationPrefsDB, (email or "").strip().lower())
        saved = dict(row.prefs or {}) if row else {}
    out = {**DEFAULT, **{k: v for k, v in saved.items() if k in DEFAULT}}
    out["groups"] = {**DEFAULT["groups"], **(saved.get("groups") or {})}
    return out


def put_prefs(email: str, data: Dict[str, Any]) -> Dict[str, Any]:
    key = (email or "").strip().lower()
    cur = get_prefs(key)
    groups = dict(cur["groups"])
    for g, chans in (data.get("groups") or {}).items():
        if g not in GROUPS:
            raise PrefsError(f"Unknown group '{g}'. Choose from: {', '.join(GROUPS)}.")
        if not isinstance(chans, list) or any(c not in CHANNELS for c in chans):
            raise PrefsError(f"Channels for {g} must be a list of: {', '.join(CHANNELS)}.")
        groups[g] = list(dict.fromkeys(chans))
    delivery = data.get("delivery", cur["delivery"])
    if delivery not in ("instant", "digest"):
        raise PrefsError("Delivery must be instant or digest.")
    new = {"groups": groups, "delivery": delivery}
    for k in ("quiet_start", "quiet_end", "digest_time"):
        v = data.get(k, cur[k])
        if v not in (None, "") and not _HHMM.match(str(v)):
            raise PrefsError(f"{k.replace('_', ' ').capitalize()} must be a time like 18:00.")
        new[k] = v or None
    if bool(new["quiet_start"]) != bool(new["quiet_end"]):
        raise PrefsError("Give both a quiet start and a quiet end, or neither.")
    with SessionLocal() as db:
        row = db.get(NotificationPrefsDB, key)
        if row is None:
            row = NotificationPrefsDB(user_email=key)
            db.add(row)
        row.prefs, row.updated_at = new, datetime.now(timezone.utc)
        db.commit()
    return get_prefs(key)


# ---------------------------------------------------------------- used by the sending code
def saved_prefs(email: str) -> Optional[Dict[str, Any]]:
    """This person's saved choices, or None when they never saved any (then nothing about how they are told changes)."""
    with SessionLocal() as db:
        row = db.get(NotificationPrefsDB, (email or "").strip().lower())
        if row is None or not row.prefs:
            return None
    return get_prefs(email)


def pref_group(kind: str) -> str:
    """The notification-choice group an alert kind belongs to."""
    k = kind or ""
    for prefix, group in (("autopilot_drafts", "drafts"), ("approval", "approvals"), ("mail_", "mail"), ("ball", "ball"), ("mention", "mentions"), ("follow", "follows"), ("lead", "leads")):
        if k.startswith(prefix):
            return group
    return "alerts"


def in_quiet_hours(prefs: Dict[str, Any], now: datetime) -> bool:
    """True when `now` (business time) is inside this person's quiet hours; a window like 18:00 to 07:00 runs overnight."""
    start, end = prefs.get("quiet_start"), prefs.get("quiet_end")
    if not (start and end):
        return False
    cur = now.strftime("%H:%M")
    return (start <= cur < end) if start <= end else (cur >= start or cur < end)

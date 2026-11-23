"""Nudges and milestone blasts: a named few (NUDGE_SENDERS, plus admins) can pester Allied staff at once, by app notice, email and text. Sent immediately (quiet hours do not apply) and marked "sent by <name>".

Rules enforced here, not in the screen: only a sender on the list or an admin; every recipient must be an active Allied staff contact (or an active user account), never an outside address; a text only goes to a
contact who has a phone and has the sms channel; a per-sender rate limit; one audit entry per recipient; and a practice flag that sends nothing and says what WOULD have gone. No table of its own: the history is
read back from the audit trail, and the app notice is an ordinary alert, so it appears in the person's Today list. Nothing here is called until a person confirms in the screen."""
import json
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB, NotificationDB, UserDB

CHANNELS = ("app", "email", "sms")
MAX_RECIPIENTS = 25
MAX_MESSAGE = 600
_sent_at: Dict[str, Deque[float]] = defaultdict(deque)
_pair_at: Dict[str, Deque[float]] = defaultdict(deque)
OUTSIDE_MESSAGE = "Nudges go to Allied staff only; use a draft for anyone outside."
MONEY_MESSAGE = "A nudge never carries prices, payment terms or customer names. Say which PO or job it is about, and nothing more."
_MONEY = re.compile(r"([$€£¥]|\b(?:usd|cad|cny|rmb)\b|\b\d[\d,]*\.\d{2}\b|\bnet\s*\d+\b|\b(?:markup|deposit|proforma|wire|price|prices|pricing)\b|\bquote[ds]?\s+at\b|\bper\s+(?:unit|piece|each)\b|\d\s*%)", re.I)
_lock = threading.Lock()


class NudgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def senders() -> List[str]:
    return [e.strip().lower() for e in str(getattr(settings, "NUDGE_SENDERS", "") or "").split(",") if e.strip()]


def may_send(user: Dict[str, Any]) -> bool:
    return str(user.get("role", "")).upper() == "ADMIN" or (user.get("email") or "").strip().lower() in senders()


def _sender_name(user: Dict[str, Any]) -> str:
    from app.services.coordination import notify

    c = notify.contacts().get((user.get("email") or "").lower())
    return (c or {}).get("name") or user.get("name") or str(user.get("email") or "").split("@")[0].replace(".", " ").title()


def _rate_ok(sender: str) -> bool:
    limit, window = int(getattr(settings, "NUDGE_RATE_PER_HOUR", 10)), 3600.0
    now = time.monotonic()
    with _lock:
        q = _sent_at[sender]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _allied_domain(email: str) -> bool:
    domain = "@" + str(settings.EMAIL_FROM).rsplit("@", 1)[-1].strip().casefold()
    return email.strip().casefold().endswith(domain) and domain != "@"


def _pair_ok(sender: str, email: str) -> bool:
    """No more than NUDGE_PER_RECIPIENT_PER_HOUR to one person from one sender (nobody is badgered)."""
    limit, now = int(getattr(settings, "NUDGE_PER_RECIPIENT_PER_HOUR", 3)), time.monotonic()
    with _lock:
        q = _pair_at[f"{sender}>{email}"]
        while q and now - q[0] > 3600.0:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def _recipient(email: str) -> Dict[str, Any]:
    """The staff record for an address, or {} when it is not an active Allied staff contact or user (never an outside address)."""
    from app.services.coordination import notify

    e = email.strip().lower()
    c = notify.contacts().get(e)
    if c is not None:
        return c if c.get("active", True) else {}
    with SessionLocal() as db:
        u = db.query(UserDB).filter(UserDB.email == e).first()
        if u is not None and u.is_active:
            return {"email": u.email, "name": u.full_name, "phone": None, "channels": ["email"], "active": True}
    return {}


def send(user: Dict[str, Any], kind: str, to: List[str], message: str, channels: List[str], milestone: Optional[Dict[str, Any]] = None, practice: bool = False) -> Dict[str, Any]:
    """{sent: [{email, channel, status}], skipped: [{email, reason}], message, practice}. Raises NudgeError for a sender who may not, a bad request, or the rate limit."""
    if not may_send(user):
        raise NudgeError(403, "Only the people named to send nudges, and admins, can do this.")
    if kind not in ("nudge", "milestone"):
        raise NudgeError(422, "The kind is nudge or milestone.")
    text = " ".join(str(message or "").split())
    if not text:
        raise NudgeError(422, "Write the message.")
    if len(text) > MAX_MESSAGE:
        raise NudgeError(422, f"Keep the message under {MAX_MESSAGE} characters.")
    chans = [c for c in dict.fromkeys(str(c).lower() for c in (channels or [])) if c]
    if not chans or any(c not in CHANNELS for c in chans):
        raise NudgeError(422, "Choose at least one channel: app, email, sms.")
    emails = list(dict.fromkeys(str(e).strip().lower() for e in (to or []) if str(e).strip()))
    if not emails:
        raise NudgeError(422, "Say who it is for.")
    if len(emails) > MAX_RECIPIENTS:
        raise NudgeError(422, f"At most {MAX_RECIPIENTS} people at once.")
    if _MONEY.search(text) or _MONEY.search(str((milestone or {}).get("name") or "")):
        raise NudgeError(422, MONEY_MESSAGE)
    try:
        from app.services.coordination import blind

        if blind.found(text + " " + str((milestone or {}).get("name") or ""), blind.customer_names()):
            raise NudgeError(422, MONEY_MESSAGE)
    except NudgeError:
        raise
    except Exception:  # noqa: BLE001 - the customer list could not be read: the message still passes the money check above
        pass
    from app.services.coordination import notify as _notify

    known = _notify.contacts()
    outside = [e for e in emails if e not in known and not _allied_domain(e)]
    if outside:
        raise NudgeError(422, OUTSIDE_MESSAGE)
    ms = milestone or {}
    if kind == "milestone" and not str(ms.get("name") or "").strip():
        raise NudgeError(422, "A milestone needs a name.")
    sender_email = (user.get("email") or "").strip().lower()
    if not practice and not _rate_ok(sender_email):
        raise NudgeError(429, "That is a lot of nudges in an hour. Wait a little, or ask an admin.")
    who = _sender_name(user)
    head = f"Milestone: {str(ms['name']).strip()[:80]}" + (f" (due {str(ms['due'])[:10]})" if ms.get("due") else "") if kind == "milestone" else "Nudge"
    full = f"{head}: {text}" if kind == "milestone" else text
    sent: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    for email in emails:
        rec = _recipient(email)
        if not rec:
            skipped.append({"email": email, "reason": "Not an active Allied staff member, so nothing was sent."})
            _audit("NUDGE_SKIPPED", user, email, kind, [], "not staff", practice, text)
            continue
        if not practice and not _pair_ok(sender_email, email):
            skipped.append({"email": email, "reason": "Already nudged this person three times this hour."})
            _audit("NUDGE_SKIPPED", user, email, kind, [], "per-person limit", practice, text)
            continue
        results: List[Dict[str, str]] = []
        for ch in chans:
            if ch == "sms" and not rec.get("phone"):
                skipped.append({"email": email, "reason": "No phone number on file for texts."})
                continue
            if ch == "sms" and "sms" not in (rec.get("channels") or []):
                skipped.append({"email": email, "reason": "This person has not turned on texts."})
                continue
            results.append({"email": email, "channel": ch, "status": "practice: not sent" if practice else _deliver(ch, rec, who, kind, full, text, ms, sender_email)})
        sent += results
        _audit("NUDGE_PRACTICE" if practice else "NUDGE_SENT", user, email, kind, results, "", practice, text)
    word = "Practice only: nothing was sent. It would have gone to" if practice else "Sent to"
    ok = sorted({s["email"] for s in sent})
    return {"sent": sent, "skipped": skipped, "practice": bool(practice), "message": f"{word} {len(ok)} {'person' if len(ok) == 1 else 'people'}" + (f"; {len(skipped)} left out" if skipped else "") + "."}


def _deliver(channel: str, rec: Dict[str, Any], who: str, kind: str, full: str, text: str, ms: Dict[str, Any], sender_email: str) -> str:
    from app.services.coordination import notify

    title = f"Sent by {who}: {full}"[:300]
    n = {"title": title, "body": f"Sent by {who}.\n\n{full}", "severity": "warning", "link": "/today", "kind": "nudge", "recipients": [rec["email"]]}
    url = notify._url(n)
    try:
        if channel == "app":
            with SessionLocal() as db:
                db.add(NotificationDB(dedup_key=f"nudge:{uuid.uuid4().hex}", kind="nudge", severity="warning", title=title, body=n["body"], link="/today", recipients=[rec["email"]],
                                      deliveries={"app": "shown", "sent_by": sender_email}))
                db.commit()
            return "sent"
        if channel == "email":
            return notify._email(rec["email"], n, url)
        return notify._sms(rec["phone"], n, url)
    except Exception as exc:  # noqa: BLE001 - one failed channel is recorded and the rest still go
        return f"failed: {type(exc).__name__}"


def _audit(action: str, user: Dict[str, Any], email: str, kind: str, results: List[Dict[str, str]], note: str, practice: bool, text: str) -> None:
    audit_log(action=action, resource_type="NUDGE", resource_id=email[:64], actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=json.dumps({"kind": kind, "to": email, "results": results, "note": note, "practice": practice, "message": text[:200]}))


def history(user: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
    """Recent nudges (newest first), from the audit trail. A sender sees their own; an admin sees everyone's."""
    with SessionLocal() as db:
        q = db.query(AuditLogDB).filter(AuditLogDB.action.in_(("NUDGE_SENT", "NUDGE_PRACTICE", "NUDGE_SKIPPED")))
        if str(user.get("role", "")).upper() != "ADMIN":
            q = q.filter(AuditLogDB.actor_email == (user.get("email") or ""))
        rows = q.order_by(AuditLogDB.id.desc()).limit(max(1, min(int(limit), 500))).all()
        out = []
        for r in rows:
            try:
                d = json.loads(r.detail or "{}")
            except ValueError:
                d = {}
            out.append({"at": r.timestamp.isoformat() if getattr(r, "timestamp", None) else None, "by": r.actor_email, "to": d.get("to") or r.resource_id, "kind": d.get("kind"), "results": d.get("results", []),
                        "note": d.get("note") or None, "practice": bool(d.get("practice")), "message": d.get("message"), "action": r.action})
        return out

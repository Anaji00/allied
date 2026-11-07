"""Approving a draft from the phone: a signed link per draft and reviewer, and a push with buttons.

A new autopilot draft is pushed to ntfy with two buttons: Send (posts the decision straight
back) and Review (opens a small page with the email, why it was drafted, and Send / Cancel).
Opening a link never decides anything: only a POST does, so a mail scanner or link preview
can't approve a draft by visiting it. The link is signed for one draft and one reviewer and
lapses with the draft (AUTOPILOT_DRAFT_HOURS); a draft can only be decided once.
"""

import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.core.logging import logger


def _sig(action_id: str, email: str, expires: int) -> str:
    msg = f"quick:{action_id}:{email.lower()}:{expires}".encode()
    return hmac.new(settings.JWT_SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:40]


def token(action_id: str, email: str) -> Dict[str, str]:
    expires = int(time.time()) + settings.AUTOPILOT_DRAFT_HOURS * 3600
    return {"e": email.lower(), "x": str(expires), "s": _sig(action_id, email, expires)}


def check(action_id: str, e: str, x: str, s: str) -> Optional[str]:
    """The reviewer's email if the link is genuine and not lapsed, else None."""
    try:
        expires = int(x)
    except (TypeError, ValueError):
        return None
    if expires < time.time() or not s or not hmac.compare_digest(s, _sig(action_id, e or "", expires)):
        return None
    return (e or "").lower()


def reviewer(email: str) -> Optional[Dict[str, Any]]:
    """Who decides through a link: an active account, or a configured reviewer with no account yet."""
    from app.core.security import _account
    from app.services.coordination.autopilot import reviewers

    found = _account(email)
    if found is not None:
        active, role = found
        return {"email": email, "role": role} if active else None
    if email in {r.lower() for r in reviewers()}:
        return {"email": email, "role": "MANAGER"}  # named in AUTOPILOT_REVIEWER_EMAILS
    return None


def page_url(action_id: str, email: str) -> str:
    return f"{settings.APP_BASE_URL.rstrip('/')}/api/v1/assistant/quick/{action_id}?{urlencode(token(action_id, email))}"


def _reachable() -> bool:
    """A phone can only follow links to an address it can reach; localhost is this machine only."""
    host = settings.APP_BASE_URL.split("://")[-1].split("/")[0].split(":")[0]
    return host not in ("localhost", "127.0.0.1", "0.0.0.0", "")


def push_draft(action_id: str, summary: str, email: str) -> str:
    """Pushes one new draft to the reviewers' phones with Send and Review buttons."""
    from app.services.coordination import notify

    if not (settings.AUTOPILOT_PUSH_DRAFTS and settings.NTFY_URL and settings.NTFY_TOPIC):
        return "off"
    if notify._quiet():
        return "held"  # the daily "drafts waiting" alert covers it; no buzz at night
    payload: Dict[str, Any] = {"topic": settings.NTFY_TOPIC, "title": "Draft to approve",
                               "message": summary[:3500], "priority": 3, "tags": ["envelope"]}
    if _reachable():
        t = token(action_id, email)
        post = f"{settings.APP_BASE_URL.rstrip('/')}/api/v1/assistant/quick/{action_id}"
        payload["actions"] = [
            {"action": "http", "label": "Send", "url": post, "method": "POST", "clear": True,
             "headers": {"Content-Type": "application/x-www-form-urlencoded"}, "body": urlencode({**t, "decision": "send"})},
            {"action": "view", "label": "Review", "url": page_url(action_id, email)},
        ]
    else:
        payload["message"] += "\n\nOpen Allied Operations to send or cancel it."
    headers = {"Authorization": f"Bearer {settings.NTFY_TOKEN}"} if settings.NTFY_TOKEN else {}
    try:
        r = httpx.post(settings.NTFY_URL.rstrip("/"), json=payload, headers=headers, timeout=10)
        return "sent" if r.status_code < 300 else f"error {r.status_code}"
    except Exception as exc:  # noqa: BLE001 - a push that fails leaves the draft waiting as before
        logger.info(f"Draft push failed: {type(exc).__name__}")
        return f"failed: {type(exc).__name__}"


def push_batch(email: str, items: List[Dict[str, Any]], waiting: int) -> str:
    """One push for a batch of new drafts: one draft keeps its Send / Review buttons; several
    become "N waiting" with a Review-all button, so a busy run doesn't buzz the phone N times."""
    if len(items) == 1 and items[0].get("id"):
        return push_draft(items[0]["id"], items[0]["what"], email)
    from app.services.coordination import notify

    if not (settings.AUTOPILOT_PUSH_DRAFTS and settings.NTFY_URL and settings.NTFY_TOPIC):
        return "off"
    if notify._quiet():
        return "held"
    lines = "\n".join(f"- {d['what']}" for d in items[:6]) + (
        f"\n- and {len(items) - 6} more" if len(items) > 6 else "")
    payload: Dict[str, Any] = {"topic": settings.NTFY_TOPIC, "title": f"{waiting} drafts waiting for your OK",
                               "message": lines[:3500], "priority": 3, "tags": ["envelope"]}
    if _reachable():
        payload["actions"] = [{"action": "view", "label": "Review all",
                               "url": f"{settings.APP_BASE_URL.rstrip('/')}/inbox?show=approvals"}]
    headers = {"Authorization": f"Bearer {settings.NTFY_TOKEN}"} if settings.NTFY_TOKEN else {}
    try:
        r = httpx.post(settings.NTFY_URL.rstrip("/"), json=payload, headers=headers, timeout=10)
        return "sent" if r.status_code < 300 else f"error {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        logger.info(f"Draft push failed: {type(exc).__name__}")
        return f"failed: {type(exc).__name__}"

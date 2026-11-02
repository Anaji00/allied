"""Polls a real mailbox so orders arrive without anyone forwarding them.

The email daemon's logic was already real; what was missing was anything to
hand it a message. This is that: an IMAP reader on a timer, converting each
unseen message into the daemon's own input type.

Deliberately conservative:

* Off unless ``IMAP_POLL_ENABLED`` and a host are both set.
* A message is marked seen only after it has been processed, so a crash
  mid-ingest re-reads it rather than losing it. Re-reading is safe because
  order creation is idempotent on the customer PO number.
* One failure never stops the loop. A mailbox that is briefly unreachable is
  an ordinary Tuesday, not a reason to stop accepting orders for the week.
"""

from __future__ import annotations

import email
import imaplib
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger

# Attachments larger than this are left on the message. A supplier sending a
# 40 MB drawing set should not be read into memory by a background thread.
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _body_text(msg: Message) -> str:
    """Extracts the plain-text body, preferring text/plain over HTML."""
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        if payload is None:
            return str(msg.get_payload())
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

    html_fallback = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" in disposition.lower():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain":
            return text
        if part.get_content_type() == "text/html" and not html_fallback:
            html_fallback = text

    if html_fallback:
        import re

        return re.sub(r"<[^>]+>", " ", html_fallback)
    return ""


def _attachments(msg: Message) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" not in disposition.lower():
            continue
        filename = _decode(part.get_filename()) or "attachment"
        payload = part.get_payload(decode=True)
        if payload is None or len(payload) > _MAX_ATTACHMENT_BYTES:
            continue
        out.append({
            "filename": filename,
            "content": payload,
            "mime": part.get_content_type(),
        })
    return out


def folders() -> List[str]:
    """The folders read: IMAP_FOLDERS (comma-separated), else IMAP_FOLDER."""
    raw = settings.IMAP_FOLDERS or settings.IMAP_FOLDER or "INBOX"
    return [f.strip() for f in raw.split(",") if f.strip()]


def _quote(folder: str) -> str:
    return f'"{folder}"' if (" " in folder or "[" in folder) and not folder.startswith('"') else folder


def _message_ids(conn: imaplib.IMAP4, nums: List[bytes]) -> Dict[bytes, str]:
    """Each message's Message-ID, from the headers only (one request; nothing is marked read)."""
    if not nums:
        return {}
    typ, data = conn.fetch(b",".join(nums), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
    out: Dict[bytes, str] = {}
    for part in data or []:
        if isinstance(part, tuple) and part[0]:
            num = part[0].split(b" ", 1)[0]
            out[num] = _decode(email.message_from_bytes(part[1] or b"").get("Message-ID")).strip()
    return out


def _fallback_id(msg: Message) -> str:
    """For the rare message with no Message-ID: the same message always gets the same id."""
    import hashlib

    raw = f"{msg.get('From')}|{msg.get('Date')}|{msg.get('Subject')}".encode("utf-8", "replace")
    return "noid-" + hashlib.sha256(raw).hexdigest()[:32]


class MailboxPoller:
    """Reads unseen mail on a timer and feeds it to the inbound email daemon."""

    def __init__(self, daemon: Any = None) -> None:
        self._daemon = daemon
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._stats: Dict[str, Any] = {
            "running": False,
            "polls": 0,
            "messages_ingested": 0,
            "orders_created": 0,
            "errors": 0,
            "last_poll_at": None,
            "last_error": None,
        }

    # ----------------------------------------------------------------- state

    @property
    def enabled(self) -> bool:
        # A mailbox needs its login; with the host set but no app password yet it waits, quietly.
        return bool(settings.IMAP_POLL_ENABLED and settings.IMAP_HOST and settings.IMAP_USERNAME and settings.IMAP_PASSWORD)

    def status(self) -> Dict[str, Any]:
        detail = "Polling is switched off."
        if not settings.IMAP_HOST:
            detail = "No mailbox configured; orders must be handed to the system."
        elif not settings.IMAP_POLL_ENABLED:
            detail = "Mailbox configured but polling is switched off."
        elif not (settings.IMAP_USERNAME and settings.IMAP_PASSWORD):
            detail = "Mailbox set; waiting for IMAP_USERNAME and IMAP_PASSWORD (an app password)."
        elif self._stats["running"]:
            detail = (
                f"Watching {settings.IMAP_USERNAME or settings.IMAP_HOST} "
                f"every {settings.IMAP_POLL_SECONDS}s."
            )
        return {**self._stats, "enabled": self.enabled, "detail": detail,
                "poll_seconds": settings.IMAP_POLL_SECONDS,
                "folders": folders()}

    # ------------------------------------------------------------- lifecycle

    def start(self) -> bool:
        """Starts the background loop. Returns False when not configured."""
        if not self.enabled:
            logger.info("Mailbox polling is not enabled; inbound orders arrive over the API only.")
            return False
        if self._thread and self._thread.is_alive():
            return True

        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="allied-mailbox-poller", daemon=True)
        self._thread.start()
        self._stats["running"] = True
        logger.info(
            f"Mailbox polling started against {settings.IMAP_HOST}/{settings.IMAP_FOLDER} "
            f"every {settings.IMAP_POLL_SECONDS}s."
        )
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._stats["running"] = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the loop outlives any single failure
                self._stats["errors"] += 1
                self._stats["last_error"] = f"{type(exc).__name__}: {exc}"
                logger.error(f"Mailbox poll failed: {exc}")
            self._stop.wait(max(15, settings.IMAP_POLL_SECONDS))

    # ------------------------------------------------------------------ work

    def poll_once(self) -> Dict[str, Any]:
        """Reads every folder once. Each message is handled once, by its Message-ID in the mail
        log, without marking it read in the mailbox. Safe to call directly, including in tests."""
        if self._daemon is None:
            from app.services.inbound_email_daemon import inbound_email_daemon

            self._daemon = inbound_email_daemon
        self._stats["polls"] += 1
        self._stats["last_poll_at"] = datetime.now(timezone.utc).isoformat()
        totals = {"ingested": 0, "orders_created": 0, "logged_only": 0}
        conn = self._connect()
        try:
            for folder in folders():
                if self._stop.is_set():
                    break
                for key, n in self._poll_folder(conn, folder).items():
                    totals[key] += n
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - already closing
                pass
        self._stats["messages_ingested"] += totals["ingested"] + totals["orders_created"]
        self._stats["orders_created"] += totals["orders_created"]
        if any(totals.values()):
            logger.info(f"Mailbox poll: {totals['ingested'] + totals['orders_created']} handled, "
                        f"{totals['logged_only']} logged only, {totals['orders_created']} order(s) created.")
        return totals

    def _poll_folder(self, conn: imaplib.IMAP4, folder: str) -> Dict[str, int]:
        from app.services import mail_log

        out = {"ingested": 0, "orders_created": 0, "logged_only": 0}
        # Read only, unless messages are to be marked read: otherwise the mailbox is never changed.
        typ, _ = conn.select(_quote(folder), readonly=not settings.IMAP_MARK_SEEN)
        if typ != "OK":
            logger.warning(f"Mail folder {folder} could not be opened; skipped.")
            return out
        since = (datetime.now(timezone.utc) - timedelta(days=settings.IMAP_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        typ, data = conn.search(None, "SINCE", since)
        nums = data[0].split() if typ == "OK" and data and data[0] else []
        ids = _message_ids(conn, nums)
        seen = mail_log.known([i for i in ids.values() if i])
        act_after = datetime.now(timezone.utc) - timedelta(hours=settings.IMAP_ACT_ON_HOURS)
        for num in nums:
            if self._stop.is_set():
                break
            if ids.get(num) and ids[num] in seen:
                continue
            typ, raw = conn.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not isinstance(raw[0], tuple):
                continue
            handled = self._one(email.message_from_bytes(raw[0][1]), folder, act_after)
            if handled is None:
                continue
            out[handled] += 1
            if handled != "logged_only" and settings.IMAP_MARK_SEEN:
                try:
                    conn.store(num, "+FLAGS", "\\Seen")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Could not mark message seen: {exc}")
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return out

    def _one(self, msg: Message, folder: str, act_after: datetime) -> Optional[str]:
        """Handles one new message and logs it: ingested / orders_created / logged_only, or None
        if it was logged already (another poll got there first)."""
        from app.services import mail_log, mail_tags
        from app.services.inbound_email_daemon import InboundEmailMessage

        sender = email.utils.parseaddr(msg.get("From", ""))[1] or "unknown@unknown"
        subject = _decode(msg.get("Subject"))
        message_id = _decode(msg.get("Message-ID")).strip() or _fallback_id(msg)
        try:
            received = email.utils.parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
            if received is not None:
                received = received.astimezone(timezone.utc) if received.tzinfo else received.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            received = None
        recipients = ", ".join(a for _, a in email.utils.getaddresses(msg.get_all("To", []) + msg.get_all("Cc", [])) if a)
        way = mail_log.direction(sender, folder)
        body = _body_text(msg)
        tags = mail_tags.topics(subject, body)
        quiet = mail_tags.noise(msg, sender, subject) if way != "out" else None
        thread = mail_tags.thread_key(msg, subject, message_id)
        base = dict(message_id=message_id, folder=folder, sender=sender, sender_name=_decode(msg.get("From")),
                    recipients=recipients, subject=subject, received_at=received, direction_=way,
                    tags=([quiet] if quiet else []) + tags, thread_key=thread)
        if way == "out":
            done = mail_log.record(**base, category="sent", summary=" ".join(body.split())[:300],
                                   outcome="LOGGED_ONLY", needs_action=False)
            return "logged_only" if done else None
        if quiet:
            # Newsletters, auto-replies, bounces, invites, notifications: logged quietly, with no
            # AI read (it saves the free quota) and no alert, and hidden from the feed.
            done = mail_log.record(**base, category="noise", summary=" ".join(body.split())[:300],
                                   outcome="LOGGED_ONLY", needs_action=False)
            return "logged_only" if done else None
        if received is not None and received < act_after:
            # Older mail (the backlog on first start) is recorded, never acted on, so an old
            # thread can't raise orders, RFQs or drafts today.
            done = mail_log.record(**base, category="earlier", summary=" ".join(body.split())[:300],
                                   outcome="LOGGED_ONLY", needs_action=False)
            return "logged_only" if done else None
        message = InboundEmailMessage(
            email_id=message_id, sender_email=sender, sender_name=_decode(msg.get("From")), subject=subject,
            body_text=body,
            attachments=[{"filename": a["filename"], "mime": a["mime"], "size_bytes": len(a["content"]),
                          "content": a["content"]} for a in _attachments(msg)],
        )
        try:
            result = self._daemon.process_inbound_email(message)
        except Exception as exc:  # noqa: BLE001 - logged for a person; not retried every poll
            self._stats["errors"] += 1
            self._stats["last_error"] = f"{type(exc).__name__}: {exc}"
            logger.error(f"Could not process message {message_id}: {exc}")
            mail_log.record(**base, category="other", summary=f"Could not be read automatically: {exc}"[:500],
                            outcome="FAILED", needs_action=True)  # waits in the feed and the daily summary
            return "ingested"
        category, needs_action = mail_log.classify(result.status)
        urgent = "urgent" in tags and way == "in"
        if not mail_log.record(**base, category=category, summary=result.notes, outcome=result.status,
                               needs_action=needs_action or urgent):
            return None
        # Only what can't wait alerts at once, and once per conversation a day; the rest that
        # needs a person waits in the feed and the daily summary.
        if (needs_action or urgent) and mail_log.alert_now(category, tags):
            mail_log.alert(category if category in mail_log.TITLED else "urgent", thread, sender, subject, result.notes)
        return "orders_created" if getattr(result, "sales_order_number", None) else "ingested"

    def _connect(self) -> imaplib.IMAP4:
        # A verified TLS context (the default one checks nothing) and a timeout, so a
        # stalled server can't hang the poller thread.
        if settings.IMAP_USE_SSL:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT,
                                                    ssl_context=ssl.create_default_context(), timeout=30)
        else:
            conn = imaplib.IMAP4(settings.IMAP_HOST, settings.IMAP_PORT, timeout=30)
            if settings.IMAP_USERNAME:  # never send the password in the clear
                conn.starttls(ssl_context=ssl.create_default_context())
        if settings.IMAP_USERNAME:
            conn.login(settings.IMAP_USERNAME, settings.IMAP_PASSWORD)
        return conn


mailbox_poller = MailboxPoller()

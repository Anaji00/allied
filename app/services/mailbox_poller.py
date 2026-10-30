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
import threading
import time
from datetime import datetime, timezone
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
        return bool(settings.IMAP_POLL_ENABLED and settings.IMAP_HOST)

    def status(self) -> Dict[str, Any]:
        detail = "Polling is switched off."
        if not settings.IMAP_HOST:
            detail = "No mailbox configured; orders must be handed to the system."
        elif not settings.IMAP_POLL_ENABLED:
            detail = "Mailbox configured but polling is switched off."
        elif self._stats["running"]:
            detail = (
                f"Watching {settings.IMAP_USERNAME or settings.IMAP_HOST} "
                f"every {settings.IMAP_POLL_SECONDS}s."
            )
        return {**self._stats, "enabled": self.enabled, "detail": detail,
                "poll_seconds": settings.IMAP_POLL_SECONDS,
                "folder": settings.IMAP_FOLDER}

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
        """Reads unseen messages once. Safe to call directly, including in tests."""
        from app.services.inbound_email_daemon import InboundEmailMessage

        if self._daemon is None:
            from app.services.inbound_email_daemon import inbound_email_daemon

            self._daemon = inbound_email_daemon

        ingested = 0
        created = 0
        self._stats["polls"] += 1
        self._stats["last_poll_at"] = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.select(settings.IMAP_FOLDER)
            typ, data = conn.search(None, "UNSEEN")
            if typ != "OK":
                return {"ingested": 0, "orders_created": 0}

            for num in (data[0].split() if data and data[0] else []):
                if self._stop.is_set():
                    break
                typ, raw = conn.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not raw or not raw[0]:
                    continue

                msg = email.message_from_bytes(raw[0][1])
                sender = email.utils.parseaddr(msg.get("From", ""))[1]
                message = InboundEmailMessage(
                    email_id=_decode(msg.get("Message-ID")) or f"IMAP-{num.decode()}",
                    sender_email=sender or "unknown@unknown",
                    sender_name=_decode(msg.get("From")),
                    subject=_decode(msg.get("Subject")),
                    body_text=_body_text(msg),
                    attachments=[
                        {"filename": a["filename"], "mime": a["mime"], "size_bytes": len(a["content"])}
                        for a in _attachments(msg)
                    ],
                )

                try:
                    result = self._daemon.process_inbound_email(message)
                    ingested += 1
                    if getattr(result, "sales_order_number", None):
                        created += 1
                except Exception as exc:  # noqa: BLE001
                    self._stats["errors"] += 1
                    self._stats["last_error"] = f"{type(exc).__name__}: {exc}"
                    logger.error(f"Could not process message {message.email_id}: {exc}")
                    # Left unseen on purpose, so it is retried and visible.
                    continue

                if settings.IMAP_MARK_SEEN:
                    try:
                        conn.store(num, "+FLAGS", "\\Seen")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"Could not mark message seen: {exc}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass

        self._stats["messages_ingested"] += ingested
        self._stats["orders_created"] += created
        if ingested:
            logger.info(f"Mailbox poll ingested {ingested} message(s), {created} order(s) created.")
        return {"ingested": ingested, "orders_created": created}

    def _connect(self) -> imaplib.IMAP4:
        if settings.IMAP_USE_SSL:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT)
        else:
            conn = imaplib.IMAP4(settings.IMAP_HOST, settings.IMAP_PORT)
        if settings.IMAP_USERNAME:
            conn.login(settings.IMAP_USERNAME, settings.IMAP_PASSWORD)
        return conn


mailbox_poller = MailboxPoller()

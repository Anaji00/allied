"""Sends the mail the system already drafts: purchase orders, dispute letters, repricing RFQs.

Before this existed, every outbound message in the system was composed, written
to the log, and dropped. The drafting was real; the sending was not. This
service is the last mile.

Three safety properties, in order of how much they matter:

1. Sending is off unless a host is configured *and* ``EMAIL_SEND_ENABLED`` is
   set. The default posture of a fresh install is to draft and record only.
2. ``EMAIL_REDIRECT_ALL_TO`` overrides every recipient. A pilot can run against
   real orders with real suppliers named in the body while nothing reaches
   them.
3. A message is recorded whether or not it was sent, with the reason it was
   not. "We never sent that" and "we never wrote that" are different failures
   and the outbox has to tell them apart.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger("allied_bridge")

# Outbox entries are kept for the operations screen. This is a display buffer,
# not the audit trail -- the audit log is the system of record.
_MAX_OUTBOX = 500


# Hosted services that refuse mail without a login. Any other host with no username is taken to be
# a relay that accepts mail by address (a local postfix, an office relay) and is used as it is.
_NEEDS_LOGIN = ("smtp.gmail.com", "smtp.office365.com", "smtp-mail.outlook.com", "smtp.mail.yahoo.com",
                "smtp.zoho.com", "smtp.sendgrid.net", "smtp.mailgun.org", "email-smtp.")


def _login_ready() -> bool:
    if settings.SMTP_USERNAME:
        return bool(settings.SMTP_PASSWORD)
    return not any(settings.SMTP_HOST.casefold().startswith(h) for h in _NEEDS_LOGIN)


class OutboundEmailService:
    """Drafts, records, and (when enabled) sends operational mail."""

    def __init__(self) -> None:
        self._outbox: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- state

    @property
    def send_enabled(self) -> bool:
        """True only when a host and its login are set and sending was switched on. A host with
        no login (Gmail, Microsoft 365) would refuse every message, so it stays draft-only."""
        return bool(settings.EMAIL_SEND_ENABLED and settings.SMTP_HOST and _login_ready())

    def status(self) -> Dict[str, Any]:
        """Describes the current posture, for the operations screen."""
        if not settings.SMTP_HOST:
            mode, detail = "DRAFT_ONLY", "No mail server configured."
        elif not settings.EMAIL_SEND_ENABLED:
            mode, detail = "DRAFT_ONLY", "Mail server configured but sending is switched off."
        elif not _login_ready():
            mode, detail = "DRAFT_ONLY", "Mail server set; waiting for SMTP_USERNAME and SMTP_PASSWORD (an app password)."
        elif settings.EMAIL_REDIRECT_ALL_TO:
            mode, detail = "REDIRECTED", f"All mail is redirected to {settings.EMAIL_REDIRECT_ALL_TO}."
        else:
            mode, detail = "LIVE", "Mail is delivered to real recipients."
        with self._lock:
            drafted = len(self._outbox)
            sent = sum(1 for m in self._outbox if m["status"] == "SENT")
        return {
            "mode": mode,
            "detail": detail,
            "send_enabled": self.send_enabled,
            "from_address": settings.EMAIL_FROM,
            "redirect_to": settings.EMAIL_REDIRECT_ALL_TO or None,
            "messages_drafted": drafted,
            "messages_sent": sent,
        }

    def get_outbox(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most recent messages first."""
        with self._lock:
            return list(reversed(self._outbox[-limit:]))

    def clear_outbox(self) -> None:
        with self._lock:
            self._outbox.clear()

    # ------------------------------------------------------------- recording

    def _record(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._outbox.append(entry)
            if len(self._outbox) > _MAX_OUTBOX:
                del self._outbox[: len(self._outbox) - _MAX_OUTBOX]
        return entry

    # ----------------------------------------------------------------- send

    def send(
        self,
        to: str | List[str],
        subject: str,
        body: str,
        category: str = "GENERAL",
        reference: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Records the message, and delivers it when sending is enabled.

        ``attachments`` entries are ``{"filename": str, "content": bytes,
        "mime": "application/pdf"}``. Never raises: a mail failure must not
        roll back an order that is otherwise complete, so the failure is
        recorded on the outbox entry and returned to the caller to act on.
        """
        recipients = [to] if isinstance(to, str) else list(to)
        intended = list(recipients)
        redirected = False

        if settings.EMAIL_REDIRECT_ALL_TO:
            recipients = [settings.EMAIL_REDIRECT_ALL_TO]
            redirected = True

        entry: Dict[str, Any] = {
            "message_id": f"MSG-{uuid.uuid4().hex[:10].upper()}",
            "category": category,
            "reference": reference,
            "to": recipients,
            "intended_to": intended,
            "redirected": redirected,
            "subject": subject,
            "body": body,
            "attachments": [a.get("filename") for a in (attachments or [])],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "DRAFTED",
            "detail": None,
        }

        if not self.send_enabled:
            entry["status"] = "DRAFTED"
            entry["detail"] = (
                "No mail server configured."
                if not settings.SMTP_HOST
                else "Sending is switched off; the draft was recorded only."
            )
            logger.info(f"[EMAIL DRAFT] {category} to {intended}: {subject}")
            return self._record(entry)

        try:
            msg = EmailMessage()
            msg["From"] = settings.EMAIL_FROM
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            if settings.EMAIL_REPLY_TO:
                msg["Reply-To"] = settings.EMAIL_REPLY_TO
            if redirected:
                # The body is unchanged, so a redirected pilot message reads
                # exactly as the real one would. The header carries the truth.
                msg["X-Allied-Intended-To"] = ", ".join(intended)
            msg.set_content(body)

            for att in attachments or []:
                mime = att.get("mime", "application/octet-stream")
                maintype, _, subtype = mime.partition("/")
                msg.add_attachment(
                    att["content"],
                    maintype=maintype or "application",
                    subtype=subtype or "octet-stream",
                    filename=att["filename"],
                )

            self._deliver(msg, recipients)
            entry["status"] = "SENT"
            entry["detail"] = f"Delivered to {', '.join(recipients)}"
            logger.info(f"[EMAIL SENT] {category} to {recipients}: {subject}")
        except Exception as exc:  # noqa: BLE001 - a mail failure is reported, never raised
            entry["status"] = "FAILED"
            entry["detail"] = f"{type(exc).__name__}: {exc}"
            logger.error(f"[EMAIL FAILED] {category} to {recipients}: {exc}")

        return self._record(entry)

    def _deliver(self, msg: EmailMessage, recipients: List[str]) -> None:
        """Opens a connection and sends. Separated so tests can replace it."""
        # Without a context, smtplib uses an unverified one: anyone in the path could read the password.
        tls = ssl.create_default_context()
        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30, context=tls) as smtp:
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg, to_addrs=recipients)
            return

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls(context=tls)
            elif settings.SMTP_USERNAME:
                raise RuntimeError("Refusing to send a mail password without TLS (set SMTP_USE_TLS or use port 465).")
            if settings.SMTP_USERNAME:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg, to_addrs=recipients)

    # ------------------------------------------------- operational shortcuts

    def send_purchase_order(
        self,
        vendor_name: str,
        vendor_email: str,
        po_number: str,
        body: str,
        pdf_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Sends a purchase order to a supplier."""
        attachments = None
        if pdf_bytes:
            attachments = [{
                "filename": f"PO_{po_number}.pdf",
                "content": pdf_bytes,
                "mime": "application/pdf",
            }]
        return self.send(
            to=vendor_email,
            subject=f"Purchase Order {po_number} - Allied Welding Systems",
            body=body,
            category="PURCHASE_ORDER",
            reference=po_number,
            attachments=attachments,
        )

    def send_dispute(
        self,
        vendor_name: str,
        vendor_email: str,
        invoice_number: str,
        body: str,
    ) -> Dict[str, Any]:
        """Sends a supplier-invoice dispute letter the reconciliation drafted."""
        return self.send(
            to=vendor_email,
            subject=f"Invoice {invoice_number} - Discrepancy Against Authorised Purchase Order",
            body=body,
            category="INVOICE_DISPUTE",
            reference=invoice_number,
        )

    def send_reprice_rfq(
        self,
        vendor_name: str,
        vendor_email: str,
        po_number: str,
        body: str,
    ) -> Dict[str, Any]:
        """Sends a request for refreshed pricing on a stale quote."""
        return self.send(
            to=vendor_email,
            subject=f"Updated Pricing Required - {po_number}",
            body=body,
            category="REPRICE_RFQ",
            reference=po_number,
        )


# Shared instance. The outbox has to be process-wide so the operations screen
# shows what the orchestrator produced.
outbound_email_service = OutboundEmailService()

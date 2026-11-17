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

import time
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
_FAIL_CLOSED = {"PURCHASE_ORDER", "PRICE_REQUEST"}      # vendor mail that is never sent without a ledger row
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
        for _try in range(3):                  # the mail may already have gone: the result is retried so the ledger does not stay ATTEMPTING
            if self._write_row(entry):
                break
            time.sleep(0.05)
        return entry

    @staticmethod
    def _ledger(entry: Dict[str, Any]) -> bool:
        """The ATTEMPTING row, written before a send (see _write_row)."""
        return OutboundEmailService._write_row(entry)

    @staticmethod
    def _write_row(entry: Dict[str, Any]) -> bool:
        """The send ledger row for this message: written (or, if an ATTEMPTING row was written before the send, updated) with what happened. Best effort
        for the final result: a database problem is logged and must never undo or hide a mail that was already sent. Returns whether it was written."""
        try:
            from app.core.database import SessionLocal
            from app.models.db_models import OutboundMailDB

            fields = dict(
                category=str(entry.get("category") or "GENERAL")[:32], reference=(str(entry["reference"])[:128] if entry.get("reference") else None),
                to_addr=", ".join(entry.get("to") or [])[:512], intended_to=", ".join(entry.get("intended_to") or [])[:512],
                status=str(entry.get("status") or "DRAFTED")[:16], redirected=bool(entry.get("redirected")),
                subject=str(entry.get("subject") or "")[:256], detail=(str(entry["detail"])[:400] if entry.get("detail") else None))
            mid = str(entry.get("message_id"))[:32]
            with SessionLocal() as db:
                row = db.query(OutboundMailDB).filter(OutboundMailDB.message_id == mid).first()
                if row is None:
                    db.add(OutboundMailDB(message_id=mid, **fields))
                else:
                    for k, v in fields.items():
                        setattr(row, k, v)
                db.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Could not write the send ledger row for {entry.get('message_id')}: {exc}")
            return False

    @staticmethod
    def prune_ledger(days: int = 180, batch: int = 1000) -> int:
        """Removes ledger rows for staff and other routine mail older than `days`. Mail to vendors (purchase orders and price requests) is kept for good:
        it is a record of what was sent to whom (customs records must be kept for years). Deletes in batches. Returns how many rows went."""
        from datetime import timedelta

        from sqlalchemy import func

        from app.core.database import SessionLocal
        from app.models.db_models import OutboundMailDB

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(30, int(days)))
        removed = 0
        with SessionLocal() as db:
            ids = [r[0] for r in db.query(OutboundMailDB.id).filter(OutboundMailDB.created_at < cutoff, func.upper(func.trim(OutboundMailDB.category)).notin_(tuple(_FAIL_CLOSED)))
                   .order_by(OutboundMailDB.id).limit(batch).all()]
            if ids:
                removed = db.query(OutboundMailDB).filter(OutboundMailDB.id.in_(ids)).delete(synchronize_session=False)
                db.commit()
        return removed

    @staticmethod
    def really_sent(category: str, reference: Optional[str] = None) -> set:
        """References of mail of this category that REALLY reached its recipient: SENT, and not redirected to the pilot test inbox. Read from the ledger,
        so it survives restarts and the in-memory outbox limit."""
        from app.core.database import SessionLocal
        from app.models.db_models import OutboundMailDB

        with SessionLocal() as db:
            q = db.query(OutboundMailDB.reference).filter(OutboundMailDB.category == category, OutboundMailDB.status == "SENT",
                                                          OutboundMailDB.redirected.is_(False), OutboundMailDB.reference.isnot(None))
            if reference is not None:
                q = q.filter(OutboundMailDB.reference == str(reference))
            return {str(r[0]) for r in q.all()}

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
        category = str(category or "GENERAL").strip().upper()          # "purchase_order" is the same kind as "PURCHASE_ORDER"
        recipients = [to] if isinstance(to, str) else list(to)
        from app.services.coordination.notify import is_placeholder

        recipients = [r for r in recipients if not is_placeholder(r)]     # a stand-in address can never receive mail
        intended = list(recipients)
        redirected = False

        if not recipients:
            entry = {"message_id": f"MSG-{uuid.uuid4().hex[:10].upper()}", "category": category, "reference": reference, "to": [], "intended_to": [],
                     "redirected": False, "subject": subject, "body": body, "attachments": [], "created_at": datetime.now(timezone.utc).isoformat(),
                     "status": "FAILED", "detail": "No real email address: only placeholder addresses were given."}
            return self._record(entry)

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

        # Written BEFORE the network call: if the process dies after the mail left but before the result is recorded, the ledger still shows an
        # ATTEMPTING row, so nobody mistakes that for "never sent". A purchase order is never sent when this row cannot be written.
        if not self._ledger({**entry, "status": "ATTEMPTING"}) and category in _FAIL_CLOSED:
            entry["status"] = "FAILED"
            entry["detail"] = "Not sent: the send ledger could not be written, so the app could not keep a record of this mail."
            return self._record(entry)

        try:
            msg = EmailMessage()
            from email.utils import make_msgid

            domain = (str(settings.EMAIL_FROM or "").rsplit("@", 1)[-1].strip(" >") or "allied.local")
            msg["Message-ID"] = make_msgid(idstring=entry["message_id"].lower(), domain=domain)          # searchable in the Sent folder when an interrupted send must be settled by hand
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
            try:
                from app.services.coordination import notify

                notify.notify(
                    "email_failed",
                    f"email-failed:{entry.get('reference') or category}:{','.join(recipients)}",
                    f"Email failed to send: {subject[:80]}",
                    f"Could not deliver {category} email to {', '.join(recipients)}: {type(exc).__name__}: {exc}",
                    severity="warning",
                    link="/orders",
                )
            except Exception:  # noqa: BLE001 - alert is best-effort
                logger.warning("Could not raise email failure notification")

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
        extra_attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Sends a purchase order to a supplier."""
        attachments = list(extra_attachments or [])
        if pdf_bytes:
            attachments.insert(0, {
                "filename": f"PO_{po_number}.pdf",
                "content": pdf_bytes,
                "mime": "application/pdf",
            })
        attachments = attachments or None
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

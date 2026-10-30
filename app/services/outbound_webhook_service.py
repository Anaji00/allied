"""Outbound webhook dispatcher for Slack and Microsoft Teams notifications.

Sends automated alerts for:
  - Order processing events (new order, Sage sync complete)
  - Vendor invoice discrepancies (3-way match failures)
  - Stale pricing halts (RFQ dispatched)
  - Exception resolutions
  - Compliance NCR generation

Delivery model: notifications are side-channel, so a slow or unreachable Slack
endpoint must never slow down or fail the business operation that raised the
alert. Synchronous callers hand the payload to a small worker pool and return
immediately; async callers can await delivery directly.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

import httpx

from app.config import settings
from app.core.logging import logger

# Small, bounded pool. Notifications are low-volume and must not accumulate
# unbounded threads if an endpoint starts hanging.
_dispatch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webhook")

_TIMEOUT_SECONDS = 10.0


class OutboundWebhookService:
    """Dispatches formatted webhook payloads to Slack and/or Microsoft Teams."""

    @property
    def slack_url(self) -> str:
        """Read at call time so configuration changes take effect without a restart."""
        return settings.SLACK_WEBHOOK_URL

    @property
    def teams_url(self) -> str:
        return settings.TEAMS_WEBHOOK_URL

    # ------------------------------------------------------------------
    # Payload construction
    # ------------------------------------------------------------------

    @staticmethod
    def _slack_payload(title: str, message: str, severity: str) -> dict:
        emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "🔴", "CRITICAL": "🚨"}.get(severity, "ℹ️")
        return {
            "text": f"{emoji} *{title}*\n{message}",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} {title}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": message}},
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"Severity: *{severity}* | Allied Universal Bridge v2.0"}
                    ],
                },
            ],
        }

    @staticmethod
    def _teams_payload(title: str, message: str, severity: str) -> dict:
        color = {"INFO": "0078D4", "WARNING": "FFA500", "ERROR": "FF0000", "CRITICAL": "8B0000"}.get(
            severity, "0078D4"
        )
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": title,
            "sections": [
                {"activityTitle": title, "activitySubtitle": f"Severity: {severity}", "text": message}
            ],
        }

    @staticmethod
    def _classify(response: httpx.Response, target: str, title: str) -> str:
        """Treats any 2xx as delivered; Teams answers 202 for accepted cards."""
        if 200 <= response.status_code < 300:
            logger.info(f"{target} notification sent: {title}")
            return "delivered"
        logger.warning(f"{target} webhook returned {response.status_code}: {response.text[:200]}")
        return f"error_{response.status_code}"

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def deliver(self, title: str, message: str, severity: str = "INFO") -> Dict[str, str]:
        """Posts to every configured endpoint, blocking until each completes."""
        results = {"slack": "skipped", "teams": "skipped"}
        targets = (
            ("slack", self.slack_url, self._slack_payload(title, message, severity)),
            ("teams", self.teams_url, self._teams_payload(title, message, severity)),
        )

        if not any(url for _, url, _ in targets):
            logger.debug(f"No webhook endpoints configured — notification skipped: {title}")
            return results

        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            for name, url, payload in targets:
                if not url:
                    continue
                try:
                    results[name] = self._classify(client.post(url, json=payload), name.title(), title)
                except Exception as exc:
                    logger.error(f"{name.title()} webhook failed: {exc}")
                    results[name] = "failed"
        return results

    async def send_notification(
        self,
        title: str,
        message: str,
        severity: str = "INFO",
        channel: Optional[str] = None,
    ) -> Dict[str, str]:
        """Awaitable delivery for callers already running in an event loop."""
        results = {"slack": "skipped", "teams": "skipped"}
        targets = (
            ("slack", self.slack_url, self._slack_payload(title, message, severity)),
            ("teams", self.teams_url, self._teams_payload(title, message, severity)),
        )

        if not any(url for _, url, _ in targets):
            logger.debug(f"No webhook endpoints configured — notification skipped: {title}")
            return results

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            for name, url, payload in targets:
                if not url:
                    continue
                try:
                    response = await client.post(url, json=payload)
                    results[name] = self._classify(response, name.title(), title)
                except Exception as exc:
                    logger.error(f"{name.title()} webhook failed: {exc}")
                    results[name] = "failed"
        return results

    def send_sync(
        self,
        title: str,
        message: str,
        severity: str = "INFO",
        wait: bool = False,
    ) -> Dict[str, str]:
        """Queues a notification from synchronous code.

        Previously this inspected the event loop and, finding one running,
        logged the message and returned "deferred" without ever sending it --
        which is the case for every call made from a request handler, so no
        notification was ever delivered. Delivery now happens on a worker
        thread regardless of the caller's context.

        Args:
            wait: block until delivery finishes and return the real per-endpoint
                status. Used by tests and by callers that need confirmation.
        """
        if not (self.slack_url or self.teams_url):
            logger.debug(f"No webhook endpoints configured — notification skipped: {title}")
            return {"slack": "skipped", "teams": "skipped"}

        future = _dispatch_pool.submit(self.deliver, title, message, severity)

        if wait:
            try:
                return future.result(timeout=_TIMEOUT_SECONDS * 2 + 5)
            except Exception as exc:
                logger.error(f"Webhook delivery failed for '{title}': {exc}")
                return {"slack": "failed", "teams": "failed"}

        # Surface a late failure in the log rather than discarding it silently.
        def _log_outcome(fut) -> None:
            try:
                outcome = fut.result()
                if any(v not in ("delivered", "skipped") for v in outcome.values()):
                    logger.warning(f"Webhook '{title}' delivery outcome: {outcome}")
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Webhook '{title}' raised during delivery: {exc}")

        future.add_done_callback(_log_outcome)
        return {"slack": "queued" if self.slack_url else "skipped",
                "teams": "queued" if self.teams_url else "skipped"}

    # ------------------------------------------------------------------
    # Typed helpers
    # ------------------------------------------------------------------

    def notify_discrepancy(
        self, invoice_number: str, vendor: str, discrepancies: list, variance_amount: float = 0.0
    ) -> Dict[str, str]:
        """Helper to send vendor invoice discrepancy alert."""
        title = f"Vendor Invoice Discrepancy: {vendor} ({invoice_number})"
        disc_text = "; ".join(discrepancies)
        message = (
            f"Invoice {invoice_number} from {vendor} has {len(discrepancies)} discrepancies. "
            f"Variance: ${variance_amount:,.2f}. Details: {disc_text}"
        )
        return self.send_sync(title=title, message=message, severity="WARNING")

    def notify_order_event(
        self, order_id: str, customer: str, status: str, total_revenue: float = 0.0
    ) -> Dict[str, str]:
        """Helper to send order status notification."""
        title = f"Order {status}: {order_id} ({customer})"
        message = f"Order {order_id} for {customer} is now {status}. Total Value: ${total_revenue:,.2f}"
        return self.send_sync(title=title, message=message, severity="INFO")

    def notify_stale_pricing(self, vendor: str, stale_skus: list) -> Dict[str, str]:
        """Helper to send stale pricing halt notification."""
        title = f"Stale Vendor Pricing Halt: {vendor}"
        message = (
            f"Orchestration paused for {vendor} due to price quotes older than "
            f"{settings.VENDOR_PRICE_STALENESS_DAYS} days for SKUs: {', '.join(stale_skus)}. "
            f"RFQ dispatched."
        )
        return self.send_sync(title=title, message=message, severity="WARNING")


# Global instances
outbound_webhooks = OutboundWebhookService()
outbound_webhook_service = outbound_webhooks

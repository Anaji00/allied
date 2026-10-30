"""Service for validating vendor pricing staleness and halting orchestration if outdated.

A vendor line item whose last-quoted date is older than
VENDOR_PRICE_STALENESS_DAYS (default: 60) halts the order and triggers an RFQ.
Prices are refreshed through `register_price`, which the operations console
calls when a buyer approves a current price or logs a new quote.

Items with no quote on record are not treated as stale: freshness is only
asserted for prices the business is actually tracking.
"""

import threading
from typing import Dict, Any, List, Optional
from datetime import date, timedelta

from app.config import settings
from app.core.logging import logger
from app.models.orders import OrderLineItem
from app.services.monday_client import MondayClient
from app.core.container import get_monday_client


class VendorRepriceHaltError(Exception):
    """Raised when vendor pricing is stale and the transaction must halt for repricing."""

    def __init__(self, message: str, vendor: str, items: List[str],
                 stale_by_vendor: Optional[Dict[str, List[str]]] = None):
        super().__init__(message)
        # `message` is kept as an attribute because handlers log it directly;
        # relying on str(exc) alone previously produced an AttributeError.
        self.message = message
        self.vendor = vendor
        self.items = items
        # Every affected vendor, not just the first one.
        self.stale_by_vendor = stale_by_vendor or {vendor: items}


class VendorRepriceService:
    """Checks pricing freshness and dispatches RFQ emails to vendors if stale."""

    def __init__(self, monday_client: MondayClient = None):
        self.monday = monday_client or get_monday_client()
        self.staleness_days = settings.VENDOR_PRICE_STALENESS_DAYS
        # Pricing registry: item_id -> {vendor, last_quoted, unit_cost}
        self._price_registry: Dict[str, Dict[str, Any]] = {}
        # Read by order orchestration and written by the operations console,
        # concurrently, so the registry is guarded.
        self._lock = threading.RLock()
        if settings.SEED_DEMO_DATA:
            # Demo fixture: a Nibrock quote that is past the staleness limit.
            # Outside demo mode this must not exist, or the flagship SKU is
            # permanently unorderable with no way to clear it.
            self._price_registry["PLATE-300XL-38"] = {
                "vendor": "NIBROCK",
                "unit_cost": 110.0,
                "last_quoted": date.today() - timedelta(days=self.staleness_days + 14),
            }

    def register_price(
        self, item_id: str, vendor: str, unit_cost: float, quoted_date: Optional[date] = None
    ) -> None:
        """Registers or updates a vendor price quote in the pricing registry."""
        with self._lock:
            self._price_registry[item_id.upper()] = {
                "vendor": vendor,
                "unit_cost": unit_cost,
                "last_quoted": quoted_date or date.today(),
            }

    def validate_pricing_freshness(
        self, vendor_lines: Dict[str, List[OrderLineItem]], po_number: str
    ) -> None:
        """
        Validates if vendor pricing is stale using timestamp-based staleness checks.
        
        For each non-STOCK vendor line item:
        1. Look up the last-quoted date from the pricing registry
        2. If a quote exists and is older than VENDOR_PRICE_STALENESS_DAYS, flag as stale
        3. If any stale items are found, halt execution and trigger RFQ
        """
        stale_vendors: Dict[str, List[str]] = {}
        cutoff_date = date.today() - timedelta(days=self.staleness_days)

        for vendor, lines in vendor_lines.items():
            if vendor == "STOCK":
                continue

            stale_items = []
            for line in lines:
                item_key = (line.item_id or "").upper()
                if not item_key:
                    continue

                with self._lock:
                    price_record = self._price_registry.get(item_key)
                # If explicitly tracked in registry and older than cutoff
                if price_record and price_record.get("last_quoted", date.today()) < cutoff_date:
                    days_old = (date.today() - price_record["last_quoted"]).days
                    stale_items.append(line.item_id)
                    logger.info(
                        f"Stale price for {item_key} from {vendor} — "
                        f"quoted {days_old} days ago (limit: {self.staleness_days}d)"
                    )

            if stale_items:
                stale_vendors[vendor] = stale_items

        if stale_vendors:
            for vendor, items in stale_vendors.items():
                logger.warning(f"Stale pricing detected for {vendor}: {items}")
                self.dispatch_reprice_request(vendor, items, po_number)

            summary = "; ".join(f"{v}: {', '.join(i)}" for v, i in stale_vendors.items())
            first_vendor = next(iter(stale_vendors))
            raise VendorRepriceHaltError(
                f"Order orchestration halted due to stale vendor pricing ({summary}).",
                vendor=first_vendor,
                items=stale_vendors[first_vendor],
                stale_by_vendor=stale_vendors,
            )

    def dispatch_reprice_request(self, vendor: str, items: List[str], po_number: str) -> None:
        """Drafts an RFQ to the vendor requesting updated pricing, and sends it."""
        email_body = (
            f"Vendor: {vendor}\n\n"
            f"Please provide updated pricing and lead times for the following items:\n"
            f"{', '.join(items)}\n\n"
            f"Our previous pricing is older than {self.staleness_days} days, so we are unable "
            f"to commit an order against it. Please advise by return.\n\n"
            f"Thank you,\n"
            f"Allied Welding Systems Purchasing"
        )
        logger.info(f"Drafted repricing RFQ to {vendor} for {po_number}")

        # Reaches the supplier rather than only the log. No-ops safely into the
        # outbox when sending is not configured.
        try:
            from app.core.container import get_entity_matcher
            from app.services.outbound_email_service import outbound_email_service

            rec, _ = get_entity_matcher().match_vendor(vendor)
            vendor_email = (rec or {}).get("email", "") or ""
            if vendor_email:
                outbound_email_service.send_reprice_rfq(
                    vendor_name=vendor,
                    vendor_email=vendor_email,
                    po_number=po_number,
                    body=email_body,
                )
            else:
                logger.info(f"No address on file for {vendor}; repricing RFQ not addressed.")
        except Exception as e:
            logger.warning(f"Could not queue repricing RFQ to {vendor}: {e}")

        # Post the 'Awaiting Updated Pricing' status to Monday.com
        try:
            self.monday.post_pulse_update(
                pulse_id=0,  # Need actual item_id in practice
                message=f"Halted for repricing from {vendor} for {items}",
            )
        except Exception as e:
            logger.warning(f"Failed to update Monday.com status: {e}")


# Shared instance. Freshness state must be process-wide: a price approved in the
# operations console has to unblock the orchestrator that rejected the order, so
# every caller works against the same registry rather than a private copy.
vendor_reprice_service = VendorRepriceService()

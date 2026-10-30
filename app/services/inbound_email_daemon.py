"""Inbound Autonomous Email Daemon for Allied Welding Systems.

Automatically ingests, parses, and converts customer PO emails into executed
Sage 50 sales orders, dispatches vendor purchase orders, and issues instant
customer order acknowledgments.
"""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import re
from typing import Any, Dict, List, Optional, Union
import uuid
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.models.orders import InboundOrderRequest, OrderLineItem
from app.services.entity_matcher import EntityMatcher
from app.services.order_orchestrator import OrderOrchestrator
from app.services.monday_client import MondayClient


# Cap on the in-memory feed; the database holds the durable record.
_MAX_FEED = 500


class InboundEmailMessage(BaseModel):
    """Raw or webhook-received inbound email payload."""
    email_id: str = Field(default_factory=lambda: f"EML-{uuid.uuid4().hex[:8].upper()}")
    sender_email: str
    sender_name: Optional[str] = None
    subject: str
    body_text: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EmailIngestionResult(BaseModel):
    """Result of autonomous email ingestion and conversion."""
    email_id: str
    status: str  # PROCESSED_ORDER_CREATED, RFQ_STAGED, FLAGGED_FOR_REVIEW
    customer_name: str
    customer_po_number: str
    sales_order_number: Optional[str] = None
    purchase_orders: Union[Dict[str, str], List[str]] = Field(default_factory=dict)
    line_items_count: int
    customer_acknowledgment_email: Optional[str] = None
    monday_pulse_id: Optional[int] = None
    notes: str


class InboundEmailDaemon:
    """Autonomous email worker running zero-click order processing."""

    def __init__(
        self,
        orchestrator: Optional[OrderOrchestrator] = None,
        entity_matcher: Optional[EntityMatcher] = None,
        monday_client: Optional[MondayClient] = None,
    ):
        from app.core.container import get_orchestrator

        self.orchestrator = orchestrator or get_orchestrator()
        self.matcher = entity_matcher or self.orchestrator.entity_matcher
        self.monday = monday_client or self.orchestrator.monday_client
        self._inbox_feed: List[Dict[str, Any]] = []
        # Seeding runs full order orchestration (real Sage documents, Monday
        # posts, DB writes), so it must never happen just because the module
        # was imported.
        if settings.SEED_DEMO_DATA:
            self._seed_sample_inbox()

    def _seed_sample_inbox(self) -> None:
        """Seed a few realistic sample emails for testing and dashboard review."""
        sample_emails = [
            InboundEmailMessage(
                email_id="EML-OC-9921",
                sender_email="purchasing@owenscorning.com",
                sender_name="Mark Richardson (Owens Corning)",
                subject="PO-9921: Hammermill Screens for Edmonton Plant",
                body_text=(
                    "Hello Allied Team,\n\n"
                    "Please find attached our purchase order PO-9921.\n"
                    "Line 1: 2x Hammermill Screens for Edmonton\n\n"
                    "Ship to our Edmonton plant facility by next Friday.\n\n"
                    "Thank you,\nMark Richardson\nOwens Corning Purchasing"
                ),
            ),
            InboundEmailMessage(
                email_id="EML-KNAUF-4412",
                sender_email="procurement@knaufinsulation.com",
                sender_name="Sarah Jenkins (Knauf Insulation)",
                subject="URGENT REORDER - Lanett Facility Ceramic Hose",
                body_text=(
                    "Good morning,\n\n"
                    "We need to reorder 2 units of Ceramic Hose for the Lanett line.\n"
                    "Customer PO Number: KNF-2026-8810.\n"
                    "Standard delivery to Lanett, AL.\n\n"
                    "Best regards,\nSarah Jenkins"
                ),
            ),
        ]
        for eml in sample_emails:
            try:
                self.process_inbound_email(eml)
            except Exception as e:
                logger.warning(f"Error seeding sample email {eml.email_id}: {e}")

    def process_inbound_email(self, email: InboundEmailMessage) -> EmailIngestionResult:
        """
        Parses inbound email body and attachments, extracts order entities,
        and dispatches end-to-end order orchestration.
        """
        logger.info(f"Processing inbound email [{email.email_id}] from {email.sender_email}: '{email.subject}'")

        # Freight forwarders' status emails update shipments, not orders.
        from app.services.coordination import shipments as _shipments

        if _shipments.is_forwarder(email.sender_email):
            outcome = _shipments.ingest_forwarder_email(email.sender_email, email.subject, email.body_text)
            result = EmailIngestionResult(
                email_id=email.email_id,
                status="SHIPMENT_UPDATE" if outcome.get("applied") else "SHIPMENT_UPDATE_NEEDS_REVIEW",
                customer_name=email.sender_name or email.sender_email,
                customer_po_number="",
                line_items_count=0,
                notes=f"Forwarder email: {outcome.get('reason') or 'applied to shipment'}",
            )
            self._inbox_feed.insert(0, {**result.model_dump(), "sender_email": email.sender_email,
                                        "subject": email.subject, "received_at": email.received_at.isoformat()})
            del self._inbox_feed[_MAX_FEED:]
            return result

        # 0. AI sorting (optional). Mail that is clearly not an order is filed
        # instead of being turned into one, and an order the AI is unsure of
        # goes to a person. When AI is off or down, the pattern parser below
        # runs exactly as before.
        ai = self._ai_read(email)
        if ai is not None and (ai.category != "order" or not self._ai_order_usable(ai)):
            return self._ai_hold(email, ai)

        # 1. Resolve Customer Identity
        # Match from domain or sender name or body
        customer_search_str = email.sender_name or email.sender_email
        if "@" in email.sender_email:
            domain_part = email.sender_email.split("@")[1].split(".")[0]
            if "owens" in domain_part or "corning" in domain_part:
                customer_search_str = "Owens Corning"
            elif "knauf" in domain_part:
                customer_search_str = "Knauf Insulation"
            elif "vulcan" in domain_part:
                customer_search_str = "Vulcan Materials"
            elif "iko" in domain_part:
                customer_search_str = "IKO Southwest"

        matched_cust, _ = self.matcher.match_customer(customer_search_str)
        customer_name = matched_cust["customer_name"]

        # 2. Extract Customer PO Number
        po_number = self._extract_po_number(email)

        # 3. Parse Line Items from Body Text or Attachments
        line_items = self._extract_line_items_from_text(email.body_text)
        if not line_items:
            # Try extracting from subject
            line_items = self._extract_line_items_from_text(email.subject)
        if not line_items:
            # Try combined search
            line_items = self._extract_line_items_from_text(f"{email.subject}\n{email.body_text}")
        if not line_items:
            # Fallback default item based on subject, attempting catalog match
            default_item_id = None
            default_price = Decimal("150.00")
            default_vendor = "STOCK"
            try:
                matched_rec, _ = self.matcher.match_item(email.subject, min_confidence=50.0)
                default_item_id = matched_rec["item_id"]
                default_price = Decimal(str(matched_rec.get("base_price", "150.00")))
                default_vendor = matched_rec.get("preferred_vendor", "STOCK")
            except Exception:
                pass

            line_items = [
                OrderLineItem(
                    item_id=default_item_id,
                    raw_item_text=email.subject,
                    quantity=Decimal("1"),
                    unit_price=default_price,
                    preferred_vendor=default_vendor,
                )
            ]

        if ai is not None:
            # A confident AI read of an order beats the pattern parser, which
            # only knows a handful of phrasings.
            po_number = ai.po_number
            line_items = [
                OrderLineItem(
                    raw_item_text=line.description,
                    quantity=Decimal(str(line.quantity)),
                    unit_price=Decimal(str(line.unit_price or 0)),
                )
                for line in ai.lines
            ]

        # 4. Construct Inbound Order Request
        inbound_req = InboundOrderRequest(
            customer_name=customer_name,
            customer_po_number=po_number,
            shipping_address=matched_cust.get("shipping_address", "Customer Master Destination"),
            line_items=line_items,
            notes=(
                f"Autonomous Email Ingestion from {email.sender_email} (Ref: {email.email_id})"
                + (" -- no customer PO found in the email; needs a PO number." if po_number.startswith("PO-NOPO-") else "")
            ),
            payment_terms=matched_cust.get("payment_terms", "Net 30 Days"),
        )

        # 5. Execute Order Orchestration
        # Email orders have no board item. A random number here was posted to
        # as if it were one, which on a live account comments on whichever real
        # item happens to own that id.
        pulse_id = None
        try:
            dual_sided_result = self.orchestrator.process_inbound_order(inbound_req)
            so_number = dual_sided_result.sales_order_number
            pos = dual_sided_result.purchase_orders
            status_val = "PROCESSED_ORDER_CREATED"
            notes = f"Successfully generated Sage SO #{so_number} and split POs {pos}"
            ack_body = self._generate_order_acknowledgment(
                customer_name=customer_name,
                po_number=po_number,
                sales_order_number=so_number,
                items=line_items,
                recipient_email=email.sender_email,
            )
        except Exception as e:
            logger.warning(f"Order orchestration exception for email {email.email_id}: {e}")
            from app.services.exception_queue_service import exception_queue_service, ExceptionItem
            so_number = f"SO-PENDING-{email.email_id[-4:]}"
            pos = []
            status_val = "FLAGGED_FOR_REVIEW"
            notes = f"Order held for review: {str(e)}"
            exception_queue_service.add_exception(
                ExceptionItem(
                    exception_type="STALE_VENDOR_PRICE" if "stale" in str(e).lower() else "ORDER_EXCEPTION",
                    severity="HIGH",
                    title=f"Inbound Email Review: {customer_name} ({po_number})",
                    description=f"Inbound order from {email.sender_email} held: {str(e)}",
                    reference_id=po_number,
                    vendor_or_customer=customer_name,
                    available_actions=[
                        {"action": "APPROVE_AND_STAGE", "label": "Approve & Stage Order"},
                        {"action": "REQUEST_CUSTOMER_CLARIFICATION", "label": "Email Customer"},
                    ],
                )
            )
            ack_body = (
                f"From: Allied Welding Systems Orders <orders@alliedweldingsystems.com>\n"
                f"To: {email.sender_email}\n"
                f"Subject: Order Received - Under Review (Your PO: {po_number})\n\n"
                f"Dear {customer_name} Team,\n\n"
                f"We have received your purchase order #{po_number}. Our engineering and pricing desk "
                f"is verifying current mill lead times and stock availability.\n\n"
                f"We will provide official order confirmation within 24 hours.\n\n"
                f"Sincerely,\nAllied Operations"
            )

        result = EmailIngestionResult(
            email_id=email.email_id,
            status=status_val,
            customer_name=customer_name,
            customer_po_number=po_number,
            sales_order_number=so_number,
            purchase_orders=pos,
            line_items_count=len(line_items),
            customer_acknowledgment_email=ack_body,
            monday_pulse_id=pulse_id,
            notes=notes,
        )

        feed_entry = {
            **result.model_dump(),
            "sender_email": email.sender_email,
            "subject": email.subject,
            "received_at": email.received_at.isoformat(),
        }
        self._inbox_feed.insert(0, feed_entry)
        del self._inbox_feed[_MAX_FEED:]
        return result

    # ------------------------------------------------------------------ AI

    def _ai_read(self, email: InboundEmailMessage):
        if not settings.AI_EMAIL_TRIAGE_ENABLED:
            return None
        from app.services.ai.features import triage_email
        from app.services.ai.providers import AIUnavailableError

        try:
            triage, _ = triage_email(
                email.sender_email, email.subject, email.body_text, email.attachments, reference=email.email_id
            )
            return triage
        except AIUnavailableError as exc:
            logger.warning(f"AI sorting unavailable for {email.email_id}; using the pattern parser. ({exc})")
            return None

    @staticmethod
    def _ai_order_usable(ai) -> bool:
        return bool(
            ai.confidence >= settings.AI_CONFIDENCE_THRESHOLD
            and ai.po_number
            and ai.lines
            and all(line.quantity > 0 and line.description for line in ai.lines)
        )

    def _ai_hold(self, email: InboundEmailMessage, ai) -> "EmailIngestionResult":
        """Files mail that is not an order, or parks an order the AI is unsure of."""
        if ai.category == "order":
            status_val = "FLAGGED_FOR_REVIEW"
            notes = (
                f"AI read this as an order but is not sure enough to raise it "
                f"(confidence {ai.confidence:.0%}; unclear: {', '.join(ai.uncertain_fields) or 'details'}). "
                f"{ai.summary}"
            )
            from app.services.exception_queue_service import ExceptionItem, exception_queue_service

            exception_queue_service.add_exception(
                ExceptionItem(
                    exception_type="ORDER_EXCEPTION",
                    severity="MEDIUM",
                    title=f"Check order email: {email.subject[:120]}",
                    description=notes,
                    reference_id=ai.po_number or email.email_id,
                    vendor_or_customer=ai.customer_name or email.sender_email,
                    available_actions=[
                        {"action": "APPROVE_AND_STAGE", "label": "Approve & Stage Order"},
                        {"action": "REQUEST_CUSTOMER_CLARIFICATION", "label": "Email Customer"},
                    ],
                )
            )
        else:
            status_val = f"SORTED_{ai.category.upper()}"
            notes = f"AI sorted this as {ai.category.replace('_', ' ')}; no order was created. {ai.summary}"

        result = EmailIngestionResult(
            email_id=email.email_id,
            status=status_val,
            customer_name=ai.customer_name or email.sender_name or email.sender_email,
            customer_po_number=ai.po_number or "",
            line_items_count=len(ai.lines),
            notes=notes,
        )
        self._inbox_feed.insert(0, {
            **result.model_dump(),
            "sender_email": email.sender_email,
            "subject": email.subject,
            "received_at": email.received_at.isoformat(),
        })
        del self._inbox_feed[_MAX_FEED:]
        return result

    # Labels that follow a "PO" token but are not the PO identifier itself.
    _PO_STOPWORDS = {"NUMBER", "NUM", "NO", "REF", "REFERENCE", "ATTACHED", "IS", "FOR", "ORDER"}

    # Customer PO prefixes that are part of the identifier, so "PO-" is not
    # prepended to them.
    _SELF_PREFIXED = {"KNF", "OC", "IKO", "VUL", "PO"}

    def _extract_po_number(self, email: InboundEmailMessage) -> str:
        """Extracts the customer PO identifier from an email subject/body.

        The previous single pattern matched the literal words "PO Number" in
        "Customer PO Number: KNF-2026-8810" and returned "PO NUMBER" as the
        customer's purchase order. Labelled forms are tried first, and any
        candidate that is only a label word is rejected.
        """
        haystack = f"{email.subject} {email.body_text}"

        patterns = (
            # "Customer PO Number: KNF-2026-8810", "PO #: 4518695782-2".
            # Here "PO"/"PO Number" is a label, so only the value is captured.
            r"\bP\.?O\.?\s*(?:NUMBER|NUM|NO|REF|#)?\s*[:#]\s*([A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*)",
            # "PO-KNF-MULTI-99", "PO-9921". Hyphen-joined, so the "PO-" is part
            # of the identifier and is captured with it -- capturing only the
            # remainder turned "PO-KNF-MULTI-99" into "KNF-MULTI-99".
            r"\b(P\.?O\.?-[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*)",
            # "our PO 9921" -- whitespace-separated label form.
            r"\bP\.?O\.?[\s#]+([A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*)",
            # Customer prefixes that stand alone, with no "PO" token nearby.
            r"\b((?:KNF|OC|IKO|VUL)-[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*)",
        )

        for pattern in patterns:
            for match in re.finditer(pattern, haystack, re.IGNORECASE):
                candidate = match.group(1).strip().strip(".,;:").upper()
                if not candidate or candidate in self._PO_STOPWORDS:
                    continue
                # An identifier carries either a digit ("9921") or internal
                # structure ("EDGE-QTY"). A bare alphabetic word is a label.
                if not any(ch.isdigit() for ch in candidate) and not re.search(r"[-_/]", candidate):
                    continue
                # Re-attach the PO- prefix unless the identifier already carries
                # a customer prefix that stands on its own. Matching any 2-4
                # letter token here turned "PO-EDGE-QTY" into "EDGE-QTY".
                if candidate.split("-")[0] in self._SELF_PREFIXED:
                    return candidate
                return candidate if candidate.startswith("PO-") else f"PO-{candidate}"

        # No PO in the message. The synthetic number must be unique per email:
        # it is the idempotency key for order creation, and the old
        # "PO-" + last 4 characters of the Message-ID was "PO-COM>" for nearly
        # every message, so every later PO-less order was dropped as a
        # duplicate of the first. Hashing the full id keeps redeliveries of
        # the same email idempotent while separating different emails.
        digest = hashlib.sha256((email.email_id or "").encode("utf-8")).hexdigest()[:12].upper()
        return f"PO-NOPO-{digest}"

    def _extract_line_items_from_text(self, text: str) -> List[OrderLineItem]:
        """Extracts line items by matching text against catalog and aliases."""
        lines: List[OrderLineItem] = []
        raw_lines = text.split("\n")

        # Candidates come from the matcher's prefix index, longest key first so
        # PLATE-300XL-38 matches before the shorter 300XL inside it. Narrowing
        # per line keeps the cost independent of catalogue size.

        for l in raw_lines:
            cleaned = l.strip()
            if not cleaned or len(cleaned) < 3:
                continue

            # Skip pleasantries/headers unless they contain order keywords
            cleaned_lower = cleaned.lower()
            if any(cleaned_lower.startswith(p) for p in ["hello", "dear", "thank", "ship to", "customer po", "best regards", "regards"]):
                continue

            # Strip line/item numbering prefixes e.g. "Line 1:", "Item 2:", "1.", "1)", "1 - "
            content_without_prefix = re.sub(
                r"^(?:line\s*\d+:?|item\s*\d+:?|\d+[\.\)\-:]\s*)",
                "",
                cleaned,
                flags=re.IGNORECASE,
            ).strip()

            # Extract quantity
            qty = Decimal("1")
            qty_match = re.search(
                r"(?:\((\d+(?:\.\d+)?)\)|\b(\d+(?:\.\d+)?)\s*(?:x|units?|spools?|pcs?|ea|sheets?|ft|feet)\b)",
                content_without_prefix,
                re.IGNORECASE,
            )
            if qty_match:
                try:
                    parsed_str = qty_match.group(1) or qty_match.group(2)
                    parsed_qty = Decimal(parsed_str)
                    if 0 < parsed_qty < 10000:
                        qty = parsed_qty
                except Exception:
                    pass
            else:
                lead_match = re.match(r"^(\d+)\s+", content_without_prefix)
                if lead_match:
                    try:
                        parsed_qty = Decimal(lead_match.group(1))
                        if 0 < parsed_qty < 10000:
                            qty = parsed_qty
                    except Exception:
                        pass

            matched_item = None

            # 1. Search for exact SKU in line (longer SKUs checked first).
            # The cheap containment test runs first so the word-boundary regex
            # is only built for a SKU that could actually match this line.
            cleaned_upper = cleaned.upper()
            for item_id, item_rec in self.matcher.scan_candidates(cleaned_upper):
                if item_id not in cleaned_upper:
                    continue
                if re.search(rf"\b{re.escape(item_id)}\b", cleaned, re.IGNORECASE):
                    matched_item = item_rec
                    break

            # 2. Search for alias in line
            if not matched_item:
                for alias_key, target_sku in self.matcher.scan_candidates(cleaned_upper, aliases=True):
                    if alias_key in cleaned_upper:
                        if target_sku in self.matcher.items_cache:
                            matched_item = self.matcher.items_cache[target_sku]
                            break

            # 3. Fuzzy match fallback on cleaned line
            if not matched_item:
                try:
                    item_record, confidence = self.matcher.match_item(cleaned, min_confidence=60.0)
                    matched_item = item_record
                except Exception:
                    pass

            if matched_item:
                lines.append(
                    OrderLineItem(
                        item_id=matched_item["item_id"],
                        raw_item_text=cleaned,
                        quantity=qty,
                        unit_price=Decimal(str(matched_item.get("base_price", "250.00"))),
                        preferred_vendor=matched_item.get("preferred_vendor", "STOCK"),
                    )
                )

        # 4. If line-by-line parsing yielded nothing, search entire text
        if not lines:
            qty = Decimal("1")
            qty_match = re.search(r"\b(\d+)\s*(?:x|units?|spools?|pcs?|ea|sheets?)?\b", text, re.IGNORECASE)
            if qty_match:
                try:
                    parsed_qty = Decimal(qty_match.group(1))
                    if 0 < parsed_qty < 1000:
                        qty = parsed_qty
                except Exception:
                    pass

            matched_item = None
            text_upper = text.upper()
            for item_id, item_rec in self.matcher.scan_candidates(text_upper):
                if item_id not in text_upper:
                    continue
                if re.search(rf"\b{re.escape(item_id)}\b", text, re.IGNORECASE):
                    matched_item = item_rec
                    break

            if not matched_item:
                for alias_key, target_sku in self.matcher.scan_candidates(text_upper, aliases=True):
                    if alias_key in text_upper:
                        if target_sku in self.matcher.items_cache:
                            matched_item = self.matcher.items_cache[target_sku]
                            break

            if matched_item:
                lines.append(
                    OrderLineItem(
                        item_id=matched_item["item_id"],
                        raw_item_text=matched_item.get("description", matched_item["item_id"]),
                        quantity=qty,
                        unit_price=Decimal(str(matched_item.get("base_price", "250.00"))),
                        preferred_vendor=matched_item.get("preferred_vendor", "STOCK"),
                    )
                )

        return lines

    def _generate_order_acknowledgment(
        self,
        customer_name: str,
        po_number: str,
        sales_order_number: str,
        items: List[OrderLineItem],
        recipient_email: str,
    ) -> str:
        """Drafts instant customer acknowledgment email."""
        item_rows = "\n".join([f"  • {it.quantity}x {it.item_id or it.raw_item_text}" for it in items])
        return (
            f"From: Allied Welding Systems Orders <orders@alliedweldingsystems.com>\n"
            f"To: {recipient_email}\n"
            f"Subject: Order Acknowledgment - Allied Order #{sales_order_number} (Your PO: {po_number})\n\n"
            f"Dear {customer_name} Purchasing Team,\n\n"
            f"Thank you for your order. We have successfully logged and processed PO #{po_number} "
            f"under Allied Sales Order #{sales_order_number}.\n\n"
            f"Order Breakdown:\n"
            f"{item_rows}\n\n"
            f"Our production scheduling team has verified material allocation and mill test certificates (MTR). "
            f"Your order has been queued for immediate staging and freight dispatch.\n\n"
            f"Estimated Dispatch Date: Within 3-5 business days\n"
            f"Shipping Terms: FOB Origin / Motor Freight\n\n"
            f"You will receive an automated shipping notification with your official packing list as soon as the carrier collects the freight.\n\n"
            f"Sincerely,\n"
            f"Allied Autonomous Bridge Operations\n"
            f"Allied Welding Systems | Support: (800) 555-ALLIED"
        )

    def get_inbox_feed(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns the live feed of ingested emails and order conversions."""
        return self._inbox_feed[:limit]


# Global daemon instance
inbound_email_daemon = InboundEmailDaemon()

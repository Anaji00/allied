"""Central Operations Exceptions & Discrepancies Queue Service.

Maintains triage queue for operational exceptions (stale vendor pricing,
invoice cost variances, unmatched SKUs) with 1-click resolution actions.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import re
import threading
import uuid
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.services.monday_client import MondayClient
from app.core.database import SessionLocal
from app.services.database_service import DatabaseService
from app.core.audit import audit_log
from app.core.container import get_monday_client


class ExceptionItem(BaseModel):
    """Single operational discrepancy requiring one-click resolution."""
    exception_id: str = Field(default_factory=lambda: f"EXC-{uuid.uuid4().hex[:6].upper()}")
    exception_type: str  # STALE_VENDOR_PRICE, INVOICE_COST_VARIANCE, UNMATCHED_SKU, EXPEDITED_FREIGHT
    severity: str = "MEDIUM"  # LOW, MEDIUM, HIGH, CRITICAL
    title: str
    description: str
    reference_id: str  # Order ID, PO Number, or Invoice Number
    vendor_or_customer: str
    variance_amount: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "OPEN"  # OPEN, RESOLVED, DISMISSED
    resolution_notes: Optional[str] = None
    available_actions: List[Dict[str, str]] = Field(default_factory=list)


class ResolveExceptionRequest(BaseModel):
    """Payload to resolve an exception."""
    action_type: str
    resolution_notes: Optional[str] = "Resolved via Operations Console"
    # Required by OVERRIDE_PRICE_CHECK. The override previously registered the
    # exception's variance amount (often 0.00) as the new unit cost.
    new_unit_price: Optional[Decimal] = Field(None, gt=0)


# Always accepted, whatever the exception lists: the operator attests the work
# was done outside the system, and the resolution says exactly that.
MANUAL_RESOLUTION_ACTION = "MANUALLY_RESOLVED"


# Triage ordering: most severe first within each status group.
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


class ExceptionQueueService:
    """Database-backed triage queue for back-office discrepancies."""

    def __init__(self, monday_client: Optional[MondayClient] = None):
        self.monday = monday_client or get_monday_client()
        self._queue: Dict[str, ExceptionItem] = {}
        # Handlers run concurrently on a threadpool, so the status check and
        # the status write in resolve_exception must not interleave: two
        # operators resolving the same exception could otherwise both pass
        # the check before either wrote.
        self._lock = threading.RLock()
        if settings.SEED_DEMO_DATA:
            self._seed_default_exceptions()

    def _seed_default_exceptions(self) -> None:
        """Seeds representative realistic exceptions."""
        seed_items = [
            ExceptionItem(
                exception_id="EXC-NIB-01",
                exception_type="STALE_VENDOR_PRICE",
                severity="HIGH",
                title="Stale Quote Detected: Nibrock Mining & Wear Solutions",
                description="Pricing for PLATE-300XL-38 ($110.00/unit) on PO-NIBROCK-9921 is 74 days old (>60d limit).",
                reference_id="PO-NIBROCK-9921",
                vendor_or_customer="NIBROCK",
                variance_amount=0.0,
                available_actions=[
                    {"action": "DISPATCH_URGENT_RFQ", "label": "Send Urgent RFQ Email to Nibrock"},
                    {"action": "OVERRIDE_PRICE_CHECK", "label": "Approve Current Price ($110.00)"},
                ],
            ),
            ExceptionItem(
                exception_id="EXC-INV-02",
                exception_type="INVOICE_COST_VARIANCE",
                severity="HIGH",
                title="Vendor Billed Price Increase: Invoice #INV-NIBROCK-8840",
                description="Nibrock billed $128.50/unit (+16.8% variance) vs authorized PO cost of $110.00. Extra freight: $70.00.",
                reference_id="INV-NIBROCK-8840",
                vendor_or_customer="NIBROCK",
                variance_amount=144.00,
                available_actions=[
                    {"action": "DISPATCH_DISPUTE_NOTICE", "label": "Draft & Send Dispute Notice to Vendor AR"},
                    {"action": "ACCEPT_PRICE_SURCHARGE", "label": "Accept Surcharge & Release Sage AP"},
                ],
            ),
            ExceptionItem(
                exception_id="EXC-SKU-03",
                exception_type="UNMATCHED_SKU",
                severity="MEDIUM",
                title="Custom Customer Drawing Ref: 'CHUTE-LINER-DWG-881'",
                description="Owens Corning PO requested 'CHUTE-LINER-DWG-881'. Nearest catalog match is Allied 300XL Clad Plate.",
                reference_id="PO-OC-4419",
                vendor_or_customer="Owens Corning",
                variance_amount=None,
                available_actions=[
                    {"action": "MAP_TO_PLATE_300XL", "label": "Map SKU to PLATE-300XL-38"},
                    {"action": "REQUEST_CAD_DRAWING", "label": "Request CAD Takeoff Drawing via Form"},
                ],
            ),
        ]
        for item in seed_items:
            # Route through add_exception so seeded items exist in the database
            # too; otherwise resolving one updates nothing durable.
            self.add_exception(item)

    def list_exceptions(self, status_filter: Optional[str] = None) -> List[ExceptionItem]:
        """Returns exceptions, open first, then most severe, then oldest.

        The database is the source of truth so that resolutions survive a
        restart; the in-memory queue is only a fallback when it is unreachable.
        """
        items: List[ExceptionItem] = []
        try:
            with SessionLocal() as db:
                rows = DatabaseService.get_exceptions(db, status_filter=status_filter)
            items = [self._row_to_item(r) for r in rows]
        except Exception as e:
            logger.warning(f"Could not read exceptions from DB, using in-memory queue: {e}")

        if not items:
            items = list(self._queue.values())
            if status_filter:
                items = [i for i in items if i.status.upper() == status_filter.upper()]

        return sorted(
            items,
            key=lambda x: (
                x.status.upper() != "OPEN",
                _SEVERITY_ORDER.get(x.severity.upper(), 99),
                x.created_at,
            ),
        )

    @staticmethod
    def _row_to_item(row: Dict[str, Any]) -> ExceptionItem:
        """Rebuilds an ExceptionItem from a persisted database row."""
        created = row.get("created_at")
        return ExceptionItem(
            exception_id=row["exception_id"],
            exception_type=row.get("exception_type", ""),
            severity=row.get("severity", "MEDIUM"),
            title=row.get("title", ""),
            description=row.get("description", ""),
            reference_id=row.get("reference_id", ""),
            vendor_or_customer=row.get("vendor_or_customer", ""),
            variance_amount=row.get("variance_amount"),
            created_at=datetime.fromisoformat(created) if created else datetime.now(timezone.utc),
            status=row.get("status", "OPEN"),
            resolution_notes=row.get("resolution_notes"),
            available_actions=row.get("available_actions") or [],
        )

    def add_exception(self, item: ExceptionItem) -> ExceptionItem:
        """Adds a new exception into the queue and persists to DB."""
        with self._lock:
            self._queue[item.exception_id] = item
        logger.info(f"Queued exception [{item.exception_id}]: {item.title}")

        try:
            with SessionLocal() as db:
                DatabaseService.persist_exception(db, item.model_dump())
        except Exception as e:
            logger.warning(f"Could not persist exception to DB: {e}")

        return item

    def resolve_exception(self, exception_id: str, request: ResolveExceptionRequest, actor_email: str = "system") -> Dict[str, Any]:
        """Marks an exception resolved and performs the requested system action."""
        item = self._queue.get(exception_id)
        if not item:
            # It may exist only in the database (e.g. raised by an earlier run).
            try:
                with SessionLocal() as db:
                    # Filtered in SQL on the indexed column, rather than
                    # pulling a thousand rows back to find one.
                    row = DatabaseService.get_exception_by_id(db, exception_id)
                if row:
                    item = self._row_to_item(row)
                    with self._lock:
                        # Another thread may have loaded it first; keep theirs
                        # so both callers operate on the same object.
                        item = self._queue.setdefault(exception_id, item)
            except Exception as e:
                logger.warning(f"Could not look up exception {exception_id} in DB: {e}")
        if not item:
            raise KeyError(f"Exception ID {exception_id} not found")

        action = (request.action_type or "").strip().upper()
        allowed = {
            str(a.get("action", "")).strip().upper() for a in (item.available_actions or [])
        } - {""}
        # An exception that lists its actions accepts only those (plus the
        # explicit manual resolution); a caller could previously "resolve" a
        # price variance with any string it liked.
        if allowed and action not in allowed and action != MANUAL_RESOLUTION_ACTION:
            raise ValueError(
                f"Action '{request.action_type}' is not available for exception {exception_id}. "
                f"Choose one of: {sorted(allowed | {MANUAL_RESOLUTION_ACTION})}"
            )
        if action == "OVERRIDE_PRICE_CHECK" and request.new_unit_price is None:
            raise ValueError("OVERRIDE_PRICE_CHECK requires new_unit_price, the price being approved.")

        # The status check and the status write are one atomic step: two
        # operators clicking resolve on the same exception must not both get
        # past the check and both run the action.
        with self._lock:
            if item.status.upper() != "OPEN":
                raise ValueError(
                    f"Exception {exception_id} is already {item.status} and cannot be resolved again."
                )
            # Claim it before doing the work, so a concurrent caller sees it taken.
            item.status = "RESOLVING"

        try:
            # Carry out the requested action before recording the resolution,
            # so a failed action does not leave the queue claiming the work
            # was done.
            action_outcome, completed = self._execute_action(item, request, actor_email)
        except Exception:
            with self._lock:
                item.status = "OPEN"
            raise

        if not completed:
            # The action did not take effect (a mail that was only drafted, a
            # step with no automation behind it), so the exception stays open
            # and the response says what was recorded instead of "resolved".
            with self._lock:
                item.status = "OPEN"
                item.resolution_notes = f"{request.action_type} (not completed): {action_outcome}"
            audit_log(
                action="EXCEPTION_ACTION_RECORDED",
                resource_type="EXCEPTION",
                resource_id=exception_id,
                actor_email=actor_email,
                detail=f"[{request.action_type}] recorded, exception left open: {action_outcome}",
            )
            return {
                "status": "recorded",
                "exception_id": exception_id,
                "exception_status": "OPEN",
                "action_executed": request.action_type,
                "action_completed": False,
                "action_outcome": action_outcome,
                "detail": item.resolution_notes,
            }

        with self._lock:
            item.status = "RESOLVED"
            item.resolution_notes = f"{request.action_type}: {request.resolution_notes}"

        # Persist to database
        try:
            with SessionLocal() as db:
                updated = DatabaseService.resolve_exception(
                    db,
                    exception_id=exception_id,
                    action_type=request.action_type,
                    resolution_notes=request.resolution_notes or "",
                    actor_email=actor_email,
                )
                if updated is None:
                    # Not yet persisted (e.g. added before this code path existed).
                    DatabaseService.persist_exception(db, item.model_dump())
                    DatabaseService.resolve_exception(
                        db,
                        exception_id=exception_id,
                        action_type=request.action_type,
                        resolution_notes=request.resolution_notes or "",
                        actor_email=actor_email,
                    )
        except Exception as e:
            logger.warning(f"Could not update exception resolution in DB: {e}")

        # Record immutable audit trail
        audit_log(
            action="EXCEPTION_RESOLVED",
            resource_type="EXCEPTION",
            resource_id=exception_id,
            actor_email=actor_email,
            detail=f"Resolved with action [{request.action_type}]: {request.resolution_notes}",
        )

        # Post update to Monday.com
        try:
            self.monday.post_pulse_update(
                0,
                f"✓ Resolved Exception [{exception_id}]: {item.title} ({request.action_type})",
            )
        except Exception:
            pass

        return {
            "status": "success",
            "exception_id": exception_id,
            "exception_status": "RESOLVED",
            "action_executed": request.action_type,
            "action_completed": True,
            "action_outcome": action_outcome,
            "detail": item.resolution_notes,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }

    def _execute_action(
        self, item: ExceptionItem, request: ResolveExceptionRequest, actor_email: str = "system"
    ) -> Tuple[str, bool]:
        """Carries out the system effect behind a 1-click resolution action.

        Returns (outcome, completed). ``completed`` is False when nothing
        actually happened -- the caller then leaves the exception open rather
        than reporting a dispute as sent or a step as done.
        """
        action = (request.action_type or "").strip().upper()

        if action == MANUAL_RESOLUTION_ACTION:
            return "Resolved manually; the operator attests the work was done outside the system.", True

        if action == "OVERRIDE_PRICE_CHECK":
            from app.services.vendor_reprice import vendor_reprice_service

            sku = self._extract_sku(item)
            if not sku:
                return "No SKU identified on the exception; price override not applied.", False
            new_price = Decimal(str(request.new_unit_price))
            vendor_reprice_service.register_price(
                item_id=sku,
                vendor=item.vendor_or_customer,
                unit_cost=float(new_price),
            )
            return (
                f"Registered {item.vendor_or_customer} price ${new_price:,.2f} for {sku} "
                f"(re-dated today); orders may proceed.",
                True,
            )

        if action == "DISPATCH_URGENT_RFQ":
            from app.services.outbound_email_service import outbound_email_service
            from app.services.vendor_reprice import vendor_reprice_service

            sku = self._extract_sku(item)
            vendor_reprice_service.dispatch_reprice_request(
                vendor=item.vendor_or_customer,
                items=[sku] if sku else [],
                po_number=item.reference_id,
            )
            entry = next(
                (m for m in outbound_email_service.get_outbox(limit=20)
                 if m.get("category") == "REPRICE_RFQ" and m.get("reference") == item.reference_id),
                None,
            )
            return self._mail_outcome(entry, f"Urgent RFQ to {item.vendor_or_customer}")

        if action == "DISPATCH_DISPUTE_NOTICE":
            from app.services.reconciliation_service import reconciliation_service

            entry = reconciliation_service.send_dispute_for_invoice(
                invoice_number=item.reference_id, vendor_name=item.vendor_or_customer
            )
            return self._mail_outcome(entry, f"Dispute notice to {item.vendor_or_customer}")

        if action in ("RETURN_TO_SUPPLIER", "APPROVE_CONCESSION") and item.exception_type == "COMPLIANCE_MTR_FAILURE":
            from app.models.compliance_mtr import DispositionNCRRequest
            from app.services.mtr_service import mtr_service

            ncr = mtr_service.get_ncr(item.reference_id)
            if ncr is None:
                return f"NCR {item.reference_id} not found; disposition not applied.", False
            mtr_service.disposition_ncr(
                item.reference_id,
                DispositionNCRRequest(action=action, disposition_notes=request.resolution_notes or ""),
                actor_email=actor_email,
            )
            return f"NCR {item.reference_id} dispositioned: {action}.", True

        if action == "ACCEPT_PRICE_SURCHARGE":
            # A decision, and recording it is the action. Releasing the payment
            # itself happens in Sage; nothing here claims it was released.
            return "Surcharge accepted and recorded. Release the payment in Sage AP.", True

        return (
            f"No automated effect is defined for '{request.action_type}'; nothing was done. "
            f"Carry it out, then resolve with {MANUAL_RESOLUTION_ACTION}."
            if item.available_actions
            else f"Recorded as a manual resolution ({request.action_type}).",
            # An exception with no listed actions can only ever be closed by hand.
            not item.available_actions,
        )

    @staticmethod
    def _mail_outcome(entry: Optional[Dict[str, Any]], what: str) -> Tuple[str, bool]:
        """Turns an outbox entry into an honest outcome: only SENT completes."""
        if not entry:
            return f"{what}: no message was produced (no address or draft on file).", False
        status = entry.get("status")
        if status == "SENT":
            return f"{what} sent ({entry.get('message_id')}).", True
        return f"{what} {str(status).lower()}, not sent: {entry.get('detail')}", False

    @staticmethod
    def _extract_sku(item: ExceptionItem) -> Optional[str]:
        """Pulls the SKU an exception refers to out of its description."""
        match = re.search(r"\b([A-Z]{3,}[A-Z0-9]*(?:-[A-Z0-9]+){1,3})\b", item.description or "")
        return match.group(1) if match else None


# Global instance
exception_queue_service = ExceptionQueueService()

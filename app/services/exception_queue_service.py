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
    exception_type: str  # INVOICE_COST_VARIANCE, UNMATCHED_SKU, EXPEDITED_FREIGHT, ORDER_EXCEPTION, ...
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
    # No longer used: vendor repricing was dropped. Kept so older screens that still
    # send it are not rejected.
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
        """Adds an exception to the queue and persists it to the DB.

        A problem that is already open (same type, reference and party) is
        refreshed in place rather than raised again: rechecking the same bill
        used to add a fresh copy of "Invoice already approved once" each time.
        """
        with self._lock:
            existing = self._find_open(item)
            if existing is not None and existing.exception_id != item.exception_id:
                logger.info(f"Exception [{existing.exception_id}] already open; refreshed: {item.title}")
                item = item.model_copy(
                    update={"exception_id": existing.exception_id, "created_at": existing.created_at}
                )
            else:
                logger.info(f"Queued exception [{item.exception_id}]: {item.title}")
            self._queue[item.exception_id] = item

            try:
                with SessionLocal() as db:
                    DatabaseService.persist_exception(db, item.model_dump())
            except Exception as e:
                logger.warning(f"Could not persist exception to DB: {e}")

        return item

    def _find_open(self, item: ExceptionItem) -> Optional[ExceptionItem]:
        """The open exception that `item` repeats, from the DB or else memory."""
        try:
            with SessionLocal() as db:
                row = DatabaseService.find_open_exception(
                    db, item.exception_type, item.reference_id, item.vendor_or_customer
                )
            if row:
                return self._row_to_item(row)
        except Exception as e:
            logger.warning(f"Could not check for an open duplicate exception: {e}")
        return next(
            (
                q for q in self._queue.values()
                if q.status.upper() == "OPEN"
                and q.exception_type == item.exception_type
                and q.reference_id == item.reference_id
                and q.vendor_or_customer == item.vendor_or_customer
            ),
            None,
        )

    def close_open(self, exception_type: str, reference_id: str, note: str, actor_email: str = "system") -> List[str]:
        """Resolves the open exceptions of one type for one reference.

        For work finished somewhere other than the queue, such as a monday
        event replayed from the event log, so the queue doesn't keep asking.
        """
        closed: List[str] = []
        try:
            with SessionLocal() as db:
                rows = [
                    r for r in DatabaseService.get_exceptions(db, status_filter="OPEN", limit=1000)
                    if r["exception_type"] == exception_type and r["reference_id"] == reference_id
                ]
                for r in rows:
                    DatabaseService.resolve_exception(
                        db, exception_id=r["exception_id"], action_type=MANUAL_RESOLUTION_ACTION,
                        resolution_notes=note, actor_email=actor_email,
                    )
                    closed.append(r["exception_id"])
        except Exception as e:
            logger.warning(f"Could not close exceptions for {reference_id}: {e}")
        with self._lock:
            for q in self._queue.values():
                if q.status.upper() == "OPEN" and q.exception_type == exception_type and q.reference_id == reference_id:
                    q.status = "RESOLVED"
                    q.resolution_notes = f"{MANUAL_RESOLUTION_ACTION}: {note}"
                    if q.exception_id not in closed:
                        closed.append(q.exception_id)
        for exception_id in closed:
            audit_log(
                action="EXCEPTION_RESOLVED",
                resource_type="EXCEPTION",
                resource_id=exception_id,
                actor_email=actor_email,
                detail=note,
            )
        return closed

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

        if action == "REPLAY_MONDAY_EVENT":
            from app.services import monday_events

            match = re.fullmatch(r"MONDAY-EVT-(\d+)", item.reference_id or "")
            if not match:
                return f"No monday event on {item.reference_id}; nothing replayed.", False
            event_id = int(match.group(1))
            if not monday_events.replay(event_id):
                return f"monday event #{event_id} is not waiting for a replay; nothing replayed.", False
            audit_log(
                action="REPLAY",
                resource_type="MONDAY_EVENT",
                resource_id=str(event_id),
                actor_email=actor_email,
                detail=f"Replayed from exception {item.exception_id}",
            )
            return f"monday event #{event_id} is queued to run again.", True

        if action in ("APPROVE_AND_STAGE", "REQUEST_CUSTOMER_CLARIFICATION"):
            return self._held_order_action(item, action, actor_email)

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

    def _held_order_action(self, item: ExceptionItem, action: str, actor_email: str) -> Tuple[str, bool]:
        """An order email the AI was unsure of: raise it as read once a person approves,
        or ask the customer to confirm. The AI's read is the email_triage record."""
        from app.models.db_models import AiSuggestionDB

        match = re.search(r"Email ref: (\S+?)\.?$", item.description or "")
        ref = match.group(1) if match else item.reference_id
        with SessionLocal() as db:
            row = (db.query(AiSuggestionDB).filter(AiSuggestionDB.feature == "email_triage",
                                                   AiSuggestionDB.reference == ref)
                   .order_by(AiSuggestionDB.id.desc()).first())
            read = dict(row.output or {}) if row else None
            source = dict(row.input or {}) if row else {}
            suggestion_id = row.id if row else None
        if read is None:
            return "The AI's reading of this email is no longer on file; enter the order by hand.", False
        sender = source.get("sender") or ""

        if action == "REQUEST_CUSTOMER_CLARIFICATION":
            from app.services.outbound_email_service import outbound_email_service

            if "@" not in sender:
                return "No sender address on file to ask.", False
            entry = outbound_email_service.send(
                to=sender, subject=f"Re: {source.get('subject') or 'your order'}",
                body=("Thank you for your order. Before we enter it, could you confirm the PO number, "
                      "the quantities and the prices? We want to be sure we ship exactly what you need."
                      "\n\nAllied Welding Systems"),
                category="CUSTOMER_CLARIFICATION", reference=item.exception_id,
            )
            return self._mail_outcome(entry, f"Clarification request to {sender}")

        if not read.get("po_number") or not read.get("lines"):
            return "The AI found no PO number or no lines; ask the customer, or enter the order by hand.", False
        from app.models.orders import InboundOrderRequest, OrderLineItem
        from app.services.entity_matcher import EntityMatcher
        from app.services.order_orchestrator import OrderOrchestrator

        customer, _ = EntityMatcher().match_customer(read.get("customer_name") or sender)
        request = InboundOrderRequest(
            customer_name=customer["customer_name"], customer_po_number=read["po_number"],
            shipping_address=customer.get("shipping_address", "Customer Master Destination"),
            line_items=[OrderLineItem(raw_item_text=line.get("description") or "",
                                      quantity=Decimal(str(line.get("quantity") or 1)),
                                      unit_price=Decimal(str(line.get("unit_price") or 0)))
                        for line in read["lines"]],
            notes=f"Approved by {actor_email} from a held order email ({ref}).",
            payment_terms=customer.get("payment_terms", "Net 30 Days"),
        )
        result = OrderOrchestrator().process_inbound_order(request)
        if suggestion_id:
            from app.services.ai import store

            try:
                store.review(suggestion_id, approve=True, actor={"email": actor_email}, note=f"Raised SO #{result.sales_order_number}")
            except Exception as exc:  # noqa: BLE001 - the order stands; the review mark is bookkeeping
                logger.info(f"Could not mark AI suggestion {suggestion_id} approved: {exc}")
        return f"Raised SO #{result.sales_order_number} for {customer['customer_name']} (PO {read['po_number']}).", True

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

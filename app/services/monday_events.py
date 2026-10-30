r"""Durable inbox and router for monday.com webhook events.

The webhook endpoint used to hand the raw event to a FastAPI BackgroundTask
and forget it. That lost the event on any restart, processed monday's
redeliveries twice, and routed by searching the changed label for substrings,
so *every* column edit that was not a ship/approve label fell through to
"create a new order" (with a made-up customer when the item name did not
parse).

The flow is now the standard inbox pattern:

    receive -> verify -> INSERT (dedup on triggerUuid) -> 200 OK
                                  |
                 worker: claim -> route -> handler -> DONE / IGNORED
                                                  \-> RETRY (backoff) -> DEAD
                                                  \-> FAILED (needs a person)

Handlers are idempotent per board item, so toggling a status back and forth
cannot ship an order twice.
"""

import hashlib
import json
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.config import settings
from app.core.correlation import get_correlation_id
from app.core.database import SessionLocal
from app.core.exceptions import (
    ComplianceViolationError,
    DualSidedTransactionError,
    EntityNotFoundError,
    OrderValidationError,
)
from app.core.logging import logger
from app.models.db_models import MondayEventDB, MondayItemStateDB, OrderDB

# Payload `type` values monday uses for the subscriptions we care about.
_CREATE_TYPES = {"create_pulse", "create_item", "create_subitem"}
_COLUMN_TYPES = {
    "update_column_value",
    "change_column_value",
    "change_status_column_value",
    "change_specific_column_value",
}

# Actions a route can resolve to.
CREATE_ORDER = "CREATE_ORDER"
RELEASE_PO = "RELEASE_PO"
DISPATCH = "DISPATCH"

TERMINAL = {"DONE", "IGNORED", "FAILED", "DEAD"}
# Longer than any realistic run (each monday call retries for up to ~2.5 min).
_LEASE = timedelta(minutes=30)
# Per-item serialisation inside this process; _item_busy covers other processes.
_item_locks: Dict[str, threading.Lock] = {}
_item_locks_guard = threading.Lock()


def _item_lock(item_id: Optional[str]) -> threading.Lock:
    with _item_locks_guard:
        return _item_locks.setdefault(item_id or "", threading.Lock())


class PermanentEventError(Exception):
    """The event can never succeed as-is; a person has to fix the board item."""


_PERMANENT_ERRORS: Tuple[type, ...] = (
    PermanentEventError,
    DualSidedTransactionError,
    EntityNotFoundError,
    OrderValidationError,
    ComplianceViolationError,
    ValidationError,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes; everything here is stored in UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def status_label(value: Any) -> str:
    """Extracts the label text from a status column value.

    monday sends {"label": {"index": 1, "text": "Done", ...}, "post_id": null},
    {"label": null} when the status is cleared, and plain strings from our
    own simulator.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        label = value.get("label")
        if isinstance(label, dict):
            return str(label.get("text") or "").strip()
        if isinstance(label, str):
            return label.strip()
        if isinstance(value.get("text"), str):
            return value["text"].strip()
    return ""


def dedup_key(event: Dict[str, Any], raw_body: bytes = b"") -> str:
    """monday puts a triggerUuid on every delivery and keeps it on retries."""
    trigger = event.get("triggerUuid") or event.get("originalTriggerUuid")
    if trigger:
        return f"t:{trigger}"
    body = raw_body or json.dumps(event, sort_keys=True, default=str).encode()
    return f"h:{hashlib.sha256(body).hexdigest()}"


def _allowed_boards() -> set:
    return {b.strip() for b in str(settings.MONDAY_BOARD_ID or "").split(",") if b.strip()}


def decide(event: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Maps an event to an action, or (None, reason) when it is not ours to act on."""
    boards = _allowed_boards()
    board_id = str(event.get("boardId") or "")
    if boards and board_id not in boards:
        return None, f"board {board_id or '?'} is not a configured order board"

    if settings.MONDAY_BOT_USER_ID and str(event.get("userId") or "") == str(settings.MONDAY_BOT_USER_ID):
        return None, "change was made by this integration (write-back)"

    event_type = str(event.get("type") or "")
    if event_type in _CREATE_TYPES:
        if settings.MONDAY_CREATE_ORDER_ON_NEW_ITEM:
            return CREATE_ORDER, "new board item"
        return None, "new items do not create orders (MONDAY_CREATE_ORDER_ON_NEW_ITEM is off)"

    if event_type in _COLUMN_TYPES:
        if str(event.get("columnId") or "") != settings.MONDAY_STATUS_COLUMN_ID:
            return None, f"column '{event.get('columnId')}' is not the trigger column"
        label = status_label(event.get("value")).casefold()
        if not label:
            return None, "status was cleared"
        if label in settings.label_set(settings.MONDAY_LABELS_DISPATCH):
            return DISPATCH, f"status -> {label}"
        if label in settings.label_set(settings.MONDAY_LABELS_RELEASE_PO):
            return RELEASE_PO, f"status -> {label}"
        if label in settings.label_set(settings.MONDAY_LABELS_CREATE_ORDER):
            return CREATE_ORDER, f"status -> {label}"
        return None, f"status '{label}' is not mapped to an action"

    return None, f"event type '{event_type or 'unknown'}' is not handled"


# ---------------------------------------------------------------------------
# Inbox persistence
# ---------------------------------------------------------------------------


def _new_event_row(event: Dict[str, Any], key: str) -> MondayEventDB:
    """Builds the inbox row for an event; the caller adds and commits it."""
    return MondayEventDB(
        dedup_key=key,
        event_type=str(event.get("type") or "")[:64] or None,
        board_id=str(event.get("boardId") or "") or None,
        item_id=str(event.get("pulseId") or event.get("itemId") or "") or None,
        column_id=str(event.get("columnId") or "")[:64] or None,
        user_id=str(event.get("userId") or "") or None,
        payload=json.loads(json.dumps(event, default=str)),
        status="PENDING",
        attempts=0,
        correlation_id=get_correlation_id() or None,
    )


def record_event(event: Dict[str, Any], raw_body: bytes = b"", key: Optional[str] = None) -> Tuple[int, bool]:
    """Stores the event. Returns (row id, is_duplicate)."""
    key = key or dedup_key(event, raw_body)
    with SessionLocal() as db:
        existing = db.query(MondayEventDB.id).filter(MondayEventDB.dedup_key == key).first()
        if existing:
            return existing[0], True
        row = _new_event_row(event, key)
        db.add(row)
        try:
            db.commit()
        except Exception:
            # Two concurrent deliveries of the same trigger: the unique index
            # decides the winner and the loser is a duplicate.
            db.rollback()
            existing = db.query(MondayEventDB.id).filter(MondayEventDB.dedup_key == key).first()
            if existing:
                return existing[0], True
            raise
        new_id = row.id
    _remember_label(event)
    return new_id, False


def _remember_label(event: Dict[str, Any]) -> None:
    """Keeps the board reader's last-seen status in step with webhook deliveries."""
    if str(event.get("columnId") or "") != settings.MONDAY_STATUS_COLUMN_ID or not event.get("pulseId"):
        return
    now = _utcnow()
    try:
        with SessionLocal() as db:
            state = db.get(MondayItemStateDB, str(event["pulseId"]))
            label = status_label(event.get("value"))
            if state is None:
                db.add(MondayItemStateDB(
                    item_id=str(event["pulseId"]), board_id=str(event.get("boardId") or ""), label=label,
                    item_name=event.get("pulseName"), seen_at=now, changed_at=now,
                ))
            elif (state.label or "") != label:
                state.label, state.changed_at, state.seen_at = label, now, now
            db.commit()
    except Exception as exc:  # noqa: BLE001 - the event itself is already safe in the inbox
        logger.warning(f"Could not update board-reader state for item {event.get('pulseId')}: {exc}")


def _claim(event_id: int) -> Optional[MondayEventDB]:
    """Atomically moves a runnable event to PROCESSING so only one worker runs it."""
    now = _utcnow()
    with SessionLocal() as db:
        claimed = (
            db.query(MondayEventDB)
            .filter(MondayEventDB.id == event_id, MondayEventDB.status.in_(("PENDING", "RETRY")))
            .update(
                {
                    MondayEventDB.status: "PROCESSING",
                    MondayEventDB.attempts: MondayEventDB.attempts + 1,
                    MondayEventDB.next_attempt_at: now + _LEASE,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not claimed:
            return None
        row = db.get(MondayEventDB, event_id)
        db.expunge(row)
        return row


def _finish(event_id: int, claimed_attempt: Optional[int] = None, **fields: Any) -> bool:
    """Records the outcome, but only if this run still owns the event.

    Without the ownership check, a run whose lease expired could overwrite the
    outcome of the run that took over from it.
    """
    with SessionLocal() as db:
        q = db.query(MondayEventDB).filter(MondayEventDB.id == event_id)
        if claimed_attempt is not None:
            q = q.filter(MondayEventDB.status == "PROCESSING", MondayEventDB.attempts == claimed_attempt)
        n = q.update({getattr(MondayEventDB, k): v for k, v in fields.items()}, synchronize_session=False)
        db.commit()
    if not n:
        logger.warning(f"monday event #{event_id}: outcome dropped, another run owns it now.")
    return bool(n)


def _item_busy(event_id: int, item_id: Optional[str]) -> bool:
    """Another event for the same board item is mid-flight (in any process)."""
    if not item_id:
        return False
    with SessionLocal() as db:
        return (
            db.query(MondayEventDB.id)
            .filter(
                MondayEventDB.item_id == item_id,
                MondayEventDB.id != event_id,
                MondayEventDB.status == "PROCESSING",
            )
            .first()
            is not None
        )


def _backoff(attempts: int) -> timedelta:
    base = max(1, settings.WEBHOOK_RETRY_BASE_SECONDS)
    return timedelta(seconds=min(base * (2 ** max(0, attempts - 1)), 3600))


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def process_event(event_id: int) -> Optional[str]:
    """Runs one event to its next state. Returns that state, or None if not claimable."""
    row = _claim(event_id)
    if row is None:
        return None
    event = row.payload or {}
    action, reason = decide(event)
    mine = row.attempts

    if action is None:
        logger.info(f"monday event #{event_id} ignored: {reason}")
        _finish(event_id, mine, status="IGNORED", result={"reason": reason}, processed_at=_utcnow(), next_attempt_at=None)
        return "IGNORED"

    # Two triggers on one item ("Shipped" then "Dispatched", or a quick
    # toggle) must run one after the other; run together, both pass the
    # already-done check and the order ships twice.
    lock = _item_lock(row.item_id)
    busy = not lock.acquire(blocking=False)
    if not busy and _item_busy(event_id, row.item_id):
        lock.release()
        busy = True
    if busy:
        # Not a failure, so the attempt is handed back. Jitter keeps two
        # processes from backing off in lockstep.
        _finish(
            event_id, mine, status="RETRY", attempts=mine - 1, action=action,
            next_attempt_at=_utcnow() + timedelta(seconds=random.randint(10, 30)),
        )
        return "RETRY"
    try:
        return _run(event_id, event, action, row, mine)
    finally:
        lock.release()


def _run(event_id: int, event: Dict[str, Any], action: str, row: MondayEventDB, mine: int) -> str:
    handler = _HANDLERS[action]
    try:
        result = handler(event)
    except _PERMANENT_ERRORS as exc:
        message = getattr(exc, "message", None) or str(exc)
        logger.warning(f"monday event #{event_id} ({action}) needs a person: {message}")
        if _finish(
            event_id, mine, status="FAILED", action=action, last_error=message[:2000],
            processed_at=_utcnow(), next_attempt_at=None,
        ):
            _escalate(event_id, event, action, message)
        return "FAILED"
    except Exception as exc:  # noqa: BLE001 - anything else is assumed transient
        message = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"
        if row.attempts >= settings.WEBHOOK_MAX_ATTEMPTS:
            logger.error(f"monday event #{event_id} ({action}) gave up after {row.attempts} attempts: {message}")
            if _finish(
                event_id, mine, status="DEAD", action=action, last_error=message[:2000],
                processed_at=_utcnow(), next_attempt_at=None,
            ):
                _escalate(event_id, event, action, f"Gave up after {row.attempts} attempts. {message}")
            return "DEAD"
        delay = _backoff(row.attempts)
        logger.warning(f"monday event #{event_id} ({action}) failed, retry in {delay}: {message}")
        _finish(
            event_id, mine, status="RETRY", action=action, last_error=message[:2000],
            next_attempt_at=_utcnow() + delay,
        )
        return "RETRY"

    _finish(
        event_id, mine, status="DONE", action=action, result=json.loads(json.dumps(result, default=str)),
        last_error=None, processed_at=_utcnow(), next_attempt_at=None,
    )
    return "DONE"


def sweep(limit: int = 25) -> int:
    """Runs events that are due: new ones nobody picked up, retries, expired leases."""
    now = _utcnow()
    with SessionLocal() as db:
        # An expired lease means the process died mid-event; hand it back,
        # unless it has used up its attempts. An event that kills the worker
        # every time would otherwise cycle forever without being escalated.
        expired = (
            db.query(MondayEventDB)
            .filter(MondayEventDB.status == "PROCESSING", MondayEventDB.next_attempt_at < now)
            .all()
        )
        exhausted = []
        for r in expired:
            if r.attempts >= settings.WEBHOOK_MAX_ATTEMPTS:
                r.status, r.processed_at, r.next_attempt_at = "DEAD", now, None
                r.last_error = "Processing was interrupted on every attempt."
                exhausted.append((r.id, dict(r.payload or {}), r.action or "UNKNOWN"))
            else:
                r.status = "RETRY"
        db.commit()
        if expired:
            logger.warning(f"Recovered {len(expired)} monday event(s) abandoned mid-processing.")
        due = [
            r[0]
            for r in db.query(MondayEventDB.id)
            .filter(
                (MondayEventDB.status == "PENDING")
                | ((MondayEventDB.status == "RETRY") & (MondayEventDB.next_attempt_at <= now))
            )
            .order_by(MondayEventDB.id)
            .limit(limit)
            .all()
        ]
    for event_id, payload, action in exhausted:
        _escalate(event_id, payload, action, "Processing was interrupted on every attempt.")
    for event_id in due:
        try:
            process_event(event_id)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the sweep
            logger.error(f"Sweep could not process monday event #{event_id}: {exc}")
    return len(due)


def replay(event_id: int) -> bool:
    """Puts a finished event back in the queue (operator action after fixing the item)."""
    with SessionLocal() as db:
        n = (
            db.query(MondayEventDB)
            # DONE is deliberately not replayable: it would ship or email again.
            .filter(MondayEventDB.id == event_id, MondayEventDB.status.in_(("FAILED", "DEAD", "IGNORED")))
            .update(
                {
                    MondayEventDB.status: "PENDING",
                    MondayEventDB.attempts: 0,
                    MondayEventDB.next_attempt_at: None,
                    MondayEventDB.last_error: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
    return bool(n)


def list_events(limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(MondayEventDB).order_by(MondayEventDB.id.desc())
        if status:
            q = q.filter(MondayEventDB.status == status.upper())
        return [
            {
                "id": r.id,
                "status": r.status,
                "action": r.action,
                "event_type": r.event_type,
                "board_id": r.board_id,
                "item_id": r.item_id,
                "column_id": r.column_id,
                "label": status_label((r.payload or {}).get("value")),
                "item_name": (r.payload or {}).get("pulseName"),
                "attempts": r.attempts,
                "last_error": r.last_error,
                "result": r.result,
                "received_at": _iso(r.received_at),
                "processed_at": _iso(r.processed_at),
                "next_attempt_at": _iso(r.next_attempt_at),
            }
            for r in q.limit(limit).all()
        ]


def stats() -> Dict[str, int]:
    from sqlalchemy import func

    with SessionLocal() as db:
        rows = db.query(MondayEventDB.status, func.count()).group_by(MondayEventDB.status).all()
    counts = {s: 0 for s in ("PENDING", "PROCESSING", "RETRY", "DONE", "IGNORED", "FAILED", "DEAD")}
    counts.update({s: n for s, n in rows})
    counts["needs_attention"] = counts["FAILED"] + counts["DEAD"]
    return counts


def _iso(value: Optional[datetime]) -> Optional[str]:
    value = _as_utc(value)
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _orchestrator():
    from app.core.container import get_orchestrator

    return get_orchestrator()


def _monday():
    from app.core.container import get_monday_client

    return get_monday_client()


def _item_int(event: Dict[str, Any]) -> Optional[int]:
    raw = event.get("pulseId") or event.get("itemId")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _split_name(name: str) -> List[str]:
    """'Customer - PO-123 - Item' -> parts. Hyphens inside a PO number survive."""
    return [p.strip() for p in re.split(r"\s+[-–|]\s+", name or "") if p.strip()]


def _column_text(item: Optional[Dict[str, Any]], column_id: str) -> str:
    if not (item and column_id):
        return ""
    return str(((item.get("columns") or {}).get(column_id) or {}).get("text") or "").strip()


def _already_done(item_id: Optional[int], action: str) -> Optional[Dict[str, Any]]:
    """The result of an earlier successful run of this action on this item."""
    if not item_id:
        return None
    with SessionLocal() as db:
        row = (
            db.query(MondayEventDB)
            .filter(
                MondayEventDB.item_id == str(item_id),
                MondayEventDB.action == action,
                MondayEventDB.status == "DONE",
            )
            .order_by(MondayEventDB.id.desc())
            .first()
        )
        return dict(row.result or {}, event_id=row.id) if row else None


def find_order_for_item(item_id: Optional[int], item_name: str = "") -> Optional[Dict[str, Any]]:
    """The order a board item produced: by stored item id, then by PO in the name."""
    with SessionLocal() as db:
        row = None
        if item_id:
            row = db.query(OrderDB).filter(OrderDB.monday_item_id == str(item_id)).first()
        if row is None:
            for part in _split_name(item_name):
                row = db.query(OrderDB).filter(OrderDB.customer_po_number == part).first()
                if row:
                    break
        if row is None:
            return None
        return {
            "order_id": row.order_id,
            "sales_order_number": row.sales_order_number,
            "customer_po_number": row.customer_po_number,
            "customer_name": row.customer_name,
            "purchase_orders": row.purchase_orders or {},
            "facility": row.facility,
            "currency": row.currency,
            "monday_item_id": row.monday_item_id,
        }


def _link_item(order_id: str, board_id: Any, item_id: Optional[int]) -> None:
    if not item_id:
        return
    with SessionLocal() as db:
        db.query(OrderDB).filter(OrderDB.order_id == order_id, OrderDB.monday_item_id.is_(None)).update(
            {OrderDB.monday_item_id: str(item_id), OrderDB.monday_board_id: str(board_id or "") or None},
            synchronize_session=False,
        )
        db.commit()


def _idem(event: Dict[str, Any], suffix: str) -> Optional[str]:
    """Idempotency key for a board write caused by this event."""
    trigger = event.get("triggerUuid") or event.get("originalTriggerUuid")
    return f"{trigger}-{suffix}" if trigger else None


def _write_back(event: Dict[str, Any], sync_label: str, so_number: Optional[str] = None) -> None:
    board_id, item_id = event.get("boardId"), _item_int(event)
    values: Dict[str, Any] = {}
    if so_number and settings.MONDAY_SO_COLUMN_ID:
        values[settings.MONDAY_SO_COLUMN_ID] = str(so_number)
    sync_col = settings.MONDAY_SYNC_STATUS_COLUMN_ID
    if sync_col and sync_col == settings.MONDAY_STATUS_COLUMN_ID:
        # Writing the trigger column would fire the trigger again.
        logger.warning("MONDAY_SYNC_STATUS_COLUMN_ID equals the trigger column; status write-back skipped.")
    elif sync_col:
        values[sync_col] = {"label": sync_label}
    if values:
        _monday().update_column_values(board_id, item_id, values, _idem(event, "columns"))


def handle_create_order(event: Dict[str, Any]) -> Dict[str, Any]:
    from decimal import Decimal, InvalidOperation

    from app.models.orders import InboundOrderRequest, OrderLineItem

    item_id = _item_int(event)
    previous = _already_done(item_id, CREATE_ORDER)
    if previous:
        # The webhook and the board reader can both report one status change.
        return {"skipped": "an order was already created for this item", "previous": previous}
    board_id = event.get("boardId")
    item = _monday().get_item(item_id) if item_id else None
    name = event.get("pulseName") or (item or {}).get("name") or ""
    parts = _split_name(name)

    customer = _column_text(item, settings.MONDAY_CUSTOMER_COLUMN_ID) or (parts[0] if len(parts) > 0 else "")
    po_number = _column_text(item, settings.MONDAY_PO_COLUMN_ID) or (parts[1] if len(parts) > 1 else "")
    item_text = _column_text(item, settings.MONDAY_ITEM_COLUMN_ID) or (parts[2] if len(parts) > 2 else "")
    address = _column_text(item, settings.MONDAY_ADDRESS_COLUMN_ID) or "Destination per Customer Master Record"
    qty_text = _column_text(item, settings.MONDAY_QTY_COLUMN_ID) or "1"

    # The old code defaulted the customer to "Owens Corning" and the PO to
    # "PO-<item id>", which raised real Sage documents for the wrong account.
    missing = [label for label, v in (("customer", customer), ("PO number", po_number), ("item", item_text)) if not v]
    if missing:
        raise PermanentEventError(
            f"Board item '{name}' is missing {', '.join(missing)}. "
            f"Name it 'Customer - PO number - Item' or fill the mapped columns."
        )
    try:
        quantity = Decimal(re.sub(r"[^0-9.]", "", qty_text) or "1")
    except InvalidOperation:
        raise PermanentEventError(f"Quantity '{qty_text}' on '{name}' is not a number.")

    request = InboundOrderRequest(
        customer_name=customer,
        customer_po_number=po_number,
        shipping_address=address,
        line_items=[OrderLineItem(raw_item_text=item_text, quantity=quantity, unit_price=Decimal("0.00"))],
        notes=f"Ingested from monday.com board {board_id}, item {item_id}",
    )
    result = _orchestrator().process_inbound_order(
        request, pulse_id=item_id, board_id=board_id, actor_email="monday-webhook"
    )
    _link_item(result.order_id, board_id, item_id)

    staged = "staged for Sage import" if settings.SAGE_SIMULATION_MODE else "written to Sage"
    _write_back(event, "Synced", result.sales_order_number)
    _monday().post_pulse_update(
        item_id,
        f"Sales Order #{result.sales_order_number} {staged}.\n"
        f"Vendor POs: {', '.join(f'{v} #{n}' for v, n in result.purchase_orders.items()) or 'none (stock)'}\n"
        f"Margin: {result.gross_margin_pct}%",
        _idem(event, "order-update"),
    )
    return {
        "order_id": result.order_id,
        "sales_order_number": result.sales_order_number,
        "purchase_orders": result.purchase_orders,
        "messages": result.messages,
    }


def handle_release_po(event: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.outbound_email_service import outbound_email_service

    item_id = _item_int(event)
    previous = _already_done(item_id, RELEASE_PO)
    if previous:
        return {"skipped": "purchase orders were already released for this item", "previous": previous}

    order = find_order_for_item(item_id, event.get("pulseName") or "")
    if not order:
        raise PermanentEventError("No Allied sales order is linked to this board item, so there are no POs to release.")

    vendors = _orchestrator().entity_matcher.vendors_cache
    # A retry after a partial failure must not re-email suppliers that
    # already have their PO.
    already = {
        str(m.get("reference")): m.get("status")
        for m in outbound_email_service.get_outbox(limit=500)
        if m.get("category") == "PURCHASE_ORDER" and m.get("status") in ("SENT", "DRAFTED")
    }
    outcomes = {}
    for vendor_code, po_number in (order["purchase_orders"] or {}).items():
        if str(po_number) in already:
            outcomes[vendor_code] = {"po": po_number, "status": f"{already[str(po_number)].lower()} earlier"}
            continue
        vendor = vendors.get(vendor_code, {})
        email = vendor.get("email") or vendor.get("vendor_email")
        if not email:
            outcomes[vendor_code] = {"po": po_number, "status": "no supplier email on file"}
            continue
        sent = outbound_email_service.send_purchase_order(
            vendor_name=vendor.get("vendor_name", vendor_code),
            vendor_email=email,
            po_number=str(po_number),
            body=(
                f"Please find Allied Welding Systems purchase order {po_number} "
                f"(our ref SO #{order['sales_order_number']}, customer PO {order['customer_po_number']}).\n\n"
                f"BLIND SHIPMENT: do not include pricing or your invoice in the crate."
            ),
        )
        outcomes[vendor_code] = {"po": po_number, "status": sent.get("status", "recorded")}

    summary = "; ".join(f"{v} PO #{o['po']}: {o['status']}" for v, o in outcomes.items()) or "stock order, no vendor POs"
    _monday().post_pulse_update(
        item_id, f"Purchase orders for SO #{order['sales_order_number']}: {summary}", _idem(event, "po-update")
    )
    return {"sales_order_number": order["sales_order_number"], "purchase_orders": outcomes}


def handle_dispatch(event: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.logistics_dispatcher import LogisticsDispatchRequest, logistics_dispatcher

    item_id = _item_int(event)
    previous = _already_done(item_id, DISPATCH)
    if previous:
        # A status toggled away and back must not ship the order twice.
        return {"skipped": "this item was already dispatched", "previous": previous}

    order = find_order_for_item(item_id, event.get("pulseName") or "")
    if not order:
        raise PermanentEventError("No Allied sales order is linked to this board item; nothing was dispatched.")

    result = logistics_dispatcher.dispatch_shipment(
        LogisticsDispatchRequest(
            sales_order_number=order["sales_order_number"],
            origin_facility=order.get("facility") or "HQ",
            currency=order.get("currency") or "USD",
        )
    )
    _write_back(event, "Shipped")
    _monday().post_pulse_update(
        item_id,
        f"Dispatched SO #{order['sales_order_number']}.\n"
        f"Packing list: {result.packing_slip_filename}\nTracking: {result.tracking_number}",
        _idem(event, "dispatch-update"),
    )
    return {
        "sales_order_number": order["sales_order_number"],
        "dispatch_id": result.dispatch_id,
        "tracking_number": result.tracking_number,
    }


_HANDLERS = {
    CREATE_ORDER: handle_create_order,
    RELEASE_PO: handle_release_po,
    DISPATCH: handle_dispatch,
}


def _escalate(event_id: int, event: Dict[str, Any], action: str, message: str) -> None:
    """Puts a stuck event in front of a person: exceptions queue, Slack/Teams, the board."""
    item_id = _item_int(event)
    try:
        from app.services.exception_queue_service import ExceptionItem, exception_queue_service

        exception_queue_service.add_exception(
            ExceptionItem(
                exception_type="MONDAY_EVENT_FAILED",
                severity="HIGH",
                title=f"monday automation stopped: {action} on item {item_id or '?'}",
                description=message,
                reference_id=f"MONDAY-EVT-{event_id}",
                vendor_or_customer=str(event.get("pulseName") or f"board {event.get('boardId')}"),
                available_actions=[{"action": "REPLAY_MONDAY_EVENT", "label": "Replay after fixing the item"}],
            )
        )
    except Exception as exc:  # noqa: BLE001 - escalation is best effort
        logger.error(f"Could not queue exception for monday event #{event_id}: {exc}")
    try:
        from app.services.outbound_webhook_service import outbound_webhooks

        outbound_webhooks.send_sync(
            title=f"monday automation needs attention ({action})",
            message=f"Event #{event_id}, item {item_id}: {message}",
            severity="WARNING",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Could not send alert for monday event #{event_id}: {exc}")
    # The board sees a short, safe message; the detail stays in the console,
    # since board viewers include people outside operations.
    _monday().post_pulse_update(
        item_id,
        f"Automation could not complete '{action.replace('_', ' ').lower()}'. "
        f"Operations has been notified (ref MONDAY-EVT-{event_id}).",
    )


# ---------------------------------------------------------------------------
# Board reader: learns about changes by reading the board, not from monday
# ---------------------------------------------------------------------------


# Reserved item id marking that the reader has completed a full read of a
# board. monday item ids are numeric, so this can never collide with one.
_BASELINE_PREFIX = "baseline:"


def _baseline_marker_id(board_id: str) -> str:
    return f"{_BASELINE_PREFIX}{board_id}"[:32]


def _record_read_change(event: Dict[str, Any], key: str, change: Dict[str, Any]) -> int:
    """Stores a change's event and its new item state in one transaction.

    Committing every state first and queueing events afterwards meant a
    failure on one change lost all the ones after it: their new labels were
    already recorded, so no later read saw them as changes.
    """
    item = change["item"]
    item_id = str(item["id"])
    now = _utcnow()
    with SessionLocal() as db:
        row = _new_event_row(event, key)
        db.add(row)
        state = db.get(MondayItemStateDB, item_id)
        if state is None:
            db.add(MondayItemStateDB(
                item_id=item_id, board_id=change["board_id"], label=change["label"],
                item_name=item.get("name"), seen_at=now, changed_at=now,
            ))
        else:
            state.label, state.changed_at, state.seen_at = change["label"], now, now
        db.commit()
        return row.id


def read_boards(client=None) -> Dict[str, Any]:
    """Reads the trigger column of each order board and queues what changed.

    Allied's system runs every automation; monday only holds the data. Reading
    the board costs API calls (1,000 a day on the cheaper plans) but no monday
    automation or integration actions. The first read of a board only records
    where every job stands, so switching the reader on never fires old jobs.
    """
    client = client or _monday()
    boards = sorted(_allowed_boards())
    if not boards:
        return {"skipped": "MONDAY_BOARD_ID is not set"}
    if not client.live:
        return {"skipped": "board writes are in simulation; nothing to read"}

    column = settings.MONDAY_STATUS_COLUMN_ID
    changes: List[Dict[str, Any]] = []
    seen = 0
    for board_id in boards:
        result = client.fetch_board_items(int(board_id), limit=500)
        boards_data = (result.get("data") or {}).get("boards") or []
        items = ((boards_data[0] if boards_data else {}).get("items_page") or {}).get("items") or []
        now = _utcnow()
        with SessionLocal() as db:
            # Webhooks and simulations also create item-state rows, so "some
            # row exists for this board" did not mean the reader had ever read
            # it, and the first live read fired every labelled job. The
            # baseline is now an explicit marker written by the reader itself.
            baselined = db.get(MondayItemStateDB, _baseline_marker_id(board_id)) is not None
            for item in items:
                seen += 1
                item_id = str(item.get("id"))
                label = next((str(cv.get("text") or "") for cv in item.get("column_values") or [] if cv.get("id") == column), "")
                state = db.get(MondayItemStateDB, item_id)
                if not baselined:
                    # First full read: record where every job stands, fire nothing.
                    if state is None:
                        db.add(MondayItemStateDB(
                            item_id=item_id, board_id=board_id, label=label, item_name=item.get("name"),
                            seen_at=now, changed_at=now,
                        ))
                    else:
                        state.label, state.item_name, state.seen_at = label, item.get("name"), now
                    continue
                if state is None:
                    if label:
                        # A job added since the last read, already at a status.
                        # Its state is written together with its event, below.
                        changes.append({"item": item, "label": label, "board_id": board_id})
                    else:
                        db.add(MondayItemStateDB(
                            item_id=item_id, board_id=board_id, label=label, item_name=item.get("name"),
                            seen_at=now, changed_at=now,
                        ))
                    continue
                state.seen_at = now
                state.item_name = item.get("name")
                if (state.label or "") != label:
                    # Left at the old label until the event is stored, so a
                    # failure part way through is detected again next read.
                    changes.append({"item": item, "label": label, "board_id": board_id})
            if not baselined:
                db.add(MondayItemStateDB(
                    item_id=_baseline_marker_id(board_id), board_id=board_id, label=None,
                    item_name="board reader baseline", seen_at=now, changed_at=now,
                ))
            db.commit()

    queued = 0
    for change in changes:
        item = change["item"]
        event = {
            "type": "update_column_value",
            "app": "board_reader",
            "boardId": change["board_id"],
            "pulseId": int(item["id"]),
            "pulseName": item.get("name"),
            "columnId": column,
            "value": {"label": {"text": change["label"]}},
        }
        key = f"read:{item['id']}:{column}:{change['label']}:{_utcnow().isoformat()}"
        try:
            event_id = _record_read_change(event, key, change)
        except Exception as exc:  # noqa: BLE001 - the item keeps its old state and is retried next read
            logger.error(f"Could not queue board change for item {item.get('id')}: {exc}")
            continue
        queued += 1
        try:
            process_event(event_id)
        except Exception as exc:  # noqa: BLE001 - stored as PENDING; the sweep retries it
            logger.error(f"Board change event #{event_id} failed on first run: {exc}")
    return {"boards": len(boards), "items_seen": seen, "changes_queued": queued}


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------


class MondayInboxWorker:
    """Drains due events on a timer. Immediate processing happens per request;
    this catches retries and anything a restart interrupted."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.last_sweep_at: Optional[str] = None
        self.last_read_at: Optional[str] = None
        self.last_read: Optional[Dict[str, Any]] = None
        self._next_read = 0.0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="allied-monday-inbox", daemon=True)
        self._thread.start()
        logger.info(f"monday inbox worker started (every {settings.WEBHOOK_SWEEP_SECONDS}s).")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                sweep()
                self.last_sweep_at = _utcnow().isoformat()
            except Exception as exc:  # noqa: BLE001 - the loop outlives any single failure
                logger.error(f"monday inbox sweep failed: {exc}")
            if settings.MONDAY_POLL_SECONDS > 0 and time.monotonic() >= self._next_read:
                self._next_read = time.monotonic() + max(60, settings.MONDAY_POLL_SECONDS)
                try:
                    self.last_read = read_boards()
                    self.last_read_at = _utcnow().isoformat()
                except Exception as exc:  # noqa: BLE001
                    self.last_read = {"error": f"{type(exc).__name__}: {exc}"}
                    logger.error(f"monday board read failed: {exc}")
            self._stop.wait(max(5, settings.WEBHOOK_SWEEP_SECONDS))


monday_inbox_worker = MondayInboxWorker()

"""Unified data access layer bridging in-memory AgentMemoryCache with persistent SQLite.

On write operations, data is written to BOTH the persistent database AND the in-memory
cache for instant AI Copilot retrieval. On read operations, the database is the source
of truth, with the in-memory cache used for fast lookups and RAG context.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models.db_models import (
    AuditLogDB,
    ExceptionDB,
    OrderDB,
    ReconciliationDB,
    UserDB,
)


def _opt_str(value: Any) -> Optional[str]:
    """Stores an external id as text, keeping None/blank as NULL."""
    if value is None or value == "" or value == 0:
        return None
    return str(value)


def _money(value: Any) -> Decimal:
    """Coerces an incoming amount to exact decimal.

    Values arrive as Decimal, float, int or string depending on the caller.
    Going through str() rather than Decimal(float) avoids inheriting the
    binary representation error of a float that was never exact to begin with.
    """
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        logger.warning(f"Could not read {value!r} as an amount; storing 0")
        return Decimal("0")


class DatabaseService:
    """Hybrid persistence layer: SQLite for durability, in-memory cache for speed."""

    # -----------------------------------------------------------------------
    # Orders
    # -----------------------------------------------------------------------

    @staticmethod
    def persist_order(db: Session, order_data: Dict[str, Any], actor_email: str = "system") -> OrderDB:
        """Writes an order to the database (upsert if exists) and returns the ORM object."""
        order_id = order_data.get("order_id", "")
        existing = db.query(OrderDB).filter(OrderDB.order_id == order_id).first() if order_id else None
        if existing:
            for k, v in order_data.items():
                if hasattr(existing, k) and k not in ("created_at", "order_id"):
                    setattr(existing, k, v)
            db.commit()
            db.refresh(existing)
            return existing

        record = OrderDB(
            order_id=order_data.get("order_id", ""),
            customer_po_number=order_data.get("customer_po_number", ""),
            sales_order_number=order_data.get("sales_order_number", ""),
            customer_id=order_data.get("customer_id", ""),
            customer_name=order_data.get("customer_name", ""),
            facility=order_data.get("facility", "HQ"),
            currency=order_data.get("currency", "USD"),
            status=order_data.get("status", "RECEIVED"),
            archetype=order_data.get("archetype", "STANDARD_STOCK"),
            total_revenue=_money(order_data.get("total_revenue", 0)),
            total_expense=_money(order_data.get("total_expense", 0)),
            gross_margin_pct=_money(order_data.get("gross_margin_pct", 0)),
            purchase_orders=order_data.get("purchase_orders", {}),
            line_items=order_data.get("line_items", []),
            shipping_address=order_data.get("shipping_address"),
            payment_terms=order_data.get("payment_terms", "Net 30 Days"),
            packing_slip_path=order_data.get("packing_slip_path"),
            internal_notes=order_data.get("internal_notes"),
            po_date=str(order_data.get("po_date", "")),
            created_by=actor_email,
            monday_board_id=_opt_str(order_data.get("monday_board_id")),
            monday_item_id=_opt_str(order_data.get("monday_item_id")),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        logger.info(f"Persisted order [{record.order_id}] to database (by {actor_email})")
        return record

    @staticmethod
    def get_all_orders(db: Session, facility: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Retrieves all orders from the database, optionally filtered by facility."""
        query = db.query(OrderDB).order_by(OrderDB.created_at.desc())
        if facility:
            query = query.filter(OrderDB.facility == facility)
        rows = query.limit(limit).all()
        return [
            {
                "order_id": r.order_id,
                "customer_po_number": r.customer_po_number,
                "sales_order_number": r.sales_order_number,
                "customer_id": r.customer_id,
                "customer_name": r.customer_name,
                "facility": r.facility,
                "currency": r.currency,
                "status": r.status,
                "total_revenue": r.total_revenue,
                "total_expense": r.total_expense,
                "gross_margin_pct": r.gross_margin_pct,
                "purchase_orders": r.purchase_orders or {},
                "line_items": r.line_items or [],
                "shipping_address": r.shipping_address,
                "payment_terms": r.payment_terms,
                "po_date": r.po_date,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "created_by": r.created_by,
                "monday_board_id": r.monday_board_id,
                "monday_item_id": r.monday_item_id,
                "sage_exported_at": r.sage_exported_at.isoformat() if r.sage_exported_at else None,
            }
            for r in rows
        ]

    # -----------------------------------------------------------------------
    # Reconciliations
    # -----------------------------------------------------------------------

    @staticmethod
    def persist_reconciliation(db: Session, rec_data: Dict[str, Any]) -> ReconciliationDB:
        """Writes a reconciliation record to the database (upsert if exists)."""
        rec_id = rec_data.get("reconciliation_id", "")
        existing = db.query(ReconciliationDB).filter(ReconciliationDB.reconciliation_id == rec_id).first() if rec_id else None
        if existing:
            for k, v in rec_data.items():
                if hasattr(existing, k) and k not in ("reconciled_at", "reconciliation_id"):
                    setattr(existing, k, v)
            db.commit()
            db.refresh(existing)
            return existing

        record = ReconciliationDB(
            reconciliation_id=rec_data.get("reconciliation_id", ""),
            invoice_number=rec_data.get("invoice_number", ""),
            vendor_name=rec_data.get("vendor_name", ""),
            po_number=rec_data.get("po_number", ""),
            reconciliation_status=rec_data.get("reconciliation_status", ""),
            authorized_po_total=_money(rec_data.get("authorized_po_total", 0)),
            actual_billed_total=_money(rec_data.get("actual_billed_total", 0)),
            cost_variance_amount=_money(rec_data.get("cost_variance_amount", 0)),
            cost_variance_pct=_money(rec_data.get("cost_variance_pct", 0)),
            discrepancies=rec_data.get("discrepancies", []),
            suggested_action=rec_data.get("suggested_action"),
            dispute_email_draft=rec_data.get("dispute_email_draft"),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_reconciliation_history(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves reconciliation history from the database."""
        rows = db.query(ReconciliationDB).order_by(ReconciliationDB.reconciled_at.desc()).limit(limit).all()
        return [
            {
                "reconciliation_id": r.reconciliation_id,
                "invoice_number": r.invoice_number,
                "vendor_name": r.vendor_name,
                "po_number": r.po_number,
                "reconciliation_status": r.reconciliation_status,
                "authorized_po_total": r.authorized_po_total,
                "actual_billed_total": r.actual_billed_total,
                "cost_variance_amount": r.cost_variance_amount,
                "cost_variance_pct": r.cost_variance_pct,
                "discrepancies": r.discrepancies or [],
                "suggested_action": r.suggested_action,
                "reconciled_at": r.reconciled_at.isoformat() if r.reconciled_at else None,
            }
            for r in rows
        ]

    # -----------------------------------------------------------------------
    # Exceptions
    # -----------------------------------------------------------------------

    @staticmethod
    def persist_exception(db: Session, exc_data: Dict[str, Any]) -> ExceptionDB:
        """Writes an exception item to the database (upsert if exists)."""
        exc_id = exc_data.get("exception_id", "")
        existing = db.query(ExceptionDB).filter(ExceptionDB.exception_id == exc_id).first() if exc_id else None
        if existing:
            for k, v in exc_data.items():
                if hasattr(existing, k) and k not in ("created_at", "exception_id"):
                    setattr(existing, k, v)
            db.commit()
            db.refresh(existing)
            return existing

        record = ExceptionDB(
            exception_id=exc_data.get("exception_id", ""),
            exception_type=exc_data.get("exception_type", ""),
            severity=exc_data.get("severity", "MEDIUM"),
            title=exc_data.get("title", ""),
            description=exc_data.get("description", ""),
            reference_id=exc_data.get("reference_id", ""),
            vendor_or_customer=exc_data.get("vendor_or_customer", ""),
            variance_amount=exc_data.get("variance_amount"),
            status=exc_data.get("status", "OPEN"),
            available_actions=exc_data.get("available_actions", []),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_exceptions(db: Session, status_filter: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves exceptions from database with optional status filter."""
        query = db.query(ExceptionDB).order_by(ExceptionDB.created_at.desc())
        if status_filter:
            query = query.filter(ExceptionDB.status == status_filter.upper())
        rows = query.limit(limit).all()
        return [
            {
                "exception_id": r.exception_id,
                "exception_type": r.exception_type,
                "severity": r.severity,
                "title": r.title,
                "description": r.description,
                "reference_id": r.reference_id,
                "vendor_or_customer": r.vendor_or_customer,
                "variance_amount": r.variance_amount,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "resolved_by": r.resolved_by,
                "resolution_notes": r.resolution_notes,
                "available_actions": r.available_actions or [],
            }
            for r in rows
        ]

    @staticmethod
    def get_exception_by_id(db: Session, exception_id: str) -> Optional[Dict[str, Any]]:
        """Fetches one exception by id, filtering in SQL on the indexed column."""
        row = (
            db.query(ExceptionDB)
            .filter(ExceptionDB.exception_id == exception_id)
            .first()
        )
        if row is None:
            return None
        return {
            "exception_id": row.exception_id,
            "exception_type": row.exception_type,
            "severity": row.severity,
            "title": row.title,
            "description": row.description,
            "reference_id": row.reference_id,
            "vendor_or_customer": row.vendor_or_customer,
            "variance_amount": row.variance_amount,
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            "resolved_by": row.resolved_by,
            "resolution_notes": row.resolution_notes,
            "available_actions": row.available_actions or [],
        }

    @staticmethod
    def resolve_exception(
        db: Session,
        exception_id: str,
        action_type: str,
        resolution_notes: str = "",
        actor_email: str = "system",
    ) -> Optional[ExceptionDB]:
        """Resolves an exception in the database with audit detail."""
        record = db.query(ExceptionDB).filter(ExceptionDB.exception_id == exception_id).first()
        if not record:
            return None
        record.status = "RESOLVED"
        record.resolved_at = datetime.now(timezone.utc)
        record.resolved_by = actor_email
        record.resolution_notes = f"{action_type}: {resolution_notes}" if resolution_notes else action_type
        db.commit()
        db.refresh(record)
        return record

    # -----------------------------------------------------------------------
    # Audit Log
    # -----------------------------------------------------------------------

    @staticmethod
    def write_audit(
        db: Session,
        action: str,
        resource_type: str,
        resource_id: str = "",
        actor_email: str = "system",
        actor_role: str = "SYSTEM",
        facility: str = "HQ",
        detail: str = "",
        ip_address: str = "",
        correlation_id: str = "",
    ) -> AuditLogDB:
        """Writes an immutable audit log entry.

        The entry is committed in its own short-lived session rather than on the
        caller's. Committing the request session here would also commit whatever
        else that handler had pending, turning an audit write into an
        unintended partial save of the operation being audited.
        """
        from app.core.database import SessionLocal

        entry_values = dict(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_email=actor_email,
            actor_role=actor_role,
            facility=facility,
            detail=detail,
            ip_address=ip_address,
            correlation_id=correlation_id,
        )

        with SessionLocal() as audit_db:
            entry = AuditLogDB(**entry_values)
            audit_db.add(entry)
            audit_db.commit()
            audit_db.refresh(entry)
            # Detach so the caller can read attributes after the session closes.
            audit_db.expunge(entry)
        return entry

    @staticmethod
    def get_audit_log(
        db: Session,
        resource_type: Optional[str] = None,
        actor_email: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Retrieves audit log entries with optional filters."""
        query = db.query(AuditLogDB).order_by(AuditLogDB.timestamp.desc())
        if resource_type:
            query = query.filter(AuditLogDB.resource_type == resource_type)
        if actor_email:
            query = query.filter(AuditLogDB.actor_email == actor_email)
        rows = query.limit(limit).all()
        return [
            {
                "event_id": r.event_id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "actor_email": r.actor_email,
                "actor_role": r.actor_role,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "facility": r.facility,
                "detail": r.detail,
            }
            for r in rows
        ]

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    @staticmethod
    def get_user_by_email(db: Session, email: str) -> Optional[UserDB]:
        """Fetches a user by email address. Email comparison is case-insensitive."""
        return db.query(UserDB).filter(UserDB.email == (email or "").strip().lower()).first()

    @staticmethod
    def create_user(
        db: Session,
        email: str,
        hashed_password: str,
        full_name: str,
        role: str = "VIEWER",
        facility: str = "HQ",
    ) -> UserDB:
        """Creates a new user account. Email is normalized to lowercase for lookup."""
        user = UserDB(
            email=(email or "").strip().lower(),
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
            facility=facility,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def update_last_login(db: Session, user: UserDB) -> None:
        """Updates the last_login timestamp for a user."""
        user.last_login = datetime.now(timezone.utc)
        db.commit()

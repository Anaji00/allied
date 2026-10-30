"""Immutable audit logging with actor context for enterprise compliance.

Every mutation (order creation, price override, exception resolution, Sage export,
user login) is recorded with actor identity, timestamp, facility, and detail.
Audit records are INSERT-only — never updated or deleted.
"""

from typing import Optional
from sqlalchemy.orm import Session

from app.core.correlation import get_correlation_id
from app.core.logging import logger


def audit_event(
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
) -> None:
    """Records an immutable audit trail entry.

    Args:
        db: Active database session
        action: Verb describing the mutation (CREATE, UPDATE, DELETE, LOGIN, EXPORT, RESOLVE)
        resource_type: Entity class (ORDER, USER, RECONCILIATION, EXCEPTION, SAGE_EXPORT)
        resource_id: Primary identifier of the affected resource
        actor_email: Email of the user who initiated the action
        actor_role: Role of the actor (ADMIN, MANAGER, OPERATOR, VIEWER, SYSTEM)
        facility: Facility where the action occurred (HQ, EDMONTON, ZIBO)
        detail: Human-readable description of what happened
        ip_address: Client IP address
        correlation_id: Request correlation ID for tracing
    """
    # Fall back to the current request's correlation id so callers deep in the
    # service layer do not each have to pass it.
    correlation_id = correlation_id or get_correlation_id()
    try:
        from app.services.database_service import DatabaseService
        DatabaseService.write_audit(
            db=db,
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
        logger.debug(
            f"AUDIT: {action} {resource_type}/{resource_id} by {actor_email} ({actor_role})"
        )
    except Exception as exc:
        # Audit logging must never crash the primary operation
        logger.error(f"Audit logging failed: {exc}")


def audit_log(
    action: str,
    resource_type: str,
    resource_id: str = "",
    actor_email: str = "system",
    actor_role: str = "SYSTEM",
    facility: str = "HQ",
    detail: str = "",
    ip_address: str = "",
    correlation_id: str = "",
    db: Optional[Session] = None,
) -> None:
    """Convenience wrapper for audit logging that manages SessionLocal automatically if db is None."""
    if db is not None:
        audit_event(
            db=db,
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
        return

    try:
        from app.core.database import SessionLocal
        with SessionLocal() as session:
            audit_event(
                db=session,
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
    except Exception as exc:
        logger.error(f"Audit logging wrapper failed: {exc}")

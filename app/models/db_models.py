"""SQLAlchemy ORM models for persistent enterprise data storage.

Tables:
  - users: RBAC user accounts with hashed credentials
  - orders: Persisted master orders (SO + split POs)
  - reconciliations: Vendor invoice 3-way match audit records
  - exceptions: Operations discrepancy queue
  - audit_log: Immutable audit trail of all system mutations
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Numeric,
    DateTime,
    Integer,
    String,
    Text,
    Boolean,
    JSON,
)

from app.core.database import Base


def _uuid() -> str:
    """Generates an identifier for a column with a UNIQUE constraint.

    Uses the full uuid4. Truncating to 8 hex characters left only 32 bits, so
    audit_log.event_id had roughly even odds of colliding by ~77,000 rows --
    and a collision surfaces as an IntegrityError on an audit write.
    """
    return str(uuid.uuid4()).upper()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Money is stored as exact decimal, never float. These amounts are compared
# for equality during three-way invoice matching, which is exactly where
# binary floating point produces a phantom discrepancy on an invoice that
# actually matches to the cent.
MONEY = Numeric(14, 2)
# A percentage needs more places than money but the same exactness.
RATE = Numeric(9, 4)


# ---------------------------------------------------------------------------
# Users & RBAC
# ---------------------------------------------------------------------------


class UserDB(Base):
    """Enterprise user account with role-based access."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), unique=True, nullable=False, default=_uuid)
    email = Column(String(256), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    full_name = Column(String(256), nullable=False)
    role = Column(String(64), nullable=False, default="VIEWER")  # ADMIN, MANAGER, OPERATOR, VIEWER
    facility = Column(String(64), nullable=False, default="HQ")  # HQ, EDMONTON, ZIBO
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    last_login = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Orders — persistent copy of in-memory cache
# ---------------------------------------------------------------------------


class OrderDB(Base):
    """Persisted master order record."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(16), unique=True, nullable=False, index=True)
    customer_po_number = Column(String(64), nullable=False, index=True)
    sales_order_number = Column(String(64), nullable=False, index=True)
    customer_id = Column(String(64), nullable=False)
    customer_name = Column(String(256), nullable=False)
    facility = Column(String(64), nullable=False, default="HQ")
    currency = Column(String(3), nullable=False, default="USD")  # USD, CAD, CNY
    status = Column(String(32), nullable=False, default="RECEIVED")
    archetype = Column(String(32), nullable=False, default="STANDARD_STOCK")
    total_revenue = Column(MONEY, default=0)
    total_expense = Column(MONEY, default=0)
    gross_margin_pct = Column(RATE, default=0)
    purchase_orders = Column(JSON, default=dict)  # {vendor: po_number}
    line_items = Column(JSON, default=list)
    shipping_address = Column(Text, nullable=True)
    payment_terms = Column(String(64), default="Net 30 Days")
    packing_slip_path = Column(String(512), nullable=True)
    internal_notes = Column(Text, nullable=True)
    po_date = Column(String(16), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    created_by = Column(String(256), nullable=True)
    # The board item this order came from. Without it a later status change on
    # that item ("Shipped") had no reliable way back to the order it produced.
    monday_board_id = Column(String(32), nullable=True)
    monday_item_id = Column(String(32), nullable=True, index=True)
    # Set when the order is included in a Sage import file, so the next export
    # does not hand Sage the same sales order twice.
    sage_exported_at = Column(DateTime(timezone=True), nullable=True)
    # The same for the order's vendor POs, which go to Sage in their own file.
    sage_po_exported_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Staff coordination: projects and milestones from monday, design-doc
# submissions, and who gets told when something is behind
# ---------------------------------------------------------------------------


class StaffContactDB(Base):
    """How to reach a person. Leadership gets the daily summary and escalations."""

    __tablename__ = "staff_contacts"

    email = Column(String(256), primary_key=True)
    name = Column(String(256), nullable=False)
    role_tag = Column(String(32), nullable=False, default="staff")  # leadership, engineer, operations, staff
    phone = Column(String(32), nullable=True)  # E.164, e.g. +12055550100
    channels = Column(JSON, nullable=False, default=lambda: ["email"])  # email, sms, push
    daily_summary = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)


class NotificationDB(Base):
    """One alert. The in-app inbox is the record; email/text/push are deliveries of it."""

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_key = Column(String(200), nullable=False, index=True)
    kind = Column(String(48), nullable=False, index=True)
    severity = Column(String(16), nullable=False, default="info")  # info, warning, critical
    title = Column(String(300), nullable=False)
    body = Column(Text, nullable=False)
    link = Column(String(300), nullable=True)
    project_item_id = Column(String(32), nullable=True, index=True)
    recipients = Column(JSON, nullable=False, default=list)
    deliveries = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by = Column(String(256), nullable=True)
    escalated_at = Column(DateTime(timezone=True), nullable=True)


class MondayProjectDB(Base):
    """A project item on the monday projects board, mirrored for schedule checks.

    monday is the source of truth; this copy is refreshed by the board reader
    and only exists so lateness can be worked out without a call per check.
    """

    __tablename__ = "monday_projects"

    item_id = Column(String(32), primary_key=True)
    board_id = Column(String(32), nullable=False, index=True)
    name = Column(String(512), nullable=False)
    group_title = Column(String(256), nullable=True)
    owner_emails = Column(JSON, nullable=False, default=list)
    status = Column(String(128), nullable=True)
    due_date = Column(String(10), nullable=True)
    done = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MondayMilestoneDB(Base):
    """A milestone (a subitem, or an item, on the projects board)."""

    __tablename__ = "monday_milestones"

    item_id = Column(String(32), primary_key=True)
    project_item_id = Column(String(32), nullable=False, index=True)
    board_id = Column(String(32), nullable=True)
    name = Column(String(512), nullable=False)
    owner_emails = Column(JSON, nullable=False, default=list)
    reviewer_emails = Column(JSON, nullable=False, default=list)
    status = Column(String(128), nullable=True)
    due_date = Column(String(10), nullable=True)
    done = Column(Boolean, nullable=False, default=False)
    needs_submission = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ShipmentDB(Base):
    """Goods moving with a freight forwarder. Late, slipping or silent ones are flagged."""

    __tablename__ = "shipments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_item_id = Column(String(32), nullable=True, index=True)
    forwarder = Column(String(128), nullable=True)
    mode = Column(String(8), nullable=False, default="SEA")  # SEA, AIR, TRUCK
    reference = Column(String(64), nullable=True, index=True)   # booking / house B/L
    container_no = Column(String(16), nullable=True, index=True)
    awb = Column(String(16), nullable=True, index=True)
    carrier = Column(String(64), nullable=True)
    origin = Column(String(128), nullable=True)
    destination = Column(String(128), nullable=True)
    etd = Column(String(10), nullable=True)
    eta_original = Column(String(10), nullable=True)
    eta_current = Column(String(10), nullable=True)
    free_time_ends = Column(String(10), nullable=True)  # last free day at the port
    status = Column(String(20), nullable=False, default="BOOKED")
    owner_email = Column(String(256), nullable=True)
    last_update_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ShipmentEventDB(Base):
    __tablename__ = "shipment_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    shipment_id = Column(Integer, nullable=False, index=True)
    at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    status = Column(String(20), nullable=True)
    eta = Column(String(10), nullable=True)
    description = Column(Text, nullable=False)
    source = Column(String(16), nullable=False, default="manual")  # manual, email, api
    raw = Column(JSON, nullable=True)


class SubmissionDB(Base):
    """Design documents submitted against a milestone, and the review of them.

    The reviewer must be someone other than the submitter.
    """

    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    milestone_item_id = Column(String(32), nullable=False, index=True)
    project_item_id = Column(String(32), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    files = Column(JSON, nullable=False, default=list)  # [{name, stored, size, sha256}]
    note = Column(Text, nullable=True)
    submitted_by = Column(String(256), nullable=False)
    submitted_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    status = Column(String(20), nullable=False, default="SUBMITTED")  # SUBMITTED, APPROVED, CHANGES_REQUESTED
    reviewed_by = Column(String(256), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# AI suggestions — what the AI proposed, and who accepted or rejected it
# ---------------------------------------------------------------------------


class AiSuggestionDB(Base):
    """One AI result: an email sorted, a call summarised, a drawing checked.

    AI never acts on its own; anything binding waits here as SUGGESTED until a
    person approves or rejects it. INFO rows are results nobody has to act on
    (a translation, an explanation) but are kept so usage can be measured.
    """

    __tablename__ = "ai_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feature = Column(String(32), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="SUGGESTED", index=True)
    title = Column(String(256), nullable=False)
    reference = Column(String(128), nullable=True, index=True)
    provider = Column(String(16), nullable=False)
    model = Column(String(64), nullable=True)
    confidence = Column(RATE, nullable=True)
    needs_review = Column(Boolean, nullable=False, default=True)
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    created_by = Column(String(256), nullable=True)
    reviewed_by = Column(String(256), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)


# ---------------------------------------------------------------------------
# monday.com webhook inbox
# ---------------------------------------------------------------------------


class MondayItemStateDB(Base):
    """The trigger column's last-seen value per board item, for the board reader.

    The reader compares the board with this to spot changes itself, so no
    monday automation or integration action is needed to learn about them.
    """

    __tablename__ = "monday_item_state"

    item_id = Column(String(32), primary_key=True)
    board_id = Column(String(32), nullable=False, index=True)
    label = Column(String(128), nullable=True)
    item_name = Column(String(512), nullable=True)
    seen_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    changed_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MondayEventDB(Base):
    """One received monday.com webhook delivery.

    Events are written here before the webhook is acknowledged, so a crash or
    restart never loses one, and a redelivery is recognised by dedup_key
    instead of being processed twice.

    status: PENDING -> PROCESSING -> DONE | IGNORED | RETRY -> ... -> DEAD
    """

    __tablename__ = "monday_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_key = Column(String(128), unique=True, nullable=False, index=True)
    event_type = Column(String(64), nullable=True)
    board_id = Column(String(32), nullable=True, index=True)
    item_id = Column(String(32), nullable=True, index=True)
    column_id = Column(String(64), nullable=True)
    user_id = Column(String(32), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(16), nullable=False, default="PENDING", index=True)
    action = Column(String(32), nullable=True)  # which route handled it
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    received_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    correlation_id = Column(String(64), nullable=True)


# ---------------------------------------------------------------------------
# Reconciliation — vendor invoice 3-way match records
# ---------------------------------------------------------------------------


class ReconciliationDB(Base):
    """Persisted vendor bill reconciliation audit record."""

    __tablename__ = "reconciliations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reconciliation_id = Column(String(32), unique=True, nullable=False, index=True)
    invoice_number = Column(String(64), nullable=False)
    vendor_name = Column(String(256), nullable=False)
    po_number = Column(String(64), nullable=False)
    reconciliation_status = Column(String(32), nullable=False)
    authorized_po_total = Column(MONEY, default=0)
    actual_billed_total = Column(MONEY, default=0)
    cost_variance_amount = Column(MONEY, default=0)
    cost_variance_pct = Column(RATE, default=0)
    discrepancies = Column(JSON, default=list)
    suggested_action = Column(Text, nullable=True)
    dispute_email_draft = Column(Text, nullable=True)
    reconciled_at = Column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Exception Queue — operations discrepancy items
# ---------------------------------------------------------------------------


class ExceptionDB(Base):
    """Persisted operations exception / discrepancy record."""

    __tablename__ = "exceptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exception_id = Column(String(32), unique=True, nullable=False, index=True)
    exception_type = Column(String(64), nullable=False)
    severity = Column(String(16), nullable=False, default="MEDIUM")
    title = Column(String(512), nullable=False)
    description = Column(Text, nullable=False)
    reference_id = Column(String(64), nullable=False)
    vendor_or_customer = Column(String(256), nullable=False)
    variance_amount = Column(MONEY, nullable=True)
    status = Column(String(16), nullable=False, default="OPEN")
    resolution_notes = Column(Text, nullable=True)
    available_actions = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(String(256), nullable=True)


# ---------------------------------------------------------------------------
# Immutable Audit Log
# ---------------------------------------------------------------------------


class AuditLogDB(Base):
    """Immutable audit trail entry — INSERT only, never UPDATE or DELETE."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(32), unique=True, nullable=False, default=_uuid, index=True)
    timestamp = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    actor_email = Column(String(256), nullable=False, default="system")
    actor_role = Column(String(64), nullable=False, default="SYSTEM")
    action = Column(String(64), nullable=False)  # CREATE, UPDATE, DELETE, LOGIN, EXPORT, RESOLVE
    resource_type = Column(String(64), nullable=False)  # ORDER, USER, RECONCILIATION, EXCEPTION, SAGE_EXPORT
    resource_id = Column(String(64), nullable=True)
    facility = Column(String(64), nullable=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    correlation_id = Column(String(64), nullable=True)

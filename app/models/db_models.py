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
    UniqueConstraint,
    Index,
    func,
    text,
    Column,
    Numeric,
    DateTime,
    Integer,
    Float,
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
    language = Column(String(8), nullable=False, default="en", server_default="en")  # en, zh, vi: what the app shows them in
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
    snoozed_until = Column(DateTime(timezone=True), nullable=True)   # hidden from the lists until this time passes
    assignee = Column(String(256), nullable=True)                    # the one person it was handed to


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


class VendorJobDB(Base):
    """One vendor PO for a project: when it was asked for, promised and shipped.

    The first promise is kept apart from the current one, so a vendor that
    keeps re-promising does not look on time. Every date change is in history.
    """

    __tablename__ = "vendor_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    monday_item_id = Column(String(32), nullable=True, unique=True)
    board_id = Column(String(32), nullable=True)
    vendor = Column(String(256), nullable=False, index=True)
    description = Column(String(512), nullable=False, default="")
    project_item_id = Column(String(32), nullable=True, index=True)
    po_number = Column(String(64), nullable=True)
    purchase_order_id = Column(Integer, nullable=True, index=True)   # the one purchase order record this line is for (number, vendor, lines, status live there)
    owner_email = Column(String(256), nullable=True)
    status = Column(String(128), nullable=True)
    mode = Column(String(8), nullable=False, default="SEA")
    ordered_on = Column(String(10), nullable=True)
    requested_on = Column(String(10), nullable=True)
    acknowledged_on = Column(String(10), nullable=True)
    quoted_lead_days = Column(Integer, nullable=True)
    follow_up_days = Column(Integer, nullable=True)  # set by hand: days after the PO before the vendor is followed up
    promised_original = Column(String(10), nullable=True)
    promised_on = Column(String(10), nullable=True)
    shipped_on = Column(String(10), nullable=True)
    need_by = Column(String(10), nullable=True)  # set by hand; otherwise worked out from the project
    customer_due = Column(String(10), nullable=True)  # the customer's date, when no project is linked
    slips = Column(Integer, nullable=False, default=0)
    history = Column(JSON, nullable=False, default=list)
    source = Column(String(16), nullable=False, default="manual")  # manual, monday
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RfqDB(Base):
    """A customer's request for a quote, and how long the quote took."""

    __tablename__ = "rfqs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    number = Column(String(24), nullable=False, unique=True)
    email_id = Column(String(256), nullable=True, unique=True)
    monday_item_id = Column(String(32), nullable=True, unique=True, index=True)  # from the Open RFQs board
    customer = Column(String(256), nullable=False, default="")
    contact_email = Column(String(256), nullable=True)
    subject = Column(String(512), nullable=False, default="")
    summary = Column(Text, nullable=True)
    items = Column(JSON, nullable=False, default=list)
    complexity = Column(String(16), nullable=False, default="STANDARD")  # SIMPLE, STANDARD, COMPLEX
    status = Column(String(20), nullable=False, default="NEW", index=True)
    owner_email = Column(String(256), nullable=True, index=True)
    vendor = Column(String(256), nullable=True)
    received_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    ack_due_at = Column(DateTime(timezone=True), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    waiting_since = Column(DateTime(timezone=True), nullable=True)   # asked a vendor for a price
    paused_at = Column(DateTime(timezone=True), nullable=True)       # waiting on the customer
    paused_hours = Column(Numeric(9, 2), nullable=False, default=0)
    quoted_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    quote_number = Column(String(64), nullable=True)
    quote_amount = Column(MONEY, nullable=True)
    quote_currency = Column(String(3), nullable=True)                 # empty for older quotes; the currency the quote_amount is in
    close_reason = Column(String(256), nullable=True)
    notes = Column(Text, nullable=True)
    history = Column(JSON, nullable=False, default=list)
    source = Column(String(16), nullable=False, default="manual")  # manual, email, assistant
    created_by = Column(String(256), nullable=True)


class EngineeringNoteDB(Base):
    """A note from management to the engineers on one engineering job."""

    __tablename__ = "engineering_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    board_id = Column(String(32), nullable=True, index=True)
    item_id = Column(String(32), nullable=False, index=True)      # the monday item (drawing job)
    item_name = Column(String(512), nullable=True)
    author_email = Column(String(256), nullable=False)
    text = Column(Text, nullable=False)                           # the English note, as written
    translations = Column(JSON, nullable=False, default=dict)     # {"zh": {...}, "vi": {...}}
    recipients = Column(JSON, nullable=False, default=list)
    urgent = Column(Boolean, nullable=False, default=False)       # also sent as a text and push
    read_by = Column(JSON, nullable=False, default=dict)          # {email: when read}
    audio = Column(JSON, nullable=True)                           # {file, mime, size, seconds} of a voice note
    transcript = Column(Text, nullable=True)                      # what the voice note says, as heard
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ItemFileDB(Base):
    """A file attached to an item (or to a comment on it). Stored on the server; removing one hides it."""

    __tablename__ = "item_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_type = Column(String(24), nullable=False)
    item_ref = Column(String(64), nullable=False)
    update_id = Column(Integer, nullable=True)            # set when attached to a comment
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(1024), nullable=False)
    size = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=False, default="")
    content_type = Column(String(120), nullable=True)
    uploaded_by = Column(String(256), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    design = Column(Boolean, nullable=False, default=False)       # marked as a design file: the only kind that goes to a vendor


class OutboundMailDB(Base):
    """The send ledger: one row for every mail the app tried to send or only drafted, with what really happened (SENT, DRAFTED, FAILED; REDIRECTED is a
    SENT to the pilot test inbox). The PO and order paths read "did it go?" from here, not from memory, so a restart never forgets."""

    __tablename__ = "outbound_mail_log"
    __table_args__ = (Index("ix_mail_log_ref", "category", "reference"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(32), nullable=False, unique=True)
    category = Column(String(32), nullable=False, default="GENERAL")
    reference = Column(String(128), nullable=True)
    to_addr = Column(String(512), nullable=True)
    intended_to = Column(String(512), nullable=True)
    status = Column(String(16), nullable=False)                           # SENT, DRAFTED, FAILED
    redirected = Column(Boolean, nullable=False, default=False)
    subject = Column(String(256), nullable=True)
    detail = Column(String(400), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class MondayChangeDB(Base):
    """A change to monday that is staged (saved, not sent) until someone pushes it. Like a commit that hasn't been pushed."""

    __tablename__ = "monday_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(16), nullable=False)               # create_item, update_columns, post_update
    board_id = Column(String(32), nullable=False, default="")
    item_id = Column(String(32), nullable=False, default="")
    payload = Column(JSON, nullable=False, default=dict)
    summary = Column(String(500), nullable=False, default="")
    source = Column(String(40), nullable=False, default="app")
    status = Column(String(12), nullable=False, default="STAGED", index=True)   # STAGED, PUSHED, FAILED, SKIPPED, EXPIRED
    dedupe_key = Column(String(120), nullable=False, default="", index=True)
    staged_by = Column(String(256), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=True)
    pushed_at = Column(DateTime(timezone=True), nullable=True)


class ItemBallDB(Base):
    """Whose turn it is on an item, and since when. One row per item. Moving the ball restarts the clock
    and the chasing; the history keeps who moved it, when and why."""

    __tablename__ = "item_balls"
    __table_args__ = (Index("ix_item_balls_item", "item_type", "item_ref", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_type = Column(String(24), nullable=False)
    item_ref = Column(String(64), nullable=False)
    owner_email = Column(String(256), nullable=True)       # who is responsible for the item getting done
    ball_with = Column(String(16), nullable=False)         # us, engineer, manager, vendor, customer, carrier, other
    ball_person = Column(String(256), nullable=True)       # an email for our own people, a name for outsiders
    note = Column(String(300), nullable=True)               # what it is waiting for
    due_at = Column(String(10), nullable=True)
    ball_since = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    escalation_level = Column(Integer, nullable=False, default=0)   # 0 none, 1 nudged, 2 owner told, 3 leadership told
    last_escalated_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    history = Column(JSON, nullable=False, default=list)
    set_by = Column(String(256), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ItemUpdateDB(Base):
    """A comment on any order, RFQ, vendor job, shipment, milestone or board item: one thread per item,
    one level of replies, @mentions that alert the person named. Deleting hides it (the row stays)."""

    __tablename__ = "item_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_type = Column(String(24), nullable=False)       # order, rfq, vendor_job, shipment, milestone, board_item
    item_ref = Column(String(64), nullable=False)
    parent_id = Column(Integer, nullable=True)           # set on a reply
    author_email = Column(String(256), nullable=False)
    author_name = Column(String(256), nullable=False, default="")
    body = Column(Text, nullable=False)
    mentions = Column(JSON, nullable=False, default=list)  # emails of the people named
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class PackingSlipDB(Base):
    """A sales order's packing slip: queued on creation, made, retried if it fails, marked printed."""

    __tablename__ = "packing_slips"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sales_order_number = Column(String(64), nullable=False, unique=True, index=True)
    customer_po_number = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="QUEUED", index=True)   # QUEUED, MAKING, READY, FAILED, PRINTED
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    path = Column(String(512), nullable=True)
    payload = Column(JSON, nullable=True)                 # the order as it was, so a retry makes the same slip
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    next_try_at = Column(DateTime(timezone=True), nullable=True)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    printed_at = Column(DateTime(timezone=True), nullable=True)
    printed_by = Column(String(256), nullable=True)


class MailLogDB(Base):
    """Every email the mailbox reader has seen, once, and what was done with it."""

    __tablename__ = "mail_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(512), nullable=False, unique=True, index=True)
    folder = Column(String(128), nullable=False)
    sender = Column(String(320), nullable=False)
    sender_name = Column(String(320), nullable=True)
    recipients = Column(String(2000), nullable=True)
    subject = Column(String(500), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=True, index=True)
    direction = Column(String(16), nullable=False)       # in (from outside), internal (staff), out (sent by us)
    category = Column(String(32), nullable=False, index=True)
    tags = Column(String(300), nullable=True)             # comma-separated: rfq, invoice, urgent, newsletter, ...
    thread_key = Column(String(200), nullable=True, index=True)   # the conversation's first Message-ID
    summary = Column(Text, nullable=True)
    outcome = Column(String(64), nullable=False)          # what the system did (RFQ_LOGGED, VENDOR_REPLY, LOGGED_ONLY, ...)
    needs_action = Column(Boolean, nullable=False, default=False, index=True)
    handled_by = Column(String(256), nullable=True)
    handled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AutomationRecipeDB(Base):
    """A person's own rule: when an item's status becomes a label, or a date is N days
    away, tell someone or draft an email. Recipes only ever act inside Allied's system."""

    __tablename__ = "automation_recipes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    board_role = Column(String(16), nullable=False)        # projects, vendors, rfqs
    trigger = Column(String(16), nullable=False)           # status_is, date_within
    column = Column(String(128), nullable=True)            # column id or title; blank = the status column
    value = Column(String(128), nullable=True)             # the label, or the number of days
    action = Column(String(16), nullable=False)            # notify, draft_email
    recipients = Column(String(512), nullable=True)        # emails, or owner / leadership / reviewers
    email_to = Column(String(256), nullable=True)          # draft_email: an address, or vendor / customer
    subject = Column(String(256), nullable=True)
    body = Column(Text, nullable=True)                     # {item} {status} {date} {board} and column titles
    mode = Column(String(8), nullable=False, default="suggest")   # suggest: a person sends; auto
    enabled = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AssistantActionDB(Base):
    """A change the assistant proposed. Nothing happens until its user confirms it."""

    __tablename__ = "assistant_actions"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_email = Column(String(256), nullable=False, index=True)
    user_role = Column(String(32), nullable=False)
    tool = Column(String(128), nullable=False)
    args = Column(JSON, nullable=False, default=dict)
    summary = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="PENDING")  # PENDING, DONE, FAILED, CANCELLED, EXPIRED
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)


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
    bill_detail = Column(JSON, nullable=True)                               # {"lines": [...], "freight", "currency", "invoice_date"}: the bill as read; empty for bills checked before this was kept
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


class JobDB(Base):
    """One job from the customer's RFQ to the shipment: the thread that links the RFQ, the engineering talk, the design approval,
    the vendor POs, the sales order and the shipments. Its stage is worked out from what exists, not typed in."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ux_jobs_monday_item", "monday_board_id", "monday_item_id", unique=True),)      # one monday item belongs to at most one job

    id = Column(Integer, primary_key=True, autoincrement=True)
    ref = Column(String(24), unique=True, nullable=False, index=True)        # JOB-2026-0001
    title = Column(String(256), nullable=False, default="")
    customer = Column(String(256), nullable=True)
    rfq_id = Column(Integer, nullable=True, unique=True)
    sales_order_number = Column(String(64), nullable=True, index=True)
    design_approved_by = Column(String(256), nullable=True)
    design_approved_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    monday_board_id = Column(String(32), nullable=True)                      # the monday item this job is linked to (set only by a person, or by making the job from / pushing it to that item)
    monday_item_id = Column(String(32), nullable=True)


class PurchaseOrderDB(Base):
    """A purchase order Allied issues to a vendor for a job: one per vendor per job. It is drafted, approved after the design is
    approved, then sent as a draft a person OKs; the same number is kept when the customer's order arrives."""

    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ux_po_job_vendor", "job_id", func.lower(Column("vendor")), unique=True, sqlite_where=text("status <> 'CANCELLED'")),
                      Index("ux_po_number", "po_number", unique=True, sqlite_where=text("po_number is not null")))

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=False, index=True)
    po_number = Column(String(64), nullable=True, index=True)               # assigned when it is approved
    vendor = Column(String(256), nullable=False)
    vendor_email = Column(String(256), nullable=True)
    lines = Column(JSON, nullable=False, default=list)                       # [{qty, item, description, unit_cost, amount}]
    terms = Column(String(64), nullable=True)
    ship_via = Column(String(64), nullable=True)
    good_thru = Column(String(16), nullable=True)
    status = Column(String(16), nullable=False, default="DRAFT")            # DRAFT, APPROVED, SENT, ACKNOWLEDGED
    approved_by = Column(String(256), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    action_id = Column(String(36), nullable=True)                            # the email draft waiting for a person's OK
    incoterm = Column(String(16), nullable=True)                            # optional: FOB, EXW ... for an overseas vendor
    revision_marks = Column(JSON, nullable=True)                            # {"acked": {"1": {"revision", "by", "at"}}}: the drawing revision the vendor said it is making
    incoterm_place = Column(String(64), nullable=True)                      # optional: Ningbo ...
    cancelled_at = Column(DateTime(timezone=True), nullable=True)           # a manager cancelled it (the customer cancelled, or it was raised by mistake)
    cancel_reason = Column(String(256), nullable=True)
    currency = Column(String(3), nullable=True)                              # the currency the prices are in; blank until it is stated, never assumed to be USD
    created_by = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class JobResearchDB(Base):
    """A research note on a job: a site or result someone (or the web search) found, with a note."""

    __tablename__ = "job_research"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=False, index=True)
    title = Column(String(512), nullable=False, default="")
    url = Column(String(1024), nullable=True)
    note = Column(Text, nullable=True)
    source = Column(String(16), nullable=False, default="manual")           # manual, search
    created_by = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class AutomationModeDB(Base):
    """The one switch for a timed play or an instant reaction: off, suggest (a person approves) or auto. Overrides the environment setting."""

    __tablename__ = "automation_modes"

    key = Column(String(80), primary_key=True)                 # play:vendor_chaser, reaction:rfq_won
    mode = Column(String(8), nullable=False)
    changed_by = Column(String(256), nullable=True)
    changed_at = Column(DateTime(timezone=True), nullable=True)


class JobDocumentDB(Base):
    """One document found inside a scanned job packet (a PO, a quotation, a sales order, drawings...): what it is, which pages, and the facts printed
    on it. A fact that is not printed is not stored."""

    __tablename__ = "job_documents"
    __table_args__ = (UniqueConstraint("source_sha", "page_from", name="ux_job_documents_page"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=True, index=True)            # blank until the packet is attached to a job
    kind = Column(String(24), nullable=False)
    label = Column(String(80), nullable=False, default="")
    page_from = Column(Integer, nullable=False)
    page_to = Column(Integer, nullable=False)
    fields = Column(JSON, nullable=False, default=dict)
    text = Column(Text, nullable=True)                              # what the pages say, for search
    source_name = Column(String(255), nullable=False, default="")
    source_sha = Column(String(64), nullable=False, index=True)
    created_by = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    confirmed_by = Column(String(256), nullable=True)            # a person checked these facts against the page
    confirmed_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Sage 50 import mirrors: read-only copies of what a Sage export file said. The app never writes to Sage; these tables only ever come from a file.
# ---------------------------------------------------------------------------

class SageImportDB(Base):
    """One committed import file: what it was, who brought it in, what it changed. The same file (same kind, same hash) is never taken twice."""

    __tablename__ = "sage_imports"
    __table_args__ = (UniqueConstraint("kind", "file_hash", name="ux_sage_imports_file"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(24), nullable=False)
    file_name = Column(String(255), nullable=False, default="")
    file_hash = Column(String(64), nullable=False)
    rows_total = Column(Integer, nullable=False, default=0)
    created = Column(Integer, nullable=False, default=0)
    updated = Column(Integer, nullable=False, default=0)
    unchanged = Column(Integer, nullable=False, default=0)
    problems = Column(Integer, nullable=False, default=0)
    imported_by = Column(String(256), nullable=True)
    imported_at = Column(DateTime(timezone=True), default=_utcnow)


class SageInvoiceDB(Base):
    __tablename__ = "sage_invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String(64), nullable=False, unique=True)
    customer_id = Column(String(64), nullable=True, index=True)
    customer_name = Column(String(256), nullable=True)
    invoice_date = Column(String(10), nullable=False)
    due_date = Column(String(10), nullable=True)
    customer_po = Column(String(128), nullable=True, index=True)
    sales_order = Column(String(64), nullable=True, index=True)
    total = Column(Numeric(14, 2), nullable=False)
    amount_due = Column(Numeric(14, 2), nullable=True)           # blank when the file did not say: then payments decide
    import_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)


class SagePaymentDB(Base):
    __tablename__ = "sage_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pay_key = Column(String(64), nullable=False, unique=True)
    receipt_number = Column(String(64), nullable=True)
    payment_date = Column(String(10), nullable=False)
    customer_id = Column(String(64), nullable=True)
    invoice_number = Column(String(64), nullable=True, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    import_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)


class SageOpenOrderDB(Base):
    """A sales order or purchase order that is still open in Sage (so the app does not make it twice)."""

    __tablename__ = "sage_open_orders"
    __table_args__ = (UniqueConstraint("kind", "number", name="ux_sage_open_orders"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(2), nullable=False)                     # SO or PO
    number = Column(String(64), nullable=False)
    party_id = Column(String(64), nullable=True)
    party_name = Column(String(256), nullable=True)
    order_date = Column(String(10), nullable=False)
    customer_po = Column(String(128), nullable=True, index=True)
    amount = Column(Numeric(14, 2), nullable=True)
    open_amount = Column(Numeric(14, 2), nullable=True)
    import_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)


class SageBillDB(Base):
    __tablename__ = "sage_bills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bill_key = Column(String(160), nullable=False, unique=True)  # vendor | bill number: two vendors can use the same number
    bill_number = Column(String(64), nullable=False, index=True)
    vendor_id = Column(String(64), nullable=True)
    vendor_name = Column(String(256), nullable=True)
    bill_date = Column(String(10), nullable=False)
    due_date = Column(String(10), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    amount_due = Column(Numeric(14, 2), nullable=True)
    po_number = Column(String(64), nullable=True)
    import_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Follow / last seen, a person's mail-AI settings and use log, notification preferences
# ---------------------------------------------------------------------------

class ItemFollowDB(Base):
    """A person follows one item (a job, an order, an RFQ or a board row). One person's follows never affect another's."""

    __tablename__ = "item_follows"
    __table_args__ = (Index("ux_item_follows", "user_email", "item_kind", "item_ref", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String(320), nullable=False)
    item_kind = Column(String(24), nullable=False)
    item_ref = Column(String(120), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ItemSeenDB(Base):
    """When a person last looked at an item: what changed after that is "changed since you looked"."""

    __tablename__ = "item_seen"
    __table_args__ = (Index("ux_item_seen", "user_email", "item_kind", "item_ref", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String(320), nullable=False)
    item_kind = Column(String(24), nullable=False)
    item_ref = Column(String(120), nullable=False)
    seen_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MailAiSettingsDB(Base):
    """One person's choices about AI on their mail: everything off until they turn it on."""

    __tablename__ = "mail_ai_settings"

    user_email = Column(String(320), primary_key=True)
    ai_opt_in = Column(Boolean, nullable=False, default=False)
    auto_draft = Column(Boolean, nullable=False, default=False)
    style_note = Column(String(500), nullable=False, default="")
    exclude_senders = Column(JSON, nullable=False, default=list)
    exclude_words = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class MailAiUseDB(Base):
    """Who used the AI on which mail, when, with which model and what came of it. Never the message text and never the prompt."""

    __tablename__ = "mail_ai_use"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_email = Column(String(320), nullable=False, index=True)
    at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    mail_id = Column(Integer, nullable=True)
    model = Column(String(80), nullable=True)
    outcome = Column(String(40), nullable=False)


class NotificationPrefsDB(Base):
    """One person's notification choices (channel per alert group, quiet hours, instant or digest) kept as one small document."""

    __tablename__ = "notification_prefs"

    user_email = Column(String(320), primary_key=True)
    prefs = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Lead engine and reply templates
# ---------------------------------------------------------------------------

class LeadAccountDB(Base):
    __tablename__ = "lead_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company = Column(String(256), nullable=False, index=True)
    site = Column(String(256), nullable=True)
    industry = Column(String(120), nullable=True)
    location = Column(String(200), nullable=True)
    domain = Column(String(200), nullable=True, index=True)
    bottlenecks = Column(JSON, nullable=False, default=list)
    estimated_annual_spend = Column(Float, nullable=True)
    source = Column(String(24), nullable=False, default="manual")
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LeadContactDB(Base):
    __tablename__ = "lead_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=True, index=True)
    name = Column(String(256), nullable=False)
    title = Column(String(200), nullable=True)
    email = Column(String(320), nullable=True, index=True)
    phone = Column(String(60), nullable=True)
    source = Column(String(24), nullable=False, default="manual")
    consent_note = Column(String(300), nullable=True)
    bad_address = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LeadDB(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=True, index=True)
    contact_id = Column(Integer, nullable=True, index=True)
    status = Column(String(24), nullable=False, default="Found", index=True)
    source = Column(String(24), nullable=False, default="manual")
    source_detail = Column(String(300), nullable=True)
    reason = Column(Text, nullable=True)
    score = Column(Integer, nullable=True)
    score_parts = Column(JSON, nullable=True)
    owner = Column(String(320), nullable=True)
    notes = Column(Text, nullable=True)
    next_touch_at = Column(DateTime(timezone=True), nullable=True)
    last_touch_at = Column(DateTime(timezone=True), nullable=True)
    sequence_name = Column(String(60), nullable=True)
    sequence_step = Column(Integer, nullable=False, default=0)
    sequence_paused = Column(Boolean, nullable=False, default=False)
    monday_board_id = Column(String(32), nullable=True)
    monday_item_id = Column(String(32), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class LeadOutreachDB(Base):
    """What was drafted for or heard from a lead (never the message text of a reply)."""

    __tablename__ = "lead_outreach"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    template = Column(String(40), nullable=True)
    step = Column(Integer, nullable=True)
    action_id = Column(String(64), nullable=True)
    mail_id = Column(Integer, nullable=True)
    note = Column(String(500), nullable=True)
    by = Column(String(320), nullable=True)
    at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LeadDoNotContactDB(Base):
    __tablename__ = "lead_do_not_contact"
    __table_args__ = (Index("ux_lead_dnc", "kind", "value", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(8), nullable=False)
    value = Column(String(320), nullable=False)
    reason = Column(String(300), nullable=True)
    source = Column(String(24), nullable=False, default="manual")
    added_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LeadSettingsDB(Base):
    __tablename__ = "lead_settings"

    id = Column(Integer, primary_key=True)
    settings = Column(JSON, nullable=False, default=dict)
    updated_by = Column(String(320), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class CustomerAliasDB(Base):
    __tablename__ = "customer_aliases"
    __table_args__ = (Index("ux_customer_alias", "alias", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    alias = Column(String(256), nullable=False)
    canonical = Column(String(256), nullable=False)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class MailTemplateDB(Base):
    __tablename__ = "mail_templates"

    key = Column(String(60), primary_key=True)
    title = Column(String(200), nullable=False)
    subject = Column(String(300), nullable=False)
    body = Column(Text, nullable=False)
    situation = Column(String(40), nullable=True)
    updated_by = Column(String(320), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Organizing data beside the records: families, spellings, tags, vendor contacts, owners, pinned views
# ---------------------------------------------------------------------------

class CustomerFamilyDB(Base):
    __tablename__ = "customer_families"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company = Column(String(256), nullable=False, index=True)
    plant = Column(String(256), nullable=True)
    family = Column(String(256), nullable=False, index=True)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AliasTargetDB(Base):
    __tablename__ = "alias_targets"
    __table_args__ = (Index("ux_alias_targets_norm", "alias_norm", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    alias_norm = Column(String(256), nullable=False)
    alias_text = Column(String(256), nullable=False)
    family_id = Column(Integer, nullable=False, index=True)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RecordTagDB(Base):
    __tablename__ = "record_tags"
    __table_args__ = (Index("ux_record_tags", "item_kind", "item_ref", "tag", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_kind = Column(String(24), nullable=False)
    item_ref = Column(String(120), nullable=False)
    tag = Column(String(40), nullable=False, index=True)
    added_by = Column(String(320), nullable=True)
    added_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class VendorContactDB(Base):
    __tablename__ = "vendor_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    company = Column(String(256), nullable=True, index=True)
    country = Column(String(80), nullable=True)
    email = Column(String(320), nullable=True)
    phone = Column(String(60), nullable=True)
    notes = Column(String(500), nullable=True)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RecordOwnerDB(Base):
    __tablename__ = "record_owners"
    __table_args__ = (Index("ux_record_owners", "item_kind", "item_ref", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_kind = Column(String(24), nullable=False)
    item_ref = Column(String(120), nullable=False)
    owner = Column(String(320), nullable=True)
    ball_with = Column(String(80), nullable=True)
    note = Column(String(300), nullable=True)
    updated_by = Column(String(320), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ViewPinDB(Base):
    __tablename__ = "view_pins"
    __table_args__ = (Index("ux_view_pins", "view_id", "user_email", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    view_id = Column(String(40), nullable=False)
    user_email = Column(String(320), nullable=False)
    pinned_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RefreshStateDB(Base):
    """The latest refresh token of each person (by its id, never the token), the one before it for a short grace window, and the moment before which every session of theirs is dead."""

    __tablename__ = "refresh_state"

    user_email = Column(String(320), primary_key=True)
    latest_jti = Column(String(40), nullable=True)
    latest_exp = Column(Float, nullable=True)
    prev_jti = Column(String(40), nullable=True)
    prev_superseded_at = Column(Float, nullable=True)
    revoked_before = Column(Float, nullable=False, default=0.0)
    updated_at = Column(Float, nullable=True)


# ---------------------------------------------------------------------------
# Verified workflows: vendor payments, engineer gates, redaction, landed cost, shipment pipeline
# ---------------------------------------------------------------------------

class PaymentScheduleDB(Base):
    """How one vendor PO is paid: tranches that add up to exactly 100.00 percent."""

    __tablename__ = "payment_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    po_id = Column(Integer, nullable=False, index=True)
    job_id = Column(Integer, nullable=True, index=True)
    vendor = Column(String(256), nullable=True)
    currency = Column(String(3), nullable=True)
    total = Column(Numeric(14, 2), nullable=False, default=0)
    terms_code = Column(String(64), nullable=True)                           # 30/50/20, 30/70, 50/50 or custom
    status = Column(String(16), nullable=False, default="ACTIVE")
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class PaymentTrancheDB(Base):
    __tablename__ = "payment_tranches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, nullable=False, index=True)
    seq = Column(Integer, nullable=False, default=1)
    label = Column(String(80), nullable=True)
    percent = Column(Numeric(6, 2), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    trigger = Column(String(24), nullable=False)                             # PO_ISSUED, PROFORMA, GATE2_APPROVED, DELIVERY_CONFIRMED, CUSTOM
    due_on = Column(String(10), nullable=True)
    status = Column(String(12), nullable=False, default="PENDING")           # PENDING, DUE, PAID
    triggered_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)


class VendorPaymentDB(Base):
    """One payment entry. Idempotent: the same idem_key is one payment."""

    __tablename__ = "vendor_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tranche_id = Column(Integer, nullable=False, index=True)
    idem_key = Column(String(80), nullable=False, unique=True)
    paid_on = Column(String(10), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=True)
    method = Column(String(24), nullable=True)
    reference = Column(String(120), nullable=True)
    wire_file_id = Column(Integer, nullable=True)
    proforma_file_id = Column(Integer, nullable=True)
    recorded_by = Column(String(320), nullable=True)
    recorded_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    ledger_written_at = Column(DateTime(timezone=True), nullable=True)
    monday_staged_at = Column(DateTime(timezone=True), nullable=True)
    remittance_action_id = Column(String(36), nullable=True)
    voided_at = Column(DateTime(timezone=True), nullable=True)


class EngineerGateDB(Base):
    """Gate 1 (vendor shop drawing) or Gate 2 (measurement photos): approved by an engineer, or overridden by a manager with a written reason."""

    __tablename__ = "engineer_gates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=False, index=True)
    po_id = Column(Integer, nullable=True, index=True)
    gate = Column(Integer, nullable=False)                                   # 1 or 2
    file_ids = Column(JSON, nullable=False, default=list)
    status = Column(String(12), nullable=False, default="PENDING")           # PENDING, APPROVED, REJECTED, OVERRIDDEN, REVOKED
    requested_by = Column(String(320), nullable=True)
    requested_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    decided_by = Column(String(320), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(String(500), nullable=True)
    override_by = Column(String(320), nullable=True)
    override_reason = Column(String(500), nullable=True)
    override_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(320), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(500), nullable=True)


class RedactedArtifactDB(Base):
    __tablename__ = "redacted_artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=False, index=True)
    source_file_id = Column(Integer, nullable=False, index=True)
    source_sha256 = Column(String(64), nullable=False)
    redacted_file_id = Column(Integer, nullable=True)
    redacted_sha256 = Column(String(64), nullable=True)
    method = Column(String(12), nullable=False, default="AUTO")              # AUTO or ATTESTED
    findings = Column(JSON, nullable=False, default=list)
    status = Column(String(12), nullable=False, default="PENDING")           # PENDING, CLEAN, VERIFIED, BLOCKED, VOID
    verified_by = Column(String(320), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    attested_by = Column(String(320), nullable=True)
    attested_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class LandedCostSheetDB(Base):
    __tablename__ = "landed_cost_sheets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, nullable=True, index=True)
    rfq_id = Column(Integer, nullable=True, index=True)
    vendor_quote_ref = Column(String(80), nullable=True)
    currency = Column(String(3), nullable=True)
    lines = Column(JSON, nullable=False, default=list)
    freight = Column(Numeric(14, 2), nullable=True)
    tariff_total = Column(Numeric(14, 2), nullable=True)
    tariff_unknown = Column(Boolean, nullable=False, default=False)
    landed_total = Column(Numeric(14, 2), nullable=True)
    tiers = Column(JSON, nullable=False, default=list)
    picked_tier = Column(String(40), nullable=True)
    created_by = Column(String(320), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class TariffRateDB(Base):
    __tablename__ = "tariff_rates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(120), nullable=False, index=True)
    hs_code = Column(String(16), nullable=True)
    country = Column(String(2), nullable=True)
    rate_percent = Column(Numeric(6, 2), nullable=False)
    effective_from = Column(String(10), nullable=True)
    note = Column(String(300), nullable=True)
    updated_by = Column(String(320), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ShipmentPipelineDB(Base):
    """Fulfilment pipeline fields of a shipment, kept beside it (the shipments table is not changed)."""

    __tablename__ = "shipment_pipeline"

    shipment_id = Column(Integer, primary_key=True)
    pipeline = Column(String(32), nullable=False)                            # US_OFFICE_PARCEL, US_MOUNTAIN_MOVERS, CN_FREIGHT_FORWARDER
    tracking_no = Column(String(64), nullable=True)
    carrier = Column(String(64), nullable=True)
    bol_file_id = Column(Integer, nullable=True)
    quote_options = Column(JSON, nullable=False, default=list)
    forwarder_digest_at = Column(DateTime(timezone=True), nullable=True)
    freight_cost_actual = Column(Numeric(14, 2), nullable=True)
    freight_cost_quoted = Column(Numeric(14, 2), nullable=True)
    freight_populated_at = Column(DateTime(timezone=True), nullable=True)
    variance_flag = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SageImportRowDB(Base):
    """What one Sage import did to each mirror row (created, updated, closed) and the values before, so exactly that import can be undone; UNDONE is the marker that it was."""

    __tablename__ = "sage_import_rows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    import_id = Column(Integer, nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    row_key = Column(String(200), nullable=False)
    action = Column(String(8), nullable=False)                               # CREATED, UPDATED, CLOSED, UNDONE
    before = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

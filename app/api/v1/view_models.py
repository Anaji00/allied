"""Response models for the screens the frontend builds typed clients for. Every field is named and typed; each is optional where a role or a quiet system leaves it out, and extra fields are allowed so
a service can add a field without breaking a route. Routes using these send `response_model_exclude_unset=True`, so what goes over the wire is exactly what the service returned."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class V(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---- ball
class BallState(V):
    item_type: str
    item_ref: str
    label: Optional[str] = None
    derived: Optional[bool] = None
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    ball_with: Optional[str] = None
    ball_label: Optional[str] = None
    ball_person: Optional[str] = None
    ball_name: Optional[str] = None
    note: Optional[str] = None
    due_at: Optional[str] = None
    overdue: Optional[bool] = None
    since: Optional[str] = None
    days_waiting: Optional[float] = None
    level: Optional[int] = None
    status: Optional[str] = None
    resolved: Optional[bool] = None
    history: List[Dict[str, Any]] = []


class BallKind(V):
    kind: str
    label: Optional[str] = None
    count: int
    late: int
    oldest_days: float


class BallHolder(V):
    name: str
    kind: Optional[str] = None
    count: int
    late: int
    oldest_days: float
    waiting_days: Optional[int] = None


class BallRules(V):
    nudge_days: int
    escalate_days: int
    leadership_days: int


class BallSummary(V):
    open: int
    late: int
    escalated: int
    by_kind: List[BallKind]
    by_holder: List[BallHolder]
    oldest: List[BallState]
    rules: BallRules
    late_means: Optional[str] = None


# ---- packets
class PacketDocument(V):
    id: int
    job_id: Optional[int] = None
    kind: Optional[str] = None
    label: Optional[str] = None
    pages: Optional[str] = None
    page_from: Optional[int] = None
    page_to: Optional[int] = None
    fields: Dict[str, Any] = {}
    warnings: List[str] = []
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    source: Optional[str] = None
    packet: Optional[str] = None
    at: Optional[str] = None


class PacketRead(V):
    """The result of reading a scanned packet."""
    packet: str
    already_read: bool
    job_id: Optional[int] = None
    documents: List[PacketDocument] = []


class WaitingPacket(V):
    packet: str
    source: Optional[str] = None
    documents: List[PacketDocument] = []


class PacketAttached(V):
    job_id: int
    documents: List[PacketDocument] = []


# ---- AI
class ExplanationOut(V):
    explanation: str
    suggested_fix: str


class ExplainResponse(V):
    suggestion_id: Optional[Any] = None
    result: ExplanationOut


class ItemChoiceOut(V):
    item_id: str
    reason: str
    confidence: float


class ItemCandidate(V):
    item_id: str
    description: str = ""
    score: float


class SuggestItemResponse(V):
    suggestion_id: Optional[Any] = None
    result: ItemChoiceOut
    candidates: List[ItemCandidate] = []


# ---- job page
class PageAction(V):
    action: Optional[str] = None
    label: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    body: Optional[Dict[str, Any]] = None


class Change(V):
    at: Optional[str] = None
    who: Optional[str] = None
    sentence: str
    source: Optional[str] = None


class SinceOut(V):
    kind: str
    ref: str
    title: Optional[str] = None
    since: Optional[str] = None
    count: int
    changes: List[Change] = []
    text: Optional[str] = None


class JobStage(V):
    stage: str
    stage_word: Optional[str] = None
    done: Optional[bool] = None
    entered_at: Optional[str] = None
    by: Optional[str] = None
    blocked_reason: Optional[str] = None


class PagePO(V):
    id: int
    po_number: Optional[str] = None
    vendor: Optional[str] = None
    status: Optional[str] = None
    pdf_url: Optional[str] = None


class JobCore(V):
    id: int
    ref: str
    title: Optional[str] = None
    customer: Optional[str] = None
    stage: str
    stage_word: Optional[str] = None
    done: List[Any] = []
    updated_at: Optional[str] = None
    stages: List[JobStage] = []
    rfq: Optional[Dict[str, Any]] = None
    thread: Optional[Dict[str, Any]] = None
    design: Optional[Dict[str, Any]] = None
    sales_order: Optional[Any] = None
    # staff only (a viewer's page leaves these out)
    waiting: Optional[List[Dict[str, Any]]] = None
    next_steps: Optional[List[Dict[str, Any]]] = None
    checks: Optional[List[Any]] = None
    ball: Optional[BallState] = None
    research: Optional[List[Dict[str, Any]]] = None
    purchase_orders: Optional[List[PagePO]] = None
    shipments: Optional[List[Dict[str, Any]]] = None
    drafts: Optional[List[Dict[str, Any]]] = None
    documents: Optional[Dict[str, Any]] = None


class ThreadComment(V):
    id: int
    item_type: Optional[str] = None
    item_ref: Optional[str] = None
    parent_id: Optional[int] = None
    author_email: Optional[str] = None
    author_name: Optional[str] = None
    body: str
    removed: Optional[bool] = None
    mentions: List[Any] = []
    created_at: Optional[str] = None
    edited_at: Optional[str] = None
    mine: Optional[bool] = None
    files: List[Any] = []
    replies: List[Any] = []
    kind: Optional[str] = None


class PrepPO(V):
    id: int
    po_number: Optional[str] = None
    vendor: Optional[str] = None
    status: Optional[str] = None
    raw_status: Optional[str] = None
    total: Optional[float] = None
    sent_at: Optional[str] = None
    good_thru: Optional[str] = None
    revisions: List[Dict[str, Any]] = []
    drawing_mismatch_lines: List[Any] = []


class PrepOut(V):
    kind: str
    ref: str
    title: Optional[str] = None
    emails: List[Dict[str, Any]] = []
    pos: List[PrepPO] = []
    rfqs: List[Dict[str, Any]] = []
    shipments: List[Dict[str, Any]] = []
    jobs: List[Dict[str, Any]] = []
    ball: Optional[BallState] = None
    comments: List[Dict[str, Any]] = []
    customer: Optional[str] = None
    stage: Optional[str] = None
    next_steps: List[Any] = []
    text: Optional[str] = None


class PageActions(V):
    kind: Optional[str] = None
    ref: Optional[str] = None
    title: Optional[str] = None
    actions: List[PageAction] = []
    link: Optional[str] = None


class MondayLinkState(V):
    state: str
    monday_item_id: Optional[str] = None
    monday_board_id: Optional[str] = None
    monday_item_name: Optional[str] = None
    actions: List[Any] = []


class JobPage(V):
    job: JobCore
    thread: List[ThreadComment] = []
    since: Optional[SinceOut] = None
    prep: Optional[PrepOut] = None
    actions: Optional[PageActions] = None
    errors: List[Any] = []
    files: List[Any] = []
    monday: Optional[MondayLinkState] = None
    staff: bool


# ---- conversations
class WaitsOn(V):
    kind: Optional[str] = None
    name: Optional[str] = None
    note: Optional[str] = None
    since: Optional[str] = None


class ConversationThread(V):
    key: str
    item_type: str
    item_ref: str
    title: str
    link: str
    job_ref: Optional[str] = None
    last_at: Optional[str] = None
    last_by: str = ""
    last_sentence: str = ""
    messages: int
    unread: int
    waits_on: Optional[WaitsOn] = None
    drafts_waiting: int


class Conversations(V):
    total: int
    unread_total: int
    unread_threads: int
    items: List[ConversationThread]


# ---- Sage export overview
class ReadyToSend(V):
    sales_orders: int
    purchase_orders: int
    purchase_lines: int = 0
    sales_totals: Dict[str, float] = {}        # per currency, e.g. {"USD": 9099.23, "CAD": 1200.0}


class SageOverview(V):
    ready_to_send: ReadyToSend
    last_export: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = []
    unmark: List[Dict[str, Any]] = []
    text: Optional[str] = None
    how: Optional[Dict[str, Any]] = None


# ---- job automation
class AutomationEntry(V):
    at: Optional[str] = None
    sentence: str
    source: Optional[str] = None


class AutomationLog(V):
    job: str
    count: int
    items: List[AutomationEntry] = []
    text: Optional[str] = None


# ---- my day, brief, chase
class DayAction(V):
    label: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    body: Optional[Dict[str, Any]] = None


class DayItem(V):
    id: str
    kind: str
    sentence: str
    reason: Optional[str] = None
    primary_action: Optional[DayAction] = None
    other_actions: List[DayAction] = []
    link: Optional[str] = None
    age: Optional[str] = None
    age_days: Optional[int] = None
    severity: Optional[str] = None


class MyDay(V):
    total: int
    need_action: int
    badge: int
    counts: Dict[str, int] = {}
    items: List[DayItem] = []


class Brief(V):
    headline: str
    date: Optional[str] = None
    timezone: Optional[str] = None
    count: int
    need_action: Optional[int] = None
    items: List[Dict[str, Any]] = []


class ChaseItem(V):
    id: str
    kind: str
    who: Optional[str] = None
    role: Optional[str] = None
    owes: Optional[str] = None
    what: Optional[str] = None
    days_waiting: Optional[int] = None
    age: Optional[str] = None
    since: Optional[str] = None
    link: Optional[str] = None
    overdue: Optional[bool] = None
    draft_id: Optional[str] = None
    send: Optional[Dict[str, Any]] = None
    dismiss: Optional[Dict[str, Any]] = None


class Chase(V):
    total: int
    with_draft: int
    items: List[ChaseItem] = []


# ---- bills checked, automation scorecard, my counts, my notification choices
class BillHistoryRow(V):
    reconciliation_id: str
    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    po_number: Optional[str] = None
    reconciliation_status: Optional[str] = None
    status_word: Optional[str] = None
    authorized_po_total: Optional[Any] = None
    actual_billed_total: Optional[Any] = None
    cost_variance_amount: Optional[Any] = None
    cost_variance_pct: Optional[float] = None
    discrepancies: List[str] = []
    suggested_action: Optional[str] = None
    reconciled_at: Optional[Any] = None


class AutomationScore(V):
    name: str
    mode: Optional[str] = None
    drafted: int
    sent: int
    cancelled: int
    lapsed: int
    failed: int
    waiting: int
    send_rate: Optional[float] = None
    advice: str


class AlertScore(V):
    kind: str
    raised: int
    handled: int
    cleared: int
    ignored: int
    open: int
    handled_rate: Optional[float] = None
    advice: Optional[str] = None


class Scorecard(V):
    days: int
    automations: List[AutomationScore] = []
    alerts: List[AlertScore] = []


class MyCounts(V):
    alerts_unread: int
    mail_needing_action: int
    drafts_waiting: int
    email_drafts_waiting: int = 0
    total: int
    need_action: int


class NotificationPrefs(V):
    groups: Dict[str, List[str]] = {}
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None
    delivery: str = "instant"
    digest_time: Optional[str] = None


# ---- the lead engine
class ScorePart(V):
    label: str
    points: int
    max: Optional[int] = None


class LeadSequence(V):
    name: Optional[str] = None
    step: int = 0
    paused: bool = False


class Lead(V):
    id: int
    account_id: Optional[int] = None
    contact_id: Optional[int] = None
    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: str
    source_detail: Optional[str] = None
    profile_url: Optional[str] = None
    status: str
    owner: Optional[str] = None
    reason: Optional[str] = None
    score: Optional[int] = None
    score_parts: List[ScorePart] = []
    next_touch_at: Optional[str] = None
    last_touch_at: Optional[str] = None
    notes: Optional[str] = None
    estimated_annual_spend: Optional[float] = None
    do_not_contact: bool = False
    bad_address: bool = False
    sequence: LeadSequence = LeadSequence()
    monday_item_id: Optional[str] = None
    created_at: Optional[str] = None
    duplicate: Optional[bool] = None


class LeadPage(V):
    items: List[Lead]
    total: int
    due_today: int


class LeadSummary(V):
    by_stage: Dict[str, int]
    due_today: int
    replied_week: int
    won: int
    lost: int
    win_rate: Optional[float] = None
    pipeline_value: Optional[float] = None
    by_source: Dict[str, Dict[str, int]] = {}


class LeadSuggestion(V):
    id: str
    kind: str
    account: str
    sentence: str
    suggested_template: str
    spend: Optional[float] = None
    email: Optional[str] = None
    already_a_lead: bool = False


class DoNotContactRow(V):
    id: int
    kind: str
    value: str
    reason: Optional[str] = None
    source: str
    added_by: Optional[str] = None
    created_at: str


class LeadOutreachRow(V):
    id: int
    kind: str
    template: Optional[str] = None
    step: Optional[int] = None
    action_id: Optional[str] = None
    mail_id: Optional[int] = None
    note: Optional[str] = None
    by: Optional[str] = None
    at: str


class LeadDraft(V):
    lead_id: int
    template: str
    to: str
    subject: str
    body: str
    action_id: str
    next_touch_at: Optional[str] = None


class QueueState(V):
    state: str
    reasons: List[str] = []


# ---- shipments (Logistics screen)
class ShipmentEventOut(V):
    at: Optional[str] = None
    status: Optional[str] = None
    status_word: Optional[str] = None
    eta: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None


class ShipmentRow(V):
    id: int
    project_item_id: Optional[str] = None
    forwarder: Optional[str] = None
    forwarder_label: Optional[str] = None
    mode: Optional[str] = None
    reference: Optional[str] = None
    container_no: Optional[str] = None
    awb: Optional[str] = None
    carrier: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    etd: Optional[str] = None
    eta_original: Optional[str] = None
    eta_current: Optional[str] = None
    free_time_ends: Optional[str] = None
    status: Optional[str] = None
    status_word: Optional[str] = None
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    last_update_at: Optional[str] = None
    created_at: Optional[str] = None
    quiet_days: Optional[int] = None
    quiet_flag: str = ""
    flags: Optional[List[str]] = None
    events: Optional[List[ShipmentEventOut]] = None


class ShipmentList(V):
    statuses: Optional[Any] = None
    shipments: List[ShipmentRow] = []

"""Projects and milestones (from monday), design-doc submissions, shipments, vendor jobs, RFQs, alerts."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.core.database import SessionLocal
from app.core.security import require_admin, require_manager, require_operator, require_viewer
from app.models.db_models import StaffContactDB
from app.services.coordination import notify as alerts
from app.services.coordination import projects, rfqs, schedule, shipments, vendors

projects_router = APIRouter()
shipments_router = APIRouter()
alerts_router = APIRouter()
vendors_router = APIRouter()
rfqs_router = APIRouter()
autopilot_router = APIRouter()
boards_router = APIRouter()


def _fail(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise exc


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@projects_router.get("", summary="Projects and milestones from monday, with where each stands")
def project_overview(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    data = projects.overview()
    data["last_sync"] = schedule.coordination_worker.last.get("sync")
    return data


@projects_router.post("/sync", summary="Read the monday projects board now")
def sync_now(_user: dict = Depends(require_operator)):
    try:
        return projects.sync_projects()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not read monday: {exc}")


_MAX_FILES = 20


def _read_capped(f: UploadFile) -> bytes:
    """Reads one upload, stopping just past the size limit instead of holding all of it."""
    limit = settings.SUBMISSION_MAX_MB * 1024 * 1024
    data = f.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"{f.filename} is larger than {settings.SUBMISSION_MAX_MB} MB.")
    return data


# A plain def: FastAPI runs it in a worker thread, so file writes and the
# monday call do not hold up every other request.
@projects_router.post("/milestones/{item_id}/submissions", summary="Submit design documents for a milestone")
def submit_documents(
    item_id: str,
    files: List[UploadFile] = File(...),
    note: str = Form(""),
    user: dict = Depends(require_operator),
):
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Attach at most {_MAX_FILES} files at once.")
    payload = [(f.filename or "document", _read_capped(f)) for f in files]
    try:
        return projects.submit(item_id, payload, note, user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


class ReviewBody(BaseModel):
    approve: bool
    note: str = ""


@projects_router.post("/submissions/{submission_id}/review", summary="Approve, or send back with changes")
def review_submission(submission_id: int, body: ReviewBody, user: dict = Depends(require_operator)):
    try:
        return projects.review(submission_id, body.approve, body.note, user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@projects_router.get("/submissions/{submission_id}/files/{index}", summary="Download a submitted document")
def download(submission_id: int, index: int, _user: dict = Depends(require_viewer)):
    try:
        name, path = projects.submission_file(submission_id, index)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    return FileResponse(path, filename=name)


# ---------------------------------------------------------------------------
# Shipments
# ---------------------------------------------------------------------------


@shipments_router.get("", summary="Shipments, with plain-language flags")
def list_shipments(include_finished: bool = True, _user: dict = Depends(require_viewer)):
    return {"statuses": shipments.STATUSES, "shipments": shipments.list_shipments(include_finished)}


class ShipmentIn(BaseModel):
    forwarder: Optional[str] = None
    mode: Literal["SEA", "AIR", "TRUCK"] = "SEA"
    reference: Optional[str] = None
    container_no: Optional[str] = None
    awb: Optional[str] = None
    carrier: Optional[str] = None
    origin: Optional[str] = None
    destination: Optional[str] = None
    etd: Optional[str] = Field(None, description="YYYY-MM-DD")
    eta: Optional[str] = Field(None, description="YYYY-MM-DD")
    free_time_ends: Optional[str] = Field(None, description="Last free day at the port, YYYY-MM-DD")
    status: str = "BOOKED"
    owner_email: Optional[str] = None
    project_item_id: Optional[str] = None


@shipments_router.post("", summary="Add a shipment")
def add_shipment(body: ShipmentIn, user: dict = Depends(require_operator)):
    try:
        return shipments.create(body.model_dump(), user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


class ShipmentUpdate(BaseModel):
    status: Optional[str] = None
    eta: Optional[str] = None
    etd: Optional[str] = None
    free_time_ends: Optional[str] = None
    note: Optional[str] = None
    owner_email: Optional[str] = None
    forwarder: Optional[str] = None


@shipments_router.patch("/{shipment_id}", summary="Record a status or date change")
def update_shipment(shipment_id: int, body: ShipmentUpdate, user: dict = Depends(require_operator)):
    try:
        return shipments.update(shipment_id, body.model_dump(exclude_none=True), user.get("email", "unknown"))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


class ForwarderEmail(BaseModel):
    sender: str = ""
    subject: str = ""
    body: str = Field(..., min_length=1)


@shipments_router.post("/forwarder-email", summary="Read a forwarder's status email into its shipment")
def forwarder_email(body: ForwarderEmail, user: dict = Depends(require_operator)):
    return shipments.ingest_forwarder_email(body.sender, body.subject, body.body, actor=user.get("email", "system"))


# ---------------------------------------------------------------------------
# Vendor jobs
# ---------------------------------------------------------------------------


@vendors_router.get("", summary="Vendor jobs, whether each still lets its project finish on time, and vendor scores")
def vendor_overview(include_shipped: bool = False, _user: dict = Depends(require_viewer)):
    data = vendors.overview(include_shipped=include_shipped)
    data["last_sync"] = schedule.coordination_worker.last.get("vendor_sync")
    return data


@vendors_router.post("/sync", summary="Read the monday vendor board now")
def vendor_sync(_user: dict = Depends(require_operator)):
    try:
        return vendors.sync_vendor_board()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Could not read monday: {exc}")


class VendorJobIn(BaseModel):
    vendor: Optional[str] = None
    description: Optional[str] = None
    project_item_id: Optional[str] = None
    po_number: Optional[str] = None
    owner_email: Optional[str] = None
    status: Optional[str] = None
    mode: Optional[Literal["SEA", "AIR", "TRUCK"]] = None
    ordered_on: Optional[str] = Field(None, description="PO placed, YYYY-MM-DD")
    requested_on: Optional[str] = Field(None, description="Ship date Allied asked for")
    acknowledged_on: Optional[str] = Field(None, description="Vendor confirmed the PO")
    quoted_lead_days: Optional[int] = Field(None, ge=0, le=730)
    follow_up_days: Optional[int] = Field(None, ge=0, le=365,
                                          description="Days after the PO before the vendor is followed up; 0 = the usual wait")
    promised_on: Optional[str] = Field(None, description="Vendor's promised ship date")
    shipped_on: Optional[str] = Field(None, description="Actually shipped")
    need_by: Optional[str] = Field(None, description="Override the ship-by date worked out from the project")
    note: Optional[str] = None


@vendors_router.post("", summary="Add a vendor job")
def add_vendor_job(body: VendorJobIn, user: dict = Depends(require_operator)):
    if not (body.vendor or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Say which vendor.")
    try:
        return vendors.create(body.model_dump(exclude_none=True), user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@vendors_router.get("/link-suggestions", summary="Orders with no drawing linked, and the drawing jobs each likely belongs to")
def vendor_link_suggestions(_user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return vendors.link_suggestions()


@vendors_router.patch("/{job_id}", summary="Record a vendor date or detail")
def update_vendor_job(job_id: int, body: VendorJobIn, user: dict = Depends(require_operator)):
    try:
        return vendors.update(job_id, body.model_dump(exclude_none=True), user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


# ---------------------------------------------------------------------------
# RFQs
# ---------------------------------------------------------------------------


@rfqs_router.get("", summary="Customer RFQs with their quote deadlines")
def rfq_list(open_only: bool = True, mine: bool = False, user: dict = Depends(require_viewer)):
    return {"rfqs": rfqs.list_rfqs(open_only, user.get("email") if mine else None),
            "metrics": rfqs.metrics(), "statuses": rfqs.STATUSES,
            "targets": {"ack_hours": settings.RFQ_ACK_BUSINESS_HOURS, "simple_days": settings.RFQ_QUOTE_DAYS_SIMPLE,
                        "standard_days": settings.RFQ_QUOTE_DAYS_STANDARD, "complex_days": settings.RFQ_QUOTE_DAYS_COMPLEX}}


# The customer's own request form. It needs no login (a customer has none), so it is
# held to a shared token, a honeypot field and a per-address hourly limit.
_form_hits: Dict[str, List[float]] = {}


class RfqForm(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    name: str = Field("", max_length=120)
    email: str = Field(..., min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str = Field("", max_length=40)
    subject: str = Field(..., min_length=1, max_length=300)
    details: str = Field("", max_length=8000)
    quantity: str = Field("", max_length=100)
    needed_by: Optional[str] = Field(None, max_length=40)
    website: str = Field("", max_length=200, description="Leave empty (a trap for bots)")


def _form_open(token: Optional[str]) -> None:
    import hmac

    if not settings.RFQ_FORM_TOKEN:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The request form is switched off.")
    if not token or not hmac.compare_digest(token.encode(), settings.RFQ_FORM_TOKEN.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This form link is not valid.")


def _client(request: Request) -> str:
    """The customer's address. Requests come through the app's own proxies (Caddy for https,
    then the frontend), each adding the address it saw to the end of X-Forwarded-For. Reading
    from the end and stepping past our proxies (Docker's private network, loopback) gives the
    first address a proxy of ours saw; anything further left was written by the sender and
    could be faked."""
    import ipaddress

    hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ip = ipaddress.ip_address(hop)
        except ValueError:
            return hop[:64]
        if not (ip.is_loopback or ip in ipaddress.ip_network("172.16.0.0/12")):
            return hop[:64]
    return (hops[0] if hops else (request.client.host if request.client else "unknown"))[:64]


@rfqs_router.get("/form", summary="The customer request form's fields (send the form token as X-Form-Token)")
def rfq_form_fields(request: Request) -> Dict[str, Any]:
    _form_open(request.headers.get("x-form-token"))
    return {"fields": [{"name": n, "required": f.is_required(), "max_length": next(
        (m.max_length for m in f.metadata if hasattr(m, "max_length")), None)}
        for n, f in RfqForm.model_fields.items() if n != "website"]}


@rfqs_router.post("/form", summary="A customer sends a request for quote", status_code=status.HTTP_201_CREATED)
def rfq_form_submit(body: RfqForm, request: Request) -> Dict[str, Any]:
    import time as _time

    # The token comes in a header only: a ?token= in the URL would be written to every access log.
    _form_open(request.headers.get("x-form-token"))
    if body.website:  # a person never fills the hidden field; answer as if it worked
        return {"received": True, "reference": None}
    who = _client(request)
    now = _time.monotonic()
    for key in [k for k, v in _form_hits.items() if not v or now - v[-1] >= 3600]:
        del _form_hits[key]  # an address quiet for an hour is forgotten
    for key, cap in ((who, settings.RFQ_FORM_PER_HOUR), ("*", settings.RFQ_FORM_PER_HOUR * 10)):
        hits = [t for t in _form_hits.get(key, []) if now - t < 3600]
        if len(hits) >= cap:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests; please email us instead.")
        _form_hits[key] = hits
    for key in (who, "*"):
        _form_hits[key].append(now)
    contact = " ".join(x for x in (body.name, body.phone) if x)
    lines = [body.details.strip(), f"Quantity: {body.quantity}" if body.quantity else "",
             f"Needed by: {body.needed_by}" if body.needed_by else "", f"Contact: {contact}" if contact else ""]
    try:
        out = rfqs.create({"customer": body.company, "contact_email": body.email, "subject": body.subject,
                           "summary": "\n".join(x for x in lines if x)[:4000]},
                          {"email": "rfq-form@allied", "role": "SYSTEM"}, source="form")
    except ValueError as exc:
        _fail(exc)
    return {"received": True, "reference": out["number"]}


@rfqs_router.get("/{rfq_id}", summary="One RFQ with its history")
def rfq_detail(rfq_id: str, _user: dict = Depends(require_viewer)):
    try:
        return rfqs.get(rfq_id)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


class RfqIn(BaseModel):
    customer: str = ""
    contact_email: Optional[str] = None
    subject: str = ""
    summary: Optional[str] = None
    complexity: Literal["SIMPLE", "STANDARD", "COMPLEX"] = "STANDARD"
    owner_email: Optional[str] = None
    received_at: Optional[datetime] = None
    notes: Optional[str] = None


@rfqs_router.post("", summary="Log an RFQ")
def add_rfq(body: RfqIn, user: dict = Depends(require_operator)):
    try:
        return rfqs.create(body.model_dump(), user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


class RfqUpdate(BaseModel):
    status: Optional[str] = None
    reason: Optional[str] = None
    owner_email: Optional[str] = None
    vendor: Optional[str] = None
    complexity: Optional[Literal["SIMPLE", "STANDARD", "COMPLEX"]] = None
    quote_number: Optional[str] = None
    quote_amount: Optional[str] = None
    note: Optional[str] = None


@rfqs_router.patch("/{rfq_id}", summary="Move an RFQ along, assign it, or record the quote")
def update_rfq(rfq_id: str, body: RfqUpdate, user: dict = Depends(require_operator)):
    try:
        return rfqs.update(rfq_id, body.model_dump(exclude_none=True), user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@alerts_router.get("", summary="My alerts (managers can see everyone's)")
def my_alerts(
    open_only: bool = False,
    everyone: bool = False,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_viewer),
):
    see_all = everyone and user.get("role") in ("ADMIN", "MANAGER")
    items = alerts.inbox(None if see_all else user.get("email"), open_only, limit)
    return {"items": items, "open": sum(1 for n in items if not n["acknowledged_at"]), "channels": alerts.channel_status()}


@alerts_router.post("/{notification_id}/acknowledge", summary="Mark an alert as seen and handled")
def acknowledge(notification_id: int, user: dict = Depends(require_viewer)):
    result = alerts.acknowledge(notification_id, user.get("email", "unknown"),
                                any_alert=user.get("role") in ("ADMIN", "MANAGER"))
    if result == "missing":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such alert.")
    if result == "forbidden":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This alert was sent to someone else.")
    if result == "done":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already acknowledged.")
    return {"acknowledged": notification_id}


class ContactIn(BaseModel):
    email: str
    name: str
    role_tag: Literal["leadership", "engineer", "operations", "staff"] = "staff"
    phone: Optional[str] = Field(None, description="E.164, e.g. +12055550100")
    channels: List[Literal["email", "sms", "push"]] = ["email"]
    daily_summary: bool = False
    active: bool = True


@alerts_router.get("/contacts", summary="Who gets told, and how")
def list_contacts(_user: dict = Depends(require_manager)):
    return {"contacts": list(alerts.contacts().values()), "channels": alerts.channel_status()}


@alerts_router.put("/contacts", summary="Add or update a person's alert settings")
def save_contact(body: ContactIn, _user: dict = Depends(require_admin)):
    with SessionLocal() as db:
        email = body.email.strip().lower()
        row = db.get(StaffContactDB, email) or StaffContactDB(email=email)
        for k, v in body.model_dump().items():
            setattr(row, k, v)
        row.email = email
        db.merge(row)
        db.commit()
    alerts.forget_contacts()  # the change applies to the next alert, not 30 seconds later
    return alerts.contacts().get(email)


@alerts_router.post("/test", summary="Send myself a test alert on every channel I use")
def test_alert(severity: Literal["info", "warning", "critical"] = "warning", user: dict = Depends(require_viewer)):
    import time

    nid = alerts.notify(
        "test", f"test:{user.get('email')}:{time.time()}", "Test alert from Allied Operations",
        "If you can read this, alerts reach you. Nothing needs doing.", severity=severity,
        to=[user.get("email")], link="/alerts", wait=True,
    )
    mine = alerts.inbox(user.get("email"), limit=1) if nid else []
    return {"id": nid, "delivered": mine[0]["deliveries"] if mine else None}


@alerts_router.get("/summary", summary="Preview today's leadership summary")
def preview_summary(_user: dict = Depends(require_viewer)):
    return schedule.build_summary()


@alerts_router.post("/summary/send", summary="Send the leadership summary now")
def send_summary_now(_user: dict = Depends(require_manager)):
    return {"id": schedule.send_summary(force=True)}


@alerts_router.post("/check-now", summary="Run the behind-schedule checks now")
def check_now(_user: dict = Depends(require_operator)):
    return schedule.run_checks()


# ---------------------------------------------------------------------------
# Autopilot
# ---------------------------------------------------------------------------


@boards_router.get("", summary="The monday boards the app reads, and when each was last read")
def board_list(_user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.services.coordination import boards

    return boards.boards()


@boards_router.get("/{board_id}", summary="A monday board exactly as monday shows it, with the app's view of each row")
def board_view(board_id: str, hide_done: bool = Query(False, description="Leave out finished items"),
               _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import boards

    try:
        return boards.view(board_id, hide_done)
    except LookupError as exc:
        _fail(exc)


@boards_router.get("/{board_id}/activity", summary="What changed on a board (monday's activity log), newest first")
def board_activity(board_id: str, days: int = Query(7, ge=1, le=90), item_id: Optional[str] = None,
                   _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import activity

    try:
        return activity.feed(board_id, days, item_id)
    except LookupError as exc:
        _fail(exc)


@boards_router.get("/{board_id}/status-times", summary="How long items spend in each status, and who is sitting longest")
def board_status_times(board_id: str, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import activity, boards

    try:
        view = boards.view(board_id)
        return activity.status_times(board_id, view["status_column"], boards._done_labels(view["role"]))
    except LookupError as exc:
        _fail(exc)


@boards_router.get("/{board_id}/dashboard", summary="monday's battery, workload and calendar for a board")
def board_dashboard(board_id: str, days_ahead: int = Query(30, ge=1, le=365), _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import activity

    try:
        return activity.dashboard(board_id, days_ahead)
    except LookupError as exc:
        _fail(exc)


@boards_router.get("/{board_id}/timeline", summary="The board as Kanban lanes, Gantt bars and a calendar")
def board_timeline(board_id: str, hide_done: bool = True, start: Optional[str] = Query(None, max_length=10),
                   end: Optional[str] = Query(None, max_length=10), _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import activity

    try:
        return activity.timeline(board_id, hide_done, start, end)
    except LookupError as exc:
        _fail(exc)


@boards_router.get("/{board_id}/items/{item_id}/updates", summary="An item's comments and files, read live from monday")
def item_updates(board_id: str, item_id: str, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import activity
    from app.services.coordination.projects import _client

    client = _client()
    if not client.live:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="monday is in simulation; nothing is read.")
    try:
        return activity.updates(item_id, client)
    except LookupError as exc:
        _fail(exc)


@boards_router.get("/{board_id}/automations", summary="What runs on its own for this board, and the drafts each item is waiting on")
def board_automations(board_id: str, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import board_automations as automations

    try:
        return automations.for_board(board_id, user)
    except LookupError as exc:
        _fail(exc)


class DraftNow(BaseModel):
    key: str = Field(..., max_length=200, description="The `key` of one of the item's `due` drafts")


@boards_router.post("/{board_id}/items/{item_id}/drafts", summary="Draft one item's message now, for a person to send or cancel")
def board_draft_now(board_id: str, item_id: str, body: DraftNow, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services.coordination import board_automations as automations

    try:
        return automations.draft_now(board_id, item_id, body.key)
    except LookupError as exc:
        _fail(exc)


class AutopilotRun(BaseModel):
    play: Optional[str] = None
    dry_run: bool = True  # say what would happen; a real run must ask for it


@autopilot_router.get("", summary="What the autopilot does on its own, each play's mode and last run")
def autopilot_status(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import autopilot

    return autopilot.status()


@autopilot_router.get("/events", summary="Recent events and what each reaction did")
def autopilot_events(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import events

    return events.status()


@autopilot_router.post("/run", summary="Run the autopilot now (a dry run unless dry_run is false)")
def autopilot_run(body: AutopilotRun, _user: dict = Depends(require_admin)) -> Dict[str, Any]:
    from app.services.coordination import autopilot

    try:
        return autopilot.run(body.play, dry_run=body.dry_run)
    except LookupError as exc:
        _fail(exc)


# ---------------------------------------------------------------------------
# Automation recipes ("when this happens, then do that"), people's own
# ---------------------------------------------------------------------------


class RecipeIn(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    board_role: Optional[Literal["projects", "vendors", "rfqs"]] = None
    trigger: Optional[Literal["status_is", "status_for", "date_within"]] = None
    column: Optional[str] = Field(None, max_length=120,
                                  description="date_within: the date column's id or title; status_for: the status label")
    value: Optional[str] = Field(None, max_length=120, description="status_is: the label; date_within: days")
    action: Optional[Literal["notify", "draft_email"]] = None
    recipients: Optional[str] = Field(None, max_length=500, description="owner, leadership, reviewers, or emails")
    email_to: Optional[str] = Field(None, max_length=200, description="draft_email: vendor, customer, or an address")
    subject: Optional[str] = Field(None, max_length=200)
    body: Optional[str] = Field(None, max_length=4000)
    mode: Optional[Literal["suggest", "auto"]] = None
    enabled: Optional[bool] = None


@autopilot_router.get("/recipes", summary="People's own automation recipes (each with board_role and board_id)")
def recipe_list(board_role: Optional[str] = None, _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.services.coordination import recipes

    return recipes.list_recipes(board_role)


@autopilot_router.get("/recipes/options", summary="Everything the rule form needs as choices: labels, dates, people, fields")
def recipe_options(board_role: str = Query(..., max_length=16), _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import recipes

    try:
        return recipes.options(board_role)
    except ValueError as exc:
        _fail(exc)


@autopilot_router.post("/recipes/preview", summary="What a rule (saved or not) would act on today; nothing is saved or sent")
def recipe_preview(body: RecipeIn, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.coordination import recipes

    data = {"mode": "suggest", "name": "Preview", **body.model_dump(exclude_none=True)}
    try:
        clean = recipes._check(data)
    except ValueError as exc:
        _fail(exc)
    return {"description": recipes.describe(clean), "today": recipes.preview(clean)}


@autopilot_router.get("/recipes/starters", summary="Allied's ready-made recipes, from what the boards show, each with why")
def recipe_starters(preview: bool = False, _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    """preview=true adds `today`: the items each would act on now (nothing is saved or sent)."""
    from app.services.coordination import recipes

    return recipes.starters(with_preview=preview)


@autopilot_router.post("/recipes/starters/{slug}", summary="Add a ready-made recipe (recipients, mode etc. may be changed)",
                       status_code=status.HTTP_201_CREATED)
def recipe_add_starter(slug: str, body: RecipeIn, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import recipes

    try:
        return recipes.add_starter(slug, body.model_dump(exclude_none=True), user)
    except (LookupError, ValueError) as exc:
        _fail(exc)


@autopilot_router.post("/recipes", summary="Add a recipe", status_code=status.HTTP_201_CREATED)
def recipe_add(body: RecipeIn, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import recipes

    try:
        return recipes.create(body.model_dump(exclude_none=True), user)
    except ValueError as exc:
        _fail(exc)


@autopilot_router.patch("/recipes/{recipe_id}", summary="Change a recipe, or switch it on or off")
def recipe_update(recipe_id: int, body: RecipeIn, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import recipes

    try:
        return recipes.update(recipe_id, body.model_dump(exclude_none=True), user)
    except (LookupError, ValueError) as exc:
        _fail(exc)


@autopilot_router.delete("/recipes/{recipe_id}", summary="Remove a recipe", status_code=status.HTTP_204_NO_CONTENT)
def recipe_delete(recipe_id: int, _user: dict = Depends(require_manager)) -> None:
    from app.services.coordination import recipes

    try:
        recipes.delete(recipe_id)
    except LookupError as exc:
        _fail(exc)


@autopilot_router.post("/recipes/run-dates", summary="Check the date recipes now (a dry run unless dry_run is false)")
def recipe_run_dates(dry_run: bool = True, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import recipes

    return {"dry_run": dry_run, "did": recipes.run_dates(dry_run=dry_run)}


@autopilot_router.get("/scorecard", summary="How often people send or cancel each automation's drafts, with advice")
def autopilot_scorecard(days: int = Query(90, ge=7, le=365), _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    from app.services.coordination import autopilot

    return autopilot.scorecard(days)

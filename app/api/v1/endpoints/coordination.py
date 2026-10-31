"""Projects and milestones (from monday), design-doc submissions, shipments, vendor jobs, RFQs, alerts."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
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

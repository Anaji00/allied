"""Jobs: one record from the customer's RFQ to the shipment (stage, what is done, what is waiting, who has the ball)."""

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile, status

from app.api.v1.view_models import AutomationLog, JobPage
from app.core.security import require_manager, require_operator, require_viewer
from app.services.coordination import jobs, packets, purchase_orders

router = APIRouter()


def _fail(exc: Exception):
    """Our own plain-language messages (JobError) and 404s are shown as they are; anything else becomes a fixed message, never raw text."""
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    if isinstance(exc, jobs.JobError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That request could not be processed. Check what was entered and try again.")


def _staff(user: dict) -> bool:
    return user.get("role") in ("ADMIN", "MANAGER", "OPERATOR")


@router.get("", summary="Jobs, newest first, with the stage and who has the ball")
def list_jobs(stage: str = Query("", max_length=24), owner: str = Query("", max_length=256), q: str = Query("", max_length=100),
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100000), _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        out = jobs.list_jobs(stage, owner, q, limit, offset)
        from app.services import follow

        follow.annotate(_user, "job", out.get("items", []), lambda r: r.get("ref"))              # changed_at, followed and changed_since_seen: this person's own marks only
        return out
    except jobs.JobError as exc:
        _fail(exc)


@router.get("/{job}/automation", summary="What the app did on its own for this job (drafts made, alerts raised, plays run), in plain sentences with when", response_model=AutomationLog, response_model_exclude_unset=True)
def job_automation(job: str, limit: int = Query(100, ge=1, le=300), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services import job_automation

    try:
        return job_automation.log(job, limit)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.get("/{job}/page", summary="The whole job page in one call: job, comments, what changed since yesterday, prep facts and the actions that apply (by role)", response_model=JobPage, response_model_exclude_unset=True)
def job_page(job: str, since: str = Query("yesterday", max_length=30), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services import job_page as _page

    try:
        return _page.page(job, user, since)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.get("/{job}", summary="One job by id or reference")
def one(job: str, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.core.money_view import hide_pos

    try:
        out = jobs.get(job, staff=_staff(user))
        if isinstance(out.get("purchase_orders"), list):
            hide_pos(out["purchase_orders"], user)
        return out
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/from-rfq/{rfq}", summary="The job for an RFQ (made once, then the same one comes back)")
def from_rfq(rfq: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return jobs.from_rfq(rfq, user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{job}/design/approve", summary="A manager approves the design; POs cannot be approved or sent before this")
def approve_design(job: str, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return jobs.approve_design(job, user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.get("/{job}/purchase-orders", summary="The job's purchase orders (one per vendor)")
def list_pos(job: str, _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.core.money_view import hide_pos

    try:
        return hide_pos(purchase_orders.for_job(jobs.get(job)["id"]), _user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{job}/purchase-orders", summary="Draft the purchase order for a vendor (one per vendor per job; asking again returns it)")
def create_po(job: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        lines = body.get("lines")
        if lines is not None and (not isinstance(lines, list) or len(lines) > 100):
            raise jobs.JobError("Lines must be a list (at most 100).")
        return purchase_orders.create(job, str(body.get("vendor") or ""), user, lines, str(body.get("terms") or ""), str(body.get("ship_via") or ""),
                                      str(body.get("good_thru") or ""), str(body.get("currency") or ""))
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.post("/{job}/research", summary="Add a research note (title, link, note)")
def add_research(job: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return jobs.add_research(job, str(body.get("title") or ""), str(body.get("url") or ""), str(body.get("note") or ""), user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{job}/research/search", summary="Scan the web for a query and save the top results as research notes")
def search_research(job: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return jobs.search_research(job, str(body.get("query") or ""), user)
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{job}/sales-order", summary="Create Allied's internal sales order for the job (vendors keep their existing PO numbers)")
def create_so(job: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return jobs.create_sales_order(job, body, user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.post("/{job}/link-order", summary="Tie an order that is already made to this job (unsent vendor POs take the job's numbers; sent ones are flagged)")
def link_order(job: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return jobs.link_existing_order(job, str(body.get("sales_order_number") or ""), user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.get("/{job}/sales-order/sheet.pdf", summary="The internal Sales Order Information sheet (staff only, never sent to anyone)")
def so_sheet(job: str, _user: dict = Depends(require_operator)) -> Response:
    try:
        return Response(jobs.sales_order_sheet(job), media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="sales_order_information.pdf"'})
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


catalog_router = APIRouter()


@catalog_router.get("/items", summary="Catalog parts that look like the text, for a pick-list")
def catalog_items(q: str = Query("", max_length=100), limit: int = Query(8, ge=1, le=25), _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return jobs.catalog_search(q, limit)


@router.post("/{job}/packets", summary="Read a scanned job packet (PDF) and put the documents in it on this job")
def upload_packet(job: str, file: UploadFile = File(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return packets.ingest(packets.read_upload(file.file), file.filename or "packet.pdf", user, job=job)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.get("/{job}/documents", summary="The documents found in this job's packets, with only the facts printed on them")
def job_documents(job: str, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        j = jobs.get(job, staff=_staff(user))
        return j["documents"]
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)


@router.post("/{job}/vendor-quotes", summary="Record that a vendor's quotation is in (only the facts given are kept); this makes the PO draft and tells the managers")
def vendor_quote(job: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return jobs.record_vendor_quote(job, str(body.get("vendor") or ""), {k: body.get(k) for k in ("quote_order_number", "date", "total", "lead_time", "valid_until", "currency")}, user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)


@router.post("/{job}/packets/stage-monday", summary="Stage the packet's printed facts for this job's row on the orders board (nothing is sent; only blank columns)")
def stage_packet(job: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return packets.stage_to_monday(job, user)
    except (LookupError, jobs.JobError, ValueError, TypeError) as exc:
        _fail(exc)

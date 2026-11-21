"""FastAPI router for Lead Generation, Sales Intelligence, and ROI Financial Calculator."""

from typing import Any, Dict, List
from app.core.security import require_manager, require_operator, require_viewer
from app.services import leads as svc
from app.api.v1.view_models import DoNotContactRow, Lead, LeadDraft, LeadOutreachRow, LeadPage, LeadSuggestion, LeadSummary
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.lead_agent import (
    LeadProfile,
    LeadSearchUnavailable,
    ROICalculatorRequest,
    ROICalculatorResult,
    TargetPlantAccount,
    lead_agent,
)

router = APIRouter()


class AutomatedFormRequest(BaseModel):
    pulse_id: int
    lead_email: str


class OEMUpgradeRequest(BaseModel):
    current_part: str
    oem_material: str


class ScrapeLeadsRequest(BaseModel):
    job_titles: List[str]
    industries: List[str]
    location: str
    max_results: int = Field(15, ge=1, le=50)


@router.post(
    "/outreach",
    response_model=Dict[str, str],
    summary="Generate personalized cold outreach email for plant engineers",
)
def generate_outreach(profile: LeadProfile, _user: dict = Depends(require_operator)):
    """Generate cold outreach email targeting maintenance and reliability engineers."""
    try:
        body = lead_agent.generate_outreach_email(profile)
        return {"email_body": body}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/oem-upgrade",
    response_model=Dict[str, str],
    summary="Generate an OEM material upgrade proposal",
)
def generate_oem_upgrade(request: OEMUpgradeRequest, _user: dict = Depends(require_operator)):
    """Generate an OEM upgrade proposal recommending Allied 300XL or ceramic composites."""
    try:
        return lead_agent.generate_oem_upgrade_proposal(request.current_part, request.oem_material)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/crm-form-request",
    response_model=Dict[str, Any],
    summary="Send automated CRM request for technical specs and drawings",
)
def send_crm_form_request(request: AutomatedFormRequest, _user: dict = Depends(require_operator)):
    """Send an automated CRM email requesting CAD files/specs via a secure form."""
    try:
        return lead_agent.send_automated_crm_form_request(request.pulse_id, request.lead_email)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/scrape",
    response_model=List[Dict[str, Any]],
    summary="Search web/directories for industrial plant prospects",
)
def scrape_for_leads(request: ScrapeLeadsRequest, _user: dict = Depends(require_operator)):
    """Search for plant maintenance and reliability managers in high-wear industries. Off unless the owner turns on LEAD_WEB_SEARCH (see the setup page)."""
    from app.config import settings as _s

    if not _s.LEAD_WEB_SEARCH:
        raise HTTPException(status_code=409, detail="Searching the web for people is switched off. Add leads by hand or import a CSV instead.")
    try:
        return lead_agent.scrape_web_for_leads(
            job_titles=request.job_titles,
            industries=request.industries,
            location=request.location,
            max_results=request.max_results,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LeadSearchUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post(
    "/roi-calculator",
    response_model=ROICalculatorResult,
    summary="Calculate ROI and Total Cost of Ownership (TCO) for wear part upgrades",
)
def calculate_wear_roi(
    request: ROICalculatorRequest,
    _user: dict = Depends(require_viewer),
) -> ROICalculatorResult:
    """Computes exact annual financial savings, downtime reduction, and payback period."""
    try:
        return lead_agent.calculate_roi(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/targets",
    response_model=List[TargetPlantAccount],
    summary="List pre-seeded high-value enterprise target plant accounts",
)
def list_target_accounts(_user: dict = Depends(require_viewer)) -> List[TargetPlantAccount]:
    """Returns curated target accounts in cement, asphalt, glass, and mining."""
    return lead_agent.get_target_accounts()


# ---------------------------------------------------------------------------
# The lead engine: stored leads, settings, do-not-contact, outreach drafts, suggestions, summary
# ---------------------------------------------------------------------------

def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except svc.LeadError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("", summary="My leads: {items, total, due_today}, best score first, account and contact names inline", response_model=LeadPage)
def list_leads(status: str = "", owner: str = "", due: bool = False, q: str = "", limit: int = 200, offset: int = 0, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.list_leads, status, owner, due, q, limit, offset)


@router.post("", summary="Add a lead, or many ({leads:[...]}). A duplicate (same email, or same company and name) is not added again", status_code=201)
def add_leads(body: Dict[str, Any], user: dict = Depends(require_operator)) -> Dict[str, Any]:
    if isinstance(body.get("leads"), list):
        return _call(svc.add_many, body["leads"], user)
    return _call(svc.add_lead, body, user)


@router.post("/seed-examples", summary="Add the five example target accounts once (a manager); reading the leads never adds anything", status_code=201)
def seed_examples(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return {"added": svc.seed_targets()}


@router.get("/summary", summary="Counts per stage, due today, replied this week, won and lost, win rate, pipeline value and source performance", response_model=LeadSummary)
def lead_summary(_user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return svc.summary(_user.get("role") in ("MANAGER", "ADMIN"))


@router.get("/settings", summary="Lead settings: profile, follow-up days, sequence, signature, postal address, scoring weights, templates, search on or off")
def get_lead_settings(_user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return svc.get_settings()


@router.put("/settings", summary="Change lead settings (a manager)")
def put_lead_settings(body: Dict[str, Any], user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.put_settings, body, user)


@router.get("/do-not-contact", summary="The do-not-contact list (an address or a whole domain)", response_model=List[DoNotContactRow])
def get_dnc(_user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return svc.list_dnc()


@router.post("/do-not-contact", summary="Add an address or domain to the do-not-contact list: no lead draft is made for it", status_code=201)
def add_dnc(body: Dict[str, Any], user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.add_dnc, str(body.get("value") or ""), user, str(body.get("reason") or ""))


@router.delete("/do-not-contact/{dnc_id}", summary="Take one off the do-not-contact list (a manager)")
def remove_dnc(dnc_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    svc.remove_dnc(dnc_id)
    return {"removed": True, "id": dnc_id}


@router.get("/suggestions", summary="Leads Allied already has in its own history: lapsed customers, quotes not won, reorders due, each with the sentence that explains it", response_model=List[LeadSuggestion])
def lead_suggestions(_user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return svc.suggestions(_user.get("role") in ("MANAGER", "ADMIN"))


@router.post("/suggestions/{suggestion_id}/make-lead", summary="Make a lead from a suggestion", status_code=201)
def make_lead(suggestion_id: str, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.make_lead_from_suggestion, suggestion_id, user)


@router.get("/aliases", summary="Customer name aliases (CT-KC means CertainTeed Kansas City); matching uses this list, never a fuzzy merge")
def get_aliases(_user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return svc.list_aliases()


@router.post("/aliases", summary="Say that one customer name is another (a manager)", status_code=201)
def post_alias(body: Dict[str, Any], user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.add_alias, str(body.get("alias") or ""), str(body.get("canonical") or ""), user)


@router.post("/sequences/run", summary="Make the follow-up drafts that are due (a dry run unless dry_run is false; drafts only, in business hours, up to the daily limit)")
def run_sequences(body: Dict[str, Any] = None, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    body = body or {}
    return _call(svc.run_sequences, user, bool(body.get("dry_run", True)), bool(body.get("ignore_hours", False)))


@router.post("/{lead_id}/enrich", summary="Add a public web page's title to the lead's notes (a pasted address; only when the owner has switched web lookups on)")
def enrich(lead_id: int, body: Dict[str, Any], _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.enrich_from_url, lead_id, str(body.get("url") or ""))


@router.post("/digest", summary="Make this week's lead pipeline digest draft now (a manager); it waits in the approval queue and goes to you only")
def make_digest(_user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.weekly_digest)


@router.patch("/{lead_id}", summary="Change a lead: status, owner, notes, next_touch_at, and its contact or account details", response_model=Lead)
def patch_lead(lead_id: int, body: Dict[str, Any], _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.patch_lead, lead_id, body)


@router.delete("/{lead_id}", summary="Archive a lead (it can be found again by an administrator)")
def archive_lead(lead_id: int, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.archive_lead, lead_id)


@router.post("/{lead_id}/outreach", summary="Draft outreach for this lead from a template (a draft in the approval queue; never sent; refused for do-not-contact)", response_model=LeadDraft)
def lead_outreach(lead_id: int, body: Dict[str, Any] = None, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    body = body or {}
    return _call(svc.draft_outreach, lead_id, user, str(body.get("template") or ""))


@router.get("/{lead_id}/outreach", summary="What was drafted for or heard from this lead", response_model=List[LeadOutreachRow])
def lead_outreach_log(lead_id: int, _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return svc.outreach_log(lead_id)


@router.post("/{lead_id}/sequence", summary="Start the follow-up sequence for this lead (Day 0, 4 and 10 by default)")
def start_lead_sequence(lead_id: int, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.start_sequence, lead_id)


@router.post("/{lead_id}/to-monday", summary="Stage this lead as a row on the board the owner chose (a dry run unless dry_run is false; a manager sends it; there is no default board)")
def lead_to_monday(lead_id: int, body: Dict[str, Any] = None, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    body = body or {}
    return _call(svc.to_monday, lead_id, user, str(body.get("board_id") or ""), bool(body.get("dry_run", True)))

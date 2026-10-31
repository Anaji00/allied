"""FastAPI router for Lead Generation, Sales Intelligence, and ROI Financial Calculator."""

from typing import Any, Dict, List
from app.core.security import require_operator, require_viewer
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
    """Search for plant maintenance and reliability managers in high-wear industries."""
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

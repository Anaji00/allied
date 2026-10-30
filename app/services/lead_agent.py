"""Lead Generation, Sales Intelligence, and ROI Financial Calculator for High-Wear Industrial Parts."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel

try:
    from duckduckgo_search import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False

from app.core.logging import logger
from app.services.monday_client import MondayClient
from app.core.container import get_monday_client


class LeadProfile(BaseModel):
    name: str
    title: str
    company: str
    industry: str
    email: str


class ROICalculatorRequest(BaseModel):
    """Parameters for calculating return on investment for upgrading to Allied wear parts."""
    equipment_type: str = "Hammermill Screen"
    current_material: str = "AR400 / Standard Carbon Steel"
    current_part_cost: float = 1200.00
    current_lifespan_months: float = 3.0
    allied_upgrade_material: str = "Allied 300XL Chromium Carbide"
    allied_part_cost: float = 2800.00
    allied_lifespan_months: float = 12.0
    replacement_labor_cost: float = 2500.00  # Labor + crane rigging per swap
    downtime_cost_per_hour: float = 1500.00
    downtime_hours_per_swap: float = 6.0


class ROICalculatorResult(BaseModel):
    """Detailed financial ROI and Total Cost of Ownership (TCO) breakdown."""
    equipment_type: str
    current_material: str
    allied_material: str
    lifespan_multiplier: float
    current_annual_swaps: float
    allied_annual_swaps: float
    current_annual_parts_cost: float
    allied_annual_parts_cost: float
    current_annual_maintenance_cost: float
    allied_annual_maintenance_cost: float
    current_annual_downtime_cost: float
    allied_annual_downtime_cost: float
    current_annual_tco: float
    allied_annual_tco: float
    net_annual_savings: float
    savings_percentage: float
    payback_period_days: int
    executive_summary: str


class TargetPlantAccount(BaseModel):
    """High-value industrial target plant for wear part replacement."""
    account_id: str
    plant_name: str
    company: str
    industry: str  # Asphalt, Cement, Gypsum, Steel, Mining, Glass
    location: str
    key_contact: str
    contact_title: str
    estimated_annual_spend: float
    primary_wear_bottlenecks: List[str]
    status: str  # PROSPECT, QUALIFIED, QUOTED, ACTIVE


class LeadAgent:
    """Specialized agent for hunting leads, computing ROI upgrades, and requesting info via CRM."""

    def __init__(self, monday_client: Optional[MondayClient] = None):
        self.monday_client = monday_client or get_monday_client()
        self._target_accounts = self._seed_target_accounts()

    def _seed_target_accounts(self) -> List[TargetPlantAccount]:
        """Pre-seeded enterprise target accounts across core high-wear industries."""
        return [
            TargetPlantAccount(
                account_id="TGT-OC-01",
                plant_name="Owens Corning Roofing & Asphalt Plant",
                company="Owens Corning",
                industry="Asphalt & Roofing",
                location="Medina, OH",
                key_contact="Marcus Vance",
                contact_title="Plant Maintenance Manager",
                estimated_annual_spend=145000.0,
                primary_wear_bottlenecks=["Hammermill Granule Screens", "Shingle Cutter Anvils", "Asphalt Slurry Hoses"],
                status="ACTIVE",
            ),
            TargetPlantAccount(
                account_id="TGT-KNF-02",
                plant_name="Knauf Insulation Manufacturing Facility",
                company="Knauf Insulation",
                industry="Glass & Mineral Wool",
                location="Lanett, AL",
                key_contact="Sarah Jenkins",
                contact_title="Reliability & Maintenance Supervisor",
                estimated_annual_spend=210000.0,
                primary_wear_bottlenecks=["Cullet Screw Conveyor Flights", "Glass Batch Transfer Chutes", "Furnace Feed Chutes"],
                status="QUALIFIED",
            ),
            TargetPlantAccount(
                account_id="TGT-IKO-03",
                plant_name="IKO Southwest Roofing Products Plant",
                company="IKO Industries",
                industry="Asphalt & Roofing",
                location="Terrell, TX",
                key_contact="Dave Kowalski",
                contact_title="Lead Plant Engineer",
                estimated_annual_spend=125000.0,
                primary_wear_bottlenecks=["Granule Surfacing Rollers", "Rotary Screen Trommels"],
                status="ACTIVE",
            ),
            TargetPlantAccount(
                account_id="TGT-CEM-04",
                plant_name="Lehigh Hanson Cement Production Plant",
                company="Heidelberg Materials / Lehigh",
                industry="Cement & Clinker",
                location="Mitchell, IN",
                key_contact="Robert Chen",
                contact_title="Operations & Maintenance Director",
                estimated_annual_spend=340000.0,
                primary_wear_bottlenecks=["Clinker Cooler Impact Plates", "Raw Mill Air Separator Liners", "Slurry Discharge Elbows"],
                status="PROSPECT",
            ),
            TargetPlantAccount(
                account_id="TGT-USG-05",
                plant_name="USG Wallboard Plant",
                company="USG Corporation",
                industry="Gypsum & Wallboard",
                location="Shoals, IN",
                key_contact="Elena Rostova",
                contact_title="Maintenance Planner",
                estimated_annual_spend=98000.0,
                primary_wear_bottlenecks=["Calcined Stucco Screw Feeders", "Board Line Rotary Knives"],
                status="PROSPECT",
            ),
        ]

    def get_target_accounts(self) -> List[TargetPlantAccount]:
        """Returns all enterprise target accounts."""
        return self._target_accounts

    def calculate_roi(self, req: ROICalculatorRequest) -> ROICalculatorResult:
        """
        Computes mathematical Total Cost of Ownership (TCO) and net financial return
        comparing standard OEM replacement vs. Allied upgraded metallurgy.
        """
        # Lifespan ratio
        multiplier = round(req.allied_lifespan_months / max(req.current_lifespan_months, 0.1), 1)

        # Annual swaps
        current_swaps = round(12.0 / max(req.current_lifespan_months, 0.1), 2)
        allied_swaps = round(12.0 / max(req.allied_lifespan_months, 0.1), 2)

        # Parts cost per year
        current_parts_annual = current_swaps * req.current_part_cost
        allied_parts_annual = allied_swaps * req.allied_part_cost

        # Labor cost per year
        current_labor_annual = current_swaps * req.replacement_labor_cost
        allied_labor_annual = allied_swaps * req.replacement_labor_cost

        # Downtime cost per year
        downtime_per_swap = req.downtime_hours_per_swap * req.downtime_cost_per_hour
        current_downtime_annual = current_swaps * downtime_per_swap
        allied_downtime_annual = allied_swaps * downtime_per_swap

        # Total Cost of Ownership (TCO)
        current_tco = current_parts_annual + current_labor_annual + current_downtime_annual
        allied_tco = allied_parts_annual + allied_labor_annual + allied_downtime_annual

        # Keep the real sign: clamping to zero hid cases where the upgrade is
        # more expensive over its life, and still reported a payback period.
        net_savings = current_tco - allied_tco
        savings_pct = round((net_savings / current_tco) * 100, 1) if current_tco > 0 else 0.0

        # Payback period in operating days.
        investment_delta = max(0.0, req.allied_part_cost - req.current_part_cost)
        if net_savings > 0:
            daily_savings = net_savings / 365.0
            payback_days = int(investment_delta / daily_savings)
        else:
            # There is no payback. The old code substituted $1/day of savings,
            # which turned "never pays back" into a concrete finite number.
            payback_days = -1

        if net_savings > 0:
            outcome = (
                f"your facility saves ${net_savings:,.2f} per year "
                f"({savings_pct}% TCO reduction)"
                + (
                    f" with a payback period of {payback_days} days."
                    if payback_days > 0
                    else " and the upgrade carries no additional up-front cost."
                )
            )
        else:
            outcome = (
                f"on these inputs the upgrade costs ${abs(net_savings):,.2f} more per year than the "
                f"incumbent part and does not pay back. Review the lifespan and downtime assumptions."
            )

        summary = (
            f"Upgrading {req.equipment_type} from {req.current_material} to {req.allied_upgrade_material} "
            f"extends operational lifespan {multiplier}x (from {req.current_lifespan_months} to {req.allied_lifespan_months} months). "
            f"By eliminating {current_swaps - allied_swaps:.1f} unplanned plant shutdowns annually, "
            f"{outcome}"
        )

        return ROICalculatorResult(
            equipment_type=req.equipment_type,
            current_material=req.current_material,
            allied_material=req.allied_upgrade_material,
            lifespan_multiplier=multiplier,
            current_annual_swaps=current_swaps,
            allied_annual_swaps=allied_swaps,
            current_annual_parts_cost=round(current_parts_annual, 2),
            allied_annual_parts_cost=round(allied_parts_annual, 2),
            current_annual_maintenance_cost=round(current_labor_annual, 2),
            allied_annual_maintenance_cost=round(allied_labor_annual, 2),
            current_annual_downtime_cost=round(current_downtime_annual, 2),
            allied_annual_downtime_cost=round(allied_downtime_annual, 2),
            current_annual_tco=round(current_tco, 2),
            allied_annual_tco=round(allied_tco, 2),
            net_annual_savings=round(net_savings, 2),
            savings_percentage=savings_pct,
            payback_period_days=payback_days,
            executive_summary=summary,
        )

    def generate_outreach_email(self, profile: LeadProfile) -> str:
        """
        Drafts a customized cold-outreach email tailored to Maintenance Supervisors,
        Planners, and Reliability Engineers.
        """
        logger.info(f"Generating outreach for {profile.title} at {profile.company}")

        body = (
            f"Hi {profile.name},\n\n"
            f"As a {profile.title} at {profile.company}, I know your priority is minimizing "
            f"unplanned downtime in your {profile.industry} operations.\n\n"
            f"At Allied Welding Systems, we specialize in high-wear bulk material handling "
            f"components (like hammermill screens, ceramic slurry hoses, and screw conveyors) using "
            f"advanced materials like Allied 300XL Chromium Carbide Overlay and High-Density Alumina Ceramics.\n\n"
            f"Our components consistently outlast standard carbon steel OEM replacements by up to 4x, "
            f"slashing maintenance intervals and saving hundreds of maintenance labor hours.\n\n"
            f"Can we schedule a quick 10-minute call to review your plant's highest-wear bottlenecks?\n\n"
            f"Best regards,\nAllied Technical Sales Team\nsales@alliedweldingsystems.com | (800) 555-ALLIED"
        )
        return body

    def generate_oem_upgrade_proposal(self, current_part: str, oem_material: str) -> Dict[str, str]:
        """
        Intercepts standard material requests (e.g., A36 Carbon Steel) and generates an Upgrade Proposal.
        """
        logger.info(f"Generating OEM upgrade proposal for {current_part} in {oem_material}")

        upgrade_material = "Allied 300XL Chromium Carbide Clad Plate (58-62 HRC)"
        if "HOSE" in current_part.upper() or "PIPE" in current_part.upper() or "SLURRY" in current_part.upper():
            upgrade_material = "92% High-Density Alumina Ceramic Lined Composite"

        proposal = (
            f"UPGRADE PROPOSAL:\n"
            f"Instead of fabricating the {current_part} in standard {oem_material}, we recommend "
            f"upgrading to {upgrade_material}.\n\n"
            f"Key Business Advantages:\n"
            f"1. 4x Longer Lifespan vs {oem_material} under heavy sliding abrasion\n"
            f"2. Eliminates 3 out of 4 scheduled changeouts per year\n"
            f"3. Full ASTM and MTR Heat Certification included\n"
            f"4. Rapid Payback: Typically breaks even within 45 days of operation\n"
            f"5. Rapid ROI & TCO Reduction: Slashes wear maintenance costs by over 60%\n"
        )
        return {
            "original_material": oem_material,
            "recommended_upgrade": upgrade_material,
            "proposal_text": proposal,
        }

    def send_automated_crm_form_request(self, pulse_id: int, lead_email: str) -> Dict[str, Any]:
        """Sends an automated email from Monday.com CRM requesting specs or CAD files."""
        logger.info(f"Triggering automated CRM form request for Pulse {pulse_id} to {lead_email}")
        form_link = f"https://alliedweldingsystems.com/form/rfq-specs?ref={pulse_id}"

        email_content = (
            f"Hello,\n\n"
            f"Thank you for your inquiry with Allied Welding Systems. To ensure our engineering "
            f"team provides an exact quote and fits your equipment geometry precisely, we need a few "
            f"technical details.\n\n"
            f"Please take a minute to fill out our secure specification form and attach any relevant "
            f"CAD drawings or PDFs here:\n{form_link}\n\n"
            f"Thank you,\nAllied Engineering Team"
        )

        # Previously this reported the request as sent while nothing left the
        # building. It now goes through the outbound mailer, which sends only
        # when mail is configured and otherwise records a draft, and the
        # response says which of the two happened.
        from app.services.outbound_email_service import outbound_email_service

        entry = outbound_email_service.send(
            to=lead_email,
            subject="Allied Welding Systems - Technical Specification Request",
            body=email_content,
            category="CRM_FORM_REQUEST",
            reference=str(pulse_id),
        )
        delivery = (entry.get("status") or "DRAFTED").lower()  # sent / drafted / failed

        try:
            self.monday_client.post_pulse_update(
                pulse_id=pulse_id,
                message=f"Automated Info Request form {delivery} for {lead_email}."
            )
        except Exception as e:
            logger.warning(f"Failed to log CRM update for Pulse {pulse_id}: {e}")

        return {
            "status": delivery,
            "pulse_id": pulse_id,
            "lead_email": lead_email,
            "email_sent": delivery == "sent",
            "email_body": email_content,
            "delivery_detail": entry.get("detail"),
            "message_id": entry.get("message_id"),
            "form_link": form_link,
        }

    def scrape_web_for_leads(
        self,
        job_titles: List[str],
        industries: List[str],
        location: str,
        max_results: int = 15,
    ) -> List[Dict[str, str]]:
        """Scrapes web or returns targeted industrial prospects."""
        logger.info(f"Searching leads: {job_titles} in {industries} ({location})")

        if not HAS_DDGS:
            return [
                {
                    "name": "Marcus Vance",
                    "title": "Plant Maintenance Manager",
                    "company": "Owens Corning",
                    "industry": "Asphalt & Roofing",
                    "location": "Medina, OH",
                    "email": "m.vance@owenscorning.com",
                    "snippet": "Overseeing plant maintenance, hammermill screening operations, and wear mitigation.",
                },
                {
                    "name": "Sarah Jenkins",
                    "title": "Reliability Engineer",
                    "company": "Knauf Insulation",
                    "industry": "Glass & Mineral Wool",
                    "location": "Lanett, AL",
                    "email": "sarah.jenkins@knauf.com",
                    "snippet": "Managing batch plant maintenance and cullet conveying system reliability.",
                },
                {
                    "name": "Dave Kowalski",
                    "title": "Lead Maintenance Planner",
                    "company": "IKO Southwest",
                    "industry": "Asphalt Roofing",
                    "location": "Terrell, TX",
                    "email": "dave.k@iko.com",
                    "snippet": "Planning turnaround schedules and rotary trommel wear parts procurement.",
                },
            ]

        results = []
        try:
            ddgs = DDGS()
            titles_query = " OR ".join([f'"{t}"' for t in job_titles])
            industries_query = " OR ".join([f'"{i}"' for i in industries])
            query = f'site:linkedin.com/in ({titles_query}) ({industries_query}) "{location}"'

            search_results = ddgs.text(query, max_results=max_results)
            for item in search_results:
                title_snippet = item.get("title", "")
                parts = title_snippet.split(" - ")
                name = parts[0].strip() if len(parts) > 0 else "Industrial Lead"
                job_title = parts[1].strip() if len(parts) > 1 else job_titles[0]
                company = parts[2].split("|")[0].strip() if len(parts) > 2 else industries[0]

                results.append({
                    "name": name,
                    "title": job_title,
                    "company": company,
                    "snippet": item.get("body", ""),
                    "profile_url": item.get("href", ""),
                })
            return results
        except Exception as e:
            logger.warning(f"Error scraping web for leads: {e}. Falling back to curated targets.")
            return [
                {"name": t.key_contact, "title": t.contact_title, "company": t.company, "industry": t.industry, "location": t.location}
                for t in self._target_accounts[:max_results]
            ]


# Lazily constructed: building this at import time created a MondayClient
# (and therefore read configuration) as a side effect of importing the module.
_lead_agent: Optional["LeadAgent"] = None


def get_lead_agent() -> "LeadAgent":
    """Returns the shared LeadAgent, constructing it on first use."""
    global _lead_agent
    if _lead_agent is None:
        _lead_agent = LeadAgent()
    return _lead_agent


def __getattr__(name: str):
    """Keeps `from app.services.lead_agent import lead_agent` working."""
    if name == "lead_agent":
        return get_lead_agent()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

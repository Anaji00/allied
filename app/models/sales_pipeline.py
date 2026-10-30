"""Pydantic schemas for Sales RFQ Ingestion, Predictive Wear Tracking, Surcharges, and Upsells."""

from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PredictiveWearEquipment(BaseModel):
    """Monitors running plant equipment wear-life cycle for industrial customers."""
    equipment_tag: str = Field(..., description="Plant equipment tag (e.g. OC-ED-HM-01, KNF-LAN-SC-04)")
    customer_id: str
    customer_name: str
    plant_location: str
    component_name: str
    installed_item_id: str
    install_date: str
    expected_lifespan_days: int
    days_in_service: int
    estimated_wear_pct: float
    alert_level: str = "HEALTHY"  # HEALTHY, WARNING_75_PCT, CRITICAL_REPLACE
    suggested_replacement_sku: str
    historical_unit_price: Decimal
    last_service_order: str

    # How the lifespan above was arrived at. Wear is a projection against an
    # assumed service life, and a projection is only worth as much as its
    # basis -- so the basis travels with the number rather than being implied.
    lifespan_basis: str = "CATALOGUE"  # CATALOGUE | OBSERVED
    lifespan_basis_detail: str = "Published service life for this part."
    observed_lifespan_days: Optional[int] = None
    replacement_observations: int = 0
    days_remaining: int = 0
    projected_replacement_date: Optional[str] = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class DraftQuoteFromWearRequest(BaseModel):
    """Request to generate a proactive sales quote for expiring equipment component."""
    equipment_tag: str
    apply_volume_discount_pct: Decimal = Decimal("5.0")
    target_delivery_weeks: int = 2



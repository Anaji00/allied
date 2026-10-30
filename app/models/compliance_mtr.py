"""Pydantic schemas for Material Test Reports (MTRs), Heat Certs, and Certificates of Conformance (CofC)."""

from decimal import Decimal
from typing import Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ChemicalComposition(BaseModel):
    """Certified chemical percentages extracted from mill test report."""
    carbon_pct: Optional[Decimal] = None
    chromium_pct: Optional[Decimal] = None
    manganese_pct: Optional[Decimal] = None
    silicon_pct: Optional[Decimal] = None
    molybdenum_pct: Optional[Decimal] = None
    nickel_pct: Optional[Decimal] = None
    phosphorus_pct: Optional[Decimal] = None
    sulfur_pct: Optional[Decimal] = None
    iron_pct: Optional[Decimal] = None


class MechanicalProperties(BaseModel):
    """Certified mechanical test data."""
    yield_strength_psi: Optional[int] = None
    tensile_strength_psi: Optional[int] = None
    elongation_pct: Optional[Decimal] = None
    hardness_rating: Optional[str] = None  # e.g., 58-62 HRC, 220 HBW


class MTRRecord(BaseModel):
    """Certified Material Test Report metadata linked to supplier heat number."""
    heat_number: str = Field(..., description="Unique mill heat / batch identifier")
    item_id: str = Field(..., description="Master Sage SKU (e.g. PLATE-300XL-38, BEAM-W8X31)")
    material_spec: str = Field(..., description="ASTM specification (e.g. ASTM A992, Allied 300XL CCO)")
    mill_supplier: str = Field(..., description="Originating steel mill or clad foundry")
    chemical_analysis: ChemicalComposition = Field(default_factory=ChemicalComposition)
    mechanical_tests: MechanicalProperties = Field(default_factory=MechanicalProperties)
    verified_compliant: bool = True
    compliance_notes: List[str] = Field(default_factory=list)
    certificate_pdf_url: Optional[str] = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class MTRVerifyRequest(BaseModel):
    """Request payload for OCR/VLM verification of an inbound supplier mill test PDF."""
    heat_number: str
    item_id: str
    raw_ocr_text: Optional[str] = None
    chemistry_input: Optional[Dict[str, float]] = None
    hardness_input: Optional[str] = None


class CertificateOfConformance(BaseModel):
    """Formal Certificate of Conformance (CofC) bundle issued to customers."""
    cofc_id: str = Field(..., description="CofC certificate identifier (e.g. COFC-2026-9901)")
    sales_order_number: str
    customer_id: str
    customer_name: str
    customer_po_number: str
    issued_date: str
    heat_numbers: List[str]
    mtr_records: List[MTRRecord]
    conformance_statement: str = (
        "Allied Welding Systems hereby certifies that all materials supplied on this order "
        "comply strictly with certified ASTM standards, metallurgical hardness ratings, "
        "and customer engineering specifications without exception."
    )
    authorized_signature: str = "Chief Quality Inspector — Allied Welding Systems"
    compliance_verified: bool = True


class ToleranceSpec(BaseModel):
    """Min/max tolerance envelope bounds."""
    min_val: Optional[float] = None
    max_val: Optional[float] = None


class ASTMEnvelope(BaseModel):
    """Standardized metallurgy bounds for steel, clad, and ceramic items."""
    spec_code: str
    description: str
    carbon_pct: Optional[ToleranceSpec] = None
    chromium_pct: Optional[ToleranceSpec] = None
    manganese_pct: Optional[ToleranceSpec] = None
    silicon_pct: Optional[ToleranceSpec] = None
    min_yield_psi: Optional[int] = None
    min_tensile_psi: Optional[int] = None
    min_hardness_hrc: Optional[float] = None
    max_hardness_hrc: Optional[float] = None


class NCRRecord(BaseModel):
    """Non-Conformance Report issued when mill certificate fails ASTM envelope."""
    ncr_id: str = Field(..., description="Unique NCR number (e.g. NCR-2026-001)")
    heat_number: str
    item_id: str
    supplier: str
    material_spec: str
    failure_reasons: List[str] = Field(default_factory=list)
    severity: str = "HIGH"  # CRITICAL, HIGH, MEDIUM
    status: str = "QUARANTINED"  # QUARANTINED, REJECTED, CONCESSION_APPROVED, CLOSED
    quarantined_by: str = "Quality Automated Guard"
    created_at: str
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    disposition_notes: Optional[str] = None
    available_actions: List[Dict[str, str]] = Field(default_factory=lambda: [
        {"action": "RETURN_TO_SUPPLIER", "label": "Reject Lot & Issue Supplier Return (RMA)"},
        {"action": "APPROVE_CONCESSION", "label": "Approve Engineering Concession / Non-Critical Use"},
        {"action": "REQUEST_LAB_RETEST", "label": "Quarantine Lot & Request Independent Lab Retest"},
    ])


class DispositionNCRRequest(BaseModel):
    """Payload to disposition a quarantined material lot.

    Deliberately carries no actor field: the acting user is taken from the
    bearer token so that the compliance audit trail cannot be forged by a client.
    """
    action: str  # RETURN_TO_SUPPLIER, APPROVE_CONCESSION, REQUEST_LAB_RETEST
    disposition_notes: str

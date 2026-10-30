"""Service for Material Test Reports (MTRs), Heat Certs, Non-Conformance Reports (NCR), and CofC bundles."""

import json
import re
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.config import settings
from app.core.logging import logger
from app.core.audit import audit_log
from app.models.compliance_mtr import (
    ASTMEnvelope,
    CertificateOfConformance,
    ChemicalComposition,
    DispositionNCRRequest,
    MechanicalProperties,
    MTRRecord,
    MTRVerifyRequest,
    NCRRecord,
    ToleranceSpec,
)


# Standard ASTM / Manufacturing metallurgy tolerances
ASTM_ENVELOPES: Dict[str, ASTMEnvelope] = {
    "PLATE-300XL-38": ASTMEnvelope(
        spec_code="ALLIED_300XL_CCO",
        description="Allied 300XL Premium Chromium Carbide Overlay Plate",
        carbon_pct=ToleranceSpec(min_val=3.8, max_val=5.5),
        chromium_pct=ToleranceSpec(min_val=25.0, max_val=33.0),
        min_hardness_hrc=58.0,
        max_hardness_hrc=64.0,
    ),
    "BEAM-W8X31": ASTMEnvelope(
        spec_code="ASTM_A992",
        description="Structural Steel Wide Flange Beams (ASTM A992)",
        carbon_pct=ToleranceSpec(min_val=0.05, max_val=0.23),
        manganese_pct=ToleranceSpec(min_val=0.50, max_val=1.60),
        silicon_pct=ToleranceSpec(min_val=0.10, max_val=0.40),
        min_yield_psi=50000,
        min_tensile_psi=65000,
    ),
    "PLATE-A36-12": ASTMEnvelope(
        spec_code="ASTM_A36",
        description="Carbon Structural Steel Plate (ASTM A36)",
        carbon_pct=ToleranceSpec(min_val=0.05, max_val=0.26),
        manganese_pct=ToleranceSpec(min_val=0.30, max_val=1.20),
        min_yield_psi=36000,
        min_tensile_psi=58000,
    ),
    "HOSE-CERAMIC-4IN": ASTMEnvelope(
        spec_code="ZIBO_ALUMINA_92",
        description="92% Alumina Ceramic Lined Slurry Hose",
        min_hardness_hrc=60.0,
    ),
}

# A material specification governs a whole product family, not one SKU. Keying
# envelopes only by exact SKU meant every new size (BEAM-W10X49 alongside
# BEAM-W8X31) fell through to "no envelope" and was silently auto-passed.
# Patterns are tried in order; the first match wins, and exact SKU entries above
# still take precedence over the family they belong to.
ASTM_FAMILY_PATTERNS: List[tuple] = [
    (re.compile(r"^PLATE-300XL"), "PLATE-300XL-38"),
    (re.compile(r"^(BEAM|COL|WF)-W\d"), "BEAM-W8X31"),
    (re.compile(r"^(PLATE|SHT)-A36"), "PLATE-A36-12"),
    (re.compile(r"^(HOSE|PIPE)-(CERAMIC|ALUMINA)"), "HOSE-CERAMIC-4IN"),
]


def resolve_astm_envelope(item_id: str) -> Optional[ASTMEnvelope]:
    """Finds the governing ASTM envelope for a SKU, by exact match then family."""
    sku = (item_id or "").strip().upper()
    if not sku:
        return None
    if sku in ASTM_ENVELOPES:
        return ASTM_ENVELOPES[sku]
    for pattern, envelope_key in ASTM_FAMILY_PATTERNS:
        if pattern.match(sku):
            return ASTM_ENVELOPES[envelope_key]
    return None


_HEAT_PREFIX = "HT-"


def _normalise_heat(heat_number: Optional[str]) -> str:
    """Canonical form of a heat number: upper case, separators folded to '-'.

    'ht 300xl_8819' and 'HT-300XL-8819' are the same heat; '8819' is not.
    """
    cleaned = re.sub(r"[\s_]+", "-", (heat_number or "").strip().upper())
    return re.sub(r"-{2,}", "-", cleaned).strip("-")


# Disposition action -> resulting NCR status.
_NCR_DISPOSITIONS = {
    "RETURN_TO_SUPPLIER": "REJECTED",
    "APPROVE_CONCESSION": "CONCESSION_APPROVED",
    "REQUEST_LAB_RETEST": "QUARANTINED",
    "CLOSE": "CLOSED",
}


def _as_float(value: Any) -> Optional[float]:
    """Coerces a reported measurement to float, or None when it is absent/unparseable."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    """Coerces a reported measurement to int, or None when it is absent/unparseable."""
    f = _as_float(value)
    return int(f) if f is not None else None


def _parse_hrc(hardness: str) -> Optional[float]:
    """Extracts a Rockwell C number from free-text hardness such as '60.5 HRC'."""
    if not hardness:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", hardness)
    if not match or "HRC" not in hardness.upper():
        return None
    return float(match.group(1))


def _audit_range(
    element: str, measured: Optional[float], spec: Optional[ToleranceSpec], unit: str
) -> List[str]:
    """Checks one measurement against a tolerance envelope.

    A required element that was not measured is a finding, not a pass. Bounds are
    compared against None rather than truthiness so that a legitimate 0.0 bound
    is still enforced.
    """
    if spec is None:
        return []
    if measured is None:
        bounds = f"{spec.min_val}-{spec.max_val}".replace("None", "n/a")
        return [f"{element} not reported (spec requires {bounds}{unit})"]

    findings: List[str] = []
    if spec.min_val is not None and measured < spec.min_val:
        findings.append(f"{element} {measured}{unit} below min {spec.min_val}{unit}")
    if spec.max_val is not None and measured > spec.max_val:
        findings.append(f"{element} {measured}{unit} exceeds max {spec.max_val}{unit}")
    return findings


class MTRService:
    """Manages certified mill test data, heat number verification, ASTM audit, and CofC bundles."""

    def __init__(self, certs_json_path: Optional[Path] = None):
        self.certs_path = certs_json_path or (settings.DATA_DIR / "mtr_certs.json")
        self.mtr_cache: Dict[str, MTRRecord] = {}
        self.ncrs: Dict[str, NCRRecord] = {}
        # disposition_ncr checks the status then writes it; without this the
        # same quarantined lot could be dispositioned twice concurrently.
        self._lock = threading.RLock()
        self.load_certs()
        if settings.SEED_DEMO_DATA:
            self._seed_sample_ncr()

    def load_certs(self) -> None:
        """Loads certified MTR records into memory."""
        if not self.certs_path.exists():
            return

        try:
            with open(self.certs_path, mode="r", encoding="utf-8") as f:
                data = json.load(f)
                for entry in data:
                    chem_dict = entry.get("chemical_analysis", {})
                    mech_dict = entry.get("mechanical_tests", {})

                    chem = ChemicalComposition(
                        carbon_pct=Decimal(str(chem_dict["carbon_pct"])) if "carbon_pct" in chem_dict else None,
                        chromium_pct=Decimal(str(chem_dict["chromium_pct"])) if "chromium_pct" in chem_dict else None,
                        manganese_pct=Decimal(str(chem_dict["manganese_pct"])) if "manganese_pct" in chem_dict else None,
                        silicon_pct=Decimal(str(chem_dict["silicon_pct"])) if "silicon_pct" in chem_dict else None,
                        molybdenum_pct=Decimal(str(chem_dict["molybdenum_pct"])) if "molybdenum_pct" in chem_dict else None,
                        iron_pct=Decimal(str(chem_dict["iron_pct"])) if "iron_pct" in chem_dict else None,
                    )

                    mech = MechanicalProperties(
                        yield_strength_psi=mech_dict.get("yield_strength_psi"),
                        tensile_strength_psi=mech_dict.get("tensile_strength_psi"),
                        elongation_pct=Decimal(str(mech_dict["elongation_pct"])) if "elongation_pct" in mech_dict else None,
                        hardness_rating=mech_dict.get("hardness_rating"),
                    )

                    record = MTRRecord(
                        heat_number=entry["heat_number"],
                        item_id=entry["item_id"],
                        material_spec=entry["material_spec"],
                        mill_supplier=entry["mill_supplier"],
                        chemical_analysis=chem,
                        mechanical_tests=mech,
                        verified_compliant=entry.get("verified_compliant", True),
                        compliance_notes=entry.get("compliance_notes", []),
                    )
                    self.mtr_cache[record.heat_number.upper()] = record

            logger.info(f"Loaded {len(self.mtr_cache)} certified MTR records into memory")
        except Exception as e:
            logger.warning(f"Could not load MTR certs from {self.certs_path}: {e}")

    def _seed_sample_ncr(self) -> None:
        """Seeds a realistic quarantined Non-Conformance Report for compliance triage."""
        ncr_id = "NCR-2026-0041"
        self.ncrs[ncr_id] = NCRRecord(
            ncr_id=ncr_id,
            heat_number="HT-LOWCR-9921",
            item_id="PLATE-300XL-38",
            supplier="Offshore Foundry Group",
            material_spec="Allied 300XL CCO (ASTM Spec)",
            failure_reasons=[
                "Chromium content 21.4% is below required minimum of 25.0% for 300XL overlay",
                "Hardness measured 52.5 HRC, failing minimum specification of 58.0 HRC",
            ],
            severity="CRITICAL",
            status="QUARANTINED",
            quarantined_by="Quality Automated Guard (ASTM Audit)",
            created_at=datetime.now(timezone.utc).isoformat(),
            disposition_notes="Material held in Yard Bay 4 awaiting vendor return authorization.",
        )

    def get_mtr_by_heat(self, heat_number: str) -> Optional[MTRRecord]:
        """Looks up a certified MTR by mill heat number, or None if there is none.

        An unknown heat returns None. It previously returned a synthetic
        "compliant" record with invented chemistry, which meant an unrecognised
        heat number looked identical to a certified one to every caller.
        """
        clean_heat = _normalise_heat(heat_number)
        if not clean_heat:
            return None
        if clean_heat in self.mtr_cache:
            return self.mtr_cache[clean_heat]

        # Exact match on the normalised number only. A substring test let
        # '8819' certify HT-300XL-8819, i.e. a partial number could vouch for
        # a lot it was never written for. The one tolerance kept is a missing
        # 'HT-' prefix, and then the whole remaining number must match.
        for key, record in self.mtr_cache.items():
            if _normalise_heat(key) == clean_heat:
                return record
        if not clean_heat.startswith(_HEAT_PREFIX):
            prefixed = _HEAT_PREFIX + clean_heat
            for key, record in self.mtr_cache.items():
                if _normalise_heat(key) == prefixed:
                    return record
        return None

    def verify_and_register_mtr(
        self,
        req: MTRVerifyRequest,
        actor_email: str = "system",
        actor_role: str = "SYSTEM",
    ) -> MTRRecord:
        """
        Audits OCR chemistry and hardness data against the ASTM tolerance envelope.
        If non-compliant, automatically flags the record, issues an NCR, and alerts triage.
        """
        chemistry = req.chemistry_input or {}
        # Absent measurements stay absent. Substituting nominal in-spec values
        # here would turn "the mill cert could not be read" into "certified".
        c_val = _as_float(chemistry.get("carbon"))
        cr_val = _as_float(chemistry.get("chromium"))
        mn_val = _as_float(chemistry.get("manganese"))
        si_val = _as_float(chemistry.get("silicon"))

        chem = ChemicalComposition(
            carbon_pct=Decimal(str(c_val)) if c_val is not None else None,
            chromium_pct=Decimal(str(cr_val)) if cr_val is not None else None,
            manganese_pct=Decimal(str(mn_val)) if mn_val is not None else None,
            silicon_pct=Decimal(str(si_val)) if si_val is not None else None,
        )

        hardness_str = (req.hardness_input or "").strip()
        hardness_hrc = _parse_hrc(hardness_str)
        mech = MechanicalProperties(
            hardness_rating=hardness_str or None,
            yield_strength_psi=_as_int(chemistry.get("yield_strength_psi")),
            tensile_strength_psi=_as_int(chemistry.get("tensile_strength_psi")),
        )

        envelope = resolve_astm_envelope(req.item_id)
        discrepancies: List[str] = []

        if envelope is None:
            # No published envelope means this material cannot be audited, which
            # is not the same as passing an audit.
            discrepancies.append(
                f"No ASTM tolerance envelope on file for item '{req.item_id}' — "
                f"manual metallurgical review required before release."
            )
        else:
            discrepancies.extend(
                _audit_range("Carbon", c_val, envelope.carbon_pct, "%")
                + _audit_range("Chromium", cr_val, envelope.chromium_pct, "%")
                + _audit_range("Manganese", mn_val, envelope.manganese_pct, "%")
                + _audit_range("Silicon", si_val, envelope.silicon_pct, "%")
            )

            if envelope.min_hardness_hrc is not None or envelope.max_hardness_hrc is not None:
                if hardness_hrc is None:
                    discrepancies.append(
                        f"Hardness not reported (spec requires "
                        f"{envelope.min_hardness_hrc}-{envelope.max_hardness_hrc} HRC)"
                    )
                else:
                    if envelope.min_hardness_hrc is not None and hardness_hrc < envelope.min_hardness_hrc:
                        discrepancies.append(
                            f"Hardness {hardness_hrc} HRC below min {envelope.min_hardness_hrc} HRC"
                        )
                    if envelope.max_hardness_hrc is not None and hardness_hrc > envelope.max_hardness_hrc:
                        discrepancies.append(
                            f"Hardness {hardness_hrc} HRC exceeds max {envelope.max_hardness_hrc} HRC"
                        )

            # Mechanical minimums were declared on the A36/A992 envelopes but
            # never actually checked.
            for label, measured, minimum in (
                ("Yield strength", mech.yield_strength_psi, envelope.min_yield_psi),
                ("Tensile strength", mech.tensile_strength_psi, envelope.min_tensile_psi),
            ):
                if minimum is None:
                    continue
                if measured is None:
                    discrepancies.append(f"{label} not reported (spec requires >= {minimum:,} psi)")
                elif measured < minimum:
                    discrepancies.append(f"{label} {measured:,} psi below min {minimum:,} psi")

        is_compliant = len(discrepancies) == 0
        notes = ["ASTM metallurgy tolerance verified clean."] if is_compliant else discrepancies

        record = MTRRecord(
            heat_number=_normalise_heat(req.heat_number),
            item_id=req.item_id,
            material_spec=envelope.description if envelope else f"Specification for {req.item_id}",
            mill_supplier="Inbound Supplier Mill",
            chemical_analysis=chem,
            mechanical_tests=mech,
            verified_compliant=is_compliant,
            compliance_notes=notes,
        )

        previous = self.mtr_cache.get(record.heat_number)
        self.mtr_cache[record.heat_number] = record

        # Registering a cert replaces whatever was on file for the heat, so the
        # replacement is recorded rather than only the NCR path being audited.
        audit_log(
            action="MTR_REGISTERED" if previous is None else "MTR_REPLACED",
            resource_type="COMPLIANCE_MTR",
            resource_id=record.heat_number,
            actor_email=actor_email,
            actor_role=actor_role,
            detail=(
                f"Heat #{record.heat_number} ({req.item_id}) registered "
                f"{'compliant' if is_compliant else 'non-compliant'}"
                + (
                    f"; replaced prior certificate (was "
                    f"{'compliant' if previous.verified_compliant else 'non-compliant'}, "
                    f"item {previous.item_id})"
                    if previous is not None else ""
                )
            ),
        )

        # If non-compliant, create automated Non-Conformance Report (NCR)
        if not is_compliant:
            ncr_num = f"NCR-{datetime.now().year}-{uuid.uuid4().hex[:4].upper()}"
            ncr = NCRRecord(
                ncr_id=ncr_num,
                heat_number=req.heat_number.upper(),
                item_id=req.item_id,
                supplier="Supplier Inbound Delivery",
                material_spec=record.material_spec,
                failure_reasons=discrepancies,
                severity="HIGH",
                status="QUARANTINED",
                quarantined_by="Automated ASTM Quality Inspector",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            with self._lock:
                self.ncrs[ncr_num] = ncr

            # Log audit event
            audit_log(
                action="MTR_NCR_ISSUED",
                resource_type="COMPLIANCE_NCR",
                resource_id=ncr_num,
                actor_email=actor_email,
                actor_role=actor_role,
                detail=f"Quarantined Heat #{req.heat_number}: {'; '.join(discrepancies)}",
            )

            # Also push into operational exception queue for back-office awareness
            try:
                from app.services.exception_queue_service import ExceptionItem, exception_queue_service
                exception_queue_service.add_exception(
                    ExceptionItem(
                        exception_id=f"EXC-{ncr_num}",
                        exception_type="COMPLIANCE_MTR_FAILURE",
                        severity="HIGH",
                        title=f"Material Quarantined: Heat #{req.heat_number} ({req.item_id})",
                        description=f"ASTM tolerance failure: {'; '.join(discrepancies)}",
                        reference_id=ncr_num,
                        vendor_or_customer="Supplier Inbound Delivery",
                        available_actions=[
                            {"action": "RETURN_TO_SUPPLIER", "label": "Reject & Return Heat Lot"},
                            {"action": "APPROVE_CONCESSION", "label": "Engineering Concession Approval"},
                        ],
                    )
                )
            except Exception as e:
                logger.warning(f"Could not queue exception for NCR: {e}")

        return record

    def list_ncrs(self, status_filter: Optional[str] = None) -> List[NCRRecord]:
        """Returns all Non-Conformance Reports sorted by creation date."""
        items = list(self.ncrs.values())
        if status_filter:
            items = [n for n in items if n.status.upper() == status_filter.upper()]
        return sorted(items, key=lambda x: x.created_at, reverse=True)

    def get_ncr(self, ncr_id: str) -> Optional[NCRRecord]:
        """Retrieves single NCR by ID."""
        return self.ncrs.get(ncr_id.upper())

    def disposition_ncr(
        self,
        ncr_id: str,
        request: DispositionNCRRequest,
        actor_email: str = "system",
    ) -> NCRRecord:
        """Dispositions a quarantined NCR (e.g. Return to Supplier or Approve Concession).

        `actor_email` is supplied by the caller from the authenticated principal.
        The value on the request body is ignored: a quality disposition is an
        auditable act and its actor must not be self-declared by the client.
        """
        action = (request.action or "").strip().upper()
        if action not in _NCR_DISPOSITIONS:
            raise ValueError(
                f"Unknown disposition '{request.action}'. Must be one of: {sorted(_NCR_DISPOSITIONS)}"
            )

        # Checking the status and writing it are one step: a quarantined lot
        # must not be dispositioned twice by two concurrent requests.
        with self._lock:
            ncr = self.ncrs.get((ncr_id or "").upper())
            if not ncr:
                raise KeyError(f"NCR {ncr_id} not found")
            if ncr.status != "QUARANTINED":
                raise ValueError(
                    f"NCR {ncr_id} is already dispositioned (status: {ncr.status}) and cannot be changed."
                )

            ncr.status = _NCR_DISPOSITIONS[action]
            ncr.disposition_notes = f"{action}: {request.disposition_notes}"

            # A retest request leaves the lot quarantined, so it is not a resolution.
            if ncr.status != "QUARANTINED":
                ncr.resolved_at = datetime.now(timezone.utc).isoformat()
                ncr.resolved_by = actor_email

        audit_log(
            action="MTR_NCR_DISPOSITIONED",
            resource_type="COMPLIANCE_NCR",
            resource_id=ncr_id,
            actor_email=actor_email,
            detail=f"Status set to {ncr.status}: {request.disposition_notes}",
        )

        return ncr

    def generate_cofc(
        self,
        sales_order_number: str,
        customer_id: str,
        customer_name: str,
        customer_po_number: str,
        heat_numbers: List[str],
    ) -> CertificateOfConformance:
        """Constructs an audited Certificate of Conformance bundle for an order.

        A CofC is only marked verified when every requested heat resolved to a
        certificate on file and every one of those passed. Missing heats used to
        be back-filled with an unrelated sample certificate, which produced a
        signed conformance statement for material that had never been certified.
        """
        resolved = {h: self.get_mtr_by_heat(h) for h in heat_numbers}
        mtr_list = [m for m in resolved.values() if m is not None]
        unresolved = [h for h, m in resolved.items() if m is None]

        clean_so = sales_order_number.replace("SO-", "")
        cofc_id = f"COFC-2026-{clean_so}"

        if unresolved:
            logger.warning(
                f"CofC {cofc_id}: no certificate on file for heat(s) {unresolved} — marking unverified"
            )

        # Verified requires: at least one heat, every heat resolved, all passing.
        all_compliant = (
            bool(heat_numbers)
            and not unresolved
            and all(m.verified_compliant for m in mtr_list)
        )

        return CertificateOfConformance(
            cofc_id=cofc_id,
            sales_order_number=sales_order_number,
            customer_id=customer_id,
            customer_name=customer_name,
            customer_po_number=customer_po_number,
            issued_date=datetime.now().strftime("%Y-%m-%d"),
            heat_numbers=heat_numbers,
            mtr_records=mtr_list,
            compliance_verified=all_compliant,
        )


# Global instance
mtr_service = MTRService()

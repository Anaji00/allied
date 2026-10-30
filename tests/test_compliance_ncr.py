"""Unit and integration tests for ASTM Metallurgy Envelopes and Non-Conformance Report (NCR) Quarantines."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.compliance_mtr import MTRVerifyRequest, DispositionNCRRequest
from app.services.mtr_service import mtr_service

client = TestClient(app)


def test_mtr_compliant_verification():
    """Verify inbound MTR that meets ASTM envelope is marked compliant."""
    req = MTRVerifyRequest(
        heat_number="HT-CLEAN-9901",
        item_id="PLATE-300XL-38",
        chemistry_input={"carbon": 4.8, "chromium": 29.5, "manganese": 1.1},
        hardness_input="61 HRC",
    )
    result = mtr_service.verify_and_register_mtr(req)
    assert result.verified_compliant is True
    assert "verified clean" in result.compliance_notes[0]


def test_mtr_non_compliant_auto_ncr_issuance():
    """Verify inbound MTR failing ASTM envelope is quarantined and auto-generates an NCR."""
    req = MTRVerifyRequest(
        heat_number="HT-FAIL-CR-002",
        item_id="PLATE-300XL-38",
        chemistry_input={"carbon": 4.5, "chromium": 21.0, "manganese": 1.2},  # 21% is below 25.0% min
        hardness_input="53 HRC",  # 53 HRC is below 58.0 HRC min
    )
    result = mtr_service.verify_and_register_mtr(req)
    assert result.verified_compliant is False

    # Check that an NCR was generated in the NCR registry
    ncrs = mtr_service.list_ncrs()
    matching_ncr = next((n for n in ncrs if n.heat_number == "HT-FAIL-CR-002"), None)
    assert matching_ncr is not None
    assert matching_ncr.status == "QUARANTINED"
    assert any("Chromium" in r for r in matching_ncr.failure_reasons)
    assert any("Hardness" in r for r in matching_ncr.failure_reasons)


def test_ncr_disposition_lifecycle_api():
    """Verify NCR listing and dispositioning via REST API."""
    # List NCRs
    resp = client.get("/api/v1/compliance/ncr")
    assert resp.status_code == 200
    ncrs = resp.json()
    assert len(ncrs) >= 1

    sample_ncr_id = ncrs[0]["ncr_id"]

    # Retrieve single NCR
    detail_resp = client.get(f"/api/v1/compliance/ncr/{sample_ncr_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["ncr_id"] == sample_ncr_id

    # Disposition NCR. An actor_email in the body is ignored -- the acting user
    # is taken from the authenticated principal, not self-declared by the client.
    disp_payload = {
        "action": "RETURN_TO_SUPPLIER",
        "disposition_notes": "Rejected lot due to low overlay hardness. Issued RMA #9921.",
        "actor_email": "spoofed@attacker.com",
    }
    disp_resp = client.post(f"/api/v1/compliance/ncr/{sample_ncr_id}/disposition", json=disp_payload)
    assert disp_resp.status_code == 200
    updated_ncr = disp_resp.json()
    assert updated_ncr["status"] == "REJECTED"
    assert updated_ncr["resolved_by"] != "spoofed@attacker.com"
    assert "@" in updated_ncr["resolved_by"]

    # An already-dispositioned NCR cannot be silently re-dispositioned.
    repeat = client.post(f"/api/v1/compliance/ncr/{sample_ncr_id}/disposition", json=disp_payload)
    assert repeat.status_code == 422

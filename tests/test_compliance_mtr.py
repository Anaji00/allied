"""Unit tests for MTR mill certificates, heat number verification, and CofC bundles."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_mtr_by_valid_heat_number():
    """Verify retrieving certified MTR by heat number."""
    response = client.get("/api/v1/compliance/mtr/HT-300XL-8819")
    assert response.status_code == 200
    data = response.json()
    assert data["heat_number"] == "HT-300XL-8819"
    assert data["verified_compliant"] is True
    assert "chemical_analysis" in data
    assert float(data["chemical_analysis"]["chromium_pct"]) >= 28.0


def test_verify_supplier_mtr():
    """Verify registering a complete, in-spec supplier mill test certificate.

    BEAM-W10X49 has no exact envelope entry; it resolves to the ASTM A992
    family envelope, so it is genuinely audited rather than auto-passed.
    """
    payload = {
        "heat_number": "HT-A992-9988",
        "item_id": "BEAM-W10X49",
        "chemistry_input": {
            "carbon": 0.09,
            "chromium": 0.05,
            "manganese": 1.30,
            "silicon": 0.22,
            "yield_strength_psi": 52000,
            "tensile_strength_psi": 68000,
        },
        "hardness_input": "170 HBW",
    }
    response = client.post("/api/v1/compliance/mtr/verify", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["heat_number"] == "HT-A992-9988"
    assert data["verified_compliant"] is True


def test_verify_supplier_mtr_incomplete_is_not_certified():
    """An MTR missing required measurements must not be certified compliant."""
    response = client.post(
        "/api/v1/compliance/mtr/verify",
        json={"heat_number": "HT-A992-EMPTY", "item_id": "BEAM-W10X49"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["verified_compliant"] is False
    assert any("not reported" in note for note in data["compliance_notes"])


def test_verify_supplier_mtr_unknown_material_is_not_certified():
    """A material with no governing envelope cannot be audited, so it cannot pass."""
    response = client.post(
        "/api/v1/compliance/mtr/verify",
        json={
            "heat_number": "HT-UNKNOWN-1",
            "item_id": "MYSTERY-ALLOY-9",
            "chemistry_input": {"carbon": 0.01, "chromium": 0.0},
            "hardness_input": "10 HRC",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["verified_compliant"] is False
    assert any("No ASTM tolerance envelope" in note for note in data["compliance_notes"])


def test_generate_certificate_of_conformance():
    """Verify generating audited Certificate of Conformance bundle for an order."""
    response = client.get(
        "/api/v1/compliance/cofc/SO-10221-998?customer_id=10221&customer_name=Owens%20Corning&customer_po_number=PO-OC-998&heat_numbers=HT-300XL-8819,HT-C1045-9120"
    )
    assert response.status_code == 200
    data = response.json()
    assert "COFC-2026-" in data["cofc_id"]
    assert data["sales_order_number"] == "SO-10221-998"
    assert len(data["mtr_records"]) >= 1
    assert data["compliance_verified"] is True

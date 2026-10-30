"""Tests for Vendor Bill 3-Way Reconciliation Service."""

from decimal import Decimal
from fastapi.testclient import TestClient
from app.main import app
from app.services.reconciliation_service import (
    VendorInvoice,
    VendorInvoiceLine,
    reconciliation_service,
)

client = TestClient(app)


def test_reconciliation_exact_match_approval():
    """Verify clean 3-way match approves invoice without discrepancies."""
    inv = VendorInvoice(
        invoice_number="INV-ZIBO-TEST-01",
        vendor_name="ZIBO",
        po_number="PO-ZIBO-4412",
        lines=[
            VendorInvoiceLine(
                item_id="HOSE-CERAMIC-4IN",
                quantity_billed=Decimal("2"),
                unit_cost_billed=Decimal("420.00"),
                total_amount=Decimal("840.00"),
            )
        ],
        freight_billed=Decimal("85.00"),
        total_amount_billed=Decimal("925.00"),
    )
    result = reconciliation_service.reconcile_vendor_invoice(inv)
    assert result.reconciliation_status == "RECONCILED_APPROVED"
    assert result.cost_variance_amount == Decimal("0.00")
    assert len(result.discrepancies) == 0
    assert result.dispute_email_draft is None


def test_reconciliation_price_and_freight_discrepancy():
    """Verify detection of price increase and unauthorized freight with dispute generation."""
    inv = VendorInvoice(
        invoice_number="INV-NIBROCK-SURCHARGE",
        vendor_name="NIBROCK",
        po_number="PO-NIBROCK-9921",
        lines=[
            VendorInvoiceLine(
                item_id="PLATE-300XL-38",
                quantity_billed=Decimal("4"),
                unit_cost_billed=Decimal("125.00"),  # PO authorized was $110.00
                total_amount=Decimal("500.00"),
            )
        ],
        freight_billed=Decimal("180.00"),  # PO authorized was $125.00
        total_amount_billed=Decimal("680.00"),
    )
    result = reconciliation_service.reconcile_vendor_invoice(inv)
    assert result.reconciliation_status == "DISCREPANCY_FLAGGED"
    assert result.cost_variance_amount > Decimal("0.00")
    assert len(result.discrepancies) >= 1
    assert result.dispute_email_draft is not None
    assert "Billing Discrepancy Notice" in result.dispute_email_draft


def test_reconciliation_api_endpoints():
    """Verify REST API endpoints for matching invoices and reading history."""
    payload = {
        "invoice_number": "INV-API-TEST",
        "vendor_name": "KWS",
        "po_number": "PO-KWS-8819",
        "lines": [
            {
                "item_id": "SCREEN-250",
                "quantity_billed": 2,
                "unit_cost_billed": 650.0,
                "total_amount": 1300.0,
            }
        ],
        "freight_billed": 0.0,
        "total_amount_billed": 1300.0,
    }
    resp = client.post("/api/v1/reconciliation/match", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["reconciliation_status"] == "RECONCILED_APPROVED"

    hist_resp = client.get("/api/v1/reconciliation/history")
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()) >= 1

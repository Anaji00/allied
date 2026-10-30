"""Regression tests for the back-office review findings.

Covers vendor-invoice reconciliation (totals, vendor, duplicates, dispute
dispatch, freight), compliance auth and heat matching, CSV import merging and
batch validation, exception-queue action handling, and the agent memory cache.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.compliance_mtr import MTRVerifyRequest
from app.models.db_models import AuditLogDB
from app.services.agent_memory_cache import agent_memory_cache
from app.services.csv_import_service import csv_import_service
from app.services.exception_queue_service import (
    ExceptionItem,
    ResolveExceptionRequest,
    exception_queue_service,
)
from app.services.mtr_service import mtr_service
from app.services.reconciliation_service import (
    VendorInvoice,
    VendorInvoiceLine,
    reconciliation_service,
)

client = TestClient(app)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


def _zibo_invoice(number: str, total: str = "925.00", vendor: str = "ZIBO", line_total: str = "840.00"):
    """An invoice against PO-ZIBO-4412 (2 x HOSE-CERAMIC-4IN @ $420, freight $85)."""
    return VendorInvoice(
        invoice_number=number,
        vendor_name=vendor,
        po_number="PO-ZIBO-4412",
        lines=[
            VendorInvoiceLine(
                item_id="HOSE-CERAMIC-4IN",
                quantity_billed=Decimal("2"),
                unit_cost_billed=Decimal("420.00"),
                total_amount=Decimal(line_total),
            )
        ],
        freight_billed=Decimal("85.00"),
        total_amount_billed=Decimal(total),
    )


@pytest.fixture
def dispute_spy(monkeypatch):
    """Records dispute letters instead of handing them to the mailer."""
    from app.services import outbound_email_service as oes

    sent = []

    class _Matcher:
        def match_vendor(self, name):
            return {"email": "ar@supplier.example"}, 100.0

    def fake_send_dispute(vendor_name, vendor_email, invoice_number, body):
        entry = {"status": "DRAFTED", "message_id": f"MSG-{len(sent)}", "detail": "test"}
        sent.append((vendor_name, invoice_number))
        return entry

    monkeypatch.setattr(reconciliation_service, "_matcher", _Matcher())
    monkeypatch.setattr(oes.outbound_email_service, "send_dispute", fake_send_dispute)
    return sent


def _audit_rows(action: str, resource_id: str):
    with SessionLocal() as db:
        return (
            db.query(AuditLogDB)
            .filter(AuditLogDB.action == action, AuditLogDB.resource_id == resource_id)
            .all()
        )


# -- 1. invoice totals -------------------------------------------------------

def test_invoice_total_far_above_authorised_is_not_approved():
    res = reconciliation_service.reconcile_vendor_invoice(
        _zibo_invoice(_uid("INV-BIG"), total="50000.00", line_total="840.00")
    )
    assert res.reconciliation_status != "RECONCILED_APPROVED"
    assert any("Invoice Total Exceeds Authorised" in d for d in res.discrepancies)


def test_line_total_above_authorised_is_flagged():
    res = reconciliation_service.reconcile_vendor_invoice(
        _zibo_invoice(_uid("INV-LINE"), total="10085.00", line_total="10000.00")
    )
    assert res.reconciliation_status == "DISCREPANCY_FLAGGED"
    assert any("Line Total Overbilled" in d for d in res.discrepancies)


def test_clean_invoice_still_approves_and_is_audited():
    number = _uid("INV-OK")
    res = reconciliation_service.reconcile_vendor_invoice(_zibo_invoice(number))
    assert res.reconciliation_status == "RECONCILED_APPROVED"
    assert _audit_rows("INVOICE_APPROVED_FOR_PAYMENT", res.reconciliation_id)


# -- 2. vendor mismatch ------------------------------------------------------

def test_invoice_from_a_different_vendor_needs_a_person(dispute_spy):
    number = _uid("INV-KWS")
    res = reconciliation_service.reconcile_vendor_invoice(_zibo_invoice(number, vendor="KWS"))
    assert res.reconciliation_status == "VENDOR_MISMATCH"
    assert any("Vendor mismatch" in d for d in res.discrepancies)
    assert dispute_spy == [], "the wrong supplier must not be sent a dispute"
    queued = exception_queue_service.list_exceptions(status_filter="OPEN")
    assert any(e.reference_id == number for e in queued)


def test_vendor_long_name_matches_po_vendor_code():
    res = reconciliation_service.reconcile_vendor_invoice(
        _zibo_invoice(_uid("INV-ZLONG"), vendor="Zibo Supereal Ceramics Co., Ltd.")
    )
    assert res.reconciliation_status == "RECONCILED_APPROVED"


# -- 3. duplicate invoices ---------------------------------------------------

def test_same_vendor_and_invoice_number_cannot_be_approved_twice():
    number = _uid("INV-DUP")
    first = reconciliation_service.reconcile_vendor_invoice(_zibo_invoice(number))
    assert first.reconciliation_status == "RECONCILED_APPROVED"
    second = reconciliation_service.reconcile_vendor_invoice(_zibo_invoice(number))
    assert second.reconciliation_status == "DUPLICATE_INVOICE"
    assert any(first.reconciliation_id in d for d in second.discrepancies)


# -- 7. dispute dispatch -----------------------------------------------------

def test_unmatched_invoice_is_not_disputed_with_the_vendor(dispute_spy):
    number = _uid("INV-NOPO")
    res = reconciliation_service.reconcile_vendor_invoice(
        VendorInvoice(
            invoice_number=number,
            vendor_name="NIBROCK",
            po_number="PO-DOES-NOT-EXIST",
            lines=[],
            total_amount_billed=Decimal("48000.00"),
        )
    )
    assert res.reconciliation_status == "UNMATCHED_NO_PO"
    assert dispute_spy == []
    assert any(
        e.reference_id == number for e in exception_queue_service.list_exceptions(status_filter="OPEN")
    )


def test_dispute_is_sent_once_per_vendor_and_invoice(dispute_spy):
    number = _uid("INV-DISP")
    for _ in range(2):
        res = reconciliation_service.reconcile_vendor_invoice(
            _zibo_invoice(number, total="1025.00", line_total="840.00")
        )
        assert res.reconciliation_status == "DISCREPANCY_FLAGGED"
    assert dispute_spy == [("ZIBO", number)]


# -- 8. freight on cache-found POs ------------------------------------------

def test_cache_found_po_authorises_no_freight_by_default():
    # ORD-OC-EDM-2025-01: NIBROCK PO-VEND-NIB-4491, 2 x OC755507 @ 1785, 4 x BAR-34X2-41 @ 115.
    res = reconciliation_service.reconcile_vendor_invoice(
        VendorInvoice(
            invoice_number=_uid("INV-FRT"),
            vendor_name="NIBROCK",
            po_number="PO-VEND-NIB-4491",
            lines=[
                VendorInvoiceLine(item_id="OC755507", quantity_billed=Decimal("2"),
                                  unit_cost_billed=Decimal("1785.00"), total_amount=Decimal("3570.00")),
                VendorInvoiceLine(item_id="BAR-34X2-41", quantity_billed=Decimal("4"),
                                  unit_cost_billed=Decimal("115.00"), total_amount=Decimal("460.00")),
            ],
            freight_billed=Decimal("90.00"),
            total_amount_billed=Decimal("4120.00"),
        )
    )
    assert res.reconciliation_status == "DISCREPANCY_FLAGGED"
    assert any("Unauthorized Freight" in d for d in res.discrepancies)


# -- 4. compliance auth and MTR audit ---------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/v1/compliance/mtr/HT-300XL-8819"),
    ("post", "/api/v1/compliance/mtr/verify"),
    ("get", "/api/v1/compliance/cofc/SO-1"),
    ("get", "/api/v1/cache/summary"),
    ("get", "/api/v1/cache/orders/SO-10881"),
    ("get", "/api/v1/sales/predictive-wear"),
    ("post", "/api/v1/sales/predictive-wear/scan-and-alert"),
    ("get", "/api/v1/health"),
])
def test_back_office_routes_require_a_token(auth_enforced, method, path):
    kwargs = {"json": {"heat_number": "HT-X", "item_id": "PLATE-300XL-38"}} if method == "post" else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code in (401, 403), f"{path} returned {response.status_code}"


def test_liveness_probe_stays_public_and_reveals_no_config(auth_enforced):
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "timestamp"}


def test_replacing_an_mtr_is_audited():
    heat = _uid("HT-AUD")
    req = MTRVerifyRequest(
        heat_number=heat, item_id="PLATE-300XL-38",
        chemistry_input={"carbon": 4.8, "chromium": 29.5, "manganese": 1.1}, hardness_input="61 HRC",
    )
    mtr_service.verify_and_register_mtr(req)
    mtr_service.verify_and_register_mtr(req, actor_email="qa@allied.example", actor_role="OPERATOR")
    assert _audit_rows("MTR_REGISTERED", heat)
    replaced = _audit_rows("MTR_REPLACED", heat)
    assert replaced and replaced[0].actor_email == "qa@allied.example"


# -- 5. heat number matching -------------------------------------------------

@pytest.mark.parametrize("query", ["8819", "300XL-881", "HT-300XL-881", "XL-8819"])
def test_partial_heat_number_does_not_certify(query):
    assert mtr_service.get_mtr_by_heat(query) is None


@pytest.mark.parametrize("query", ["HT-300XL-8819", " ht-300xl-8819 ", "HT 300XL 8819", "300XL-8819"])
def test_full_heat_number_resolves(query):
    rec = mtr_service.get_mtr_by_heat(query)
    assert rec is not None and rec.heat_number == "HT-300XL-8819"


# -- 9. CSV master-data merge ------------------------------------------------

def test_item_update_only_touches_columns_in_the_csv():
    item_id = _uid("TEST-MERGE")
    full = (
        "item_id,description,gl_sales_account,gl_inventory_account,standard_cost,base_price,preferred_vendor,uom,category,stock_on_hand,min_stock_level\n"
        f'{item_id},"MERGE TEST PLATE",4000-00,1200-00,1500.00,3200.00,NIBROCK,SHT,Clad Plate,5.0,2.0\n'
    )
    assert csv_import_service.import_csv(full, "ITEMS").status == "SUCCESS"

    res = csv_import_service.import_csv(f"item_id,base_price\n{item_id},3300.00\n", "ITEMS")
    assert res.status == "SUCCESS" and res.records_updated == 1
    rec = csv_import_service.matcher.items_cache[item_id]
    assert Decimal(rec["base_price"]) == Decimal("3300.00")
    assert Decimal(rec["standard_cost"]) == Decimal("1500.00")
    assert rec["preferred_vendor"] == "NIBROCK"
    assert rec["description"] == "MERGE TEST PLATE"


def test_non_numeric_cost_rejects_the_row():
    item_id = _uid("TEST-BADNUM")
    csv_import_service.import_csv(f"item_id,standard_cost\n{item_id},120.00\n", "ITEMS")
    res = csv_import_service.import_csv(f"item_id,standard_cost\n{item_id},twelve\n", "ITEMS")
    assert res.status == "FAILED"
    assert any("standard_cost" in e and "twelve" in e for e in res.errors)
    assert Decimal(csv_import_service.matcher.items_cache[item_id]["standard_cost"]) == Decimal("120.00")


def test_vendor_update_without_email_column_keeps_email():
    vend = _uid("TESTV").replace("-", "_")
    csv_import_service.import_csv(
        f"vendor_id,vendor_name,email\n{vend},Merge Vendor,ar@merge.example\n", "VENDORS"
    )
    csv_import_service.import_csv(f"vendor_id,payment_terms\n{vend},Net 60 Days\n", "VENDORS")
    rec = csv_import_service.matcher.vendors_cache[vend]
    assert rec["email"] == "ar@merge.example"
    assert rec["payment_terms"] == "Net 60 Days"


# -- 10. batch order validation ----------------------------------------------

def test_bad_batch_row_creates_no_orders(monkeypatch):
    calls = []
    monkeypatch.setattr(
        csv_import_service.orchestrator, "process_inbound_order", lambda req: calls.append(req)
    )
    csv_text = (
        "customer_name,customer_po_number,item_id_or_text,quantity,unit_price,shipping_address,payment_terms\n"
        "Owens Corning,PO-VAL-1,OC755507,2,3772.91,Dock 1,Net 30 Days\n"
        "Knauf Insulation,PO-VAL-2,KN5218554,abc,35378.14,Dock 2,Net 30 Days\n"
        "Knauf Insulation,PO-VAL-3,KN5218554,1,,Dock 2,Net 30 Days\n"
        "Knauf Insulation,PO-VAL-4,KN5218554,-1,10,Dock 2,Net 30 Days\n"
    )
    res = csv_import_service.import_csv(csv_text, "ORDERS_BATCH")
    assert res.status == "FAILED"
    assert res.records_created == 0
    assert calls == [], "no order may be committed while any row is invalid"
    joined = " | ".join(res.errors)
    assert "Row 2" in joined and "quantity" in joined
    assert "Row 3" in joined and "unit_price" in joined
    assert "Row 4" in joined


def test_batch_endpoint_returns_422_not_500_for_bad_rows():
    response = client.post("/api/v1/csv/import", json={
        "import_type": "ORDERS_BATCH",
        "csv_text": "customer_name,customer_po_number,item_id_or_text,quantity,unit_price\nX,PO-1,SKU,1e999999,1\n",
    })
    assert response.status_code == 422


# -- 11. exception queue actions ---------------------------------------------

def _exc(**kw):
    item = ExceptionItem(
        exception_id=_uid("EXC-RF"),
        exception_type=kw.pop("exception_type", "INVOICE_COST_VARIANCE"),
        title="Review fix probe",
        description=kw.pop("description", "Nibrock billed TESTRF-SKU-77 above PO"),
        reference_id=kw.pop("reference_id", _uid("INV-RF")),
        vendor_or_customer="NIBROCK",
        variance_amount=0.0,
        available_actions=kw.pop("available_actions", [
            {"action": "DISPATCH_DISPUTE_NOTICE", "label": "Send dispute"},
            {"action": "OVERRIDE_PRICE_CHECK", "label": "Approve price"},
        ]),
    )
    return exception_queue_service.add_exception(item)


def test_action_not_offered_by_the_exception_is_refused():
    item = _exc()
    with pytest.raises(ValueError):
        exception_queue_service.resolve_exception(
            item.exception_id, ResolveExceptionRequest(action_type="ACCEPT_PRICE_SURCHARGE")
        )
    assert exception_queue_service._queue[item.exception_id].status == "OPEN"


def test_dispute_notice_that_was_not_sent_leaves_exception_open():
    item = _exc()
    res = exception_queue_service.resolve_exception(
        item.exception_id, ResolveExceptionRequest(action_type="DISPATCH_DISPUTE_NOTICE")
    )
    assert res["status"] == "recorded"
    assert res["action_completed"] is False
    assert exception_queue_service._queue[item.exception_id].status == "OPEN"


def test_price_override_requires_and_uses_an_explicit_price():
    from app.services.vendor_reprice import vendor_reprice_service

    item = _exc()
    with pytest.raises(ValueError):
        exception_queue_service.resolve_exception(
            item.exception_id, ResolveExceptionRequest(action_type="OVERRIDE_PRICE_CHECK")
        )
    res = exception_queue_service.resolve_exception(
        item.exception_id,
        ResolveExceptionRequest(action_type="OVERRIDE_PRICE_CHECK", new_unit_price=Decimal("112.50")),
    )
    assert res["status"] == "success"
    assert vendor_reprice_service._price_registry["TESTRF-SKU-77"]["unit_cost"] == 112.50


def test_manual_resolution_is_always_available():
    item = _exc()
    res = exception_queue_service.resolve_exception(
        item.exception_id, ResolveExceptionRequest(action_type="MANUALLY_RESOLVED")
    )
    assert res["status"] == "success"
    assert exception_queue_service._queue[item.exception_id].status == "RESOLVED"


# -- 12 / 16. sales tags and cache keys --------------------------------------

def test_unknown_equipment_tag_is_not_found():
    response = client.post("/api/v1/sales/predictive-wear/draft-quote",
                           json={"equipment_tag": "NO-SUCH-TAG", "apply_volume_discount_pct": 5})
    assert response.status_code == 404


def test_discount_above_cap_is_refused():
    response = client.post("/api/v1/sales/predictive-wear/draft-quote",
                           json={"equipment_tag": "OC-EDM-HM-01", "apply_volume_discount_pct": 150})
    assert response.status_code == 422


def test_two_customers_sharing_a_po_do_not_overwrite_each_other():
    po = _uid("PO-SHARED")
    a = {"order_id": _uid("ORD-A"), "sales_order_number": _uid("SO-A"), "customer_po_number": po,
         "customer_id": "CUST-A", "customer_name": "Alpha Mills"}
    b = {"order_id": _uid("ORD-B"), "sales_order_number": _uid("SO-B"), "customer_po_number": po,
         "customer_id": "CUST-B", "customer_name": "Beta Plant"}
    agent_memory_cache.cache_order(a)
    agent_memory_cache.cache_order(b)

    assert len(agent_memory_cache.get_orders_by_po(po)) == 2
    assert agent_memory_cache.get_order(po) is None, "an ambiguous PO must not pick one customer"
    assert agent_memory_cache.get_order(po, customer="CUST-B")["order_id"] == b["order_id"]
    assert agent_memory_cache.get_order(a["sales_order_number"])["customer_id"] == "CUST-A"

    results = agent_memory_cache.search_orders(po, limit=10)
    ids = [r["order_id"] for r in results]
    assert sorted(ids) == sorted([a["order_id"], b["order_id"]])

"""A purchase order can state its currency (blank stays blank, never dollars), the PDF and the commercial invoice follow it, and a checked bill keeps its own lines so they sit beside the PO's.
Temporary database; nothing is sent."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import PaymentScheduleDB, PurchaseOrderDB, ReconciliationDB
from app.services import bill_match
from app.services.coordination import jobs, purchase_orders, rfqs
from tests.conftest import staff_token

BOSS = {"email": "boss.cur@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def H(role="MANAGER"):
    return {"Authorization": "Bearer " + staff_token(f"cur-{role.lower()}@alliedweldingsystems.com", role)}


def _job():
    r = rfqs.create({"customer": "CurCo", "subject": f"cur {uuid.uuid4().hex[:5]}"}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    return j


def _po(currency="", vendor=None):
    j = _job()
    return purchase_orders.create(j["id"], vendor or f"CurVendor {uuid.uuid4().hex[:4]}", BOSS, lines=[{"description": "plate", "quantity": 2, "unit_cost": 300, "item": "P1"}], currency=currency)


def test_a_new_po_has_no_currency_unless_one_is_given_and_a_bad_one_is_refused():
    assert _po()["currency"] is None and _po()["currency_source"] is None
    po = _po("eur")
    assert po["currency"] == "EUR" and po["currency_source"] == "purchase order"
    with pytest.raises(jobs.JobError):
        _po("ZZZ")


def test_the_currency_can_be_set_and_cleared_on_a_draft_and_follows_the_payment_schedule_when_blank(client):
    po = _po()
    r = client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"currency": "cad"}, headers=H())
    assert r.status_code == 200 and r.json()["currency"] == "CAD"
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"currency": "nope"}, headers=H()).status_code in (400, 422)
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"currency": ""}, headers=H()).json()["currency"] is None
    with SessionLocal() as db:
        db.add(PaymentScheduleDB(po_id=po["id"], job_id=po["job_id"], vendor=po["vendor"], currency="CNY", total=600, terms_code="50/50", status="ACTIVE"))
        db.commit()
    got = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=H()).json()
    assert got["currency"] == "CNY" and got["currency_source"] == "payment schedule"
    with SessionLocal() as db:
        assert db.get(PurchaseOrderDB, po["id"]).currency is None               # read-through only: nothing was written to the PO


def test_the_pdf_and_the_commercial_invoice_follow_the_pos_currency_and_a_blank_stays_unstated(client):
    import fitz

    blank, eur = _po(), _po("EUR")
    text = lambda b: "".join(p.get_text() for p in fitz.open(stream=b, filetype="pdf"))  # noqa: E731
    assert "in EUR" in text(purchase_orders.pdf(eur["id"])) and "All prices and amounts are in" not in text(purchase_orders.pdf(blank["id"]))
    facts = client.get(f"/api/v1/purchase-orders/{eur['id']}/import-paperwork", headers=H("OPERATOR")).json()
    assert facts["currency"] == "EUR" and not any(m["field"] == "currency" for m in facts["missing"])
    none = client.get(f"/api/v1/purchase-orders/{blank['id']}/import-paperwork", headers=H("OPERATOR")).json()
    assert none["currency"] == "" and any(m["field"] == "currency" for m in none["missing"])
    chosen = client.get(f"/api/v1/purchase-orders/{eur['id']}/import-paperwork", params={"currency": "USD"}, headers=H("OPERATOR")).json()
    assert chosen["currency"] == "USD"                                           # a choice made on the screen still wins


def _bill(po, **kw):
    detail = {"lines": [{"item_id": "P1", "description": "plate", "quantity": "3", "unit_cost": "350.00", "amount": "1050.00"},
                        {"item_id": "ZZ", "description": "mystery", "quantity": "1", "unit_cost": "10.00", "amount": "10.00"}], "freight": "195.00", "currency": "USD", "invoice_date": "2026-11-20"}
    with SessionLocal() as db:
        db.add(ReconciliationDB(reconciliation_id=uuid.uuid4().hex[:16], invoice_number=f"INV-{uuid.uuid4().hex[:4]}", vendor_name=po["vendor"], po_number=po["po_number"] or "PO-X",
                                reconciliation_status="DISCREPANCY_FLAGGED", authorized_po_total=600, actual_billed_total=1255, cost_variance_amount=655, cost_variance_pct=109, discrepancies=[],
                                bill_detail=kw.get("detail", detail)))
        db.commit()


def test_bill_match_puts_each_bill_line_beside_its_po_line_and_an_old_bill_has_no_detail(client):
    po = purchase_orders.approve(_po()["id"], BOSS)
    _bill(po)
    old = purchase_orders.approve(_po()["id"], BOSS)
    _bill(old, detail=None)
    out = client.get(f"/api/v1/purchase-orders/{po['id']}/bill-match", headers=H("OPERATOR")).json()
    d = out["bills"][0]["detail"]
    assert d["freight"] == "195.00" and d["currency"] == "USD" and d["po_lines_not_billed"] == []
    first, second = d["lines"]
    assert first["matched"] and first["po"]["line"] == 1 and first["po"]["quantity"] == "2" and first["variance"] == {"quantity": "1", "unit_cost": "50.00", "amount": "450.00"}
    assert second["matched"] is False and second["po"] is None and second["variance"] is None
    assert client.get(f"/api/v1/purchase-orders/{old['id']}/bill-match", headers=H("OPERATOR")).json()["bills"][0]["detail"] is None


def test_a_bill_read_in_keeps_its_own_lines():
    from decimal import Decimal

    from app.services.reconciliation_service import VendorInvoice, VendorInvoiceLine, reconciliation_service

    inv = VendorInvoice(invoice_number=f"KEEP-{uuid.uuid4().hex[:4]}", vendor_name="Nobody Ltd", po_number=f"NOPO-{uuid.uuid4().hex[:4]}", currency="CAD", freight_billed=Decimal("5.00"), total_amount_billed=Decimal("25.00"),
                        lines=[VendorInvoiceLine(item_id="A1", description="widget", quantity_billed=Decimal("2"), unit_cost_billed=Decimal("10.00"), total_amount=Decimal("20.00"))])
    reconciliation_service.reconcile_vendor_invoice(inv)
    with SessionLocal() as db:
        row = db.query(ReconciliationDB).filter(ReconciliationDB.invoice_number == inv.invoice_number).one()
        assert row.bill_detail["freight"] == "5.00" and row.bill_detail["currency"] == "CAD" and row.bill_detail["lines"][0] == {"item_id": "A1", "description": "widget", "quantity": "2", "unit_cost": "10.00", "amount": "20.00"}

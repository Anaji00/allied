"""Payments by vendor, job fields on the due list, certificates to chase, the packing-slip flag on the orders list, and plain dates in check-in drafts. Read only; temporary database and output folder."""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, OrderDB, PurchaseOrderDB
from app.services import payments
from tests.conftest import staff_token
from tests.test_vendor_payment_sync import MGR, _world


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def H(role="MANAGER"):
    return {"Authorization": "Bearer " + staff_token(f"gaps-{role.lower()}@alliedweldingsystems.com", role)}


def test_due_rows_carry_the_job_and_the_po_number_so_each_row_opens_its_job(client):
    job_id, po_id, tag = _world()
    payments.create_schedule(po_id, "30/50/20", MGR, currency="CNY")
    payments.fire(po_id, "PO_ISSUED", MGR)
    rows = client.get("/api/v1/payments/due", headers=H()).json()["due"]
    row = next(r for r in rows if r["po_id"] == po_id)
    assert row["job_id"] == job_id and row["job_ref"] == f"JOB-SYN-{tag}" and row["po_number"] == f"PO{tag}" and row["currency"] == "CNY"


def test_schedules_by_vendor_show_terms_paid_left_and_the_next_step_and_are_for_managers(client):
    job_id, po_id, tag = _world("1000.00")
    payments.create_schedule(po_id, "30/50/20", MGR, currency="CNY")
    payments.fire(po_id, "PO_ISSUED", MGR)
    out = client.get("/api/v1/payments/schedules", headers=H()).json()
    s = next(x for x in out["schedules"] if x["po_id"] == po_id)
    assert s["terms_code"] == "30/50/20" and s["total"] == "1000.00" and s["paid"] == "0.00" and s["remaining"] == "1000.00" and s["job_ref"] == f"JOB-SYN-{tag}" and s["po_number"] == f"PO{tag}"
    assert s["next_step"]["status"] == "DUE" and s["next_step"]["amount"] == "300.00" and [t["seq"] for t in s["steps"]] == [1, 2, 3] and "tranches" not in s
    v = next(x for x in out["vendors"] if x["vendor"] == s["vendor"])
    assert v["po_count"] == 1 and v["po_ids"] == [po_id]
    for role in ("OPERATOR", "VIEWER"):
        assert client.get("/api/v1/payments/schedules", headers=H(role)).status_code == 403
    with SessionLocal() as db:
        db.get(PurchaseOrderDB, po_id).status = "CANCELLED"
        db.commit()
    assert not any(x["po_id"] == po_id for x in client.get("/api/v1/payments/schedules", headers=H()).json()["schedules"])        # a cancelled PO drops out


def _cert_po(tmp_path, cert=True, status="SENT"):
    job_id, po_id, tag = _world("500.00")
    with SessionLocal() as db:
        po = db.get(PurchaseOrderDB, po_id)
        po.lines = [{"qty": 2, "item": "P1", "description": "plate", "unit_cost": "250", "amount": "500", "cert_required": cert}, {"qty": 1, "item": "P2", "description": "bar", "unit_cost": "5", "amount": "5"}]
        po.status = status
        db.commit()
    return job_id, po_id, tag


def test_a_po_needing_a_certificate_is_listed_until_a_certificate_file_is_attached_and_shows_no_prices(client, tmp_path):
    job_id, po_id, tag = _cert_po(tmp_path)
    _, other, _t = _cert_po(tmp_path, cert=False)
    rows = client.get("/api/v1/compliance/certs-to-chase", headers=H("OPERATOR")).json()
    row = next(r for r in rows["items"] if r["po_id"] == po_id)
    assert row["vendor"] and row["line_count"] == 1 and row["po_number"] == f"PO{tag}" and row["job_ref"] == f"JOB-SYN-{tag}" and "days_waiting" in row and "last_asked_on" in row
    assert not any(r["po_id"] == other for r in rows["items"])                              # nothing to chase when no line needs one
    assert not any(k in row for k in ("total", "unit_cost", "amount", "lines"))
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4")
    with SessionLocal() as db:
        db.add(ItemFileDB(item_type="po", item_ref=str(po_id), filename="MTR heat HT-4411.pdf", stored_path=str(f), size=8, sha256=uuid.uuid4().hex, content_type="application/pdf", uploaded_by="x"))
        db.commit()
    assert not any(r["po_id"] == po_id for r in client.get("/api/v1/compliance/certs-to-chase", headers=H("OPERATOR")).json()["items"])
    assert client.get("/api/v1/compliance/certs-to-chase", headers=H("VIEWER")).status_code == 403


def test_a_draft_or_cancelled_po_is_not_chased_and_an_unrelated_file_does_not_count(client, tmp_path):
    _, draft, _t = _cert_po(tmp_path, status="DRAFT")
    _, cancelled, _t2 = _cert_po(tmp_path, status="CANCELLED")
    job_id, po_id, tag = _cert_po(tmp_path)
    f = tmp_path / "d.pdf"
    f.write_bytes(b"%PDF-1.4")
    with SessionLocal() as db:
        db.add(ItemFileDB(item_type="po", item_ref=str(po_id), filename="drawing OC-12.pdf", stored_path=str(f), size=8, sha256=uuid.uuid4().hex, content_type="application/pdf", uploaded_by="x"))
        db.commit()
    ids = {r["po_id"] for r in client.get("/api/v1/compliance/certs-to-chase", headers=H("OPERATOR")).json()["items"]}
    assert po_id in ids and draft not in ids and cancelled not in ids


def _order(so):
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"G{tag}", customer_po_number=f"CPG{tag}", sales_order_number=so, customer_id=f"C{tag}", customer_name=f"SlipCo {tag}", total_revenue=100, total_expense=50, gross_margin_pct=50, line_items=[]))
        db.commit()
    return tag


def test_the_orders_list_says_a_packing_slip_exists_when_its_file_is_in_the_slip_folder_even_with_no_stored_path(client):
    n1, n2 = str(70000 + uuid.uuid4().int % 9999), str(80000 + uuid.uuid4().int % 9999)
    t1, t2 = _order(n1), _order(n2)
    folder = Path(settings.OUTPUT_DIR) / "packing_slips"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"packing_slip_SO_{n1}.pdf").write_bytes(b"%PDF-1.4")
    (folder / f"packing_slip_SO_{n1}_extra.pdf").write_bytes(b"%PDF-1.4")                 # a differently named file never matches
    for tag, want in ((t1, True), (t2, False)):
        page = client.get("/api/v1/orders", params={"q": tag}, headers=H("VIEWER")).json()
        assert next(r for r in page["items"] if r["customer_po_number"] == f"CPG{tag}")["has_packing_slip"] is want
    one = client.get(f"/api/v1/orders/{n1}", headers=H("VIEWER")).json()
    assert one["has_packing_slip"] is True
    assert client.get(f"/api/v1/orders/{n2}", headers=H("VIEWER")).json()["has_packing_slip"] is False


def test_check_in_drafts_write_dates_as_people_say_them():
    from app.services.coordination import autopilot

    j = {"id": 1, "po_number": "PO-9", "description": "plates"}
    late = autopilot.watchdog_body(j, {"tier": "OVERDUE", "due_on": "2026-11-16", "days_left": -3})
    soon = autopilot.watchdog_body(j, {"tier": "APPROACHING_3", "due_on": "2026-11-26", "days_left": 3})
    assert "Nov 16" in late and "2026-11-16" not in late and "Nov 26" in soon and "2026-11-26" not in soon


def test_only_certificate_looking_names_count_as_a_certificate_on_file():
    from app.services import certs_to_chase as c

    ok = lambda n: bool(c._CERT_NAME.search(n) and not c._NOT_A_CERT.search(n))  # noqa: E731
    for name in ("MTR heat HT-4411.pdf", "MTR_4411.pdf", "mill test report.pdf", "Mill_Cert_4411.pdf", "CoC.pdf", "certificate of conformance.pdf", "heat-number 12.pdf", "Test Report 55.pdf", "MTC 4411.pdf", "mtc_heat_HT-9.pdf"):
        assert ok(name), name
    for name in ("Heat_exchanger_drawing.pdf", "Certificate_of_insurance.pdf", "certified_drawing.pdf", "wheat_pallet_photo.jpg", "smtrack_export.csv", "certificate_of_origin.pdf", "drawing OC-12.pdf"):
        assert not ok(name), name


def test_a_link_in_the_slip_folder_that_leads_elsewhere_is_not_a_slip(tmp_path):
    import os

    from app.api.v1.endpoints import orders as orders_ep

    folder = Path(settings.OUTPUT_DIR) / "packing_slips"
    folder.mkdir(parents=True, exist_ok=True)
    real = tmp_path / "elsewhere.pdf"
    real.write_bytes(b"%PDF-1.4")
    link = folder / "packing_slip_SO_99991.pdf"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("this machine does not allow links")
    assert "packing_slip_SO_99991.pdf" not in orders_ep._slip_names()


def test_the_shipment_label_reads_plainly_and_the_summary_line_says_who_to_ask():
    from app.services.coordination import shipments

    s = {"id": 3, "reference": "PO 9275", "forwarder": "Pacific Bridge", "origin": "KWS", "destination": "OC-AMARILLO"}
    assert shipments._label(s) == "PO 9275, KWS to OC-AMARILLO" and "(" not in shipments._label(s)


def test_the_sage_tab_dollar_total_uses_the_same_rates_as_the_orders_screen_and_prints_dollars_without_cents(monkeypatch):
    from decimal import Decimal

    from app.services import fx
    from app.services.sage_export_service import SageExportService

    monkeypatch.setattr(fx, "cad_to_usd", lambda: 0.5)
    monkeypatch.setattr(fx, "cny_to_usd", lambda: 0.125)
    assert SageExportService.to_usd(Decimal("100.00"), "CAD") == Decimal("50.00") and SageExportService.to_usd(Decimal("800.00"), "CNY") == Decimal("100.00")
    assert SageExportService.to_usd(Decimal("7.00"), "USD") == Decimal("7.00")

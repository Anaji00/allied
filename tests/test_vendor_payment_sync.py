"""Priority 3: one payment entry updates the job's payment sheet, stages a monday update, rebuilds the xlsx ledger and stages the remittance draft; the same entry twice is one. Temporary database and
output folder; nothing is sent or pushed."""
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, ItemFileDB, JobDB, JobDocumentDB, MondayChangeDB, PurchaseOrderDB, VendorPaymentDB
from app.services import gates, payments, vendor_payments
from tests.conftest import staff_token

MGR = {"email": "mgr.sync@allied.test", "role": "MANAGER"}


def _world(total="1000.00", monday=True, email=True):
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        job = JobDB(ref=f"JOB-SYN-{tag}", title="t", customer="C", monday_board_id="777" if monday else None, monday_item_id=f"it{tag}" if monday else None)
        db.add(job)
        db.commit()
        po = PurchaseOrderDB(job_id=job.id, po_number=f"PO{tag}", vendor=f"Zibo {tag}", vendor_email=f"sales@zibo{tag}.test" if email else None,
                             lines=[{"qty": 1, "item": "x", "unit_cost": total, "amount": total}], status="SENT")
        db.add(po)
        db.commit()
        ids = (job.id, po.id, tag)
    return ids


def _file(tmp_path, name="wire.pdf", data=b"%PDF-1.4 wire copy", po_id=None):
    p = Path(tmp_path) / f"{uuid.uuid4().hex[:6]}-{name}"
    p.write_bytes(data)
    with SessionLocal() as db:
        f = ItemFileDB(item_type="po" if po_id else "vendor_job", item_ref=str(po_id or 1), filename=name, stored_path=str(p), size=len(data), sha256=uuid.uuid4().hex, content_type="application/pdf", uploaded_by="x")
        db.add(f)
        db.commit()
        return f.id


def _due_first(po_id):
    payments.create_schedule(po_id, "30/50/20", MGR, currency="USD")
    return payments.fire(po_id, "PO_ISSUED", MGR)[0]["id"]


def test_one_entry_does_everything_and_the_same_entry_again_does_nothing(tmp_path):
    job_id, po_id, tag = _world()
    tr = _due_first(po_id)
    wire, pro = _file(tmp_path, "wire.pdf", po_id=po_id), _file(tmp_path, "proforma.pdf", po_id=po_id)
    out = vendor_payments.record(tr, MGR, "2026-11-18", method="wire", reference="WT-1", wire_file_id=wire, proforma_file_id=pro)
    assert out["duplicate"] is False and out["payment"]["amount"] == "300.00"
    fx = out["effects"]
    assert fx["sales_sheet"]["ok"] and fx["sales_sheet"]["paid"] == "300.00" and fx["sales_sheet"]["remaining"] == "700.00"
    assert fx["monday"]["ok"] and fx["ledger"]["ok"] and fx["remittance_draft"]["ok"] and sorted(fx["remittance_draft"]["attachments"]) == ["proforma.pdf", "wire.pdf"]
    again = vendor_payments.record(tr, MGR, "2026-11-18", method="wire", reference="WT-1", wire_file_id=wire, proforma_file_id=pro)
    assert again["duplicate"] is True and again["payment"]["id"] == out["payment"]["id"] and again["effects"] == {}
    with SessionLocal() as db:
        assert db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id == tr).count() == 1
        sheets = db.query(JobDocumentDB).filter(JobDocumentDB.job_id == job_id, JobDocumentDB.kind == "PAYMENT_SHEET").all()
        assert len(sheets) == 1 and sheets[0].fields["paid"] == "300.00"
        changes = db.query(MondayChangeDB).filter(MondayChangeDB.item_id == f"it{tag}").all()
        assert len(changes) == 1 and changes[0].status == "STAGED" and "300.00" in changes[0].payload["text"]
        drafts = [a for a in db.query(AssistantActionDB).filter(AssistantActionDB.tool == "send_email") if (a.args or {}).get("_payment_id") == out["payment"]["id"]]
        assert len(drafts) == 1 and drafts[0].status == "PENDING" and drafts[0].args["to"] == f"sales@zibo{tag}.test"
        assert {s["type"] for s in drafts[0].args["_attach"]} == {"payment_doc"} and "Allied PO" in drafts[0].args["body"]


def test_the_remittance_attaches_only_files_a_recorded_payment_names(tmp_path):
    job_id, po_id, tag = _world()
    tr = _due_first(po_id)
    wire = _file(tmp_path, po_id=po_id)
    stranger = _file(tmp_path, "customer-drawing.pdf", po_id=po_id)
    vendor_payments.record(tr, MGR, "2026-11-18", wire_file_id=wire, draft_remittance=False)
    assert vendor_payments.payment_doc_bytes(wire)["content"].startswith(b"%PDF")
    with pytest.raises(payments.PaymentError):
        vendor_payments.payment_doc_bytes(stranger)
    from app.services import actions

    got = actions.resolve_attachments([{"type": "payment_doc", "id": wire}], "vendor", MGR)
    assert got[0]["filename"] == "wire.pdf"
    with pytest.raises(actions.ActionError):
        actions.resolve_attachments([{"type": "payment_doc", "id": stranger}], "vendor", MGR)
    with pytest.raises(actions.ActionError):
        actions.resolve_attachments([{"type": "payment_doc", "id": wire}], "customer", MGR)


def test_two_simultaneous_identical_entries_make_one_payment_and_one_ledger_row(tmp_path):
    _, po_id, _ = _world()
    tr = _due_first(po_id)
    results, errs = [], []

    def go():
        try:
            results.append(vendor_payments.record(tr, MGR, "2026-11-18", draft_remittance=False))
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for r in results if not r["duplicate"]) == 1
    assert all(isinstance(e, payments.PaymentError) and e.status == 409 for e in errs)
    with SessionLocal() as db:
        assert db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id == tr).count() == 1


def test_a_payment_that_is_not_due_wrong_amount_wrong_date_or_a_missing_file_is_refused_and_records_nothing(tmp_path):
    _, po_id, _ = _world("1000.00")
    payments.create_schedule(po_id, "30/70", MGR)
    tranches = payments.get_for_po(po_id)["tranches"]
    with pytest.raises(payments.PaymentError) as e:
        vendor_payments.record(tranches[0]["id"], MGR, "2026-11-18")                       # the PO has not been sent: not due
    assert e.value.status == 409
    payments.fire(po_id, "PO_ISSUED", MGR)
    with pytest.raises(payments.PaymentError) as e:
        vendor_payments.record(tranches[1]["id"], MGR, "2026-11-18")                       # the Gate 2 payment cannot go out before Gate 2
    assert e.value.status == 409 and "Gate 2" in e.value.message
    for kwargs in ({"paid_on": "2026-02-30"}, {"paid_on": "18/11/2026"}, {"paid_on": "2026-11-18", "amount": "299.99"}, {"paid_on": "2026-11-18", "amount": "abc"},
                   {"paid_on": "2026-11-18", "method": "bitcoin"}, {"paid_on": "2026-11-18", "wire_file_id": 99999999}):
        with pytest.raises(payments.PaymentError):
            vendor_payments.record(tranches[0]["id"], MGR, **kwargs)
    with SessionLocal() as db:
        assert db.query(VendorPaymentDB).count() == 0 or all(p.tranche_id != tranches[0]["id"] for p in db.query(VendorPaymentDB))
    assert payments.get_for_po(po_id)["tranches"][0]["status"] == "DUE"


def test_the_ledger_has_one_sheet_per_vendor_the_right_totals_and_is_swapped_in_atomically():
    _, po1, tag1 = _world("1000.00")
    _, po2, tag2 = _world("500.00")
    t1, t2 = _due_first(po1), None
    payments.create_schedule(po2, "50/50", MGR)
    t2 = payments.fire(po2, "PO_ISSUED", MGR)[0]["id"]
    vendor_payments.record(t1, MGR, "2026-11-18", draft_remittance=False)
    vendor_payments.record(t2, MGR, "2026-11-19", draft_remittance=False)
    path = vendor_payments.ledger_path()
    assert path.exists() and not list(path.parent.glob(".ledger.*.tmp"))
    wb = load_workbook(path)
    assert f"Zibo {tag1}" in wb.sheetnames and f"Zibo {tag2}" in wb.sheetnames and wb.sheetnames[0] == "Summary"
    ws = wb[f"Zibo {tag1}"]
    assert [c.value for c in ws[1]][:4] == ["Date", "PO", "Payment", "Amount"] and ws.max_row == 2 and ws.cell(2, 4).value == 300.0
    totals = {r[0].value: r[3].value for r in wb["Summary"].iter_rows(min_row=2)}
    assert totals[f"Zibo {tag1}"] == 300.0 and totals[f"Zibo {tag2}"] == 250.0
    vendor_payments.write_ledger()                                                              # rebuilding changes nothing
    assert load_workbook(path)[f"Zibo {tag1}"].max_row == 2


def test_a_job_with_no_monday_link_or_no_vendor_email_still_records_and_says_what_was_skipped():
    _, po_id, _ = _world(monday=False, email=False)
    tr = _due_first(po_id)
    out = vendor_payments.record(tr, MGR, "2026-11-18")
    assert out["effects"]["monday"]["ok"] is False and out["effects"]["remittance_draft"]["ok"] is False and out["effects"]["ledger"]["ok"] is True
    assert out["payment"]["amount"] == "300.00"


def test_the_api_records_a_payment_for_a_manager_only_and_a_repeat_says_duplicate(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    h = lambda email, role: {"Authorization": "Bearer " + staff_token(email, role)}  # noqa: E731
    _, po_id, _ = _world()
    tr = _due_first(po_id)
    body = {"paid_on": "2026-11-18", "reference": "WT-9", "draft_remittance": False}
    assert c.post(f"/api/v1/payments/tranche/{tr}/record", json=body, headers=h("ops.sync@allied.test", "OPERATOR")).status_code == 403
    first = c.post(f"/api/v1/payments/tranche/{tr}/record", json=body, headers=h("m.sync@allied.test", "MANAGER"))
    second = c.post(f"/api/v1/payments/tranche/{tr}/record", json=body, headers=h("m.sync@allied.test", "MANAGER"))
    assert first.status_code == 200 and first.json()["duplicate"] is False and second.json()["duplicate"] is True
    assert c.get(f"/api/v1/payments/po/{po_id}/payments", headers=h("m.sync@allied.test", "MANAGER")).json()["payments"][0]["amount"] == "300.00"
    assert c.get("/api/v1/payments/due", headers=h("m.sync@allied.test", "MANAGER")).status_code == 200

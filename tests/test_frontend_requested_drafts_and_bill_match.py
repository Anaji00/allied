"""Three read-or-draft routes the screens asked for: a payment note to the vendor, a freight quote request to the forwarder, and the PO next to its checked bills. Temporary database; nothing is sent."""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, ItemFileDB, ReconciliationDB
from app.services import actions, payments
from tests.conftest import staff_token
from tests.test_vendor_payment_sync import MGR, _file, _world


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def H(role="MANAGER"):
    return {"Authorization": "Bearer " + staff_token(f"drafts-{role.lower()}@alliedweldingsystems.com", role)}


def _tranche(po_id):
    payments.create_schedule(po_id, "30/50/20", MGR, currency="USD")
    return payments.fire(po_id, "PO_ISSUED", MGR)[0]["id"]


def _draft(action_id):
    with SessionLocal() as db:
        return db.get(AssistantActionDB, action_id)


def test_payment_note_names_vendor_po_and_amount_and_attaches_only_the_jobs_own_files(client, tmp_path):
    job_id, po_id, tag = _world()
    tr = _tranche(po_id)
    wire = _file(tmp_path, "wire.pdf", po_id=po_id)
    stranger = _file(tmp_path, "other.pdf", po_id=po_id + 999)
    r = client.post(f"/api/v1/payments/tranche/{tr}/email-draft", json={"note": "Wire goes out Friday.", "wire_file_id": wire}, headers=H())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["drafted"] and out["amount"] == "300.00" and out["po_number"] == f"PO{tag}" and out["attached"] == ["wire.pdf"] and out["to_add_by_hand"] == ["proforma invoice"]
    a = _draft(out["action_id"])
    assert a.status == "PENDING" and a.args["to"] == f"sales@zibo{tag}.test" and "300.00" in a.args["body"] and "Wire goes out Friday." in a.args["body"]
    assert [s["type"] for s in a.args["_attach"]] == ["po_file"]
    files = actions.resolve_attachments(a.args["_attach"], "vendor", MGR)                                  # the file really resolves, and only for its own PO
    assert files[0]["filename"] == "wire.pdf"
    bad = client.post(f"/api/v1/payments/tranche/{tr}/email-draft", json={"wire_file_id": stranger}, headers=H())
    assert bad.status_code == 422 and "does not belong" in bad.json()["detail"]
    with pytest.raises(Exception):
        actions.resolve_attachments([{"type": "po_file", "id": stranger, "po": po_id}], "vendor", MGR)
    assert client.post(f"/api/v1/payments/tranche/{tr}/email-draft", json={}, headers=H("OPERATOR")).status_code == 403
    assert client.post("/api/v1/payments/tranche/999999/email-draft", json={}, headers=H()).status_code == 404


def test_payment_note_needs_a_vendor_email(client):
    job_id, po_id, tag = _world(email=False)
    tr = _tranche(po_id)
    r = client.post(f"/api/v1/payments/tranche/{tr}/email-draft", json={}, headers=H())
    assert r.status_code == 422 and "email" in r.json()["detail"].lower()


QUOTE = "Quotation Q-1\nGross weight: 1,250 kg\nDimensions: 120 x 80 x 100 cm\n12 crates\nPrice USD 9,999.00\n"


def test_freight_request_fills_cargo_from_the_quote_lists_blanks_and_never_names_the_customer(client, tmp_path):
    job_id, po_id, tag = _world()
    r = client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "text": QUOTE, "forwarder_email": "quotes@forwarder.test", "origin": "Ningbo", "destination": "Houston"}, headers=H("OPERATOR"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["drafted"] and out["missing"] == []
    body = _draft(out["action_id"]).args["body"]
    assert "1250 kg" in body and "120 x 80 x 100 cm" in body and "Packages: 12" in body and "9,999" not in body and "Zibo" not in body
    only_weight = client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "text": "Gross weight: 900 kg", "forwarder_email": "quotes@forwarder.test"}, headers=H("OPERATOR")).json()
    assert set(only_weight["missing"]) == {"dimensions", "packages", "origin", "destination"}
    assert "(to be filled in)" in _draft(only_weight["action_id"]).args["body"]
    from app.models.db_models import JobDB

    with SessionLocal() as db:
        db.get(JobDB, job_id).customer = f"Acme Boilers {tag}"
        db.commit()
    named = client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "text": QUOTE, "forwarder_email": "quotes@forwarder.test", "destination": f"Acme Boilers {tag} plant"}, headers=H("OPERATOR"))
    assert named.status_code == 422 and "customer" in named.json()["detail"]
    assert client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "text": QUOTE, "forwarder_email": "not-an-email"}, headers=H("OPERATOR")).status_code == 422
    assert client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "text": QUOTE, "forwarder_email": "q@f.test"}, headers=H("VIEWER")).status_code == 403
    assert client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": 999999, "forwarder_email": "q@f.test"}, headers=H("OPERATOR")).status_code == 404


def test_freight_request_reads_a_text_file_of_the_pos_job_and_refuses_any_other_file(client, tmp_path):
    job_id, po_id, tag = _world()
    ok = _file(tmp_path, "quote.txt", data=QUOTE.encode(), po_id=po_id)
    other = _file(tmp_path, "secret.txt", data=QUOTE.encode(), po_id=po_id + 999)
    r = client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "quote_file_id": ok, "forwarder_email": "q@f.test", "destination": "Houston"}, headers=H("OPERATOR"))
    assert r.status_code == 200 and "1250 kg" in _draft(r.json()["action_id"]).args["body"]
    bad = client.post("/api/v1/shipments/freight-rfq-draft", json={"po_id": po_id, "quote_file_id": other, "forwarder_email": "q@f.test"}, headers=H("OPERATOR"))
    assert bad.status_code == 422 and "not a file of this PO" in bad.json()["detail"]


def test_bill_match_puts_po_lines_beside_the_checked_bills_and_hides_prices_from_view_only(client):
    job_id, po_id, tag = _world("500.00")
    with SessionLocal() as db:
        db.add(ReconciliationDB(reconciliation_id=uuid.uuid4().hex[:16], invoice_number=f"INV{tag}", vendor_name=f"Zibo {tag}", po_number=f"PO{tag}", reconciliation_status="DISPUTED",
                                authorized_po_total=500, actual_billed_total=640, cost_variance_amount=140, cost_variance_pct=28,
                                discrepancies=["Quantity Variance on X1: Billed 3 vs PO 1", "Price Surcharge on X1: Billed $210.00 vs PO $150.00 (+$60.00/unit)", "Unauthorized Freight Add-On: Billed $195.00 vs Agreed $125.00 (+$70.00)", "something new"],
                                suggested_action="Dispute"))
        db.add(ReconciliationDB(reconciliation_id=uuid.uuid4().hex[:16], invoice_number="OTHER", vendor_name="V", po_number="PO-OTHER", reconciliation_status="APPROVED", discrepancies=[]))
        db.commit()
    r = client.get(f"/api/v1/purchase-orders/{po_id}/bill-match", headers=H("OPERATOR"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["po_number"] == f"PO{tag}" and len(out["bills"]) == 1 and out["has_bills"] and out["po_lines"]
    b = out["bills"][0]
    assert b["billed_total"] == "640.00" and b["variance"] == "140.00" and b["status"] == "DISPUTED"
    kinds = {f["kind"]: f for f in b["findings"]}
    assert kinds["quantity"]["variance"] == "2" and kinds["unit_price"]["variance"] == "60.00" and kinds["freight"]["variance"] == "70.00" and kinds["other"]["text"] == "something new"
    assert client.get(f"/api/v1/purchase-orders/{po_id}/bill-match", headers=H("VIEWER")).status_code == 403
    nobody = client.get("/api/v1/purchase-orders/999999/bill-match", headers=H("OPERATOR"))
    assert nobody.status_code == 404
    j2, po2, tag2 = _world()
    empty = client.get(f"/api/v1/purchase-orders/{po2}/bill-match", headers=H("OPERATOR")).json()
    assert empty["bills"] == [] and not empty["has_bills"] and "No supplier bill" in empty["note"]


def test_freight_request_says_so_in_plain_words_when_the_quote_file_is_missing_damaged_or_locked(client, tmp_path):
    job_id, po_id, tag = _world()
    body = {"po_id": po_id, "forwarder_email": "q@f.test"}
    gone = _file(tmp_path, "gone.txt", data=b"x", po_id=po_id)
    with SessionLocal() as db:
        Path(db.get(ItemFileDB, gone).stored_path).unlink()
    bad = _file(tmp_path, "bad.pdf", data=b"%PDF-1.4 not really a pdf", po_id=po_id)
    for fid in (gone, bad):
        r = client.post("/api/v1/shipments/freight-rfq-draft", json={**body, "quote_file_id": fid}, headers=H("OPERATOR"))
        assert r.status_code == 422 and "could not be read" in r.json()["detail"], r.text


def test_the_outbox_holds_whole_mail_bodies_so_it_is_for_managers_only(client):
    assert client.get("/api/v1/automation/outbox", headers=H("MANAGER")).status_code == 200
    for role in ("OPERATOR", "VIEWER"):
        assert client.get("/api/v1/automation/outbox", headers=H(role)).status_code == 403

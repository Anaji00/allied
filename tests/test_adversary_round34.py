"""Round 34 (adversary): POST /payments/tranche/{id}/email-draft, POST /shipments/freight-rfq-draft, GET /purchase-orders/{po}/bill-match. Temporary database and files only; synthetic PDFs; nothing is sent
(drafts wait for approval; attachments are only resolved, never mailed). xfail(strict=False, reason="ADV34-Tn ...") marks holes."""
import io
import uuid
from decimal import Decimal

import fitz
import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, ItemFileDB, ReconciliationDB
from app.services import actions, payments
from app.services.coordination import files, jobs, purchase_orders, rfqs
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss34-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
CUSTOMER = f"Zorbex Industries {TAG}"
ORDER = ["VIEWER", "OPERATOR", "MANAGER", "ADMIN"]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"a34-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ORDER}

    def call(method, path, role="MANAGER", **kw):
        return c.request(method, "/api/v1" + path, headers=({} if role is None else heads[role]), **kw)

    return call


def _pdf(text):
    d = fitz.open()
    d.new_page().insert_text((40, 80), text, fontsize=12)
    return d.tobytes()


def _job(customer=CUSTOMER, vendor_email=True, vendor=None):
    r = rfqs.create({"customer": customer, "subject": f"r34 {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], vendor or f"V34 {uuid.uuid4().hex[:5]}", BOSS, lines=[{"description": "plate", "quantity": 1, "unit_cost": 4321.09}])
    if vendor_email:
        purchase_orders.update(po["id"], {"vendor_email": f"sales{uuid.uuid4().hex[:5]}@vendor-example.test"}, BOSS)
    po = purchase_orders.approve(po["id"], BOSS)
    return r, j, po


def _tranche(po_id, total=1000):
    payments.create_schedule(po_id, "50/50", BOSS, total=total)
    payments.fire(po_id, "PO_ISSUED")
    return payments.get_for_po(po_id)["tranches"][0]["id"]


def _action(action_id):
    with SessionLocal() as db:
        a = db.get(AssistantActionDB, action_id)
        return dict(a.args or {}), a.status, a.summary


# =============================================================== (1) tranche email draft

def test_tranche_draft_is_manager_only_and_unknown_or_vendorless_things_are_plain_errors(api):
    _r, _j, po = _job()
    tr = _tranche(po["id"])
    assert api("post", f"/payments/tranche/{tr}/email-draft", role=None, json={}).status_code in (401, 403)
    for role in ("VIEWER", "OPERATOR"):
        assert api("post", f"/payments/tranche/{tr}/email-draft", role=role, json={}).status_code == 403
    assert api("post", "/payments/tranche/99999999/email-draft", json={}).status_code == 404
    _r2, _j2, po2 = _job(vendor_email=False)
    tr2 = _tranche(po2["id"])
    assert api("post", f"/payments/tranche/{tr2}/email-draft", json={}).status_code == 422
    ok = api("post", f"/payments/tranche/{tr}/email-draft", json={"note": "Thanks"})
    assert ok.status_code == 200 and ok.json()["drafted"] is True and "Nothing was sent" in ok.json()["message"]
    args, status, _s = _action(ok.json()["action_id"])
    assert status == "PENDING" and args["_audience"] == "vendor"


def test_tranche_draft_body_names_only_this_po_and_step_never_the_customer_or_other_vendors(api):
    r, j, po = _job()
    r2, j2, po2 = _job(vendor="OtherVendor34 Ltd")
    tr = _tranche(po["id"])
    out = api("post", f"/payments/tranche/{tr}/email-draft", json={}).json()
    args, _s, _x = _action(out["action_id"])
    low = (args["body"] + args["subject"]).lower()
    assert "zorbex" not in low and "othervendor34" not in low and "4321" not in low and "$" not in low
    assert str(po["po_number"] or po["id"]).lower() in low


@pytest.mark.parametrize("which", ["another_jobs_rfq_file", "deleted", "missing_number"])
def test_a_file_that_is_not_this_pos_is_refused_or_not_attached(api, which):
    _r, _j, po = _job()
    r2, j2, po2 = _job()
    tr = _tranche(po["id"])
    if which == "another_jobs_rfq_file":
        fid = files.add("rfq", r2["number"], "wire.pdf", _pdf("wire"), BOSS)["id"]
    elif which == "deleted":
        fid = files.add("rfq", _r["number"], "wire.pdf", _pdf("wire"), BOSS)["id"]
        files.remove(fid, BOSS)
    else:
        fid = 99999999
    r = api("post", f"/payments/tranche/{tr}/email-draft", json={"wire_file_id": fid})
    assert r.status_code == 422, (which, r.status_code, r.text[:200])


def test_d1_a_customers_own_file_cannot_ride_along_as_a_wire_copy(api):
    r, j, po = _job()
    tr = _tranche(po["id"])
    fid = files.add("rfq", r["number"], "customer-po.pdf", _pdf(f"Purchase order from {CUSTOMER}, plant 7"), BOSS)["id"]
    out = api("post", f"/payments/tranche/{tr}/email-draft", json={"proforma_file_id": fid})
    assert out.status_code == 422
    if out.status_code == 200:
        args, _s, _x = _action(out.json()["action_id"])
        got = actions.resolve_attachments(args["_attach"], "vendor", BOSS)
        assert CUSTOMER.encode() not in b"".join(g["content"] for g in got)


def test_d2_another_vendors_job_file_is_not_attachable(api):
    r, j, po = _job(vendor="VendorA34")
    po_b = purchase_orders.create(j["id"], "VendorB34 Ltd", BOSS, lines=[{"description": "other", "quantity": 1, "unit_cost": 999}])
    tr = _tranche(po["id"])
    fid = files.add("rfq", r["number"], "vendorB-quote.pdf", _pdf("VendorB34 quote total $999.00"), BOSS)["id"]
    out = api("post", f"/payments/tranche/{tr}/email-draft", json={"wire_file_id": fid})
    assert out.status_code == 422


def test_d3_the_note_is_checked_for_the_customers_name(api):
    r, j, po = _job()
    tr = _tranche(po["id"])
    out = api("post", f"/payments/tranche/{tr}/email-draft", json={"note": f"This is the deposit for {CUSTOMER}"})
    if out.status_code == 200:
        args, _s, summary = _action(out.json()["action_id"])
        assert CUSTOMER.lower() not in args["body"].lower() or args.get("_warnings")
    else:
        assert out.status_code == 422


def test_a_po_file_resolves_only_for_its_own_po_and_not_after_it_is_deleted(api):
    r, j, po = _job()
    r2, j2, po2 = _job()
    tr = _tranche(po["id"])
    import tempfile, pathlib
    pth = pathlib.Path(tempfile.mkdtemp()) / "proforma.pdf"
    pth.write_bytes(_pdf("proforma only"))
    with SessionLocal() as db:
        row = ItemFileDB(item_type="po", item_ref=str(po["id"]), filename="proforma.pdf", stored_path=str(pth), size=pth.stat().st_size, sha256=uuid.uuid4().hex, content_type="application/pdf", uploaded_by="x")
        db.add(row)
        db.commit()
        fid = row.id
    out = api("post", f"/payments/tranche/{tr}/email-draft", json={"proforma_file_id": fid}).json()
    args, _s, _x = _action(out["action_id"])
    got = actions.resolve_attachments(args["_attach"], "vendor", BOSS)
    assert got and got[0]["filename"] == "proforma.pdf"
    swapped = [{**args["_attach"][0], "po": po2["id"]}]
    with pytest.raises(actions.ActionError):
        actions.resolve_attachments(swapped, "vendor", BOSS)
    for bad in ([{"type": "po_file", "id": fid}], [{"type": "po_file", "id": "x", "po": po["id"]}], [{"type": "po_file", "id": fid, "po": None}]):
        with pytest.raises(actions.ActionError):
            actions.resolve_attachments(bad, "vendor", BOSS)
    files.remove(fid, BOSS)
    with pytest.raises(actions.ActionError):
        actions.resolve_attachments(args["_attach"], "vendor", BOSS)


# =============================================================== (2) freight RFQ draft

def _fr(api, role="OPERATOR", **body):
    base = {"forwarder_email": "fwd@forwarder-example.test", "origin": "Shenzhen", "destination": "Houston", "text": "Gross weight: 1,250 kg. Dimensions: 120 x 80 x 95 cm. 4 crates."}
    base.update(body)
    return api("post", "/shipments/freight-rfq-draft", role=role, json=base)


def test_freight_draft_role_and_plain_errors(api):
    assert _fr(api, role=None).status_code in (401, 403)
    assert _fr(api, role="VIEWER").status_code == 403
    assert _fr(api, po_id=99999999).status_code == 404
    assert _fr(api, text="x" * 100001).status_code == 422
    for bad in ("", "a", "a@b", "a b@c.com", "a@b.com,c@d.com", "<a@b.com>", "a@b.com\nBcc: x@y.test", "a@b.com;c@d.com"):
        assert _fr(api, forwarder_email=bad).status_code == 422, bad
    assert _fr(api, text="", po_id=None).status_code == 422


@pytest.mark.parametrize("place", [CUSTOMER, CUSTOMER.upper(), CUSTOMER.lower(), "Z o r b e x Industries", "Zorbex-Industries", "Zörbex Industries", "Zorbex​ Industries",
                                   "c/o ZORBEX INDUSTRIES, dock 4"], ids=range(8))
def test_a_customer_name_in_the_origin_or_destination_is_refused_in_any_spelling(api, place):
    r, j, po = _job()
    for field in ("origin", "destination"):
        out = _fr(api, po_id=po["id"], **{field: place})
        assert out.status_code == 422, (field, place, out.status_code, out.text[:160])


def test_the_body_never_carries_prices_the_vendor_or_the_customer_even_if_the_quote_does(api):
    r, j, po = _job(vendor="SecretVendor34 Co")
    text = f"Quote for {CUSTOMER} from SecretVendor34 Co. Price $48,000.00 total. Gross weight: 1,250 kg. Dimensions: 120 x 80 x 95 cm. 4 crates. PO {po['po_number']}"
    out = _fr(api, po_id=po["id"], text=text)
    assert out.status_code == 200, out.text[:200]
    args, status, _s = _action(out.json()["action_id"])
    low = args["body"].lower()
    assert status == "PENDING" and args["_audience"] == "forwarder"
    assert "zorbex" not in low and "secretvendor34" not in low and "48,000" not in low and "$" not in low and str(po["po_number"]).lower() not in low


@pytest.mark.parametrize("which", ["other_jobs_file", "deleted", "no_po", "missing_on_disk", "corrupt_pdf", "encrypted_pdf", "huge_text_file"])
def test_the_quote_file_must_be_this_pos_and_odd_files_are_a_plain_error_not_a_500(api, which):
    r, j, po = _job()
    r2, j2, po2 = _job()
    if which == "other_jobs_file":
        fid = files.add("rfq", r2["number"], "q.pdf", _pdf("Gross weight: 10 kg. Dimensions: 1 x 1 x 1 m"), BOSS)["id"]
        out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
        want = (422,)
    elif which == "deleted":
        fid = files.add("rfq", r["number"], "q.pdf", _pdf("Gross weight: 10 kg"), BOSS)["id"]
        files.remove(fid, BOSS)
        out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
        want = (422,)
    elif which == "no_po":
        fid = files.add("rfq", r["number"], "q.pdf", _pdf("Gross weight: 10 kg"), BOSS)["id"]
        out = _fr(api, po_id=None, text="", quote_file_id=fid)
        want = (422,)
    elif which == "missing_on_disk":
        import os

        fid = files.add("rfq", r["number"], "q.pdf", _pdf("Gross weight: 10 kg"), BOSS)["id"]
        with SessionLocal() as db:
            os.remove(db.get(ItemFileDB, fid).stored_path)
        out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
        want = (404, 422)
    elif which == "corrupt_pdf":
        fid = files.add("rfq", r["number"], "q.pdf", b"%PDF-1.4\n" + b"x" * 300, BOSS)["id"]
        out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
        want = (200, 422)
    elif which == "encrypted_pdf":
        d = fitz.open()
        d.new_page().insert_text((40, 40), "Gross weight: 10 kg")
        fid = files.add("rfq", r["number"], "q.pdf", d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u"), BOSS)["id"]
        out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
        want = (200, 422)
    else:
        fid = files.add("rfq", r["number"], "q.txt", (b"filler words " * 200000), BOSS)["id"]
        out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
        want = (200, 422)
    assert out.status_code in want, (which, out.status_code, out.text[:200])


def test_f1_a_damaged_missing_or_huge_quote_file_never_500s(api):
    r, j, po = _job()
    import os

    fid = files.add("rfq", r["number"], "q.pdf", b"%PDF-1.4\n" + b"x" * 300, BOSS)["id"]
    out = _fr(api, po_id=po["id"], text="", quote_file_id=fid)
    assert out.status_code < 500
    fid2 = files.add("rfq", r["number"], "q2.pdf", _pdf("Gross weight: 10 kg"), BOSS)["id"]
    with SessionLocal() as db:
        os.remove(db.get(ItemFileDB, fid2).stored_path)
    assert _fr(api, po_id=po["id"], text="", quote_file_id=fid2).status_code < 500


# =============================================================== (3) bill match

def _bill(po_number, vendor="V", amount="100.00", invoice=None):
    with SessionLocal() as db:
        db.add(ReconciliationDB(reconciliation_id=uuid.uuid4().hex[:32], invoice_number=invoice or f"INV-{uuid.uuid4().hex[:6]}", vendor_name=vendor, po_number=po_number, reconciliation_status="MATCHED",
                                authorized_po_total=Decimal(amount), actual_billed_total=Decimal(amount), cost_variance_amount=Decimal("0"), cost_variance_pct=Decimal("0"), discrepancies=[]))
        db.commit()


def test_bill_match_needs_staff_and_an_unknown_po_is_404(api):
    r, j, po = _job()
    assert api("get", f"/purchase-orders/{po['id']}/bill-match", role=None).status_code in (401, 403)
    assert api("get", f"/purchase-orders/{po['id']}/bill-match", role="VIEWER").status_code == 403
    assert api("get", "/purchase-orders/99999999/bill-match", role="OPERATOR").status_code == 404
    assert api("get", f"/purchase-orders/{po['id']}/bill-match", role="OPERATOR").status_code == 200


def test_bills_match_only_this_po_number_with_case_and_outer_whitespace_ignored():
    r, j, po = _job()
    r2, j2, po2 = _job()
    n = po["po_number"]
    from app.services import bill_match as bm

    _bill(f" {str(n).lower()} ", vendor="Mine", invoice="MINE-1")
    _bill(po2["po_number"], vendor="Theirs", invoice="THEIRS-1")
    _bill(f"{n}9", vendor="Longer", invoice="LONGER-1")
    _bill(f"X{n}", vendor="Prefixed", invoice="PREFIX-1")
    out = bm.for_po(po["id"], purchase_orders.get(po["id"]))
    inv = {b["invoice_number"] for b in out["bills"]}
    assert "MINE-1" in inv and not {"THEIRS-1", "LONGER-1", "PREFIX-1"} & inv


def test_b1_a_bill_from_another_vendor_with_this_po_number_is_not_shown_as_a_match():
    r, j, po = _job(vendor="RealVendor34")
    from app.services import bill_match as bm

    _bill(po["po_number"], vendor="Different Supplier Inc", invoice="OTHERVENDOR-1")
    out = bm.for_po(po["id"], purchase_orders.get(po["id"]))
    shown = [b for b in out["bills"] if b["invoice_number"] == "OTHERVENDOR-1"]
    assert not shown or all(b.get("vendor_mismatch") for b in shown)


def test_b2_an_older_pos_bills_are_found_after_many_newer_bills():
    r, j, po = _job()
    from datetime import datetime, timedelta, timezone

    from app.services import bill_match as bm

    _bill(po["po_number"], invoice="OLD-ONE")
    with SessionLocal() as db:
        row = db.query(ReconciliationDB).filter(ReconciliationDB.invoice_number == "OLD-ONE").one()
        row.reconciled_at = datetime.now(timezone.utc) - timedelta(days=400)
        db.add_all([ReconciliationDB(reconciliation_id=uuid.uuid4().hex[:32], invoice_number=f"N-{i}", vendor_name="x", po_number=f"ZZ-{i}", reconciliation_status="MATCHED", discrepancies=[]) for i in range(520)])
        db.commit()
    out = bm.for_po(po["id"], purchase_orders.get(po["id"]))
    assert any(b["invoice_number"] == "OLD-ONE" for b in out["bills"])


def test_a_po_without_a_number_matches_nothing_and_says_so():
    r, j, po = _job(vendor_email=False)
    po_draft = purchase_orders.create(j["id"], "Draft Vendor34", BOSS, lines=[{"description": "x", "quantity": 1, "unit_cost": 1}])
    from app.services import bill_match as bm

    _bill("", vendor="Blank", invoice="BLANK-1")
    out = bm.for_po(po_draft["id"], purchase_orders.get(po_draft["id"]))
    assert out["bills"] == [] and "no number" in out["note"].lower()


def test_bill_match_trims_prices_like_the_po_for_a_view_only_role_and_never_500s_on_odd_sentences():
    r, j, po = _job()
    from app.services import bill_match as bm

    sentences = ["Quantity Variance on " + "x" * 5000 + ": Billed 1,0 vs PO 2..", "Price Surcharge on A: Billed $abc vs PO $1.2.3", "", None, 7, "Unexpected item SKU on invoice: Z (Not on authorized PO)"]
    assert len(bm.findings(sentences)) == len(sentences)

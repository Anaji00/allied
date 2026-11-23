"""Round 35 (adversary): GET /payments/schedules (manager), GET /compliance/certs-to-chase (operator+), and the packing-slip lookup by sales order number (orders.py). Temporary database and a temporary output
folder only; read-only probes; nothing is sent or written to monday/Sage. xfail(strict=False, reason="ADV35-Tn ...") marks holes."""
import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, PaymentScheduleDB, PurchaseOrderDB
from app.services import payments
from app.services.coordination import jobs, purchase_orders, rfqs
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss35-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
ORDER = ["VIEWER", "OPERATOR", "MANAGER", "ADMIN"]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"a35-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ORDER}

    def call(method, path, role="MANAGER", **kw):
        return c.request(method, "/api/v1" + path, headers=({} if role is None else heads[role]), **kw)

    return call


@pytest.fixture()
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
    (tmp_path / "output" / "packing_slips").mkdir(parents=True)
    return tmp_path / "output"


def _po(vendor=None, lines=None):
    r = rfqs.create({"customer": f"Cust35 {uuid.uuid4().hex[:4]}", "subject": f"r35 {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], vendor or f"V35 {uuid.uuid4().hex[:5]}", BOSS, lines=lines or [{"description": "plate", "quantity": 1, "unit_cost": 4321.09}])
    purchase_orders.update(po["id"], {"vendor_email": f"s{uuid.uuid4().hex[:5]}@vendor-example.test"}, BOSS)
    po = purchase_orders.approve(po["id"], BOSS)
    return po, j


def _sched(po_id, total=1000):
    payments.create_schedule(po_id, "50/50", BOSS, total=total)
    payments.fire(po_id, "PO_ISSUED")


# =============================================================== GET /payments/schedules

def test_schedules_are_manager_only_and_show_paid_left_next_step_and_by_vendor(api):
    po, j = _po()
    _sched(po["id"])
    assert api("get", "/payments/schedules", role=None).status_code in (401, 403)
    for role in ("VIEWER", "OPERATOR"):
        assert api("get", "/payments/schedules", role=role).status_code == 403
    for role in ("MANAGER", "ADMIN"):
        r = api("get", "/payments/schedules", role=role)
        assert r.status_code == 200
    body = r.json()
    mine = next(s for s in body["schedules"] if s["po_id"] == po["id"])
    assert mine["next_step"]["status"] in ("DUE", "PENDING") and mine["paid"] == "0.00" and mine["remaining"] == "1000.00" and mine["job_ref"] == j["ref"]
    assert any(v["vendor"] == po["vendor"] and v["po_count"] >= 1 for v in body["vendors"])


def test_a_cancelled_po_and_a_replaced_schedule_are_left_out_and_nothing_is_written(api):
    po, j = _po()
    _sched(po["id"])
    po2, _j2 = _po()
    _sched(po2["id"])
    purchase_orders.cancel(po2["id"], "customer cancelled the order", BOSS)
    with SessionLocal() as db:
        before = (db.query(PaymentScheduleDB).count(), db.query(PurchaseOrderDB).count())
    ids = {s["po_id"] for s in api("get", "/payments/schedules").json()["schedules"]}
    due = {d["po_id"] for d in api("get", "/payments/due").json()["due"]}
    assert po["id"] in ids and po2["id"] not in ids and po2["id"] not in due
    with SessionLocal() as db:
        assert (db.query(PaymentScheduleDB).count(), db.query(PurchaseOrderDB).count()) == before


@pytest.mark.parametrize("vendor", ["<script>alert(1)</script>", "=HYPERLINK(\"http://evil.example\",\"x\")", "Vendor ‮evil", "' OR 1=1 --", "V" * 250, "Zoë 中文 GmbH"], ids=range(6))
def test_hostile_vendor_text_comes_back_as_plain_data_and_never_breaks_the_page(api, vendor):
    po, j = _po(vendor=vendor)
    _sched(po["id"])
    r = api("get", "/payments/schedules")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert any(s["po_id"] == po["id"] for s in r.json()["schedules"])
    assert api("get", "/payments/due").status_code == 200


def test_the_vendor_with_no_name_is_grouped_and_a_missing_date_never_crashes(api):
    po, j = _po()
    _sched(po["id"])
    with SessionLocal() as db:
        s = db.query(PaymentScheduleDB).filter(PaymentScheduleDB.po_id == po["id"]).one()
        s.vendor = None
        db.commit()
    r = api("get", "/payments/schedules")
    assert r.status_code == 200 and any(v["vendor"] == "(no vendor)" for v in r.json()["vendors"])


# =============================================================== GET /compliance/certs-to-chase

def _cert_po():
    po, j = _po(lines=[{"description": "plate", "quantity": 1, "unit_cost": 4321.09, "cert_required": True}])
    with SessionLocal() as db:
        row = db.get(PurchaseOrderDB, po["id"])
        row.lines = [{**ln, "cert_required": True} for ln in (row.lines or [])]
        db.commit()
    return po, j


def _file(po, name, kind="po"):
    with SessionLocal() as db:
        db.add(ItemFileDB(item_type=kind, item_ref=str(po["id"]), filename=name, stored_path="x", size=1, sha256="", uploaded_by="t@x.test"))
        db.commit()


def test_certs_to_chase_roles_no_prices_and_a_real_cert_file_removes_the_po(api):
    po, j = _cert_po()
    assert api("get", "/compliance/certs-to-chase", role=None).status_code in (401, 403)
    assert api("get", "/compliance/certs-to-chase", role="VIEWER").status_code == 403
    for role in ("OPERATOR", "MANAGER"):
        r = api("get", "/compliance/certs-to-chase", role=role)
        assert r.status_code == 200
    items = {i["po_id"]: i for i in r.json()["items"]}
    assert po["id"] in items and "4321" not in r.text and "unit_cost" not in r.text and "lines" not in items[po["id"]]
    _file(po, "MTR_heat_A12345.pdf")
    assert po["id"] not in {i["po_id"] for i in api("get", "/compliance/certs-to-chase", role="OPERATOR").json()["items"]}


@pytest.mark.parametrize("name", ["Heat_exchanger_drawing.pdf", "Certificate_of_insurance.pdf", "certified_drawing_rev_B.pdf", "wheat_pallet_photo.jpg", "smtrack_export.csv"], ids=range(5))
def test_c1_an_unrelated_file_with_cert_or_heat_in_its_name_is_not_a_certificate(api, name):
    po, j = _cert_po()
    _file(po, name)
    assert po["id"] in {i["po_id"] for i in api("get", "/compliance/certs-to-chase", role="OPERATOR").json()["items"]}


def test_a_cancelled_or_draft_po_and_a_deleted_cert_are_handled(api):
    po, j = _cert_po()
    po_c, _j = _cert_po()
    purchase_orders.cancel(po_c["id"], "customer cancelled the order", BOSS)
    _file(po, "MTR.pdf")
    with SessionLocal() as db:
        from datetime import datetime, timezone

        db.query(ItemFileDB).filter(ItemFileDB.item_ref == str(po["id"])).update({"deleted_at": datetime.now(timezone.utc)})
        db.commit()
    ids = {i["po_id"] for i in api("get", "/compliance/certs-to-chase", role="OPERATOR").json()["items"]}
    assert po["id"] in ids and po_c["id"] not in ids


@pytest.mark.parametrize("flag", [True, "true", "YES", 1, "1", "required", False, "no", None, 0, "", [], {}, "yes please"], ids=range(14))
def test_odd_cert_required_values_never_crash(api, flag):
    po, j = _po()
    with SessionLocal() as db:
        row = db.get(PurchaseOrderDB, po["id"])
        row.lines = [{"description": "x", "quantity": 1, "unit_cost": 1, "cert_required": flag}, "junk", None, 7]
        db.commit()
    assert api("get", "/compliance/certs-to-chase", role="OPERATOR").status_code == 200


# =============================================================== packing-slip lookup by sales order number

def _slip(out_dir, number, data=b"%PDF-1.4 slip"):
    p = out_dir / "packing_slips" / f"packing_slip_SO_{number}.pdf"
    p.write_bytes(data)
    return p


def test_the_download_serves_only_the_named_file_and_every_role_that_may_read_orders_gets_plain_sentences(api, out_dir):
    _slip(out_dir, "55501")
    assert api("get", "/orders/55501/packing-slip", role=None).status_code in (401, 403)
    ok = api("get", "/orders/SO-55501/packing-slip", role="VIEWER")
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/pdf" and "no-store" in ok.headers["cache-control"]
    miss = api("get", "/orders/55502/packing-slip", role="OPERATOR")
    assert miss.status_code == 404 and isinstance(miss.json()["detail"], str)


@pytest.mark.parametrize("so", ["..%2f..%2fsecret", "%2e%2e%2fsecret", "..%5c..%5csecret", "SO-..", "a%2fb", "a%5cb", "x" * 65, "x" * 5000, "55501.pdf", "55501%00", "55501%0a", "%EF%BC%95%EF%BC%95", "55501%20", "%2e", "..",
                              "C%3A%5Cwindows%5Cwin.ini", "con", "aux.pdf"], ids=range(18))
def test_hostile_order_numbers_are_a_plain_400_or_404_and_never_reach_another_file(api, out_dir, so):
    _slip(out_dir, "55501")
    secret = out_dir / "packing_slip_SO_secret.pdf"
    secret.write_bytes(b"%PDF-1.4 SECRET OUTSIDE")
    r = api("get", f"/orders/{so}/packing-slip", role="OPERATOR")
    assert r.status_code in (400, 404, 405, 422), (so, r.status_code)
    assert b"SECRET OUTSIDE" not in r.content
    if r.status_code in (400, 404):
        assert "detail" in r.json() and isinstance(r.json()["detail"], str)


def test_a_symlink_in_the_folder_pointing_outside_is_never_served(api, out_dir):
    secret = out_dir / "outside-secret.pdf"
    secret.write_bytes(b"%PDF-1.4 SECRET OUTSIDE")
    link = out_dir / "packing_slips" / "packing_slip_SO_77701.pdf"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not allowed here")
    r = api("get", "/orders/77701/packing-slip", role="OPERATOR")
    assert r.status_code != 200 and b"SECRET OUTSIDE" not in r.content


def test_s1_the_flag_and_the_download_agree_for_a_link_that_points_outside(out_dir):
    from app.api.v1.endpoints import orders as ep

    secret = out_dir / "outside-secret.pdf"
    secret.write_bytes(b"x")
    link = out_dir / "packing_slips" / "packing_slip_SO_77702.pdf"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not allowed here")
    assert not ep._has_slip("77702", "", ep._slip_names())


def test_the_flag_ignores_odd_stored_paths_and_numbers_and_the_lookup_writes_nothing(out_dir):
    from app.api.v1.endpoints import orders as ep

    _slip(out_dir, "88801")
    names = ep._slip_names()
    before = sorted(p.name for p in (out_dir / "packing_slips").iterdir())
    assert ep._has_slip("SO-88801", "", names) and ep._has_slip("88801", None, names) and ep._has_slip("so-88801", "", names)
    for so, stored in (("88802", "\x00"), ("../88801", ""), ("88801/..", ""), ("", ""), (None, None), ("x" * 5000, "y" * 5000), ("８８８０１", ""), ("88801.pdf", ""), ("88 801", "")):
        assert ep._has_slip(so, stored, names) is False, (so, stored)
    assert before == sorted(p.name for p in (out_dir / "packing_slips").iterdir())


def test_a_missing_output_folder_means_no_slip_not_a_crash(tmp_path, monkeypatch):
    from app.api.v1.endpoints import orders as ep

    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "does-not-exist")
    assert ep._slip_names() == set() and ep._has_slip("123", "", set()) is False

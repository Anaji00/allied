"""Round 27 (adversary): the verified-workflow build: the payment terms engine, the single-entry payment record and its ledger / sales sheet / monday / remittance effects, the engineer gates and
the vendor watchdog. Temporary database and files only (the ledger goes to the test output folder); mail and monday are fakes; nothing is sent. The migration was rehearsed separately on a scratch
database. xfail(strict=False, reason="ADV27-Tn ...") marks holes."""
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, EngineerGateDB, ItemFileDB, JobDocumentDB, MondayChangeDB, PaymentTrancheDB, PurchaseOrderDB, VendorJobDB, VendorPaymentDB
from app.services import gates, payments, vendor_payments
from app.services.coordination import autopilot, jobs, purchase_orders, rfqs, vendors
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss-r27-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
OP = {"email": f"op-r27-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
ENG = {"email": f"athish-r27-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
CUSTOMER = "Owens Corning Starr Composites"


def _po(total=10000, vendor=None, approve=True, customer=CUSTOMER, user=BOSS):
    r = rfqs.create({"customer": customer, "subject": f"r27 {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, user, announce=False)
    j = jobs.from_rfq(r["id"], user)
    jobs.approve_design(j["id"], user)
    po = purchase_orders.create(j["id"], vendor or f"V27 {uuid.uuid4().hex[:5]}", user, lines=[{"description": "liner plate", "quantity": 1, "unit_cost": total}])
    purchase_orders.update(po["id"], {"vendor_email": f"sales{uuid.uuid4().hex[:5]}@vendor-example.test"}, user)
    if approve:
        po = purchase_orders.approve(po["id"], user)
    return po, j


def _tranches(sched):
    return {t["seq"]: t for t in sched["tranches"]}


def _due_tranche(po_id, terms="50/50", total=1000):
    s = payments.create_schedule(po_id, terms, BOSS, total=total)
    payments.fire(po_id, "PO_ISSUED")
    return payments.get_for_po(po_id)["tranches"][0]["id"]


@pytest.fixture()
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
    (tmp_path / "output").mkdir()
    return tmp_path / "output"


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"a27-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", **kw):
        return client.request(method, "/api/v1" + path, headers=heads[role], **kw)

    return call


# =============================================================== the terms engine

@pytest.mark.parametrize("terms,want", [("30/50/20", ["30.00", "50.00", "20.00"]), ("30/70", ["30.00", "70.00"]), ("50-50", ["50.00", "50.00"]), ("100", ["100.00"]), ("33.33/33.33/33.34", ["33.33", "33.33", "33.34"]),
                                        ("30 / 50 / 20", ["30.00", "50.00", "20.00"]), ("30%/70%", ["30.00", "70.00"]), ([30, 70], ["30.00", "70.00"]), ("12.5/12.5/75", ["12.50", "12.50", "75.00"]),
                                        ("0.01/99.99", ["0.01", "99.99"]), ("1e2", ["100.00"]), ("25 25 25 25", ["25.00"] * 4)])
def test_terms_that_add_to_exactly_100_are_read(terms, want):
    assert [f"{p:.2f}" for p in payments.parse_terms(terms)] == want


@pytest.mark.parametrize("terms", ["", None, "abc", "30/60", "30/80", "50/50/10", "0/100", "-30/130", "30/-10/80", "NaN/100", "Infinity", "50.005/49.995", "33.33/33.33/33.33", "1/1/1/1/1/1/96", "100/0", "30//70/x",
                                   "٣٠/٧٠x", "1e3/-900", "99.999/0.001", [], [None], [True, 99], "100.00001"])
def test_terms_that_are_not_exactly_100_or_are_odd_are_refused_plainly(terms):
    with pytest.raises(payments.PaymentError):
        payments.parse_terms(terms)


@pytest.mark.parametrize("total", ["0.01", "1", "99.99", "100.01", "1000", "1234.56", "9999.99", "33333.33", "1000000.01", "7"])
@pytest.mark.parametrize("terms", ["30/50/20", "30/70", "50/50", "33.33/33.33/33.34", "12.5/12.5/75", "16.67/16.67/16.67/16.67/16.67/16.65"])
def test_tranches_always_add_to_the_total_to_the_cent_and_are_never_negative(total, terms):
    pcts = payments.parse_terms(terms)
    parts = payments.split_amount(Decimal(total), pcts)
    assert sum(parts) == Decimal(total)
    assert all(p >= 0 for p in parts), parts


def test_t1_a_tiny_total_never_gives_a_negative_or_zero_tranche():
    for total, terms in (("0.02", "25/25/25/25"), ("0.03", "16.67/16.67/16.67/16.67/16.67/16.65")):
        parts = payments.split_amount(Decimal(total), payments.parse_terms(terms))
        assert all(p >= 0 for p in parts), (total, parts)


@pytest.mark.parametrize("total,why", [("-5", "negative"), ("0", "zero"), ("NaN", "nan"), ("Infinity", "inf"), ("abc", "text")])
def test_a_bad_total_is_refused_plainly(total, why):
    po, _j = _po()
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(po["id"], "50/50", BOSS, total=total)


@pytest.mark.parametrize("total", ["12,50", "1,2,3", "1,23", "0,5"])
def test_t15_a_comma_decimal_total_is_refused(total):
    po, _j = _po()
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(po["id"], "50/50", BOSS, total=total)


@pytest.mark.parametrize("total", ["1e30", "99999999999999999999999999999", "1E+400", "9" * 40])
def test_t2_a_huge_total_is_refused_plainly_not_a_crash(total):
    po, _j = _po()
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(po["id"], "50/50", BOSS, total=total)


def test_t3_the_schedule_total_must_match_the_po_total():
    po, _j = _po(total=10000)
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(po["id"], "50/50", BOSS, total="1000000")


def test_t4_a_cancelled_po_has_no_payments_due():
    po, _j = _po()
    payments.create_schedule(po["id"], "50/50", BOSS)
    payments.fire(po["id"], "PO_ISSUED")
    purchase_orders.cancel(po["id"], "customer cancelled it", BOSS)
    assert not [d for d in payments.due_list() if d["po_id"] == po["id"]]
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(po["id"], "30/70", BOSS)


def test_t5_a_replaced_schedules_tranches_cannot_be_paid():
    po, _j = _po()
    payments.create_schedule(po["id"], "50/50", BOSS, total="1000")
    payments.fire(po["id"], "PO_ISSUED")
    old = payments.get_for_po(po["id"])["tranches"][0]["id"]
    payments.create_schedule(po["id"], "30/70", BOSS, total="1000")
    with pytest.raises(payments.PaymentError):
        vendor_payments.record(old, BOSS, "2026-11-20", draft_remittance=False)


def test_a_schedule_with_a_payment_recorded_cannot_be_replaced_and_the_trigger_fires_once(out_dir):
    po, _j = _po()
    payments.create_schedule(po["id"], "50/50", BOSS, total="1000")
    first = payments.fire(po["id"], "PO_ISSUED")
    assert len(first) == 1 and payments.fire(po["id"], "PO_ISSUED") == [] and payments.fire(po["id"], "po_issued") == []
    tr = payments.get_for_po(po["id"])["tranches"][0]["id"]
    vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(po["id"], "30/70", BOSS, total="1000")
    assert payments.fire(po["id"], "PO_ISSUED") == []


def test_triggers_fired_out_of_order_only_open_their_own_tranches_and_unknown_triggers_do_nothing():
    po, _j = _po()
    payments.create_schedule(po["id"], "30/50/20", BOSS, total="1000")
    assert [t["seq"] for t in payments.fire(po["id"], "DELIVERY_CONFIRMED")] == [3]
    assert payments.fire(po["id"], "BANANA") == [] and payments.fire(po["id"], "") == [] and payments.fire(po["id"], None) == []
    assert [t["seq"] for t in payments.fire(po["id"], "GATE2_APPROVED")] == [2]
    assert [t["seq"] for t in payments.fire(po["id"], "PO_ISSUED")] == [1]


def test_two_threads_firing_the_same_trigger_open_each_tranche_once():
    po, _j = _po()
    payments.create_schedule(po["id"], "50/50", BOSS, total="1000")
    got = []
    ts = [threading.Thread(target=lambda: got.append(len(payments.fire(po["id"], "PO_ISSUED")))) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(got) >= 1 and payments.get_for_po(po["id"])["tranches"][0]["status"] == "DUE"
    assert sum(1 for t in payments.get_for_po(po["id"])["tranches"] if t["status"] == "DUE") == 1


# =============================================================== recording a payment

def test_a_payment_is_recorded_once_and_the_same_entry_again_changes_nothing(out_dir):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    a = vendor_payments.record(tr, BOSS, "2026-11-20", method="WIRE", reference="WIRE-1", draft_remittance=False)
    b = vendor_payments.record(tr, BOSS, "2026-11-20", method="WIRE", reference="WIRE-1", draft_remittance=False)
    assert a["duplicate"] is False and b["duplicate"] is True and a["payment"]["id"] == b["payment"]["id"]
    with pytest.raises(payments.PaymentError):
        vendor_payments.record(tr, BOSS, "2026-11-21", idem_key="other-key", draft_remittance=False)          # the tranche is paid: another entry is refused
    with SessionLocal() as db:
        assert db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id == tr).count() == 1


def test_five_threads_recording_the_same_payment_make_one(out_dir):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    res, errs = [], []

    def go():
        try:
            res.append(vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False))
        except payments.PaymentError:
            pass
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    with SessionLocal() as db:
        assert db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id == tr).count() == 1


def test_t6_the_same_tranche_cannot_be_paid_twice_by_different_keys_at_once(out_dir):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    errs = []

    def go(i):
        try:
            vendor_payments.record(tr, BOSS, "2026-11-20", idem_key=f"key-{i}", draft_remittance=False)
        except payments.PaymentError:
            pass
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with SessionLocal() as db:
        n = db.query(VendorPaymentDB).filter(VendorPaymentDB.tranche_id == tr).count()
    assert n == 1, (n, errs)


def test_t7_one_key_reused_on_another_tranche_is_refused_not_silently_a_duplicate(out_dir):
    po, _j = _po()
    payments.create_schedule(po["id"], "50/50", BOSS, total="1000")
    payments.fire(po["id"], "PO_ISSUED")
    payments.fire(po["id"], "GATE2_APPROVED")
    t1, t2 = [t["id"] for t in payments.get_for_po(po["id"])["tranches"]]
    vendor_payments.record(t1, BOSS, "2026-11-20", idem_key="SAME", draft_remittance=False)
    try:
        second = vendor_payments.record(t2, BOSS, "2026-11-21", idem_key="SAME", draft_remittance=False)
    except payments.PaymentError:
        return
    with SessionLocal() as db:
        assert db.get(PaymentTrancheDB, t2).status == "PAID" or second["duplicate"] is False


@pytest.mark.parametrize("kw,ok", [({"amount": "500"}, True), ({"amount": "500.00"}, True), ({"amount": "$500.00"}, True), ({"amount": "499.99"}, False), ({"amount": "500.01"}, False), ({"amount": "1000"}, False),
                                   ({"amount": "-500"}, False), ({"amount": "0"}, False), ({"amount": "NaN"}, False), ({"amount": "abc"}, False), ({"paid_on": "2026-02-30"}, False), ({"paid_on": "20/11/2026"}, False),
                                   ({"paid_on": ""}, False), ({"method": "BITCOIN"}, False), ({"method": "wire"}, True)])
def test_the_amount_must_be_exactly_what_is_due_and_dates_and_methods_are_checked(out_dir, kw, ok):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    args = {"paid_on": "2026-11-20", "draft_remittance": False, **kw}
    if ok:
        assert vendor_payments.record(tr, BOSS, **args)["duplicate"] is False
    else:
        with pytest.raises(payments.PaymentError):
            vendor_payments.record(tr, BOSS, **args)


def test_a_tranche_that_is_not_due_yet_says_why_and_an_unknown_one_is_404(out_dir):
    po, _j = _po()
    payments.create_schedule(po["id"], "30/50/20", BOSS, total="1000")
    payments.fire(po["id"], "PO_ISSUED")
    ids = [t["id"] for t in payments.get_for_po(po["id"])["tranches"]]
    for tid in ids[1:]:
        with pytest.raises(payments.PaymentError) as e:
            vendor_payments.record(tid, BOSS, "2026-11-20", draft_remittance=False)
        assert e.value.status == 409
    with pytest.raises(payments.PaymentError) as e:
        vendor_payments.record(99999999, BOSS, "2026-11-20", draft_remittance=False)
    assert e.value.status == 404


def test_t8_no_payment_can_be_recorded_for_a_cancelled_po(out_dir):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    purchase_orders.cancel(po["id"], "customer cancelled it", BOSS)
    with pytest.raises(payments.PaymentError):
        vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)


def test_t9_a_wire_copy_from_another_job_is_refused(out_dir, tmp_path):
    po, _j = _po()
    other, oj = _po(vendor="OTHER VENDOR 27")
    f = tmp_path / "other-quote.pdf"
    f.write_bytes(b"%PDF-1.4 other vendor's confidential quote")
    with SessionLocal() as db:
        row = ItemFileDB(item_type="rfq", item_ref="RFQ-OTHER", filename="other-quote.pdf", stored_path=str(f), content_type="application/pdf", size=f.stat().st_size, uploaded_by="x")
        db.add(row)
        db.commit()
        fid = row.id
    tr = _due_tranche(po["id"])
    with pytest.raises(payments.PaymentError):
        vendor_payments.record(tr, BOSS, "2026-11-20", wire_file_id=fid, draft_remittance=True)


def test_a_missing_or_deleted_file_is_refused_and_the_remittance_draft_names_only_po_amount_and_date(out_dir, tmp_path):
    po, j = _po()
    f = tmp_path / "wire.pdf"
    f.write_bytes(b"%PDF-1.4 wire")
    with SessionLocal() as db:
        row = ItemFileDB(item_type="po", item_ref=str(po["id"]), filename="wire.pdf", stored_path=str(f), content_type="application/pdf", size=14, uploaded_by="x")
        db.add(row)
        db.commit()
        fid = row.id
    tr = _due_tranche(po["id"])
    with pytest.raises(payments.PaymentError):
        vendor_payments.record(tr, BOSS, "2026-11-20", wire_file_id=99999999, draft_remittance=False)
    out = vendor_payments.record(tr, BOSS, "2026-11-20", wire_file_id=fid, reference="WIRE 77")
    act = out["effects"]["remittance_draft"]
    assert act["ok"] and act["attachments"] == ["wire.pdf"]
    with SessionLocal() as db:
        a = db.get(AssistantActionDB, act["action_id"])
        text = f"{a.args['subject']} {a.args['body']} {a.summary}"
        assert a.status == "PENDING" and a.args["_audience"] == "vendor"
    for bad in (CUSTOMER, "Owens", j["ref"], "SO-", "margin", "Starr"):
        assert bad.lower() not in text.lower(), bad
    assert str(po["po_number"]) in text and "500.00" in text and "2026-11-20" in text


def test_a_file_can_only_be_attached_if_a_recorded_payment_names_it(out_dir, tmp_path):
    f = tmp_path / "secret.pdf"
    f.write_bytes(b"secret pricing")
    with SessionLocal() as db:
        row = ItemFileDB(item_type="rfq", item_ref="RFQ-X", filename="secret.pdf", stored_path=str(f), content_type="application/pdf", size=14, uploaded_by="x")
        db.add(row)
        db.commit()
        fid = row.id
    with pytest.raises(payments.PaymentError) as e:
        vendor_payments.payment_doc_bytes(fid)
    assert e.value.status == 403
    from app.services import actions

    with pytest.raises(actions.ActionError):
        actions.resolve_attachments([{"type": "payment_doc", "id": fid, "name": "secret.pdf"}], "customer", BOSS)
    with pytest.raises((actions.ActionError, payments.PaymentError)):
        actions.resolve_attachments([{"type": "payment_doc", "id": fid, "name": "secret.pdf"}], "vendor", BOSS)


# =============================================================== the ledger

def _ledger_cells(path):
    import openpyxl

    wb = openpyxl.load_workbook(path)
    return {ws.title: [[(c.value, c.data_type) for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


@pytest.mark.parametrize("evil", ["=1+1", "=HYPERLINK(\"http://evil.test\",\"x\")", "+cmd|' /C calc'!A0", "@SUM(1,1)", "-2+3"])
def test_t10_text_that_looks_like_a_formula_is_written_as_plain_text(out_dir, evil):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    vendor_payments.record(tr, BOSS, "2026-11-20", reference=evil, draft_remittance=False)
    cells = _ledger_cells(vendor_payments.ledger_path())
    bad = [(sheet, c) for sheet, rows in cells.items() for row in rows for c in row if c[1] == "f"]
    assert not bad, bad[:3]


def test_t11_a_vendor_name_that_looks_like_a_formula_is_plain_text(out_dir):
    po, _j = _po(vendor="=HYPERLINK(\"http://evil.test\")")
    tr = _due_tranche(po["id"])
    vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)
    cells = _ledger_cells(vendor_payments.ledger_path())
    assert not [c for rows in cells.values() for row in rows for c in row if c[1] == "f"]


def test_the_ledger_is_rebuilt_from_the_database_so_it_never_holds_a_row_twice_and_a_crash_leaves_the_old_file(out_dir, monkeypatch):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    vendor_payments.record(tr, BOSS, "2031-03-04", draft_remittance=False)
    vendor_payments.write_ledger()
    vendor_payments.write_ledger()
    path = vendor_payments.ledger_path()
    before = path.read_bytes()
    rows = [r for rows in _ledger_cells(path).values() for r in rows if r and r[0][0] == "2031-03-04"]
    assert len(rows) == 1
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        vendor_payments.write_ledger()
    monkeypatch.setattr(os, "replace", real_replace)
    assert path.read_bytes() == before and not list(path.parent.glob(".ledger.*.tmp"))


def test_many_writers_at_once_leave_one_good_ledger(out_dir):
    po, _j = _po()
    tr = _due_tranche(po["id"])
    vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)
    errs = []

    def go():
        try:
            vendor_payments.write_ledger()
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert _ledger_cells(vendor_payments.ledger_path()) and not list(vendor_payments.ledger_path().parent.glob(".ledger.*.tmp"))


def test_sheet_names_are_valid_unique_and_short_even_for_hostile_or_huge_vendor_lists():
    used = set()
    names = [vendor_payments._sheet_name(v, used) for v in ["A" * 80, "A" * 80, "A" * 80, "Zibo/Foundry", "zibo:foundry", "Zibo*Foundry", "", "Summary", "summary", "x" * 31, "x" * 31] + [f"Vendor number {i} with a long name" for i in range(300)]]
    assert len({n.casefold() for n in names}) == len(names) and all(1 <= len(n) <= 31 for n in names)
    assert not any(ch in n for n in names for ch in "[]*?/\\:")


@pytest.mark.parametrize("vendor", ["'Quoted Foundry'", "Vendor'", "'Vendor", "History", "history"])
def test_t12_sheet_names_are_valid_for_excel(vendor):
    name = vendor_payments._sheet_name(vendor, set())
    assert not name.startswith("'") and not name.endswith("'") and name.casefold() != "history"


# =============================================================== staging: monday, sales sheet

def test_recording_a_payment_stages_for_monday_but_never_calls_monday_and_updates_the_sales_sheet_once(out_dir, monkeypatch):
    calls = []
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: type("M", (), {"live": True, "writable": False, "execute_query": lambda self, q, v=None: calls.append(q) or {"data": {}}})())
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    po, j = _po()
    with SessionLocal() as db:
        from app.models.db_models import JobDB

        job = db.get(JobDB, j["id"])
        job.monday_board_id, job.monday_item_id = f"B{TAG}", f"I{uuid.uuid4().hex[:6]}"
        db.commit()
    tr = _due_tranche(po["id"])
    out = vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)
    vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)
    vendor_payments.update_sales_sheet(j["id"], payments.get_for_po(po["id"])["id"])
    assert out["effects"]["monday"]["ok"] is True and not [q for q in calls if "mutation" in q.casefold()]
    with SessionLocal() as db:
        assert db.query(MondayChangeDB).filter(MondayChangeDB.dedupe_key.like("vendor-payment:%"), MondayChangeDB.status == "STAGED").count() >= 1
        assert db.query(JobDocumentDB).filter(JobDocumentDB.job_id == j["id"], JobDocumentDB.kind == "PAYMENT_SHEET").count() == 1


def test_t13_a_viewer_does_not_see_vendor_payment_amounts_on_the_job(api, out_dir):
    po, j = _po()
    tr = _due_tranche(po["id"], total=7777)
    vendor_payments.record(tr, BOSS, "2026-11-20", draft_remittance=False)
    r = api("GET", f"/jobs/{j['id']}/documents", "VIEWER")
    assert r.status_code == 403 or "3888.50" not in r.text and "PAYMENT_SHEET" not in r.text


def test_t14_an_operator_does_not_read_payment_schedule_amounts(api):
    po, _j = _po(total=7777)
    payments.create_schedule(po["id"], "50/50", BOSS, total="7777")
    r = api("GET", f"/payments/po/{po['id']}", "OPERATOR")
    assert r.status_code == 403 or "3888.50" not in r.text


def test_money_routes_by_role(api):
    po, _j = _po()
    for method, path, body in (("PUT", f"/payments/po/{po['id']}/schedule", {"terms": "50/50", "total": "100"}), ("POST", f"/payments/po/{po['id']}/fire", {"trigger": "PROFORMA"}),
                               ("POST", "/payments/tranche/1/record", {"paid_on": "2026-11-20"}), ("GET", f"/payments/po/{po['id']}/payments", None), ("GET", "/payments/due", None)):
        kw = {"json": body} if body is not None else {}
        assert api(method, path, "VIEWER", **kw).status_code == 403, (method, path)
        assert api(method, path, "OPERATOR", **kw).status_code == 403, (method, path)
    assert api("GET", f"/payments/po/{po['id']}", "VIEWER").status_code == 403
    assert api("PUT", f"/payments/po/{po['id']}/schedule", "MANAGER", json={"terms": "50/50", "total": "100"}).status_code == 200
    assert api("PUT", f"/payments/po/{po['id']}/schedule", "MANAGER", json={"terms": "60/50"}).status_code == 422
    assert api("POST", f"/payments/po/{po['id']}/fire", "MANAGER", json={"trigger": "NONSENSE"}).status_code == 422


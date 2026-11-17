"""Adversarial round 11: customs invoice, three-way match, overseas PO fields and incoterms, the partial unique index, ledger prune, synced-folder guard, Sage skip list and FX, inbox cap. Fakes and temporary database only."""
import io
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, OutboundMailDB, PurchaseOrderDB
from app.services import order_documents
from app.services.assistant import agent, tools  # noqa: F401  (first: avoids a circular import)
from app.services.coordination import jobs, purchase_orders
from app.services.outbound_email_service import outbound_email_service
from tests.test_adversary_po import BOSS, OP, _approved_po, _job  # noqa: F401

CA_ADDR = "1 Rd, Calgary, AB T2P 5G3, Canada"


def _text(pdf: bytes) -> str:
    from pypdf import PdfReader
    return " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


# ============================================================ carrier booking: the customs invoice

def _cci(items, currency="USD", po="PO-9", origin="ZIBO"):
    from app.services.logistics_dispatcher import LogisticsDispatcher
    return LogisticsDispatcher().generate_customs_invoice("123456", "Cust", CA_ADDR, origin, items, currency, po)


def test_a_missing_unit_price_is_never_declared_as_a_made_up_value():
    try:
        inv = _cci([{"item_id": "OC755507", "quantity": 2}])
    except (ValueError, KeyError):
        return
    assert inv.hs_tariff_lines[0]["unit_value"] != 100.0


def test_the_customs_invoice_uses_the_hts_code_that_was_entered():
    inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 10, "hts_code": "7208.39.0030", "description": "Hot-rolled clad plate"}])
    assert inv.hs_tariff_lines[0]["hs_tariff_code"] == "7208.39.0030"


def test_the_origin_statement_is_not_claimed_for_goods_made_in_china():
    inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 10}], origin="ZIBO")
    assert inv.country_of_origin == "China" and "USMCA" not in inv.declaration_statement


@pytest.mark.parametrize("cur", ["EUR", "MXN", "GBP"])
def test_an_unconverted_currency_is_not_declared_as_dollars(cur):
    try:
        inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 1000}], currency=cur)
    except ValueError:
        return
    assert inv.total_declared_usd != 1000


def test_the_customs_invoice_number_never_carries_the_internal_order_number():
    for po in ("PO-9", "", "   ", "中文"):
        inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 10}], po=po)
        assert "123456" not in inv.cci_number


def test_a_customs_invoice_for_an_order_without_lines_is_refused():
    with pytest.raises(ValueError):
        _cci([])


# ============================================================ three-way match

_PO = {"n": "R11PO"}


def _svc_with_po(po=None, qty=10, cost="5.00", vendor="KWS"):
    from app.services.reconciliation_service import ReconciliationService
    po = po or f"R11{uuid.uuid4().hex[:8].upper()}"
    _PO["n"] = po
    svc = ReconciliationService()
    svc._authorized_pos[po.upper()] = {"vendor": vendor, "sales_order": "SO-R11", "lines": {"X1": {"qty": Decimal(qty), "unit_cost": Decimal(cost)}}, "authorized_freight": Decimal("0.00")}
    return svc


def _inv(number, qty=10, cost="5.00", po=None, vendor="KWS"):
    po = po or _PO["n"]
    from app.services.reconciliation_service import VendorInvoice, VendorInvoiceLine
    total = Decimal(qty) * Decimal(cost)
    return VendorInvoice(invoice_number=number, vendor_name=vendor, po_number=po, lines=[VendorInvoiceLine(item_id="X1", quantity_billed=Decimal(qty), unit_cost_billed=Decimal(cost), total_amount=total)],
                         total_amount_billed=total)


def test_an_exact_bill_is_approved_once_and_the_same_number_again_is_a_duplicate():
    svc = _svc_with_po()
    n = f"INV-{uuid.uuid4().hex[:6]}"
    assert svc.reconcile_vendor_invoice(_inv(n)).reconciliation_status == "RECONCILED_APPROVED"
    assert svc.reconcile_vendor_invoice(_inv(n)).reconciliation_status == "DUPLICATE_INVOICE"


def test_a_second_bill_for_the_same_pieces_under_a_new_number_is_not_approved():
    svc = _svc_with_po()
    assert svc.reconcile_vendor_invoice(_inv(f"INV-{uuid.uuid4().hex[:6]}")).reconciliation_status == "RECONCILED_APPROVED"
    assert svc.reconcile_vendor_invoice(_inv(f"INV-{uuid.uuid4().hex[:6]}")).reconciliation_status != "RECONCILED_APPROVED"


def test_a_partial_shipment_bill_at_the_right_price_is_not_a_dispute():
    svc = _svc_with_po()
    assert svc.reconcile_vendor_invoice(_inv(f"INV-{uuid.uuid4().hex[:6]}", qty=5)).reconciliation_status == "RECONCILED_APPROVED"


def test_a_vendor_bill_carries_its_currency():
    from app.services.reconciliation_service import VendorInvoice
    assert "currency" in VendorInvoice.model_fields


def test_overbilling_a_wrong_vendor_a_missing_po_and_a_bad_total_are_all_held():
    svc = _svc_with_po()
    assert svc.reconcile_vendor_invoice(_inv(f"I{uuid.uuid4().hex[:6]}", cost="5.50")).reconciliation_status == "DISCREPANCY_FLAGGED"
    assert svc.reconcile_vendor_invoice(_inv(f"I{uuid.uuid4().hex[:6]}", vendor="NIBROCK")).reconciliation_status == "VENDOR_MISMATCH"
    assert svc.reconcile_vendor_invoice(_inv(f"I{uuid.uuid4().hex[:6]}", po="NOSUCHPO")).reconciliation_status == "UNMATCHED_NO_PO"
    bad = _inv(f"I{uuid.uuid4().hex[:6]}")
    bad.total_amount_billed = Decimal("99999.00")
    assert svc.reconcile_vendor_invoice(bad).reconciliation_status != "RECONCILED_APPROVED"


# ============================================================ PO overseas fields and incoterms

def _lines(**line):
    return order_documents.po_lines([{"quantity": 2, "description": "liner", "unit_cost": "10.00", **line}])


def test_overseas_fields_are_cleaned_clipped_and_printed():
    ln = _lines(hts_code="7208.39.0030", origin="China", uom="EA", net_kg="12.5", cert_required=True)[0]
    assert ln["hts_code"] == "7208.39.0030" and ln["net_kg"] == 12.5 and ln["cert_required"] is True
    bad = _lines(hts_code="<b>&</para>" + "9" * 100, origin="‮China\x00", uom="U" * 100, net_kg="nan")[0]
    assert len(bad.get("hts_code", "")) <= 20 and "‮" not in bad["origin"] and len(bad["uom"]) <= 12 and "net_kg" not in bad


@pytest.mark.parametrize("net", ["-5", "0", "nan", "inf", "1e9", "abc", "12,5", None, True, [1]])
def test_a_net_weight_that_is_not_a_sane_positive_number_is_dropped(net):
    assert "net_kg" not in _lines(net_kg=net)[0] or _lines(net_kg=net)[0]["net_kg"] in (1.0,)


def test_the_po_pdf_prints_the_overseas_details_and_survives_hostile_text():
    _, j = _job()
    po = purchase_orders.create(j["id"], f"OVER {uuid.uuid4().hex[:4]}", BOSS, lines=[{"quantity": 1, "description": "liner", "unit_cost": "5.00", "hts_code": "7208.39.0030", "origin": "China", "uom": "EA", "net_kg": 12.5}])
    purchase_orders.update(po["id"], {"incoterm": "fob", "incoterm_place": "Qingdao port"}, BOSS)
    text = _text(purchase_orders.pdf(po["id"]))
    assert "7208.39.0030" in text and "CHINA" in text.upper() and "FOB" in text and "Qingdao" in text
    po2 = purchase_orders.create(j["id"], f"OVER2 {uuid.uuid4().hex[:4]}", BOSS, lines=[{"quantity": 1, "description": "x", "hts_code": "<b>&</para>", "origin": "中国 ไทย"}])
    with pytest.raises(jobs.JobError):                        # markup is not an Incoterm: refused in plain words; the place text is cleaned
        purchase_orders.update(po2["id"], {"incoterm": "<b>FOB</b>&", "incoterm_place": "上海"}, BOSS)
    purchase_orders.update(po2["id"], {"incoterm": "FOB", "incoterm_place": "上海 <b>&"}, BOSS)
    assert purchase_orders.pdf(po2["id"]).startswith(b"%PDF")


@pytest.mark.parametrize("v", ["YES", "TRUE", "Y", "y", "1", "required"])
def test_a_certificate_requirement_is_not_lost_to_spelling(v):
    assert _lines(cert_required=v)[0].get("cert_required") is True


@pytest.mark.parametrize("code", ["banana", "12", "N/A", "TBD"])
def test_an_hts_code_that_is_not_a_tariff_number_is_not_kept(code):
    assert "hts_code" not in _lines(hts_code=code)[0]


@pytest.mark.parametrize("term", ["banana", "NA", "N/A", "free delivery"])
def test_an_incoterm_must_be_one_of_the_eleven(term):
    po, _ = _approved_po()
    try:
        out = purchase_orders.update(po["id"], {"incoterm": term}, BOSS)
    except jobs.JobError:
        return
    assert out["incoterm"] in (None, "")


def test_real_incoterms_are_stored_upper_case_and_cleaned():
    po, _ = _approved_po()
    out = purchase_orders.update(po["id"], {"incoterm": " fob! ", "incoterm_place": "Qingdao\x00 port‮"}, BOSS)
    assert out["incoterm"] == "FOB" and "\x00" not in out["incoterm_place"]


def test_customs_details_can_still_be_added_after_approval():
    po, _ = _approved_po()
    purchase_orders.update(po["id"], {"incoterm": "FOB"}, BOSS)
    out = purchase_orders.update(po["id"], {"lines": [dict(po["lines"][0], hts_code="7208.39.0030", origin="China")]}, BOSS)
    assert out["lines"][0].get("hts_code") == "7208.39.0030"


def test_the_overseas_job_checks_fire_when_they_should_and_not_for_cancelled_pos():
    base = {"po_number": "50123", "vendor": "HELEN", "incoterm": "FOB", "lines_missing_overseas": 1, "cert_lines": 1}
    got = {c["key"] for c in jobs.job_checks({"checked": [], "purchase_orders": [{**base, "status": "SENT"}]}, today=date(2026, 11, 14))}
    assert {"overseas_details_missing", "tariff_reminder", "certificate_expected"} <= got
    quiet = {c["key"] for c in jobs.job_checks({"checked": [], "purchase_orders": [{**base, "status": "CANCELLED"}]}, today=date(2026, 11, 14))}
    assert not ({"overseas_details_missing", "tariff_reminder", "certificate_expected"} & quiet) and "all_pos_cancelled" in quiet
    domestic = {c["key"] for c in jobs.job_checks({"checked": [], "purchase_orders": [{"po_number": "5", "vendor": "X", "status": "SENT", "lines_missing_overseas": 3}]}, today=date(2026, 11, 14))}
    assert "overseas_details_missing" not in domestic


# ============================================================ the partial unique index

def test_a_new_po_after_a_cancel_is_for_managers_and_two_live_pos_stay_impossible():
    po, j = _approved_po("R11VENDOR")
    purchase_orders.cancel(po["id"], "customer changed the order", BOSS)
    with pytest.raises(jobs.JobError):
        purchase_orders.create(j["id"], "R11VENDOR", OP)
    fresh = purchase_orders.create(j["id"], "R11VENDOR", BOSS)
    assert fresh["id"] != po["id"] and fresh["status"] == "DRAFT"
    assert purchase_orders.create(j["id"], "r11vendor", BOSS)["id"] == fresh["id"]
    with SessionLocal() as db, pytest.raises(Exception):
        db.add(PurchaseOrderDB(job_id=j["id"], vendor="R11vendor", status="DRAFT", lines=[], created_by="t"))
        db.commit()


def test_concurrent_creates_after_a_cancel_make_exactly_one_live_po():
    po, j = _approved_po("R11RACE")
    purchase_orders.cancel(po["id"], "customer changed the order", BOSS)
    got = []
    gate = threading.Barrier(8)

    def go():
        gate.wait()
        try:
            got.append(purchase_orders.create(j["id"], "R11RACE", BOSS)["id"])
        except Exception as exc:  # noqa: BLE001
            got.append(type(exc).__name__)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = {g for g in got if isinstance(g, int)}
    assert len(ids) == 1
    with SessionLocal() as db:
        live = db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == j["id"], PurchaseOrderDB.status != "CANCELLED").count()
    assert live == 1


def test_a_second_cancel_and_a_third_po_still_work_and_numbers_stay_unique():
    po, j = _approved_po("R11THIRD")
    purchase_orders.cancel(po["id"], "customer changed the order", BOSS)
    second = purchase_orders.approve(purchase_orders.create(j["id"], "R11THIRD", BOSS)["id"], BOSS)
    purchase_orders.cancel(second["id"], "changed again please", BOSS)
    third = purchase_orders.approve(purchase_orders.create(j["id"], "R11THIRD", BOSS)["id"], BOSS)
    assert len({po["po_number"], second["po_number"], third["po_number"]}) == 3


def test_downgrading_with_a_cancelled_and_a_live_po_for_one_vendor_leaves_a_unique_index(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    url = f"sqlite:///{tmp_path / 'scratch.db'}"
    monkeypatch.setattr("app.core.database._get_db_url", lambda: url)
    command.upgrade(cfg, "head")
    with sqlite3.connect(tmp_path / "scratch.db") as c:
        cols = [r[1] for r in c.execute("pragma table_info(purchase_orders)")]
        c.execute("insert into jobs (id, ref, title, created_at) values (1, 'J-1', 't', '2026-01-01')") if False else None
        base = {"job_id": 1, "vendor": "V", "status": "CANCELLED", "lines": "[]", "created_by": "t", "created_at": "2026-01-01 00:00:00"}
        use = {k: v for k, v in base.items() if k in cols}
        c.execute(f"insert into purchase_orders ({','.join(use)}) values ({','.join('?' * len(use))})", list(use.values()))
        use["status"] = "DRAFT"
        c.execute(f"insert into purchase_orders ({','.join(use)}) values ({','.join('?' * len(use))})", list(use.values()))
    command.downgrade(cfg, "-1")
    with sqlite3.connect(tmp_path / "scratch.db") as c:
        idx = c.execute("select sql from sqlite_master where name='ux_po_job_vendor'").fetchone()
    assert idx is not None


# ============================================================ ledger prune

def _old_row(category, status="SENT", days=400):
    with SessionLocal() as db:
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:10]}", category=category, reference=f"P{uuid.uuid4().hex[:5]}", status=status,
                              created_at=datetime.now(timezone.utc) - timedelta(days=days)))
        db.commit()


def _count(category):
    with SessionLocal() as db:
        return db.query(OutboundMailDB).filter(OutboundMailDB.category == category).count()


def test_prune_removes_old_staff_mail_and_keeps_vendor_mail_and_recent_rows():
    for cat in ("STAFF_ALERT", "PURCHASE_ORDER", "PRICE_REQUEST"):
        _old_row(cat)
    _old_row("STAFF_ALERT", days=5)
    outbound_email_service.prune_ledger(days=180)
    assert _count("PURCHASE_ORDER") >= 1 and _count("PRICE_REQUEST") >= 1
    with SessionLocal() as db:
        old_staff = db.query(OutboundMailDB).filter(OutboundMailDB.category == "STAFF_ALERT", OutboundMailDB.created_at < datetime.now(timezone.utc) - timedelta(days=180)).count()
    assert old_staff == 0


@pytest.mark.parametrize("cat", ["purchase_order", "Purchase_Order", "PURCHASE_ORDER "])
def test_vendor_mail_in_another_spelling_is_still_kept(cat):
    _old_row(cat)
    outbound_email_service.prune_ledger(days=180)
    assert _count(cat) >= 1


def test_prune_never_goes_below_thirty_days_and_survives_odd_arguments():
    _old_row("STAFF_ALERT", days=10)
    assert outbound_email_service.prune_ledger(days=0) >= 0 and outbound_email_service.prune_ledger(days=-5) >= 0
    assert outbound_email_service.prune_ledger(days=180, batch=0) >= 0
    with SessionLocal() as db:
        assert db.query(OutboundMailDB).filter(OutboundMailDB.category == "STAFF_ALERT", OutboundMailDB.created_at > datetime.now(timezone.utc) - timedelta(days=20)).count() >= 1


# ============================================================ synced-folder guard

@pytest.fixture()
def copy_to(tmp_path, monkeypatch):
    from app.core.database import init_database
    from app.services import backups
    init_database()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    backups.last.clear()

    def run(*parts, allow=False):
        monkeypatch.setattr(settings, "BACKUP_ALLOW_SYNCED" if False else "BACKUP_COPY_ALLOW_SYNCED", allow)
        monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(tmp_path.joinpath(*parts)))
        return backups.run()["copy"]
    return run


@pytest.mark.parametrize("name", ["OneDrive", "OneDrive - Allied", "Dropbox", "Google Drive", "GoogleDrive", "iCloudDrive", "ONEDRIVE"])
def test_the_known_cloud_folders_are_refused_unless_allowed(copy_to, name):
    out = copy_to(name, "copy")
    assert not out["ok"] and "cloud" in out["error"].lower()
    assert copy_to(name + "2" if False else name, "copy", allow=True)["ok"]


@pytest.mark.parametrize("name", ["Box", "pCloudDrive", "MEGAsync", "Nextcloud", "SharePoint", "Sync", "Tresorit"])
def test_other_cloud_folders_are_refused_too(copy_to, name):
    assert not copy_to(name, "copy")["ok"]


def test_an_ordinary_folder_is_accepted(copy_to):
    assert copy_to("plain", "copy")["ok"]


# ============================================================ Sage export: skipped lines and FX

def _orders(monkeypatch, *os_):
    from app.services.sage_export_service import SageExportService
    monkeypatch.setattr(SageExportService, "_get_orders", lambda self, facility=None, only_new=False, kind="so": list(os_))


def _o(**over):
    o = {"sales_order_number": f"9{uuid.uuid4().int % 10**5}", "customer_id": "C1", "customer_name": "Owens Corning", "customer_po_number": "CUST-1", "po_date": date(2026, 11, 14),
         "shipping_address": "5678 Industrial Blvd, Houston, TX 77002", "currency": "USD", "purchase_orders": {"NIBROCK": "50123"},
         "line_items": [{"item_id": "OC755507", "description": "screen", "quantity": 2, "unit_price": "100.00", "unit_cost": "50.00", "preferred_vendor": "NIBROCK"}]}
    o.update(over)
    return o


def test_a_cny_order_is_converted_and_a_bad_order_is_skipped_and_reported(monkeypatch):
    from app.services.sage_export_service import sage_export_service
    _orders(monkeypatch, _o(currency="CNY", line_items=[{"item_id": "OC755507", "quantity": 1, "unit_price": "724.00"}]), _o(currency="XXX"), _o(line_items=[{"item_id": "OC755507", "quantity": "ten", "unit_price": "5"}]))
    text, _ = sage_export_service.export_sales_orders()
    rows = [r for r in text.splitlines()[1:] if r]
    assert any("100.00" in r for r in rows)                   # 724 CNY at 7.24 per dollar
    assert len(sage_export_service.last_skipped) >= 2


def test_two_exports_at_once_each_report_their_own_skips(monkeypatch):
    from app.services.sage_export_service import SageExportService
    svc = SageExportService()
    bad = _o(currency="XXX")
    _orders(monkeypatch, bad)
    seen = []
    gate = threading.Barrier(6)

    def go():
        gate.wait()
        svc.export_sales_orders()
        seen.append(len(svc.last_skipped))
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(n >= 1 for n in seen)


def test_the_sage_files_and_customs_use_the_same_exchange_rate():
    from app.services import fx
    assert abs(1 / settings.FX_RATES["CAD"] - fx.cad_to_usd()) < 1e-3 and abs(1 / settings.FX_RATES["CNY"] - fx.cny_to_usd()) < 1e-3


# ============================================================ sales order create bounds

def _make(lines, ref=None, **extra):
    from app.services.coordination import rfqs
    r = rfqs.create({"customer": "Owens Corning Starr Composites", "subject": f"r11 {uuid.uuid4().hex[:5]}", "items": [{"description": "screen", "quantity": 2}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    return jobs.create_sales_order(j["id"], {"customer_reference": ref or f"CUST-{uuid.uuid4().hex[:6]}", "lines": lines, **extra}, BOSS)


@pytest.fixture(autouse=True)
def _tidy_orders():
    from app.models.db_models import OrderDB
    with SessionLocal() as db:
        before = {r[0] for r in db.query(OrderDB.order_id).all()}
    yield
    with SessionLocal() as db:
        db.query(OrderDB).filter(~OrderDB.order_id.in_(before)).delete(synchronize_session=False)
        db.commit()


@pytest.mark.parametrize("price", ["1e30", 10 ** 12, "-1", "nan", "inf"])
def test_absurd_prices_are_refused_in_plain_words(price):
    with pytest.raises(jobs.JobError) as exc:
        _make([{"description": "OC755507", "quantity": 1, "unit_price": price}])
    assert "Traceback" not in str(exc.value) and "pydantic" not in str(exc.value).lower()


@pytest.mark.parametrize("line", [{"unit_price": "0.001", "quantity": 1}, {"unit_price": "10.00", "quantity": "0.5"}, {"unit_price": "10.00", "quantity": "2.0000001"}])
def test_sub_cent_prices_and_fractional_pieces_are_refused(line):
    with pytest.raises(jobs.JobError):
        _make([{"description": "OC755507", **line}])


def test_two_hundred_lines_and_long_text_are_handled_or_refused_plainly():
    try:
        _make([{"description": "OC755507", "quantity": 1, "unit_price": "1.00"}] * 200)
    except jobs.JobError as exc:
        assert str(exc)


# ============================================================ the approval inbox cap

def test_the_inbox_shows_all_waiting_proposals_or_says_there_are_more():
    user = {"email": f"inbox-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com", "role": "MANAGER"}
    ids = [agent._propose(tools.TOOLS["approve_design"], {"job": f"JOB-{i}"}, user)["id"] for i in range(130)]
    try:
        shown = agent.pending(user)
        assert len(shown) >= 130
    finally:
        with SessionLocal() as db:
            db.query(AssistantActionDB).filter(AssistantActionDB.id.in_(ids)).delete(synchronize_session=False)
            db.commit()

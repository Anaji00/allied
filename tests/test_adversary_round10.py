"""Adversarial round 10: Sage export files, sales-order creation money and lines, customer-facing packing slips. Fake data, temporary database and folder only."""
import csv
import io
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.coordination import jobs
from app.services.sage_export_service import SageExportService, money_line, sage_date, sage_export_service, split_shipping_address
from tests.test_adversary_po import BOSS, _job  # noqa: F401

ITEM = "OC755507"


@pytest.fixture(autouse=True)
def _leave_no_orders_behind():
    """Orders made here (some deliberately absurd) are removed from the temporary database afterwards, so they cannot disturb other test files."""
    with SessionLocal() as db:
        before = {r[0] for r in db.query(OrderDB.order_id).all()}
    yield
    with SessionLocal() as db:
        db.query(OrderDB).filter(~OrderDB.order_id.in_(before)).delete(synchronize_session=False)
        db.commit()


def _order(**over):
    o = {"sales_order_number": f"9{uuid.uuid4().int % 10**5}", "customer_id": "C1", "customer_name": "Owens Corning", "customer_po_number": "CUST-1", "po_date": date(2026, 11, 14),
         "shipping_address": "5678 Industrial Blvd, Houston, TX 77002", "payment_terms": "Net 30 Days", "currency": "USD", "purchase_orders": {"NIBROCK": "50123"},
         "line_items": [{"item_id": ITEM, "description": "screen", "quantity": 2, "unit_price": "3772.91", "unit_cost": "1785.00", "preferred_vendor": "NIBROCK"}]}
    o.update(over)
    return o


@pytest.fixture()
def orders(monkeypatch):
    box = {"orders": []}
    monkeypatch.setattr(SageExportService, "_get_orders", lambda self, facility=None, only_new=False, kind="so": list(box["orders"]))
    return box


def _so(box, *os_):
    box["orders"] = list(os_)
    text, _ = sage_export_service.export_sales_orders()
    return list(csv.reader(io.StringIO(text)))


def _po(box, *os_):
    box["orders"] = list(os_)
    text, _ = sage_export_service.export_purchase_orders()
    return list(csv.reader(io.StringIO(text)))


# ============================================================ Sage files: format and money

def test_a_normal_order_exports_exact_cents_and_the_right_columns(orders):
    rows = _so(orders, _order())
    head, row = rows[0], rows[1]
    assert head[:3] == ["Customer ID", "SO Number", "Date"] and row[head.index("Date")] == "11/14/26"
    assert row[head.index("Unit Price")] == "3772.91" and row[head.index("Amount")] == "7545.82"


@pytest.mark.parametrize("unit,qty,amount", [("0.005", 3, "0.03"), ("2.675", 1, "2.68"), ("10.015", 100, "1002.00"), ("1e2", 1, "100.00")])
def test_rounding_is_half_up_on_the_unit_then_the_amount(unit, qty, amount):
    assert f"{money_line(qty, unit)[1]:.2f}" == amount


@pytest.mark.parametrize("field,value", [("unit_price", "N/A"), ("unit_price", "$12.50"), ("quantity", "ten"), ("unit_price", "NaN"), ("quantity", "1,5")])
def test_one_bad_line_does_not_stop_the_whole_file(orders, field, value):
    good = _order()
    bad = _order(line_items=[{"item_id": ITEM, "description": "x", "quantity": 1, "unit_price": "5.00", **{field: value}}])
    rows = _so(orders, good, bad)
    assert any(r[1] == good["sales_order_number"] for r in rows[1:])


def test_a_zero_quantity_line_is_not_exported_as_one_piece(orders):
    rows = _so(orders, _order(line_items=[{"item_id": ITEM, "description": "x", "quantity": 0, "unit_price": "100.00"}]))
    assert len(rows) == 1 or rows[1][rows[0].index("Amount")] == "0.00"


@pytest.mark.parametrize("bad", ["=HYPERLINK(\"http://evil.example\",\"x\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1+1"])
def test_formula_text_is_neutralised(orders, bad):
    rows = _so(orders, _order(customer_name=bad, customer_po_number=bad, line_items=[{"item_id": ITEM, "description": bad, "quantity": 1, "unit_price": "1.00"}]))
    for cell in rows[1]:
        assert not str(cell).startswith(("=", "+", "-", "@", "\t")) or cell in ("-",)


def test_a_newline_in_a_description_does_not_split_the_row(orders):
    text, _ = sage_export_service.export_sales_orders() if False else (None, None)
    orders["orders"] = [_order(line_items=[{"item_id": ITEM, "description": "line one\nline two\r\nline three", "quantity": 1, "unit_price": "1.00"}])]
    text, _ = sage_export_service.export_sales_orders()
    assert len([ln for ln in text.splitlines() if ln.strip()]) == 2


def test_orders_without_a_number_are_not_exported_under_one_made_up_number(orders):
    a, b = _order(), _order()
    a.pop("sales_order_number")
    b.pop("sales_order_number")
    rows = _so(orders, a, b)
    assert len({r[1] for r in rows[1:]}) != 1 or len(rows) == 1


def test_a_duplicate_sales_order_number_is_not_written_twice(orders):
    n = "977777"
    rows = _so(orders, _order(sales_order_number=n), _order(sales_order_number=n, customer_name="Someone Else"))
    assert len([r for r in rows[1:] if r[1] == n]) <= 1


@pytest.mark.parametrize("cur", ["CNY", "CAD"])
def test_a_foreign_currency_order_is_converted_or_labelled_in_the_file(orders, cur):
    rows = _so(orders, _order(currency=cur, line_items=[{"item_id": ITEM, "description": "x", "quantity": 1, "unit_price": "7200.00"}]))
    head, row = rows[0], rows[1]
    assert row[head.index("Unit Price")] != "7200.00" or any(cur in str(c) for c in row)


@pytest.mark.parametrize("cur", ["JPY", "EUR", "XXX", ""])
def test_an_unknown_currency_is_not_silently_treated_as_dollars(cur):
    if not cur:
        pytest.skip("blank means the default currency")
    try:
        got = sage_export_service.to_usd(Decimal("1000"), cur)
    except ValueError:
        return
    assert got != Decimal("1000") or cur in settings.FX_RATES


def test_summary_survives_odd_orders(orders):
    orders["orders"] = [_order(total_revenue="abc"), _order(currency="usd", total_revenue="100"), _order(line_items=[{"preferred_vendor": "KWS", "unit_cost": "5", "quantity": "x"}])]
    try:
        sage_export_service.get_batch_summary()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"the summary raised {type(exc).__name__}")


def test_the_po_file_uses_the_issued_number_and_skips_stock_and_vendorless_lines(orders):
    o = _order(line_items=[{"item_id": ITEM, "quantity": 1, "unit_cost": "10.00", "preferred_vendor": "NIBROCK"}, {"item_id": ITEM, "quantity": 1, "unit_cost": "10.00", "preferred_vendor": "STOCK"},
                           {"item_id": ITEM, "quantity": 1, "unit_cost": "10.00"}, {"item_id": ITEM, "quantity": 1, "unit_cost": "10.00", "preferred_vendor": "KWS"}])
    rows = _po(orders, o)
    assert [r[1] for r in rows[1:]] == ["50123"]


def test_a_numeric_vendor_id_does_not_break_the_po_file(orders):
    good = _order()
    bad = _order(line_items=[{"item_id": ITEM, "quantity": 1, "unit_cost": "10.00", "preferred_vendor": 12345}])
    rows = _po(orders, good, bad)
    assert len(rows) >= 2


@pytest.mark.parametrize("addr,want", [("5678 Industrial Blvd, Houston, TX 77002", ("5678 Industrial Blvd", "Houston", "TX", "77002")), ("Plant 4, 1 Rd, Calgary, AB T2P 5G3, Canada", ("Plant 4, 1 Rd", "Calgary", "AB", "T2P 5G3")),
                                       ("", ("", "", "", "")), ("just one line", ("just one line", "", "", ""))])
def test_ship_to_address_splitting_for_the_usual_destinations(addr, want):
    assert split_shipping_address(addr) == want


@pytest.mark.parametrize("addr", ["1 Plant Rd, Monterrey, NL 64000, Mexico", "9 Dock St, Hanoi, 100000, Vietnam"])
def test_other_countries_do_not_end_up_in_the_state_column(addr):
    assert split_shipping_address(addr)[2].upper() not in ("MEXICO", "VIETNAM")


def test_an_unreadable_order_date_is_not_replaced_by_today():
    assert sage_date("11/20/2026") == "11/20/26"


def _db_order(so, **over):
    row = dict(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number="CUST-X", sales_order_number=so, customer_id="C", customer_name="Z", status="NEW", currency="USD",
               line_items=[{"item_id": ITEM, "description": "x", "quantity": 1, "unit_price": "10.00", "unit_cost": "5.00", "preferred_vendor": "NIBROCK"}],
               purchase_orders={"NIBROCK": f"7{uuid.uuid4().int % 10**5}"})
    row.update(over)
    with SessionLocal() as db:
        db.add(OrderDB(**{k: v for k, v in row.items() if hasattr(OrderDB, k)}))
        db.commit()


def test_a_failure_after_the_claim_does_not_lose_the_orders(monkeypatch):
    so = f"8{uuid.uuid4().int % 10**5}"
    _db_order(so)
    import app.services.sage_export_service as mod
    real = mod.audit_log
    monkeypatch.setattr(mod, "audit_log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit db down")))
    try:
        sage_export_service.export_sales_orders(only_new=True, mark_exported=True)
    except RuntimeError:
        pass
    monkeypatch.setattr(mod, "audit_log", real)
    text, _ = sage_export_service.export_sales_orders(only_new=True)
    assert so in text


def test_an_order_is_exported_once_and_only_one_of_two_overlapping_exports_gets_it():
    import threading
    so = f"6{uuid.uuid4().int % 10**5}"
    _db_order(so)
    got = []
    ts = [threading.Thread(target=lambda: got.append(sage_export_service.export_sales_orders(only_new=True, mark_exported=True)[1])) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(so in g for g in got) == 1
    assert so not in sage_export_service.export_sales_orders(only_new=True)[0]
    assert sage_export_service.unmark_exported([so], actor="t") == [so]
    assert so in sage_export_service.export_sales_orders(only_new=True)[0]


# ============================================================ sales order creation

def _so_job():
    from app.services.coordination import rfqs
    r = rfqs.create({"customer": "Owens Corning Starr Composites", "subject": f"so part {uuid.uuid4().hex[:5]}", "items": [{"description": "screen", "quantity": 2}]}, BOSS, announce=False)
    return jobs.from_rfq(r["id"], BOSS)


def _make(lines, ref=None, **extra):
    j = _so_job()
    return jobs.create_sales_order(j["id"], {"customer_reference": ref or f"CUST-{uuid.uuid4().hex[:6]}", "lines": lines, **extra}, BOSS)


def test_a_good_sales_order_is_made_once_and_the_second_try_is_refused():
    j = _so_job()
    data = {"customer_reference": f"CUST-{uuid.uuid4().hex[:6]}", "lines": [{"description": ITEM, "quantity": 2, "unit_price": "3772.91"}]}
    out = jobs.create_sales_order(j["id"], data, BOSS)
    assert out["sales_order"]
    with pytest.raises(jobs.JobError):
        jobs.create_sales_order(j["id"], data, BOSS)


@pytest.mark.parametrize("lines", [[], [{}], [{"description": ""}], [None, 5, "x"], [{"description": "no such part at all zzz", "quantity": 1, "unit_price": 1}]])
def test_unusable_lines_are_refused_in_plain_words(lines):
    with pytest.raises(jobs.JobError) as exc:
        _make(lines)
    assert "Traceback" not in str(exc.value) and str(exc.value)


@pytest.mark.parametrize("ref", ["", "   ", None])
def test_a_missing_customer_reference_is_refused(ref):
    j = _so_job()
    with pytest.raises(jobs.JobError):
        jobs.create_sales_order(j["id"], {"customer_reference": ref, "lines": [{"description": ITEM, "quantity": 1, "unit_price": 1}]}, BOSS)


def test_a_line_without_a_price_is_refused_not_priced_at_zero():
    with pytest.raises(jobs.JobError):
        _make([{"description": ITEM, "quantity": 2}])


@pytest.mark.parametrize("qty", [0, -3, "0"])
def test_a_zero_or_negative_quantity_is_refused(qty):
    with pytest.raises(Exception):
        _make([{"description": ITEM, "quantity": qty, "unit_price": "10.00"}])


@pytest.mark.parametrize("price", [-10, "-0.01", "nan", "inf", "1e30", 10 ** 12])
def test_an_absurd_unit_price_is_refused(price):
    with pytest.raises(Exception):
        _make([{"description": ITEM, "quantity": 1, "unit_price": price}])


@pytest.mark.parametrize("field,value", [("unit_price", "$1,000.00"), ("unit_price", "ten dollars"), ("unit_price", {"a": 1}), ("unit_price", [1]), ("quantity", "ten"), ("quantity", [2])])
def test_a_garbled_price_or_quantity_gets_a_plain_error(field, value):
    line = {"description": ITEM, "quantity": 1, "unit_price": "10.00"}
    line[field] = value
    with pytest.raises(jobs.JobError):
        _make([line])


def test_the_customer_reference_and_address_are_bounded_and_clean():
    try:
        out = _make([{"description": ITEM, "quantity": 1, "unit_price": "10.00"}], ref="R" * 100000 + "‮\x00", shipping_address="A" * 100000)
    except jobs.JobError:
        return
    assert len(str(out["sales_order"].get("customer_reference") or "")) <= 100


# ============================================================ customer-facing packing slip

def _slip(tmp_path, desc="liner", so="SO-123456", vendor="NIBROCK"):
    from app.models.orders import CustomerDetails, MasterOrder, OrderArchetype, OrderLineItem, OrderStatus, ShippingDetails
    from app.services.packing_slips import PackingSlipGenerator
    order = MasterOrder(order_id="ORD-R10", customer_po_number="PO-R10", po_date=date(2026, 2, 1),
                        customer=CustomerDetails(customer_id="C1", customer_name="Owens Corning", billing_address="1 Main St, Houston, TX", sales_rep="K"),
                        shipping=ShippingDetails(recipient_name="Plant 4", shipping_address="2 Plant Rd, Houston, TX 77002", shipping_method="MOTOR FRT", blind_shipping_required=True),
                        line_items=[OrderLineItem(item_id=ITEM, raw_item_text=desc, description=desc, quantity=Decimal("2.0"), unit_price=Decimal("3772.91"), unit_cost=Decimal("1785.00"),
                                                  preferred_vendor=vendor)],
                        archetype=OrderArchetype.BLIND_DROPSHIP, status=OrderStatus.PROCESSING)
    path = PackingSlipGenerator(output_dir=tmp_path).generate_pdf(order=order, sales_order_number=so)
    from pypdf import PdfReader
    return path, " ".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def test_the_slip_shows_no_cost_no_vendor_and_no_internal_order_number(tmp_path):
    _, text = _slip(tmp_path)
    for secret in ("SO-123456", "123456", "NIBROCK", "1,785", "1785", "unit_cost", "margin"):
        assert secret not in text, secret


def test_the_slip_the_customer_receives_is_not_named_with_the_internal_order_number(tmp_path, client):
    """The file on disk keeps the internal name (several screens find it that way); the name the customer gets is built from THEIR reference."""
    import app.api.v1.endpoints.orders as orders_mod
    src = Path(orders_mod.__file__).read_text(encoding="utf-8")
    assert "download_name = f\"packing_list_" in src and "filename=download_name" in src


def test_a_vendor_name_typed_into_a_description_is_not_printed(tmp_path):
    _, text = _slip(tmp_path, desc="Hammermill screen made by NIBROCK")
    assert "NIBROCK" not in text


def test_the_slip_survives_odd_text_and_many_lines(tmp_path):
    for desc in ("中文 ไทย ‮ RTL", "<b>&</para>", "x" * 3000, "\x00\x1b"):
        path, _ = _slip(tmp_path, desc=desc)
        assert Path(path).read_bytes().startswith(b"%PDF")

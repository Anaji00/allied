"""Totals over several currencies are per currency (or marked "about" when converted), and supplier lines leave "ready" once their file is marked, and return on unmark. Temporary database."""
import uuid

from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.sage_export_service import overview, sage_export_service

TAG = uuid.uuid4().hex[:5]


def _order(currency, total, vendor="HELEN"):
    so = f"9{uuid.uuid4().int % 10**5}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=uuid.uuid4().hex, customer_po_number=f"PO{uuid.uuid4().hex[:6]}", sales_order_number=so, customer_id="C1", customer_name=f"Mixed Co {TAG}", currency=currency, total_revenue=total,
                       status="RECEIVED", line_items=[{"item_id": "X1", "quantity": 2, "unit_cost": total / 4, "unit_price": total / 2, "preferred_vendor": vendor}], purchase_orders={vendor: f"PO-{so}"}, shipping_address="x"))
        db.commit()
    return so


def test_a_batch_in_three_currencies_is_shown_per_currency_and_the_converted_figure_says_about():
    base = sage_export_service.get_batch_summary(only_new=True)
    a, b, c = _order("USD", 61000.0), _order("CAD", 66400.0), _order("CNY", 65500.0)
    got = sage_export_service.get_batch_summary(only_new=True)
    d = {k: float(v) - float(base.by_currency.get(k, 0)) for k, v in got.by_currency.items()}
    assert d["USD"] == 61000.0 and d["CAD"] == 66400.0 and d["CNY"] == 65500.0
    assert "USD" in got.sales_label and "CAD" in got.sales_label and "CNY" in got.sales_label and "about USD" in got.sales_label        # per currency first; the converted total is labelled "about"
    assert got.purchase_by_currency and "about USD" in got.purchase_label
    ov = overview()
    assert ov["ready_to_send"]["sales_totals"]["CAD"] >= 66400.0 and "CAD" in ov["text"] and "USD" in ov["text"]


def test_supplier_lines_leave_ready_when_their_file_is_marked_and_come_back_on_unmark():
    so = _order("USD", 1000.0, vendor=f"V{TAG}")
    before = sage_export_service.get_batch_summary(only_new=True).purchase_orders_count
    assert overview()["ready_to_send"]["purchase_orders"] >= 1
    sage_export_service.generate_purchase_orders_csv(only_new=True, mark_exported=True)
    after = sage_export_service.get_batch_summary(only_new=True)
    assert after.purchase_orders_count < before and overview()["ready_to_send"]["purchase_lines"] == after.purchase_orders_count      # the box and the sentence agree
    sage_export_service.unmark_exported([so], kind="po", actor="test")
    again = sage_export_service.get_batch_summary(only_new=True)
    assert again.purchase_orders_count == after.purchase_orders_count + 1

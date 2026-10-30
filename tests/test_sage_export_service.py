"""Tests for Sage 50 US (Peachtree) CSV Batch Export Adapter."""

from fastapi.testclient import TestClient
from app.main import app
from app.services.sage_export_service import sage_export_service

client = TestClient(app)


def test_sage_sales_orders_csv_format():
    """Verify SO_IMPORT.CSV contains compliant Peachtree/Sage 50 headers and values."""
    csv_content = sage_export_service.generate_sales_orders_csv()
    assert "Customer ID,SO Number,Date" in csv_content
    assert "GL Account" in csv_content
    assert "40000-00-FAB" in csv_content


def test_sage_purchase_orders_csv_format():
    """Verify PO_IMPORT.CSV contains compliant Peachtree/Sage 50 headers and values."""
    csv_content = sage_export_service.generate_purchase_orders_csv()
    assert "Vendor ID,PO Number,Date" in csv_content
    assert "50000-00-STEEL" in csv_content


def test_sage_export_api_endpoints():
    """Verify REST API downloads and batch payload."""
    # Summary
    sum_resp = client.get("/api/v1/sage/export/summary")
    assert sum_resp.status_code == 200
    assert sum_resp.json()["sales_orders_count"] >= 1

    # SO CSV Download
    so_resp = client.get("/api/v1/sage/export/sales-orders")
    assert so_resp.status_code == 200
    assert "text/csv" in so_resp.headers["content-type"]
    assert "SO Number" in so_resp.text

    # PO CSV Download
    po_resp = client.get("/api/v1/sage/export/purchase-orders")
    assert po_resp.status_code == 200
    assert "PO Number" in po_resp.text

    # Batch Payload
    batch_resp = client.post("/api/v1/sage/export/batch")
    assert batch_resp.status_code == 200
    batch_data = batch_resp.json()
    assert "SO_IMPORT.CSV" in batch_data["files"]
    assert "PO_IMPORT.CSV" in batch_data["files"]


def test_sage_dates_and_only_new_exports_each_order_once():
    """Sage 50 US imports mm/dd/yy, and an order must reach Sage only once."""
    import csv
    import io
    import uuid
    from decimal import Decimal

    from app.core.container import get_orchestrator
    from app.models.orders import InboundOrderRequest, OrderLineItem
    from app.services.sage_export_service import sage_date, sage_export_service

    assert sage_date("2026-10-27") == "10/27/26"

    po = f"PO-EXP-{uuid.uuid4().hex[:6].upper()}"
    result = get_orchestrator().process_inbound_order(
        InboundOrderRequest(
            customer_name="Owens Corning",
            customer_po_number=po,
            shipping_address="1 Plant Rd, Waxahachie, TX 75165",
            line_items=[OrderLineItem(raw_item_text="AR400 wear plate", quantity=Decimal("2"), unit_price=Decimal("0"))],
        )
    )
    first = sage_export_service.generate_sales_orders_csv(only_new=True, mark_exported=True)
    rows = [r for r in csv.DictReader(io.StringIO(first)) if r["SO Number"] == result.sales_order_number]
    assert rows and rows[0]["Customer PO"] == po
    assert rows[0]["Date"].count("/") == 2
    assert rows[0]["Number of Distributions"] == str(len(rows))

    second = sage_export_service.generate_sales_orders_csv(only_new=True, mark_exported=True)
    assert result.sales_order_number not in second

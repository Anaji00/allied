"""The paged order list and single-order lookup behind the Orders screen."""

import uuid

from app.core.database import SessionLocal
from app.services.database_service import DatabaseService


def _order(tag: str, n: int, **overrides):
    """Persists one order in the test database. `tag` keeps each test's rows apart."""
    data = {
        "order_id": uuid.uuid4().hex[:12],
        "customer_po_number": f"PO-{tag}-{n}",
        "sales_order_number": f"{tag}{n}",
        "customer_id": "10221",
        "customer_name": f"Customer {tag}",
        "status": "SYNCED_TO_SAGE",
        "total_revenue": 1000,
        "total_expense": 750,
        "gross_margin_pct": 25,
        "purchase_orders": {"NIBROCK": f"PO-N-{n}"},
        "line_items": [{"item_id": "OC755507", "quantity": 1}],
    }
    data.update(overrides)
    with SessionLocal() as db:
        DatabaseService.persist_order(db, data)
    return data


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_list_is_newest_first_with_total_and_paging(client, admin_token):
    tag = "LST" + uuid.uuid4().hex[:5].upper()
    for n in range(3):
        _order(tag, n)

    res = client.get(f"/api/v1/orders?q={tag}&limit=2", headers=_auth(admin_token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3
    assert [o["sales_order_number"] for o in body["items"]] == [f"{tag}2", f"{tag}1"]

    second = client.get(f"/api/v1/orders?q={tag}&limit=2&offset=2", headers=_auth(admin_token)).json()
    assert [o["sales_order_number"] for o in second["items"]] == [f"{tag}0"]


def test_attention_view_holds_failures_and_thin_margins_only(client, admin_token):
    tag = "ATT" + uuid.uuid4().hex[:5].upper()
    _order(tag, 1)  # healthy
    _order(tag, 2, status="SAGE_ERROR")
    _order(tag, 3, gross_margin_pct=9.5)  # under the 15% floor
    _order(tag, 4, status="VALIDATION_ERROR")

    body = client.get(f"/api/v1/orders?q={tag}&view=attention", headers=_auth(admin_token)).json()
    assert sorted(o["sales_order_number"] for o in body["items"]) == [f"{tag}2", f"{tag}3", f"{tag}4"]
    assert body["min_margin_pct"] == 15.0


def test_vendor_and_sage_views(client, admin_token):
    tag = "VEN" + uuid.uuid4().hex[:5].upper()
    _order(tag, 1, purchase_orders={"ZIBO": "PO-Z-1"})
    _order(tag, 2, purchase_orders={"KWS": "PO-K-2"})

    zibo = client.get(f"/api/v1/orders?q={tag}&vendor=zibo", headers=_auth(admin_token)).json()
    assert [o["sales_order_number"] for o in zibo["items"]] == [f"{tag}1"]
    assert zibo["total"] == 1

    not_exported = client.get(f"/api/v1/orders?q={tag}&view=not_in_sage", headers=_auth(admin_token)).json()
    assert not_exported["total"] == 2
    exported = client.get(f"/api/v1/orders?q={tag}&view=in_sage", headers=_auth(admin_token)).json()
    assert exported["total"] == 0


def test_detail_by_sales_order_number(client, admin_token):
    tag = "DET" + uuid.uuid4().hex[:5].upper()
    _order(tag, 7, shipping_address="831 Hayter Rd NW, Edmonton")

    for key in (f"{tag}7", f"SO-{tag}7"):
        res = client.get(f"/api/v1/orders/{key}", headers=_auth(admin_token))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["customer_po_number"] == f"PO-{tag}-7"
        assert body["shipping_address"].startswith("831 Hayter")
        assert body["has_packing_slip"] is False
        assert "packing_slip_path" not in body  # the server path stays on the server

    assert client.get(f"/api/v1/orders/{tag}404", headers=_auth(admin_token)).status_code == 404


def test_list_needs_a_signed_in_user(client, auth_enforced):
    assert client.get("/api/v1/orders").status_code == 401


def test_packing_slip_is_never_cached(client, admin_token):
    """The slip names the customer and address; browsers must not keep a copy."""
    from app.config import settings

    slips = settings.OUTPUT_DIR / "packing_slips"
    slips.mkdir(parents=True, exist_ok=True)
    (slips / "packing_slip_SO_NOCACHE1.pdf").write_bytes(b"%PDF-1.4 test")

    res = client.get("/api/v1/orders/SO-NOCACHE1/packing-slip", headers=_auth(admin_token))
    assert res.status_code == 200, res.text
    assert "no-store" in res.headers["cache-control"]

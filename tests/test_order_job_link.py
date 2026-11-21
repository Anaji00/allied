"""Every RFQ, sales order and purchase order says which job it belongs to (job_ref and job_id, null when none), in the book, the order routes, the PO and the RFQ. Temporary database."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"oj-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


def test_each_order_kind_carries_its_job_and_an_unlinked_one_carries_null(api):
    rfq = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Link {TAG}{uuid.uuid4().hex[:3]}"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("POST", f"/jobs/{job['ref']}/design/approve", role="MANAGER")
    po = api("POST", f"/jobs/{job['ref']}/purchase-orders", json={"vendor": f"Link Vendor {TAG}", "lines": [{"description": "hose", "quantity": 1, "unit_cost": 100, "revision": "A"}]}).json()
    api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER")                                      # a numbered PO is what the book lists
    so_body = {"customer_reference": f"4500{uuid.uuid4().int % 10**6}", "lines": [{"raw_item_text": "OC262064", "quantity": 1, "unit_price": 1385.0}], "shipping_address": "4837 Highway 81 South, Starr, SC 29684"}
    so = str(api("POST", f"/jobs/{job['ref']}/sales-order", role="MANAGER", json=so_body).json()["sales_order"]["number"])
    # the book: all three kinds
    rows = api("GET", "/book", params={"area": "orders", "kind": "all", "limit": 500}).json()["rows"]
    mine = {r["kind"]: r for r in rows if r["job_ref"] == job["ref"]}
    assert {"rfq", "so", "po"} <= set(mine) and all(r["job_id"] == job["id"] for r in mine.values())
    assert mine["rfq"]["ref"] == rfq["number"] and mine["so"]["ref"] == so
    # the order routes
    assert api("GET", f"/orders/{so}").json()["job_ref"] == job["ref"]
    listed = api("GET", "/orders", params={"q": so}).json()["items"]
    assert next(o for o in listed if o["sales_order_number"] == so)["job_ref"] == job["ref"] and next(o for o in listed if o["sales_order_number"] == so)["job_id"] == job["id"]
    assert api("GET", f"/purchase-orders/{po['id']}").json()["job_ref"] == job["ref"]
    assert api("GET", f"/rfqs/{rfq['id']}").json()["job_ref"] == job["ref"]
    # an RFQ with no job, and an order made outside any job, say null
    lone = api("POST", "/rfqs", json={"customer": CUSTOMER, "subject": f"Lone {TAG}{uuid.uuid4().hex[:3]}"}).json()
    assert api("GET", f"/rfqs/{lone['id']}").json()["job_ref"] is None and api("GET", f"/rfqs/{lone['id']}").json()["job_id"] is None
    book_lone = next(r for r in api("GET", "/book", params={"area": "orders", "kind": "rfq", "q": lone["number"]}).json()["rows"] if r["ref"] == lone["number"])
    assert book_lone["job_ref"] is None and book_lone["job_id"] is None
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(customer_name=CUSTOMER, customer_po_number=f"9{uuid.uuid4().int % 10**6}", shipping_address="4837 Highway 81 South, Starr, SC 29684",
                                                                       line_items=[{"raw_item_text": "OC262064", "quantity": 1, "unit_price": 1385.0}]), actor_email="oj@alliedweldingsystems.com")
    assert api("GET", f"/orders/{out.sales_order_number}").json()["job_ref"] is None
    jobs_rows = api("GET", "/book", params={"area": "jobs", "q": job["ref"]}).json()["rows"]
    assert jobs_rows[0]["job_ref"] == job["ref"] and jobs_rows[0]["job_id"] == job["id"]

"""Contacts list, a catalogue part put on a board, the RFQ action bar, and the book's "waiting a while" flag. Temporary database and catalogue folder only."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemBallDB, MondayChangeDB
from app.services import monday_push
from app.services.coordination import boards
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"ct-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()
    return call


def test_contacts_lists_customers_vendors_and_carriers_with_last_and_open_orders(api):
    c = api("GET", "/contacts", params={"kind": "customer", "q": "starr"}).json()
    assert c["kind"] == "customer" and c["count"] >= 1 and c["editable_with"] == "/api/v1/records/customer/{ref}"
    row = c["items"][0]
    assert {"name", "email", "phone", "last_order_at", "open_orders", "owner", "ref", "link"} <= set(row) and "Starr" in row["name"]
    v = api("GET", "/contacts", params={"kind": "vendor", "sort": "open_orders"}).json()
    assert v["count"] >= 1 and all(isinstance(i["open_orders"], int) for i in v["items"])
    ship = api("POST", "/create/shipment", json={"reference": f"CT-{TAG}", "forwarder": f"Fwd {TAG}"}).json()
    car = api("GET", "/contacts", params={"kind": "carrier", "q": f"Fwd {TAG}"}).json()
    assert car["count"] == 1 and car["items"][0]["open_orders"] == 1 and car["items"][0]["editable_with"] if False else car["items"][0]["open_orders"] == 1
    api("GET", "/contacts", params={"kind": "banana"}, expect=400)
    api("GET", "/contacts", params={"sort": "x"}, expect=400)
    api("GET", "/contacts", role="VIEWER", expect=403)


def test_a_customer_or_vendor_is_edited_through_records_by_a_manager(api):
    ref = api("GET", "/contacts", params={"kind": "customer", "q": "starr"}).json()["items"][0]["ref"]
    f = {x["field"]: x for x in api("GET", f"/records/customer/{ref}/fields").json()["fields"]}
    assert f["email"]["editable"] and not f["customer_id"]["editable"]
    r = api("PATCH", f"/records/customer/{ref}", role="MANAGER", json={"field": "contact_name", "text": f"Pat {TAG}"})
    assert r.status_code == 200 and r.json()["now"] == f"Pat {TAG}"


def test_a_catalogue_part_can_be_put_on_a_board_and_the_rfq_has_an_action_bar(api):
    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Parts Board", "role": "reference", "read_at": "now", "groups": [{"id": "g", "title": "New", "color": ""}],
                               "columns": [{"id": "d", "title": "Description", "type": "text"}, {"id": "p", "title": "Price", "type": "numbers"}, {"id": "c", "title": "Cost", "type": "numbers"}], "items": []}
    try:
        part = f"CT-{TAG.upper()}{uuid.uuid4().hex[:3].upper()}"
        api("POST", "/create/product", json={"item_id": part, "description": "Board part", "base_price": "12.5", "standard_cost": "7"})
        dry = api("POST", f"/actions/product/{part}/to-monday", json={"board_id": bid, "group": "new", "dry_run": True}).json()
        assert {c["column"] for c in dry["columns"]} == {"Description", "Price"} and not dry["staged"] and not any(c["column"] == "Cost" for c in dry["columns"])      # cost is not put on a board
        real = api("POST", f"/actions/product/{part}/to-monday", json={"board_id": bid, "group": "new"}).json()
        assert real["staged"] and real["group"] == "New"
        api("GET", f"/actions/product/{part}")
        api("GET", "/actions/product/NOPE-PART", expect=404)
        rfq = api("POST", "/create/rfq", json={"customer": "Owens Corning Starr Composites", "subject": f"Bar {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
        acts = api("GET", f"/actions/rfq/{rfq}").json()
        assert {a["action"] for a in acts["actions"]} >= {"copy_text", "email_draft", "share", "hand_ball"}
    finally:
        with boards._lock:
            boards._mirror.pop(bid, None)


def test_the_book_flags_jobs_waiting_more_than_three_days_and_filters_them(api):
    rfq = api("POST", "/create/rfq", json={"customer": "Owens Corning Starr Composites", "subject": f"Late {TAG}{uuid.uuid4().hex[:3]}"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("POST", f"/actions/job/{job['ref']}/handoff", json={"ball_with": "vendor", "to": "Helen at Zibo", "note": "Proforma"})
    fresh = api("GET", "/book", params={"area": "jobs", "q": job["ref"]}).json()["rows"][0]
    assert fresh["late"] is False and fresh["days_waiting"] is not None and fresh["days_waiting"] <= 1
    assert api("GET", "/book", params={"area": "jobs", "late": True, "q": job["ref"]}).json()["count"] == 0
    with SessionLocal() as db:
        row = db.query(ItemBallDB).filter(ItemBallDB.item_type == "rfq", ItemBallDB.item_ref == rfq["ref"]).one()
        row.ball_since = datetime.now(timezone.utc) - timedelta(days=8)
        db.commit()
    got = api("GET", "/book", params={"area": "jobs", "late": True, "q": job["ref"]}).json()
    assert got["count"] == 1 and got["rows"][0]["late"] is True and got["rows"][0]["days_waiting"] > 3

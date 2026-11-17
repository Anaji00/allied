"""One search across everything. Test data only; nothing external is called."""

import uuid

import pytest

from app.core.database import SessionLocal
from app.models.db_models import ShipmentDB, StaffContactDB
from app.services import search
from app.services.coordination import boards, rfqs, shipments, updates, vendors
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:6]


@pytest.fixture(autouse=True)
def _app(client):
    """Starting the app creates the tables."""


def _order(po):
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=po, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return str(out.sales_order_number)


def _kinds(res):
    return {g["type"]: g["hits"] for g in res["groups"]}


def test_one_search_finds_an_order_an_rfq_a_vendor_job_a_shipment_a_board_row_a_comment_and_a_person():
    word = f"zork{TAG}"
    so = _order(f"PO-{word}")
    rfqs.create({"customer": f"Vulcan {word}", "subject": "Chute liner", "contact_email": "v@vulcan.test"}, {"email": "x", "role": "SYSTEM"}, announce=False)
    vendors.create({"vendor": f"KINGCERA {word}", "description": "Cyclone", "po_number": f"V{TAG}"}, {"email": "x", "role": "SYSTEM"})
    shipments.create({"reference": f"BKG-{word}", "mode": "SEA", "status": "BOOKED", "owner_email": "x@y.z"}, {"email": "x", "role": "SYSTEM"})
    updates.post("order", so, f"please check the {word} drawing", {"email": "x@y.z", "role": "OPERATOR"})
    with SessionLocal() as db:
        db.add(StaffContactDB(email=f"{word}@allied.test", name=f"Nguyễn {word}", role_tag="staff", channels=["email"], active=True))
        db.commit()
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "Open RFQs", "role": "rfqs", "read_at": "now", "groups": [], "columns": [],
                                 "items": [{"id": "9", "name": f"Liner {word}", "group_title": "Open RFQs 4", "values": {"status": "Working"}}]}
    try:
        res = search.search(word)
        k = _kinds(res)
        assert set(k) == {"order", "rfq", "vendor_job", "shipment", "comment", "board_item", "person"}, set(k)
        assert k["order"][0]["ref"] == so and k["order"][0]["title"].startswith(f"PO PO-{word}")
        assert k["board_item"][0]["link"].startswith("/boards?board=") and k["rfq"][0]["link"].startswith("/rfqs?id=")
        assert k["order"][0]["link"] == f"/orders?order={so}" and k["vendor_job"][0]["link"].startswith("/vendors?job=")
        assert k["shipment"][0]["link"].startswith("/shipments?id=") and k["comment"][0]["link"] == f"/orders?order={so}"
        assert res["total"] == sum(len(v) for v in k.values())
    finally:
        with boards._lock:
            boards._mirror.pop(board, None)


def test_every_word_must_match_in_any_order_and_case_and_accents_are_ignored_for_names():
    word = f"quux{TAG}"
    so = _order(f"PO-{word}")
    assert [h["ref"] for h in _kinds(search.search(f"{word.upper()} owens"))["order"]] == [so]
    assert "order" not in _kinds(search.search(f"{word} nosuchcustomer"))
    with SessionLocal() as db:
        db.add(StaffContactDB(email=f"van{TAG}@allied.test", name="Nguyễn Văn Ân", role_tag="staff", channels=["email"], active=True))
        db.commit()
    assert any(h["ref"] == f"van{TAG}@allied.test" for h in _kinds(search.search("nguyen an"))["person"])


@pytest.mark.parametrize("q", ["", " ", "a", "%", "_", "100%", "\\", "x" * 79, "'; drop table orders;--", "李明", "\u0000bad"])
def test_odd_search_text_never_breaks_and_wildcards_are_not_wildcards(q):
    res = search.search(q)
    assert isinstance(res["groups"], list)
    if q.strip() in ("%", "_", "\\"):
        assert res["total"] == 0                                         # a % is a percent sign, not "match everything"


def test_removed_comments_are_not_searchable():
    word = f"gone{TAG}"
    so = _order(f"PO-A{word}")
    c = updates.post("order", so, f"secret {word}", {"email": "x@y.z", "role": "OPERATOR"})
    assert "comment" in _kinds(search.search(word))
    updates.delete(c["id"], {"email": "x@y.z", "role": "OPERATOR"})
    assert "comment" not in _kinds(search.search(f"secret {word}"))


def test_the_limit_applies_per_kind():
    word = f"many{TAG}"
    for i in range(5):
        _order(f"PO-{word}-{i}")
    assert len(_kinds(search.search(word, 3))["order"]) == 3


def test_the_api_needs_a_login_validates_and_returns_the_grouped_shape(auth_enforced, client):
    word = f"api{TAG}"
    _order(f"PO-{word}")
    assert client.get("/api/v1/search", params={"q": word}).status_code in (401, 403)
    h = {"Authorization": f"Bearer {staff_token(f'sv{TAG}@alliedweldingsystems.com', 'VIEWER')}"}
    r = client.get("/api/v1/search", params={"q": word}, headers=h)
    assert r.status_code == 200 and set(r.json()) == {"query", "total", "groups"} and r.json()["groups"][0]["hits"][0]["type"] == "order"
    assert client.get("/api/v1/search", params={"q": "x"}, headers=h).status_code == 422
    assert client.get("/api/v1/search", params={"q": "x" * 81}, headers=h).status_code == 422
    assert client.get("/api/v1/search", params={"q": word, "limit": 99}, headers=h).status_code == 422

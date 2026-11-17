"""Updates: comments with @mentions on any item. Test data only; no mail leaves and nothing touches monday."""

import uuid

import pytest

from app.core.database import SessionLocal
from app.models.db_models import ItemUpdateDB, NotificationDB, StaffContactDB
from app.services.coordination import updates
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
ATHISH = f"athish{TAG}@allied.test"
DOUG = f"doug{TAG}@allied.test"
ELI = f"eli{TAG}@allied.test"
DOUG2 = f"doug{TAG}.b@allied.test"
ENG = {"email": ELI, "role": "OPERATOR"}
BOSS = {"email": ATHISH, "role": "MANAGER"}


@pytest.fixture(autouse=True)
def _people(client):
    with SessionLocal() as db:
        for email, name in ((ATHISH, f"Athish{TAG}"), (DOUG, f"Doug{TAG}"), (ELI, f"Eli{TAG}")):
            if db.get(StaffContactDB, email) is None:
                db.add(StaffContactDB(email=email, name=name, role_tag="staff", channels=["email"], active=True))
        db.commit()


def _order():
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return str(out.sales_order_number)


def _alerts(prefix):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB).all() if n.dedup_key.startswith(prefix)]


def test_a_comment_names_a_person_and_alerts_only_them_with_a_link_back():
    so = _order()
    c = updates.post("order", so, f"@athish{TAG} please check the revision before we cut", ENG)
    assert [m["email"] for m in c["mentions"]] == [ATHISH] and c["unresolved"] == []
    got = _alerts(f"mention:{c['id']}:")
    assert len(got) == 1 and got[0].recipients == [ATHISH] and "mentioned you" in got[0].title and got[0].link == "/orders"
    assert not _alerts(f"mention:{c['id']}:{ELI}")                            # the author isn't alerted about their own comment


def test_naming_yourself_or_nobody_alerts_nobody_and_unknown_names_are_reported():
    so = _order()
    c = updates.post("order", so, f"note to self @eli{TAG}, and @nobodyhere", ENG)
    assert c["mentions"] and not _alerts(f"mention:{c['id']}:")
    assert [u["word"] for u in c["unresolved"]] == ["nobodyhere"]
    mail = updates.post("order", so, "email john@example.com about it", ENG)
    assert mail["mentions"] == [] and mail["unresolved"] == []                 # an email address is not a mention


def test_two_people_with_one_first_name_ask_which_one_instead_of_guessing():
    with SessionLocal() as db:
        db.add(StaffContactDB(email=DOUG2, name=f"Doug{TAG} Brown", role_tag="staff", channels=["email"], active=True))
        db.commit()
    try:
        c = updates.post("order", _order(), f"@doug{TAG} can you look", ENG)
        assert c["mentions"] == [] and len(c["unresolved"][0]["matches"]) == 2
        d = updates.post("order", _order(), "can you look", ENG, mentions=[DOUG2])           # picked from the list
        assert [m["email"] for m in d["mentions"]] == [DOUG2]
    finally:
        with SessionLocal() as db:
            db.query(StaffContactDB).filter(StaffContactDB.email == DOUG2).delete()
            db.commit()


def test_replies_nest_one_level_and_tell_the_person_replied_to():
    so = _order()
    top = updates.post("order", so, "Which drawing revision is this?", ENG)
    r1 = updates.post("order", so, "Rev C", BOSS, parent_id=top["id"])
    r2 = updates.post("order", so, "Thanks", ENG, parent_id=r1["id"])                      # a reply to a reply stays under the top one
    assert r2["parent_id"] == top["id"]
    th = updates.thread("order", so, ELI)
    assert [c["id"] for c in th] == [top["id"]] and [r["id"] for r in th[0]["replies"]] == [r1["id"], r2["id"]]
    assert th[0]["mine"] is True and th[0]["replies"][0]["mine"] is False
    assert _alerts(f"reply:{r1['id']}:")[0].recipients == [ELI]


def test_only_the_author_edits_and_new_names_are_alerted_once():
    so = _order()
    c = updates.post("order", so, "first draft", ENG)
    with pytest.raises(PermissionError):
        updates.edit(c["id"], "hijacked", BOSS)
    e = updates.edit(c["id"], f"now with @doug{TAG}", ENG)
    assert e["edited_at"] and [m["email"] for m in e["mentions"]] == [DOUG]
    assert len(_alerts(f"mention:{c['id']}:{DOUG}")) == 1
    updates.edit(c["id"], f"still @doug{TAG}", ENG)
    assert len(_alerts(f"mention:{c['id']}:{DOUG}")) == 1


def test_authors_and_managers_can_remove_and_a_removed_parent_keeps_its_replies():
    so = _order()
    a = updates.post("order", so, "mine", ENG)
    b = updates.post("order", so, "reply", BOSS, parent_id=a["id"])
    with pytest.raises(PermissionError):
        updates.delete(a["id"], {"email": f"stranger{TAG}@allied.test", "role": "OPERATOR"})
    updates.delete(a["id"], BOSS)                                                          # a manager can
    th = updates.thread("order", so)
    assert th[0]["body"] == "(removed)" and th[0]["removed"] and th[0]["replies"][0]["id"] == b["id"]
    updates.delete(b["id"], BOSS)
    assert updates.thread("order", so) == []
    with pytest.raises(LookupError):
        updates.delete(a["id"], BOSS)
    with SessionLocal() as db:
        assert db.get(ItemUpdateDB, a["id"]) is not None                                   # hidden, not erased


@pytest.mark.parametrize("kind,ref,msg", [("order", "99999999", "no order"), ("rfq", "RFQ-0000", "no RFQ"), ("vendor_job", "999999", "no vendor job"),
                                          ("shipment", "999999", "no shipment")])
def test_comments_only_go_on_things_that_exist(kind, ref, msg):
    with pytest.raises(LookupError, match=msg):
        updates.post(kind, ref, "hello", ENG)


@pytest.mark.parametrize("body,msg", [("", "Write something"), ("   \n ", "Write something"), ("x" * 4001, "too long")])
def test_empty_and_huge_comments_are_refused(body, msg):
    with pytest.raises(updates.UpdateError, match=msg):
        updates.post("milestone", "123", body, ENG)
    with pytest.raises(updates.UpdateError, match="Comments can go on"):
        updates.post("banana", "1", "hi", ENG)


def test_control_characters_are_stripped_and_html_is_kept_as_plain_text():
    c = updates.post("board_item", "555", "<b>bold</b>\x00\x07 text", ENG)
    assert c["body"] == "<b>bold</b> text"


def test_my_mentions_list_and_counts_for_badges():
    so = _order()
    updates.post("order", so, f"@athish{TAG} one", ENG)
    updates.post("order", so, f"@athish{TAG} two", ENG)
    updates.post("order", so, "no mention", ENG)
    mine = updates.mentions_of(ATHISH)
    assert {m["body"] for m in mine} >= {f"@athish{TAG} one", f"@athish{TAG} two"} and all(m["author_email"] == ELI for m in mine)
    assert updates.counts("order", [so, "SO-" + so, "000"]) == {so: 3}


def test_the_api_roles_and_shapes(auth_enforced, client):
    def tok(role, name):
        return {"Authorization": f"Bearer {staff_token(f'{name}{TAG}@alliedweldingsystems.com', role)}"}

    v, o = tok("VIEWER", "viewer"), tok("OPERATOR", "oper")
    so = _order()
    assert client.get("/api/v1/updates", params={"item_type": "order", "item_ref": so}).status_code in (401, 403)
    assert client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": "hi"}, headers=v).status_code == 403
    made = client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": f"@athish{TAG} look"}, headers=o)
    assert made.status_code == 201 and made.json()["mentions"][0]["email"] == ATHISH
    th = client.get("/api/v1/updates", params={"item_type": "order", "item_ref": so}, headers=v).json()
    assert len(th) == 1 and th[0]["mine"] is False
    assert client.get("/api/v1/updates", params={"item_type": "order", "item_ref": "1"}, headers=v).status_code == 404
    assert client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": ""}, headers=o).status_code == 400
    assert client.patch(f"/api/v1/updates/{made.json()['id']}", json={"body": "x"}, headers=tok("OPERATOR", "other")).status_code == 403
    assert client.patch("/api/v1/updates/999999", json={"body": "x"}, headers=o).status_code == 404
    assert client.get("/api/v1/updates/people", params={"q": f"athish{TAG}"}, headers=v).json()[0]["email"] == ATHISH
    assert client.get("/api/v1/updates/counts", params={"item_type": "order", "refs": so}, headers=v).json() == {so: 1}
    assert client.get("/api/v1/updates/counts", params={"item_type": "nope", "refs": "1"}, headers=v).status_code == 400
    assert client.get("/api/v1/updates/mentions", headers=v).status_code == 200
    assert client.delete(f"/api/v1/updates/{made.json()['id']}", headers=o).status_code == 204

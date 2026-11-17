"""Backend features asked for by the frontend: alert snooze/assign, and the rest as they land. Fakes and temporary database only."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal
from app.models.db_models import NotificationDB
from app.services.coordination import notify
from tests.conftest import staff_token


@pytest.fixture(autouse=True)
def _enforced(auth_enforced):
    yield


def _h(email, role="OPERATOR"):
    return {"Authorization": f"Bearer {staff_token(email, role)}"}


def _alert(to, kind="vendor_overdue"):
    with SessionLocal() as db:
        row = NotificationDB(dedup_key=f"t-{uuid.uuid4().hex}", kind=kind, severity="warning", title="Chase the vendor", body="b", recipients=[to])
        db.add(row)
        db.commit()
        return row.id


def _me():
    return f"r7-{uuid.uuid4().hex[:8]}@alliedweldingsystems.com"


# ---------------------------------------------------------------- alerts

def test_a_snoozed_alert_leaves_the_lists_until_its_time_and_comes_back(client):
    me = _me()
    nid = _alert(me)
    r = client.post(f"/api/v1/alerts/{nid}/snooze", json={"hours": 4}, headers=_h(me))
    assert r.status_code == 200 and r.json()["snoozed_until"]
    assert nid not in [n["id"] for n in client.get("/api/v1/alerts", headers=_h(me)).json()["items"]]
    flat = [i["id"] for g in client.get("/api/v1/alerts/rollup", headers=_h(me)).json() for i in g["items"]]
    assert nid not in flat
    with SessionLocal() as db:                                    # the time passes
        db.get(NotificationDB, nid).snoozed_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    items = client.get("/api/v1/alerts", headers=_h(me)).json()["items"]
    assert nid in [n["id"] for n in items]


def test_list_items_carry_snoozed_until_and_assignee(client):
    me = _me()
    nid = _alert(me)
    item = next(n for n in client.get("/api/v1/alerts", headers=_h(me)).json()["items"] if n["id"] == nid)
    assert "snoozed_until" in item and "assignee" in item and item["assignee"] is None


@pytest.mark.parametrize("body", [{}, {"hours": 0}, {"hours": -3}, {"hours": 24 * 31}, {"hours": "abc"}, {"until": "yesterday"}, {"until": "2001-01-01T00:00:00"}, {"hours": "1e999"}, {"hours": "nan"}])
def test_a_bad_snooze_is_refused_plainly(client, body):
    me = _me()
    nid = _alert(me)
    r = client.post(f"/api/v1/alerts/{nid}/snooze", json=body, headers=_h(me))
    assert r.status_code in (422, 400)


def test_snoozing_needs_the_right_to_touch_the_alert(client):
    owner, stranger = _me(), _me()
    nid = _alert(owner)
    assert client.post(f"/api/v1/alerts/{nid}/snooze", json={"hours": 1}, headers=_h(stranger)).status_code == 403
    assert client.post(f"/api/v1/alerts/{nid}/snooze", json={"hours": 1}, headers=_h(stranger, "MANAGER")).status_code == 200
    assert client.post("/api/v1/alerts/99999999/snooze", json={"hours": 1}, headers=_h(owner)).status_code == 404


def test_an_acknowledged_alert_cannot_be_snoozed_or_assigned(client):
    me = _me()
    nid = _alert(me)
    assert client.post(f"/api/v1/alerts/{nid}/acknowledge", headers=_h(me)).status_code == 200
    assert client.post(f"/api/v1/alerts/{nid}/snooze", json={"hours": 1}, headers=_h(me)).status_code == 409
    assert client.post(f"/api/v1/alerts/{nid}/assign", json={"email": _me()}, headers=_h(me)).status_code == 409


def test_assigning_hands_it_to_one_person_who_then_sees_and_can_close_it(client):
    owner, helper = _me(), _me()
    nid = _alert(owner)
    helper_h = _h(helper)
    assert nid not in [n["id"] for n in client.get("/api/v1/alerts", headers=helper_h).json()["items"]]
    assert client.post(f"/api/v1/alerts/{nid}/assign", json={"email": f"  {helper.upper()} "}, headers=_h(owner)).status_code == 200
    item = next(n for n in client.get("/api/v1/alerts", headers=helper_h).json()["items"] if n["id"] == nid)
    assert item["assignee"] == helper
    assert client.post(f"/api/v1/alerts/{nid}/acknowledge", headers=helper_h).status_code == 200


@pytest.mark.parametrize("who", ["", "not-an-email", "someone@placeholder.invalid", "a@b" + "c" * 300 + ".com", None])
def test_assigning_to_nobody_real_is_refused(client, who):
    me = _me()
    nid = _alert(me)
    assert client.post(f"/api/v1/alerts/{nid}/assign", json={"email": who}, headers=_h(me)).status_code == 422


def test_assigning_wakes_a_snoozed_alert(client):
    owner, helper = _me(), _me()
    nid = _alert(owner)
    client.post(f"/api/v1/alerts/{nid}/snooze", json={"hours": 5}, headers=_h(owner))
    _h(helper)          # the helper has an account
    client.post(f"/api/v1/alerts/{nid}/assign", json={"email": helper}, headers=_h(owner))
    assert nid in [n["id"] for n in client.get("/api/v1/alerts", headers=_h(helper)).json()["items"]]


# ---------------------------------------------------------------- job stage history

def _job(client, h):
    from app.services.coordination import rfqs
    r = rfqs.create({"customer": "Owens Corning", "subject": f"r7 part {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]},
                    {"email": "boss-r7@alliedweldingsystems.com", "role": "ADMIN"}, announce=False)
    got = client.post(f"/api/v1/jobs/from-rfq/{r['id']}", headers=h)
    assert got.status_code in (200, 201), got.text
    return got.json()


def test_a_job_lists_every_stage_with_dates_from_its_records_and_marks_the_stuck_one(client):
    boss = _h(_me(), "MANAGER")
    job = _job(client, boss)
    got = client.get(f"/api/v1/jobs/{job['ref']}", headers=boss).json()
    stages = got["stages"]
    from app.services.coordination.jobs import STAGES
    assert [s["stage"] for s in stages] == STAGES
    first = stages[0]
    assert first["done"] and first["entered_at"] and set(first) == {"stage", "stage_word", "done", "entered_at", "by", "blocked_reason"}
    assert not any(s["done"] and s["entered_at"] is None and s["stage"] in ("DESIGN_APPROVED",) for s in stages)
    assert sum(1 for s in stages if s["blocked_reason"]) <= 1


def test_approving_the_design_dates_that_stage_by_the_approver(client):
    me = _me()
    boss = _h(me, "MANAGER")
    job = _job(client, boss)
    assert client.post(f"/api/v1/jobs/{job['ref']}/design/approve", headers=boss).status_code == 200
    st = {s["stage"]: s for s in client.get(f"/api/v1/jobs/{job['ref']}", headers=boss).json()["stages"]}
    assert st["DESIGN_APPROVED"]["done"] and st["DESIGN_APPROVED"]["by"] == me and st["DESIGN_APPROVED"]["entered_at"]
    assert st["SHIPPED"]["entered_at"] is None and st["SHIPPED"]["by"] is None


def test_the_job_list_does_not_carry_stage_history(client):
    boss = _h(_me(), "MANAGER")
    _job(client, boss)
    items = client.get("/api/v1/jobs", headers=boss).json()["items"]
    assert items and all("stages" not in i for i in items)


# ---------------------------------------------------------------- design revision record and customs patch

BOSS = {"email": "boss-r7@alliedweldingsystems.com", "role": "ADMIN"}


def _sent_po(client, h, lines=None, status="SENT"):
    from app.services.coordination import purchase_orders, rfqs
    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB
    r = rfqs.create({"customer": "Owens Corning", "subject": f"r7 rev {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    from app.services.coordination import jobs
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], f"Vendor {uuid.uuid4().hex[:5]}", BOSS, lines=lines or [{"quantity": 2, "description": "liner", "unit_cost": 5, "revision": "B"}, {"quantity": 1, "description": "ring", "unit_cost": 3, "revision": "C"}])
    po = purchase_orders.approve(po["id"], BOSS)
    if status:
        with SessionLocal() as db:
            db.get(PurchaseOrderDB, po["id"]).status = status
            db.commit()
    return po, j


def test_a_sent_po_shows_the_revision_sent_and_none_acknowledged_yet(client):
    h = _h(_me(), "MANAGER")
    po, _ = _sent_po(client, h)
    got = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()
    assert got["revision_sent"] == ["B", "C"] and got["revision_acked"] == [None, None] and got["revision_mismatch"] == []
    assert got["revisions"][0] == {"line": 1, "revision_sent": "B", "revision_acked": None, "acked_by": None, "acked_at": None, "mismatch": False}


def test_a_draft_or_approved_po_has_no_revision_sent_yet(client):
    h = _h(_me(), "MANAGER")
    po, _ = _sent_po(client, h, status=None)
    assert client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()["revision_sent"] == [None, None]
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": "B"}, headers=h).status_code == 400


def test_the_vendor_acknowledging_the_same_revision_is_no_mismatch_and_a_different_one_is(client):
    h = _h(_me(), "OPERATOR")
    po, j = _sent_po(client, h)
    ok = client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": " b ", "by": "Helen at the vendor"}, headers=h)
    assert ok.status_code == 200 and ok.json()["revision_mismatch"] == [] and ok.json()["revisions"][0]["acked_by"] == "Helen at the vendor"
    bad = client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 2, "revision": "A"}, headers=h).json()
    assert bad["revision_mismatch"] == [2] and bad["revisions"][1]["mismatch"] is True
    from app.services.coordination import jobs
    assert "revision_mismatch" in {c["key"] for c in jobs.get(j["id"])["checks"]}


@pytest.mark.parametrize("body", [{"line": 0, "revision": "B"}, {"line": 3, "revision": "B"}, {"line": True, "revision": "B"}, {"line": "x", "revision": "B"}, {"line": 1, "revision": ""},
                                  {"line": 1}, {"line": [1], "revision": "B"}, {"line": 1.5, "revision": "B"}])
def test_a_bad_revision_ack_is_refused_plainly(client, body):
    h = _h(_me(), "OPERATOR")
    po, _ = _sent_po(client, h)
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json=body, headers=h).status_code == 400


def test_a_viewer_cannot_record_a_revision_ack(client):
    po, _ = _sent_po(client, _h(_me(), "MANAGER"))
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": "B"}, headers=_h(_me(), "VIEWER")).status_code == 403


def test_customs_details_can_be_set_after_approval_and_after_sending_without_touching_the_core(client):
    h = _h(_me(), "OPERATOR")
    po, _ = _sent_po(client, h)
    r = client.patch(f"/api/v1/purchase-orders/{po['id']}/customs", headers=h, json={"lines": [{"line": 2, "hts_code": "7318.15.20", "origin": "China", "uom": "EA", "net_kg": 1.5, "cert_required": True}],
                                                                                    "incoterm": "fob", "incoterm_place": "Ningbo"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["lines"][1]["hts_code"] == "7318.15.20" and got["lines"][1]["origin"] == "China" and got["lines"][1]["net_kg"] == 1.5 and got["lines"][1]["cert_required"] is True
    assert got["incoterm"] == "FOB" and got["incoterm_place"] == "Ningbo"
    assert got["lines"][0].get("hts_code") is None and got["lines"][1]["unit_cost"] == 3 and got["lines"][1]["revision"] == "C" and got["status"] == "SENT"
    cleared = client.patch(f"/api/v1/purchase-orders/{po['id']}/customs", headers=h, json={"lines": [{"line": 2, "origin": ""}]}).json()
    assert "origin" not in cleared["lines"][1] and cleared["lines"][1]["hts_code"] == "7318.15.20"


@pytest.mark.parametrize("body", [{"lines": [{"line": 9, "origin": "x"}]}, {"lines": [{"origin": "x"}]}, {"lines": "x"}, {"lines": [{"line": 1, "hts_code": "banana"}]}, {"incoterm": "XYZ"},
                                  {"lines": [{"line": 1, "net_kg": "heavy"}]}, {"lines": [1]}, {"lines": [{"line": True}]}])
def test_bad_customs_details_are_refused_and_change_nothing(client, body):
    h = _h(_me(), "OPERATOR")
    po, _ = _sent_po(client, h)
    before = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}/customs", headers=h, json=body).status_code == 400
    assert client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()["lines"] == before["lines"]


@pytest.mark.parametrize("status", ["SENDING", "CANCELLED"])
def test_customs_cannot_change_while_sending_or_after_cancelling(client, status):
    h = _h(_me(), "OPERATOR")
    po, _ = _sent_po(client, h, status=status)
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}/customs", headers=h, json={"incoterm": "FOB"}).status_code == 400


# ---------------------------------------------------------------- orders inbox, me/needs lines, plain status words

def test_the_orders_inbox_merges_the_sections_and_filters_by_type(client):
    from app.models.db_models import ExceptionDB, MailLogDB, PackingSlipDB
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(MailLogDB(message_id=f"<r7-{tag}@x>", folder="INBOX", sender="a@b.com", subject=f"PO attached {tag}", direction="in", category="order", outcome="LOGGED", needs_action=True,
                         received_at=datetime.now(timezone.utc) - timedelta(days=3)))
        db.add(ExceptionDB(exception_id=f"EX-{tag}", exception_type="INVOICE_MISMATCH", title=f"Bill {tag} is over", description="d", reference_id=f"PO-{tag}", vendor_or_customer="V",
                           variance_amount=12.5, status="OPEN"))
        db.add(ExceptionDB(exception_id=f"EY-{tag}", exception_type="ORDER_CHECK", title=f"Order {tag} check", description="d", reference_id=f"SO-{tag}", vendor_or_customer="C", status="OPEN"))
        db.add(PackingSlipDB(sales_order_number=f"SO-R7-{tag}", customer_po_number=f"CP-{tag}", status="FAILED", created_at=datetime.now(timezone.utc) - timedelta(days=1)))
        db.commit()
    h = _h(_me())
    everything = client.get("/api/v1/orders/inbox", headers=h).json()
    mine = [i for i in everything["items"] if tag in (i["title"] + i["ref"])]
    assert {i["kind"] for i in mine} == {"email", "bill", "order", "slip"}
    assert all(set(i) == {"kind", "ref", "title", "status_word", "amount", "currency", "age_days", "link"} for i in mine)
    ages = [i["age_days"] for i in everything["items"]]
    assert ages == sorted(ages, reverse=True) and everything["total"] == sum(everything["counts"].values())
    only = client.get("/api/v1/orders/inbox?filter=bills", headers=h).json()
    assert {i["kind"] for i in only["items"]} == {"bill"} and any(i["amount"] == 12.5 for i in only["items"])
    assert {i["kind"] for i in client.get("/api/v1/orders/inbox?filter=slips", headers=h).json()["items"]} == {"slip"}
    assert {i["kind"] for i in client.get("/api/v1/orders/inbox?filter=emails", headers=h).json()["items"]} == {"email"}
    assert client.get("/api/v1/orders/inbox?filter=nonsense", headers=h).status_code == 422


def test_me_needs_lines_have_links_and_plain_words_and_the_count_matches(client):
    me = _me()
    nid = _alert(me)
    got = client.get("/api/v1/me/needs", headers=_h(me)).json()
    assert got["total"] == sum(got["counts"].values()) == got["counts"]["alerts"] + got["counts"]["drafts"] + got["counts"]["ball"]
    a = next(x for x in got["alerts"] if x["id"] == nid)
    assert a["status_word"] == "Behind" and a["link"] and "age_days" in a
    assert {i["kind"] for i in got["items"]} >= {"alert"} and all(set(i) == {"kind", "ref", "title", "status_word", "link", "age_days"} for i in got["items"])


def test_a_snoozed_alert_is_not_counted_in_me_needs(client):
    me = _me()
    nid = _alert(me)
    before = client.get("/api/v1/me/needs", headers=_h(me)).json()["total"]
    client.post(f"/api/v1/alerts/{nid}/snooze", json={"hours": 2}, headers=_h(me))
    assert client.get("/api/v1/me/needs", headers=_h(me)).json()["total"] == before - 1


def test_status_words_are_plain_and_never_blank_or_a_raw_code():
    from app.core import status_words as w
    assert w.word("po", "SENT") == "Sent to the vendor" and w.word("order", "SAGE_ERROR").startswith("Needs a person")
    assert w.word("shipment", "weird_new_code") == "Weird new code"
    assert w.word("order", None) == "" and w.word("nope", "SOME_THING") == "Some thing"


def test_status_word_sits_next_to_the_raw_code_on_pos_orders_shipments_and_rfqs(client):
    h = _h(_me(), "MANAGER")
    po, _ = _sent_po(client, h)
    got = client.get(f"/api/v1/purchase-orders/{po['id']}", headers=h).json()
    assert got["status"] == "SENT" and got["status_word"] == "Sent to the vendor"
    jobs_item = client.get("/api/v1/jobs", headers=h).json()["items"][0]
    assert jobs_item["stage_word"]
    orders = client.get("/api/v1/orders?limit=5", headers=h).json()["items"]
    assert all("status_word" in o for o in orders)
    assert all("status_word" in r for r in client.get("/api/v1/rfqs", headers=h).json()["items"]) if "items" in client.get("/api/v1/rfqs", headers=h).json() else True

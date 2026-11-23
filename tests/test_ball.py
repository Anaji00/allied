"""Owner and "ball is with": handing over, the clock, the chasing ladder, worked-out balls, the management view.
Test data only; no mail leaves and nothing touches monday."""

import uuid

import pytest

from app.core.database import SessionLocal
from app.models.db_models import ItemBallDB, NotificationDB, StaffContactDB
from app.services.coordination import ball, updates
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
ATHISH, ELI, DOUG = (f"{n}{TAG}@allied.test" for n in ("athish", "eli", "doug"))
ME = {"email": ATHISH, "role": "MANAGER"}


@pytest.fixture(autouse=True)
def _people(client):
    with SessionLocal() as db:
        for email, name in ((ATHISH, f"Athish{TAG}"), (ELI, f"Eli{TAG}"), (DOUG, f"Doug{TAG}")):
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


def _row(so):
    with SessionLocal() as db:
        return db.query(ItemBallDB).filter(ItemBallDB.item_type == "order", ItemBallDB.item_ref == so).one()


def _alerts(prefix):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB).all() if n.dedup_key.startswith(prefix)]


def _days(monkeypatch, value):
    monkeypatch.setattr(ball, "_days", lambda since: value[0])
    return value


# ------------------------------------------------------------- handing the ball over


def test_handing_the_ball_to_an_engineer_starts_the_clock_writes_the_thread_and_alerts_them():
    so = _order()
    b = ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH, note="Check revision C against the PO", due="2099-01-01")
    assert b["ball_with"] == "engineer" and b["ball_name"] == f"Eli{TAG}" and b["owner_name"] == f"Athish{TAG}"
    assert b["days_waiting"] == 0 and b["status"] == "on track" and b["history"][0]["to"].startswith("engineering")
    line = updates.thread("order", so)[0]
    assert "Ball is now with" in line["body"] and "Check revision C" in line["body"] and [m["email"] for m in line["mentions"]] == [ELI]
    assert any(n.recipients == [ELI] for n in _alerts(f"mention:{line['id']}:"))
    assert ball.get("order", so)["note"] == "Check revision C against the PO"


def test_the_ball_can_sit_with_an_outsider_but_every_choice_must_make_sense():
    so = _order()
    assert ball.set_ball("order", so, ME, "vendor", "KINGCERA")["ball_name"] == "KINGCERA"
    with pytest.raises(ball.BallError, match="Say who it is waiting on"):
        ball.set_ball("order", _order(), ME, "customer", "  ")
    with pytest.raises(ball.BallError, match="I don't know"):
        ball.set_ball("order", _order(), ME, "engineer", "ghost@allied.test")
    with pytest.raises(ball.BallError, match="Whose turn"):
        ball.set_ball("order", _order(), ME, "wizard", "x")
    with pytest.raises(ball.BallError, match="Say whose turn"):
        ball.set_ball("order", _order(), ME)
    with pytest.raises(ball.BallError, match="Pick the owner"):
        ball.set_ball("order", _order(), ME, "us", owner="ghost@allied.test")
    with pytest.raises(ball.BallError, match="due date"):
        ball.set_ball("order", _order(), ME, "us", due="soon")
    with pytest.raises(LookupError):
        ball.set_ball("order", "99999999", ME, "us")


def test_changing_only_the_note_keeps_the_clock_but_a_new_holder_restarts_it_and_the_chasing(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH)
    with SessionLocal() as db:
        db.query(ItemBallDB).filter(ItemBallDB.item_ref == so).update({"escalation_level": 2})
        db.commit()
    first_since = _row(so).ball_since
    ball.set_ball("order", so, ME, note="Still waiting for the revised drawing")           # same holder
    assert _row(so).ball_since == first_since and _row(so).escalation_level == 2 and len(_row(so).history) == 1
    ball.set_ball("order", so, ME, "engineer", DOUG)                                        # a different engineer
    r = _row(so)
    assert r.escalation_level == 0 and r.ball_since > first_since and len(r.history) == 2 and r.history[1]["from"].startswith("engineering")


def test_done_stops_the_chasing_and_a_later_hand_over_reopens_it(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "vendor", "NIBROCK", owner=ATHISH)
    done = ball.resolve("order", so, ME)
    assert done["resolved"] and done["status"] == "done"
    assert so not in [i["item_ref"] for i in ball.list_open(include_derived=False)]
    _days(monkeypatch, [30])
    assert ball.run() == {"nudged": 0, "escalated": 0, "leadership": 0} or not _alerts(f"ball:{_row(so).id}:")
    with pytest.raises(LookupError):
        ball.resolve("order", so, ME)
    ball.set_ball("order", so, ME, "customer", "Owens Corning")
    assert not _row(so).resolved_at and so in [i["item_ref"] for i in ball.list_open(include_derived=False)]


# ------------------------------------------------------------- the chasing ladder


def test_the_ladder_reminds_the_holder_then_tells_the_owner_then_leadership_once_each(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH, note="revision check")
    rid, d = _row(so).id, _days(monkeypatch, [1.0])
    ball.run()
    assert not _alerts(f"ball:{rid}:")                                                       # one day: nothing yet
    d[0] = 2.0
    ball.run(), ball.run()                                                                   # twice: still one reminder
    first = _alerts(f"ball:{rid}:")
    assert len(first) == 1 and first[0].recipients == [ELI] and "Still with you" in first[0].title
    d[0] = 4.0
    ball.run()
    second = [n for n in _alerts(f"ball:{rid}:") if n.dedup_key.endswith(":2")][0]
    assert set(second.recipients) == {ATHISH, ELI} and second.severity == "warning"
    d[0] = 7.0
    assert ball.run()["leadership"] >= 1                                                      # (other tests' open balls climb too)
    third = [n for n in _alerts(f"ball:{rid}:") if n.dedup_key.endswith(":3")][0]
    assert third.severity == "critical" and "Stuck" in third.title
    assert ball.run() == {"nudged": 0, "escalated": 0, "leadership": 0}
    assert ball.get("order", so)["status"] == "leadership told"


def test_when_an_outsider_has_the_ball_the_owner_is_told_to_follow_up(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "vendor", "KINGCERA", owner=ELI)
    _days(monkeypatch, [2.0])
    ball.run()
    n = _alerts(f"ball:{_row(so).id}:")[0]
    assert n.recipients == [ELI] and "follow up with KINGCERA" in n.title


def test_a_passed_due_date_goes_straight_to_telling_the_owner(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH, due="2000-01-01")
    _days(monkeypatch, [0.5])
    assert ball.run()["escalated"] == 1
    assert "was due Jan 1, 2000" in _alerts(f"ball:{_row(so).id}:")[0].body
    assert ball.get("order", so)["overdue"] is True


def test_moving_the_ball_chases_again_for_the_new_holder(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH)
    _days(monkeypatch, [2.0])
    ball.run()
    ball.set_ball("order", so, ME, "engineer", DOUG)
    ball.run()
    mine = _alerts(f"ball:{_row(so).id}:")
    assert len(mine) == 2 and {tuple(n.recipients) for n in mine} == {(ELI,), (DOUG,)}


# ------------------------------------------------------------- lists and the management view


def test_lists_merge_worked_out_balls_filter_by_person_and_kind_and_sort_by_wait(monkeypatch):
    so = _order()
    ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH)
    fake = [{"item_type": "rfq", "item_ref": "RFQ-X", "label": "RFQ X", "derived": True, "owner_email": ELI, "owner_name": "", "ball_with": "vendor",
             "ball_label": "A vendor", "ball_person": "KWS", "ball_name": "KWS", "note": "", "due_at": None, "overdue": False,
             "since": None, "days_waiting": 6.0, "level": 3, "status": "worked out from the item", "resolved": False, "history": []},
            {"item_type": "order", "item_ref": so, "label": "dup", "derived": True, "owner_email": None, "owner_name": "", "ball_with": "us",
             "ball_label": "Us", "ball_person": "", "ball_name": "", "note": "", "due_at": None, "overdue": False, "since": None,
             "days_waiting": 99.0, "level": 3, "status": "x", "resolved": False, "history": []}]
    monkeypatch.setattr(ball, "_derived", lambda: fake)
    everything = ball.list_open()
    assert everything[0]["item_ref"] == "RFQ-X" and sum(i["item_ref"] == so for i in everything) == 1          # a set ball beats a worked-out one
    assert so in [i["item_ref"] for i in ball.list_open("me", ELI) if i["item_type"] == "order"]
    assert {i["ball_with"] for i in ball.list_open(kind="vendor")} == {"vendor"}
    assert all(i["days_waiting"] >= 2 or i["overdue"] for i in ball.list_open(late_only=True))
    s = ball.summary()
    assert s["open"] >= 2 and any(k["kind"] == "vendor" for k in s["by_kind"]) and s["oldest"][0]["item_ref"] == "RFQ-X"
    assert s["rules"] == {"nudge_days": 2, "escalate_days": 4, "leadership_days": 7}


def test_the_morning_brief_lists_what_is_waiting_on_you(monkeypatch):
    from app.services.coordination import secretary

    so = _order()
    ball.set_ball("order", so, ME, "engineer", ELI, owner=ATHISH, note="revision check")
    _days(monkeypatch, [3.0])
    lines = secretary.brief_for(ELI)
    assert any("Waiting on you for 3 day(s)" in x and "revision check" in x for x in lines)


# ------------------------------------------------------------- the API


def test_the_api_roles_and_shapes(auth_enforced, client):
    def tok(role, name):
        return {"Authorization": f"Bearer {staff_token(f'{name}{TAG}@alliedweldingsystems.com', role)}"}

    v, o = tok("VIEWER", "bviewer"), tok("OPERATOR", "boper")
    so = _order()
    q = {"item_type": "order", "item_ref": so}
    assert client.get("/api/v1/ball", params=q).status_code in (401, 403)
    assert client.get("/api/v1/ball", params=q, headers=v).json() is None
    body = {**q, "ball_with": "engineer", "ball_person": ELI, "owner": ATHISH, "note": "check it"}
    assert client.put("/api/v1/ball", json=body, headers=v).status_code == 403
    done = client.put("/api/v1/ball", json=body, headers=o)
    assert done.status_code == 200 and done.json()["ball_name"] == f"Eli{TAG}"
    assert client.get("/api/v1/ball", params=q, headers=v).json()["note"] == "check it"
    assert client.put("/api/v1/ball", json={**q, "ball_with": "wizard"}, headers=o).status_code == 400
    assert client.put("/api/v1/ball", json={"item_type": "order", "item_ref": "1", "ball_with": "us"}, headers=o).status_code == 404
    assert client.get("/api/v1/ball", params={"item_type": "nope", "item_ref": "1"}, headers=v).status_code == 400
    assert any(i["item_ref"] == so for i in client.get("/api/v1/ball/open", headers=v).json())
    assert client.get("/api/v1/ball/open", params={"who": "everyone"}, headers=v).status_code == 422
    assert client.get("/api/v1/ball/summary", headers=v).json()["open"] >= 1
    assert client.post("/api/v1/ball/run", headers=v).status_code == 403
    assert set(client.post("/api/v1/ball/run", headers=o).json()) == {"nudged", "escalated", "leadership"}
    assert client.post("/api/v1/ball/resolve", json=q, headers=o).json()["resolved"] is True
    assert client.post("/api/v1/ball/resolve", json=q, headers=o).status_code == 404

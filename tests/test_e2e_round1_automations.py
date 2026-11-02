"""Round 1: the automations that had no end-to-end test, as Allied's real situations, stressed.

Each test follows one real situation through the checks and plays that act on it, then
pushes it: the hourly run repeated, the situation getting worse, getting fixed, and the
odd case. Time moves by giving records past dates. Temporary database and fakes only."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import (AssistantActionDB, MondayMilestoneDB, MondayProjectDB, NotificationDB, RfqDB,
                                  SubmissionDB, VendorJobDB)
from app.services.coordination import autopilot, events, rfqs, schedule, shipments, vendors
from app.services.coordination import notify as alerts
from app.services.coordination import workdays

LEAD = "boss@alliedweldingsystems.com"
DOUG = "doug@alliedweldingsystems.com"
TODAY = alerts.today()


@pytest.fixture(autouse=True)
def _allied(monkeypatch):
    for k, v in {"AUTOPILOT_ENABLED": True, "AUTOPILOT_EVENTS_ASYNC": False, "AUTOPILOT_REVIEWER_EMAILS": DOUG,
                 "LEADERSHIP_EMAILS": LEAD, "RFQ_TEAM_EMAILS": DOUG, "AI_PROVIDER": "off"}.items():
        monkeypatch.setattr(settings, k, v)


def _open(prefix):
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{prefix}%"),
                                               NotificationDB.acknowledged_at.is_(None)).all()


def _all(prefix):
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{prefix}%")).all()


def _drafts(key_part):
    with SessionLocal() as db:
        return [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT")
                if key_part in (r.summary or "")]


def _age_rfq(rfq_id, **fields):
    with SessionLocal() as db:
        row = db.get(RfqDB, rfq_id)
        for k, v in fields.items():
            setattr(row, k, v)
        db.commit()


def _days_ago(n):
    return workdays.now() - timedelta(days=n)


# ---------------------------------------------------------------------------
# RFQs: Hecla Mining's spool piece, from the email to the win
# ---------------------------------------------------------------------------


def test_hecla_rfq_through_acknowledgement_vendor_pricing_quote_and_win(monkeypatch):
    r = rfqs.create({"customer": "Hecla-Keno Hill", "subject": "Spool Piece 6in", "contact_email": "buyer@hecla.test",
                     "received_at": _days_ago(4), "owner_email": DOUG}, {"email": "intake"}, source="email")
    # Not acknowledged for 4 days: one alert, however many times the 15-minute check runs.
    for _ in range(5):
        rfqs.run_checks()
    assert len(_all(f"rfq-ack:{r['id']}:")) == 1
    # Doug acknowledges: the "acknowledge it" alert must close, not nag on.
    rfqs.update(r["id"], {"status": "ACKNOWLEDGED"}, {"email": DOUG})
    rfqs.run_checks()
    assert not _open(f"rfq-ack:{r['id']}:")
    # Waiting on Helen and Duke for prices: Helen is asked by email, Duke (no email) by hand.
    rfqs.update(r["id"], {"status": "WAITING_VENDOR", "vendor": "Helen, Duke"}, {"email": DOUG})
    asks = _drafts(f"price {r['number']}")
    assert len(asks) == 1 and asks[0].args["to"].startswith("helen@")   # Helen's address from the vendor list
    assert len(_all(f"autopilot:rfq-price-ask:{r['id']}:no-email")) == 1
    # Three working days later Helen hasn't priced it: a reminder once this week, and the team is told.
    _age_rfq(r["id"], waiting_since=workdays.add_days(workdays.now(), -3))
    autopilot.run("rfq_vendor_chaser")
    autopilot.run("rfq_vendor_chaser")
    assert len(_drafts(f"price on {r['number']}")) == 1
    rfqs.run_checks()
    assert len(_open(f"rfq-vendor:{r['id']}:chase")) == 1
    # Five working days: escalated to leadership, the chase note closes.
    _age_rfq(r["id"], waiting_since=workdays.add_days(workdays.now(), -5))
    rfqs.run_checks()
    esc = _open(f"rfq-vendor:{r['id']}:escalate")
    assert len(esc) == 1 and LEAD in esc[0].recipients
    # Priced and quoted; no answer for 3 working days: one follow-up to the customer.
    rfqs.update(r["id"], {"status": "PRICING"}, {"email": DOUG})
    rfqs.update(r["id"], {"status": "QUOTE_SENT", "quote_number": "Q-26-118", "quote_amount": "8,450"}, {"email": DOUG})
    assert not _open(f"rfq-vendor:{r['id']}:")                      # no longer waiting on a vendor
    _age_rfq(r["id"], quoted_at=workdays.add_days(workdays.now(), -3))
    autopilot.run("quote_followup")
    autopilot.run("quote_followup")
    follow = [d for d in _drafts(r["number"]) if (d.args or {}).get("_play") == "quote_followup"]
    assert len(follow) == 1 and follow[0].args["to"] == "buyer@hecla.test"
    # Won: the team and leadership hear once, even if the status is saved twice.
    rfqs.update(r["id"], {"status": "WON"}, {"email": DOUG})
    events.emit("rfq.status_changed", {"rfq": rfqs.get(r["id"]), "from": "QUOTE_SENT", "to": "WON"})
    won = _all(f"autopilot:rfq-won:{r['id']}")
    assert len(won) == 1 and LEAD in won[0].recipients
    # Closed: every open RFQ alert for it is cleared.
    rfqs.run_checks()
    left = [n.dedup_key for n in _open("rfq-") if n.dedup_key.split(":")[1] == str(r["id"])]
    assert not left, left


def test_an_rfq_past_its_quote_date_is_critical_for_leadership_only_once_a_day_late(monkeypatch):
    r = rfqs.create({"customer": "TECO", "subject": '2" Transport', "contact_email": "p@teco.test",
                     "received_at": _days_ago(20), "owner_email": DOUG}, {"email": "intake"}, source="email")
    rfqs.update(r["id"], {"status": "PRICING"}, {"email": DOUG})
    for _ in range(3):
        rfqs.run_checks()
    late = _open(f"rfq-overdue:{r['id']}:")
    assert len(late) == 1 and late[0].severity == "critical" and LEAD in late[0].recipients


# ---------------------------------------------------------------------------
# Vendor orders: HELEN hasn't confirmed the PO; GRACE ships next week
# ---------------------------------------------------------------------------


def _vendor_job(**kw):
    job = vendors.create({"vendor": f"HELEN-{uuid.uuid4().hex[:4]}", "description": "OC-AMARILLO - AUGER",
                          "po_number": str(9000 + uuid.uuid4().int % 999), "owner_email": DOUG, **kw}, {"email": DOUG})
    return job


def _drop_job(job):
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


def test_a_po_nobody_confirmed_is_chased_once_and_clears_when_the_vendor_confirms():
    job = _vendor_job(ordered_on=workdays.add_days(workdays.now(), -5).date().isoformat())
    try:
        for _ in range(4):
            vendors.run_checks()
        assert len(_all(f"vendor-noack:{job['id']}:")) == 1
        vendors.update(job["id"], {"acknowledged_on": TODAY.isoformat()}, {"email": DOUG})
        vendors.run_checks()
        assert not _open(f"vendor-noack:{job['id']}:")
    finally:
        _drop_job(job)


def test_an_order_about_to_ship_is_reconfirmed_once_then_turns_late_and_is_chased():
    soon = (TODAY + timedelta(days=3)).isoformat()
    job = _vendor_job(ordered_on=(TODAY - timedelta(days=40)).isoformat(), promised_on=soon,
                      acknowledged_on=(TODAY - timedelta(days=38)).isoformat())
    try:
        vendors.run_checks(TODAY)
        vendors.run_checks(TODAY)
        assert len(_all(f"vendor-checkin:{job['id']}:")) == 1
        # The promised day passes and it hasn't shipped: the reconfirm note closes, a late alert opens.
        later = TODAY + timedelta(days=6)
        vendors.run_checks(later)
        assert not _open(f"vendor-checkin:{job['id']}:") and len(_open(f"vendor-late:{job['id']}:")) == 1
        # It ships: everything about it closes.
        vendors.update(job["id"], {"shipped_on": later.isoformat()}, {"email": DOUG})
        vendors.run_checks(later)
        assert not _open(f"vendor-late:{job['id']}:")
    finally:
        _drop_job(job)


# ---------------------------------------------------------------------------
# Engineering: the Splitter Box drawing for Intrepid Potash
# ---------------------------------------------------------------------------


def _project(due_offset=None, ms=()):
    pid = f"p{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(MondayProjectDB(item_id=pid, board_id="1", name="Intrepid Potash", owner_emails=[DOUG],
                               due_date=(TODAY + timedelta(days=due_offset)).isoformat() if due_offset is not None else None))
        for name, offset, status in ms:
            db.add(MondayMilestoneDB(item_id=f"m{uuid.uuid4().hex[:8]}", project_item_id=pid, board_id="1", name=name,
                                     owner_emails=["paul.wang@alliedweldingsystems.com"], status=status,
                                     due_date=(TODAY + timedelta(days=offset)).isoformat(),
                                     done=status in ("Complete", "Sent to Shop")))
        db.commit()
    return pid


def _ms(pid):
    with SessionLocal() as db:
        return {m.name: m.item_id for m in db.query(MondayMilestoneDB).filter(MondayMilestoneDB.project_item_id == pid)}


def _set_ms(item_id, **kw):
    with SessionLocal() as db:
        m = db.get(MondayMilestoneDB, item_id)
        for k, v in kw.items():
            setattr(m, k, v)
        db.commit()


def test_a_drawing_walks_from_due_soon_to_late_to_escalated_with_one_alert_at_each_step(monkeypatch):
    monkeypatch.setattr(settings, "MILESTONE_DONE_LABELS", "Complete,Sent to Shop")
    monkeypatch.setattr(settings, "MILESTONE_HOLD_LABELS", "Paused,On Hold")
    pid = _project(ms=[("Splitter Box", 1, "Working on it")])
    mid = _ms(pid)["Splitter Box"]
    for _ in range(3):
        schedule.run_checks()
    assert len(_all(f"ms-due-soon:{mid}:")) == 1
    _set_ms(mid, due_date=(TODAY - timedelta(days=1)).isoformat())       # a day late
    schedule.run_checks()
    assert not _open(f"ms-due-soon:{mid}:") and len(_open(f"ms-overdue:{mid}:")) == 1
    _set_ms(mid, due_date=(TODAY - timedelta(days=5)).isoformat())       # well past: leadership, in one alert
    schedule.run_checks()
    esc = _open(f"ms-escalate:{mid}:")
    assert len(esc) == 1 and LEAD in esc[0].recipients
    _set_ms(mid, status="Paused")                                         # on hold: nothing to chase
    schedule.run_checks()
    assert not _open(f"ms-escalate:{mid}:") and not _open(f"ms-overdue:{mid}:")
    _set_ms(mid, status="Complete", done=True)
    schedule.run_checks()
    assert not _open(f"ms-") or all(mid not in n.dedup_key for n in _open("ms-"))


def test_submitted_drawings_waiting_for_review_and_a_late_project_reach_the_right_people(monkeypatch):
    pid = _project(due_offset=-3, ms=[("Headlap Elevator #2 Discharge Chute", 10, "Sent for Review")])
    mid = _ms(pid)["Headlap Elevator #2 Discharge Chute"]
    with SessionLocal() as db:
        db.add(SubmissionDB(milestone_item_id=mid, project_item_id=pid, version=1, files=[], submitted_by="paul.wang@alliedweldingsystems.com",
                            submitted_at=datetime.now(timezone.utc) - timedelta(days=4), status="SUBMITTED"))
        db.commit()
    schedule.run_checks()
    schedule.run_checks()
    assert len(_all("review-wait:")) >= 1
    late = _open(f"proj-overdue:{pid}:")
    assert len(late) == 1 and late[0].severity == "critical" and LEAD in late[0].recipients


# ---------------------------------------------------------------------------
# Freight: the NOL-TEC container
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case, fields, prefix", [
    ("late", {"status": "DEPARTED", "eta": "{-3}"}, "ship-late:"),
    ("rolled", {"status": "BOOKED", "etd": "{-2}", "eta": "{+30}"}, "ship-rolled:"),
    ("free time", {"status": "DISCHARGED", "eta": "{-5}", "free_time_ends": "{+1}"}, "ship-free-time:"),
])
def test_each_freight_problem_alerts_once_and_clears_when_delivered(case, fields, prefix):
    data = {k: ((TODAY + timedelta(days=int(v[1:-1]))).isoformat() if isinstance(v, str) and v.startswith("{") else v)
            for k, v in fields.items()}
    s = shipments.create({"reference": f"NOL-TEC {case}", "container_no": f"MSCU{uuid.uuid4().int % 10**7:07d}",
                          "owner_email": DOUG, **data}, {"email": DOUG})
    for _ in range(3):
        schedule.run_checks()
    assert len(_all(f"{prefix}{s['id']}:")) == 1, case
    shipments.update(s["id"], {"status": "DELIVERED"}, DOUG)
    schedule.run_checks()
    assert not _open(f"{prefix}{s['id']}:"), case


def test_an_eta_that_keeps_slipping_alerts_for_each_new_date_not_every_hour():
    s = shipments.create({"reference": "KWS augers", "status": "DEPARTED", "owner_email": DOUG,
                          "eta": (TODAY + timedelta(days=10)).isoformat()}, {"email": DOUG})
    for slip in (10, 10, 17, 17, 24):
        shipments.update(s["id"], {"eta": (TODAY + timedelta(days=10 + slip)).isoformat()}, "forwarder", source="email")
        schedule.run_checks()
    assert len(_all(f"ship-slip:{s['id']}:")) == 3                      # three different ETAs, three alerts


# ---------------------------------------------------------------------------
# The daily summary
# ---------------------------------------------------------------------------


def test_the_morning_summary_goes_once_a_day_and_says_all_is_well_when_it_is(monkeypatch):
    first = schedule.send_summary()
    again = schedule.send_summary()
    assert first and not again                                            # once a day
    assert schedule.send_summary(force=True)                              # unless asked for
    text = schedule.build_summary()["text"]
    assert text.startswith("Allied operations summary for")



def test_working_days_count_back_as_well_as_forward():
    friday = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
    back = workdays.add_days(friday, -3).astimezone(alerts.now_local().tzinfo).date()
    ahead = workdays.add_days(friday, 3).astimezone(alerts.now_local().tzinfo).date()
    assert back == date(2026, 10, 27) and ahead == date(2026, 11, 4)   # skipping the weekend both ways

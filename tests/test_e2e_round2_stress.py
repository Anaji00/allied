"""Round 2: the checks under pressure, as Allied's boards really behave.

Deadlines get moved (often several times), the boards are full, the checks run every 15
minutes all day, and alerts arrive at night. The property throughout: one live alert per
real problem, never a pile of stale ones. Temporary database and fakes only."""

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayMilestoneDB, MondayProjectDB, NotificationDB, RfqDB, ShipmentDB, VendorJobDB
from app.services.coordination import rfqs, schedule, shipments, vendors
from app.services.coordination import notify as alerts
from app.services.coordination import workdays

DOUG = "doug@alliedweldingsystems.com"
LEAD = "boss@alliedweldingsystems.com"
TODAY = alerts.today()


@pytest.fixture(autouse=True)
def _allied(monkeypatch):
    for k, v in {"AUTOPILOT_ENABLED": True, "AUTOPILOT_EVENTS_ASYNC": False, "LEADERSHIP_EMAILS": LEAD,
                 "RFQ_TEAM_EMAILS": DOUG, "AI_PROVIDER": "off", "MILESTONE_DONE_LABELS": "Complete",
                 "MILESTONE_HOLD_LABELS": "Paused"}.items():
        monkeypatch.setattr(settings, k, v)


def _open(prefix):
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{prefix}%"),
                                               NotificationDB.acknowledged_at.is_(None)).all()


def _day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------------------
# Moving deadlines: one live alert per problem
# ---------------------------------------------------------------------------


def test_a_drawing_whose_due_date_keeps_moving_has_one_live_alert(monkeypatch):
    pid, mid = f"p{uuid.uuid4().hex[:8]}", f"m{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(MondayProjectDB(item_id=pid, board_id="1", name="OC-Irving", owner_emails=[DOUG]))
        db.add(MondayMilestoneDB(item_id=mid, project_item_id=pid, board_id="1", name="TCL Finger Guides Rev 2",
                                 owner_emails=[DOUG], status="Working on it", due_date=_day(-1)))
        db.commit()
    for new_due in (-1, -3, -1, -6, 1):          # Paul moves the Estimate finish around all week
        with SessionLocal() as db:
            db.get(MondayMilestoneDB, mid).due_date = _day(new_due)
            db.commit()
        schedule.run_checks()
        live = [n for p in ("ms-due-soon:", "ms-overdue:", "ms-escalate:") for n in _open(f"{p}{mid}:")]
        assert len(live) == 1, (new_due, [n.dedup_key for n in live])


def test_a_vendor_whose_promise_keeps_slipping_has_one_live_late_alert():
    job = vendors.create({"vendor": f"KWS-{uuid.uuid4().hex[:4]}", "description": "OC-AMARILLO - AUGERS", "po_number": "9277",
                          "owner_email": DOUG, "ordered_on": _day(-60), "acknowledged_on": _day(-58),
                          "promised_on": _day(-5)}, {"email": DOUG})
    try:
        for promise in (-5, -2, -1, -9):
            vendors.update(job["id"], {"promised_on": _day(promise)}, {"email": DOUG})
            vendors.run_checks()
            assert len(_open(f"vendor-late:{job['id']}:")) == 1, promise
    finally:
        with SessionLocal() as db:
            db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
            db.commit()


def test_a_shipment_whose_eta_keeps_slipping_has_one_live_slip_alert():
    s = shipments.create({"reference": "NOL-TEC liners", "status": "DEPARTED", "owner_email": DOUG,
                          "eta": _day(10)}, {"email": DOUG})
    for slip in (17, 24, 31):
        shipments.update(s["id"], {"eta": _day(slip)}, "forwarder", source="email")
        schedule.run_checks()
        assert len(_open(f"ship-slip:{s['id']}:")) == 1, slip


def test_an_rfq_whose_deadline_is_extended_has_one_live_overdue_alert():
    r = rfqs.create({"customer": "OC-Jackson", "subject": "Booster Adapter", "contact_email": "b@oc.test",
                     "received_at": workdays.now() - timedelta(days=25), "owner_email": DOUG}, {"email": "x"}, source="email")
    rfqs.update(r["id"], {"status": "PRICING"}, {"email": DOUG})
    rfqs.run_checks()
    with SessionLocal() as db:                    # the customer agrees to a later date, still in the past
        db.get(RfqDB, r["id"]).due_at = workdays.now() - timedelta(days=2)
        db.commit()
    rfqs.run_checks()
    assert len(_open(f"rfq-overdue:{r['id']}:")) == 1


# ---------------------------------------------------------------------------
# Full boards, checked all day
# ---------------------------------------------------------------------------


def test_full_boards_checked_every_15_minutes_all_day_raise_each_alert_once_and_stay_fast():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        for p in range(60):
            pid = f"p{tag}{p}"
            db.add(MondayProjectDB(item_id=pid, board_id="9", name=f"Project {p}", owner_emails=[DOUG]))
            for m in range(6):
                db.add(MondayMilestoneDB(item_id=f"m{tag}{p}x{m}", project_item_id=pid, board_id="9",
                                         name=f"Drawing {p}.{m}", owner_emails=[DOUG], status="Working on it",
                                         due_date=_day((p + m) % 15 - 7)))
        for v in range(250):
            db.add(VendorJobDB(vendor=f"V{v % 12}", description=f"Order {v}", po_number=f"{tag}{v}", owner_email=DOUG,
                               ordered_on=_day(-(v % 90) - 10), promised_on=_day((v % 40) - 20), history=[], source="manual"))
        db.commit()
    for i in range(60):
        shipments.create({"reference": f"Box {tag} {i}", "status": "DEPARTED" if i % 2 else "BOOKED", "owner_email": DOUG,
                          "eta": _day(i % 20 - 10), "etd": _day(i % 9 - 4)}, {"email": DOUG})
    started = time.perf_counter()
    first = schedule.run_checks()
    one_run = time.perf_counter() - started
    for _ in range(10):                            # the rest of the morning
        again = schedule.run_checks()
        assert sum(v for v in again.values() if isinstance(v, int)) == 0, again   # nothing raised twice
    assert sum(v for v in first.values() if isinstance(v, int)) > 100
    assert one_run < 30, f"one check of full boards took {one_run:.1f}s"
    with SessionLocal() as db:                    # no duplicate live alerts anywhere
        keys = [k for (k,) in db.query(NotificationDB.dedup_key).filter(NotificationDB.acknowledged_at.is_(None))]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Nights and weekends
# ---------------------------------------------------------------------------


def test_texts_and_pushes_wait_for_morning_but_critical_ones_go_at_once(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_push", lambda n, url: sent.append(("push", n["title"])) or "sent")
    monkeypatch.setattr(alerts, "_quiet", lambda at=None: True)                       # 11 pm
    monkeypatch.setattr(alerts, "contacts", lambda: {DOUG: {"channels": ["push"], "phone": None, "active": True}})
    calm = alerts.notify("test_kind", f"night-calm-{uuid.uuid4().hex}", "Vendor late: KWS", "b", severity="warning",
                         to=[DOUG], wait=True)
    loud = alerts.notify("test_kind", f"night-loud-{uuid.uuid4().hex}", "Port free time ends tomorrow", "b",
                         severity="critical", to=[DOUG], wait=True)
    assert [t for _, t in sent] == ["Port free time ends tomorrow"]                     # only the critical one
    monkeypatch.setattr(alerts, "_quiet", lambda at=None: False)                      # 7 am
    alerts.release_held()
    alerts.release_held()
    assert [t for _, t in sent].count("Vendor late: KWS") == 1                          # released once
    assert calm and loud


def test_a_pile_left_from_before_clears_on_the_next_check():
    """Alerts raised before replacing existed: two live "late" alerts for one order become one."""
    job = vendors.create({"vendor": f"KWS-{uuid.uuid4().hex[:4]}", "po_number": "9275", "owner_email": DOUG,
                          "ordered_on": _day(-60), "acknowledged_on": _day(-58), "promised_on": _day(-3)}, {"email": DOUG})
    try:
        with SessionLocal() as db:   # the old pile: last week's promise date, still open
            db.add(NotificationDB(dedup_key=f"vendor-late:{job['id']}:{_day(-9)}", kind="vendor_late", title="old",
                                  body="b", recipients=[DOUG]))
            db.commit()
        vendors.run_checks()
        vendors.run_checks()
        live = _open(f"vendor-late:{job['id']}:")
        assert len(live) == 1 and live[0].dedup_key.endswith(_day(-3))
    finally:
        with SessionLocal() as db:
            db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
            db.commit()

"""Priority 1: the vendor lead-time watchdog. APPROACHING (7 and 3 days) and OVERDUE check-ins are drafted for a person's OK, name the PO, the part, where the order stands and the promised date, never repeat,
and leave a note staged for monday. Temporary database; nothing is sent or pushed."""
import uuid
from datetime import date, timedelta

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, EngineerGateDB, JobDB, MondayChangeDB, PurchaseOrderDB, VendorJobDB
from app.services.coordination import autopilot, vendors

TODAY = date(2026, 11, 18)
OWNER = {"email": "buyer.wd@allied.test", "role": "OPERATOR"}


def _d(days):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def only(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_watchdog=suggest," + ",".join(f"{k}=off" for k in autopilot.PLAYS if k != "vendor_watchdog"))
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "owner")


def _job(tag, promised_in, **extra):
    return vendors.create({"vendor": f"WD{tag}", "description": "AR400 liner plate", "po_number": f"P{tag}", "ordered_on": _d(-30), "promised_on": _d(promised_in), **extra}, OWNER)


@pytest.mark.parametrize("left,tier", [(10, None), (8, None), (7, "APPROACHING_7"), (4, "APPROACHING_7"), (3, "APPROACHING_3"), (0, "APPROACHING_3"), (-1, "OVERDUE"), (-40, "OVERDUE")])
def test_the_tier_is_worked_from_the_promised_date(left, tier):
    j = {"promised_on": _d(left), "shipped_on": None, "status": ""}
    got = vendors.watchdog_tier(j, TODAY)
    assert (got or {}).get("tier") == tier


def test_shipped_cancelled_and_dateless_orders_are_not_watched_and_a_quoted_lead_time_stands_in_for_a_promise():
    assert vendors.watchdog_tier({"promised_on": _d(-5), "shipped_on": _d(-1)}, TODAY) is None
    assert vendors.watchdog_tier({"promised_on": _d(-5), "status": "Cancelled"}, TODAY) is None
    assert vendors.watchdog_tier({}, TODAY) is None
    assert vendors.watchdog_tier({"ordered_on": _d(-25), "quoted_lead_days": 30}, TODAY)["tier"] == "APPROACHING_7"


def _drafts(tag):
    with SessionLocal() as db:
        return [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT") if (r.args or {}).get("to") == f"sales@wd{tag}.test"]


def test_a_check_in_is_drafted_once_per_tier_names_everything_and_moves_up_as_the_date_nears(monkeypatch, only):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    job = _job(tag, 6)
    autopilot.run("vendor_watchdog", on=TODAY)
    autopilot.run("vendor_watchdog", on=TODAY)                                                       # a second pass drafts nothing new
    first = _drafts(tag)
    assert len(first) == 1 and first[0].status == "PENDING" and first[0].user_email == OWNER["email"]
    body = first[0].args["body"]
    assert f"P{tag}" in body and "AR400 liner plate" in body and _d(6) in body and "awaiting Gate 1 shop drawing approval" not in body.replace("milestone not recorded", "")   # no job record: the milestone is not claimed
    assert first[0].args["_tier"] == "APPROACHING_7"
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=3))                                   # 3 days left: the next tier
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=9))                                   # overdue: the last tier
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=12))                                  # still overdue: no spam
    assert sorted(d.args["_tier"] for d in _drafts(tag)) == ["APPROACHING_3", "APPROACHING_7", "OVERDUE"]
    with SessionLocal() as db:
        j = db.get(VendorJobDB, job["id"])
        j.promised_on = _d(40)                                                                       # a new promise starts over: nothing is due yet
        db.commit()
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=12))
    assert len(_drafts(tag)) == 3


def test_the_email_says_where_the_order_stands_from_the_engineer_gates(monkeypatch, only):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    with SessionLocal() as db:
        jb = JobDB(ref=f"JOB-WD-{tag}", title="t", customer="C")
        db.add(jb)
        db.commit()
        po = PurchaseOrderDB(job_id=jb.id, vendor=f"WD{tag}", lines=[], status="SENT")
        db.add(po)
        db.commit()
        job_id, po_id = jb.id, po.id
    job = _job(tag, 2)
    with SessionLocal() as db:
        db.get(VendorJobDB, job["id"]).purchase_order_id = po_id
        db.commit()
    autopilot.run("vendor_watchdog", on=TODAY)
    assert "awaiting Gate 1 shop drawing approval" in _drafts(tag)[0].args["body"]
    with SessionLocal() as db:
        db.add(EngineerGateDB(job_id=job_id, po_id=po_id, gate=1, file_ids=[], status="APPROVED"))
        db.commit()
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=-1))                                  # same tier is not drafted twice, so look at the text directly
    from app.services.coordination.autopilot import watchdog_body

    text = watchdog_body({**job, "purchase_order_id": po_id}, {"tier": "APPROACHING_3", "days_left": 2, "due_on": _d(2)})
    assert "in production, awaiting Gate 2 measurement photos" in text


def test_a_monday_note_is_staged_never_pushed_and_only_for_a_job_that_is_on_a_monday_board(monkeypatch, only):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    job = _job(tag, 1)
    with SessionLocal() as db:
        j = db.get(VendorJobDB, job["id"])
        j.monday_item_id, j.board_id = f"mi{tag}", "999"
        db.commit()
    autopilot.run("vendor_watchdog", on=TODAY)
    autopilot.run("vendor_watchdog", on=TODAY)
    with SessionLocal() as db:
        rows = db.query(MondayChangeDB).filter(MondayChangeDB.item_id == f"mi{tag}").all()
    assert len(rows) == 1 and rows[0].status == "STAGED" and rows[0].kind == "post_update" and f"P{tag}" in rows[0].payload["text"]


def test_a_vendor_with_no_email_on_file_gets_no_draft(monkeypatch, only):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", "")
    _job(tag, 2)
    autopilot.run("vendor_watchdog", on=TODAY)
    assert _drafts(tag) == []

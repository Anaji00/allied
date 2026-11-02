"""What reviewers see beside a draft, when each late order is next followed up, and which alerts get ignored.

No monday, no mail: drafts stay in the confirm queue (conftest switches mail off)."""

import uuid
from datetime import date, datetime, timedelta, timezone

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, NotificationDB, VendorJobDB
from app.services.coordination import autopilot, vendors

ADMIN = {"email": "reviewer@allied.test", "role": "ADMIN"}


def _late_job():
    promised = (date.today() - timedelta(days=4)).isoformat()
    return vendors.create({"vendor": f"V{uuid.uuid4().hex[:5]}", "description": "Cyclone", "po_number": "9264",
                           "promised_on": promised, "ordered_on": (date.today() - timedelta(days=60)).isoformat(),
                           "owner_email": "buyer@allied.test"}, {"email": "b@allied.test"})


def _drop(jid):
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == jid).delete()
        db.commit()


def test_a_reviewer_sees_the_email_and_why_it_was_drafted(monkeypatch):
    from app.services.assistant import agent

    job = _late_job()
    monkeypatch.setattr(autopilot, "vendor_email", lambda name: "sales@vendor.test")
    monkeypatch.setattr(autopilot, "reviewers", lambda owner=None: [ADMIN["email"]])
    try:
        j = {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]
        intent = autopilot.vendor_chase(j, date.today())
        assert autopilot.carry_out("vendor_chaser", "suggest", [intent])[0]["outcome"] == "drafted"
        mine = [p for p in agent.pending(ADMIN) if p["summary"] == intent.summary][0]
        assert mine["args"]["to"] == "sales@vendor.test" and "ship date" in mine["args"]["subject"]
        assert "_why" not in mine["args"] and "_job_id" not in mine["args"]
        assert any("not shipped (4 days late)" in w for w in mine["why"]) and "No chaser sent yet" in mine["why"]
    finally:
        _drop(job["id"])


def test_each_order_says_when_its_vendor_is_followed_up():
    job = _late_job()  # PO 60 days ago, no quoted lead time: the usual wait
    with SessionLocal() as db:  # SQLite reuses a dropped job's id; an earlier test's draft must not count here
        for row in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT"):
            if (row.args or {}).get("_job_id") == job["id"]:
                db.delete(row)
        db.commit()
    try:
        first = {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]["follow_up"]
        # The usual wait after the PO, or the day after the vendor's promised date (4 days ago) if later.
        due_on = max(date.today() - timedelta(days=60) + timedelta(days=settings.VENDOR_FOLLOWUP_DAYS),
                     date.today() - timedelta(days=3)).isoformat()
        assert first["first"] == due_on and first["next"] == due_on and first["due"] and first["last"] is None
        assert first["wait_basis"].startswith("the usual wait") and first["owner"] == "buyer@allied.test"
        sent = datetime.now(timezone.utc) - timedelta(days=2)
        with SessionLocal() as db:
            db.add(AssistantActionDB(id=uuid.uuid4().hex, user_email="r@allied.test", user_role="AUTOPILOT",
                                     tool="send_email", args={"_job_id": job["id"], "_po_date": job["ordered_on"],
                                                              "to": "x@v.test"}, summary="s",
                                     status="DONE", decided_at=sent))
            db.commit()
        after = {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]["follow_up"]
        assert after["last"] == vendors._local_day(sent)      # Allied's own day, not UTC
        assert after["next"] is None and not after["due"] and after["started"]   # later ones are staff's call
    finally:
        _drop(job["id"])


def test_the_scorecard_names_alerts_nobody_reads(monkeypatch):
    from app.services.coordination import notify

    monkeypatch.setattr(settings, "AUTOPILOT_SCORECARD_MIN", 3)
    noisy, useful = f"noisy_{uuid.uuid4().hex[:6]}", f"useful_{uuid.uuid4().hex[:6]}"
    old = datetime.now(timezone.utc) - timedelta(days=5)
    with SessionLocal() as db:
        for i in range(5):
            db.add(NotificationDB(dedup_key=f"{noisy}:{i}", kind=noisy, title="t", body="b", recipients=[], created_at=old))
        for i in range(4):
            db.add(NotificationDB(dedup_key=f"{useful}:{i}", kind=useful, title="t", body="b", recipients=[], created_at=old,
                                  acknowledged_at=old, acknowledged_by="eng@allied.test"))
        db.add(NotificationDB(dedup_key=f"{useful}:x", kind=useful, title="t", body="b", recipients=[], created_at=old,
                              acknowledged_at=old, acknowledged_by=notify.RESOLVED_BY))
        db.commit()
    card = {a["kind"]: a for a in autopilot.scorecard()["alerts"]}
    assert card[noisy]["ignored"] == 5 and "switch it off" in card[noisy]["advice"]
    assert card[useful]["handled"] == 4 and card[useful]["cleared"] == 1 and card[useful]["advice"] == "People act on it."


def _clear_actions(jid):
    with SessionLocal() as db:  # SQLite reuses a dropped job's id; earlier tests' drafts must not count
        for row in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT"):
            if (row.args or {}).get("_job_id") == jid:
                db.delete(row)
        db.commit()


def _job_with(**kw):
    base = {"vendor": f"V{uuid.uuid4().hex[:5]}", "description": "Liner", "po_number": "9300",
            "ordered_on": (date.today() - timedelta(days=40)).isoformat(), "quoted_lead_days": 30}
    job = vendors.create({**base, **kw}, {"email": "b@allied.test"})
    _clear_actions(job["id"])
    return job


def _add_action(job, status, made=None, **args):
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=uuid.uuid4().hex, user_email="r@allied.test", user_role="AUTOPILOT",
                                 tool="send_email", args={"_job_id": job["id"], "to": "x@v.test", **args}, summary="s",
                                 status=status, decided_at=datetime.now(timezone.utc) if status == "DONE" else None,
                                 **({"created_at": made} if made else {})))
        db.commit()


def _follow(job):
    return {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]


def test_only_a_follow_up_for_this_po_counts(monkeypatch):
    job = _job_with()
    try:
        before_po = datetime.now(timezone.utc) - timedelta(days=50)
        _add_action(job, "PENDING", made=before_po)                   # an old weekly chaser, before this PO
        _add_action(job, "DONE", _po_date="2025-01-01")               # one for an earlier PO
        _add_action(job, "FAILED", _po_date=job["ordered_on"])        # one that failed to send
        assert _follow(job)["follow_up"]["due"] and not _follow(job)["follow_up"]["started"]
        _add_action(job, "CANCELLED", _po_date=job["ordered_on"])     # a person decided against it
        assert _follow(job)["follow_up"]["started"] and not _follow(job)["follow_up"]["due"]
    finally:
        _drop(job["id"])


def test_an_old_weekly_chaser_after_the_po_still_counts():
    job = _job_with()
    try:
        _add_action(job, "PENDING")      # made today under the old rule (no PO date), still waiting
        assert _follow(job)["follow_up"]["started"]   # no second, new-style draft for the same order
    finally:
        _drop(job["id"])


def test_after_staff_follow_up_the_late_event_drafts_nothing_more(monkeypatch):
    monkeypatch.setattr(autopilot, "vendor_email", lambda name: "sales@vendor.test")
    job = _job_with()
    try:
        _add_action(job, "DONE", _po_date=job["ordered_on"], _manual=True)   # "Follow up now" was pressed
        j = _follow(job)
        assert autopilot.vendor_chase(j, date.today()) is None                # the event's call
        assert autopilot.vendor_chase(j, date.today(), manual=True) is not None   # staff can still ask
    finally:
        _drop(job["id"])


def test_a_vendor_with_a_later_confirmed_date_is_not_asked_before_it():
    later = (date.today() + timedelta(days=10)).isoformat()
    job = _job_with(promised_on=later)            # lead time ran out 10 days ago, but the vendor said a later date
    try:
        f = _follow(job)["follow_up"]
        assert f["first"] == (date.today() + timedelta(days=11)).isoformat() and not f["due"]
        assert "confirmed date" in f["wait_basis"]
    finally:
        _drop(job["id"])

"""Round 4: people, time and restarts.

Who may do what with sign-in on; every screen and automation straight after a restart
(before the boards are read again); the clock going back tonight (1 Nov 2026), Thanksgiving
and the year end; a person and the board read changing one order at once; and the change
check's monday budget across a whole day. Temporary database and fakes only."""

import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, VendorJobDB
from app.services.coordination import activity, boards, changes, events, recipes, replies, rfqs, vendors, workdays
from app.services.coordination import notify as alerts

CHI = ZoneInfo("America/Chicago")


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role, method, path, body, allowed", [
    ("VIEWER", "post", "/api/v1/autopilot/recipes", {"name": "x", "board_role": "vendors", "trigger": "status_is",
                                                     "value": "STUCK", "action": "notify"}, False),
    ("OPERATOR", "post", "/api/v1/autopilot/recipes/starters/orders-ask-photos", {}, False),
    ("MANAGER", "post", "/api/v1/autopilot/recipes/starters/orders-ask-photos", {}, True),
    ("OPERATOR", "post", "/api/v1/autopilot/run", {"dry_run": True}, False),
    ("MANAGER", "post", "/api/v1/autopilot/run", {"dry_run": True}, False),
    ("ADMIN", "post", "/api/v1/autopilot/run", {"dry_run": True}, True),
    ("VIEWER", "get", "/api/v1/autopilot/scorecard", None, False),
    ("MANAGER", "get", "/api/v1/autopilot/scorecard", None, True),
    ("VIEWER", "get", "/api/v1/mail", None, True),
    ("VIEWER", "post", "/api/v1/mail/1/handled", None, False),
    ("VIEWER", "patch", "/api/v1/vendors/1", {"follow_up_days": 10}, False),
    ("VIEWER", "get", "/api/v1/boards/1/timeline", None, True),
])
def test_each_role_gets_exactly_its_rights(auth_enforced, client, role, method, path, body, allowed):
    from tests.conftest import staff_token

    token = staff_token(f"{role.lower()}{uuid.uuid4().hex[:4]}@alliedweldingsystems.com", role)
    kw = {"headers": {"Authorization": f"Bearer {token}"}}
    if body is not None:
        kw["json"] = body
    status = getattr(client, method)(path, **kw).status_code
    assert (status != 403) == allowed, (role, path, status)
    assert status != 401


def test_signed_out_people_reach_only_the_public_doors(auth_enforced, client, monkeypatch):
    for method, path in (("get", "/api/v1/mail"), ("get", "/api/v1/autopilot/recipes"), ("post", "/api/v1/assistant/actions/batch"),
                         ("get", "/api/v1/vendors/link-suggestions"), ("get", "/api/v1/boards")):
        assert getattr(client, method)(path).status_code == 401, path
    monkeypatch.setattr(settings, "RFQ_FORM_TOKEN", "")
    assert client.get("/api/v1/rfqs/form", headers={"X-Form-Token": "x"}).status_code == 404   # public, but off
    assert client.get("/api/v1/assistant/quick/abc", params={"e": "x", "x": "1", "s": "y"}).status_code == 403


def test_a_viewer_who_reviews_can_see_drafts_but_a_stranger_cannot(monkeypatch):
    from app.services.assistant import agent

    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", "karissa@alliedweldingsystems.com")
    aid = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=aid, user_email="karissa@alliedweldingsystems.com", user_role="AUTOPILOT",
                                 tool="send_email", status="PENDING", args={"to": "v@v.test", "subject": "s", "body": "b"},
                                 summary="s"))
        db.commit()
    assert aid in {a["id"] for a in agent.pending({"email": "karissa@alliedweldingsystems.com", "role": "VIEWER"})}
    assert aid not in {a["id"] for a in agent.pending({"email": "shane@alliedweldingsystems.com", "role": "MANAGER"})}
    with pytest.raises(PermissionError):    # sending email needs an operator, even for a named reviewer
        agent.decide(aid, True, {"email": "karissa@alliedweldingsystems.com", "role": "VIEWER"})
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, aid).status == "PENDING"     # still waiting, not marked failed


# ---------------------------------------------------------------------------
# Right after a restart
# ---------------------------------------------------------------------------


def test_everything_answers_sensibly_before_the_boards_are_read_again(client, monkeypatch):
    with boards._lock:
        saved_mirror = dict(boards._mirror)
        boards._mirror.clear()
    with activity._lock:
        saved_logs = dict(activity._logs)
        activity._logs.clear()
    try:
        monkeypatch.setattr(settings, "MONDAY_VENDOR_BOARD_ID", "18394243717")
        assert client.get("/api/v1/boards").json() == []
        assert client.get("/api/v1/boards/18394243717/timeline").status_code == 404
        assert client.get("/api/v1/boards/18394243717/dashboard").status_code == 404
        starters = client.get("/api/v1/autopilot/recipes/starters", params={"preview": True})
        assert starters.status_code == 200 and all(s["today"] == [] for s in starters.json())
        assert recipes.run_dates(dry_run=True) == []
        assert client.get("/api/v1/vendors").status_code == 200
        assert client.get("/api/v1/vendors/link-suggestions").status_code == 200
        assert client.get("/api/v1/autopilot/scorecard").status_code == 200
        assert client.get("/api/v1/autopilot/events").status_code == 200
    finally:
        with boards._lock:
            boards._mirror.update(saved_mirror)
        with activity._lock:
            activity._logs.update(saved_logs)


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def test_the_clocks_going_back_tonight_doesnt_bend_working_hours_or_quiet_hours():
    # Friday 30 Oct 2026, 4 pm Chicago (CDT). Clocks go back at 2 am Sunday 1 Nov.
    friday_4pm = datetime(2026, 10, 30, 16, tzinfo=CHI)
    monday_9am = datetime(2026, 11, 2, 9, tzinfo=CHI)                    # now CST
    assert workdays.hours_between(friday_4pm, monday_9am) == pytest.approx(2.0)   # 1 h Fri + 1 h Mon
    landed = workdays.add_hours(friday_4pm, 3).astimezone(CHI)
    assert (landed.date(), landed.hour) == (date(2026, 11, 2), 10)
    # 1:30 am happens twice tonight: both are quiet hours; 7 am CST is not.
    first, second = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
    assert first.astimezone(CHI).hour == second.astimezone(CHI).hour == 1
    assert alerts._quiet(first.astimezone(CHI)) and alerts._quiet(second.astimezone(CHI))
    assert not alerts._quiet(datetime(2026, 11, 2, 7, 0, tzinfo=CHI))


def test_thanksgiving_and_the_year_end_are_skipped_in_quote_deadlines():
    wednesday = datetime(2026, 11, 25, 15, tzinfo=CHI)                   # day before Thanksgiving
    due = workdays.add_days(wednesday, 2).astimezone(CHI).date()
    assert due == date(2026, 12, 1)                                      # Thu + Fri off, then Mon, Tue
    dec23 = datetime(2026, 12, 23, 10, tzinfo=CHI)
    assert workdays.add_days(dec23, 3).astimezone(CHI).date() == date(2026, 12, 30)   # 24, 25 off; 28, 29, 30


def test_vendor_dates_across_the_new_year_land_in_the_right_year():
    assert replies._dates_in("Will ship Jan 5", date(2026, 12, 18)) == [date(2027, 1, 5)]
    assert replies._dates_in("It shipped Dec 28", date(2027, 1, 3)) == [date(2026, 12, 28)]
    assert replies._dates_in("ships 2/29", date(2027, 1, 10)) == []        # no 29 Feb in 2027


# ---------------------------------------------------------------------------
# A person and the board read changing one order at once
# ---------------------------------------------------------------------------


def test_a_person_and_the_board_read_saving_the_same_order_at_once_both_survive(monkeypatch):
    job = vendors.create({"vendor": "KWS", "po_number": "9277", "ordered_on": "2026-09-01"}, {"email": "k@a.test"})
    errors, barrier = [], threading.Barrier(2)

    def person():
        barrier.wait()
        try:
            vendors.update(job["id"], {"promised_on": "2026-11-20", "note": "KWS called"}, {"email": "k@a.test"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def board():
        barrier.wait()
        try:
            vendors.update(job["id"], {"acknowledged_on": "2026-09-03"}, {"email": "monday"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=person), threading.Thread(target=board)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with SessionLocal() as db:
        row = db.get(VendorJobDB, job["id"])
        assert not errors and row.promised_on == "2026-11-20" and row.acknowledged_on == "2026-09-03"
        assert any("KWS called" in h["change"] for h in row.history)
        db.delete(row)
        db.commit()


# ---------------------------------------------------------------------------
# monday's daily budget
# ---------------------------------------------------------------------------


def test_the_change_check_stays_well_inside_mondays_daily_call_budget(monkeypatch):
    """Walk a whole working day minute by minute, as the worker would, counting checks."""
    calls, t = 0, datetime(2026, 11, 2, 0, 0, tzinfo=CHI)           # a Monday
    end = t + timedelta(days=1)
    while t < end:
        monkeypatch.setattr(alerts, "now_local", lambda t=t: t)
        calls += 1
        t += timedelta(seconds=changes.interval())
    hourly_full_reads = 24 * 3                                          # three boards, once an hour
    assert calls + hourly_full_reads < 700, calls                      # Standard plan: about 1,000 a day


def test_ten_people_noting_the_same_order_at_once_lose_no_note():
    job = vendors.create({"vendor": "KINGCERA", "po_number": "9264"}, {"email": "k@a.test"})
    barrier = threading.Barrier(10)

    def note(i):
        barrier.wait()
        vendors.update(job["id"], {"note": f"call {i}"}, {"email": f"p{i}@a.test"})

    threads = [threading.Thread(target=note, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with SessionLocal() as db:
        row = db.get(VendorJobDB, job["id"])
        notes = {h["change"] for h in row.history}
        assert {f"call {i}" for i in range(10)} <= notes, notes
        db.delete(row)
        db.commit()


def test_ten_people_updating_the_same_rfq_at_once_lose_no_note():
    r = rfqs.create({"customer": "TECO", "subject": "JM 4in pipes", "contact_email": "p@teco.test"}, {"email": "x"})
    barrier = threading.Barrier(10)

    def note(i):
        barrier.wait()
        rfqs.update(r["id"], {"note": f"spoke to TECO {i}"}, {"email": f"p{i}@a.test"})

    threads = [threading.Thread(target=note, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    history = " ".join(h["change"] for h in rfqs.get(r["id"])["history"])
    assert all(f"spoke to TECO {i}" in history for i in range(10)), history

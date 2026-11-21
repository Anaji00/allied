"""Real health: a background job that fails or goes quiet turns red, one alert tells the managers, and it closes itself when things are well. The setup card's red lines.
Fake workers and the throwaway database only."""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import NotificationDB
from app.services import setup_check, worker_health
from app.services.coordination.schedule import coordination_worker


def _now(minutes=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _open_alerts():
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{worker_health.ALERT_KEY}%"), NotificationDB.acknowledged_at.is_(None)).count()


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(coordination_worker, "last", {})
    with SessionLocal() as db:
        db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{worker_health.ALERT_KEY}%")).delete(synchronize_session=False)
        db.commit()
    worker_health.check_and_alert()
    yield
    monkeypatch.undo()


def _by(snap, name):
    return next(w for w in snap["workers"] if w["name"] == name)


def test_a_failing_step_is_red_with_one_alert_and_the_alert_closes_when_it_is_fixed(monkeypatch):
    monkeypatch.setattr(coordination_worker, "last", {"checks": {"at": _now(), "result": {}}, "autopilot": {"at": _now(), "error": "ValueError: boom"}})
    snap = worker_health.snapshot()
    c = _by(snap, "coordination")
    assert snap["status"] == "red" and c["status"] == "red" and "autopilot" in c["why"] and "boom" in c["last_error"] and c["steps"][1]["ok"] is False
    worker_health.check_and_alert()
    worker_health.check_and_alert()
    assert _open_alerts() == 1                                                      # one alert however often it is checked
    monkeypatch.setattr(coordination_worker, "last", {"checks": {"at": _now(), "result": {}}, "autopilot": {"at": _now(), "result": {}}})
    assert worker_health.snapshot()["status"] == "green" and worker_health.check_and_alert()["red"] == []
    assert _open_alerts() == 0                                                      # it closed itself


def test_a_step_that_has_gone_quiet_for_three_intervals_is_red(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULE_CHECK_SECONDS", 300)
    monkeypatch.setattr(coordination_worker, "last", {"checks": {"at": _now(minutes=10), "result": {}}})
    assert _by(worker_health.snapshot(), "coordination")["status"] == "green"       # ten minutes is under three times five
    monkeypatch.setattr(coordination_worker, "last", {"checks": {"at": _now(minutes=20), "result": {}}})
    c = _by(worker_health.snapshot(), "coordination")
    assert c["status"] == "red" and "checks step has not succeeded" in c["why"]


def test_the_mailbox_reader_is_red_when_it_errors_or_is_expected_but_stopped(monkeypatch):
    from app.services.mailbox_poller import mailbox_poller

    monkeypatch.setattr(settings, "IMAP_POLL_ENABLED", True)
    monkeypatch.setattr(settings, "IMAP_HOST", "imap.example.test")
    monkeypatch.setattr(settings, "IMAP_USERNAME", "u")
    monkeypatch.setattr(settings, "IMAP_PASSWORD", "p")
    monkeypatch.setattr(settings, "IMAP_POLL_SECONDS", 60)
    monkeypatch.setitem(mailbox_poller._stats, "running", True)
    monkeypatch.setitem(mailbox_poller._stats, "last_ok_at", _now(minutes=1))
    monkeypatch.setitem(mailbox_poller._stats, "last_error", None)
    assert _by(worker_health.snapshot(), "mailbox_poller")["status"] == "green"
    monkeypatch.setitem(mailbox_poller._stats, "last_error", "LoginError: nope")
    monkeypatch.setitem(mailbox_poller._stats, "last_error_at", _now())
    w = _by(worker_health.snapshot(), "mailbox_poller")
    assert w["status"] == "red" and "LoginError" in w["why"]
    monkeypatch.setitem(mailbox_poller._stats, "last_error", None)
    monkeypatch.setitem(mailbox_poller._stats, "last_ok_at", _now(minutes=30))
    assert _by(worker_health.snapshot(), "mailbox_poller")["status"] == "red"       # 30 minutes with a 60 s interval
    monkeypatch.setitem(mailbox_poller._stats, "running", False)
    assert "not running" in _by(worker_health.snapshot(), "mailbox_poller")["why"] or _by(worker_health.snapshot(), "mailbox_poller")["status"] == "red"
    monkeypatch.setattr(settings, "IMAP_POLL_ENABLED", False)
    assert _by(worker_health.snapshot(), "mailbox_poller")["status"] == "off"       # not set up: not red


def test_the_monday_inbox_worker_records_its_last_error():
    from app.services.monday_events import monday_inbox_worker

    assert hasattr(monday_inbox_worker, "last_error") and hasattr(monday_inbox_worker, "last_error_at")


def test_health_over_http_says_degraded_with_the_workers_and_live_stays_plain(client, auth_enforced, monkeypatch):
    from tests.conftest import staff_token

    h = {"Authorization": "Bearer " + staff_token("wh-viewer@alliedweldingsystems.com", "VIEWER")}
    assert client.get("/api/v1/health", headers=h).json()["status"] == "healthy"
    monkeypatch.setattr(coordination_worker, "last", {"checks": {"at": _now(), "error": "RuntimeError: x"}})
    got = client.get("/api/v1/health", headers=h).json()
    assert got["status"] == "degraded" and got["workers"]["red"] == ["coordination"] and {w["name"] for w in got["workers"]["workers"]} == {"coordination", "mailbox_poller", "monday_inbox_worker"}
    assert client.get("/api/v1/health/live").json()["status"] == "healthy"


def test_the_setup_card_has_the_red_lines_in_plain_words(monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", "")
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_chaser=auto,rfq_vendor_chaser=suggest,quote_followup=suggest")
    items = {i["key"]: i for i in setup_check.checklist()["items"]}
    assert not items["backup_copy"]["ok"] and not items["po_limit"]["ok"] and not items["autopilot_auto"]["ok"]
    assert "any size" in items["po_limit"]["if_blank"] and "vendor_chaser" in items["autopilot_auto"]["how_to_fix"] and items["po_limit"]["level"] == "needed"
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", "D:/second")
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 5000.0)
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_chaser=suggest")
    items = {i["key"]: i for i in setup_check.checklist()["items"]}
    assert items["backup_copy"]["ok"] and items["po_limit"]["ok"] and items["autopilot_auto"]["ok"]

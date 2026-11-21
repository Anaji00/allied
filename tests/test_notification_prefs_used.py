"""The sending code consults a person's own notification choices: channels per group, their quiet hours, instant or digest. A critical alert ignores quiet hours and digest; with nothing saved, nothing changes."""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import NotificationDB
from app.services import my_prefs
from app.services.coordination import notify

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def sent(monkeypatch):
    log = []
    monkeypatch.setattr(notify, "_email", lambda address, n, url: log.append((address, n["severity"])) or "sent")
    monkeypatch.setattr(notify, "_push", lambda n, url: "sent")
    monkeypatch.setattr(notify, "_quiet", lambda at=None: False)
    monkeypatch.setattr(notify, "contacts", lambda: {})
    return log


def _alert(who, severity="warning", kind="vendor_late"):
    nid = notify.notify(kind, f"np-{uuid.uuid4().hex}", f"Alert {TAG}", "body", to=[who], severity=severity, wait=True)
    with SessionLocal() as db:
        return db.get(NotificationDB, nid).deliveries.get(who, {})


def _save(who, **prefs):
    my_prefs.put_prefs(who, prefs)


def test_with_nothing_saved_the_email_goes_as_before(sent):
    who = f"np-none-{TAG}@alliedweldingsystems.com"
    assert _alert(who)["email"] == "sent" and sent == [(who, "warning")]


def test_a_group_without_email_sends_no_email_but_the_alert_is_still_theirs(sent):
    who = f"np-chan-{TAG}@alliedweldingsystems.com"
    _save(who, groups={"alerts": ["app"]})
    assert "email" not in _alert(who) and not sent
    with SessionLocal() as db:
        assert db.query(NotificationDB).filter(NotificationDB.recipients.like(f"%{who}%")).count() == 1            # it is in their list
    other = f"np-other-{TAG}@alliedweldingsystems.com"
    assert _alert(other)["email"] == "sent"                                                                        # someone else's choices are theirs alone
    _save(who, groups={"alerts": ["app", "email"]})
    assert _alert(who)["email"] == "sent"


def test_quiet_hours_hold_back_email_unless_it_is_critical(sent, monkeypatch):
    who = f"np-quiet-{TAG}@alliedweldingsystems.com"
    _save(who, quiet_start="18:00", quiet_end="07:00")
    monkeypatch.setattr(notify, "now_local", lambda: datetime(2026, 11, 18, 23, 30, tzinfo=ZoneInfo(settings.BUSINESS_TZ)))
    assert "quiet hours" in _alert(who)["email"] and not sent
    assert _alert(who, severity="critical")["email"] == "sent" and len(sent) == 1                                    # urgent ignores quiet hours
    monkeypatch.setattr(notify, "now_local", lambda: datetime(2026, 11, 18, 3, 0, tzinfo=ZoneInfo(settings.BUSINESS_TZ)))            # the window runs overnight
    assert "quiet hours" in _alert(who)["email"]
    monkeypatch.setattr(notify, "now_local", lambda: datetime(2026, 11, 18, 12, 0, tzinfo=ZoneInfo(settings.BUSINESS_TZ)))
    assert _alert(who)["email"] == "sent"


def test_the_digest_choice_holds_back_instant_email_except_critical(sent):
    who = f"np-digest-{TAG}@alliedweldingsystems.com"
    _save(who, delivery="digest")
    assert "digest" in _alert(who, severity="info")["email"] and not sent
    assert _alert(who, severity="critical")["email"] == "sent"
    _save(who, delivery="instant")
    assert _alert(who, severity="info")["email"] == "sent"


def test_the_group_of_an_alert_kind_and_the_overnight_window():
    assert my_prefs.pref_group("autopilot_drafts") == "drafts" and my_prefs.pref_group("mail_complaint") == "mail" and my_prefs.pref_group("lead_replied") == "leads" and my_prefs.pref_group("vendor_late") == "alerts"
    p = {"quiet_start": "18:00", "quiet_end": "07:00"}
    tz = ZoneInfo(settings.BUSINESS_TZ)
    assert my_prefs.in_quiet_hours(p, datetime(2026, 11, 18, 22, 0, tzinfo=tz)) and my_prefs.in_quiet_hours(p, datetime(2026, 11, 18, 6, 59, tzinfo=tz)) and not my_prefs.in_quiet_hours(p, datetime(2026, 11, 18, 7, 0, tzinfo=tz))
    assert not my_prefs.in_quiet_hours({"quiet_start": None, "quiet_end": None}, datetime(2026, 11, 18, 22, 0, tzinfo=tz))

"""Fixes from the walk-through of the demo: a US order stays in dollars, customs papers follow the shipment's route, one count of open alerts everywhere,
a waiting draft names a person, and the order status does not claim Sage has it. Temporary database and fakes only."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.core.status_words import word
from app.models.db_models import StaffContactDB
from app.services import import_paperwork
from app.services.coordination import jobs, notify, schedule
from app.services.order_orchestrator import OrderOrchestrator

TAG = uuid.uuid4().hex[:6]


def test_a_us_ship_to_stays_in_dollars_even_when_the_vendor_is_in_china():
    resolve = OrderOrchestrator.resolve_facility_and_currency
    assert resolve("OWENS CORNING- STARR\n4837 Highway 81 South\nStarr, SC 29684", ["ZIBO"]) == ("HQ", "USD")
    assert resolve("831 Hayter Rd NW, Edmonton, AB T6S 1A1, Canada", ["ZIBO"]) == ("EDMONTON", "CAD")
    assert resolve("Zibo, Shandong, China", [])[1] == "CNY"
    assert resolve("", ["ZIBO"])[1] == "CNY"                                   # no ship-to at all: the vendor is the only clue


def test_the_order_status_does_not_claim_sage_has_it():
    assert "Sage" in word("order", "SYNCED_TO_SAGE") and "not in a Sage file" in word("order", "SYNCED_TO_SAGE")
    assert word("order", "SYNCED_TO_SAGE") != "In Sage"


@pytest.mark.parametrize("text, expected", [
    ("Houston, TX", "US"), ("Houston", "US"), ("Starr, SC 29684", "US"), ("Long Beach", "US"), ("United States", "US"),
    ("Vancouver, BC", "CA"), ("Edmonton, AB T6S 1A1", "CA"), ("Prince Rupert", "CA"), ("Toronto, Canada", "CA"),
    ("Qingdao", None), ("", None), (None, None)])
def test_a_place_is_read_as_us_or_canada_only_when_it_says_so(text, expected):
    assert import_paperwork._country_of(text) == expected


def test_a_waiting_draft_names_the_person_who_reviews_it(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", f"boss{TAG}@alliedweldingsystems.com")
    with SessionLocal() as db:
        db.merge(StaffContactDB(email=f"boss{TAG}@alliedweldingsystems.com", name="Pat Boss", role_tag="leadership", channels=["email"], active=True))
        db.commit()
    notify.forget_contacts()
    assert jobs._reviewer_name() == "Pat Boss"
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", f"boss{TAG}@alliedweldingsystems.com,two{TAG}@alliedweldingsystems.com")
    assert jobs._reviewer_name() == "Pat Boss and 1 more"
    assert jobs._reviewer_name() != "reviewer"


def test_the_daily_summary_counts_the_same_open_alerts_as_the_inbox_and_drops_a_snoozed_one():
    me = f"count{TAG}@alliedweldingsystems.com"
    before_open = len(notify.inbox(open_only=True, limit=500))
    before = schedule.build_summary()
    warn = notify.notify("vendor_noack", f"count-warn-{TAG}", f"Behind {TAG}", "a warning", severity="warning", to=[me], wait=True)
    notify.notify("rfq_overdue", f"count-crit-{TAG}", f"Urgent {TAG}", "an urgent one", severity="critical", to=[me], wait=True)
    after = schedule.build_summary()
    assert len(notify.inbox(open_only=True, limit=500)) == before_open + 2
    assert after["alerts_open"] == before["alerts_open"] + 2 and after["alerts_behind"] == before["alerts_behind"] + 1 and after["urgent_open"] == before["urgent_open"] + 1
    assert f"Behind {TAG}" in after["text"]
    until = datetime.now(timezone.utc) + timedelta(hours=3)
    assert notify.snooze(warn, me, until=until) == until
    snoozed = schedule.build_summary()
    assert snoozed["alerts_open"] == after["alerts_open"] - 1 == len(notify.inbox(open_only=True, limit=500))        # Today, the badge's list and the summary agree
    assert f"Behind {TAG}" not in snoozed["text"]

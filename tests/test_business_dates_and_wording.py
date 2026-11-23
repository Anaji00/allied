"""Dates shown to people use the business's own day (America/Chicago), not the UTC date; dates inside sentences are written the way people say them. No database."""
from datetime import datetime, timezone

from app.services.coordination import ball


def test_a_utc_evening_instant_is_the_business_days_date_not_the_next_day():
    late_evening_chicago = datetime(2026, 11, 16, 2, 30, tzinfo=timezone.utc)                # 8:30 pm on Nov 15 in Chicago
    assert ball._local_date(late_evening_chicago) == "2026-11-15"
    assert ball._local_date(datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)) == "2026-11-16"
    assert ball._local_date(datetime(2026, 11, 16, 2, 30)) == "2026-11-15"                  # a naive time is taken as UTC, as everywhere else
    assert ball._local_date(None) is None


def test_a_date_in_a_sentence_is_said_the_way_people_say_it():
    assert ball._say_date("2026-11-16") == "Nov 16"
    assert ball._say_date("2019-03-02") == "Mar 2, 2019"
    assert ball._say_date("soon") == "soon"


def test_the_chase_row_carries_the_business_date(monkeypatch):
    from app.services import my_day
    from app.services.assistant import agent

    monkeypatch.setattr(agent, "pending", lambda user: [])
    holder = {"item_type": "po", "item_ref": "9296", "ball_with": "vendor", "ball_name": "NIBROCK", "ball_label": "Vendor", "resolved": False, "days_waiting": 3, "label": "PO 9296",
              "note": "", "since": "2026-11-16T02:30:00+00:00", "since_date": "2026-11-15"}
    monkeypatch.setattr(ball, "list_open", lambda *a, **k: [holder])
    row = my_day.chase({"email": "x@alliedweldingsystems.com", "role": "MANAGER"})["items"][0]
    assert row["since_date"] == "2026-11-15"

"""My counts in one call, my own notification choices, and the typed scorecard and bills history. Temporary database."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import view_models as vm
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {"A": {"Authorization": "Bearer " + staff_token(f"mc-a-{TAG}@alliedweldingsystems.com", "MANAGER")},
             "B": {"Authorization": "Bearer " + staff_token(f"mc-b-{TAG}@alliedweldingsystems.com", "OPERATOR")}}

    def call(method, path, who="A", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[who], **kw)
        assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text[:300]}"
        return r.json()

    return call


def test_my_counts_come_from_the_same_source_as_today(api):
    got = api("GET", "/me/counts")
    vm.MyCounts.model_validate(got)
    assert got["total"] == got["alerts_unread"] + got["mail_needing_action"] + got["drafts_waiting"]
    needs = api("GET", "/me/needs")
    assert got["alerts_unread"] == needs["counts"]["alerts"] and got["drafts_waiting"] == needs["counts"]["drafts"]
    assert got["need_action"] == api("GET", "/my-day")["need_action"]


def test_notification_choices_default_validate_and_stay_one_persons_own(api):
    first = api("GET", "/me/notifications")
    assert first["delivery"] == "instant" and first["quiet_start"] is None and first["groups"]["alerts"] == ["app", "email"]
    got = api("PUT", "/me/notifications", json={"groups": {"alerts": ["app", "push"]}, "quiet_start": "18:00", "quiet_end": "07:00", "delivery": "digest", "digest_time": "08:30"})
    assert got["groups"]["alerts"] == ["app", "push"] and got["groups"]["drafts"] == ["app", "email"] and got["quiet_start"] == "18:00" and got["delivery"] == "digest"
    assert api("GET", "/me/notifications", who="B")["delivery"] == "instant"                             # nobody else's
    for bad in ({"groups": {"nonsense": ["app"]}}, {"groups": {"alerts": ["carrier-pigeon"]}}, {"delivery": "weekly"}, {"quiet_start": "25:99", "quiet_end": "07:00"}, {"quiet_start": "18:00", "quiet_end": ""}):
        api("PUT", "/me/notifications", who="A", json=bad, expect=422)
    api("PUT", "/me/notifications", json={"quiet_start": "", "quiet_end": "", "delivery": "instant"})
    assert api("GET", "/me/notifications")["quiet_start"] is None


def test_the_scorecard_and_the_bills_history_answer_through_their_models(api):
    sc = api("GET", "/autopilot/scorecard")
    vm.Scorecard.model_validate(sc)
    assert sc["days"] == 90 and isinstance(sc["automations"], list) and isinstance(sc["alerts"], list)
    hist = api("GET", "/reconciliation/history")
    assert isinstance(hist, list)
    for row in hist:
        vm.BillHistoryRow.model_validate(row)
    spec = api.__closure__ and None
    from app.main import app

    schema = app.openapi()
    assert "Scorecard" in str(schema["paths"]["/api/v1/autopilot/scorecard"]["get"]["responses"]["200"]) and "BillHistoryRow" in str(schema["paths"]["/api/v1/reconciliation/history"]["get"]["responses"]["200"])
    assert "MyCounts" in str(schema["paths"]["/api/v1/me/counts"]["get"]["responses"]["200"])

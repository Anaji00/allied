"""Free-standing email drafts: they wait in the approval queue, are checked by audience, and are never sent by the route. Temporary database."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"sd-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text[:300]}"
        return r.json()

    return call


def test_a_free_draft_waits_in_the_queue_shows_its_fields_and_is_counted(api, monkeypatch):
    sent = []
    from app.services.outbound_email_service import OutboundEmailService

    monkeypatch.setattr(OutboundEmailService, "_deliver", lambda self, msg, rcpts: sent.append(rcpts))
    got = api("POST", "/secretary/drafts", expect=201, json={"to": f"colleague-{TAG}@alliedweldingsystems.com", "subject": "Quick note", "body": "Please check the drawing."})
    assert got["drafted"] and got["status"] == "PENDING" and got["audience"] == "internal" and "Nothing was sent" in got["message"] and not sent
    rows = [a for a in api("GET", "/assistant/actions")["actions"] if a["id"] == got["action_id"]]
    row = rows[0]
    assert row["tool"] == "send_email" and row["args"]["to"].startswith("colleague-") and row["args"]["subject"] == "Quick note" and row["args"]["body"] and row["created_at"] and row["status"] == "PENDING"
    counts = api("GET", "/me/counts")
    assert counts["email_drafts_waiting"] >= 1 and counts["drafts_waiting"] >= counts["email_drafts_waiting"]
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, got["action_id"]).status == "PENDING"


def test_checks_by_audience_and_plain_errors(api):
    me = f"colleague-{TAG}@alliedweldingsystems.com"
    assert "Allied addresses" in api("POST", "/secretary/drafts", expect=422, json={"to": "someone@outside-example.test", "subject": "s", "body": "b"})["detail"]
    assert "sales order number" in api("POST", "/secretary/drafts", expect=422, json={"to": "buyer@customer-example.test", "subject": "Update", "body": "Your order is SO 12889.", "audience": "customer"})["detail"]
    ok = api("POST", "/secretary/drafts", expect=201, json={"to": "buyer@customer-example.test", "subject": "Hello", "body": "Thanks for asking; we will reply soon.", "audience": "customer"})
    assert ok["audience"] == "customer"
    for bad in ({"to": "", "subject": "s", "body": "b"}, {"to": me, "subject": "", "body": "b"}, {"to": me, "subject": "s", "body": "  "}, {"to": me, "subject": "s", "body": "b", "audience": "nobody"},
                {"to": me, "subject": "s", "body": "b", "link_kind": "job"}):
        assert api("POST", "/secretary/drafts", expect=422, json=bad)["detail"]
    api("POST", "/secretary/drafts", role="VIEWER", expect=403, json={"to": me, "subject": "s", "body": "b"})
    assert api("POST", "/secretary/drafts", expect=404, json={"to": me, "subject": "s", "body": "b", "link_kind": "job", "link_ref": "JOB-9999-9999"})

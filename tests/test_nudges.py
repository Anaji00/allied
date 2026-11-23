"""Nudges and milestone blasts: who may send, who may receive, channels, practice mode, the rate limit, audit and history, and that the app notice lands in the person's alerts (Today). Temporary database;
email and text are faked (nothing is sent)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB, StaffContactDB
from app.services import nudges
from app.services.coordination import notify
from tests.conftest import staff_token

ATHISH = {"email": "athish.nudge@allied.test", "role": "OPERATOR", "name": "Athish Singh"}
ADMIN = {"email": "admin.nudge@allied.test", "role": "ADMIN"}
OTHER = {"email": "someone.nudge@allied.test", "role": "OPERATOR"}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setattr(settings, "NUDGE_SENDERS", ATHISH["email"])
    nudges._sent_at.clear()
    nudges._pair_at.clear()
    mails, texts = [], []
    monkeypatch.setattr(notify, "_email", lambda address, n, url: mails.append((address, n["title"], n["body"])) or "sent")
    monkeypatch.setattr(notify, "_sms", lambda phone, n, url: texts.append((phone, n["title"])) or "sent")
    tag = uuid.uuid4().hex[:5]
    with SessionLocal() as db:
        for key, name, phone, chans in (("a", "Ann Ally", "+15550000001", ["email", "sms"]), ("b", "Bob NoPhone", None, ["email", "sms"]), ("c", "Cy NoSms", "+15550000003", ["email"])):
            db.merge(StaffContactDB(email=f"{key}.{tag}@allied.test", name=name, role_tag="staff", phone=phone, channels=chans, daily_summary=False, active=True))
        db.merge(StaffContactDB(email=f"off.{tag}@allied.test", name="Off Duty", role_tag="staff", phone=None, channels=["email"], daily_summary=False, active=False))
        db.commit()
    notify._contacts_cache.update(at=0.0, value=None)
    return {"tag": tag, "mails": mails, "texts": texts, "a": f"a.{tag}@allied.test", "b": f"b.{tag}@allied.test", "c": f"c.{tag}@allied.test", "off": f"off.{tag}@allied.test"}


def test_only_a_named_sender_or_an_admin_can_send(world):
    assert nudges.send(ATHISH, "nudge", [world["a"]], "please send the drawing", ["app"])["sent"]
    assert nudges.send(ADMIN, "nudge", [world["a"]], "please send the drawing", ["app"])["sent"]
    with pytest.raises(nudges.NudgeError) as e:
        nudges.send(OTHER, "nudge", [world["a"]], "x", ["app"])
    assert e.value.status == 403


def test_with_no_senders_set_only_admins_may(monkeypatch, world):
    monkeypatch.setattr(settings, "NUDGE_SENDERS", "")
    with pytest.raises(nudges.NudgeError):
        nudges.send(ATHISH, "nudge", [world["a"]], "x", ["app"])
    assert nudges.send(ADMIN, "nudge", [world["a"]], "x", ["app"])["sent"]


def test_an_outside_address_refuses_the_whole_request_and_an_inactive_colleague_is_skipped(world):
    with pytest.raises(nudges.NudgeError) as e:
        nudges.send(ATHISH, "nudge", ["customer@outside.example", world["a"]], "chase the PO", ["app", "email"])
    assert e.value.status == 422 and e.value.message == "Nudges go to Allied staff only; use a draft for anyone outside." and world["mails"] == []
    out = nudges.send(ATHISH, "nudge", [world["off"], world["a"]], "chase PO 123", ["app", "email"])
    assert {s["email"] for s in out["skipped"]} == {world["off"]}
    assert {(s["email"], s["channel"]) for s in out["sent"]} == {(world["a"], "app"), (world["a"], "email")}
    assert all(m[0] == world["a"] for m in world["mails"])


@pytest.mark.parametrize("text", ["send the $1,250.00 quote", "Net 30 terms for PO 5", "your markup is low", "deposit is due", "price is 12.50", "USD 400 please", "wire the money", "it is 30% done"])
def test_a_nudge_never_carries_prices_payment_terms_or_money_words(world, text):
    with pytest.raises(nudges.NudgeError) as e:
        nudges.send(ATHISH, "nudge", [world["a"]], text, ["app"])
    assert e.value.status == 422 and "never carries prices" in e.value.message and world["mails"] == []


def test_a_customer_name_in_a_nudge_is_refused_but_a_po_reference_is_fine(world):
    from app.services.coordination import blind

    names = blind.customer_names()
    if names:
        with pytest.raises(nudges.NudgeError):
            nudges.send(ATHISH, "nudge", [world["a"]], f"call {names[0]} about it", ["app"])
    assert nudges.send(ATHISH, "nudge", [world["a"]], "please confirm the ship date for PO 4521 / JOB-2026-0007", ["app"])["sent"]


def test_one_sender_cannot_nudge_the_same_person_more_than_three_times_an_hour(world):
    nudges._pair_at.clear()
    for _ in range(3):
        assert nudges.send(ATHISH, "nudge", [world["a"]], "ping", ["app"])["sent"]
    out = nudges.send(ATHISH, "nudge", [world["a"]], "ping", ["app"])
    assert out["sent"] == [] and "three times this hour" in out["skipped"][0]["reason"]
    assert nudges.send(ATHISH, "nudge", [world["c"]], "ping", ["app"])["sent"]


def test_a_text_goes_only_to_a_contact_with_a_phone_and_the_sms_channel(world):
    out = nudges.send(ATHISH, "nudge", [world["a"], world["b"], world["c"]], "call me", ["sms"])
    assert [(s["email"], s["channel"]) for s in out["sent"]] == [(world["a"], "sms")] and world["texts"][0][0] == "+15550000001"
    reasons = {s["email"]: s["reason"] for s in out["skipped"]}
    assert "No phone" in reasons[world["b"]] and "not turned on texts" in reasons[world["c"]]


def test_the_app_notice_is_marked_sent_by_the_sender_and_lands_in_the_persons_alerts_even_in_quiet_hours(monkeypatch, world):
    monkeypatch.setattr(notify, "_quiet", lambda: True)
    nudges.send(ATHISH, "nudge", [world["a"]], "need the shop drawing today", ["app", "email"])
    inbox = [n for n in notify.inbox(world["a"], open_only=True, limit=50) if n["kind"] == "nudge"]
    assert inbox and "Sent by Athish Singh" in inbox[0]["title"] and "need the shop drawing today" in inbox[0]["body"] and inbox[0]["severity"] == "warning"
    assert world["mails"] and "Sent by Athish Singh" in world["mails"][0][1]                       # the email went at once as well


def test_a_milestone_blast_needs_a_name_and_carries_it_and_the_due_date(world):
    with pytest.raises(nudges.NudgeError):
        nudges.send(ATHISH, "milestone", [world["a"]], "everyone", ["app"], milestone={"name": " "})
    nudges.send(ATHISH, "milestone", [world["a"]], "all hands", ["email"], milestone={"name": "Gate 1 for Zibo liners", "due": "2026-12-04"})
    assert "Milestone: Gate 1 for Zibo liners (due 2026-12-04): all hands" in world["mails"][0][2]


def test_practice_sends_nothing_writes_no_notice_and_is_not_rate_limited_but_is_audited(world):
    for _ in range(15):
        out = nudges.send(ATHISH, "nudge", [world["a"]], "rehearsal", ["app", "email", "sms"], practice=True)
    assert out["practice"] is True and out["message"].startswith("Practice only") and all(s["status"].startswith("practice") for s in out["sent"])
    assert world["mails"] == [] and world["texts"] == []
    assert not [n for n in notify.inbox(world["a"], open_only=True, limit=50) if n["kind"] == "nudge"]
    assert any(h["practice"] for h in nudges.history(ATHISH))


def test_the_rate_limit_is_per_sender_and_says_so(monkeypatch, world):
    monkeypatch.setattr(settings, "NUDGE_RATE_PER_HOUR", 3)
    for _ in range(3):
        nudges.send(ATHISH, "nudge", [world["a"]], "again", ["app"])
    with pytest.raises(nudges.NudgeError) as e:
        nudges.send(ATHISH, "nudge", [world["a"]], "again", ["app"])
    assert e.value.status == 429
    assert nudges.send(ADMIN, "nudge", [world["a"]], "admin is a different sender", ["app"])["sent"]


@pytest.mark.parametrize("kwargs", [{"message": ""}, {"message": "x" * 601}, {"channels": []}, {"channels": ["fax"]}, {"to": []}, {"kind": "shout"}, {"to": [f"p{i}@allied.test" for i in range(26)]}])
def test_bad_requests_are_refused_in_plain_words(world, kwargs):
    args = {"kind": "nudge", "to": [world["a"]], "message": "hello", "channels": ["app"]}
    args.update(kwargs)
    with pytest.raises(nudges.NudgeError) as e:
        nudges.send(ATHISH, args["kind"], args["to"], args["message"], args["channels"])
    assert e.value.status == 422 and e.value.message


def test_every_send_leaves_an_audit_entry_per_recipient_and_history_is_the_senders_own_unless_admin(world):
    def count():
        with SessionLocal() as db:
            return db.query(AuditLogDB).filter(AuditLogDB.actor_email == ATHISH["email"], AuditLogDB.action.in_(("NUDGE_SENT", "NUDGE_SKIPPED"))).count()

    before = count()
    nudges.send(ATHISH, "nudge", [world["a"], world["off"]], "audit me", ["app"])
    nudges.send(ADMIN, "nudge", [world["a"]], "admin message", ["app"])
    assert count() - before == 2
    own = nudges.history(ATHISH)
    assert {h["by"] for h in own} == {ATHISH["email"]} and any(h["note"] == "not staff" for h in own)
    assert {ATHISH["email"], ADMIN["email"]} <= {h["by"] for h in nudges.history(ADMIN)}


def test_the_api_roles_and_shape(auth_enforced, world):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    h = lambda email, role: {"Authorization": "Bearer " + staff_token(email, role)}  # noqa: E731
    body = {"kind": "nudge", "to": [world["a"]], "message": "hi", "channels": ["app"]}
    assert c.post("/api/v1/nudges", json=body, headers=h("v@allied.test", "VIEWER")).status_code == 403
    assert c.post("/api/v1/nudges", json=body, headers=h(OTHER["email"], "OPERATOR")).status_code == 403
    ok = c.post("/api/v1/nudges", json={**body, "practice": True}, headers=h(ATHISH["email"], "OPERATOR"))
    assert ok.status_code == 200 and set(ok.json()) >= {"sent", "skipped", "message", "practice"}
    assert c.get("/api/v1/nudges", headers=h(OTHER["email"], "OPERATOR")).status_code == 403
    got = c.get("/api/v1/nudges", headers=h(ATHISH["email"], "OPERATOR")).json()
    assert got["history"] and got["senders_set"] is True

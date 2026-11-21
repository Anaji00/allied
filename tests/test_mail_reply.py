"""Mail: only my mail, reply drafts (rule, AI, plain template), my AI settings and the AI-use log. Temporary database; the AI is a fake; nothing is sent."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, MailAiUseDB
from app.services import mail_log, mail_reply
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
ME = f"mailer-{TAG}@alliedweldingsystems.com"


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    head = {"Authorization": "Bearer " + staff_token(ME, "OPERATOR")}

    def call(method, path, expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=head, **kw)
        assert r.status_code == expect, f"{method} {path}: {r.status_code} {r.text[:300]}"
        return r.json()

    return call


def _mail(sender="cust@example.test", recipients="", category="other", summary="Hello, can you send a quote?", subject=None):
    mid = f"<{uuid.uuid4().hex}@t>"
    mail_log.record(mid, "INBOX", sender, "Cust", recipients, subject or f"Quote {TAG}", None, "in", category, summary, "LOGGED_ONLY", True)
    with SessionLocal() as db:
        from app.models.db_models import MailLogDB

        return db.query(MailLogDB).filter(MailLogDB.message_id == mid).one().id


def test_mine_matches_the_whole_address_only(api):
    a = _mail(sender=ME)
    b = _mail(recipients=f"x@y.test, {ME.upper()}")
    c = _mail(recipients=f"jo{ME}")                                   # joann@x is not ann@x
    got = api("GET", "/mail", params={"mine": True, "days": 30})
    ids = {m["id"] for m in got["mail"]}
    assert a in ids and b in ids and c not in ids and got["mine"] is True and "whole address" in got["match_note"]
    assert {"mail", "counts", "tags", "waiting"} <= set(got)
    assert c in {m["id"] for m in api("GET", "/mail", params={"days": 30, "limit": 1000})["mail"]}


def test_the_settings_are_off_by_default_validated_and_one_persons_own(api):
    assert api("GET", "/me/mail-settings") == {"ai_opt_in": False, "auto_draft": False, "style_note": "", "exclude_senders": [], "exclude_words": []}
    api("PUT", "/me/mail-settings", json={"auto_draft": True}, expect=422)                                   # automatic needs opting in
    api("PUT", "/me/mail-settings", json={"style_note": "x" * 501}, expect=422)
    got = api("PUT", "/me/mail-settings", json={"ai_opt_in": True, "style_note": "short", "exclude_senders": ["Boss@Example.test", "@bank.test"], "exclude_words": ["Salary"]})
    assert got["ai_opt_in"] and got["exclude_senders"] == ["boss@example.test", "@bank.test"] and got["exclude_words"] == ["salary"]
    assert mail_reply.get_settings(f"other-{TAG}@alliedweldingsystems.com")["ai_opt_in"] is False              # nobody else's
    api("PUT", "/me/mail-settings", json={"ai_opt_in": False})


def test_a_plain_template_when_the_ai_is_not_asked_and_errors_are_plain_sentences(api):
    m = _mail()
    got = api("POST", f"/mail/{m}/draft-reply", json={})
    assert got["source"] == "template" and got["body"] == "" and got["action_id"] is None and got["subject"].startswith("Re: ")
    assert api("POST", f"/mail/{m}/draft-reply", json={"use_ai": True}, expect=409).get("detail") == "AI replies are off for you."
    api("POST", "/mail/999999999/draft-reply", json={}, expect=404)
    api("POST", f"/mail/{m}/draft-reply", json={"use_ai": "maybe"}, expect=422)


def test_the_ai_is_used_only_when_opted_in_asked_and_not_excluded_and_never_sees_the_quote_or_signature(api, monkeypatch):
    from app.services.ai import features

    seen = []

    def fake(kind, facts, language="en", recipient="", reference=None, actor="system"):
        seen.append(facts)
        return features.EmailDraft(subject="Re: your quote", body="Thanks, we will send the quote.", states_commitment=False), "sg-1"

    monkeypatch.setattr(features, "draft_email", fake)
    api("PUT", "/me/mail-settings", json={"ai_opt_in": True, "exclude_senders": ["secret@example.test"], "exclude_words": ["salary"]})
    m = _mail(summary="Please quote 5 plates.\n> old quoted text\nOn Mon, Bob wrote:\nSO 12345 is ours\n-- \nSignature Co")
    got = api("POST", f"/mail/{m}/draft-reply", json={"use_ai": True})
    assert got["source"] == "ai" and got["body"].startswith("Thanks") and got["action_id"] and "old quoted" not in seen[0] and "Signature" not in seen[0] and "12345" not in seen[0]
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, got["action_id"])
        assert row.status == "PENDING" and row.tool == "send_email"                                           # waits for a person; nothing sent
    n = len(seen)
    assert api("POST", f"/mail/{_mail(sender='Secret <secret@example.test>')}/draft-reply", json={"use_ai": True}, expect=409)["detail"] == "This sender is on your never-send-to-AI list."
    assert "word" in api("POST", f"/mail/{_mail(summary='about my salary')}/draft-reply", json={"use_ai": True}, expect=409)["detail"]
    assert len(seen) == n                                                                                     # an excluded message never reached the AI
    with SessionLocal() as db:
        rows = db.query(MailAiUseDB).filter(MailAiUseDB.user_email == ME).all()
        assert {r.outcome for r in rows} >= {"drafted", "excluded_sender", "excluded_words"}
        assert not any(hasattr(r, "body") or hasattr(r, "prompt") or hasattr(r, "text") for r in rows)
    api("PUT", "/me/mail-settings", json={"ai_opt_in": False})


def test_when_the_ai_is_off_or_fails_the_template_is_returned(api, monkeypatch):
    from app.services.ai import features

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(features, "draft_email", boom)
    api("PUT", "/me/mail-settings", json={"ai_opt_in": True})
    got = api("POST", f"/mail/{_mail()}/draft-reply", json={"use_ai": True})
    assert got["source"] == "template" and got["body"] == "" and "not available" in got["note"]
    api("PUT", "/me/mail-settings", json={"ai_opt_in": False})


def test_a_delivery_question_gets_the_secretary_rule_draft(api):
    m = _mail(category="delivery_question", summary="Where is my order PO 12345?", sender="buyer@customer-example.test")
    got = api("POST", f"/mail/{m}/draft-reply", json={})
    assert got["source"] == "rule" and got["rule"] == "delivery_reply" and got["action_id"] and got["body"] and got["to"] == "buyer@customer-example.test"
    again = api("POST", f"/mail/{m}/draft-reply", json={})
    assert again["action_id"] == got["action_id"]                                                              # asking twice gives the same draft


def test_the_setup_page_says_zero_data_retention_is_not_confirmed_until_the_owner_sets_it(monkeypatch):
    from app.config import settings
    from app.services import setup_check

    def zdr():
        return next(i for i in setup_check.checklist()["items"] if i["key"] == "ai_zdr")

    monkeypatch.setattr(settings, "ALLIED_GROQ_ZDR_CONFIRMED", False)
    assert not zdr()["ok"] and "zero data retention not confirmed" in zdr()["label"].lower()
    monkeypatch.setattr(settings, "ALLIED_GROQ_ZDR_CONFIRMED", True)
    assert zdr()["ok"]


def test_each_email_carries_its_queue_state_and_why(api):
    waiting = _mail(category="other")
    rows = {m["id"]: m for m in api("GET", "/mail", params={"days": 30, "limit": 1000})["mail"]}
    q = rows[waiting]["queue"]
    assert q["state"] == "needs_reply" and q["reasons"] and "needs a person" in q["reasons"][0]
    api("POST", f"/mail/{waiting}/handled")
    rows = {m["id"]: m for m in api("GET", "/mail", params={"days": 30, "limit": 1000})["mail"]}
    assert rows[waiting]["queue"]["state"] == "done" and "Handled by" in rows[waiting]["queue"]["reasons"][0]


def test_managers_keep_reply_templates_with_known_merge_fields(api, auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    boss = {"Authorization": "Bearer " + staff_token(f"mgr-{TAG}@alliedweldingsystems.com", "MANAGER")}
    me = {"Authorization": "Bearer " + staff_token(ME, "OPERATOR")}
    key = f"thanks-{TAG}"
    ok = client.put(f"/api/v1/mail/templates/{key}", headers=boss, json={"title": "Thanks", "subject": "Re: {job}", "body": "Hello {first_name}, thanks about {po}.\n{signature}", "situation": "order"})
    assert ok.status_code == 200 and ok.json()["merge_fields"]
    assert client.put(f"/api/v1/mail/templates/{key}", headers=me, json={"subject": "s", "body": "b"}).status_code == 403
    bad = client.put(f"/api/v1/mail/templates/{key}", headers=boss, json={"subject": "s", "body": "Hello {nonsense}"})
    assert bad.status_code == 422 and "{nonsense}" in bad.json()["detail"]
    assert client.put("/api/v1/mail/templates/Bad Key", headers=boss, json={"subject": "s", "body": "b"}).status_code in (404, 422)
    assert any(t["key"] == key for t in client.get("/api/v1/mail/templates", headers=me).json())
    assert client.delete(f"/api/v1/mail/templates/{key}", headers=boss).json()["removed"]


def test_the_mail_status_says_in_one_sentence_whether_the_mailbox_is_connected(api, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "IMAP_HOST", "")
    got = api("GET", "/mail/status")
    assert got["connected"] is False and got["reason"] == "Not connected yet: the mailbox is not set up." and "running" in got["polling"]
    monkeypatch.setattr(settings, "IMAP_HOST", "imap.example.test")
    monkeypatch.setattr(settings, "IMAP_USERNAME", "")
    assert "login is missing" in api("GET", "/mail/status")["reason"]


def test_the_setup_page_explains_test_versus_live(monkeypatch):
    from app.config import settings
    from app.services import setup_check

    def line():
        return next(i for i in setup_check.checklist()["items"] if i["key"] == "live_mode")

    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert line()["ok"] is False and "Test" in line()["label"] and "APP_ENV=production" in line()["how_to_fix"] and "Live" in line()["how_to_fix"]
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert line()["ok"] is True

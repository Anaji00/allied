"""The plain-language helpers: Control room attention, Secretary summary, what I may do, task finder, glossary, the typed Secretary request (drafts only), the ledger download, and the stroke-letter fold.
Temporary data."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import EngineerGateDB, JobDB, MailLogDB
from app.services import guide, redaction
from tests.conftest import staff_token


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def H(email, role):
    return {"Authorization": "Bearer " + staff_token(email, role)}


def test_control_attention_has_every_key_money_is_manager_only_and_pending_gates_are_counted(client):
    with SessionLocal() as db:
        j = JobDB(ref=f"JOB-CTL-{uuid.uuid4().hex[:5]}", title="t", customer="C")
        db.add(j)
        db.commit()
        db.add(EngineerGateDB(job_id=j.id, gate=1, file_ids=[], status="PENDING"))
        db.commit()
    mgr = client.get("/api/v1/control/attention", headers=H("ctl.m@allied.test", "MANAGER")).json()
    assert set(mgr) == {"quiet_shipments", "payments_due", "gates_waiting", "drawings_unverified", "drafts_waiting", "backups"}
    assert mgr["gates_waiting"]["count"] >= 1 and mgr["payments_due"] is not None and {"state", "text"} <= set(mgr["backups"])
    ops = client.get("/api/v1/control/attention", headers=H("ctl.o@allied.test", "OPERATOR")).json()
    assert ops["payments_due"] is None
    assert client.get("/api/v1/control/attention", headers=H("ctl.v@allied.test", "VIEWER")).status_code == 403


def test_secretary_summary_shape_and_money_lines_for_managers_only(client):
    mgr = client.get("/api/v1/secretary/summary", headers=H("sec.m@allied.test", "MANAGER")).json()
    assert set(mgr) == {"mail_needing_reply", "drafts_waiting", "order_emails", "bills_to_check", "owed_to_us", "late_orders"} and mgr["bills_to_check"] is not None
    ops = client.get("/api/v1/secretary/summary", headers=H("sec.o@allied.test", "OPERATOR")).json()
    assert ops["bills_to_check"] is None and ops["owed_to_us"] is None and ops["mail_needing_reply"]["count"] >= 0


def test_me_can_gives_flags_and_a_reason_for_every_no(client, monkeypatch):
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "eng.can@allied.test")
    v = client.get("/api/v1/me/can", headers=H("can.v@allied.test", "VIEWER")).json()
    flags = {k for k in v if k.startswith("can_")}
    assert not any(v[k] for k in flags) and set(v["reasons"]) == flags
    e = client.get("/api/v1/me/can", headers=H("eng.can@allied.test", "OPERATOR")).json()
    assert e["can_approve_gates"] and e["can_make_orders"] and not e["can_record_payments"] and "can_approve_gates" not in e["reasons"]
    m = client.get("/api/v1/me/can", headers=H("can.m@allied.test", "MANAGER")).json()
    assert m["can_record_payments"] and m["can_override_gates"] and m["can_see_money"] and not m["can_approve_gates"]
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "")
    assert "set the approver list" in client.get("/api/v1/me/can", headers=H("can.m@allied.test", "MANAGER")).json()["reasons"]["can_approve_gates"]


def test_the_task_finder_answers_what_do_i_do_and_says_whether_i_may(client):
    r = client.get("/api/v1/search/tasks", params={"q": "record a payment"}, headers=H("tf.o@allied.test", "OPERATOR")).json()
    first = r["tasks"][0]
    assert first["id"] == "record_payment" and first["allowed_for_me"] is False and "manager" in first["not_allowed_because"].lower() and first["link"]
    assert client.get("/api/v1/search/tasks", params={"q": "record a payment"}, headers=H("tf.m@allied.test", "MANAGER")).json()["tasks"][0]["allowed_for_me"] is True
    ids = [t["id"] for t in client.get("/api/v1/search/tasks", params={"q": "approve shop drawing"}, headers=H("tf.m@allied.test", "MANAGER")).json()["tasks"]]
    assert ids[0] == "approve_shop_drawing"
    assert client.get("/api/v1/search/tasks", params={"q": "zzzzqqq"}, headers=H("tf.m@allied.test", "MANAGER")).json()["tasks"] == []
    assert len(client.get("/api/v1/search/tasks", headers=H("tf.m@allied.test", "MANAGER")).json()["tasks"]) >= 10


def test_the_glossary_has_the_plain_defaults_and_only_managers_edit_it(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_DIR", tmp_path)
    terms = {t["term"]: t for t in client.get("/api/v1/glossary/app", headers=H("g.v@allied.test", "VIEWER")).json()["terms"]}
    assert "remov" in terms["Redaction"]["plain"].lower() and "customer" in terms["Redaction"]["plain"].lower() and terms["PO"]["edited"] is False and "Awaiting invoice" in terms
    assert client.put("/api/v1/glossary/app/PO", json={"plain": "Our order to a supplier."}, headers=H("g.o@allied.test", "OPERATOR")).status_code == 403
    assert client.put("/api/v1/glossary/app/PO", json={"plain": "no"}, headers=H("g.m@allied.test", "MANAGER")).status_code == 422
    ok = client.put("/api/v1/glossary/app/PO", json={"plain": "Our order to a supplier: what, how many, the price, the date."}, headers=H("g.m@allied.test", "MANAGER"))
    assert ok.status_code == 200
    again = {t["term"]: t for t in client.get("/api/v1/glossary/app", headers=H("g.v@allied.test", "VIEWER")).json()["terms"]}
    assert again["PO"]["edited"] is True and again["PO"]["plain"].startswith("Our order") and (tmp_path / "app_glossary.json").exists()


def test_the_secretary_request_makes_drafts_only_and_says_when_it_cannot_tell(client):
    u = {"email": "sec.req@allied.test", "role": "OPERATOR"}
    assert guide.secretary_request("", u)["understood"] is False
    bad = client.post("/api/v1/ai/secretary", json={"prompt": "make me a sandwich"}, headers=H("sec.req@allied.test", "OPERATOR")).json()
    assert bad["understood"] is False and "could not tell" in bad["message"] and bad["examples"]
    summary = client.post("/api/v1/ai/secretary", json={"prompt": "give me my summary for today"}, headers=H("sec.req@allied.test", "OPERATOR")).json()
    assert summary["understood"] and summary["intent"] == "summary" and summary["drafts"] == []
    chase = client.post("/api/v1/ai/secretary", json={"prompt": "follow up with everyone who owes us"}, headers=H("sec.req@allied.test", "OPERATOR")).json()
    assert chase["intent"] == "follow_up" and "Nothing has been sent" in chase["message"]
    miss = client.post("/api/v1/ai/secretary", json={"prompt": "reply to Zzyzx Nobody"}, headers=H("sec.req@allied.test", "OPERATOR")).json()
    assert miss["intent"] == "reply" and miss["drafts"] == [] and "could not find" in miss["message"]
    assert client.post("/api/v1/ai/secretary", json={"prompt": "hi"}, headers=H("sec.v@allied.test", "VIEWER")).status_code == 403


def test_a_reply_request_finds_the_mail_and_queues_a_draft_that_is_not_sent(client):
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        db.add(MailLogDB(message_id=f"<{tag}@t>", folder="INBOX", sender=f"maria.{tag}@customer.example", recipients="me@alliedweldingsystems.com", subject=f"Quote question {tag}", direction="in", category="rfq",
                         outcome="LOGGED_ONLY", summary="Can you quote 10 plates?", received_at=datetime.now(timezone.utc)))
        db.commit()
    r = client.post("/api/v1/ai/secretary", json={"prompt": f"Reply to maria.{tag} about the quote"}, headers=H("sec.rep@allied.test", "OPERATOR")).json()
    assert r["intent"] == "reply" and "Nothing has been sent" in r["message"] and len(r["drafts"]) == 1


def test_the_ledger_downloads_for_managers_only(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    assert client.get("/api/v1/payments/ledger.xlsx", headers=H("led.o@allied.test", "OPERATOR")).status_code == 403
    r = client.get("/api/v1/payments/ledger.xlsx", headers=H("led.m@allied.test", "MANAGER"))
    assert r.status_code == 200 and r.content[:2] == b"PK"


@pytest.mark.parametrize("text,found", [("Øwens Corning", True), ("øwens Cørning", True), ("0wens C0rning", True), ("OWENS C0RN1NG", True), ("Owens Cornish", False)])
def test_stroke_letters_and_digits_for_letters_do_not_hide_a_name(text, found):
    assert bool(redaction.find_runs(text.split(), ["Owens Corning"])) is found


def test_digits_for_letters_do_not_make_a_short_part_number_look_like_a_term():
    assert redaction.find_runs(["Rev", "10", "B"], ["Lob"]) == []

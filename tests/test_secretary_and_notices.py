"""Draft notices grouped per batch, the fixes from the latest review, and the secretary drafts.

Fakes for Gmail, the AI and the phone; test database only."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MailLogDB, NotificationDB, OrderDB

from tests.test_e2e_allied_use_cases import REVIEWER, day  # noqa: F401


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables and loads the catalogs."""


# ---------------------------------------------------------------------------
# Drafts: one notice per batch, never one per draft
# ---------------------------------------------------------------------------


def test_a_batch_of_drafts_is_one_alert_and_one_push(monkeypatch):
    from app.services.assistant import quick
    from app.services.coordination import autopilot
    from app.services.coordination.autopilot import Intent

    pushes = []
    monkeypatch.setattr(quick, "push_batch", lambda email, items, waiting: pushes.append((email, len(items), waiting)))
    who = f"r{uuid.uuid4().hex[:5]}@alliedweldingsystems.com"
    intents = [Intent("draft", f"t-{uuid.uuid4().hex}", f"Ask V{i} when PO {i} ships", to=[who], tool="send_email",
                      args={"to": f"v{i}@v.test", "subject": "s", "body": "b"}) for i in range(9)]
    autopilot.tell_reviewers(autopilot.carry_out("vendor_chaser", "suggest", intents))
    assert pushes == [(who, 9, 9)]
    autopilot.tell_reviewers(autopilot.carry_out("vendor_chaser", "suggest", intents[:1] + [
        Intent("draft", f"t-{uuid.uuid4().hex}", "Ask V9 when PO 9 ships", to=[who], tool="send_email",
               args={"to": "v9@v.test", "subject": "s", "body": "b"})]))
    with SessionLocal() as db:
        live = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"autopilot-drafts:{who}:%"),
                                               NotificationDB.acknowledged_at.is_(None)).all()
    assert len(live) == 1 and live[0].title == "10 messages waiting for your OK"     # one live notice, the latest count
    assert pushes[-1] == (who, 1, 10)                                                  # only the new one, not the repeat


def test_one_draft_keeps_its_send_and_review_buttons_several_get_review_all(monkeypatch):
    from app.services.assistant import quick
    from app.services.coordination import notify

    sent = []
    for k, v in {"NTFY_URL": "https://ntfy.example.test", "NTFY_TOPIC": "t", "APP_BASE_URL": "https://192.168.12.235:8443"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(notify, "_quiet", lambda at=None: False)
    monkeypatch.setattr(quick.httpx, "post", lambda url, json, headers, timeout: sent.append(json) or type("R", (), {"status_code": 200})())
    quick.push_batch(REVIEWER, [{"id": "a1", "what": "Ask KWS when PO 9277 ships"}], 1)
    quick.push_batch(REVIEWER, [{"id": f"a{i}", "what": f"Ask V{i}"} for i in range(8)], 8)
    assert [a["label"] for a in sent[0]["actions"]] == ["Send", "Review"]
    assert [a["label"] for a in sent[1]["actions"]] == ["Review all"] and "and 2 more" in sent[1]["message"]


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


def test_mail_gmail_flags_as_forged_never_raises_an_order(day, monkeypatch):
    monkeypatch.setattr(settings, "AI_EMAIL_TRIAGE_ENABLED", False)
    po = f"PO-{uuid.uuid4().hex[:5].upper()}"
    mid = day["gmail"].add("Mark <purchasing@owenscorning.com>", f"{po}: Hammermill screens",
                           f"Please process our purchase order {po}.\nLine 1: 2x SCREEN-250 (Hammermill Screen Allied 300XL)",
                           headers="Authentication-Results: mx.google.com; spf=fail smtp.mailfrom=evil.test; dmarc=fail header.from=owenscorning.com\r\n")
    day["read_mail"]()
    with SessionLocal() as db:
        assert db.query(OrderDB).filter(OrderDB.customer_po_number == po).count() == 0
        log = db.query(MailLogDB).filter(MailLogDB.message_id == mid).one()
    assert log.needs_action and "forged" in log.summary


def test_genuine_mail_with_gmails_pass_still_raises_its_order(day, monkeypatch):
    monkeypatch.setattr(settings, "AI_EMAIL_TRIAGE_ENABLED", False)
    po = f"PO-{uuid.uuid4().hex[:5].upper()}"
    day["gmail"].add("Mark <purchasing@owenscorning.com>", f"{po}: Hammermill screens",
                     f"Please process our purchase order {po}.\nLine 1: 2x SCREEN-250 (Hammermill Screen Allied 300XL)",
                     headers="Authentication-Results: mx.google.com; dkim=pass header.i=@owenscorning.com; dmarc=pass\r\n")
    day["read_mail"]()
    with SessionLocal() as db:
        assert db.query(OrderDB).filter(OrderDB.customer_po_number == po).count() == 1


def test_when_the_ai_is_busy_mail_waits_instead_of_becoming_an_order(day, monkeypatch):
    from app.services.ai import features
    from app.services.ai.providers import AIRateLimitedError

    def busy(*a, **k):
        raise AIRateLimitedError("Groq: try again in 20s")

    monkeypatch.setattr(features, "triage_email", busy)
    po = f"PO-{uuid.uuid4().hex[:5].upper()}"
    mid = day["gmail"].add("buyer@owenscorning.com", f"Where is {po}?", "We still need 2x SCREEN-250 (Hammermill Screen Allied 300XL).")
    day["read_mail"]()
    with SessionLocal() as db:
        assert db.query(OrderDB).filter(OrderDB.customer_po_number == po).count() == 0
        assert db.query(MailLogDB).filter(MailLogDB.message_id == mid).count() == 0    # not logged: read again next pass
    from tests.test_e2e_allied_use_cases import _sort

    monkeypatch.setattr(features, "triage_email", _sort)                        # the AI is back
    day["read_mail"]()
    with SessionLocal() as db:
        log = db.query(MailLogDB).filter(MailLogDB.message_id == mid).one()
    assert log.category == "delivery_question"


def test_a_po_box_in_a_signature_is_not_a_po():
    from app.services.coordination import replies

    assert replies._PO.findall("Regards, KWS Mfg, P.O. Box 9277, Ningbo") == []
    assert replies._PO.findall("RE: Allied PO 9277: ship date?") == ["9277"]
    assert replies._PO.findall("our P.O. #9264 ships Friday") == ["9264"]


def test_phone_link_signatures_never_reach_the_access_log():
    import logging

    from app.main import _HideLinkSecrets

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", "/api/v1/assistant/quick/abc?e=k@a.test&x=1&s=deadbeef", "1.1", 200), None)
    _HideLinkSecrets().filter(rec)
    assert "deadbeef" not in rec.getMessage() and "/api/v1/assistant/quick/abc" in rec.getMessage()


# ---------------------------------------------------------------------------
# Secretary drafts
# ---------------------------------------------------------------------------


def _drafts_to(addr):
    from app.models.db_models import AssistantActionDB

    with SessionLocal() as db:
        return [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.status == "PENDING")
                if (r.args or {}).get("to") == addr]


def _oc_order(po, status="STAGED", vendor_po=None):
    from app.models.db_models import VendorJobDB

    with SessionLocal() as db:
        db.add(OrderDB(order_id=uuid.uuid4().hex[:8].upper(), customer_po_number=po, sales_order_number=str(70000 + uuid.uuid4().int % 9999),
                       customer_id="10221", customer_name="Owens Corning Insulating Systems Canada LP", status=status,
                       purchase_orders={"KWS": vendor_po} if vendor_po else {}, line_items=[]))
        if vendor_po:
            db.add(VendorJobDB(vendor="KWS", po_number=vendor_po, promised_on="2026-11-20", history=[], source="manual"))
        db.commit()


def test_where_is_my_order_gets_a_reply_built_from_the_records(day):
    po, vpo = f"PO-{uuid.uuid4().hex[:5].upper()}", str(80000 + uuid.uuid4().int % 9999)
    _oc_order(po, vendor_po=vpo)
    buyer = f"buyer{uuid.uuid4().hex[:4]}@owenscorning.com"
    day["gmail"].add(buyer, f"Where is {po}?", f"Hi, any update on {po}?",
                     headers="Authentication-Results: mx.google.com; dmarc=pass\r\n")
    day["read_mail"]()
    [draft] = _drafts_to(buyer)
    assert po in draft.args["body"] and "2026-11-20" in draft.args["body"] and "with our suppliers" in draft.args["body"]
    assert draft.status == "PENDING"                                   # a person sends it


def test_another_customers_order_is_never_described(day):
    po = f"PO-{uuid.uuid4().hex[:5].upper()}"
    _oc_order(po)
    nosy = f"buyer{uuid.uuid4().hex[:4]}@knauf.com"
    day["gmail"].add(nosy, f"Where is {po}?", f"Status of {po}?", headers="Authentication-Results: mx.google.com; dmarc=pass\r\n")
    day["read_mail"]()
    [draft] = _drafts_to(nosy)
    assert po not in draft.args["body"] and "PO number" in draft.args["body"]   # asks for their own PO instead


def test_a_complaint_gets_an_acknowledgement_draft_and_forged_mail_gets_nothing(day):
    real, forged = f"qa{uuid.uuid4().hex[:4]}@oc-irving.test", f"x{uuid.uuid4().hex[:4]}@oc-irving.test"
    day["gmail"].add(real, "Deflectors cracked", "Two deflectors cracked after a week.")
    day["gmail"].add(forged, "Liner cracked", "Cracked.", headers="Authentication-Results: mx.google.com; dmarc=fail\r\n")
    day["read_mail"]()
    [ack] = _drafts_to(real)
    assert "sorry" in ack.args["body"] and "come back to you by the end of" in ack.args["body"]
    assert _drafts_to(forged) == []


def test_each_person_gets_a_morning_brief_only_when_something_waits(monkeypatch):
    from app.services.coordination import secretary

    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", REVIEWER)
    lines = secretary.brief_for(REVIEWER)
    assert all(isinstance(x, str) for x in lines)
    assert secretary.brief_for(f"nobody{uuid.uuid4().hex[:4]}@alliedweldingsystems.com") == []
    sent = secretary.send_briefs()
    again = secretary.send_briefs()
    assert again == 0 and sent >= 0                                   # once a day per person

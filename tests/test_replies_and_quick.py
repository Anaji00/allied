"""Vendor replies read into one-click updates; approving a draft from the phone; batch review.

No real mailbox, monday, ntfy or mail server: replies are passed in, the push is captured."""

import uuid
from datetime import date, timedelta

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, NotificationDB, VendorJobDB
from app.services.coordination import autopilot, replies, vendors

REVIEWER = "reviewer@allied.test"


@pytest.fixture
def job(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "off")   # the plain date reader; no AI call
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", REVIEWER)
    name = f"V{uuid.uuid4().hex[:5].upper()}"
    po = str(uuid.uuid4().int % 90000 + 10000)
    monkeypatch.setattr(autopilot, "vendor_email", lambda n: "sales@kws-mfg.test" if n == name else None)
    j = vendors.create({"vendor": name, "description": "Auger", "po_number": po, "owner_email": "buyer@allied.test",
                        "ordered_on": (date.today() - timedelta(days=40)).isoformat(), "quoted_lead_days": 30},
                       {"email": "b@allied.test"})
    with SessionLocal() as db:  # SQLite reuses a dropped job's id; an earlier test's drafts must not count
        for r in db.query(AssistantActionDB):
            if (r.args or {}).get("_job_id") == j["id"]:
                db.delete(r)
        db.commit()
    yield j
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == j["id"]).delete()
        db.commit()


def _draft(job_id):
    with SessionLocal() as db:
        rows = [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.tool == "update_vendor_job")
                if (r.args or {}).get("_job_id") == job_id]
        return rows[-1] if rows else None


def test_a_vendor_reply_with_a_date_becomes_a_one_click_update(job):
    ship = date.today() + timedelta(days=12)
    out = replies.handle(f"EML-{uuid.uuid4().hex[:6]}", "Max <sales@kws-mfg.test>", f"RE: Allied PO {job['po_number']}: ship date?",
                         f"Hi, it will ship {ship:%B} {ship.day}. Photos attached.\n\n> Could you confirm the ship date for Allied PO")
    assert out["job_id"] == job["id"] and out["did"][0]["outcome"] == "drafted"
    row = _draft(job["id"])
    assert row.args["promised_on"] == ship.isoformat() and row.status == "PENDING"
    assert row.args["acknowledged_on"] == date.today().isoformat()
    assert any("photos" in w for w in row.args["_why"]) and "Update the order?" in row.summary
    # Confirming it records the date, which then survives the next board read (monday stays read only).
    from app.services.assistant import agent

    assert agent.decide(row.id, True, {"email": REVIEWER, "role": "MANAGER"})["status"] == "DONE"
    with SessionLocal() as db:
        r = db.get(VendorJobDB, job["id"])
        r.monday_item_id = None
        assert r.promised_on == ship.isoformat()


def test_a_reply_without_a_clear_date_is_an_alert_not_a_change(job):
    replies.handle("EML-unclear", "sales@kws-mfg.test", f"RE: Allied PO {job['po_number']}",
                   "We are working on it, should be end of the month or early next.")
    assert _draft(job["id"]) is None
    with SessionLocal() as db:
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key == f"autopilot:vendor-reply:{job['id']}:EML-unclear").count() == 1


def test_shipped_and_unmatched_replies(job):
    replies.handle("EML-shipped", "sales@kws-mfg.test", "Re: update", "Good news, the auger was shipped yesterday on board MSC ANNA.")
    assert _draft(job["id"]).args["shipped_on"] == date.today().isoformat()  # no date given: today
    assert replies.handle("EML-x", "someone@else.test", "Lunch?", "Are you free?") is None    # not a vendor reply
    out = replies.handle("EML-y", "someone@else.test", "RE: Allied PO 999999: ship date?", "Ships 3 Dec")
    assert out["job_id"] is None and "not matched" in out["did"][0]["what"]


def test_a_date_the_email_does_not_contain_is_never_used(job, monkeypatch):
    from app.services.ai import features

    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    fake = features.VendorReply(ship_date="2030-01-01", summary="ships soon", unclear=False, confidence=0.9)
    monkeypatch.setattr(features, "read_vendor_reply", lambda *a, **k: (fake, 1))
    said = replies.read("sales@kws-mfg.test", "RE: PO", "It ships soon, maybe 4 Dec.")
    assert said["ship_date"] is None and said["unclear"]     # the AI's date isn't in the email


def test_a_person_confirmed_date_survives_the_board_read():
    row = VendorJobDB(vendor="X", history=[{"at": "2026-10-31T10:00:00+00:00", "by": "boss@allied.test",
                                            "change": "promised on 2026-11-02 -> 2026-11-12"}], promised_on="2026-11-12")
    assert vendors._person_promise_holds(row, "2026-11-02")          # monday still shows the old date
    assert not vendors._person_promise_holds(row, "2026-11-20")      # someone changed it on monday: monday wins
    row.history.append({"at": "2026-10-31T12:00:00+00:00", "by": "monday", "change": "promised on 2026-11-12 -> 2026-11-02"})
    assert not vendors._person_promise_holds(row, "2026-11-02")


# ---------------------------------------------------------------------------
# Phone approval and batch review
# ---------------------------------------------------------------------------


def _pending_email(to=REVIEWER):
    aid = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=aid, user_email=to, user_role="AUTOPILOT", tool="send_email", status="PENDING",
                                 args={"to": "v@vendor.test", "subject": "Allied PO 1: ship date?", "body": "Hello",
                                       "_why": ["3 days late"], "_play": "vendor_chaser"}, summary="Ask V when PO 1 ships"))
        db.commit()
    return aid


def test_a_signed_link_shows_the_draft_and_only_a_post_decides(client, monkeypatch):
    from app.services.assistant import quick

    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", REVIEWER)
    aid = _pending_email()
    t = quick.token(aid, REVIEWER)
    page = client.get(f"/api/v1/assistant/quick/{aid}", params=t)
    assert page.status_code == 200 and "Allied PO 1: ship date?" in page.text and "3 days late" in page.text
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, aid).status == "PENDING"            # opening it changed nothing
    assert client.get(f"/api/v1/assistant/quick/{aid}", params={**t, "s": "0" * 40}).status_code == 403
    assert client.post(f"/api/v1/assistant/quick/{aid}", data={**t, "decision": "send"}).status_code == 200
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, aid).status in ("DONE", "FAILED")   # mail is off in tests: drafted
    again = client.post(f"/api/v1/assistant/quick/{aid}", data={**t, "decision": "send"})
    assert again.status_code == 409                                          # decided once only
    other = quick.token(aid, "stranger@x.test")
    assert client.get(f"/api/v1/assistant/quick/{aid}", params=other).status_code == 403


def test_new_drafts_are_pushed_with_send_and_review_buttons(monkeypatch):
    from app.services.assistant import quick
    from app.services.coordination import notify

    sent = []
    monkeypatch.setattr(settings, "NTFY_URL", "https://ntfy.example.test")
    monkeypatch.setattr(settings, "NTFY_TOPIC", "allied-test")
    monkeypatch.setattr(settings, "APP_BASE_URL", "https://ops.allied.test")
    monkeypatch.setattr(notify, "_quiet", lambda at=None: False)
    monkeypatch.setattr(quick.httpx, "post", lambda url, json, headers, timeout: sent.append(json) or type("R", (), {"status_code": 200})())
    assert quick.push_draft("abc", "Ask KWS when PO 9277 ships", REVIEWER) == "sent"
    labels = [a["label"] for a in sent[0]["actions"]]
    assert labels == ["Send", "Review"] and sent[0]["actions"][0]["method"] == "POST"
    assert "decision=send" in sent[0]["actions"][0]["body"] and "/assistant/quick/abc?" in sent[0]["actions"][1]["url"]
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://127.0.0.1:3010")   # a phone can't reach this
    quick.push_draft("abc", "x", REVIEWER)
    assert "actions" not in sent[1]


def test_batch_review_decides_each_draft_on_its_own(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", "dev@allied.local,admin@alliedweldingsystems.com")
    ids = [_pending_email("admin@alliedweldingsystems.com") for _ in range(3)]
    out = client.post("/api/v1/assistant/actions/batch", json={"ids": ids + ["nope"], "confirm": False}).json()
    statuses = {r["id"]: r["status"] for r in out["results"]}
    assert [statuses[i] for i in ids] == ["CANCELLED"] * 3 and statuses["nope"] == "SKIPPED" and out["done"] == 3


def test_waiting_drafts_say_what_kind_they_are(monkeypatch):
    from app.services.assistant import agent

    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", REVIEWER)
    aid = _pending_email()
    mine = {p["id"]: p for p in agent.pending({"email": REVIEWER, "role": "MANAGER"})}
    assert mine[aid]["kind"] == "vendor_follow_up"

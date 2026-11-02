"""Pushing the logic hard: volume, replays, races, tampering and messy input.

Fakes only (Gmail, AI, SMTP); a temporary database. Each test states the property it holds."""

import random
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, MailLogDB, NotificationDB, VendorJobDB
from app.services import mail_log, mail_tags
from app.services import mailbox_poller as mp
from app.services.coordination import autopilot, events, replies, vendors

from tests.test_e2e_allied_use_cases import REVIEWER, FakeGmail, _sort, day  # noqa: F401 - the day fixture

TODAY = date.today()


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------


def test_a_flood_of_600_mixed_emails_is_read_once_quickly_and_only_real_mail_is_read_by_ai(day, monkeypatch):
    from app.services.ai import features

    calls = []
    monkeypatch.setattr(features, "triage_email", lambda *a, **k: calls.append(1) or _sort(*a, **k))
    rng = random.Random(7)
    real = 0
    for i in range(600):
        kind = rng.choice(["news", "ooo", "notify", "rfq", "internal", "question"])
        if kind == "news":
            day["gmail"].add(f"news{i}@list.test", f"Weekly {i}", "...", headers="List-Id: <steel.list.test>\r\n")
        elif kind == "ooo":
            day["gmail"].add(f"p{i}@cust.test", "Out of office", "Back Monday", headers="Auto-Submitted: auto-replied\r\n")
        elif kind == "notify":
            day["gmail"].add("noreply@monday.com", f"Update {i}", "...")
        else:
            real += 1
            subject = {"rfq": f"RFQ {i} liners", "internal": f"Crating plan {i}", "question": f"Where is PO {i}?"}[kind]
            sender = "ops@alliedweldingsystems.com" if kind == "internal" else f"buyer{i}@cust{i % 9}.test"
            day["gmail"].add(sender, subject, "Please advise.")
    started = time.perf_counter()
    first = day["read_mail"]()
    took = time.perf_counter() - started
    again = day["read_mail"]()
    assert first["ingested"] + first["orders_created"] + first["logged_only"] == 600
    assert again == {"ingested": 0, "orders_created": 0, "logged_only": 0}   # nothing read twice
    assert len(calls) == real                                                  # AI only for real mail
    assert took < 60, f"600 emails took {took:.1f}s"


def test_a_burst_of_800_board_events_is_never_dropped(monkeypatch):
    """Past the queue cap the board read runs the reaction itself; every event is handled."""
    monkeypatch.setattr(settings, "AUTOPILOT_ENABLED", True)
    monkeypatch.setattr(settings, "AUTOPILOT_EVENTS_ASYNC", True)
    handled, lock = [], threading.Lock()

    def slow_handle(event, payload, summary="", dry_run=False):
        time.sleep(0.001)
        with lock:
            handled.append(payload["n"])
        return {}

    monkeypatch.setattr(events, "handle", slow_handle)
    monkeypatch.setattr(events, "_waiting", threading.BoundedSemaphore(50))
    for n in range(800):
        events.emit("rfq.status_changed", {"n": n, "rfq": {}}, "")
    deadline = time.time() + 30
    while len(handled) < 800 and time.time() < deadline:
        time.sleep(0.05)
    assert sorted(handled) == list(range(800))


# ---------------------------------------------------------------------------
# Races and replays
# ---------------------------------------------------------------------------


def test_ten_people_pressing_send_at_once_send_it_exactly_once(day):
    from app.services.assistant import agent

    aid = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=aid, user_email=REVIEWER, user_role="AUTOPILOT", tool="send_email", status="PENDING",
                                 args={"to": "v@vendor.test", "subject": "Allied PO 1: ship date?", "body": "Hello",
                                       "_play": "vendor_chaser"}, summary="Ask V when PO 1 ships"))
        db.commit()
    outcomes, barrier = [], threading.Barrier(10)

    def press():
        barrier.wait()
        try:
            outcomes.append(agent.decide(aid, True, {"email": REVIEWER, "role": "MANAGER"})["status"])
        except ValueError:
            outcomes.append("refused")

    threads = [threading.Thread(target=press) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("DONE") == 1 and outcomes.count("refused") == 9
    assert len(day["sent"]) == 1


def test_the_timer_and_an_event_drafting_the_same_chaser_at_once_make_one(monkeypatch):
    name = f"V{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(autopilot, "vendor_email", lambda n: "s@v.test")
    job = vendors.create({"vendor": name, "po_number": "8123", "ordered_on": (TODAY - timedelta(days=90)).isoformat(),
                          "quoted_lead_days": 30}, {"email": "b@allied.test"})
    j = {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]
    intent = autopilot.vendor_chase(j, TODAY)
    out, barrier = [], threading.Barrier(8)

    def draft():
        barrier.wait()
        out.append(autopilot.carry_out("vendor_chaser", "suggest", [intent])[0]["outcome"])

    threads = [threading.Thread(target=draft) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert out.count("drafted") == 1 and out.count("already") == 7
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


def test_two_readers_on_the_same_mailbox_log_each_email_once(day):
    for i in range(40):
        day["gmail"].add(f"b{i}@cust.test", f"RFQ {i}", "Please quote")
    a, b = mp.MailboxPoller(), mp.MailboxPoller()
    a._connect = b._connect = lambda: day["gmail"]
    threads = [threading.Thread(target=p.poll_once) for p in (a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with SessionLocal() as db:
        ids = [m for (m,) in db.query(MailLogDB.message_id).filter(MailLogDB.subject.like("RFQ %"))]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Tampering
# ---------------------------------------------------------------------------


def test_phone_links_can_not_be_forged_reused_or_moved_to_another_draft(day, client):
    from app.services.assistant import quick

    ids = []
    with SessionLocal() as db:
        for _ in range(2):
            aid = uuid.uuid4().hex
            ids.append(aid)
            db.add(AssistantActionDB(id=aid, user_email=REVIEWER, user_role="AUTOPILOT", tool="send_email", status="PENDING",
                                     args={"to": "v@v.test", "subject": "s", "body": "b", "_play": "vendor_chaser"}, summary="s"))
        db.commit()
    good = quick.token(ids[0], REVIEWER)
    url = f"/api/v1/assistant/quick/{{}}"
    forged = [
        {**good, "s": good["s"][:-1] + ("0" if good["s"][-1] != "0" else "1")},   # one character off
        {**good, "x": str(int(good["x"]) + 3600)},                               # stretched expiry
        {**good, "e": "someone@else.test"},                                      # another person
        {**good, "x": str(int(time.time()) - 1), "s": quick._sig(ids[0], REVIEWER, int(time.time()) - 1)},  # lapsed
        {"e": "", "x": "", "s": ""},
    ]
    for t in forged:
        assert client.post(url.format(ids[0]), data={**t, "decision": "send"}).status_code == 403
    assert client.post(url.format(ids[1]), data={**good, "decision": "send"}).status_code == 403  # another draft
    assert client.post(url.format(ids[0]), data={**good, "decision": "launch"}).status_code == 400
    assert client.get(url.format(ids[0]), params=good).status_code == 200
    assert client.post(url.format(ids[0]), data={**good, "decision": "send"}).status_code == 200
    assert client.post(url.format(ids[0]), data={**good, "decision": "send"}).status_code == 409      # used once
    assert len(day["sent"]) == 1


def test_the_review_page_escapes_whatever_a_vendor_or_email_put_in_the_draft(day, client):
    from app.services.assistant import quick

    aid = uuid.uuid4().hex
    nasty = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=aid, user_email=REVIEWER, user_role="AUTOPILOT", tool="send_email", status="PENDING",
                                 args={"to": "v@v.test", "subject": nasty, "body": nasty, "_why": [nasty], "_play": "x"},
                                 summary=nasty))
        db.commit()
    page = client.get(f"/api/v1/assistant/quick/{aid}", params=quick.token(aid, REVIEWER)).text
    assert "<script>" not in page and "<img" not in page and "&lt;script&gt;" in page


# ---------------------------------------------------------------------------
# Messy input
# ---------------------------------------------------------------------------


def test_the_date_reader_never_crashes_and_never_goes_outside_its_window():
    rng = random.Random(11)
    bits = ["Nov", "November", "12", "31", "2/30", "13/13", "0/0", "2026-02-30", "99/99/9999", "1st", "Dec 3rd, 2026",
            "ships", "maybe", "or", "-", "/", "  ", "Feb 29", "11/12/26", "12 Jan", "end of month", "一月"]
    on = date(2026, 10, 31)
    for _ in range(3000):
        text = " ".join(rng.choice(bits) for _ in range(rng.randint(1, 8)))
        for d in replies._dates_in(text, on):
            assert on - timedelta(days=45) <= d <= on + timedelta(days=365), (text, d)


def test_tagging_survives_huge_and_hostile_mail():
    body = ("rfq " * 200_000) + ("(" * 50_000) + "\x00\ud800"[:1]
    started = time.perf_counter()
    tags = mail_tags.topics("RE: " * 500 + "urgent", body)
    assert "rfq" in tags and "urgent" in tags and time.perf_counter() - started < 2


def test_a_reply_naming_a_po_two_vendors_share_goes_to_the_right_vendor_or_to_a_person(monkeypatch):
    a, b = f"A{uuid.uuid4().hex[:4]}", f"B{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(autopilot, "vendor_email", {a: "sales@vendor-a.test", b: "sales@vendor-b.test"}.get)
    po = str(70000 + uuid.uuid4().int % 9999)
    ja = vendors.create({"vendor": a, "po_number": po}, {"email": "x@allied.test"})
    jb = vendors.create({"vendor": b, "po_number": po}, {"email": "x@allied.test"})
    try:
        assert replies.match_job("sales@vendor-b.test", f"RE: Allied PO {po}", "")["id"] == jb["id"]
        assert replies.match_job("someone@else.test", f"RE: Allied PO {po}", "") is None   # can't tell: a person decides
    finally:
        with SessionLocal() as db:
            db.query(VendorJobDB).filter(VendorJobDB.id.in_([ja["id"], jb["id"]])).delete(synchronize_session=False)
            db.commit()


def test_an_email_with_no_message_id_or_date_is_still_logged_once(day):
    raw = b"From: x@cust.test\r\nSubject: RFQ hopper\r\n\r\nPlease quote\r\n"
    day["gmail"].messages += [raw, raw]                 # the same headerless message twice
    day["read_mail"]()
    day["read_mail"]()
    with SessionLocal() as db:
        assert db.query(MailLogDB).filter(MailLogDB.subject == "RFQ hopper").count() == 1


def test_an_automatic_retry_of_a_failed_send_happens_once_however_many_runs_overlap(day, monkeypatch):
    from app.services.coordination.autopilot import Intent

    intent = Intent("draft", f"retry-{uuid.uuid4().hex}", "Ask V when PO 2 ships", to=[REVIEWER], tool="send_email",
                    args={"to": "v@v.test", "subject": "Allied PO 2: ship date?", "body": "Hello"})
    action_id = uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{intent.key}").hex
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=action_id, user_email=REVIEWER, user_role="AUTOPILOT", tool="send_email",
                                 args={**intent.args, "_play": "vendor_chaser"}, summary=intent.summary, status="FAILED"))
        db.commit()
    out, barrier = [], threading.Barrier(6)

    def run():
        barrier.wait()
        out.append(autopilot.carry_out("vendor_chaser", "auto", [intent])[0]["outcome"])

    threads = [threading.Thread(target=run) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert out.count("done") == 1 and len(day["sent"]) == 1

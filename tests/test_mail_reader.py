"""The mailbox reader: every folder, each message once (by Message-ID), nothing marked read
unless asked, older mail logged but never acted on, and every message in the mail log.

A fake IMAP server stands in for Gmail; no real mailbox is touched."""

import uuid
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MailLogDB, NotificationDB
from app.services import mail_log
from app.services import mailbox_poller as mp


def _raw(sender, subject, body, mid, when=None, to="ops@alliedweldingsystems.com", headers=""):
    when = when or datetime.now(timezone.utc)
    return (f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\nMessage-ID: {mid}\r\n{headers}"
            f"Date: {format_datetime(when)}\r\n\r\n{body}\r\n").encode()


class FakeImap:
    """Folders of raw messages; records what was opened read-only and what was flagged."""

    def __init__(self, folders):
        self.folders, self.current, self.readonly, self.stored = folders, None, [], []

    def select(self, folder, readonly=False):
        name = folder.strip('"')
        if name not in self.folders:
            return ("NO", [b"no such folder"])
        self.current = name
        self.readonly.append(readonly)
        return ("OK", [str(len(self.folders[name])).encode()])

    def search(self, charset, *criteria):
        n = len(self.folders[self.current])
        return ("OK", [b" ".join(str(i).encode() for i in range(1, n + 1))])

    def fetch(self, nums, spec):
        msgs = self.folders[self.current]
        wanted = [int(x) for x in (nums if isinstance(nums, bytes) else nums.encode()).split(b",")]
        if "HEADER.FIELDS" in spec:
            out = []
            for i in wanted:
                mid = [ln for ln in msgs[i - 1].split(b"\r\n") if ln.startswith(b"Message-ID")]
                out += [(f"{i} (BODY[HEADER.FIELDS (MESSAGE-ID)] {{40}}".encode(), (mid[0] if mid else b"") + b"\r\n\r\n"), b")"]
            return ("OK", out)
        return ("OK", [(f"{wanted[0]} (BODY[] {{100}}".encode(), msgs[wanted[0] - 1]), b")"])

    def store(self, num, flags, value):
        self.stored.append((num, value))

    def close(self):
        pass

    def logout(self):
        pass


class Daemon:
    def __init__(self, status="RFQ_STAGED", notes="Logged as RFQ-2026-0099", fail=False):
        self.seen, self.status, self.notes, self.fail = [], status, notes, fail

    def process_inbound_email(self, message):
        self.seen.append(message.subject)
        if self.fail:
            raise RuntimeError("downstream failure")
        return type("R", (), {"status": self.status, "notes": self.notes, "sales_order_number": None})()


@pytest.fixture(autouse=True)
def _mailbox(monkeypatch):
    monkeypatch.setattr(settings, "IMAP_MARK_SEEN", False)
    monkeypatch.setattr(settings, "IMAP_FOLDERS", "INBOX,[Gmail]/Sent Mail")
    monkeypatch.setattr(settings, "IMAP_USERNAME", "ops@alliedweldingsystems.com")
    monkeypatch.setattr(settings, "MAIL_OUR_DOMAINS", "alliedweldingsystems.com")
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEWER_EMAILS", "reviewer@allied.test")


def _mid():
    return f"<{uuid.uuid4().hex}@example.test>"


def _logged(mid):
    with SessionLocal() as db:
        return db.query(MailLogDB).filter(MailLogDB.message_id == mid).one_or_none()


def test_every_message_is_handled_once_and_nothing_is_marked_read():
    rfq, sent = _mid(), _mid()
    imap = FakeImap({"INBOX": [_raw("Buyer <buyer@knauf.test>", "RFQ: wear liners", "Please quote 40 liners", rfq)],
                     "[Gmail]/Sent Mail": [_raw("ops@alliedweldingsystems.com", "Re: liners", "Quote attached", sent)]})
    daemon = Daemon()
    poller = mp.MailboxPoller(daemon=daemon)
    poller._connect = lambda: imap
    first = poller.poll_once()
    second = poller.poll_once()                       # the same mail again: nothing is redone
    assert daemon.seen == ["RFQ: wear liners"] and first["ingested"] == 1 and first["logged_only"] == 1
    assert second == {"ingested": 0, "orders_created": 0, "logged_only": 0}
    assert imap.stored == [] and all(imap.readonly)   # opened read only; Gmail still shows it as new
    row = _logged(rfq)
    assert (row.category, row.outcome, row.direction, row.needs_action) == ("quote_request", "RFQ_STAGED", "in", False)
    assert _logged(sent).direction == "out" and _logged(sent).category == "sent"


def test_older_mail_is_logged_but_never_acted_on():
    old = _mid()
    when = datetime.now(timezone.utc) - timedelta(days=2)
    imap = FakeImap({"INBOX": [_raw("buyer@knauf.test", "PO 5512 hammermill screens", "Please supply 2 screens", old, when)],
                     "[Gmail]/Sent Mail": []})
    daemon = Daemon(status="PROCESSED_ORDER_CREATED")
    poller = mp.MailboxPoller(daemon=daemon)
    poller._connect = lambda: imap
    assert poller.poll_once()["logged_only"] == 1
    assert daemon.seen == [] and _logged(old).category == "earlier"   # no order from yesterday's backlog


def test_internal_mail_is_marked_internal_and_questions_raise_an_alert():
    internal, question = _mid(), _mid()
    imap = FakeImap({"INBOX": [_raw("Karissa <karissa@alliedweldingsystems.com>", "Crating tomorrow", "Truck at 9", internal),
                               _raw("buyer@oc.test", "Where is PO 4520479478?", "When will it arrive?", question)],
                     "[Gmail]/Sent Mail": []})

    class Sorting(Daemon):
        def process_inbound_email(self, message):
            self.seen.append(message.subject)
            status = "SORTED_INTERNAL" if "alliedwelding" in message.sender_email else "SORTED_DELIVERY_QUESTION"
            return type("R", (), {"status": status, "notes": f"AI sorted this. {message.subject}", "sales_order_number": None})()

    poller = mp.MailboxPoller(daemon=Sorting())
    poller._connect = lambda: imap
    poller.poll_once()
    assert (_logged(internal).direction, _logged(internal).category, _logged(internal).needs_action) == ("internal", "internal", False)
    q = _logged(question)
    assert q.category == "delivery_question" and q.needs_action
    with SessionLocal() as db:
        n = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"mail:{question}:%")).one()
        assert n.recipients == ["reviewer@allied.test"] and "Where is PO" in n.title


def test_a_message_that_fails_is_logged_for_a_person_not_retried_forever():
    bad = _mid()
    imap = FakeImap({"INBOX": [_raw("x@vendor.test", "Odd file", "???", bad)], "[Gmail]/Sent Mail": []})
    daemon = Daemon(fail=True)
    poller = mp.MailboxPoller(daemon=daemon)
    poller._connect = lambda: imap
    poller.poll_once()
    poller.poll_once()
    assert daemon.seen == ["Odd file"]                 # tried once
    assert _logged(bad).outcome == "FAILED" and _logged(bad).needs_action


def test_marking_read_only_when_asked(monkeypatch):
    monkeypatch.setattr(settings, "IMAP_MARK_SEEN", True)
    mid = _mid()
    imap = FakeImap({"INBOX": [_raw("buyer@knauf.test", "RFQ: chute", "Quote please", mid)], "[Gmail]/Sent Mail": []})
    poller = mp.MailboxPoller(daemon=Daemon())
    poller._connect = lambda: imap
    poller.poll_once()
    assert imap.stored == [(b"1", "\\Seen")] and imap.readonly[0] is False


def test_the_mail_log_can_be_listed_searched_and_handled(client):
    mid = _mid()
    mail_log.record(mid, "INBOX", "buyer@oc.test", "Buyer", "", "Complaint about weld", datetime.now(timezone.utc),
                    "in", "complaint", "Weld cracked", "SORTED_COMPLAINT", True)
    got = client.get("/api/v1/mail", params={"needs_action": True, "q": "weld"}).json()
    row = next(m for m in got["mail"] if m["message_id"] == mid)
    assert got["counts"].get("complaint", 0) >= 1 and got["waiting"] >= 1 and "reader" in got
    assert client.post(f"/api/v1/mail/{row['id']}/handled").json()["handled_by"]
    after = client.get("/api/v1/mail", params={"needs_action": True, "q": "weld"}).json()
    assert all(m["message_id"] != mid for m in after["mail"])


def test_who_is_who():
    assert mail_log.direction("karissa@alliedweldingsystems.com", "INBOX") == "internal"
    assert mail_log.direction("ops@alliedweldingsystems.com", "INBOX") == "out"
    assert mail_log.direction("buyer@knauf.test", "INBOX") == "in"
    assert mail_log.classify("SORTED_COMPLAINT") == ("complaint", True)
    assert mail_log.classify("SORTED_INTERNAL") == ("internal", False)
    assert mail_log.classify("FLAGGED_FOR_REVIEW") == ("order", True)


class Sorter(Daemon):
    """Sorts by a word in the subject, the way the AI would."""

    def process_inbound_email(self, message):
        self.seen.append(message.subject)
        s = message.subject.lower()
        status = ("SORTED_COMPLAINT" if "cracked" in s else "SORTED_SUPPLIER_INVOICE" if "invoice" in s
                  else "SORTED_OTHER")
        return type("R", (), {"status": status, "notes": message.subject, "sales_order_number": None})()


def _poll(messages):
    imap = FakeImap({"INBOX": messages, "[Gmail]/Sent Mail": []})
    daemon = Sorter()
    poller = mp.MailboxPoller(daemon=daemon)
    poller._connect = lambda: imap
    poller.poll_once()
    return daemon


def test_noise_is_logged_quietly_without_an_ai_read(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_QUIET_SENDERS", "monday.com")
    ids = [_mid() for _ in range(5)]
    daemon = _poll([
        _raw("news@supplier.test", "October deals", "Big savings", ids[0], headers="List-Unsubscribe: <mailto:u@x>\r\n"),
        _raw("buyer@knauf.test", "Automatic reply: RFQ", "I am out of the office", ids[1], headers="Auto-Submitted: auto-replied\r\n"),
        _raw("mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", "Not delivered", ids[2]),
        _raw("notifications@monday.com", "Karissa mentioned you", "on 2026 Orders", ids[3]),
        _raw("buyer@knauf.test", "RFQ for chute liners", "Please quote", ids[4]),
    ])
    assert daemon.seen == ["RFQ for chute liners"]                      # only the real one is read
    assert [_logged(i).tags.split(",")[0] for i in ids[:4]] == ["newsletter", "auto-reply", "bounce", "notification"]
    assert all(_logged(i).category == "noise" for i in ids[:4])
    feed = mail_log.list_mail(days=1, limit=1000)
    shown = {m["message_id"] for m in feed["mail"]}
    assert ids[4] in shown and not shown & set(ids[:4]) and feed["quiet_hidden"]
    assert ids[0] in {m["message_id"] for m in mail_log.list_mail(days=1, limit=1000, include_quiet=True)["mail"]}


def test_tags_come_from_the_words_and_can_be_added(monkeypatch):
    from app.services import mail_tags

    assert set(mail_tags.topics("RE: PO 9264 proforma", "Photos attached. Ship date ETA 12 Nov, urgent")) >= {
        "po", "invoice", "photos", "shipping", "urgent"}
    monkeypatch.setattr(settings, "MAIL_TAG_RULES", '{"hopper": ["bottom hopper"]}')
    assert "hopper" in mail_tags.topics("Bottom Hopper drawing", "")
    monkeypatch.setattr(settings, "MAIL_TAG_RULES", "not json")
    assert "rfq" in mail_tags.topics("RFQ", "")                         # bad rules fall back to the built-in ones


def test_a_whole_thread_raises_one_alert_a_day():
    root = _mid()
    replies = [_raw("buyer@oc.test", "RE: Liner cracked again", "Still cracked", _mid(),
                    headers=f"In-Reply-To: {root}\r\nReferences: {root}\r\n") for _ in range(3)]
    _poll([_raw("buyer@oc.test", "Liner cracked", "The liner cracked after a week", root)] + replies)
    with SessionLocal() as db:
        alerts = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"mail:{root}:%")).all()
        logged = db.query(MailLogDB).filter(MailLogDB.thread_key == root).count()
    assert logged == 4 and len(alerts) == 1 and alerts[0].severity == "critical"


def test_what_can_wait_goes_in_the_summary_not_an_alert():
    inv, rush = _mid(), _mid()
    _poll([_raw("ar@kws-mfg.test", "Invoice 7781 for PO 9277", "Attached", inv),
           _raw("buyer@oc.test", "Line down, need screens ASAP", "Urgent", rush)])
    with SessionLocal() as db:
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"mail:{inv}:%")).count() == 0
        rushed = db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"mail:{rush}:%")).one()
        assert rushed.title.startswith("Urgent email:")
    assert _logged(inv).needs_action and _logged(rush).needs_action and "urgent" in _logged(rush).tags
    assert any("Invoice 7781" in line for line in mail_log.for_summary())
    assert len(mail_log.list_mail(days=1, tag="invoice", limit=1000)["mail"]) >= 1

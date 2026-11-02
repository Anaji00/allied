"""Allied's real working day, end to end, through the API.

Each test is one thing that happens at Allied, followed from the trigger (an email, a board
change, a vendor's answer) to its final effect (a draft, a sent email, an updated order, an
alert), with the people and orders on Allied's boards. Everything runs on the test database;
Gmail (in and out), monday, ntfy and the AI are fakes. Nothing real is read or written.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, MailLogDB, NotificationDB, RfqDB, VendorJobDB
from app.services import mail_log
from app.services import mailbox_poller as mp
from app.services.ai import features
from app.services.coordination import autopilot, events, rfqs, vendors
from app.services.outbound_email_service import outbound_email_service

REVIEWER = "karissa@alliedweldingsystems.com"
TEST_INBOX = "pilot@allied.test"
TODAY = date.today()


# ---------------------------------------------------------------------------
# Fakes: Gmail in (IMAP), Gmail out (SMTP), the AI's email sorting
# ---------------------------------------------------------------------------


class FakeGmail:
    """One mailbox; messages are added as the day goes on."""

    def __init__(self):
        self.messages, self.flagged = [], []

    def add(self, sender, subject, body, mid=None, headers="", when=None):
        mid = mid or f"<{uuid.uuid4().hex}@mail.test>"
        when = when or datetime.now(timezone.utc)
        self.messages.append((f"From: {sender}\r\nTo: operations@alliedweldingsystems.com\r\nSubject: {subject}\r\n"
                              f"Message-ID: {mid}\r\n{headers}Date: {format_datetime(when)}\r\n\r\n{body}\r\n").encode())
        return mid

    # the imaplib calls the reader makes
    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def search(self, charset, *criteria):
        return ("OK", [b" ".join(str(i).encode() for i in range(1, len(self.messages) + 1))])

    def fetch(self, nums, spec):
        wanted = [int(x) for x in (nums if isinstance(nums, bytes) else nums.encode()).split(b",")]
        if "HEADER.FIELDS" in spec:
            out = []
            for i in wanted:
                mid = [ln for ln in self.messages[i - 1].split(b"\r\n") if ln.startswith(b"Message-ID")]
                out += [(f"{i} (BODY[HEADER.FIELDS (MESSAGE-ID)] {{1}}".encode(), (mid[0] if mid else b"") + b"\r\n\r\n"), b")"]
            return ("OK", out)
        return ("OK", [(f"{wanted[0]} (BODY[] {{1}}".encode(), self.messages[wanted[0] - 1]), b")"])

    def store(self, num, flags, value):
        self.flagged.append(num)

    def close(self):
        pass

    def logout(self):
        pass


def _sort(sender, subject, body, attachments=None, reference=None, actor="system"):
    """Stands in for the AI: sorts the way it is told to in the triage prompt."""
    text = f"{subject} {body}".lower()
    if "alliedweldingsystems.com" in sender and not text.startswith(("fwd", "fw:")) and "rfq" not in text:
        cat = "internal"
    elif any(w in text for w in ("rfq", "quote", "pricing")):
        cat = "quote_request"
    elif any(w in text for w in ("cracked", "defect", "wrong part")):
        cat = "complaint"
    elif any(w in text for w in ("where is", "when will", "status of our")):
        cat = "delivery_question"
    elif "invoice" in text:
        cat = "supplier_invoice"
    else:
        cat = "other"
    return features.EmailTriage(category=cat, language="en", summary=subject[:120], customer_name="", po_number="",
                                requested_date="", lines=[], uncertain_fields=[], confidence=0.95), 1


@pytest.fixture
def day(monkeypatch):
    """Allied's setup as configured: Gmail in test mode (everything to one address), sending on,
    autopilot on, one reviewer, AI sorting on (faked)."""
    sent = []
    for k, v in {"AUTOPILOT_ENABLED": True, "AUTOPILOT_EVENTS_ASYNC": False, "AUTOPILOT_REVIEWER_EMAILS": REVIEWER,
                 "EMAIL_SEND_ENABLED": True, "SMTP_HOST": "smtp.gmail.com", "SMTP_USERNAME": "operations@alliedweldingsystems.com",
                 "SMTP_PASSWORD": "app-password", "EMAIL_REDIRECT_ALL_TO": TEST_INBOX, "AI_EMAIL_TRIAGE_ENABLED": True,
                 "AI_PROVIDER": "off", "IMAP_MARK_SEEN": False, "IMAP_FOLDERS": "[Gmail]/All Mail",
                 "IMAP_USERNAME": "operations@alliedweldingsystems.com", "MAIL_OUR_DOMAINS": "alliedweldingsystems.com",
                 "APP_BASE_URL": "https://ops.allied.test"}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(features, "triage_email", _sort)
    monkeypatch.setattr(outbound_email_service, "_deliver", lambda msg, to: sent.append((msg["Subject"], list(to), msg.get_content())))
    gmail = FakeGmail()
    poller = mp.MailboxPoller()
    poller._connect = lambda: gmail
    return {"gmail": gmail, "read_mail": poller.poll_once, "sent": sent}


def _drafts(play=None, job_id=None, status="PENDING"):
    with SessionLocal() as db:
        rows = db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT",
                                                  AssistantActionDB.status == status).all()
        return [r for r in rows if (play is None or (r.args or {}).get("_play") == play)
                and (job_id is None or (r.args or {}).get("_job_id") == job_id)]


def _alerts(prefix):
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"{prefix}%")).all()


def _logged(mid):
    with SessionLocal() as db:
        return db.query(MailLogDB).filter(MailLogDB.message_id == mid).one()


def _kingcera_order(monkeypatch, **kw):
    """KINGCERA, Dilute Media Cyclone for MOSAIC: PO sent 60 days ago, 54-day lead time."""
    name = f"KINGCERA-{uuid.uuid4().hex[:4]}"
    monkeypatch.setattr(autopilot, "vendor_email", lambda n: "sales@kingcera.test" if n == name else None)
    po = str(9000 + uuid.uuid4().int % 999)
    job = vendors.create({"vendor": name, "description": "MOSAIC - DILUTE MEDIA CYCLONE", "po_number": po,
                          "owner_email": REVIEWER, "ordered_on": (TODAY - timedelta(days=60)).isoformat(),
                          "quoted_lead_days": 54, **kw}, {"email": REVIEWER})
    with SessionLocal() as db:  # SQLite reuses ids; an earlier test's drafts must not count
        for r in db.query(AssistantActionDB):
            if (r.args or {}).get("_job_id") == job["id"]:
                db.delete(r)
        db.commit()
    return job


# ---------------------------------------------------------------------------
# 1. A customer emails an RFQ
# ---------------------------------------------------------------------------


def test_knauf_emails_an_rfq_it_is_logged_acknowledged_from_a_phone_and_the_rfq_moves_on(day, client):
    from app.services.assistant import quick

    mid = day["gmail"].add("Buyer <buyer@knauf-shasta.test>", "RFQ - D cone liner, 2 pcs",
                           "Please quote 2 pcs D cone liner per attached drawing. Need by end of January.")
    day["read_mail"]()
    log = _logged(mid)
    assert (log.category, log.outcome, log.needs_action) == ("quote_request", "RFQ_STAGED", False)
    assert "rfq" in log.tags and "drawing" in log.tags
    with SessionLocal() as db:
        rfq = db.query(RfqDB).filter(RfqDB.email_id == mid).one()
        assert rfq.complexity == "COMPLEX" and rfq.status == "NEW"     # a drawing: complex, more days to quote
    ack = [d for d in _drafts("rfq_acknowledge") if (d.args or {}).get("_rfq_id") == rfq.id]
    assert len(ack) == 1 and ack[0].args["to"] == "buyer@knauf-shasta.test" and rfq.number in ack[0].args["body"]
    # Karissa taps Send on her phone.
    t = quick.token(ack[0].id, REVIEWER)
    assert client.post(f"/api/v1/assistant/quick/{ack[0].id}", data={**t, "decision": "send"}).status_code == 200
    subject, to, body = day["sent"][-1]
    assert to == [TEST_INBOX] and "Received" in subject                 # test mode: it went to the pilot inbox only
    assert rfqs.get(rfq.id)["status"] == "ACKNOWLEDGED"
    # The same email read again tomorrow (still unread in Gmail): nothing new happens.
    day["read_mail"]()
    with SessionLocal() as db:
        assert db.query(RfqDB).filter(RfqDB.email_id == mid).count() == 1
    assert len([d for d in _drafts("rfq_acknowledge", status="DONE") if d.args.get("_rfq_id") == rfq.id]) == 1


def test_doug_forwards_a_customer_rfq_and_it_is_logged_as_the_customers(day):
    mid = day["gmail"].add("Doug Hamblin <doug@alliedweldingsystems.com>", "FW: RFQ Flint Line Pipe",
                           "---------- Forwarded message ---------\nFrom: OC-Starr\nPlease quote 40 ft flint line pipe.")
    day["read_mail"]()
    log = _logged(mid)
    assert log.direction == "internal" and log.category == "quote_request"


def test_internal_chatter_is_logged_but_raises_nothing(day):
    mid = day["gmail"].add("Shane <shane@alliedweldingsystems.com>", "Truck tomorrow 9am", "Crating the NOL-TEC parts.")
    day["read_mail"]()
    log = _logged(mid)
    assert (log.direction, log.category, log.needs_action) == ("internal", "internal", False)
    assert not _alerts(f"mail:{mid}")


# ---------------------------------------------------------------------------
# 2. Chasing a vendor, and reading the answer
# ---------------------------------------------------------------------------


def test_kingcera_is_followed_up_once_answers_and_the_new_date_sticks(day, monkeypatch, client):
    job = _kingcera_order(monkeypatch)
    j = {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]
    assert j["follow_up"]["due"]                                      # 60 days since PO > 54-day lead time
    autopilot.run("vendor_chaser")
    autopilot.run("vendor_chaser")                                    # the hourly timer comes round again
    chasers = _drafts("vendor_chaser", job_id=job["id"])
    assert len(chasers) == 1 and f"Allied PO {job['po_number']}" in chasers[0].args["subject"]
    # Karissa reviews in the Inbox and sends it with the batch action.
    out = client.post("/api/v1/assistant/actions/batch", json={"ids": [chasers[0].id], "confirm": True}).json()
    assert out["results"][0]["status"] == "DONE" and day["sent"][-1][1] == [TEST_INBOX]
    after = {x["id"]: x for x in vendors.overview()["jobs"]}[job["id"]]["follow_up"]
    assert after["started"] and not after["due"] and after["last"] == TODAY.isoformat()
    autopilot.run("vendor_chaser")
    assert len(_drafts("vendor_chaser", job_id=job["id"])) == 0      # no automatic second chaser
    # KINGCERA answers.
    ship = TODAY + timedelta(days=12)
    mid = day["gmail"].add("Grace <sales@kingcera.test>", f"RE: Allied PO {job['po_number']}: ship date?",
                           f"Dear Allied, the cyclone will ship on {ship:%b} {ship.day}. Photos attached.\n\n> Could you confirm")
    day["read_mail"]()
    assert _logged(mid).category == "vendor_reply"
    update = [d for d in _drafts("vendor_reply", job_id=job["id"])]
    assert len(update) == 1 and update[0].args["promised_on"] == ship.isoformat()
    assert client.post(f"/api/v1/assistant/actions/{update[0].id}", json={"confirm": True}).json()["status"] == "DONE"
    with SessionLocal() as db:
        row = db.get(VendorJobDB, job["id"])
        assert row.promised_on == ship.isoformat() and row.acknowledged_on == TODAY.isoformat()
        assert vendors._person_promise_holds(row, (TODAY + timedelta(days=-6)).isoformat()) is False  # no board history: n/a
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


def test_a_vague_vendor_answer_changes_nothing_and_a_long_thread_alerts_once(day, monkeypatch):
    job = _kingcera_order(monkeypatch)
    root = day["gmail"].add("sales@kingcera.test", f"RE: Allied PO {job['po_number']}: ship date?",
                            "We are working hard, maybe end of month.")
    for _ in range(3):
        day["gmail"].add("sales@kingcera.test", f"RE: RE: Allied PO {job['po_number']}: ship date?",
                         "Still in production, will advise.", headers=f"In-Reply-To: {root}\r\nReferences: {root}\r\n")
    day["read_mail"]()
    assert not _drafts("vendor_reply", job_id=job["id"])
    with SessionLocal() as db:
        assert db.get(VendorJobDB, job["id"]).promised_on is None
    assert len(_alerts(f"autopilot:vendor-reply:{job['id']}:")) == 4   # one note per reply, quoting the vendor
    with SessionLocal() as db:
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


# ---------------------------------------------------------------------------
# 3. Customers writing in
# ---------------------------------------------------------------------------


def test_oc_irving_complains_and_leadership_hears_once_however_long_the_thread(day, monkeypatch):
    monkeypatch.setattr(settings, "LEADERSHIP_EMAILS", "boss@alliedweldingsystems.com")
    root = day["gmail"].add("qa@oc-irving.test", "Overlay tab deflectors cracked", "Two deflectors cracked after a week.")
    for _ in range(4):
        day["gmail"].add("qa@oc-irving.test", "RE: Overlay tab deflectors cracked", "Any update? Line is affected.",
                         headers=f"References: {root}\r\n")
    day["read_mail"]()
    alerts = _alerts(f"mail:{root}:")
    assert len(alerts) == 1 and alerts[0].severity == "critical" and "boss@alliedweldingsystems.com" in alerts[0].recipients


def test_a_where_is_my_order_email_alerts_and_a_supplier_invoice_waits_for_the_summary(day):
    q = day["gmail"].add("buyer@ct-portland.test", "Where is PO 4520479478?", "When will it ship?")
    inv = day["gmail"].add("ar@kwsmfg.test", "Invoice 7781 - PO 9277", "Please find our invoice attached.")
    day["read_mail"]()
    assert len(_alerts(f"mail:{q}:")) == 1 and not _alerts(f"mail:{inv}:")
    assert _logged(inv).needs_action and any("Invoice 7781" in line for line in mail_log.for_summary())
    from app.services.coordination import schedule

    assert "Emails waiting on a person" in schedule.build_summary()["text"]


def test_a_morning_of_newsletters_and_out_of_office_replies_costs_nothing(day, monkeypatch):
    calls = []
    monkeypatch.setattr(features, "triage_email", lambda *a, **k: calls.append(a) or _sort(*a, **k))
    ids = [day["gmail"].add("news@steel-weekly.test", f"Steel prices week {i}", "...",
                            headers="List-Unsubscribe: <mailto:u@x>\r\n") for i in range(20)]
    ids += [day["gmail"].add("buyer@knauf.test", "Automatic reply: Re: liners", "Out of office until Monday",
                             headers="Auto-Submitted: auto-replied\r\n") for _ in range(5)]
    ids += [day["gmail"].add("notifications@monday.com", "Karissa mentioned you", "...") for _ in range(5)]
    day["read_mail"]()
    assert calls == []                                                 # not one AI read
    assert all(_logged(i).category == "noise" for i in ids)
    assert not any(_alerts(f"mail:{i}:") for i in ids)
    assert not ({m["message_id"] for m in mail_log.list_mail(days=1, limit=1000)["mail"]} & set(ids))


# ---------------------------------------------------------------------------
# 4. Board changes
# ---------------------------------------------------------------------------


def test_an_order_moving_to_expecting_pics_drafts_the_photo_request_from_the_starter(day, monkeypatch):
    from app.services.coordination import recipes

    job = _kingcera_order(monkeypatch)
    recipes.add_starter("orders-ask-photos", {}, {"email": REVIEWER, "role": "MANAGER"})
    events.emit("order.status_changed", {"job": {**job, "monday_item_id": None}, "from": "FABRICATION", "to": "EXPECTING PICS"})
    events.emit("order.status_changed", {"job": {**job, "monday_item_id": None}, "from": "FABRICATION", "to": "EXPECTING PICS"})
    asks = [d for d in _drafts() if (d.args or {}).get("_play", "").startswith("recipe_")
            and d.args.get("_item") == str(job["id"]) and d.args.get("to") == "sales@kingcera.test"]
    assert len(asks) == 1 and asks[0].args["to"] == "sales@kingcera.test"   # the replay drafted nothing more
    with SessionLocal() as db:
        from app.models.db_models import AutomationRecipeDB

        db.query(AutomationRecipeDB).delete()
        db.query(VendorJobDB).filter(VendorJobDB.id == job["id"]).delete()
        db.commit()


def test_an_order_marked_shipped_starts_tracking_its_shipment(day, monkeypatch):
    from app.services.coordination import shipments

    job = _kingcera_order(monkeypatch)
    events.emit("order.status_changed", {"job": job, "from": "FABRICATION", "to": "SHIPPED"})
    ref = f"PO {job['po_number']}"
    assert [s for s in shipments.list_shipments(include_finished=True) if s.get("reference") == ref]
    events.emit("order.status_changed", {"job": job, "from": "FABRICATION", "to": "SHIPPED"})
    assert len([s for s in shipments.list_shipments(include_finished=True) if s.get("reference") == ref]) == 1


def test_the_customer_form_logs_an_rfq_and_drafts_the_receipt(day, client, monkeypatch):
    monkeypatch.setattr(settings, "RFQ_FORM_TOKEN", "form-secret")
    from app.api.v1.endpoints import coordination

    monkeypatch.setattr(coordination, "_form_hits", {})
    r = client.post("/api/v1/rfqs/form", headers={"X-Form-Token": "form-secret", "X-Forwarded-For": "198.51.100.7"},
                    json={"company": "Hecla Mining", "email": "buyer@hecla.test", "subject": "Spool piece",
                          "details": "6 in spool, 300# flanges", "quantity": "4"})
    number = r.json()["reference"]
    rfq = rfqs.get(number)
    assert rfq["source"] == "form" and [d for d in _drafts("rfq_acknowledge") if d.args.get("_rfq_id") == rfq["id"]]

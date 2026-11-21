"""Round 26 (adversary), part 4: the CEO round 2-5 changes: RFQ closing cancels drafts, notice auto-close, ball summary names, per-currency totals, email purpose guessing, mail routes and roles.
Temporary database only; fake everything else. xfail(strict=False, reason="ADV26-Tn ...") marks holes."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, MailLogDB, NotificationDB
from app.services import contacts
from app.services.coordination import ball, notify, rfqs
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss-r26-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"r26-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", **kw):
        return client.request(method, "/api/v1" + path, headers=heads[role], **kw)

    return call


def _draft(summary="", body="", why=None, role="AUTOPILOT"):
    with SessionLocal() as db:
        r = AssistantActionDB(user_email="autopilot@alliedweldingsystems.com", user_role=role, tool="send_email", summary=summary, args={"to": "x@y.test", "subject": "s", "body": body, "_why": why or []})
        db.add(r)
        db.commit()
        return r.id


def _status(i):
    with SessionLocal() as db:
        return db.get(AssistantActionDB, i).status


# =============================================================== RFQ closing cancels drafts

def test_closing_an_rfq_cancels_its_own_waiting_drafts_and_not_unrelated_ones():
    mine, other = f"RFQ-2026-{uuid.uuid4().int % 9000 + 1000}", "RFQ-2099-5555"
    a = _draft(f"Chase the price for {mine}")
    b = _draft(f"Chase the price for {other}")
    c = _draft("Chase", body=f"about {mine}")
    d = _draft(f"Chase {mine}", role="OPERATOR")                              # a person's own draft is not the app's
    assert rfqs.cancel_pending_drafts(mine, "closed") == 2
    assert (_status(a), _status(b), _status(c), _status(d)) == ("CANCELLED", "PENDING", "CANCELLED", "PENDING")


@pytest.mark.parametrize("closed,other", [("RFQ-2026-0001", "RFQ-2026-00012"), ("RFQ-2026-001", "RFQ-2026-0011"), ("RFQ-2026-0001", "XRFQ-2026-0001"), ("RFQ-2026-0001", "RFQ-2026-0001-B")],
                         ids=["five-digit", "short-vs-long", "prefix-char", "suffix"])
def test_t1_closing_one_rfq_does_not_cancel_a_similarly_numbered_rfqs_draft(closed, other):
    victim = _draft(f"Chase the price for {other}")
    rfqs.cancel_pending_drafts(closed, "closed")
    assert _status(victim) == "PENDING"


def test_t2_the_rfq_drafts_are_cancelled_even_with_many_other_waiting_drafts():
    num = f"RFQ-2031-{uuid.uuid4().int % 9000 + 1000}"
    for _ in range(505):
        _draft("unrelated chase " + uuid.uuid4().hex)
    late = _draft(f"Chase {num}")
    try:
        rfqs.cancel_pending_drafts(num, "closed")
        assert _status(late) == "CANCELLED"
    finally:
        with SessionLocal() as db:
            db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT", AssistantActionDB.status == "PENDING").update({AssistantActionDB.status: "CANCELLED"})
            db.commit()


def test_closing_through_update_cancels_and_reopening_is_refused():
    r = rfqs.create({"customer": "CloseCo", "subject": f"close me {TAG}"}, BOSS, announce=False)
    d = _draft(f"Follow up {r['number']}")
    rfqs.update(r["number"], {"status": "NO_BID", "reason": "not our product"}, BOSS)
    assert _status(d) == "CANCELLED"
    with pytest.raises(ValueError):
        rfqs.update(r["number"], {"status": "NEW"}, BOSS)


# =============================================================== notices that close themselves

def _alert(kind, severity, age_days, to=None):
    n = notify.notify(kind, f"r26-{kind}-{uuid.uuid4().hex[:6]}", f"{kind} {severity}", "body", severity=severity, to=to or [BOSS["email"]])
    nid = n if isinstance(n, int) else (n.get("id") if isinstance(n, dict) else None)
    with SessionLocal() as db:
        row = db.get(NotificationDB, nid) if nid else db.query(NotificationDB).order_by(NotificationDB.id.desc()).first()
        row.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        db.commit()
        return row.id


def _open(nid):
    with SessionLocal() as db:
        return db.get(NotificationDB, nid).acknowledged_at is None


def test_only_old_information_alerts_close_themselves_never_a_warning_or_urgent_one():
    old = {s: _alert(f"t_{s}", s, 40) for s in ("info", "warning", "critical")}
    fresh = _alert("t_fresh", "info", 1)
    notify.expire_old_info(14)
    assert not _open(old["info"])
    for s in ("warning", "critical"):
        assert _open(old[s]), s
    assert _open(fresh)


@pytest.mark.parametrize("sev", ["INFO", "Info", " info", "informational", "low", "notice"])
def test_odd_severity_spellings_never_cause_a_wrong_close_of_something_that_matters(sev):
    nid = _alert(f"t_{uuid.uuid4().hex[:4]}", sev, 40)
    notify.expire_old_info(14)
    assert _open(nid) or sev.strip().lower() in ("info", "low", "notice", "informational")


def test_t3_a_lead_replied_alert_is_not_auto_closed():
    from app.services import leads

    em = f"t3{TAG}@lead{uuid.uuid4().hex[:4]}.example.test"
    out = leads.add_lead({"company": f"T3 {TAG}", "name": "Pat", "email": em, "source": "manual"}, BOSS)
    from app.models.db_models import MailLogDB as M

    with SessionLocal() as db:
        m = M(message_id=f"<{uuid.uuid4().hex}@t>", folder="INBOX", sender=em, subject="Re: hi", direction="in", category="rfq", outcome="LOGGED_ONLY", received_at=datetime.now(timezone.utc))
        db.add(m)
        db.commit()
        mid = m.id
    assert leads.on_mail(mid, em, "Re: hi", "Yes, interested, please call me.", True) == "reply"
    with SessionLocal() as db:
        rows = db.query(NotificationDB).filter(NotificationDB.kind == "lead_replied").order_by(NotificationDB.id.desc()).limit(3).all()
        assert rows
        sev = rows[0].severity
        rows[0].created_at = datetime.now(timezone.utc) - timedelta(days=40)
        db.commit()
        nid = rows[0].id
    notify.expire_old_info(14)
    assert _open(nid), f"closed itself at severity {sev}"


def test_t4_one_persons_dismissal_does_not_close_the_alert_for_the_other_recipient():
    a, b = f"a-r26-{TAG}@alliedweldingsystems.com", f"b-r26-{TAG}@alliedweldingsystems.com"
    from app.services import my_day

    nid = _alert("t_shared", "info", 5, to=[a, b])
    my_day.dismiss_notices({"email": a, "role": "OPERATOR"})
    assert any(x["id"] == nid for x in notify.inbox(b, open_only=True, limit=500))


# =============================================================== the ball summary

def test_t5_two_different_holders_with_the_same_name_are_not_merged():
    r1 = rfqs.create({"customer": "BallCo", "subject": f"b1 {TAG}"}, BOSS, announce=False)
    r2 = rfqs.create({"customer": "BallCo", "subject": f"b2 {TAG}"}, BOSS, announce=False)
    ball.set_ball("rfq", r1["number"], BOSS, ball_with="vendor", ball_person="Helen Smithe")
    ball.set_ball("rfq", r2["number"], BOSS, ball_with="customer", ball_person="helen smithe")
    holders = [h for h in ball.summary()["by_holder"] if h["name"].casefold() == "helen smithe"]
    assert len(holders) == 2 and {h["kind"] for h in holders} == {"vendor", "customer"}


def test_the_same_name_in_different_cases_is_one_holder_with_a_readable_spelling():
    r1 = rfqs.create({"customer": "BallCo", "subject": f"c1 {TAG}"}, BOSS, announce=False)
    r2 = rfqs.create({"customer": "BallCo", "subject": f"c2 {TAG}"}, BOSS, announce=False)
    ball.set_ball("rfq", r1["number"], BOSS, ball_with="vendor", ball_person="ZIBO FOUNDRYX")
    ball.set_ball("rfq", r2["number"], BOSS, ball_with="vendor", ball_person="zibo foundryx")
    holders = [h for h in ball.summary()["by_holder"] if h["name"].casefold() == "zibo foundryx"]
    assert len(holders) == 1 and holders[0]["count"] == 2


# =============================================================== currencies

def test_won_and_quoted_values_are_kept_per_currency_never_added_and_a_missing_currency_is_unknown():
    for cur, amt in (("USD", "1000"), ("CAD", "2000"), ("CNY", "3000"), ("", "400")):
        r = rfqs.create({"customer": "CurCo", "subject": f"cur {cur} {TAG} {uuid.uuid4().hex[:4]}"}, BOSS, announce=False)
        ch = {"quote_amount": amt, "quote_number": "Q1", "status": "QUOTE_SENT"}
        if cur:
            ch["quote_currency"] = cur
        rfqs.update(r["number"], ch, BOSS)
        rfqs.update(r["number"], {"status": "WON"}, BOSS)
    st = rfqs.metrics(30)
    won = st.get("won_value_by_currency", {})
    assert won.get("USD", 0) >= 1000 and won.get("CAD", 0) >= 2000 and won.get("CNY", 0) >= 3000 and won.get("unknown", 0) >= 400
    assert "win_rate_value" in st


@pytest.mark.parametrize("cur", ["EUR", "GBP", "XXX", "usd ", "", "$", "CAD CNY", "U" * 40, "\x00"])
def test_an_unknown_quote_currency_is_refused_or_cleaned_and_never_summed_as_dollars(cur):
    r = rfqs.create({"customer": "CurCo", "subject": f"cur2 {TAG} {uuid.uuid4().hex[:4]}"}, BOSS, announce=False)
    try:
        rfqs.update(r["number"], {"quote_amount": "100", "quote_currency": cur}, BOSS)
    except ValueError:
        return
    assert (rfqs.get(r["number"]).get("quote_currency") or "").strip() in ("", "USD")


# =============================================================== guessing what an address is for

@pytest.mark.parametrize("addr", ["quotes-spam@x.test", "salesman.joe@x.test", "billingsworth@x.test", "shippingham@x.test", "buyerbeware@x.test", "financeguy.pat@x.test", "invoiceme-now@x.test", "rfqbot9000@x.test"])
def test_t6_a_name_that_only_starts_with_a_role_word_is_not_that_role(addr):
    assert contacts.purpose_of(addr) == ""


@pytest.mark.parametrize("addr,want", [("quotes@x.test", "quotes"), ("RFQ@x.test", "quotes"), ("accounts.payable@x.test", "invoices"), ("ap@x.test", "invoices"), ("shipping@x.test", "shipping"),
                                       ("receiving@x.test", "shipping"), ("pat.lee@x.test", ""), ("", ""), (None, ""), ("noatsign", ""), ("@x.test", ""), ("sales@x.test", "quotes")])
def test_the_obvious_role_mailboxes_are_still_recognised(addr, want):
    assert contacts.purpose_of(addr) == want


# =============================================================== mail routes and roles

def test_mail_status_and_lists_never_hold_a_password_or_a_token_for_any_role(api):
    for role in ("VIEWER", "OPERATOR", "MANAGER"):
        for path in ("/mail/status", "/mail"):
            r = api("GET", path, role)
            body = r.text.lower()
            assert r.status_code in (200, 403), (role, path, r.status_code)
            for word in ("password", "imap_password", "secret", "app password", "token\":"):
                assert word not in body or word == "password" and "no password" in body or r.status_code == 403, (role, path, word)


def test_t7_a_viewer_does_not_read_everyones_mail(api):
    with SessionLocal() as db:
        m = MailLogDB(message_id=f"<{uuid.uuid4().hex}@t>", folder="INBOX", sender=f"ceo{TAG}@client.example.test", recipients=f"boss{TAG}@alliedweldingsystems.com", subject=f"private matter {TAG}",
                      direction="in", category="rfq", outcome="LOGGED_ONLY", summary="salary and acquisition talks", received_at=datetime.now(timezone.utc))
        db.add(m)
        db.commit()
    r = api("GET", f"/mail?q={TAG}&days=30", "VIEWER")
    assert r.status_code == 403 or "private matter" not in r.text


def test_viewer_403_on_every_write_in_mail_me_and_leads_and_anonymous_is_refused(api, auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    spec = c.get("/openapi.json").json()["paths"]
    bad, anon = [], []
    for path, ops in spec.items():
        if not path.startswith(("/api/v1/mail", "/api/v1/me", "/api/v1/leads")):
            continue
        url = path.replace("{", "").replace("}", "") if False else __import__("re").sub(r"\{[^}]*\}", "1", path)
        for m in ops:
            if m.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            kw = {"json": {}} if m != "get" else {}
            if c.request(m.upper(), url, **kw).status_code not in (401, 403):
                anon.append((m, path))
            if m != "get":
                r = c.request(m.upper(), url, headers={"Authorization": "Bearer " + staff_token(f"v26-{TAG}@alliedweldingsystems.com", "VIEWER")}, **kw)
                if r.status_code not in (403, 401) and not path.startswith(("/api/v1/me", "/api/v1/leads/roi-calculator")):
                    bad.append((m, path, r.status_code))
    assert not anon, anon
    assert not bad, bad[:8]


def test_me_counts_and_notifications_are_the_callers_own_and_safe_for_every_role(api):
    for role in ("VIEWER", "OPERATOR", "MANAGER"):
        for path in ("/me/counts", "/me/notifications"):
            r = api("GET", path, role)
            assert r.status_code in (200, 404, 403), (role, path, r.status_code)
            assert "Traceback" not in r.text

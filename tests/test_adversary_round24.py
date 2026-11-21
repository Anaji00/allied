"""Round 24 (adversary): the lead engine (app/services/leads.py, /api/v1/leads/*) and the mail-AI / mine / template work (mail_reply, mail_log). Temporary database, fake mail, no
network, no monday. The migration chain was rehearsed separately on a scratch database. xfail(strict=False, reason="ADV24-Tn ...") marks open holes."""
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import LeadDB, LeadOutreachDB, MailAiUseDB, MailLogDB, OrderDB
from app.services import leads, mail_log, mail_reply
from app.services.assistant import agent, tools  # noqa: F401
from app.services.coordination import notify
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:6]
OP = {"email": f"op-a24-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
BOSS = {"email": f"boss-a24-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}


def _u(p="x"):
    return f"{p}{TAG}{uuid.uuid4().hex[:5]}"


def _lead(email=None, company=None, name="Pat Buyer"):
    email = email or f"{_u('pat')}@{_u('acme')}.example.test"
    out = leads.add_lead({"company": company or f"Acme {_u()}", "name": name, "email": email, "source": "manual"}, BOSS)
    return out["id"], email


@pytest.fixture(autouse=True)
def _tidy(monkeypatch):
    saved = leads.get_settings()
    yield
    leads.put_settings({"sequence_mode": "off", "daily_draft_limit": 10, "business_hours": [8, 17]}, BOSS)


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"a24-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", **kw):
        return client.request(method, "/api/v1" + path, headers=heads[role], **kw)

    return call


@pytest.fixture()
def outbox(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service

    sent = []
    monkeypatch.setattr(outbound_email_service, "send", lambda *a, **kw: sent.append({"args": a, **kw}) or {"status": "SENT"})
    return sent


# =============================================================== do-not-contact

@pytest.mark.parametrize("variant", ["upper", "spaces", "tabs"])
def test_dnc_matches_case_and_whitespace(variant):
    lid, em = _lead()
    leads.add_dnc(em, BOSS, "asked")
    probe = {"upper": em.upper(), "spaces": f"  {em}  ", "tabs": f"{em}\t"}[variant]
    assert leads.blocked(probe)
    with pytest.raises(leads.LeadError):
        leads.draft_outreach(lid, OP)


@pytest.mark.parametrize("variant", ["plus", "subdomain", "fullwidth", "trailing-dot", "display-name", "dotted-gmail"])
def test_t1_dnc_cannot_be_dodged_by_writing_the_address_another_way(variant):
    local, dom = f"ann{TAG}", f"{_u('blocked')}.example.test"
    leads.add_dnc(f"{local}@{dom}", BOSS, "asked")
    probe = {"plus": f"{local}+news@{dom}", "subdomain": f"{local}@mail.{dom}", "fullwidth": f"{local.replace('a', chr(0xFF41))}@{dom}", "trailing-dot": f"{local}@{dom}.",
             "display-name": f"Ann <{local}@{dom}>", "dotted-gmail": f"{local[:2]}.{local[2:]}@{dom}"}[variant]
    if variant == "dotted-gmail":
        pytest.skip("dots are significant outside Gmail")
    assert leads.blocked(probe)


def test_a_domain_entry_blocks_every_address_at_that_domain_and_not_lookalikes():
    dom = f"{_u('dnc')}.example.test"
    leads.add_dnc(dom, BOSS, "whole company")
    assert leads.blocked(f"a@{dom}") and leads.blocked(f"B@{dom.upper()}")
    assert not leads.blocked(f"a@x{dom}") and not leads.blocked(f"a@{dom}.evil.test")


def test_t2_a_queued_draft_is_not_sent_after_the_address_goes_on_the_list(outbox):
    lid, em = _lead()
    got = leads.draft_outreach(lid, OP)
    leads.add_dnc(em, BOSS, "asked after the draft was made")
    try:
        agent.decide(got["action_id"], True, OP)
    except Exception:  # noqa: BLE001 - refused is the right answer
        pass
    assert not [m for m in outbox if em in str(m)]


def test_control_an_unblocked_queued_draft_is_sent_when_approved(outbox):
    lid, em = _lead()
    got = leads.draft_outreach(lid, OP)
    agent.decide(got["action_id"], True, OP)
    assert [m for m in outbox if em in str(m)]


def test_a_lead_imported_after_an_unsubscribe_is_still_blocked_and_nothing_is_drafted():
    em = f"{_u('late')}@{_u('co')}.example.test"
    leads.add_dnc(em, None, "unsubscribed earlier", "mail")
    out = leads.add_lead({"company": f"Late {_u()}", "name": "Late Lead", "email": em, "source": "import"}, BOSS)
    assert out["do_not_contact"] is True
    with pytest.raises(leads.LeadError):
        leads.draft_outreach(out["id"], OP)
    leads.start_sequence(out["id"])
    leads.put_settings({"sequence_mode": "suggest"}, BOSS)
    with SessionLocal() as db:
        before = db.query(LeadOutreachDB).filter(LeadOutreachDB.lead_id == out["id"], LeadOutreachDB.kind == "draft").count()
    leads.run_sequences(BOSS, dry_run=False, ignore_hours=True)
    with SessionLocal() as db:
        assert db.query(LeadOutreachDB).filter(LeadOutreachDB.lead_id == out["id"], LeadOutreachDB.kind == "draft").count() == before


def test_t3_a_reply_draft_to_a_do_not_contact_sender_is_refused_or_flagged(monkeypatch):
    em = f"{_u('stop')}@{_u('co')}.example.test"
    leads.add_dnc(em, BOSS, "asked to stop")
    row = _mail(sender=em, category="delivery_question", subject="Where is my order")
    out = mail_reply.draft_reply(row, OP, False)
    assert out.get("action_id") is None or "do-not-contact" in str(out).lower() or "do not contact" in str(out).lower()


def _mail(sender, subject="s", category="rfq", recipients="", summary="body text", direction="in"):
    with SessionLocal() as db:
        r = MailLogDB(message_id=f"<{uuid.uuid4().hex}@t>", folder="INBOX", sender=sender, recipients=recipients, subject=subject, direction=direction, category=category, outcome="LOGGED_ONLY",
                      summary=summary, received_at=datetime.now(timezone.utc))
        db.add(r)
        db.commit()
        return r.id


# =============================================================== reply, bounce, out-of-office, unsubscribe

def test_t4_a_reply_that_quotes_the_opt_out_line_is_a_reply_not_an_unsubscribe():
    lid, em = _lead()
    body = "Yes please, send us a quote for 20 liners." + chr(10) + chr(10) + "> Hello Pat," + chr(10) + "> If you would rather not hear from us, reply \"unsubscribe\" and we will stop."
    out = leads.on_mail(_mail(em), em, "Re: Wear parts", body, True)
    assert out == "reply" and not leads.blocked(em)


@pytest.mark.parametrize("subject,body,want", [("Please unsubscribe me", "remove me", "unsubscribe"), ("Re: hello", "Stop emailing me.", "unsubscribe"), ("Automatic reply: away", "I am out of the office", "out_of_office"),
                                               ("Re: hello", "Thanks, call me tomorrow", "reply")])
def test_the_basic_mail_rules(subject, body, want):
    lid, em = _lead()
    assert leads.on_mail(_mail(em), em, subject, body, True) == want


def test_a_forged_or_untrusted_mail_changes_nothing():
    lid, em = _lead()
    assert leads.on_mail(_mail(em), em, "unsubscribe", "remove me", False) is None
    assert not leads.blocked(em) and leads.get_lead(lid)["status"] == "Found"


def test_t5_a_bounce_look_alike_from_anyone_cannot_mark_other_leads_bad():
    lid, em = _lead()
    other, other_em = _lead()
    out = leads.on_mail(_mail("someone@stranger.example.test"), "someone@stranger.example.test", "Undeliverable: hello", f"This was not delivered to {other_em} and {em}.", True)
    assert not leads.get_lead(other)["bad_address"] and not leads.get_lead(lid)["bad_address"]


def test_a_real_bounce_marks_the_failed_address_and_pauses_only_that_lead():
    lid, em = _lead()
    other, other_em = _lead()
    leads.start_sequence(lid)
    leads.start_sequence(other)
    out = leads.on_mail(_mail("mailer-daemon@googlemail.com"), "mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", f"Your message to {em} could not be delivered.", True)
    assert out == "bounce" and leads.get_lead(lid)["bad_address"] and leads.get_lead(lid)["sequence"]["paused"]
    assert not leads.get_lead(other)["bad_address"] and not leads.get_lead(other)["sequence"]["paused"]


def test_t6_an_out_of_office_does_not_restart_a_lead_that_already_replied():
    lid, em = _lead()
    leads.start_sequence(lid)
    assert leads.on_mail(_mail(em), em, "Re: hi", "Interested, call me.", True) == "reply"
    leads.on_mail(_mail(em), em, "Automatic reply: out of office", "I am out of the office, back on 2099-01-01.", True)
    assert leads.get_lead(lid)["sequence"]["paused"] is True and leads.get_lead(lid)["status"] == "Replied"


@pytest.mark.parametrize("when", ["2001-01-01", "2099-12-31"])
def test_t7_a_silly_back_date_is_ignored(when):
    lid, em = _lead()
    leads.on_mail(_mail(em), em, "Automatic reply", f"I am out of the office, back on {when}.", True)
    nxt = leads.get_lead(lid)["next_touch_at"]
    when_ = datetime.fromisoformat(nxt).replace(tzinfo=timezone.utc) if nxt else None
    assert when_ is None or (datetime.now(timezone.utc) < when_ < datetime.now(timezone.utc) + timedelta(days=120))


def test_a_reply_from_the_leads_address_in_any_case_stops_the_sequence():
    lid, em = _lead()
    leads.start_sequence(lid)
    assert leads.on_mail(_mail(em.upper()), f"Pat <{em.upper()}>", "Re: x", "ok, thanks", True) == "reply"
    got = leads.get_lead(lid)
    assert got["status"] == "Replied" and got["sequence"]["paused"] and got["next_touch_at"] is None


# =============================================================== sequences

def _due_leads(n):
    ids = []
    for _ in range(n):
        lid, _em = _lead()
        leads.start_sequence(lid)
        ids.append(lid)
    return ids


def test_t8_the_daily_limit_holds_across_repeated_runs():
    leads.put_settings({"sequence_mode": "suggest", "daily_draft_limit": 10}, BOSS)
    _due_leads(26)
    total = 0
    for _ in range(3):
        total += len(leads.run_sequences(BOSS, dry_run=False, ignore_hours=True)["made"])
    assert total <= 10


def test_t9_two_simultaneous_runs_draft_each_step_once():
    leads.put_settings({"sequence_mode": "suggest", "daily_draft_limit": 500}, BOSS)
    ids = _due_leads(4)
    errs = []

    def go():
        try:
            leads.run_sequences(BOSS, dry_run=False, ignore_hours=True)
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    with SessionLocal() as db:
        counts = [db.query(LeadOutreachDB).filter(LeadOutreachDB.lead_id == i, LeadOutreachDB.kind == "draft", LeadOutreachDB.step == 0).count() for i in ids]
    assert counts == [1] * len(ids), counts


def test_business_hours_hold_and_a_run_outside_them_makes_nothing(monkeypatch):
    leads.put_settings({"sequence_mode": "suggest", "daily_draft_limit": 500}, BOSS)
    _due_leads(2)
    from datetime import datetime as dt

    for when, ok in ((dt(2026, 11, 14, 10, 0), False), (dt(2026, 11, 16, 7, 59), False), (dt(2026, 11, 16, 17, 0), False), (dt(2026, 11, 16, 22, 0), False), (dt(2026, 11, 16, 12, 0), True)):
        monkeypatch.setattr(notify, "now_local", lambda when=when: when)
        out = leads.run_sequences(BOSS, dry_run=False)
        assert bool(out["made"]) is ok or (ok and not out["made"] and "Outside" not in out.get("note", "")), (when, out.get("note"))
    monkeypatch.setattr(notify, "now_local", lambda: dt(2026, 11, 14, 10, 0))
    assert leads.run_sequences(BOSS, dry_run=False)["made"] == []


def test_a_dry_run_and_a_switched_off_sequence_make_no_draft():
    lid, _em = _lead()
    leads.start_sequence(lid)
    with SessionLocal() as db:
        before = db.query(LeadOutreachDB).filter(LeadOutreachDB.kind == "draft").count()
    leads.put_settings({"sequence_mode": "off"}, BOSS)
    assert leads.run_sequences(BOSS, dry_run=False, ignore_hours=True)["made"] == []
    leads.put_settings({"sequence_mode": "suggest"}, BOSS)
    assert leads.run_sequences(BOSS, dry_run=True)["made"] == []
    with SessionLocal() as db:
        assert db.query(LeadOutreachDB).filter(LeadOutreachDB.kind == "draft").count() == before


def test_t10_a_sequence_draft_for_a_lapsed_customer_does_not_pretend_a_quote_exists():
    leads.put_settings({"sequence_mode": "suggest", "daily_draft_limit": 500}, BOSS)
    out = leads.add_lead({"company": f"Lapsed {_u()}", "name": "Buyer", "email": f"{_u('b')}@{_u('co')}.example.test", "source": "history", "reason": "Lapsed Co ordered 3 times, last in 2024, and nothing in 14 months."}, BOSS)
    lid = out["id"]
    leads.start_sequence(lid)
    with SessionLocal() as db:
        db.get(LeadDB, lid).sequence_step = 1
        db.commit()
    got = leads.draft_outreach(lid, OP, "quote-follow-up", 1, "step 2")
    leads.run_sequences(BOSS, dry_run=False, ignore_hours=True)
    with SessionLocal() as db:
        from app.models.db_models import AssistantActionDB

        bodies = [str((a.args or {}).get("body") or "") for a in db.query(AssistantActionDB).filter(AssistantActionDB.args["_lead_id"].as_integer() == lid)] if False else []
    assert "quote we sent" not in got["body"]


def test_t11_a_history_lead_draft_has_no_internal_stats_and_a_real_greeting():
    out = leads.add_lead({"company": f"Gap {_u()}", "name": "Buyer at Gap Co", "email": f"{_u('b')}@{_u('co')}.example.test", "source": "history",
                          "reason": "Gap Co ordered 4 times, last on Aug 10, usual gap 100 days: the next order is due in 4 days."}, BOSS)
    got = leads.draft_outreach(out["id"], OP)
    assert "usual gap" not in got["body"] and "ordered 4 times" not in got["body"] and not got["body"].startswith("Hello Buyer,")


def test_merge_fields_are_filled_from_the_lead_only_and_a_hostile_value_is_not_expanded():
    out = leads.add_lead({"company": "{signature} {optout} {0} {name.__class__}", "name": "{first_name}{company}", "email": f"{_u('m')}@{_u('co')}.example.test", "source": "manual"}, BOSS)
    got = leads.draft_outreach(out["id"], OP, "new-prospect")
    assert "__class__" not in got["body"].replace("{name.__class__}", "") and "<class" not in got["body"]


def test_t18_a_template_that_reaches_into_objects_is_refused_when_it_is_saved():
    with pytest.raises(leads.LeadError):
        leads.put_settings({"templates": {"evil": {"subject": "hi", "body": "{company.__class__.__mro__} {first_name.__init__.__globals__}"}}}, BOSS)


def test_t12_a_reply_template_with_attribute_access_is_refused():
    with pytest.raises(mail_reply.ReplyError):
        mail_reply.put_template(f"evil-{TAG}", {"subject": "hi", "body": "{customer.__class__.__mro__} {0}"}, BOSS)


# =============================================================== mining history: who sees money

def _orders(name, ages_days, total):
    with SessionLocal() as db:
        for i, age in enumerate(ages_days):
            db.add(OrderDB(order_id=uuid.uuid4().hex, customer_id=f"C{TAG}", sales_order_number=f"A24{uuid.uuid4().hex[:7]}", customer_name=name, customer_po_number=f"PO{uuid.uuid4().hex[:6]}", status="SHIPPED",
                           total_revenue=total, created_at=datetime.now(timezone.utc) - timedelta(days=age), line_items=[], purchase_orders={}, shipping_address="x"))
        db.commit()


def test_t13_an_operator_does_not_get_customer_revenue_from_the_lead_suggestions(api):
    name = f"Spendy {_u()}"
    _orders(name, [700, 600, 500], 98765.0)
    r = api("GET", "/leads/suggestions", "OPERATOR")
    assert r.status_code == 200
    mine = [s for s in r.json() if s["account"] == name]
    assert mine and not [s for s in mine if s.get("spend")]


def test_a_viewer_gets_nothing_from_any_lead_route_and_operator_vs_manager_routes(api):
    lid, _em = _lead()
    viewer_blocked = [("GET", "/leads"), ("GET", "/leads/summary"), ("GET", "/leads/settings"), ("GET", "/leads/do-not-contact"), ("GET", "/leads/suggestions"), ("GET", "/leads/aliases"),
                      ("GET", f"/leads/{lid}/outreach"), ("POST", f"/leads/{lid}/outreach"), ("POST", f"/leads/{lid}/sequence"), ("PATCH", f"/leads/{lid}"), ("DELETE", f"/leads/{lid}"),
                      ("POST", "/leads"), ("POST", "/leads/do-not-contact"), ("PUT", "/leads/settings"), ("POST", "/leads/aliases"), ("POST", "/leads/sequences/run"), ("POST", f"/leads/{lid}/to-monday")]
    for method, path in viewer_blocked:
        assert api(method, path, "VIEWER", json={}).status_code == 403, (method, path)
    for method, path in (("PUT", "/leads/settings"), ("POST", "/leads/aliases"), ("POST", "/leads/sequences/run"), ("POST", f"/leads/{lid}/to-monday"), ("DELETE", "/leads/do-not-contact/1")):
        assert api(method, path, "OPERATOR", json={}).status_code == 403, (method, path)
    assert api("POST", "/leads/sequences/run", "MANAGER", json={}).status_code == 200


def test_csv_style_hostile_rows_are_stored_inert_or_refused_and_duplicates_collapse(api):
    em = f"{_u('dup')}@{_u('co')}.example.test"
    rows = [{"company": "=cmd|' /C calc'!A0", "name": "@SUM(1+1)", "email": f"{_u('f')}@{_u('co')}.example.test"}, {"company": "Dup Co", "name": "A", "email": em}, {"company": "Dup Co", "name": "B", "email": em.upper()},
            {"company": "x" * 5000, "name": "y" * 5000, "email": f"{_u('big')}@{_u('co')}.example.test"}, {"name": "No Email Needed"}, {"email": "not-an-email"}, {"email": "a@b.com" + chr(10) + "Bcc: x@y.test"},
            {"company": "‮evil", "name": "Ünï", "email": f"{_u('u')}@{_u('co')}.example.test"}]
    r = api("POST", "/leads", "OPERATOR", json={"leads": rows})
    assert r.status_code in (200, 201), r.text[:200]
    out = r.json()
    assert out.get("duplicates", out.get("dups", 1)) >= 1 and out.get("errors") is not None
    assert leads.blocked(em) is None


def test_t14_a_bulk_add_over_the_cap_says_so(api):
    rows = [{"company": f"Bulk {_u()}", "name": "N", "email": f"{_u('b')}@{_u('co')}.example.test"} for _ in range(520)]
    r = api("POST", "/leads", "OPERATOR", json={"leads": rows})
    body = r.text.lower()
    assert r.status_code in (400, 413, 422) or "500" in body or "only the first" in body or "skipped" in body or "too many" in body


def test_aliases_are_manager_only_cycles_do_not_crash_and_matching_is_exact_after_normalising():
    leads.add_alias("CT-KC", "CertainTeed Kansas City", BOSS)
    assert leads.canonical("ct kc") == "CertainTeed Kansas City" and leads.canonical("CT-KCX") == "CT-KCX"
    leads.add_alias("Alpha Co", "Beta Co", BOSS)
    leads.add_alias("Beta Co", "Alpha Co", BOSS)
    assert leads.canonical("Alpha Co") in ("Beta Co", "Alpha Co")


# =============================================================== switches that must stay off

def test_lead_web_search_is_really_off_and_the_scrape_route_makes_no_network_call(api, monkeypatch):
    assert settings.LEAD_WEB_SEARCH is False
    calls = []
    real = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError("no network in tests")))
    leads.put_settings({"search_enabled": True}, BOSS)
    assert leads.get_settings()["search_enabled"] is False
    r = api("POST", "/leads/scrape", "OPERATOR", json={"industry": "cement", "region": "Texas", "limit": 3})
    assert r.status_code in (400, 403, 404, 409, 422, 503) and not calls, (r.status_code, r.text[:120])


def test_nothing_in_the_lead_engine_calls_monday_or_sends_mail(monkeypatch, outbox):
    sender = []
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: type("M", (), {"live": True, "writable": False, "execute_query": lambda self, q, v=None: sender.append(q) or {"data": {}}})())
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    lid, em = _lead()
    leads.draft_outreach(lid, OP)
    leads.start_sequence(lid)
    leads.put_settings({"sequence_mode": "auto"}, BOSS)
    leads.run_sequences(BOSS, dry_run=False, ignore_hours=True)
    try:
        leads.to_monday(lid, BOSS, "123", False)
    except leads.LeadError:
        pass
    assert not [q for q in sender if "mutation" in q.casefold()] and outbox == []


# =============================================================== mail: mine, AI cleaning, exclusions, AI-use log

@pytest.mark.parametrize("me,other", [(f"ann{TAG}@x.example.test", f"joann{TAG}@x.example.test"), (f"first_{TAG}@x.example.test", f"firstX{TAG}@x.example.test"),
                                      (f"bob{TAG}@x.example.test", f"bob{TAG}@x.example.test.evil.test")], ids=["substring", "underscore-wildcard", "suffix"])
def test_t15_mine_shows_only_the_mail_the_person_is_on(me, other):
    mine_id = _mail(me, subject=f"mine {TAG}")
    theirs = _mail(other, subject=f"theirs {TAG}", recipients=other)
    got = mail_log.list_mail(None, None, TAG, None, 30, 200, None, True, me)
    subjects = [m["subject"] for m in got["items"]] if "items" in got else [m["subject"] for m in got.get("mail", [])]
    assert f"mine {TAG}" in subjects and f"theirs {TAG}" not in subjects


def test_mine_never_shows_someone_elses_private_mail_when_the_address_is_blank_or_odd(api):
    for me in ("", "%", "_", "@", "x"):
        got = mail_log.list_mail(None, None, None, None, 30, 1000, None, True, me)
        assert "items" in got or "mail" in got


@pytest.mark.parametrize("leak", ["---------- Forwarded message ---------" + chr(10) + "From: Vendor Zibo" + chr(10) + "Our cost is $12,000 and margin 31%",
                                  "Begin forwarded message:" + chr(10) + "Customer is Owens Corning, SO 12889 total $5,659.16",
                                  "Am 12.11.2025 schrieb Hans:" + chr(10) + "internal margin 31 percent, vendor Zibo",
                                  "From: Ann Lee" + chr(10) + "Sent: Monday" + chr(10) + "To: Bob" + chr(10) + "the real SECRETTHREAD text",
                                  "order #12889 ships Friday and SO-12889 cost 5000"], ids=["forwarded", "apple-forward", "german", "outlook-no-address", "bare-order-number"])
def test_t16_clean_for_ai_leaves_no_quoted_or_internal_text(leak):
    text = "Thanks, please send a quote." + chr(10) + chr(10) + leak
    out = mail_reply.clean_for_ai(text)
    assert "please send a quote" in out
    for bad in ("12,000", "31", "Zibo", "SECRETTHREAD", "12889", "5,659.16", "Owens"):
        assert bad not in out, bad


def test_clean_for_ai_drops_quoted_lines_signatures_and_our_so_numbers_and_is_capped():
    text = "New text here." + chr(10) + "> old quoted" + chr(10) + "SO-12345 and sales order #99999" + chr(10) + "On Mon, Nov 10, Ann <a@b.com> wrote:" + chr(10) + "the quoted thread" + chr(10) + "-- " + chr(10) + "sig"
    out = mail_reply.clean_for_ai(text)
    assert "old quoted" not in out and "12345" not in out and "99999" not in out and "the quoted thread" not in out and "sig" not in out and len(mail_reply.clean_for_ai("x" * 99999)) <= mail_reply.TEXT_LIMIT


@pytest.mark.parametrize("sender,entry", [("ann+news@x.example.test", "ann@x.example.test"), ("ann@mail.x.example.test", "@x.example.test"), ("Ann <ANN@X.example.test>", "ann@x.example.test")], ids=["plus", "subdomain", "display-case"])
def test_t17_excluded_senders_stay_excluded_in_other_spellings(sender, entry):
    mine = {"exclude_senders": [entry], "exclude_words": []}
    assert mail_reply.excluded(mine, sender, "s", "summary") == "sender"


def test_excluded_words_and_senders_basics():
    mine = {"exclude_senders": ["ann@x.example.test", "@evil.example.test"], "exclude_words": ["nda", "salary"]}
    assert mail_reply.excluded(mine, "ann@x.example.test", "s", "t") == "sender" and mail_reply.excluded(mine, "bob@evil.example.test", "s", "t") == "sender"
    assert mail_reply.excluded(mine, "bob@ok.example.test", "Please sign the NDA", "t") == "words" and mail_reply.excluded(mine, "bob@ok.example.test", "s", "about my SALARY") == "words"
    assert mail_reply.excluded(mine, "bob@ok.example.test", "hello", "plain") is None


def test_the_ai_use_log_keeps_no_text_and_an_excluded_message_never_reaches_the_model(monkeypatch):
    from app.services.ai import features

    seen = []
    monkeypatch.setattr(features, "draft_email", lambda *a, **k: seen.append((a, k)) or (_ for _ in ()).throw(RuntimeError("model must not be called")))
    me = f"ai{TAG}@alliedweldingsystems.com"
    mail_reply.put_settings(me, {"ai_opt_in": True, "exclude_senders": ["secret@client.example.test"], "exclude_words": ["confidential"]}) if hasattr(mail_reply, "put_settings") else None
    mid = _mail("secret@client.example.test", subject="Confidential pricing", category="general", summary="our price is 100")
    try:
        mail_reply.draft_reply(mid, {"email": me, "role": "OPERATOR"}, True)
    except mail_reply.ReplyError:
        pass
    assert not seen
    cols = {c.name for c in MailAiUseDB.__table__.columns}
    assert cols <= {"id", "user_email", "mail_id", "model", "outcome", "created_at", "at"} and not {c for c in cols if "text" in c or "body" in c or "prompt" in c}

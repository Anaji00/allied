"""The lead engine: stored leads, dedupe, settings, do-not-contact, outreach drafts, mail-log rules, summary. Temporary database; nothing is sent; no web calls."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB
from app.services import leads as svc
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"ld-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r.json()

    return call


def _lead(api, **kw):
    body = {"company": f"Acme {TAG}{uuid.uuid4().hex[:3]}", "name": "Pat Jones", "title": "Plant Manager", "email": f"pat{uuid.uuid4().hex[:5]}@acme-{uuid.uuid4().hex[:6]}.test", **kw}
    return api("POST", "/leads", json=body, expect=201)


def test_the_five_example_accounts_are_seeded_once_and_leads_are_added_deduped_and_changed(api):
    assert api("GET", "/leads")["total"] == 0 and api("GET", "/leads/summary")["by_stage"]["Found"] == 0           # reading adds nothing
    api("POST", "/leads/seed-examples", role="OPERATOR", expect=403)
    assert api("POST", "/leads/seed-examples", role="MANAGER", expect=201)["added"] == 5
    assert api("POST", "/leads/seed-examples", role="MANAGER", expect=201)["added"] == 0                          # once
    first = api("GET", "/leads")
    assert first["total"] >= 5 and {"items", "total", "due_today"} <= set(first) and all(i["source"] == "target" for i in first["items"][:5])
    again = api("GET", "/leads")
    assert again["total"] == first["total"]                                                   # seeded once
    a = _lead(api)
    assert a["duplicate"] is False and a["status"] == "Found" and a["company"] and a["email"] and a["score"] is not None and a["score_parts"]
    dup = api("POST", "/leads", json={"company": a["company"], "name": a["name"]}, expect=201)
    assert dup["duplicate"] is True and dup["id"] == a["id"]
    dup2 = api("POST", "/leads", json={"email": a["email"].upper(), "company": "Other Co"}, expect=201)
    assert dup2["duplicate"] is True and dup2["id"] == a["id"]
    many = api("POST", "/leads", json={"leads": [{"company": f"Bulk {TAG}1", "name": "A B"}, {"company": f"Bulk {TAG}1", "name": "A B"}, {"email": "not an email"}]}, expect=201)
    assert many["added"] == 1 and many["duplicates"] == 1 and len(many["errors"]) == 1
    got = api("PATCH", f"/leads/{a['id']}", json={"status": "Qualified", "notes": "met at the show", "owner": "Boss@Example.test"})
    assert got["status"] == "Qualified" and got["notes"] == "met at the show" and got["owner"] == "boss@example.test"
    api("PATCH", f"/leads/{a['id']}", json={"status": "Nonsense"}, expect=422)
    api("PATCH", f"/leads/{a['id']}", json={"id": 5}, expect=422)
    assert api("GET", "/leads", params={"status": "Qualified", "q": a["company"]})["total"] == 1
    api("DELETE", f"/leads/{a['id']}")
    assert api("GET", "/leads", params={"q": a["company"]})["total"] == 0
    api("GET", "/leads", role="VIEWER", expect=403)


def test_outreach_is_a_draft_that_waits_logs_and_sets_the_next_touch_and_do_not_contact_refuses(api):
    api("PUT", "/leads/settings", role="MANAGER", json={"signature": "Sam at Allied", "postal_address": "1 Mill Rd, Starr SC", "follow_up_days": 5})
    a = _lead(api, bottlenecks=["Chute liners"])
    got = api("POST", f"/leads/{a['id']}/outreach", json={})
    assert got["template"] == "new-prospect" and "Sam at Allied" in got["body"] and "1 Mill Rd" in got["body"] and "unsubscribe" in got["body"].lower() and got["to"] == a["email"]
    with SessionLocal() as db:
        assert db.get(AssistantActionDB, got["action_id"]).status == "PENDING"            # waits for a person; nothing is sent
    after = api("GET", "/leads", params={"q": a["company"]})["items"][0]
    assert after["status"] == "Contacted" and after["next_touch_at"] and after["last_touch_at"]
    assert api("GET", f"/leads/{a['id']}/outreach")[0]["kind"] == "draft"
    api("POST", "/leads/do-not-contact", json={"value": f"@{a['email'].split('@')[1]}", "reason": "asked"}, expect=201)
    refused = api("POST", f"/leads/{a['id']}/outreach", json={}, expect=409)
    assert "do-not-contact" in refused["detail"]
    nomail = api("POST", "/leads", json={"company": f"NoMail {TAG}", "name": "Zed"}, expect=201)
    assert "no email" in api("POST", f"/leads/{nomail['id']}/outreach", json={}, expect=409)["detail"]


def test_settings_are_validated_and_search_stays_off(api):
    st = api("GET", "/leads/settings")
    assert st["search_enabled"] is False and st["sequence_days"] == [0, 4, 10] and "win-back" in st["templates"]
    api("PUT", "/leads/settings", role="OPERATOR", json={"signature": "x"}, expect=403)
    api("PUT", "/leads/settings", role="MANAGER", json={"sequence_days": [5, 1]}, expect=422)
    api("PUT", "/leads/settings", role="MANAGER", json={"weights": {"fit": 500}}, expect=422)
    api("PUT", "/leads/settings", role="MANAGER", json={"nonsense": 1}, expect=422)
    api("PUT", "/leads/settings", role="MANAGER", json={"search_enabled": True})
    assert api("GET", "/leads/settings")["search_enabled"] is False                          # the owner's switch (LEAD_WEB_SEARCH) is still off
    assert "switched off" in api("POST", "/leads/scrape", json={"job_titles": ["x"], "industries": ["y"], "location": "z", "max_results": 1}, expect=409)["detail"]


def test_replies_bounces_out_of_office_and_unsubscribes_are_read_by_rule_and_forged_mail_is_ignored(api):
    a = _lead(api)
    b = _lead(api)
    c = _lead(api)
    d = _lead(api)
    assert svc.on_mail(1, a["email"], "Re: hello", "Yes please quote", trusted=False) is None                       # failed the sender check: nothing changes
    assert api("GET", "/leads", params={"q": a["company"]})["items"][0]["status"] == "Found"
    assert svc.on_mail(2, a["email"], "Re: hello", "Yes please quote", trusted=True) == "reply"
    got = api("GET", "/leads", params={"q": a["company"]})["items"][0]
    assert got["status"] == "Replied" and got["last_touch_at"] and got["sequence"]["paused"]
    assert [x["kind"] for x in api("GET", f"/leads/{a['id']}/outreach")] == ["reply"]
    assert svc.on_mail(3, "mailer-daemon@googlemail.com", "Undeliverable: hello", f"could not deliver to {b['email']}", trusted=True) == "bounce"
    assert api("GET", "/leads", params={"q": b["company"]})["items"][0]["bad_address"] is True
    assert "bounced" in api("POST", f"/leads/{b['id']}/outreach", json={}, expect=409)["detail"]
    from datetime import datetime, timedelta, timezone

    back = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    assert svc.on_mail(4, c["email"], "Automatic reply: away", f"I am out of office, back on {back}", trusted=True) == "out_of_office"
    got = api("GET", "/leads", params={"q": c["company"]})["items"][0]
    assert got["next_touch_at"].startswith(back) and not got["sequence"]["paused"]
    assert svc.on_mail(5, d["email"], "Re: hello", "Please unsubscribe me", trusted=True) == "unsubscribe"
    assert svc.blocked(d["email"]) and "do-not-contact" in api("POST", f"/leads/{d['id']}/outreach", json={}, expect=409)["detail"]
    assert svc.on_mail(6, "stranger@nowhere.test", "hi", "hello", trusted=True) is None


def test_the_summary_counts_stages_sources_and_pipeline(api):
    a = _lead(api, estimated_annual_spend=50000)
    api("PATCH", f"/leads/{a['id']}", json={"status": "Won"})
    assert api("GET", "/leads/summary")["pipeline_value"] is None                            # an operator does not see money
    s = api("GET", "/leads/summary", role="MANAGER")
    assert set(s["by_stage"]) == set(svc.STAGES) and s["won"] >= 1 and s["by_source"]["manual"]["won"] >= 1 and s["pipeline_value"] >= 0 and "win_rate" in s and "replied_week" in s


def _order(customer, days_ago, total=1000.0):
    from datetime import datetime, timedelta, timezone

    from app.models.db_models import OrderDB

    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number=f"P{uuid.uuid4().hex[:6]}", sales_order_number=f"9{uuid.uuid4().int % 10**5}", customer_id="C1", customer_name=customer,
                       total_revenue=total, line_items=[], created_at=datetime.now(timezone.utc) - timedelta(days=days_ago)))
        db.commit()


def test_history_is_mined_into_suggestions_with_a_sentence_and_aliases_join_names(api):
    lapsed, regular = f"Lapsed Co {TAG}", f"Regular Co {TAG}"
    for d in (800, 600):
        _order(lapsed, d)
    for d in (210, 140, 70):                                        # a gap of 70 days: the next is due about now
        _order(regular, d)
    _order(f"CT-KC {TAG}", 900)
    _order(f"CertainTeed Kansas City {TAG}", 700)
    api("POST", "/leads/aliases", role="MANAGER", json={"alias": f"CT-KC {TAG}", "canonical": f"CertainTeed Kansas City {TAG}"}, expect=201)
    api("POST", "/leads/aliases", role="OPERATOR", json={"alias": "x", "canonical": "y"}, expect=403)
    sug = {s["account"]: s for s in api("GET", "/leads/suggestions")}
    assert sug[lapsed]["kind"] == "lapsed" and "ordered 2 times" in sug[lapsed]["sentence"] and sug[lapsed]["suggested_template"] == "win-back"
    assert sug[regular]["kind"] == "reorder_due" and "usual gap 70 days" in sug[regular]["sentence"]
    assert sug[f"CertainTeed Kansas City {TAG}"]["kind"] == "lapsed" and f"CT-KC {TAG}" not in sug               # the alias joined the two names into one customer
    lead = api("POST", f"/leads/suggestions/{sug[lapsed]['id']}/make-lead", expect=201)
    assert lead["source"] == "history" and lead["reason"] == sug[lapsed]["sentence"] and lead["company"] == lapsed
    assert next(s for s in api("GET", "/leads/suggestions") if s["account"] == lapsed)["already_a_lead"] is True
    api("POST", "/leads/suggestions/nope/make-lead", expect=404)


def test_sequences_make_drafts_in_step_stop_on_a_reply_and_respect_the_daily_limit(api):
    from datetime import datetime, timezone

    from app.models.db_models import LeadOutreachDB

    with SessionLocal() as db:
        done_today = db.query(LeadOutreachDB).filter(LeadOutreachDB.kind == "draft", LeadOutreachDB.at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)).count()
    api("PUT", "/leads/settings", role="MANAGER", json={"sequence_mode": "suggest", "daily_draft_limit": done_today + 2, "postal_address": "1 Mill Rd"})
    a, b, c = _lead(api), _lead(api), _lead(api)
    for x in (a, b, c):
        api("POST", f"/leads/{x['id']}/sequence")
    dry = api("POST", "/leads/sequences/run", role="MANAGER", json={"dry_run": True})
    assert len(dry["would_make"]) == 2 and dry["made"] == []                                    # the daily limit
    ran = api("POST", "/leads/sequences/run", role="MANAGER", json={"dry_run": False, "ignore_hours": True})
    assert len(ran["made"]) == 2 and all("Step 1 of 3" in m["why"] for m in ran["made"])
    first = ran["made"][0]["lead_id"]
    row = api("GET", "/leads", params={"q": a["company"]})["items"][0] if first == a["id"] else api("GET", "/leads", params={"q": b["company"]})["items"][0]
    assert row["sequence"]["step"] == 1 and row["next_touch_at"]
    svc.on_mail(70, row["email"], "Re: hi", "yes", trusted=True)                                 # a reply stops the sequence
    after = api("POST", "/leads/sequences/run", role="MANAGER", json={"dry_run": True})
    assert row["id"] not in [m["lead_id"] for m in after["would_make"]]
    api("PUT", "/leads/settings", role="MANAGER", json={"sequence_mode": "off"})
    assert "switched off" in api("POST", "/leads/sequences/run", role="MANAGER", json={"dry_run": False})["note"]


def test_to_monday_needs_a_chosen_board_shows_left_out_and_maybe_the_same_and_only_stages(api):
    from app.services.coordination import boards

    lead = _lead(api, phone="555-0100")
    assert "Choose a board" in api("POST", f"/leads/{lead['id']}/to-monday", role="MANAGER", json={}, expect=409)["detail"]
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "Some board", "role": "projects", "read_at": "now", "groups": [], "columns": [{"id": "customer", "title": "Customer", "type": "text"}],
                                 "items": [{"id": "1", "name": lead["company"].upper(), "values": {}}]}
    dry = api("POST", f"/leads/{lead['id']}/to-monday", role="MANAGER", json={"board_id": board})
    assert dry["dry_run"] and dry["maybe_the_same"][0]["item_id"] == "1" and "phone" in dry["left_out"] and "email" in dry["left_out"]
    assert "similar name" in api("POST", f"/leads/{lead['id']}/to-monday", role="MANAGER", json={"board_id": board, "dry_run": False}, expect=409)["detail"]
    api("POST", f"/leads/{lead['id']}/to-monday", role="OPERATOR", json={"board_id": board}, expect=403)


def test_a_bounce_with_delivery_report_fields_marks_only_the_final_recipient_not_every_address_quoted(api):
    a, b = _lead(api), _lead(api)
    body = f"Delivery has failed.\n\nFinal-Recipient: rfc822; {a['email']}\nAction: failed\n\n--- original message ---\nFrom: sales@alliedweldingsystems.com\nTo: {a['email']}\nCc: {b['email']}\n"
    assert svc.on_mail(90, "mailer-daemon@googlemail.com", "Delivery Status Notification (Failure)", body, trusted=True) == "bounce"
    assert api("GET", "/leads", params={"q": a["company"]})["items"][0]["bad_address"] is True
    assert api("GET", "/leads", params={"q": b["company"]})["items"][0]["bad_address"] is False


def test_the_scheduled_run_and_the_weekly_digest_make_drafts_only_and_the_web_lookup_is_off_and_safe(api, monkeypatch):
    from app.config import settings as cfg

    assert svc.scheduled_run() == {"mode": "off"}                                                   # off by default
    api("PUT", "/leads/settings", role="MANAGER", json={"sequence_mode": "suggest", "daily_draft_limit": 500, "postal_address": "1 Mill Rd"})
    lead = _lead(api)
    api("POST", f"/leads/{lead['id']}/sequence")
    monkeypatch.setattr(svc, "_business_hours_now", lambda: True)
    ran = svc.scheduled_run()
    assert ran["mode"] == "suggest" and any(m["lead_id"] == lead["id"] for m in ran["made"])
    api("PUT", "/leads/settings", role="MANAGER", json={"sequence_mode": "off"})
    d = api("POST", "/leads/digest", role="MANAGER")
    assert d["week"] and d["outcome"] in ("drafted", "already") and "Leads by stage" not in str(d)
    again = api("POST", "/leads/digest", role="MANAGER")
    assert again["outcome"] == "already"                                                           # once a week
    api("POST", "/leads/digest", role="OPERATOR", expect=403)
    assert "switched off" in api("POST", f"/leads/{lead['id']}/enrich", json={"url": "https://example.test"}, expect=409)["detail"]
    monkeypatch.setattr(cfg, "LEAD_WEB_SEARCH", True)
    api("PUT", "/leads/settings", role="MANAGER", json={"search_enabled": True})
    for bad in ("file:///etc/passwd", "http://localhost/x", "http://127.0.0.1/", "http://10.0.0.5/", "ftp://example.com/"):
        assert api("POST", f"/leads/{lead['id']}/enrich", json={"url": bad}, expect=422)["detail"]
    monkeypatch.setattr(svc, "fetch_title", lambda url: "Acme Wear Parts")
    got = api("POST", f"/leads/{lead['id']}/enrich", json={"url": "https://acme-example.test"})
    assert "Website: Acme Wear Parts" in got["notes"]

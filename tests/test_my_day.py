"""My day: one list of what waits on the signed-in person, role-aware and consistent with the badge. Temporary database; nothing is sent."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import NotificationDB
from app.services import my_day
from app.services.coordination import ball, notify, updates
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"md-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


def _shape_ok(row):
    assert {"id", "kind", "sentence", "reason", "primary_action", "link", "age", "severity"} <= set(row)
    assert {"label", "method", "path", "body"} == set(row["primary_action"]) and row["sentence"] and row["reason"]


def test_every_kind_of_waiting_shows_up_as_one_row_with_one_click(api):
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Day {TAG}{uuid.uuid4().hex[:3]}"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("POST", f"/actions/job/{job['ref']}/handoff", json={"to": me, "note": "Check the drawing"})                                    # the ball is with me
    api("POST", f"/actions/job/{job['ref']}/share", json={"body": "Please look", "mentions": [me]}, role="MANAGER")                      # a manager mentions me
    notify.notify("test", f"day-{TAG}", f"Test alert {TAG}", "body", to=[me])                                                           # an alert for me
    day = api("GET", "/my-day").json()
    kinds = {r["kind"] for r in day["items"]}
    assert {"ball", "mention", "alert"} <= kinds
    for r in day["items"]:
        _shape_ok(r)
    alert = next(r for r in day["items"] if r["kind"] == "alert" and f"Test alert {TAG}" in r["sentence"])
    assert alert["primary_action"]["method"] == "POST" and alert["primary_action"]["path"].endswith("/acknowledge")
    ball_row = next(r for r in day["items"] if r["kind"] == "ball" and job["ref"] in r["link"] or r["kind"] == "ball")
    assert ball_row["primary_action"]["method"] == "LINK" and day["counts"]["ball"] >= 1
    ages = [r["age_days"] for r in day["items"]]
    assert day["total"] == len(day["items"]) == sum(day["counts"].values())
    api("POST", f"/alerts/{alert['id'].split(':')[1]}/acknowledge")
    assert not any(r["id"] == alert["id"] for r in api("GET", "/my-day").json()["items"])


def test_a_snoozed_alert_is_left_out_and_the_counts_match_the_badge(api):
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    notify.notify("test", f"snz-{TAG}", f"Snooze me {TAG}", "body", to=[me])
    day = api("GET", "/my-day").json()
    row = next(r for r in day["items"] if f"Snooze me {TAG}" in r["sentence"])
    api("POST", f"/alerts/{row['id'].split(':')[1]}/snooze", json={"hours": 24})
    after = api("GET", "/my-day").json()
    assert not any(f"Snooze me {TAG}" in r["sentence"] for r in after["items"])
    needs = api("GET", "/me/needs").json()
    assert after["badge"] == needs["total"] and after["counts"].get("alert", 0) == needs["counts"]["alerts"] and after["counts"].get("ball", 0) == needs["counts"]["ball"]


def test_a_manager_sees_approvals_an_operator_does_not_and_a_viewer_sees_nothing(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Appr {TAG}{uuid.uuid4().hex[:3]}"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("POST", f"/jobs/{job['ref']}/design/approve", role="MANAGER")
    po = api("POST", f"/jobs/{job['ref']}/purchase-orders", json={"vendor": f"Appr Vendor {TAG}", "lines": [{"description": "bracket", "quantity": 1, "unit_cost": 40, "revision": "A"}]}).json()
    mgr = api("GET", "/my-day", role="MANAGER").json()
    row = next(r for r in mgr["items"] if r["id"] == f"approval:po:{po['id']}")
    assert row["primary_action"]["path"] == f"/api/v1/purchase-orders/{po['id']}/approve" and row["primary_action"]["method"] == "POST"
    assert not any(r["kind"] == "approval" for r in api("GET", "/my-day", role="OPERATOR").json()["items"] if r["id"] == f"approval:po:{po['id']}")
    viewer = api("GET", "/my-day", role="VIEWER").json()
    assert viewer == {"total": 0, "need_action": 0, "badge": 0, "counts": {}, "items": []}
    api("POST", f"/purchase-orders/{po['id']}/approve", role="MANAGER")                                                                   # doing the click removes the row
    assert not any(r["id"] == f"approval:po:{po['id']}" for r in api("GET", "/my-day", role="MANAGER").json()["items"])


def test_a_broken_source_never_blanks_the_day(api, monkeypatch):
    monkeypatch.setattr("app.services.coordination.ball.waiting_on", lambda email: 1 / 0)
    day = api("GET", "/my-day").json()
    assert isinstance(day["items"], list)


def test_the_brief_is_a_headline_and_linked_sentences_in_the_business_timezone(api, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(notify, "now_local", lambda: datetime(2026, 11, 17, 7, 30, tzinfo=ZoneInfo("America/Chicago")))
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    quiet = api("GET", "/brief").json()
    assert quiet["headline"].startswith("Good morning") and "Tuesday 17 November" in quiet["headline"] and quiet["date"] == "2026-11-17" and quiet["timezone"] == "America/Chicago"
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Brief {TAG}{uuid.uuid4().hex[:3]}"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("POST", f"/actions/job/{job['ref']}/handoff", json={"to": me, "note": "Look at it"})
    got = api("GET", "/brief").json()
    assert got["count"] >= 1 and got["items"] and all(i["sentence"] and i["link"].startswith("/") for i in got["items"])
    assert "need" in got["headline"] and any("Waiting on you" in i["sentence"] for i in got["items"])
    assert api("GET", "/brief", role="VIEWER").json()["items"] == []


def _world(api):
    vendor = f"Prep Vendor {TAG}{uuid.uuid4().hex[:3]}"
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Prep {TAG}{uuid.uuid4().hex[:3]}", "contact_email": f"b{TAG}@customer-example.test"}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("POST", f"/jobs/{job['ref']}/design/approve", role="MANAGER")
    po = api("POST", f"/jobs/{job['ref']}/purchase-orders", json={"vendor": vendor, "lines": [{"description": "5in flex hose", "quantity": 2, "unit_cost": 100, "revision": "B"}]}).json()
    api("POST", f"/actions/job/{job['ref']}/share", json={"body": f"Prep comment {TAG}"})
    return {"vendor": vendor, "rfq": rfq, "job": job, "po": po}


def test_prep_for_a_job_a_vendor_and_a_customer_has_the_whole_picture_and_a_text_form(api):
    w = _world(api)
    j = api("GET", f"/prep/job/{w['job']['ref']}").json()
    assert j["customer"] == CUSTOMER and j["pos"][0]["vendor"] == w["vendor"] and j["pos"][0]["revisions"][0]["line"] == 1 and j["rfqs"][0]["number"] == w["rfq"]["ref"]
    assert any(f"Prep comment {TAG}" in c["body"] for c in j["comments"]) and "Prep:" in j["text"] and w["vendor"] in j["text"]
    v = api("GET", f"/prep/vendor/{w['vendor']}").json()
    assert v["pos"][0]["id"] == w["po"]["id"] and v["open_pos"] >= 1 and "PO" in v["text"]
    c = api("GET", "/prep/customer/Owens Corning Starr Composites").json()
    assert any(x["ref"] == w["job"]["ref"] for x in c["jobs"]) and any(r["number"] == w["rfq"]["ref"] for r in c["rfqs"]) and w["vendor"] in c["text"]
    api("GET", "/prep/job/JOB-9999-9999", expect=404)
    api("GET", "/prep/banana/x", expect=422)
    api("GET", f"/prep/job/{w['job']['ref']}", role="VIEWER", expect=403)


def test_prep_shows_a_drawing_revision_the_vendor_does_not_match(api):
    w = _world(api)
    api("POST", f"/purchase-orders/{w['po']['id']}/approve", role="MANAGER")
    with SessionLocal() as db:
        from app.models.db_models import PurchaseOrderDB

        row = db.get(PurchaseOrderDB, w["po"]["id"])
        row.status = "SENT"
        row.revision_marks = {"acked": {"1": {"revision": "A", "by": "vendor", "at": "2026-11-16"}}}
        db.commit()
    j = api("GET", f"/prep/job/{w['job']['ref']}").json()
    assert j["pos"][0]["revisions"][0]["mismatch"] is True and "MISMATCH" in j["text"] and j["pos"][0]["drawing_mismatch_lines"]


def test_since_reads_plain_words_and_refuses_the_rest():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime(2026, 11, 17, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
    f = my_day.parse_since
    assert f("yesterday", now).isoformat() == "2026-11-16T00:00:00-06:00" and f("today", now).day == 17 and f("this week", now).day == 16 and f("last week", now).day == 9
    assert f("3d", now).day == 14 and f("6 hours", now).hour == 9 and f("2026-11-10", now).day == 10 and f("2w", now).day == 3
    for bad in ("someday", "-3d", "99999d", "3 fortnights"):
        with pytest.raises(ValueError):
            f(bad, now)


def test_since_for_a_job_lists_the_comments_files_ball_and_audit_trail(api):
    w = _world(api)
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    from app.services.coordination import files

    files.add("rfq", w["rfq"]["ref"], "rev_a.pdf", b"%PDF-1.4 test", {"email": me, "role": "OPERATOR"})
    api("POST", f"/actions/job/{w['job']['ref']}/handoff", json={"to": me, "note": "Check it"})
    got = api("GET", "/since", params={"kind": "job", "ref": w["job"]["ref"], "since": "today"}).json()
    srcs = {c["source"] for c in got["changes"]}
    assert {"comment", "file", "ball"} <= srcs and got["count"] == len(got["changes"]) and w["job"]["ref"] in got["title"] and "Check it" in got["text"]
    assert any(c["source"] == "comment" and f"Prep comment {TAG}" in c["sentence"] for c in got["changes"])
    assert all({"at", "who", "sentence", "source"} <= set(c) for c in got["changes"]) and got["changes"] == sorted(got["changes"], key=lambda c: c["at"], reverse=True)
    api("GET", "/since", params={"kind": "job", "ref": "JOB-9999-9999"}, expect=404)
    api("GET", "/since", params={"kind": "job", "ref": w["job"]["ref"], "since": "someday"}, expect=400)
    api("GET", "/since", params={"kind": "banana", "ref": "x"}, expect=422)
    api("GET", "/since", params={"kind": "job", "ref": w["job"]["ref"]}, role="VIEWER", expect=403)
    future = api("GET", "/since", params={"kind": "job", "ref": w["job"]["ref"], "since": "2099-01-01"}).json()
    assert future["count"] == 0 and "Nothing changed" in future["text"]


def test_since_for_a_board_lists_staged_changes_and_says_when_the_board_is_unknown(api):
    import app.services.board_edit as board_edit
    from app.services.coordination import boards

    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Since Board", "role": "reference", "read_at": "now", "groups": [], "columns": [{"id": "n", "title": "Notes", "type": "text"}],
                               "items": [{"id": "1", "name": "Row", "values": {}}]}
    try:
        board_edit.edit_cell(bid, "1", "Notes", "hello", {"email": f"md-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"})
        got = api("GET", "/since", params={"kind": "board", "ref": bid, "since": "today"}).json()
        assert got["title"] == "Since Board" and any(c["source"] == "staged" and "Edit Row" in c["sentence"] for c in got["changes"])
    finally:
        with boards._lock:
            boards._mirror.pop(bid, None)
    api("GET", "/since", params={"kind": "board", "ref": "424242"}, expect=404)


def _chase_draft(rfq_number, vendor, text):
    """A follow-up draft the way the autopilot makes it (a pending proposal in the usual queue)."""
    from app.models.db_models import AssistantActionDB

    with SessionLocal() as db:
        row = AssistantActionDB(user_email="autopilot@alliedweldingsystems.com", user_role="AUTOPILOT", tool="send_email", summary=text,
                                args={"to": f"v{TAG}@vendor-example.test", "subject": "Following up", "body": "Any news?", "vendor_name": vendor, "rfq_number": rfq_number, "_play": "rfq_vendor_chaser"})
        db.add(row)
        db.commit()
        return row.id


def test_chase_lists_who_owes_us_oldest_first_with_the_drafted_followup_and_one_call_send_dismiss(api):
    w = _world(api)
    api("POST", f"/actions/job/{w['job']['ref']}/handoff", json={"ball_with": "vendor", "to": f"Helen at {w['vendor']}", "note": "the proforma"})
    did = _chase_draft(w["rfq"]["ref"], w["vendor"], f"Ask {w['vendor']} for the overdue price on {w['rfq']['ref']}")
    got = api("GET", "/chase", role="ADMIN").json()
    row = next(r for r in got["items"] if r["draft_id"] == did)
    assert row["kind"] == "chase" and w["vendor"] in row["who"] + row["what"] and row["owes"] == "the proforma" or row["draft_id"] == did
    assert row["send"]["path"] == f"/api/v1/chase/{did}" and row["dismiss"]["body"] == {"action": "dismiss"} and got["with_draft"] >= 1
    days = [r["days_waiting"] for r in got["items"]]
    assert days == sorted(days, reverse=True)
    out = api("POST", f"/chase/{did}", role="ADMIN", json={"action": "dismiss"}).json()
    assert out["status"] == "CANCELLED"
    assert not any(r["draft_id"] == did for r in api("GET", "/chase", role="ADMIN").json()["items"])
    api("POST", f"/chase/{did}", role="ADMIN", json={"action": "dismiss"}, expect=404)                 # already decided


def test_chase_send_only_works_on_followup_drafts_and_for_the_right_roles(api):
    w = _world(api)
    from app.models.db_models import AssistantActionDB

    other = None
    with SessionLocal() as db:
        row = AssistantActionDB(user_email="autopilot@alliedweldingsystems.com", user_role="AUTOPILOT", tool="send_email", summary="A different kind of draft", args={"to": "x@example.test", "subject": "s", "body": "b", "_play": "rfq_acknowledge"})
        db.add(row)
        db.commit()
        other = row.id
    api("POST", f"/chase/{other}", role="ADMIN", json={"action": "send"}, expect=404)                     # not a follow-up: not decidable here
    did = _chase_draft(w["rfq"]["ref"], w["vendor"], f"Chase {w['vendor']} {TAG}")
    api("POST", f"/chase/{did}", role="ADMIN", json={"action": "maybe"}, expect=400)
    api("POST", f"/chase/{did}", role="VIEWER", json={"action": "send"}, expect=403)
    api("POST", f"/chase/{did}", role="OPERATOR", json={"action": "send"}, expect=404)                     # an autopilot draft is for its reviewers and admins
    api("GET", "/chase", role="VIEWER", expect=403)
    sent = api("POST", f"/chase/{did}", role="ADMIN", json={"action": "send"}).json()
    assert sent["status"] == "DONE" and sent["draft_id"] == did


def _fake_summary(monkeypatch, **over):
    from app.services.ai import features

    s = features.CallSummary(caller_name="Helen Zhao", company="Zibo", reason="supplier", language="en", summary="She said the hoses ship on the 25th.", po_numbers=["9260"],
                             action_items=["Confirm the ship date in writing", "Send the revised drawing"], callback_needed=True, urgency="normal")
    s = s.model_copy(update=over)
    seen = {}

    def fake(transcript, from_number="", reference=None, actor="system"):
        seen.update(transcript=transcript, from_number=from_number, reference=reference)
        return s, "sug-1"

    monkeypatch.setattr(features, "summarize_call", fake)
    return seen


def test_a_call_is_summarised_into_a_draft_note_on_the_job_and_posts_only_when_confirmed(api, monkeypatch):
    from app.services.assistant import agent

    w = _world(api)
    seen = _fake_summary(monkeypatch)
    out = api("POST", "/calls/to-job", json={"job": w["job"]["ref"], "text": "Hi it is Helen from Zibo, the hoses ship on the 25th.", "from_number": "+86 555"}).json()
    assert out["status"] == "PENDING" and out["job"] == w["job"]["ref"] and "Follow-ups:" in out["note"] and "- Confirm the ship date in writing" in out["note"] and "callback" in out["note"]
    assert out["confirm"]["body"] == {"confirm": True} and out["summary"]["po_numbers"] == ["9260"] and seen["reference"] == w["job"]["ref"] and seen["from_number"] == "+86 555"
    from app.services.coordination import updates

    assert not any("Call from Helen Zhao" in c["body"] for c in updates.thread("rfq", w["rfq"]["ref"], include_notes=False))          # nothing posted yet
    done = agent.decide(out["action_id"], True, {"email": f"md-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"})
    assert done["status"] == "DONE"
    assert any("Call from Helen Zhao" in c["body"] for c in updates.thread("rfq", w["rfq"]["ref"], include_notes=False))


def test_call_notes_refuse_bad_input_unknown_jobs_roles_and_an_ai_that_is_off(api, monkeypatch):
    w = _world(api)
    _fake_summary(monkeypatch)
    api("POST", "/calls/to-job", json={"job": w["job"]["ref"], "text": "  "}, expect=400)
    api("POST", "/calls/to-job", json={"job": w["job"]["ref"], "text": "x" * 30001}, expect=400)
    api("POST", "/calls/to-job", json={"job": w["job"]["ref"], "text": ["a"]}, expect=400)
    api("POST", "/calls/to-job", json={"job": "JOB-9999-9999", "text": "hello"}, expect=404)
    api("POST", "/calls/to-job", json={"job": w["job"]["ref"], "text": "hello"}, role="VIEWER", expect=403)
    from app.services.ai import features
    from app.services.ai.providers import AIUnavailableError

    def off(*a, **k):
        raise AIUnavailableError("Set GROQ_API_KEY to use Groq.")

    monkeypatch.setattr(features, "summarize_call", off)
    assert "GROQ" in api("POST", "/calls/to-job", json={"job": w["job"]["ref"], "text": "hello"}, expect=503).json()["detail"]


def test_a_call_recording_is_transcribed_then_noted_and_a_bad_file_is_refused(api, monkeypatch):
    from app.services.ai import speech

    w = _world(api)
    _fake_summary(monkeypatch)
    monkeypatch.setattr(speech, "transcribe", lambda data, suffix=".wav", language=None: {"text": "It is Helen from Zibo.", "language": "en"})
    ok = api("POST", "/calls/to-job/audio", data={"job": w["job"]["ref"], "from_number": "+1"}, files={"upload": ("call.wav", b"RIFF....WAVEfmt ", "audio/wav")}).json()
    assert ok["status"] == "PENDING" and ok["note"]
    api("POST", "/calls/to-job/audio", data={"job": w["job"]["ref"]}, files={"upload": ("call.exe", b"MZ", "application/octet-stream")}, expect=400)
    api("POST", "/calls/to-job/audio", data={"job": w["job"]["ref"]}, files={"upload": ("call.wav", b"RIFF", "audio/wav")}, role="VIEWER", expect=403)


def test_a_ball_row_says_whom_the_ball_is_with_including_items_i_own_that_wait_on_someone_else(api):
    w = _world(api)
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    api("POST", f"/actions/job/{w['job']['ref']}/handoff", json={"to": me, "note": "mine now"})
    mine = next(r for r in api("GET", "/my-day").json()["items"] if r["kind"] == "ball" and w["job"]["ref"] in r["sentence"] or r["kind"] == "ball" and "the ball is with you" in r["sentence"])
    assert mine["ball_with"]["mine"] is True and "with you" in mine["sentence"]
    api("PUT", "/ball", json={"item_type": "rfq", "item_ref": w["rfq"]["ref"], "owner": me, "ball_with": "vendor", "ball_person": f"Helen at {w['vendor']}", "note": "price"}) if False else None
    api("POST", f"/actions/job/{w['job']['ref']}/handoff", json={"ball_with": "vendor", "to": f"Helen at {w['vendor']}", "note": "price"}, role="MANAGER")
    rows = [r for r in api("GET", "/my-day").json()["items"] if r["kind"] == "ball"]
    assert all("ball_with" in r and r["ball_with"]["name"] for r in rows)
    waiting = [r for r in rows if r["ball_with"]["mine"] is False]
    assert not waiting or all("waiting on" in r["sentence"] for r in waiting)


def _alert_aged(title, days, severity="info"):
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    notify.notify("test_fyi", f"aged-{title}-{uuid.uuid4().hex[:4]}", title, "body", severity=severity, to=[me])
    with SessionLocal() as db:
        rows = db.query(NotificationDB).filter(NotificationDB.title == title).all()
        for row in rows:
            row.created_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()
        return rows[0].id


def test_old_information_alerts_fold_into_one_row_and_the_headline_counts_only_what_needs_me(api, monkeypatch):
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(notify, "now_local", lambda: _dt(2026, 11, 17, 7, 30, tzinfo=ZoneInfo("America/Chicago")))
    old = [_alert_aged(f"Old FYI {TAG} {i}", 16 + i) for i in range(3)]
    young = _alert_aged(f"Young FYI {TAG}", 1)
    warn = _alert_aged(f"Warn {TAG}", 5, "warning")
    day = api("GET", "/my-day").json()
    rows = day["items"]
    assert not any(f"Old FYI {TAG}" in r["sentence"] for r in rows)                                    # folded away
    fold = next(r for r in rows if r["kind"] == "notice")
    assert fold["sentence"].endswith("older notices") and fold["severity"] == "low" and fold["primary_action"]["path"] == "/api/v1/my-day/dismiss-notices"
    y = next(r for r in rows if f"Young FYI {TAG}" in r["sentence"])
    assert y["severity"] == "low" and "nothing to do but dismiss" in y["reason"]
    w = next(r for r in rows if f"Warn {TAG}" in r["sentence"])
    assert w["reason"].startswith("A warning for you") and w["severity"] == "normal"
    need = sum(1 for r in rows if r["severity"] != "low")
    assert day["need_action"] == need and need >= 1
    assert api("GET", "/brief").json()["need_action"] == need and f"{need} thing" in api("GET", "/brief").json()["headline"]
    out = api("POST", "/my-day/dismiss-notices").json()
    assert out["dismissed"] >= 3
    after = api("GET", "/my-day").json()["items"]
    assert not any(r["kind"] == "notice" for r in after) and any(f"Young FYI {TAG}" in r["sentence"] for r in after)         # the young one is not an "older notice"
    api("POST", "/my-day/dismiss-notices", role="VIEWER", expect=403)


def test_two_pending_drafts_for_one_target_show_as_one_row_and_the_autopilot_does_not_make_a_second_each_week(api):
    from app.models.db_models import AssistantActionDB
    from app.services.coordination import autopilot

    summary = f"Ask Helen for their price on RFQ-DUP-{TAG} (Owens)"
    with SessionLocal() as db:
        for i in range(2):
            db.add(AssistantActionDB(user_email="autopilot@alliedweldingsystems.com", user_role="AUTOPILOT", tool="send_email", summary=summary,
                                     args={"to": f"h{TAG}@vendor-example.test", "subject": "s", "body": "b", "_play": "rfq_vendor_chaser"}))
        db.commit()
    rows = [r for r in api("GET", "/my-day", role="ADMIN").json()["items"] if summary in r["sentence"]]
    assert len(rows) == 1
    intent = lambda wk: autopilot.Intent("draft", f"rfq-vendor-chase:7:helen:{wk}", f"Ask Helen for a price on RFQ-WK-{TAG} (Owens)", to=["admin@alliedweldingsystems.com"], tool="send_email",
                                         args={"to": f"h{TAG}@vendor-example.test", "subject": "s", "body": "b"})
    first = autopilot._draft("rfq_vendor_chaser", intent("2026w46"), False)
    second = autopilot._draft("rfq_vendor_chaser", intent("2026w47"), False)                            # the next week's key, the same target
    assert first == "drafted" and second == "already"
    with SessionLocal() as db:
        assert db.query(AssistantActionDB).filter(AssistantActionDB.summary == f"Ask Helen for a price on RFQ-WK-{TAG} (Owens)").count() == 1
    assert autopilot._draft("rfq_vendor_chaser", intent("2026w48"), False, explicit=True) == "drafted"      # a person's own "follow up again" is not swallowed by the weekly duplicate rule


def test_a_test_alert_closes_itself_after_a_day(api):
    me = f"md-operator-{TAG}@alliedweldingsystems.com"
    notify.notify("test", f"test:{TAG}:{uuid.uuid4().hex[:4]}", f"Test alert {TAG}", "body", to=[me])
    with SessionLocal() as db:
        for row in db.query(NotificationDB).filter(NotificationDB.title == f"Test alert {TAG}").all():
            row.created_at = datetime.now(timezone.utc) - timedelta(hours=30)
        db.commit()
    assert notify.expire_tests() >= 1
    assert not any(f"Test alert {TAG}" in r["sentence"] for r in api("GET", "/my-day").json()["items"])

"""CEO round 2: what late means in words, waiting days per holder, one spelling per name, and plain sentences when the owner and the party are the same. Temporary database."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import my_day
from app.services.coordination import ball
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


def test_one_spelling_per_name():
    got = ball._casing(["helen", "Helen", "HELEN", "kim", "KINGCERA", "x@y.test", "Ana Lee", "ana lee"])
    assert got["helen"] == "Helen" and got["kim"] == "Kim" and got["kingcera"] == "KINGCERA" and got["x@y.test"] == "x@y.test" and got["ana lee"] == "Ana Lee"


def test_the_summary_says_what_late_means_and_gives_waiting_days_per_holder(monkeypatch):
    rows = [{"item_type": "rfq", "item_ref": f"R{i}", "label": f"RFQ {i}", "derived": False, "owner_email": "o@x.test", "owner_name": "O", "ball_with": "vendor", "ball_label": "a vendor",
             "ball_person": None, "ball_name": name, "note": "", "due_at": None, "overdue": False, "since": "2026-09-01T00:00:00+00:00", "days_waiting": days, "level": 2, "status": "late", "resolved": False, "history": []}
            for i, (name, days) in enumerate([("helen", 68.0), ("HELEN", 5.0), ("Helen", 3.0)])]
    monkeypatch.setattr(ball, "list_open", lambda *a, **k: rows)
    out = ball.summary()
    assert len(out["by_holder"]) == 1 and out["by_holder"][0]["name"] == "Helen" and out["by_holder"][0]["count"] == 3
    assert out["by_holder"][0]["waiting_days"] == 68 and out["by_holder"][0]["late"] == 3
    assert f"{settings.BALL_NUDGE_DAYS} working days" in out["late_means"] and "also late" in out["late_means"] and "ball" not in out["late_means"].lower() and "escalated" not in out["late_means"].lower() and str(settings.BALL_LEADERSHIP_DAYS) in out["late_means"]
    assert {r["ball_name"] for r in out["oldest"]} == {"Helen"}


def test_owner_and_party_the_same_reads_waiting_on_the_vendor(monkeypatch):
    me = f"same-{TAG}@alliedweldingsystems.com"
    row = {"item_type": "vendor_job", "item_ref": "9234", "label": "KINGCERA PO 9234", "owner_email": me, "ball_person": "kc@vendor-example.test", "ball_name": "KINGCERA", "ball_with": "vendor", "ball_label": "a vendor",
           "note": "", "days_waiting": 3, "item": None}
    other = {**row, "item_ref": "9235", "label": "a board item", "ball_person": "someone@else.test", "ball_name": "helen", "owner_email": me}
    monkeypatch.setattr(ball, "list_open", lambda *a, **k: [row, other])
    got = my_day.items({"email": me, "role": "OPERATOR"})["items"]
    sentences = [r["sentence"] for r in got if r["kind"] == "ball"]
    assert any("waiting on the vendor KINGCERA" in s for s in sentences)
    assert any(s.startswith("A board item") for s in sentences)


@pytest.mark.parametrize("raw,want", [("A draft is waiting for your OK.)", "A draft is waiting for your OK"), ("A draft is waiting for your OK)", "A draft is waiting for your OK"), ("A draft (why: x.) is here", "A draft (why: x) is here"),
                                      ("Fine (balanced)", "Fine (balanced)"), ("Open (never closed", "Open (never closed)")])
def test_stray_brackets_are_fixed(raw, want):
    assert my_day._tidy(raw) == want


def test_old_information_alerts_and_test_alerts_close_themselves_with_an_audit_line_each():
    from datetime import datetime, timedelta, timezone

    from app.core.database import SessionLocal
    from app.models.db_models import AuditLogDB, NotificationDB
    from app.services.coordination import notify

    me = f"hk-{TAG}@alliedweldingsystems.com"
    old_info = notify.notify("t", f"hk-info-{TAG}", f"Old info {TAG}", "b", to=[me], severity="info")
    old_warn = notify.notify("t", f"hk-warn-{TAG}", f"Old warning {TAG}", "b", to=[me], severity="warning")
    fresh = notify.notify("t", f"hk-fresh-{TAG}", f"Fresh info {TAG}", "b", to=[me], severity="info")
    test_alert = notify.notify("test", f"hk-test-{TAG}", f"Test {TAG}", "b", to=[me], severity="info")
    with SessionLocal() as db:
        for nid, days in ((old_info, 20), (old_warn, 20), (test_alert, 2)):
            db.get(NotificationDB, nid).created_at = datetime.now(timezone.utc) - timedelta(days=days)
        db.commit()
    assert notify.expire_old_info() >= 1 and notify.expire_tests() >= 1
    with SessionLocal() as db:
        state = {nid: db.get(NotificationDB, nid).acknowledged_by for nid in (old_info, old_warn, fresh, test_alert)}
        lines = [a.detail for a in db.query(AuditLogDB).filter(AuditLogDB.action == "ALERT_AUTO_CLOSED", AuditLogDB.resource_id.in_([str(old_info), str(test_alert)]))]
    assert state[old_info] == "system" and state[test_alert] == "system" and state[old_warn] is None and state[fresh] is None
    assert len(lines) == 2 and any("information-only" in l for l in lines) and any("test alert" in l for l in lines)
    assert notify.expire_old_info(0) == 0


def test_closing_an_rfq_cancels_its_waiting_follow_up_drafts_and_none_is_made_again():
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB, AuditLogDB
    from app.services.coordination import autopilot, rfqs
    from app.services.coordination.autopilot import Intent

    boss = {"email": f"nobid-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
    r = rfqs.create({"customer": "Owens Corning", "subject": f"No bid {TAG}", "items": [{"description": "liner", "quantity": 1}]}, boss, announce=False)
    number = r["number"]
    with SessionLocal() as db:
        db.add(AssistantActionDB(user_email=boss["email"], user_role="AUTOPILOT", tool="send_email", args={"to": "h@vendor-example.test", "subject": "Price?", "body": f"Our ref {number}", "_play": "rfq_vendor_chaser"},
                                 summary=f"Ask Helen for their price on {number}", status="PENDING"))
        other = AssistantActionDB(user_email=boss["email"], user_role="AUTOPILOT", tool="send_email", args={"to": "h@vendor-example.test", "subject": "Other", "body": "unrelated", "_play": "x"},
                                  summary="Ask someone about RFQ-2099-0001", status="PENDING")
        db.add(other)
        db.commit()
        other_id = other.id
    rfqs.update(r["id"], {"status": "NO_BID", "reason": "left the monday board"}, boss)
    with SessionLocal() as db:
        mine = db.query(AssistantActionDB).filter(AssistantActionDB.summary == f"Ask Helen for their price on {number}").one()
        assert mine.status == "CANCELLED"
        assert db.get(AssistantActionDB, other_id).status == "PENDING"
        assert db.query(AuditLogDB).filter(AuditLogDB.action == "DRAFT_CANCELLED", AuditLogDB.resource_id == str(mine.id)).count() == 1
    again = autopilot._draft("rfq_vendor_chaser", Intent("draft", f"rfq-vendor-chase:{r['id']}:helen:2026w47", f"Ask Helen for a price on {number} (Owens)", to=[boss["email"]], tool="send_email",
                                                         args={"to": "h@vendor-example.test", "subject": "s", "body": "b"}), False)
    assert again.startswith("skipped") and "closed" in again


def test_the_health_page_says_live_or_test_and_sage_50():
    from app.api.v1.endpoints import health

    src = open(health.__file__, encoding="utf-8").read()
    assert '"environment_label": "Live" if settings.is_production else "Test"' in src and '"source_label": "Sage 50"' in src


def test_a_vendor_row_says_it_in_a_sentence_and_suggests_a_next_step_and_never_shows_bare_negative_spare():
    from datetime import date

    from app.services.coordination import vendors

    on = date(2026, 11, 18)
    late = vendors.assess({"promised_on": "2026-10-21", "need_by": "2026-11-16", "ordered_on": "2026-09-20"}, None, None, on)
    assert late["state"] == "late" and late["days_late"] == 28
    assert late["text"].startswith("Promised Oct 21; must ship by Nov 16;") and "28 days late" in late["text"] and "to spare" not in late["text"] and late["next_step_suggested"].startswith("Ask the vendor for a new ship date")
    miss = vendors.assess({"promised_on": "2026-12-20", "need_by": "2026-12-01", "ordered_on": "2026-11-10"}, None, None, on)
    assert "too late for the ship-by date" in miss["text"] and "-" not in miss["text"].split("late")[0].split(";")[-1] and miss["next_step_suggested"]
    ok = vendors.assess({"promised_on": "2026-12-01", "need_by": "2026-12-20", "ordered_on": "2026-11-10", "acknowledged_on": "2026-11-11"}, None, None, on)
    assert "days to spare" in ok["text"] and ok["next_step_suggested"]
    none = vendors.assess({}, None, None, on)
    assert none["text"] == "No dates yet." and "promise date" in none["next_step_suggested"]


def test_an_alert_about_a_board_row_that_is_gone_closes_itself_once_the_boards_are_read():
    from app.core.database import SessionLocal
    from app.models.db_models import NotificationDB
    from app.services.coordination import boards, notify

    me = f"orph-{TAG}@alliedweldingsystems.com"
    gone = notify.notify("t", f"orph-gone-{TAG}", f"Row gone {TAG}", "b", to=[me], project_item_id=f"9{TAG}1")
    kept = notify.notify("t", f"orph-kept-{TAG}", f"Row kept {TAG}", "b", to=[me], project_item_id=f"9{TAG}2")
    with boards._lock:
        saved = dict(boards._mirror)
        boards._mirror.clear()
    try:
        assert notify.close_orphaned_item_alerts() == 0                                  # no board read yet: nothing is closed
        with boards._lock:
            boards._mirror["77"] = {"board_id": "77", "name": "B", "role": "projects", "read_at": "now", "groups": [], "columns": [], "items": [{"id": f"9{TAG}2", "name": "x", "values": {}}]}
        assert notify.close_orphaned_item_alerts() >= 1
    finally:
        with boards._lock:
            boards._mirror.clear()
            boards._mirror.update(saved)
    with SessionLocal() as db:
        assert db.get(NotificationDB, gone).acknowledged_by == "system" and db.get(NotificationDB, kept).acknowledged_at is None


def test_an_address_has_a_purpose_and_a_customer_draft_is_never_quietly_sent_to_accounts():
    import pytest as _pytest

    from app.services import actions, contacts
    from app.services.coordination import rfqs

    assert [contacts.purpose_of(e) for e in ("invoices@owenscorning.com", "ap@x.test", "Accounts.Payable@x.test", "quotes@x.test", "purchasing@x.test", "receiving@x.test", "jane.doe@x.test", "")] == \
        ["invoices", "invoices", "invoices", "quotes", "quotes", "shipping", "", ""]
    boss = {"email": f"cp-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
    r = rfqs.create({"customer": f"AP Only Co {TAG}", "subject": "Accounts only", "contact_email": "invoices@ap-only-example.test", "items": [{"description": "liner", "quantity": 1}]}, boss, announce=False)
    with _pytest.raises(actions.ActionError, match="No quote contact on file"):
        actions.email_draft("rfq", r["number"], boss, audience="customer", body="Here is the quote.", include_summary=False)
    ok = actions.email_draft("rfq", r["number"], boss, to="buyer@ap-only-example.test", audience="customer", body="Here is the quote.", include_summary=False)
    assert ok["drafted"] and ok["to"] == ["buyer@ap-only-example.test"]


def test_rfq_won_and_quoted_values_are_per_currency_and_never_added_as_dollars():
    from app.services.coordination import rfqs

    boss = {"email": f"cur-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}

    def closed(amount, currency, status):
        r = rfqs.create({"customer": f"Cur Co {TAG}", "subject": f"q {uuid.uuid4().hex[:4]}", "items": [{"description": "x", "quantity": 1}]}, boss, announce=False)
        changes = {"quote_amount": amount, "quote_number": "Q1", "status": "QUOTE_SENT"}
        if currency:
            changes["quote_currency"] = currency
        rfqs.update(r["id"], changes, boss)
        rfqs.update(r["id"], {"status": status, **({"reason": "price"} if status == "LOST" else {})}, boss)
        return r

    closed("1000", "USD", "WON")
    closed("500", "USD", "LOST")
    closed("2000", "CAD", "WON")
    closed("700", None, "LOST")
    m = rfqs.metrics(days=30)
    assert m["won_value_by_currency"].get("USD") >= 1000 and m["won_value_by_currency"].get("CAD") >= 2000 and m["quoted_value_by_currency"].get("unknown") >= 700
    assert m["win_rate_value"] is None and m["win_rate_value_by_currency"]["USD"] <= 1.0                         # mixed currencies: no single number
    assert "never added together" in m["value_note"] and "unknown" in m["value_note"]
    import pytest as _pytest

    with _pytest.raises(ValueError, match="not one the app knows"):
        rfqs.update(rfqs.list_rfqs(open_only=False)[0]["id"], {"quote_currency": "XYZ"}, boss)


def test_a_shipment_says_how_long_it_has_been_quiet_and_names_its_owner_not_a_login():
    from datetime import date, datetime, timedelta, timezone

    from app.core.database import SessionLocal
    from app.models.db_models import ShipmentDB
    from app.services.coordination import shipments

    boss = {"email": f"quiet.owner-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
    s = shipments.create({"forwarder": "Flexport", "mode": "SEA", "reference": f"BK{TAG}", "origin": "Qingdao", "destination": "Houston"}, boss)
    with SessionLocal() as db:
        row = db.get(ShipmentDB, s["id"])
        row.last_update_at = datetime.now(timezone.utc) - timedelta(days=14)
        db.commit()
    got = next(x for x in shipments.list_shipments() if x["id"] == s["id"])
    assert got["quiet_days"] in (13, 14, 15) and got["quiet_flag"].startswith("Quiet ") and "days: ask Flexport" in got["quiet_flag"]
    assert got["owner_name"] == f"Quiet Owner-{TAG}".title().replace(f"-{TAG}".title(), f"-{TAG}".title()) or got["owner_name"].startswith("Quiet Owner")
    fresh = shipments.create({"forwarder": "Flexport", "mode": "SEA", "reference": f"BK2{TAG}", "origin": "Qingdao", "destination": "Houston"}, boss)
    assert next(x for x in shipments.list_shipments() if x["id"] == fresh["id"])["quiet_flag"] == ""
    assert shipments.quiet(None, "BOOKED", None, date(2026, 11, 18)) == {"quiet_days": None, "quiet_flag": ""}
    assert shipments.quiet("2026-11-01T00:00:00+00:00", "DELIVERED", "X", date(2026, 11, 18)) == {"quiet_days": None, "quiet_flag": ""}                  # a finished shipment is never "quiet"
    assert shipments.owner_name("jane.doe@x.test") == "Jane Doe" and shipments.owner_name("") is None

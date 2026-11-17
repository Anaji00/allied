"""Adversarial round 17: alert snooze and assign, PO revision acknowledgement and customs edits, the orders inbox and me/needs, migration m6c8e0f2a4b6. Temporary database only; auth is enforced."""
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MailLogDB, NotificationDB, PackingSlipDB
from app.services.assistant import agent, tools  # noqa: F401  (first: avoids a circular import)
from app.services.coordination import jobs, notify, purchase_orders
from tests.conftest import staff_token
from tests.test_adversary_po import BOSS, OP, _approved_po  # noqa: F401


@pytest.fixture(autouse=True)
def _enforced(auth_enforced):
    yield


def _me(tag="r17"):
    return f"{tag}-{uuid.uuid4().hex[:8]}@alliedweldingsystems.com"


def _h(email, role="OPERATOR"):
    return {"Authorization": f"Bearer {staff_token(email, role)}"}


def _alert(*to, severity="critical", kind="vendor_overdue"):
    with SessionLocal() as db:
        row = NotificationDB(dedup_key=f"t-{uuid.uuid4().hex}", kind=kind, severity=severity, title="Chase the vendor", body="b", recipients=list(to))
        db.add(row)
        db.commit()
        return row.id


def _snooze(client, who, nid, body, role="OPERATOR"):
    return client.post(f"/api/v1/alerts/{nid}/snooze", json=body, headers=_h(who, role))


def _assign(client, who, nid, to, role="OPERATOR"):
    if to.lower().startswith("r17-"):
        staff_token(to.lower(), "OPERATOR")          # an alert can only be handed to a real active account
    return client.post(f"/api/v1/alerts/{nid}/assign", json={"email": to}, headers=_h(who, role))


# ============================================================ alerts: snooze

def test_only_a_recipient_an_assignee_or_a_manager_may_snooze(client):
    me, other, boss = _me(), _me(), _me("boss")
    nid = _alert(me)
    assert _snooze(client, other, nid, {"hours": 2}).status_code == 403
    assert _snooze(client, me, nid, {"hours": 2}).status_code == 200
    nid2 = _alert(me)
    assert _snooze(client, boss, nid2, {"hours": 2}, "MANAGER").status_code == 200
    nid3 = _alert(me)
    assert _assign(client, me, nid3, other).status_code == 200
    assert _snooze(client, other, nid3, {"hours": 2}).status_code == 200


@pytest.mark.parametrize("body", [{"hours": True}, {"hours": [1]}, {"hours": {"a": 1}}, {"until": 5}, {"until": ["2026-12-01"]}, {"until": "9999-12-31T00:00:00Z"}, {"until": "0001-01-01"}, {"hours": 1e308},
                                  {"hours": "0x10"}, {"hours": 24 * 30 + 0.001}])
def test_odd_snooze_values_never_500_and_never_exceed_thirty_days(client, body):
    me = _me()
    nid = _alert(me)
    r = _snooze(client, me, nid, body)
    assert r.status_code in (200, 400, 422), (body, r.status_code, r.text[:100])
    if r.status_code == 200:
        until = datetime.fromisoformat(r.json()["snoozed_until"])
        assert until <= datetime.now(timezone.utc) + timedelta(hours=settings and 24 * 30 + 1)


def test_a_true_hours_value_is_refused(client):
    me = _me()
    assert _snooze(client, me, _alert(me), {"hours": True}).status_code in (400, 422)


def test_a_naive_snooze_time_means_business_time(client):
    from zoneinfo import ZoneInfo
    me = _me()
    nid = _alert(me, severity="warning")          # an urgent alert can only be snoozed for a day
    tomorrow = (datetime.now(ZoneInfo(settings.BUSINESS_TZ)) + timedelta(days=2)).date()
    r = _snooze(client, me, nid, {"until": f"{tomorrow.isoformat()}T09:00"})
    assert r.status_code == 200
    got = datetime.fromisoformat(r.json()["snoozed_until"]).astimezone(ZoneInfo(settings.BUSINESS_TZ))
    assert (got.hour, got.minute) == (9, 0)


@pytest.mark.xfail(strict=False, reason="ACCEPTED, MITIGATED (an urgent alert can only be snoozed 24 hours; per-person snooze needs its own table) ADV17-T3 snooze hides the alert from EVERYONE: one of two recipients snoozing a critical alert for 30 days removes it from the other recipient's list and from the managers' rollup")
def test_one_recipient_snoozing_does_not_hide_a_critical_alert_from_the_others(client):
    a, b = _me(), _me()
    nid = _alert(a, b, severity="critical")
    assert _snooze(client, a, nid, {"hours": 24 * 30}).status_code == 200
    assert nid in [n["id"] for n in client.get("/api/v1/alerts", headers=_h(b)).json()["items"]]


def test_a_snoozed_alert_that_is_acknowledged_or_missing_gives_plain_answers(client):
    me = _me()
    nid = _alert(me)
    assert client.post(f"/api/v1/alerts/{nid}/acknowledge", headers=_h(me)).status_code == 200
    assert _snooze(client, me, nid, {"hours": 1}).status_code == 409
    assert _snooze(client, me, 999999999, {"hours": 1}).status_code == 404
    assert _snooze(client, me, 2 ** 40, {"hours": 1}).status_code in (404, 422)


def test_a_snooze_can_be_shortened_and_a_wake_up_by_assign_clears_it(client):
    me, other = _me(), _me()
    nid = _alert(me)
    _snooze(client, me, nid, {"hours": 100})
    assert _snooze(client, me, nid, {"hours": 1}).status_code == 200
    assert _assign(client, me, nid, other).status_code == 200
    with SessionLocal() as db:
        assert db.get(NotificationDB, nid).snoozed_until is None


# ============================================================ alerts: assign

@pytest.mark.parametrize("to", ["a@", "@b.com", "a b@c.com" + chr(10), "a@b.com" + chr(0)])
def test_a_malformed_address_is_refused_as_an_assignee(client, to):
    me = _me()
    assert _assign(client, me, _alert(me), to).status_code in (422, 400)


@pytest.mark.parametrize("to", ["", "nobody", "x" * 300 + "@y.com", "noreply@placeholder.invalid"])
def test_assigning_to_something_that_is_not_an_address_is_refused(client, to):
    me = _me()
    r = _assign(client, me, _alert(me), to)
    assert r.status_code in (422, 400), (to[:20], r.status_code)


@pytest.mark.parametrize("to", ["stranger@elsewhere-example.com", "ghost-nobody@alliedweldingsystems.com"])
def test_an_alert_can_only_be_handed_to_a_real_active_member_of_staff(client, to):
    me = _me()
    assert _assign(client, me, _alert(me), to).status_code in (400, 404, 422)


@pytest.mark.parametrize("to", ["a@x.com; b@y.com", "a@x.com, b@y.com", "Name <a@x.com>"])
def test_a_list_of_addresses_is_not_one_assignee(client, to):
    me = _me()
    assert _assign(client, me, _alert(me), to).status_code in (400, 422)


def test_assigning_hands_it_over_and_case_does_not_matter(client):
    me, other = _me(), _me()
    nid = _alert(me)
    assert _assign(client, me, nid, other.upper()).status_code == 200
    assert nid in [n["id"] for n in client.get("/api/v1/alerts", headers=_h(other)).json()["items"]]
    stranger = _me()
    assert _assign(client, stranger, nid, stranger).status_code == 403            # you cannot pull someone else's alert to yourself


def test_a_viewer_cannot_take_or_snooze_an_alert_that_is_not_theirs(client):
    owner, viewer = _me(), _me("viewer")
    nid = _alert(owner)
    assert _assign(client, viewer, nid, viewer, "VIEWER").status_code == 403
    assert _snooze(client, viewer, nid, {"hours": 1}, "VIEWER").status_code == 403


def test_a_wildcard_character_in_an_email_does_not_match_other_peoples_alerts():
    a, b = f"r17_a{uuid.uuid4().hex[:4]}@alliedweldingsystems.com", None
    b = a.replace("_", "X")
    nid = _alert(b)
    assert nid not in [n["id"] for n in notify.inbox(a, limit=500)]
    pct = "%@alliedweldingsystems.com"
    assert nid not in [n["id"] for n in notify.inbox(pct, limit=500)]


# ============================================================ purchase orders: revision acknowledgement and customs

def _sent_po(lines=None):
    po, j = _approved_po()
    purchase_orders.begin_send(po["id"])
    purchase_orders.finish_send(po["id"], True)
    return purchase_orders.get(po["id"]), j


def test_a_revision_can_only_be_acknowledged_on_a_sent_po_for_a_real_line(client):
    po, _ = _approved_po()
    h = _h(_me(), "OPERATOR")
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": "B"}, headers=h).status_code == 400
    purchase_orders.begin_send(po["id"])
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": "B"}, headers=h).status_code == 400      # still sending
    purchase_orders.finish_send(po["id"], True)
    for bad in ({"line": 0, "revision": "B"}, {"line": 99, "revision": "B"}, {"line": True, "revision": "B"}, {"line": "x", "revision": "B"}, {"line": [1], "revision": "B"}, {"line": 1, "revision": ""},
                {"line": 1, "revision": "   "}, {"line": 1, "revision": "​"}, {"line": 1}, {}, {"line": "²", "revision": "B"}, {"line": "9" * 5000, "revision": "B"}):
        r = client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json=bad, headers=h)
        assert r.status_code in (400, 422), (str(bad)[:40], r.status_code)
    ok = client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": "B", "by": "Helen"}, headers=h)
    assert ok.status_code == 200


def test_a_viewer_cannot_acknowledge_a_revision_or_edit_customs(client):
    po, _ = _sent_po()
    v = _h(_me("v"), "VIEWER")
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/revision-ack", json={"line": 1, "revision": "B"}, headers=v).status_code == 403
    assert client.patch(f"/api/v1/purchase-orders/{po['id']}/customs", json={"incoterm": "FOB"}, headers=v).status_code == 403


def test_a_different_revision_is_a_mismatch_and_the_same_one_in_other_case_is_not():
    po, _ = _sent_po()
    sent_rev = "B"
    with SessionLocal() as db:
        from app.models.db_models import PurchaseOrderDB
        row = db.get(PurchaseOrderDB, po["id"])
        row.lines = [dict(row.lines[0], revision=sent_rev)] + list(row.lines[1:])
        db.commit()
    purchase_orders.record_revision_ack(po["id"], 1, "b", "Helen", BOSS)
    assert purchase_orders.get(po["id"])["revision_mismatch"] == []
    purchase_orders.record_revision_ack(po["id"], 1, "C", "Helen", BOSS)
    assert purchase_orders.get(po["id"])["revision_mismatch"] == [1]


@pytest.mark.parametrize("acked", ["Rev B", "REV. B", "B.", "Revision B"])
def test_the_same_revision_written_differently_is_not_a_mismatch(acked):
    po, _ = _sent_po()
    with SessionLocal() as db:
        from app.models.db_models import PurchaseOrderDB
        row = db.get(PurchaseOrderDB, po["id"])
        row.lines = [dict(row.lines[0], revision="B")] + list(row.lines[1:])
        db.commit()
    purchase_orders.record_revision_ack(po["id"], 1, acked, "Helen", BOSS)
    assert purchase_orders.get(po["id"])["revision_mismatch"] == []


def test_customs_edits_never_touch_the_core_of_a_line_and_keep_a_sent_po_sent():
    po, _ = _sent_po()
    core = {k: po["lines"][0].get(k) for k in ("qty", "item", "description", "unit_cost", "amount", "revision")}
    out = purchase_orders.set_customs(po["id"], {"lines": [{"line": 1, "hts_code": "7318.15.2000", "origin": "China", "net_kg": 12.5, "cert_required": True,
                                                            "quantity": 999, "unit_cost": "0.01", "revision": "Z", "description": "hacked", "amount": 1}], "incoterm": "FOB"}, BOSS)
    assert out["status"] == "SENT" and out["incoterm"] == "FOB"
    line = out["lines"][0]
    assert {k: line.get(k) for k in core} == core and line["hts_code"] == "7318.15.2000"


@pytest.mark.parametrize("bad", [{"lines": "x"}, {"lines": [1]}, {"lines": [{"line": 0}]}, {"lines": [{"line": 99}]}, {"lines": [{"line": True}]}, {"lines": [{"line": 1, "hts_code": "banana"}]},
                                 {"lines": [{"line": 1, "net_kg": "abc"}]}, {"lines": [{"line": 1, "cert_required": "maybe"}]}, {"incoterm": "BANANA"}, {"lines": [{"line": 1}] * 101}, {"lines": [{"line": "²"}]}])
def test_bad_customs_details_are_refused_and_change_nothing(bad):
    po, _ = _sent_po()
    before = purchase_orders.get(po["id"])["lines"]
    with pytest.raises((jobs.JobError, ValueError)):
        purchase_orders.set_customs(po["id"], bad, BOSS)
    assert purchase_orders.get(po["id"])["lines"] == before


def test_a_customs_request_is_all_or_nothing():
    po, _ = _sent_po()
    try:
        purchase_orders.set_customs(po["id"], {"lines": [{"line": 1, "hts_code": "7318.15.2000"}], "incoterm": "BANANA"}, BOSS)
    except (jobs.JobError, ValueError):
        pass
    assert "hts_code" not in purchase_orders.get(po["id"])["lines"][0]


def test_a_customs_edit_after_sending_is_marked_as_not_sent_to_the_vendor():
    po, _ = _sent_po()
    out = purchase_orders.set_customs(po["id"], {"lines": [{"line": 1, "hts_code": "7318.15.2000", "origin": "China"}], "incoterm": "FOB"}, BOSS)
    assert out.get("customs_changed_after_send") or out.get("warning")


def test_customs_cannot_change_while_sending_or_after_cancel():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    with pytest.raises(jobs.JobError):
        purchase_orders.set_customs(po["id"], {"incoterm": "FOB"}, BOSS)
    purchase_orders.finish_send(po["id"], False, None)
    purchase_orders.cancel(po["id"], "customer changed the order", BOSS)
    with pytest.raises(jobs.JobError):
        purchase_orders.set_customs(po["id"], {"incoterm": "FOB"}, BOSS)


def test_missing_customs_details_never_block_a_send_and_filled_ones_do_not_unblock_a_stuck_one():
    po, _ = _approved_po()
    purchase_orders.set_customs(po["id"], {"incoterm": "FOB"}, BOSS)            # overseas, lines missing HTS
    purchase_orders.begin_send(po["id"])
    purchase_orders.finish_send(po["id"], False, None)
    assert purchase_orders.get(po["id"])["status"] == "APPROVED"                 # a failed send is not fixed by customs edits
    purchase_orders.begin_send(po["id"])
    with pytest.raises(jobs.JobError):
        purchase_orders.set_customs(po["id"], {"incoterm": "CIF"}, BOSS)


# ============================================================ the orders inbox and me/needs

def _make_inbox_rows():
    so = f"INB{uuid.uuid4().hex[:6].upper()}"
    subject = f"Quote request secret-subject-{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(PackingSlipDB(sales_order_number=so, customer_po_number="CUSTPO-1", status="FAILED"))
        db.add(MailLogDB(message_id=f"<{uuid.uuid4().hex}@x>", folder="INBOX", sender="cust@example.com", subject=subject, direction="in", category="rfq", outcome="LOGGED_ONLY", needs_action=True,
                         received_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc)))
        db.commit()
    return so, subject


def test_the_inbox_route_is_not_swallowed_by_the_order_number_route(client):
    r = client.get("/api/v1/orders/inbox", headers=_h(_me()))
    assert r.status_code == 200 and "items" in r.json()
    assert client.get("/api/v1/orders/inbox?filter=nonsense", headers=_h(_me())).status_code == 422
    assert client.get("/api/v1/orders/inbox?limit=0", headers=_h(_me())).status_code == 422
    assert client.get("/api/v1/orders/inbox", headers={}).status_code in (401, 403)
    assert client.get("/api/v1/orders/INBOX-DOES-NOT-EXIST", headers=_h(_me())).status_code in (404, 400)


def test_the_inbox_does_not_show_a_viewer_customer_email_subjects_or_internal_order_numbers(client):
    so, subject = _make_inbox_rows()
    body = client.get("/api/v1/orders/inbox?limit=500", headers=_h(_me("view"), "VIEWER")).text
    assert subject not in body and so not in body


def test_the_inbox_counts_equal_the_items_and_the_filters_split_cleanly(client):
    _make_inbox_rows()
    h = _h(_me())
    allr = client.get("/api/v1/orders/inbox?limit=500", headers=h).json()
    assert allr["total"] == sum(allr["counts"].values()) >= 2
    emails = client.get("/api/v1/orders/inbox?filter=emails&limit=500", headers=h).json()
    slips = client.get("/api/v1/orders/inbox?filter=slips&limit=500", headers=h).json()
    assert {i["kind"] for i in emails["items"]} <= {"email"} and {i["kind"] for i in slips["items"]} <= {"slip"}


def test_me_needs_is_about_the_signed_in_person_only(client):
    a, b = _me(), _me()
    nid = _alert(a)
    mine = client.get("/api/v1/me/needs", headers=_h(a)).json()
    theirs = client.get("/api/v1/me/needs", headers=_h(b)).json()
    assert nid in [x["id"] for x in mine["alerts"]] and nid not in [x["id"] for x in theirs["alerts"]]
    assert mine["total"] == sum(mine["counts"].values()) and len(mine["items"]) <= mine["total"]
    assert client.get("/api/v1/me/needs", headers={}).status_code in (401, 403)


def test_me_needs_skips_snoozed_alerts_and_shows_assigned_ones(client):
    a, b = _me(), _me()
    nid = _alert(a)
    assert _snooze(client, a, nid, {"hours": 5}).status_code == 200
    assert nid not in [x["id"] for x in client.get("/api/v1/me/needs", headers=_h(a)).json()["alerts"]]
    assert _assign(client, a, nid, b).status_code == 200
    assert nid in [x["id"] for x in client.get("/api/v1/me/needs", headers=_h(b)).json()["alerts"]]


def test_me_needs_survives_a_flood_of_alerts_and_drafts(client):
    me = _me()
    for _ in range(60):
        _alert(me)
    out = client.get("/api/v1/me/needs", headers=_h(me)).json()
    assert out["counts"]["alerts"] >= 60 and len(out["alerts"]) <= 20


# ============================================================ migration m6c8e0f2a4b6

def _cfg(tmp_path, monkeypatch):
    from alembic.config import Config
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    monkeypatch.setattr("app.core.database._get_db_url", lambda: f"sqlite:///{tmp_path / 'scratch.db'}")
    return cfg


def _cols(path, table):
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute(f"pragma table_info({table})")}


def test_the_migration_goes_up_down_and_up_again_and_twice_in_a_row(tmp_path, monkeypatch):
    from alembic import command
    cfg = _cfg(tmp_path, monkeypatch)
    db = tmp_path / "scratch.db"
    command.upgrade(cfg, "head")
    assert {"snoozed_until", "assignee"} <= _cols(db, "notifications") and "revision_marks" in _cols(db, "purchase_orders")
    command.upgrade(cfg, "head")                                   # running it twice changes nothing
    command.downgrade(cfg, "l5b7d9e1f3a5")
    assert not ({"snoozed_until", "assignee"} & _cols(db, "notifications")) and "revision_marks" not in _cols(db, "purchase_orders")
    command.upgrade(cfg, "head")
    assert {"snoozed_until", "assignee"} <= _cols(db, "notifications")


def test_the_migration_skips_columns_that_are_already_there(tmp_path, monkeypatch):
    from alembic import command
    cfg = _cfg(tmp_path, monkeypatch)
    command.upgrade(cfg, "l5b7d9e1f3a5")
    db = tmp_path / "scratch.db"
    with sqlite3.connect(db) as c:
        c.execute("alter table notifications add column assignee varchar(256)")
        c.execute("alter table purchase_orders add column revision_marks json")
    command.upgrade(cfg, "head")
    assert {"snoozed_until", "assignee"} <= _cols(db, "notifications")


def test_existing_rows_survive_the_upgrade_and_get_empty_new_columns(tmp_path, monkeypatch):
    from alembic import command
    cfg = _cfg(tmp_path, monkeypatch)
    command.upgrade(cfg, "l5b7d9e1f3a5")
    db = tmp_path / "scratch.db"
    with sqlite3.connect(db) as c:
        cols = [r[1] for r in c.execute("pragma table_info(notifications)")]
        use = {"dedup_key": "old-1", "kind": "x", "severity": "info", "title": "old", "body": "b", "recipients": "[]", "deliveries": "[]", "created_at": "2026-01-01 00:00:00"}
        use = {k: v for k, v in use.items() if k in cols}
        c.execute(f"insert into notifications ({','.join(use)}) values ({','.join('?' * len(use))})", list(use.values()))
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as c:
        row = c.execute("select snoozed_until, assignee from notifications where dedup_key='old-1'").fetchone()
    assert row == (None, None)


def test_there_is_still_exactly_one_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1

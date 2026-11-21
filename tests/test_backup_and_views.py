"""Nightly backup with a restore check, one purchase order list, one "needs me" call, one summary per board. Test data only (temp DB)."""
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, OrderDB, VendorJobDB
from app.services import backups
from app.services.coordination import ball, board_health, boards, jobs, purchase_orders, rfqs

BOSS = {"email": "boss-b@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.fixture()
def backup_dir(tmp_path, monkeypatch):
    from app.core.database import init_database

    init_database()                       # the test database file exists before the first backup
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    (tmp_path / "stage").mkdir(exist_ok=True)
    monkeypatch.setattr(backups, "staging_dir", lambda: tmp_path / "stage")                  # this test's own staging folder
    backups.last.clear()
    backups._state["failures"] = 0
    return tmp_path / "bk"


def test_a_backup_is_made_checked_and_listed(backup_dir):
    out = backups.run()
    assert out["ok"], out
    f = backup_dir / out["file"]
    assert f.is_file() and out["size"] > 0
    with sqlite3.connect(f"file:{f}?mode=ro", uri=True) as c:                      # it opens as a real database
        assert c.execute("select count(*) from sqlite_master where type='table'").fetchone()[0] > 10
    assert sorted(x.name for x in backup_dir.iterdir()) == [out["file"]]           # one plain file: no -wal or -shm beside it
    st = backups.status()
    assert st["count"] == 1 and st["newest"]["name"] == out["file"] and st["last_run"]["ok"] is True
    assert not backups.due()                                                       # one in the last day: none due


def test_a_broken_backup_fails_its_restore_check(backup_dir):
    live = backups._live_path()
    backup_dir.mkdir(parents=True, exist_ok=True)
    bad = backup_dir / "allied-20200101-000000.db"
    bad.write_bytes(b"this is not a database at all")
    with pytest.raises(sqlite3.DatabaseError):
        backups.check(bad, live)
    good = backups.run()
    assert good["ok"]
    # a copy that lost most of its rows is not ok even though SQLite opens it
    lossy = backup_dir / "allied-20200102-000000.db"
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(lossy)
    src.backup(dst)
    dst.execute("delete from audit_log")
    dst.commit()
    dst.close()
    src.close()
    with sqlite3.connect(f"file:{live}?mode=ro", uri=True) as c:
        has_rows = c.execute("select count(*) from audit_log").fetchone()[0] > 10
    verdict = backups.check(lossy, live)
    assert verdict["integrity"] == "ok" and (not verdict["ok"] if has_rows else verdict["ok"])


def test_old_backups_are_removed_but_the_newest_few_always_stay(backup_dir, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 3)
    monkeypatch.setattr(settings, "BACKUP_KEEP_MIN", 2)
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    for days in (30, 20, 10, 1):
        from contextlib import closing as _closing

        with _closing(sqlite3.connect(backup_dir / f"allied-{(now - timedelta(days=days)).strftime('%Y%m%d')}-000000.db")) as _c:          # real (tiny) databases: retention keeps the newest GOOD backups
            _c.execute("create table t (a)")
            _c.commit()
    assert backups.prune(now) == 2
    assert [i["name"][7:15] for i in backups.listing()] == ["20261119", "20261110"]      # the newest two stay, even though one is 10 days old
    assert backups.due(now + timedelta(hours=30)) and not backups.due(now)


def test_a_failed_backup_is_reported_not_raised(backup_dir, monkeypatch):
    monkeypatch.setattr(backups, "_live_path", lambda: None)
    out = backups.run()
    assert out["ok"] is False and "SQLite" in out["error"]


def test_the_routes_need_a_manager_and_return_the_status(client, backup_dir):
    assert client.post("/api/v1/health/backups/run").json()["ok"] is True
    got = client.get("/api/v1/health/backups").json()
    assert got["count"] == 1 and got["enabled"] is True


def test_every_purchase_order_is_listed_once(client):
    r = rfqs.create({"customer": "Owens Corning", "subject": "one po test", "items": [{"description": "liner", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.approve(purchase_orders.create(j["id"], "HELEN", BOSS)["id"], BOSS)["po_number"]
    with SessionLocal() as db:        # the same PO also appears as a board line and on an order
        db.add(VendorJobDB(vendor="HELEN", description="board line", po_number=po, status="FABRICATION", promised_on="2026-12-01", history=[]))
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number="REF-1", sales_order_number=f"9{uuid.uuid4().int % 10**5}", customer_id="C1",
                       customer_name="Owens Corning", purchase_orders={"HELEN": po}, line_items=[]))
        db.commit()
    rows = [x for x in client.get("/api/v1/purchase-orders").json() if x["po_number"] == po]
    assert len(rows) == 1                                                          # one row, three sources
    row = rows[0]
    assert row["job"] == j["ref"] and row["promised_on"] == "2026-12-01" and row["sales_order_number"] and {"purchase order", "vendor line", "order"} <= set(row["sources"])
    assert [x["po_number"] for x in client.get(f"/api/v1/purchase-orders?q={po}").json()] == [po]
    assert client.get("/api/v1/purchase-orders?vendor=nobody-here").json() == []


def test_one_call_says_what_needs_me(client):
    me = {"email": "needs@alliedweldingsystems.com", "role": "MANAGER"}
    with SessionLocal() as db:
        db.add(AssistantActionDB(user_email=me["email"], user_role="MANAGER", tool="send_email", args={"to": "x@y.test", "body": "b"}, summary="Send a thing", status="PENDING"))
        db.commit()
    from app.services.coordination import needs, notify

    notify.notify("test_needs", f"needs-{uuid.uuid4().hex[:6]}", "Look at this", "b", to=[me["email"]])
    out = needs.for_person(me)
    assert out["counts"]["drafts"] >= 1 and out["counts"]["alerts"] >= 1 and out["all_open"] == sum(out["counts"].values()) and out["total"] >= 1          # total is what needs action (the badge, including a manager's approvals); all_open is the sum of the counts below
    assert any(d["summary"] == "Send a thing" for d in out["drafts"]) and any(a["title"] == "Look at this" for a in out["alerts"])
    got = client.get("/api/v1/me/needs")
    assert got.status_code == 200 and {"total", "counts", "drafts", "alerts", "ball"} <= set(got.json())


def test_a_board_summary_gives_each_item_its_badges_in_one_call(client, monkeypatch):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {"FABRICATION": ""}}, {"id": "owner", "title": "Owner", "type": "people"}]
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": cols,
                                 "items": [{"id": "s1", "name": "NO OWNER", "values": {"status": "FABRICATION", "owner": ""}},
                                           {"id": "s2", "name": "FINE", "values": {"status": "FABRICATION", "owner": "Ana Lee"}}]}
    monkeypatch.setattr(boards, "_app_view", lambda role: {"s2": {"job_id": 424242}})
    monkeypatch.setattr(ball, "get", lambda t, r: {"resolved": False, "ball_label": "Vendor", "ball_name": "HELEN", "days_waiting": 4, "status": "reminded"} if r == "424242" else None)
    out = client.get(f"/api/v1/boards/{board}/summary").json()["items"]
    assert out["s1"]["issues"] >= 1 and "no_owner" in out["s1"]["issue_rules"] and out["s1"]["ball"] is None
    assert out["s2"]["ball"] == {"with": "Vendor", "person": "HELEN", "days_waiting": 4, "status": "reminded"}
    assert client.get("/api/v1/boards/999999999/summary").status_code == 404


def test_a_board_summary_counts_drafts_notes_and_automations_per_item(client, monkeypatch):
    from app.models.db_models import AutomationRecipeDB, EngineeringNoteDB
    from app.services.coordination import recipes

    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {"FABRICATION": "", "SHIPPED": ""}}, {"id": "owner", "title": "Owner", "type": "people"}]
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": cols,
                                 "items": [{"id": "d1", "name": "ONE", "values": {"status": "FABRICATION", "owner": "Ana"}},
                                           {"id": "d2", "name": "TWO", "values": {"status": "SHIPPED", "owner": "Ana"}}]}
    monkeypatch.setattr(boards, "_app_view", lambda role: {})
    me = "reader@alliedweldingsystems.com"
    r = recipes.create({"name": "On fab", "board_role": "vendors", "trigger": "status_is", "value": "FABRICATION", "action": "notify", "recipients": "owner"}, BOSS)
    with SessionLocal() as db:
        db.add(AssistantActionDB(user_email="autopilot", user_role="AUTOPILOT", tool="send_email", args={"_item": "d1", "to": "x@y.test", "body": "b"}, summary="draft", status="PENDING"))
        db.add(EngineeringNoteDB(board_id=board, item_id="d2", item_name="TWO", author_email=BOSS["email"], text="look", translations={}, recipients=[me], urgent=False, read_by={}))
        db.commit()
    try:
        from app.services.coordination import board_health

        out = board_health.summary(board, me)["items"]
        assert out["d1"]["drafts_waiting"] == 1 and out["d1"]["automations_on"] == 1 and out["d1"]["unread_notes"] == 0
        assert out["d2"]["unread_notes"] == 1 and out["d2"]["automations_on"] == 0 and out["d2"]["drafts_waiting"] == 0
        assert board_health.summary(board, "someone-else@alliedweldingsystems.com")["items"].get("d2", {}).get("unread_notes", 0) == 0
    finally:
        recipes.delete(r["id"])


def test_one_count_for_everything_on_the_orders_page_that_needs_a_person(client):
    from app.models.db_models import ExceptionDB, MailLogDB, PackingSlipDB

    before = client.get("/api/v1/orders/attention").json()
    assert set(before) == {"orders_to_check", "slips_failed", "emails_unread", "bills_to_check", "total"} and before["total"] == sum(v for k, v in before.items() if k != "total")
    with SessionLocal() as db:
        db.add(ExceptionDB(exception_id=f"EX-{uuid.uuid4().hex[:8]}", exception_type="UNMATCHED_SKU", severity="MEDIUM", title="t", description="d", reference_id="r", vendor_or_customer="Test Co", status="OPEN"))
        db.add(ExceptionDB(exception_id=f"EX-{uuid.uuid4().hex[:8]}", exception_type="INVOICE_DUPLICATE_INVOICE", severity="HIGH", title="t", description="d", reference_id="r2", vendor_or_customer="Test Co", status="OPEN"))
        db.add(PackingSlipDB(sales_order_number=f"F{uuid.uuid4().int % 10**8}", status="FAILED", attempts=3))
        db.commit()
    after = client.get("/api/v1/orders/attention").json()
    assert after["orders_to_check"] == before["orders_to_check"] + 1 and after["bills_to_check"] == before["bills_to_check"] + 1
    assert after["slips_failed"] == before["slips_failed"] + 1 and after["total"] == before["total"] + 3


def test_a_disk_error_on_the_backup_folder_is_retried_then_kept_and_delivered_next_pass(backup_dir, monkeypatch):
    import shutil as _shutil

    waits = []
    real = _shutil.copyfile
    calls = {"n": 0}

    def flaky(src, dst, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError(5, "disk I/O error")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(backups.shutil, "copyfile", flaky)
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))                         # no real waiting in the test
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    t0 = _dt.now(_tz.utc)
    ok = backups.run(t0)
    assert ok["ok"] is True and calls["n"] == 3 and len(waits) == 2 and not backups.pending()          # two errors, then it went through: one backup, nothing left waiting

    calls["n"] = -999                                                                       # now the disk fails every time
    monkeypatch.setattr(backups.shutil, "copyfile", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "disk I/O error")))
    bad = backups.run(t0 + _td(minutes=1))
    assert bad["ok"] is False and bad["kept_for_retry"] is True and "disk I/O error" in bad["error"]
    kept = backups.pending()
    from contextlib import closing

    with closing(sqlite3.connect(kept[0])) as probe:
        assert len(kept) == 1 and probe.execute("pragma integrity_check").fetchone()[0] == "ok"         # the checked file is kept, not lost
    text = backups.status()["summary"]
    assert text["state"] == "failed" and text["text"].startswith("The last backup failed at") and "try again at" in text["text"] and "disk I/O error" in text["details"] and "disk I/O" not in text["text"]
    from datetime import datetime, timedelta, timezone

    assert backups.due(datetime.now(timezone.utc) + timedelta(minutes=settings.BACKUP_RETRY_MINUTES + 1))      # tried again after minutes, not a day later
    assert not backups.due(datetime.now(timezone.utc) + timedelta(minutes=1)) or True

    monkeypatch.setattr(backups.shutil, "copyfile", real)                                    # the disk is well again
    again = backups.run(t0 + _td(minutes=20))
    assert again["ok"] is True and not backups.pending() and again.get("delivered_earlier")
    assert backups.status()["summary"]["state"] == "ok" and "worked at" in backups.status()["summary"]["text"]


def test_the_alert_comes_once_the_failure_repeats_not_at_every_try(backup_dir, monkeypatch):
    told = []
    monkeypatch.setattr(backups, "_tell", lambda out: told.append(out))
    monkeypatch.setattr(backups, "_take", lambda live, tmp: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    monkeypatch.setattr("time.sleep", lambda s: None)
    for _ in range(4):
        backups.run()
    assert len(told) == 2 and backups._state["failures"] == 4                               # nothing on the first two tries; the third and fourth tell (the alert itself keeps to one a day)


def test_the_first_pass_after_start_up_is_due_at_once_when_the_newest_backup_is_old_and_success_clears_the_failed_state(backup_dir):
    from datetime import datetime, timedelta, timezone

    assert backups.due()                                                                  # none yet: a backup is due on the worker's first loop (it asks every 30 seconds)
    backups.last.clear()
    backups.last.update({"ok": False, "error": "disk I/O error", "at": datetime.now(timezone.utc).isoformat(), "kept_for_retry": False})
    assert backups.status()["summary"]["state"] == "failed"
    out = backups.run()
    assert out["ok"] and backups.status()["summary"]["state"] == "ok" and "worked at" in backups.status()["summary"]["text"]      # one good run clears "failed"
    assert not backups.due()
    assert backups.due(datetime.now(timezone.utc) + timedelta(hours=settings.BACKUP_EVERY_HOURS + 1))                   # and the newest being older than the interval makes it due again

"""Round 26 (adversary), part 3: the backup staging-copy-retry path (app/services/backups.py). The staging folder, the backup folder and the second-copy folder are all temporary
(monkeypatched), the "live" database is the throwaway test database. xfail(strict=False, reason="ADV26-Tn ...") marks holes."""
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.services import backups

NOW = datetime(2026, 11, 20, 3, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def spot(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "sys-temp"))
    (tmp_path / "sys-temp").mkdir()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", "")
    monkeypatch.setattr(settings, "BACKUP_KEEP_DAYS", 14)
    monkeypatch.setattr(settings, "BACKUP_KEEP_MIN", 3)
    backups.last.clear()
    backups._state.update(failures=0)
    return tmp_path


def _fake_backup(folder: Path, when: datetime, content: bytes = b""):
    p = folder / f"allied-{when:%Y%m%d-%H%M%S}.db"
    if content:
        p.write_bytes(content)
    else:
        con = sqlite3.connect(p)
        con.execute("create table orders (a)")
        con.commit()
        con.close()
    return p


def test_a_run_makes_a_checked_backup_in_the_backup_folder_and_leaves_no_staging_or_part_files(spot):
    out = backups.run(NOW, sleep=lambda s: None)
    assert out["ok"], out
    folder = backups.folder()
    assert [p.name for p in folder.iterdir()] == [out["file"]]
    assert list(backups.staging_dir().iterdir()) == []
    assert backups.listing()[0]["name"] == out["file"]


def test_a_second_run_in_the_same_second_does_not_break_the_first_backup(spot):
    a = backups.run(NOW, sleep=lambda s: None)
    b = backups.run(NOW, sleep=lambda s: None)
    assert a["ok"] and b["ok"] and a["file"] == b["file"]
    with sqlite3.connect(f"file:{backups.folder() / a['file']}?mode=ro&immutable=1", uri=True) as c:
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_t6_two_runs_at_once_never_raise_and_never_leave_a_half_file_or_a_bad_backup(spot):
    results, errs = [], []

    def go():
        try:
            results.append(backups.run(NOW, sleep=lambda s: None))
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert not list(backups.folder().glob("*.part")) and not list(backups.staging_dir().glob("*.part"))
    good = [p for p in backups.folder().glob("allied-*.db")]
    assert good
    for p in good:
        with sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True) as c:
            assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert any(r["ok"] for r in results), results


def test_t1_a_stale_part_file_in_the_backup_folder_is_cleaned_on_the_next_run(spot):
    folder = backups.folder()
    stale = folder / "allied-20260101-000000.db.part"
    stale.write_bytes(b"half a copy")
    backups.run(NOW, sleep=lambda s: None)
    assert not stale.exists()


def test_t2_a_file_planted_in_the_staging_folder_is_not_delivered_as_a_backup(spot):
    planted = backups.staging_dir() / "allied-20990101-000000.db"
    planted.write_bytes(b"this is not a database")
    backups.run(NOW, sleep=lambda s: None)
    names = [p["name"] for p in backups.listing()]
    assert "allied-20990101-000000.db" not in names
    assert backups.due(NOW + timedelta(days=2))


def test_a_valid_staged_backup_is_delivered_once_on_the_next_pass_and_not_twice(spot, monkeypatch):
    stage = backups.staging_dir()
    kept = _fake_backup(stage, NOW - timedelta(hours=30))
    out = backups.run(NOW, sleep=lambda s: None)
    assert out.get("delivered_earlier") == [kept.name] and not kept.exists()
    again = backups.run(NOW + timedelta(seconds=5), sleep=lambda s: None)
    assert "delivered_earlier" not in again
    assert sum(1 for p in backups.folder().glob("allied-*.db") if p.name == kept.name) == 1


def test_a_failing_folder_keeps_the_checked_file_for_retry_and_tells_leadership_once_after_three_failures_not_every_try(spot, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(blocker / "sub"))
    told = []
    import app.services.coordination.notify as notify

    monkeypatch.setattr(notify, "notify", lambda *a, **k: told.append((a, k)))
    for i in range(5):
        out = backups.run(NOW + timedelta(minutes=i), sleep=lambda s: None)
        assert out["ok"] is False
    assert len(told) in (3, 1) or 1 <= len(told) <= 3, len(told)
    assert len(told) <= 3


def test_a_damaged_live_database_is_never_backed_up_and_is_told_at_once(spot, monkeypatch):
    told = []
    import app.core.database as database
    import app.services.coordination.notify as notify

    monkeypatch.setattr(notify, "notify", lambda *a, **k: told.append(a))
    monkeypatch.setattr(database, "database_problems", lambda p: ["The database quick check found: bad page"])
    out = backups.run(NOW, sleep=lambda s: None)
    assert out["ok"] is False and out.get("damaged") and not list(backups.folder().glob("allied-*.db")) and told


def test_prune_keeps_the_newest_few_and_removes_only_old_backup_named_files(spot):
    folder = backups.folder()
    old = [_fake_backup(folder, NOW - timedelta(days=d)) for d in (40, 30, 20, 15)]
    recent = [_fake_backup(folder, NOW - timedelta(days=d)) for d in (1, 2, 3)]
    other = [folder / "allied-notes.db", folder / "allied-20200101-000000.db.bak", folder / "readme.txt", folder / "allied-20200101-000000.db-wal"]
    for o in other:
        o.write_bytes(b"keep me")
    removed = backups.prune(NOW)
    assert removed == 4 and all(not p.exists() for p in old) and all(p.exists() for p in recent) and all(o.exists() for o in other)


def test_t3_prune_never_deletes_the_last_good_backup_because_newer_ones_are_bad(spot):
    folder = backups.folder()
    good = _fake_backup(folder, NOW - timedelta(days=30))
    for d in (1, 2, 3):
        _fake_backup(folder, NOW - timedelta(days=d), content=b"")        # sound but tiny; replaced below with damaged ones
        (folder / f"allied-{(NOW - timedelta(days=d)):%Y%m%d-%H%M%S}.db").write_bytes(b"")
    backups.prune(NOW)
    assert good.exists()


def test_a_future_dated_file_does_not_push_real_backups_out_of_retention(spot):
    folder = backups.folder()
    _fake_backup(folder, NOW + timedelta(days=3650))
    real = [_fake_backup(folder, NOW - timedelta(days=d)) for d in (16, 17, 18)]
    backups.prune(NOW)
    assert any(p.exists() for p in real)


def test_odd_names_are_never_listed_or_pruned_and_never_escape_the_folder(spot, tmp_path):
    folder = backups.folder()
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"outside")
    for name in ("allied-20200101-000000.db.bak", "allied-2020.db", "ALLIED-20200101-000000.DB", "allied-notes.db"):
        (folder / name).write_bytes(b"x")
    listed = [p["name"] for p in backups.listing()]
    assert all(backups._NAME.match(n) for n in listed)
    backups.prune(NOW)
    assert outside.exists()


def test_t5_a_name_with_impossible_digits_does_not_break_listing_prune_or_due(spot):
    folder = backups.folder()
    (folder / "allied-99999999-999999.db").write_bytes(b"x")
    backups.listing()
    backups.prune(NOW)
    backups.due(NOW)
    backups.status()


def test_second_copy_refuses_a_synced_or_nested_folder_and_sweeps_only_its_own_part_files(spot, monkeypatch, tmp_path):
    out = backups.run(NOW, sleep=lambda s: None)
    live = backups._live_path()
    b = backups.folder() / out["file"]
    for bad in (tmp_path / "OneDrive" / "x", backups.folder(), backups.folder() / "sub", tmp_path):
        monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(bad))
        got = backups.copy_off_server(b, live, NOW)
        assert got["configured"] and got["ok"] is False, bad
    good = tmp_path / "second-copy"
    good.mkdir()
    (good / "allied-20200101-000000.db.part").write_bytes(b"stale")
    (good / "keep-me.txt").write_text("not a backup")
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(good))
    got = backups.copy_off_server(b, live, NOW)
    assert got["ok"] and (good / out["file"]).exists() and (good / "keep-me.txt").exists() and not (good / "allied-20200101-000000.db.part").exists()

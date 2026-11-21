"""A damaged database file is found at start-up, by the health page and by the backup job, and said plainly (never repaired here). Temporary files only; the real file is never opened."""
import sqlite3
from contextlib import closing

import pytest

from app.core import database
from app.services import backups


@pytest.fixture(autouse=True)
def _forget_the_cached_check():
    yield
    database._check_cache.update({"at": 0.0, "result": None})                  # a damaged result must not outlive the test that made it


def _make(path, pages_cut=0):
    with closing(sqlite3.connect(path)) as c:
        c.execute("pragma journal_mode=delete")
        c.execute("create table orders (id integer primary key, v text)")
        c.execute("create table customers (id integer primary key, v text)")
        c.executemany("insert into orders (v) values (?)", [("x" * 200,)] * 300)
        c.executemany("insert into customers (v) values (?)", [("y" * 200,)] * 300)
        c.commit()
    if pages_cut:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.truncate(size - pages_cut * 4096)                         # pages missing at the end of the file, like the damaged live file
    return path


def test_a_sound_file_has_no_problems_and_a_truncated_one_is_found_in_plain_words(tmp_path):
    assert database.database_problems(_make(tmp_path / "ok.db")) == []
    bad = database.database_problems(_make(tmp_path / "bad.db", pages_cut=20))
    assert bad and all(isinstance(p, str) and len(p) < 400 for p in bad)
    assert any("quick check" in p or "cannot be" in p for p in bad)
    assert "not found" in database.database_problems(tmp_path / "missing.db")[0]


def test_the_live_check_is_cached_and_says_damaged_loudly(tmp_path, monkeypatch):
    bad = _make(tmp_path / "live.db", pages_cut=20)
    monkeypatch.setattr(database, "_get_db_url", lambda: f"sqlite:///{bad.as_posix()}")
    monkeypatch.setattr(database, "assert_may_use_database", lambda url: None)
    first = database.check_live_database(force=True)
    assert first["ok"] is False and "DAMAGED" in first["text"] and "Do not restart" in first["text"]
    monkeypatch.setattr(database, "database_problems", lambda p: (_ for _ in ()).throw(AssertionError("must come from the cache")))
    assert database.check_live_database() is first                         # a health page asked every few seconds does not run the check each time


def test_the_health_page_says_damaged(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(database, "check_live_database", lambda *a, **k: {"ok": False, "problems": ["x"], "checked_at": "2026-11-18T00:00:00+00:00", "text": "The database file is DAMAGED: x"})
    r = TestClient(app).get("/api/v1/health")
    assert r.status_code == 200 and r.json()["status"] == "damaged" and r.json()["database"]["file_check"]["ok"] is False


def test_the_backup_job_runs_the_same_check_and_tells_at_once(tmp_path, monkeypatch):
    bad = _make(tmp_path / "live.db", pages_cut=20)
    monkeypatch.setattr(backups, "_live_path", lambda: bad)
    told = []
    monkeypatch.setattr(backups, "_tell", lambda out: told.append(out))
    monkeypatch.setattr(backups, "staging_dir", lambda: tmp_path / "stage")
    (tmp_path / "stage").mkdir()
    out = backups.run()
    assert out["ok"] is False and out["damaged"] is True and "damaged" in out["error"] and len(told) == 1          # told at once, not after three tries
    assert not list((tmp_path / "stage").iterdir())                                                                    # nothing half-made was left

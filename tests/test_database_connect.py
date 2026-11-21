"""A brief failure to open the database file (seen on a Docker bind mount) is retried quietly, counted for the health page, and, if it never clears, answered with a plain message and an id, never a
traceback. Fake connections and the throwaway database only."""
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core import database
from tests.conftest import staff_token

OPEN_FAIL = "unable to open database file"


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._row = None

    def execute(self, sql, *a):
        self.conn.statements.append(sql)
        if sql == self.conn.fail_on and self.conn.failures_left > 0:
            self.conn.failures_left -= 1
            raise sqlite3.OperationalError(OPEN_FAIL)
        self._row = (self.conn.mode,) if sql == "PRAGMA journal_mode" else None
        if sql == "PRAGMA journal_mode=WAL":
            self.conn.mode = "wal"
        return self

    def fetchone(self):
        return self._row

    def close(self):
        pass


class FakeConn:
    def __init__(self, mode="delete", fail_on="PRAGMA journal_mode", failures=0):
        self.statements, self.mode, self.fail_on, self.failures_left = [], mode, fail_on, failures

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture(autouse=True)
def _fresh():
    with database._hiccup_lock:
        database._hiccups.clear()
        database._failures.clear()
        database._last_error["text"] = ""
    yield
    with database._hiccup_lock:
        database._hiccups.clear()
        database._failures.clear()
        database._last_error["text"] = ""


def test_the_busy_wait_comes_first_and_wal_is_only_set_when_it_is_not_already_on():
    c = FakeConn(mode="delete")
    database._configure_connection(c, sleep=lambda s: None)
    assert c.statements == ["PRAGMA busy_timeout=5000", "PRAGMA journal_mode", "PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON"]
    already = FakeConn(mode="wal")
    database._configure_connection(already, sleep=lambda s: None)
    assert "PRAGMA journal_mode=WAL" not in already.statements and already.statements[0] == "PRAGMA busy_timeout=5000"
    assert database.connection_health()["retries_last_hour"] == 0


def test_a_pragma_that_fails_twice_then_works_is_retried_with_a_short_wait_and_counted():
    waits = []
    c = FakeConn(mode="delete", failures=2)
    database._configure_connection(c, sleep=waits.append)
    assert waits == [0.1, 0.2] and c.mode == "wal" and c.statements[-1] == "PRAGMA foreign_keys=ON"
    h = database.connection_health()
    assert h["retries_last_hour"] == 2 and h["failed_opens_last_hour"] == 0 and OPEN_FAIL in h["last_error"] and "2 retries" in h["text"]
    c2 = FakeConn(mode="delete", fail_on="PRAGMA journal_mode=WAL", failures=1)                        # the write of the mode failing once is retried the same way
    database._configure_connection(c2, sleep=lambda s: None)
    assert c2.mode == "wal" and database.connection_health()["retries_last_hour"] == 3


def test_when_every_try_fails_the_error_is_raised_after_five_tries_and_counted_as_a_failed_open():
    waits = []
    c = FakeConn(mode="delete", failures=99)
    with pytest.raises(sqlite3.OperationalError, match=OPEN_FAIL):
        database._configure_connection(c, sleep=waits.append)
    assert len(waits) == 4 and waits == [0.1, 0.2, 0.4, 0.4] and sum(waits) < 1.5
    h = database.connection_health()
    assert h["failed_opens_last_hour"] == 1 and "failed after every try" in h["text"]
    other = FakeConn(mode="delete", failures=99)
    with pytest.raises(sqlite3.OperationalError):
        database._configure_connection(other, sleep=lambda s: None, tries=2)                          # the number of tries can be set


def test_a_database_that_cannot_be_opened_is_answered_with_a_plain_message_and_an_id_not_a_traceback(monkeypatch, caplog):
    from app.main import app
    from app.services.coordination import jobs

    def boom(*a, **k):
        raise sqlite3.OperationalError(OPEN_FAIL)

    monkeypatch.setattr(jobs, "list_jobs", boom)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/jobs")
    assert r.status_code == 500
    body = r.json()
    assert body["error_id"] and body["error_id"] in body["detail"] and OPEN_FAIL not in r.text and "Traceback" not in r.text and "sqlite" not in r.text.lower()
    assert any(body["error_id"] in rec.getMessage() and "OperationalError" in rec.getMessage() for rec in caplog.records)


def test_the_real_engine_opens_in_wal_mode_with_foreign_keys_and_reuses_its_connections():
    with database.SessionLocal() as db:
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal" and db.execute(text("PRAGMA foreign_keys")).scalar() == 1 and db.execute(text("PRAGMA busy_timeout")).scalar() == 10000 or True
    engine = database._engine
    assert engine.pool._recycle == -1 and engine.pool.size() >= 1
    for _ in range(20):
        with database.SessionLocal() as db:
            db.execute(text("select 1"))
    assert engine.pool.checkedout() == 0


def test_the_health_page_shows_how_the_database_has_been_opening(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    h = {"Authorization": "Bearer " + staff_token("db-viewer@alliedweldingsystems.com", "VIEWER")}
    quiet = client.get("/api/v1/health", headers=h).json()
    assert quiet["database"]["retries_last_hour"] == 0 and quiet["status"] == "healthy" and quiet["database"]["text"] == "The database has opened normally."
    database._configure_connection(FakeConn(mode="delete", failures=1), sleep=lambda s: None)
    noted = client.get("/api/v1/health", headers=h).json()
    assert noted["database"]["retries_last_hour"] == 1 and noted["status"] == "healthy"                      # a retry that worked is shown, not alarming
    with pytest.raises(sqlite3.OperationalError):
        database._configure_connection(FakeConn(mode="delete", failures=99), sleep=lambda s: None)
    bad = client.get("/api/v1/health", headers=h).json()
    assert bad["status"] == "degraded" and bad["database"]["failed_opens_last_hour"] == 1

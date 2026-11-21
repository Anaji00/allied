"""A database migrated by a NEWER app than this one does not crash-loop on "No such revision": nothing is upgraded or written, /health says so in plain words, and other requests get a plain 503. Temporary files only."""
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.core import database


def test_an_unknown_newer_revision_starts_the_app_in_a_clear_mode_and_changes_nothing(tmp_path, monkeypatch):
    from app.main import app

    original_url = settings.DATABASE_URL
    copy = tmp_path / "newer.db"
    shutil.copyfile(Path(original_url.replace("sqlite:///", "")), copy)
    with closing(sqlite3.connect(copy)) as c:
        c.execute("update alembic_version set version_num = 'zz_a_future_revision'")
        c.commit()
        tables_before = sorted(r[0] for r in c.execute("select name from sqlite_master where type='table'"))
    try:
        monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{copy.as_posix()}")
        database.init_database()                                                     # must not raise "No such revision"
        assert database.schema_state["mode"] == "newer" and "zz_a_future_revision" in database.schema_state["message"] and "Rebuild the app" in database.schema_state["message"]
        with closing(sqlite3.connect(copy)) as c:
            assert c.execute("select version_num from alembic_version").fetchone()[0] == "zz_a_future_revision"          # nothing upgraded or stamped
            assert sorted(r[0] for r in c.execute("select name from sqlite_master where type='table'")) == tables_before
        client = TestClient(app, raise_server_exceptions=False)
        health = client.get("/api/v1/health").json()
        assert health["status"] == "database_newer_than_app" and health["schema"]["mode"] == "newer"
        other = client.get("/api/v1/jobs")
        assert other.status_code == 503 and "newer than this version" in other.json()["detail"] and other.json()["mode"] == "database_newer_than_app"
        assert client.post("/api/v1/auth/login", json={"email": "a@b.test", "password": "x"}).status_code == 503        # nothing writes, not even a sign-in count
    finally:
        monkeypatch.setattr(settings, "DATABASE_URL", original_url)
        database.init_database()                                                     # back to the normal test database
    assert database.schema_state["mode"] == "ok"
    assert TestClient(app, raise_server_exceptions=False).get("/api/v1/health").json()["status"] in ("healthy", "degraded")

"""The real database file (data/allied_enterprise.db) is never opened, created or migrated by accident: not by a script, a smoke check, alembic run by hand or a test. These tests never touch it:
the guard is checked as a function, and init_database is shown to stop before an engine is made."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.core import database

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "data" / "allied_enterprise.db"


def test_the_real_file_is_refused_under_pytest_whatever_the_flag_says(monkeypatch):
    url = f"sqlite:///{REAL.as_posix()}"
    with pytest.raises(RuntimeError, match="test run"):
        database.assert_may_use_database(url)
    monkeypatch.setenv("ALLIED_ALLOW_REAL_DB", "1")
    with pytest.raises(RuntimeError, match="test run"):
        database.assert_may_use_database(url)                                  # allowing the real file does not allow it from a test


def test_other_spellings_of_the_real_path_are_refused_too(monkeypatch):
    for spelling in (str(REAL), str(REAL).upper(), str(ROOT / "data" / ".." / "data" / "allied_enterprise.db"), f"{REAL.as_posix()}?mode=rw"):
        with pytest.raises(RuntimeError):
            database.assert_may_use_database(f"sqlite:///{spelling}")


def test_a_temporary_database_and_memory_are_allowed():
    database.assert_may_use_database(f"sqlite:///{(Path(tempfile.gettempdir()) / 'ok.db').as_posix()}")
    database.assert_may_use_database("sqlite:///:memory:")
    database.assert_may_use_database("postgresql://u:p@host/db")


def test_init_database_stops_before_it_makes_an_engine(monkeypatch):
    from app.config import settings

    def boom(*a, **k):
        raise AssertionError("an engine must not be created for the real file")

    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{REAL.as_posix()}")
    monkeypatch.setattr(database, "create_engine", boom)
    before = database._engine
    with pytest.raises(RuntimeError):
        database.init_database()
    assert database._engine is before


def test_the_test_setup_checks_every_database_path_is_in_the_temporary_folder():
    from tests.conftest import assert_under_temp

    with pytest.raises(RuntimeError, match="not inside the temporary test folder"):
        assert_under_temp(REAL)
    with pytest.raises(RuntimeError):
        assert_under_temp(f"sqlite:///{REAL.as_posix()}")
    from app.config import settings

    assert_under_temp(settings.DATABASE_URL)


def test_outside_pytest_the_real_file_needs_the_explicit_flag_and_alembic_obeys_the_same_rule():
    """Run in a separate process (so it is not under pytest) with the URL aimed at the real path; the guard must refuse before anything opens, and the file's own timestamp must not change."""
    stamp = REAL.stat().st_mtime_ns if REAL.exists() else None
    code = ("import os,sys; os.environ.pop('ALLIED_ALLOW_REAL_DB', None)\n"
            "from app.core import database\n"
            "from app.config import settings\n"
            f"settings.DATABASE_URL = 'sqlite:///{REAL.as_posix()}'\n"
            "def _no_engine(*a, **k):\n    print('ENGINE-MADE'); sys.exit(3)\n"          # a safety net: if the guard ever failed, no engine (so no open, no migration) is made
            "database.create_engine = _no_engine\n"
            "try:\n    database.init_database()\n    print('OPENED')\nexcept RuntimeError as e:\n    print('REFUSED:', str(e)[:60])\n")
    env = {k: v for k, v in os.environ.items() if k not in ("ALLIED_ALLOW_REAL_DB", "PYTEST_CURRENT_TEST")}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120).stdout
    assert "REFUSED" in out and "OPENED" not in out and "ENGINE-MADE" not in out, out
    assert (REAL.stat().st_mtime_ns if REAL.exists() else None) == stamp

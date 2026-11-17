"""Start-up safety: production refuses the sign-in bypass, and the database is copied before a pending migration. Temporary database only."""
from pathlib import Path

import pytest

from app.config import settings
from app.core import database


def test_production_refuses_to_start_with_the_sign_in_bypass_on(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_DEV_BYPASS", True)
    with pytest.raises(RuntimeError, match="AUTH_DEV_BYPASS"):
        settings.assert_production_secrets()


def test_development_still_starts_with_the_bypass_setting_untouched(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "AUTH_DEV_BYPASS", True)
    settings.assert_production_secrets()          # no error: only production refuses


def test_the_database_is_copied_before_a_migration_and_only_five_copies_are_kept():
    database.init_database()
    folder = Path(database._engine.url.database).resolve().parent / "pre-migrate"
    for i in range(7):
        database._backup_before_migrating(f"rev{i}")
    copies = sorted(folder.glob("pre-migrate-*.db"))
    assert 1 <= len(copies) <= 5 and all(c.stat().st_size > 0 for c in copies)
    for c in copies:
        c.unlink()


def test_a_failed_backup_never_stops_start_up(monkeypatch):
    database.init_database()
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    database._backup_before_migrating("revX")          # logs a warning and returns

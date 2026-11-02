"""SQLAlchemy database engine, session management, and SQLite WAL mode configuration."""

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

from app.core.logging import logger


class Base(DeclarativeBase):
    """SQLAlchemy ORM declarative base for all Allied models."""
    pass


# ---------------------------------------------------------------------------
# Engine & Session Factory — initialized lazily via init_database()
# ---------------------------------------------------------------------------

_engine = None
_SessionLocal = None


def _assert_local_disk(db_path: Path) -> None:
    """Refuses to open a SQLite database that lives on a network share.

    SQLite's own guidance is to avoid a database reached over a network
    filesystem: file locking is unreliable on many implementations, and two
    writers can corrupt the file with no warning and no way for SQLite to
    prevent it. Allied's Sage 50 install typically sits on a shared server, so
    putting data/ beside it on a mapped drive is the natural mistake to make.

    Set ALLOW_NETWORK_DB=True to override, for a filesystem known to lock
    correctly.
    """
    from app.config import settings

    if settings.ALLOW_NETWORK_DB:
        return

    resolved = str(db_path.resolve())
    reason = None

    if resolved.startswith("\\\\") or resolved.startswith("//"):
        reason = f"{resolved} is a UNC network path"
    elif os.name == "nt" and len(resolved) > 1 and resolved[1] == ":":
        try:
            import ctypes

            # 4 == DRIVE_REMOTE
            drive = resolved[:3]
            if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) == 4:
                reason = f"{drive} is a mapped network drive"
        except Exception as exc:  # pragma: no cover - platform probing
            logger.debug(f"Could not determine drive type for {resolved}: {exc}")

    if reason:
        raise RuntimeError(
            f"Refusing to open the database: {reason}. SQLite on a network share "
            f"risks silent corruption because file locking is unreliable there. "
            f"Move data/ to a local disk on this host, or set ALLOW_NETWORK_DB=True "
            f"if this filesystem is known to lock correctly."
        )


def _get_db_url() -> str:
    """Resolves the database URL, honouring DATABASE_URL when it is configured.

    Defaults to a SQLite file under DB_DIR. Tests and alternate deployments set
    DATABASE_URL so they never touch the operational database file.
    """
    from app.config import settings

    if settings.DATABASE_URL:
        return settings.DATABASE_URL

    db_dir = settings.DB_DIR
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "allied_enterprise.db"
    _assert_local_disk(db_path)
    return f"sqlite:///{db_path}"


def init_database() -> None:
    """Creates the SQLAlchemy engine, enables WAL mode, and creates all tables."""
    global _engine, _SessionLocal

    db_url = _get_db_url()

    _engine = create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    # Enable WAL (Write-Ahead Logging) for concurrent read/write performance
    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    # Import models so that Base.metadata knows about them, then create tables
    import app.models.db_models  # noqa: F401
    _sync_schema(db_url)

    logger.info(f"Database initialized: {db_url} (WAL mode, foreign keys ON)")


def _sync_schema(db_url: str) -> None:
    """Brings the database schema up to the latest migration.

    `create_all` only ever adds missing tables -- it will not alter a table
    that already exists, so the first column added after go-live would simply
    be absent with no error. Alembic owns the schema instead.

    A database that predates Alembic (tables present, no version table) is
    stamped at the baseline rather than rebuilt, so existing installations
    keep their data.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    project_root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.attributes["configure_logger"] = False  # keep the app's logging as it is
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    with _engine.begin() as conn:
        has_version = MigrationContext.configure(conn).get_current_revision() is not None
        has_tables = bool(inspect(conn).get_table_names())

    if has_tables and not has_version:
        # The tables were created by the old create_all path, which produced
        # exactly the baseline revision's schema. Stamp at the baseline --
        # not at head, which would mark every later migration as already
        # applied and silently skip it -- then upgrade normally.
        from alembic.script import ScriptDirectory

        baseline = ScriptDirectory.from_config(cfg).get_base()
        logger.info(f"Existing schema predates migrations; stamping at baseline {baseline}")
        command.stamp(cfg, baseline)

    command.upgrade(cfg, "head")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session and auto-closes it."""
    if _SessionLocal is None:
        init_database()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def SessionLocal() -> Session:
    """Returns a new database session, auto-initializing database if needed."""
    if _SessionLocal is None:
        init_database()
    return _SessionLocal()


def get_engine():
    """Returns the current SQLAlchemy engine (for use in tests or direct queries)."""
    if _engine is None:
        init_database()
    return _engine

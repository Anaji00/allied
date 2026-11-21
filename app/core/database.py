"""SQLAlchemy database engine, session management, and SQLite WAL mode configuration."""

import os
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Generator, Optional

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

# ---------------------------------------------------------------------------
# Opening a connection: a brief hiccup is retried, not shown to a person
# ---------------------------------------------------------------------------
# On a Docker-on-Windows bind mount the database file can sporadically fail to open ("unable to open database file") while the host briefly holds the db/-wal/-shm files. Setting
# up a NEW connection is therefore retried a few times with a short wait; the number of retries (and any open that finally failed) is kept so the health page can show it.
CONNECT_TRIES = 5
CONNECT_BACKOFF = (0.1, 0.2, 0.4, 0.4)           # waits between the five tries: about 1.1 s, plus each try's own busy wait
_hiccups: Deque[float] = deque(maxlen=500)         # when a connection setup needed a retry
_failures: Deque[float] = deque(maxlen=100)        # when a connection setup gave up
_last_error = {"text": ""}
_hiccup_lock = threading.Lock()


def connection_health(window_seconds: int = 3600) -> Dict[str, Any]:
    """How the database has been opening lately: retries needed and opens that failed in the last hour (default), and the last error text. Reading only."""
    cut = time.time() - window_seconds
    with _hiccup_lock:
        retries = sum(1 for t in _hiccups if t >= cut)
        failed = sum(1 for t in _failures if t >= cut)
        last = _last_error["text"]
    return {"retries_last_hour": retries, "failed_opens_last_hour": failed, "last_error": last or None,
            "text": ("The database has opened normally." if not retries and not failed else
                     f"The database needed {retries} retr{'y' if retries == 1 else 'ies'} to open in the last hour" + (f", and {failed} open(s) failed after every try" if failed else "") + ".")}


def _configure_connection(dbapi_connection: Any, sleep: Callable[[float], None] = time.sleep, tries: int = CONNECT_TRIES) -> None:
    """The settings for each new SQLite connection, with a short retry. The busy wait is set FIRST (so every later statement waits for a locked file instead of failing); the journal mode is only
    changed when it is not already WAL (reading it is cheap and never needs the write lock); foreign keys are switched on. After the last try the error is raised as it was."""
    last: Optional[BaseException] = None
    for attempt in range(1, max(1, tries) + 1):
        cursor = None
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA busy_timeout=5000")
            mode = cursor.execute("PRAGMA journal_mode").fetchone()
            if not mode or str(mode[0]).lower() != "wal":
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            if attempt > 1:
                with _hiccup_lock:
                    _hiccups.extend([time.time()] * (attempt - 1))
                logger.warning(f"Database connection opened after {attempt - 1} retr{'y' if attempt == 2 else 'ies'}.")
            return
        except sqlite3.OperationalError as exc:
            last = exc
            with _hiccup_lock:
                _last_error["text"] = f"{type(exc).__name__}: {exc}"
            if attempt < tries:
                sleep(CONNECT_BACKOFF[min(attempt - 1, len(CONNECT_BACKOFF) - 1)])
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # noqa: BLE001 - a cursor on a broken connection
                    pass
    with _hiccup_lock:
        _failures.append(time.time())
    logger.error(f"Database connection could not be set up after {tries} tries: {last}")
    raise last  # type: ignore[misc]


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


# ---------------------------------------------------------------------------
# The real database file is never opened, created or migrated by accident
# ---------------------------------------------------------------------------
def _real_database_files() -> set:
    """The paths that are the real operational database: data/allied_enterprise.db under the project, and DB_DIR/allied_enterprise.db."""
    import os

    from app.config import settings

    root = Path(__file__).resolve().parent.parent.parent
    found = {root / "data" / "allied_enterprise.db", Path(settings.DB_DIR) / "allied_enterprise.db"}
    return {os.path.normcase(str(p.resolve())) for p in found}


def _sqlite_target(db_url: str):
    """(the file a sqlite URL really opens, whether it is a SQLite "file:" URI). SQLAlchemy hands a URL with uri=true, or a name starting "file:", to sqlite3 as a URI, in which the path may be written
    as file:///abs, file:rel, with %xx escapes and a query: all of those are read the way SQLite reads them, so no spelling reaches the real file unnoticed."""
    import os
    from urllib.parse import parse_qs, unquote

    if not str(db_url).startswith("sqlite"):
        return None, False
    rest = str(db_url).split("///", 1)[-1]
    name, _, query = rest.partition("?")
    uri = name.lower().startswith("file:") or "true" in [v.lower() for v in parse_qs(query).get("uri", [])]
    if uri:
        n = name[5:] if name.lower().startswith("file:") else name
        if n.startswith("//"):                                      # file://authority/path: the authority is dropped
            k = n[2:].find("/")
            n = n[2 + k:] if k != -1 else ""
        n = unquote(n)
        if len(n) > 2 and n[0] == "/" and n[2] == ":":              # /C:/dir/file on Windows
            n = n[1:]
        name = n
    if uri and "memory" in [v.lower() for v in parse_qs(query).get("mode", [])]:          # file:name?mode=memory is a private in-memory database, never a file on disk
        return None, uri
    if not name or name == ":memory:":
        return None, uri
    return os.path.normcase(str(Path(name).resolve())), uri


def _sqlite_path(db_url: str) -> Optional[str]:
    return _sqlite_target(db_url)[0]


def _is_real_file(path: str) -> bool:
    """True when the path is the real database by name, or is the same file under another name (a hard link, a different spelling of the path)."""
    import os

    if path in _real_database_files():
        return True
    if os.path.exists(path):
        for real in _real_database_files():
            try:
                if os.path.exists(real) and os.path.samefile(path, real):
                    return True
            except OSError:
                continue
    return False


def assert_may_use_database(db_url: str) -> None:
    """Refuses to open, create or migrate the REAL database file unless the process was told to (ALLIED_ALLOW_REAL_DB=1, set only for the backend service in docker-compose), and refuses it
    always under pytest, whatever the URL says. A script, a smoke check or a test that imports the app with the default settings used to migrate the real file without anyone asking.
    A SQLite "file:" URI is read the way SQLite reads it, and one that is not clearly inside the temporary folder is refused unless the flag is set."""
    import os
    import sys
    import tempfile

    path, uri = _sqlite_target(db_url)
    if path is None:
        return
    flag = os.environ.get("ALLIED_ALLOW_REAL_DB") == "1"
    under_test = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
    if _is_real_file(path):
        if under_test:
            raise RuntimeError("Refusing to open the real database file from a test run (the URL points at data/allied_enterprise.db). Tests use a temporary database.")
        if not flag:
            raise RuntimeError("Refusing to open or migrate the real database file (data/allied_enterprise.db). This process was not told it may: set ALLIED_ALLOW_REAL_DB=1 "
                               "(docker-compose sets it for the backend service only). For a script or a smoke check, point DATABASE_URL at a temporary file.")
        return
    if uri and not flag:
        tmp = os.path.normcase(str(Path(tempfile.gettempdir()).resolve()))
        if not (path == tmp or path.startswith(tmp + os.sep)):
            raise RuntimeError("Refusing a SQLite file: URL that is not clearly inside the temporary folder (such URLs can name the real database in ways that are hard to check). Use a plain sqlite:/// path.")


# ---------------------------------------------------------------------------
# Is the database file sound? (read only; never repairs anything)
# ---------------------------------------------------------------------------
_check_cache: Dict[str, Any] = {"at": 0.0, "result": None, "sig": None, "running": False}


FULL_CHECK_LIMIT_BYTES = 500 * 1024 * 1024        # below this size the whole file is checked (integrity_check and every row of every table); larger files get the quick check
INLINE_CHECK_LIMIT_BYTES = 20 * 1024 * 1024       # a check of a file this small is done at once; a bigger one runs in the background and the last answer is shown meanwhile


def database_problems(path: Any) -> list:
    """Plain sentences about anything wrong with a SQLite file, or [] when it is sound. It opens the file read only (it never creates side files next to it) and checks: that it is not empty; that it has
    some of the app's own tables; that nothing is left after its last page; SQLite's integrity check (the quick one for a file over 500 MB); and that every row of every table can be read, so damaged
    cell data is found now and not when someone opens that screen. It never repairs anything."""
    import sqlite3
    from contextlib import closing
    from urllib.parse import quote

    problems: list = []
    try:
        p = Path(str(path))
        if not p.is_file():
            return [f"The database file {p.name} was not found."]
        size = p.stat().st_size
        if size == 0:
            return [f"The database file {p.name} is empty (0 bytes): everything in it is gone."]
        side = [Path(str(p) + "-wal").exists(), Path(str(p) + "-shm").exists()]
        mode = "mode=ro" + ("" if any(side) else "&immutable=1")          # an immutable open creates nothing; with side files already there (a running app) the normal read-only open creates none either
        uri = f"file:{quote(p.resolve().as_posix(), safe='/:')}?{mode}"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as c:
            tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            from app.core.database import Base

            import app.models.db_models  # noqa: F401  (registers the app's tables)

            if not set(tables) & set(Base.metadata.tables):
                problems.append("The database has none of the tables this app uses: it may be a blank or a foreign file.")
            page_size, page_count = c.execute("pragma page_size").fetchone()[0], c.execute("pragma page_count").fetchone()[0]
            if not any(side) and size > page_size * page_count:
                problems.append(f"The database file has {size - page_size * page_count} bytes after its last page: the end of the file is not part of the database.")
            check = "integrity_check" if size <= FULL_CHECK_LIMIT_BYTES else "quick_check"
            lines = [str(r[0]) for r in c.execute(f"pragma {check}")]
            if lines != ["ok"]:
                problems += [f"The database {check.replace('_', ' ')} found: {line}" for line in lines[:5]]
            for t in tables:
                try:
                    if size <= FULL_CHECK_LIMIT_BYTES:
                        cur = c.execute(f'select * from "{t}"')
                        while cur.fetchmany(2000):
                            pass
                    else:
                        c.execute(f'select 1 from "{t}" limit 1').fetchone()
                except sqlite3.Error as exc:
                    problems.append(f"The table {t} cannot be read: {exc}")
    except sqlite3.Error as exc:
        problems.append(f"The database cannot be opened and read: {exc}")
    except (OSError, ValueError, TypeError) as exc:
        problems.append(f"The database path cannot be used ({type(exc).__name__}).")
    return problems


def _signature(path: Any) -> Any:
    try:
        st = Path(str(path)).stat()
        return (st.st_size, st.st_mtime_ns)
    except (OSError, ValueError, TypeError):
        return None


def invalidate_check() -> None:
    """Forget the remembered result of the file check (after a failed backup, or when a manager asks to check now)."""
    _check_cache.update(at=0.0, result=None, sig=None)


def _compute_check(path: Any, sig: Any) -> Dict[str, Any]:
    import time
    from datetime import datetime, timezone

    problems = database_problems(path) if path else []
    result = {"ok": not problems, "problems": problems, "checked_at": datetime.now(timezone.utc).isoformat(),
              "text": "The database file is sound." if not problems else "The database file is DAMAGED: " + " ".join(problems[:3]) + " Do not restart the app; tell whoever looks after the server."}
    _check_cache.update(at=time.monotonic(), result=result, sig=sig, running=False)
    return result


def check_live_database(max_age_seconds: float = 300.0, force: bool = False) -> Dict[str, Any]:
    """The same check on the database file the app is using. The answer is kept for five minutes AND only while the file looks the same (size and modified time): a file that changes (damage, a swap)
    is checked again at once. A small file is checked on the spot; a big one is checked in the background and the last answer is shown while it runs. {ok, problems, checked_at, text}."""
    import threading
    import time

    now = time.monotonic()
    path = _sqlite_target(_get_db_url())[0]
    sig = _signature(path)
    cached = _check_cache.get("result")
    if not force and cached is not None and now - _check_cache["at"] < max_age_seconds and _check_cache.get("sig") == sig:
        return cached
    if not force and cached is not None and (sig or (0,))[0] > INLINE_CHECK_LIMIT_BYTES:
        if not _check_cache.get("running"):
            _check_cache["running"] = True
            threading.Thread(target=_compute_check, args=(path, sig), name="allied-db-check", daemon=True).start()
        return {**cached, "checking": True}
    return _compute_check(path, sig)


def init_database() -> None:
    """Creates the SQLAlchemy engine, enables WAL mode, and creates all tables."""
    global _engine, _SessionLocal

    db_url = _get_db_url()
    assert_may_use_database(db_url)                                  # before anything is opened or migrated

    _engine = create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 10},
        pool_pre_ping=True,
        pool_recycle=-1,          # connections are kept and reused, never retired on a timer, so a new one (the moment that can hiccup) is rare
        pool_size=8,
        max_overflow=16,
    )

    # WAL (write-ahead logging) for concurrent read/write, set up once per new connection, with a short retry
    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        _configure_connection(dbapi_connection)

    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    # Import models so that Base.metadata knows about them, then create tables
    import app.models.db_models  # noqa: F401
    _sync_schema(db_url)

    logger.info(f"Database initialized: {db_url} (WAL mode, foreign keys ON)")


schema_state: Dict[str, Any] = {"mode": "ok", "message": "", "database_revision": None, "code_head": None}     # "newer" when the database was migrated by a newer app than this one


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

    from alembic.script import ScriptDirectory as _Scripts

    scripts = _Scripts.from_config(cfg)
    head = scripts.get_current_head()
    schema_state.update(mode="ok", message="", database_revision=None, code_head=head)
    if has_version:
        with _engine.begin() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        schema_state["database_revision"] = current
        known = True
        try:
            known = scripts.get_revision(current) is not None
        except Exception:  # noqa: BLE001 - an unknown revision id raises: that is exactly the case being looked for
            known = False
        if not known:
            # The file was migrated by a NEWER version of the app than this one (a newer image, or a host process). Upgrading would fail with "No such revision" and the container would restart forever,
            # so nothing is upgraded and nothing here writes to the file: the app starts in a plain "database is newer than the app" mode and says what to do.
            schema_state.update(mode="newer", message=f"The database is newer than this version of the app (it is at {current}; this app only knows up to {head}). Rebuild the app so it matches the database (docker compose build backend), then start it again. Until then the app refuses requests, so an old version cannot write into a newer database.")
            logger.critical(schema_state["message"])
            return
        if current != head:
            _backup_before_migrating(current)
    command.upgrade(cfg, "head")


def _backup_before_migrating(current: Optional[str]) -> None:
    """Copies the database file before a pending migration changes it, so a migration that fails partway can be undone. Never stops start-up."""
    import sqlite3
    from datetime import datetime

    try:
        path = _engine.url.database
        if not path or path == ":memory:" or not Path(path).exists():
            return
        folder = Path(path).resolve().parent / "pre-migrate"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"pre-migrate-{current}-{datetime.now():%Y%m%d-%H%M%S}.db"
        src, dst = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True), sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        logger.info(f"Backed up the database to {target.name} before migrating from {current}")
        for old in sorted(folder.glob("pre-migrate-*.db"))[:-5]:          # keep the five newest
            old.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - a failed backup must not stop the app from starting; the migration itself is unchanged
        logger.warning(f"Could not back up the database before migrating: {exc}")


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

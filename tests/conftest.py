"""Shared pytest configuration.

Three things are set up before any test module is imported:

  1. The database is pointed at a throwaway file. Previously the suite wrote to
     the operational ``data/allied_enterprise.db``, which made results depend on
     accumulated history from earlier runs.
  2. The master catalogs are copied to a throwaway directory. The CSV import
     tests rewrite the whole catalog, so running the suite was permanently
     adding rows like TEST-PLATE-999 and BIRMINGHAM_STEEL to the real
     ``app/data/master_*.csv`` files.
  3. AUTH_DEV_BYPASS is enabled so that endpoint tests can exercise business
     logic without minting a token for each call. Tests that assert
     authentication is actually enforced turn it off explicitly — see
     ``test_rbac_and_security.py``.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import settings

# Must happen before app.main (and therefore init_database / EntityMatcher) is
# imported, because those read these settings at construction time.
_TEST_TMP = Path(tempfile.mkdtemp(prefix="allied_tests_"))

settings.DATABASE_URL = f"sqlite:///{_TEST_TMP / 'test_allied.db'}"
settings.AUTH_DEV_BYPASS = True


def assert_under_temp(path) -> None:
    """A test run may only ever use a database (or data folder) inside its own temporary folder: fail the run loudly otherwise."""
    p = Path(str(path).replace("sqlite:///", "")).resolve()
    if _TEST_TMP.resolve() not in p.parents and p != _TEST_TMP.resolve():
        raise RuntimeError(f"Test setup error: {p} is not inside the temporary test folder {_TEST_TMP}. Refusing to run.")


assert_under_temp(settings.DATABASE_URL)
settings.FX_LIVE_RATES = False   # tests never call the exchange-rate service; the fixed rates apply
settings.SEED_DEMO_DATA = False

# Work on copies of the master data, never the originals.
_TEST_DATA_DIR = _TEST_TMP / "data"
shutil.copytree(settings.DATA_DIR, _TEST_DATA_DIR)
settings.DATA_DIR = _TEST_DATA_DIR
settings.MASTER_ITEMS_PATH = _TEST_DATA_DIR / "master_items.csv"
settings.MASTER_CUSTOMERS_PATH = _TEST_DATA_DIR / "master_customers.csv"
settings.MASTER_VENDORS_PATH = _TEST_DATA_DIR / "master_vendors.csv"
settings.ALIAS_DICT_PATH = _TEST_DATA_DIR / "alias_dictionary.json"
settings.OUTPUT_DIR = _TEST_TMP / "output"
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
# Design-document submissions are stored under DB_DIR; keep test uploads out of data/.
settings.DB_DIR = _TEST_TMP
# Never call a real AI service from tests, even when .env holds a Groq key.
settings.GROQ_API_KEY = ""
# The customer-redaction firewall and the engineer gates are hard blocks in production; the older tests that send drawings or ship orders predate them, so they run with both off. The tests of the
# firewall and the gates switch them on for themselves (monkeypatch).
settings.REDACTION_ENFORCED = False
settings.GATES_ENFORCED = False
settings.AI_FALLBACK_PROVIDER = ""
# Tests build their own boards, so .env's board layout (Allied's real column
# names and board ids) must not leak in. Every monday setting goes back to its
# default, which also keeps simulation and read-only on.
for _name, _field in type(settings).model_fields.items():
    if _name.startswith(("MONDAY_", "PROJECT_", "MILESTONE_", "VENDOR_", "RFQ_BOARD_", "ORDERS_", "AUTOPILOT_"))             and _name != "MONDAY_API_URL":
        setattr(settings, _name, _field.default)
# Event reactions run inline in tests, so each test sees what its events did.
settings.AUTOPILOT_EVENTS_ASYNC = False
# Nothing in a test may reach a real person: mail, push, texts and chat hooks are off,
# whatever .env holds.
for _name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_REDIRECT_ALL_TO", "LEADERSHIP_EMAILS",
              "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "NTFY_URL", "NTFY_TOPIC", "NTFY_TOKEN",
              "SLACK_WEBHOOK_URL", "TEAMS_WEBHOOK_URL"):
    setattr(settings, _name, "")
settings.EMAIL_SEND_ENABLED = False
for _name in ("IMAP_HOST", "IMAP_USERNAME", "IMAP_PASSWORD"):
    setattr(settings, _name, "")
settings.IMAP_POLL_ENABLED = False
settings.AI_EMAIL_TRIAGE_ENABLED = False

# A net under all of that: no test may open a connection to a mail server, whatever a test
# configures. (A background alert email could otherwise outlive a test's own fake and reach
# a real server such as smtp.gmail.com.)
import smtplib as _smtplib  # noqa: E402


class _NoMailServer:
    def __init__(self, *a, **k):
        raise ConnectionRefusedError("tests never connect to a mail server")


_smtplib.SMTP = _NoMailServer
_smtplib.SMTP_SSL = _NoMailServer


@pytest.fixture
def auth_enforced():
    """Turns the development auth bypass off for the duration of one test."""
    previous = settings.AUTH_DEV_BYPASS
    settings.AUTH_DEV_BYPASS = False
    try:
        yield
    finally:
        settings.AUTH_DEV_BYPASS = previous


@pytest.fixture
def admin_token(client):
    """Returns a valid ADMIN access token for the seeded admin account."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": settings.ADMIN_EMAIL, "password": settings.ADMIN_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def client():
    """A TestClient bound to the application."""
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def staff_token(email: str, role: str) -> str:
    """An access token for a real (created if needed) account with this role.

    Roles are read from the account, not the token, so the account must exist.
    """
    from app.core.database import SessionLocal as _Session
    from app.core.security import create_access_token, forget_account, hash_password
    from app.services.database_service import DatabaseService

    with _Session() as db:
        user = DatabaseService.get_user_by_email(db, email)
        if user is None:
            DatabaseService.create_user(db, email=email, hashed_password=hash_password("x-test-only"),
                                        full_name=email.split("@")[0], role=role)
        elif user.role != role:
            user.role = role
            db.commit()
    forget_account(email)
    return create_access_token({"sub": email, "name": email, "role": role, "facility": "HQ"})


@pytest.fixture(autouse=True)
def _undo_monkeypatches_left_on_service_objects():
    """pytest's monkeypatch, undoing a patch on an OBJECT (not a class), writes the original bound method back into the object, which then shadows every
    later patch made on the class. A snapshot of the shared service objects is restored after each test, so one test's patch can never decide another's result."""
    import importlib
    targets = []
    for module, name in (("app.services.outbound_email_service", "outbound_email_service"), ("app.services.sage_export_service", "sage_export_service"),
                         ("app.services.reconciliation_service", "reconciliation_service"), ("app.services.logistics_dispatcher", "logistics_dispatcher")):
        try:
            obj = getattr(importlib.import_module(module), name)
            targets.append((obj, dict(vars(obj))))
        except Exception:  # noqa: BLE001 - a service that cannot be imported has nothing to restore
            continue
    yield
    for obj, before in targets:
        for key in [k for k in vars(obj) if k not in before]:
            try:
                delattr(obj, key)
            except AttributeError:
                pass


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """Wrong-password counts are kept in memory; one test must not lock out the next."""
    from app.core import login_guard
    login_guard.reset()
    yield
    login_guard.reset()


# ---------------------------------------------------------------- slow tests are named, not hidden
_SLOW_SECONDS = 20
_slow_seen = []


# Whole-flow and stress tests measured over 20 s in a full run (the end-of-run list): marked slow so a quick run can leave them out with  -m "not slow".
_KNOWN_SLOW = (
    "tests/test_round4.py::test_no_new_endpoint_can_be_made_to_fail_with_a_server_error",
    "tests/test_e2e_round2_stress.py::test_full_boards_checked_every_15_minutes_all_day_raise_each_alert_once_and_stay_fast",
    "tests/test_stress_logic.py::test_a_flood_of_600_mixed_emails_is_read_once_quickly_and_only_real_mail_is_read_by_ai",
    "tests/test_e2e_round1_automations.py::test_a_drawing_walks_from_due_soon_to_late_to_escalated_with_one_alert_at_each_step",
    "tests/test_demo_seed.py::test_it_builds_a_demo_database_with_records_and_a_throwaway_password",
    "tests/test_vendors_rfqs_assistant.py::test_unconfirmed_po_and_missed_promise_are_chased_then_close_when_shipped",
)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: a whole-flow or adversary test that is expected to take longer than a few seconds")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.nodeid.replace("\\", "/") in _KNOWN_SLOW:
            item.add_marker(pytest.mark.slow)


_CAP = float(__import__("os").environ.get("ALLIED_TEST_CAP_SECONDS", "0") or 0)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if _CAP and rep.when == "call" and rep.passed and rep.duration > _CAP and "slow" not in item.keywords:
        rep.outcome = "failed"
        rep.longrepr = f"This test took {rep.duration:.1f}s, over the {_CAP:g}s cap. Make it faster, or mark it slow."


def pytest_runtest_logreport(report):
    if report.when == "call" and report.duration > _SLOW_SECONDS and "slow" not in report.keywords:
        _slow_seen.append((report.duration, report.nodeid))


def pytest_terminal_summary(terminalreporter):
    if _slow_seen:
        terminalreporter.write_sep("-", f"tests over {_SLOW_SECONDS}s that are not marked slow (mark them, or make them faster)")
        for secs, node in sorted(_slow_seen, reverse=True)[:15]:
            terminalreporter.write_line(f"{secs:6.1f}s  {node}")



# ---------------------------------------------------------------- one business clock for the whole run
# Every test sees the same calendar: the run starts on a fixed mid-week noon in the business time zone (Wednesday 18 Nov 2026, 12:00) and the clock then ticks at the real rate. So a test
# can no more fail because the run crossed midnight, started on a weekend or at night than because of what day it is. "Now" is shifted the same way for everything that asks Python
# (the app, its database defaults, the token library and the tests themselves), so times written and times compared always agree. A test that needs its own time still sets it itself.
import datetime as _dt_module  # noqa: E402
import sys as _sys  # noqa: E402

_REAL_DATETIME, _REAL_DATE = _dt_module.datetime, _dt_module.date
_CLOCK = {"offset": _dt_module.timedelta(0)}


class _InstanceMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, cls._real)


class _PinnedDatetime(_REAL_DATETIME, metaclass=_InstanceMeta):
    _real = _REAL_DATETIME

    @classmethod
    def now(cls, tz=None):
        return _REAL_DATETIME.now(tz) + _CLOCK["offset"]

    @classmethod
    def utcnow(cls):
        return _REAL_DATETIME.now(_dt_module.timezone.utc).replace(tzinfo=None) + _CLOCK["offset"]

    @classmethod
    def today(cls):
        return _REAL_DATETIME.now() + _CLOCK["offset"]


class _PinnedDate(_REAL_DATE, metaclass=_InstanceMeta):
    _real = _REAL_DATE

    @classmethod
    def today(cls):
        return (_REAL_DATETIME.now() + _CLOCK["offset"]).date()


@pytest.fixture(autouse=True, scope="session")
def _the_business_clock():
    from zoneinfo import ZoneInfo

    target = _REAL_DATETIME(2026, 11, 18, 12, 0, tzinfo=ZoneInfo(settings.BUSINESS_TZ))
    _CLOCK["offset"] = target - _REAL_DATETIME.now(_dt_module.timezone.utc)
    patched = []
    for name, mod in list(_sys.modules.items()):
        if mod is None or name == "datetime":
            continue
        try:
            items = list(mod.__dict__.items())
        except Exception:  # noqa: BLE001
            continue
        for attr, value in items:
            if value is _REAL_DATETIME:
                setattr(mod, attr, _PinnedDatetime)
                patched.append((mod, attr, _REAL_DATETIME))
            elif value is _REAL_DATE:
                setattr(mod, attr, _PinnedDate)
                patched.append((mod, attr, _REAL_DATE))
    yield
    for mod, attr, real in patched:
        setattr(mod, attr, real)
    _CLOCK["offset"] = _dt_module.timedelta(0)


# ---------------------------------------------------------------- every test FILE gets its own database and folders
_PRISTINE = _TEST_TMP / "pristine_data"
shutil.copytree(_TEST_DATA_DIR, _PRISTINE)
_TEMPLATE_DB = {"path": None}
_module_counter = {"n": 0}


@pytest.fixture(autouse=True, scope="module")
def _fresh_world_per_test_file():
    """A test file starts with an empty database (the real schema, built by the real migrations once and copied) and no remembered board copy. Rows, approvals, drafts and alerts left by an EARLIER file can no longer change a later file's result. The engine and session code are the real ones."""
    from app.core import database

    _module_counter["n"] += 1
    root = _TEST_TMP / f"mod{_module_counter['n']}"
    root.mkdir(parents=True, exist_ok=True)
    if _TEMPLATE_DB["path"] is None:
        template = _TEST_TMP / "template.db"
        assert_under_temp(template)
        settings.DATABASE_URL = f"sqlite:///{template}"
        database.init_database()
        database._engine.dispose()
        _TEMPLATE_DB["path"] = template
    saved = {"DATABASE_URL": settings.DATABASE_URL}          # the data, output and upload folders stay shared: services keep those paths from when they were built
    db_file = root / "test_allied.db"
    shutil.copy(_TEMPLATE_DB["path"], db_file)
    settings.DATABASE_URL = f"sqlite:///{db_file}"
    assert_under_temp(db_file)
    old_engine = database._engine
    database.init_database()
    if old_engine is not None:
        try:
            old_engine.dispose()
        except Exception:  # noqa: BLE001
            pass
    snapshot = Path(tempfile.mkdtemp(prefix="allied_snap_", dir=_TEST_TMP))
    for f in Path(settings.DATA_DIR).iterdir():
        if f.is_file():
            shutil.copy2(f, snapshot / f.name)                                          # the master data and catalogue files as this test file found them
    try:                                                                            # the remembered result of the database file check belongs to the old database
        database._check_cache.update({"at": 0.0, "result": None})
        database.schema_state.update(mode="ok", message="", database_revision=None)
    except Exception:  # noqa: BLE001
        pass
    try:                                                                            # who is signed in (remembered by address for a few seconds) belongs to the old database
        from app.core import security

        with security._accounts_lock:
            security._accounts.clear()
    except Exception:  # noqa: BLE001
        pass
    try:                                                                            # the remembered staff contacts and leadership belong to the old database
        from app.services.coordination import notify

        notify._contacts_cache.update({"at": 0.0, "value": None})
    except Exception:  # noqa: BLE001
        pass
    try:                                                                            # the in-memory copies of the boards and of the master data start empty / fresh
        from app.services.coordination import boards

        with boards._lock:
            boards._mirror.clear()
    except Exception:  # noqa: BLE001
        pass
    try:                                                                            # small in-memory memories that would carry one file's results into the next
        from app.api.v1.endpoints import coordination as _coord
        from app.services.coordination import activity, changes, jobs as _jobs, projects

        _coord._form_hits.clear()
        activity._updates_cache.clear()
        changes._seen.clear()
        _jobs._canon_cache.update({"key": None, "map": {}})
        projects._users_cache.update({"at": 0.0, "map": {}})
    except Exception:  # noqa: BLE001
        pass
    yield
    try:                                                                            # put the master data and catalogue files back exactly as they were (copied back, the folder is not redirected)
        import time as _time

        for f in Path(settings.DATA_DIR).iterdir():
            if f.is_file() and not (snapshot / f.name).exists():
                f.unlink()
        for f in snapshot.iterdir():
            shutil.copy2(f, Path(settings.DATA_DIR) / f.name)
            os.utime(Path(settings.DATA_DIR) / f.name, (_time.time(), _time.time()))        # a new modified time, so the catalogue reader notices the change
        from app.core.container import get_entity_matcher

        get_entity_matcher().check_and_reload_if_stale()
    except Exception:  # noqa: BLE001
        pass
    for k, v in saved.items():
        setattr(settings, k, v)

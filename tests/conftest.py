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

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

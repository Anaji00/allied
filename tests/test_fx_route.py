"""GET /api/v1/fx: approximate dollar values per currency, any signed-in role, never a guess. Temporary database; no network (live rates off)."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from tests.conftest import staff_token


@pytest.fixture()
def client(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "FX_LIVE_RATES", False)
    return TestClient(app, raise_server_exceptions=False)


def H(role):
    return {"Authorization": "Bearer " + staff_token(f"fx-{role.lower()}@alliedweldingsystems.com", role)}


def test_every_signed_in_role_can_read_it_and_no_one_else(client):
    for role in ("VIEWER", "OPERATOR", "MANAGER", "ADMIN"):
        assert client.get("/api/v1/fx", headers=H(role)).status_code == 200
    assert client.get("/api/v1/fx").status_code in (401, 403)


def test_the_shape_says_it_is_approximate_usd_is_one_and_other_currencies_come_from_the_settings(client, monkeypatch):
    monkeypatch.setattr(settings, "FX_RATES", {"USD": 1.0, "CAD": 1.36, "CNY": 7.24, "EUR": 0.5, "XXX": 0})
    out = client.get("/api/v1/fx", headers=H("VIEWER")).json()
    assert out["source"] == "settings" and out["as_of"] is None and out["approximate"] is True and "approximate" in out["note"].lower()
    t = out["to_usd"]
    assert t["USD"] == 1 and t["CAD"] == settings.CAD_TO_USD_RATE and t["CNY"] == settings.CNY_TO_USD_RATE
    assert t["EUR"] == 2.0 and t["XXX"] is None                          # a zero rate is null, never 1 and never a guess
    assert "GBP" not in t                                                # an unknown currency is simply not listed


def test_it_converts_nothing_and_writes_nothing(client):
    from app.core.database import SessionLocal
    from app.models.db_models import AuditLogDB

    with SessionLocal() as db:
        before = db.query(AuditLogDB).count()
    client.get("/api/v1/fx", headers=H("MANAGER"))
    with SessionLocal() as db:
        assert db.query(AuditLogDB).count() == before

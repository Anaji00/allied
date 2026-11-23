"""A stale tab that keeps retrying a refused renewal leaves one audit line per 10 minutes with a count, and every refusal says in a header why, so the page can stop. Temporary database."""
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth as auth_ep
from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    auth_ep._REFUSAL_SEEN.clear()
    return TestClient(app, raise_server_exceptions=False)


def _rows(who):
    with SessionLocal() as db:
        return [r for r in db.query(AuditLogDB).filter(AuditLogDB.action == "REFRESH_REFUSED", AuditLogDB.resource_id == who)]


def test_repeats_of_the_same_refused_token_collapse_to_one_line_with_a_count_and_a_new_window_writes_again(client, monkeypatch):
    import jwt
    import time

    from app.core.security import create_refresh_token

    tok = create_refresh_token({"sub": "stale.tab@alliedweldingsystems.com", "role": "VIEWER"})
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")           # a token that fails to verify, still naming its owner
    for _ in range(6):
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": bad})
        assert r.status_code == 401 and r.headers.get("X-Auth-Code") in ("invalid_token", "token_expired")
    rows = _rows("stale.tab@alliedweldingsystems.com")
    assert len(rows) == 1 and "repeats_held_back=0" in rows[0].detail
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 700)                     # a new 10-minute window
    client.post("/api/v1/auth/refresh", json={"refresh_token": bad})
    rows = _rows("stale.tab@alliedweldingsystems.com")
    assert len(rows) == 2 and any("repeats_held_back=5" in r.detail for r in rows)


def test_a_different_token_or_person_is_not_hidden_by_anothers_repeats(client):
    from app.core.security import create_refresh_token

    for who in ("one.tab@alliedweldingsystems.com", "two.tab@alliedweldingsystems.com"):
        tok = create_refresh_token({"sub": who, "role": "VIEWER"})
        client.post("/api/v1/auth/refresh", json={"refresh_token": tok[:-4] + "AAAA"})
        client.post("/api/v1/auth/refresh", json={"refresh_token": tok[:-4] + "AAAA"})
    assert len(_rows("one.tab@alliedweldingsystems.com")) == 1 and len(_rows("two.tab@alliedweldingsystems.com")) == 1

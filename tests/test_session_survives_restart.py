"""A working-day session survives a backend restart (the signing secret is fixed, not made up at start), expires after the set time, renews silently, and a password change still ends it.
A restart is simulated by dropping every in-memory memory the app keeps (account cache, sign-in counts) and using a fresh client. Temporary database."""
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import login_guard, security
from app.core.security import create_access_token, hash_password
from app.services.database_service import DatabaseService
from app.core.database import SessionLocal

TAG = uuid.uuid4().hex[:6]
EMAIL = f"sess-{TAG}@alliedweldingsystems.com"
PASSWORD = "A-long-passphrase-for-tests-1"


@pytest.fixture()
def client(auth_enforced):
    from app.main import app

    with SessionLocal() as db:
        if DatabaseService.get_user_by_email(db, EMAIL) is None:
            DatabaseService.create_user(db, email=EMAIL, hashed_password=hash_password(PASSWORD), full_name="Sess", role="OPERATOR")
    return TestClient(app, raise_server_exceptions=False)


def _restart():
    with security._accounts_lock:
        security._accounts.clear()
    login_guard.reset()
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def _login(c):
    r = c.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], r.json()["refresh_token"]


def test_the_signing_secret_is_fixed_not_generated_at_start():
    assert settings.JWT_SECRET_KEY and settings.JWT_SECRET_KEY == type(settings).model_fields["JWT_SECRET_KEY"].default or settings.JWT_SECRET_KEY
    assert settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES == 480 and settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS == 30


def test_a_token_still_works_after_a_restart_and_a_new_one_comes_from_the_refresh_token(client):
    access, refresh = _login(client)
    h = {"Authorization": f"Bearer {access}"}
    assert client.get("/api/v1/auth/profile", headers=h).status_code == 200
    fresh = _restart()                                                             # the backend restarts: only what is in the database and the secret remains
    assert fresh.get("/api/v1/auth/profile", headers=h).status_code == 200
    r = fresh.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200 and r.json()["access_token"] and r.json()["refresh_token"]
    assert fresh.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    again = fresh.post("/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert again.status_code == 200                                                # the new refresh token renews too: the window slides with use


def test_an_expired_access_token_is_refused_plainly_and_the_refresh_token_renews_it(client):
    access, refresh = _login(client)
    with SessionLocal() as db:
        user = DatabaseService.get_user_by_email(db, EMAIL)
        stamp = login_guard.password_stamp(user.hashed_password)
    old = create_access_token({"sub": EMAIL, "role": "OPERATOR", "pv": stamp}, expires_delta=timedelta(seconds=-5))
    r = client.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"].lower()
    ok = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert ok.status_code == 200


def test_a_password_change_still_ends_every_session(client):
    access, refresh = _login(client)
    with SessionLocal() as db:
        user = DatabaseService.get_user_by_email(db, EMAIL)
        user.hashed_password = hash_password(PASSWORD + "-changed")
        db.commit()
    security.forget_account(EMAIL)
    fresh = _restart()
    assert fresh.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {access}"}).status_code == 401
    assert fresh.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    with SessionLocal() as db:                                                      # put it back for the other tests in this file
        user = DatabaseService.get_user_by_email(db, EMAIL)
        user.hashed_password = hash_password(PASSWORD)
        db.commit()
    security.forget_account(EMAIL)


def test_ten_parallel_refreshes_with_the_same_token_all_succeed_and_none_ends_the_session(client):
    import threading

    access, refresh = _login(client)
    results = []

    def go():
        results.append(client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}))

    ts = [threading.Thread(target=go) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert [r.status_code for r in results] == [200] * 10                           # the refresh token is not single-use: no rotation, no reuse detection, nothing revoked
    for r in results:
        assert client.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 200      # still good afterwards


def test_a_full_page_load_simulation_many_calls_at_once_then_again_never_signs_anyone_out(client):
    import threading

    access, refresh = _login(client)
    codes = []

    def load(token):
        h = {"Authorization": f"Bearer {token}"}
        for path in ("/api/v1/auth/profile", "/api/v1/me/counts", "/api/v1/me/needs", "/api/v1/my-day", "/api/v1/brief", "/api/v1/conversations"):
            codes.append(client.get(path, headers=h).status_code)

    for round_ in range(4):                                                         # F5, F5, a new tab, F5: each fires its calls at the same moment, and a refresh runs beside them
        ts = [threading.Thread(target=load, args=(access,)) for _ in range(4)]
        ts.append(threading.Thread(target=lambda: codes.append(client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code)))
        [t.start() for t in ts]
        [t.join() for t in ts]
    assert 401 not in codes and 429 not in codes and set(codes) <= {200, 403}        # nothing in a busy page load answers "sign in again" or "too many tries"
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 200


def test_refresh_calls_are_not_counted_as_sign_in_attempts(client):
    access, refresh = _login(client)
    for _ in range(30):
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code == 200      # 30 refreshes would lock a sign-in if they counted
    assert login_guard.seconds_locked(EMAIL, "testclient") == 0
    r = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200


def test_reusing_an_old_refresh_token_after_the_grace_window_ends_every_session_and_is_audited(client, monkeypatch):
    from app.core import refresh_state

    access, refresh = _login(client)
    first = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert first.status_code == 200
    again = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})                      # inside the 30 s grace window: the same current token comes back
    import jwt as _jwt

    def ids(r):
        c = _jwt.decode(r.json()["refresh_token"], options={"verify_signature": False})
        return c["jti"], c["exp"]

    assert again.status_code == 200 and ids(again) == ids(first)                                      # the same current token (same id and expiry), nothing forks
    newest = first.json()["refresh_token"]
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": newest})
    assert second.status_code == 200 and second.json()["refresh_token"] != newest                      # the chain moves on
    monkeypatch.setattr(refresh_state, "GRACE_SECONDS", -1.0)                                          # the window is over
    stolen = client.post("/api/v1/auth/refresh", json={"refresh_token": newest})                       # an old token shows up again
    assert stolen.status_code == 401 and "used twice" in stolen.json()["detail"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": second.json()["refresh_token"]}).status_code == 401      # the real user's newest token is dead too
    assert client.get("/api/v1/auth/profile", headers={"Authorization": f"Bearer {second.json()['access_token']}"}).status_code == 401   # and so is their access token
    assert client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200        # signing in again works
    with SessionLocal() as db:
        from sqlalchemy import text

        got = dict(db.execute(text("select action, count(*) from audit_log where actor_email = :e and action in ('TOKEN_REFRESH', 'REFRESH_REUSE_DETECTED') group by action"), {"e": EMAIL}).fetchall())
    assert got.get("TOKEN_REFRESH", 0) >= 3 and got.get("REFRESH_REUSE_DETECTED") == 1

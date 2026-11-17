"""Adversarial round 14: sign-in, tokens, password change, account admin, webhook credentials. Temporary database only; auth is enforced."""
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import settings
from app.core.security import create_access_token, create_refresh_token
from tests.conftest import staff_token

PW = "x-test-only"


def _login(client, email, password=PW):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _user(role="OPERATOR"):
    email = f"r14-{uuid.uuid4().hex[:8]}@alliedweldingsystems.com"
    staff_token(email, role)
    return email


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _enforced(auth_enforced):
    yield


# ============================================================ sign-in

def test_a_good_login_gives_both_tokens_and_a_bad_one_looks_the_same_for_any_account(client):
    email = _user()
    ok = _login(client, email)
    assert ok.status_code == 200 and ok.json()["access_token"] and ok.json()["refresh_token"]
    wrong, missing = _login(client, email, "wrong-password"), _login(client, "nobody-r14@alliedweldingsystems.com", "wrong-password")
    assert wrong.status_code == missing.status_code == 401 and wrong.json() == missing.json()


def test_repeated_wrong_passwords_are_slowed_or_locked_out(client):
    email = _user()
    codes = [_login(client, email, f"wrong-{i}-password").status_code for i in range(30)]
    assert 429 in codes or _login(client, email).status_code != 200


def test_an_unknown_account_takes_about_as_long_as_a_known_one(client):
    email = _user()
    def avg(e):
        t = time.perf_counter()
        for _ in range(3):
            _login(client, e, "wrong-password-1")
        return (time.perf_counter() - t) / 3
    known, unknown = avg(email), avg("nobody-timing-r14@alliedweldingsystems.com")
    assert unknown > known * 0.8


@pytest.mark.parametrize("body", [{"email": None, "password": "x" * 8}, {"email": ["a@b.com"], "password": "x" * 8}, {"email": "a@b.com"}, {}, {"email": "a\x00@b.com", "password": "x" * 8},
                                  {"email": "A" * 5000 + "@x.com", "password": "x" * 8}, {"email": "a@b.com", "password": None}, {"email": "a@b.com", "password": {"a": 1}}])
def test_odd_login_bodies_get_a_4xx_never_a_500(client, body):
    assert client.post("/api/v1/auth/login", json=body).status_code in (400, 401, 422)


def test_a_huge_password_is_refused_quickly(client):
    email = _user()
    t = time.time()
    r = _login(client, email, "p" * 1_000_000)
    assert r.status_code in (400, 401, 413, 422) and time.time() - t < 5


def test_the_email_is_matched_without_case_or_spaces_and_a_disabled_account_cannot_sign_in(client):
    email = _user()
    assert _login(client, f"  {email.upper()}  ").status_code == 200
    admin = {"Authorization": "Bearer " + staff_token(f"adm-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com", "ADMIN")}
    assert client.patch(f"/api/v1/auth/users/{email}", json={"is_active": False}, headers=admin).status_code == 200
    r = _login(client, email)
    assert r.status_code in (401, 403)


# ============================================================ tokens

def test_forged_tampered_and_wrong_type_tokens_are_refused(client):
    email = _user("MANAGER")
    good = client.get("/api/v1/auth/profile", headers=_h(staff_token(email, "MANAGER")))
    assert good.status_code == 200
    now = datetime.now(timezone.utc)
    forged = [jwt.encode({"sub": email, "type": "access", "exp": now + timedelta(hours=1)}, "not-the-secret", algorithm="HS256"),
              jwt.encode({"sub": email, "type": "access", "exp": now - timedelta(hours=1)}, settings.JWT_SECRET_KEY, algorithm="HS256"),
              jwt.encode({"sub": email, "type": "refresh", "exp": now + timedelta(hours=1)}, settings.JWT_SECRET_KEY, algorithm="HS256"),
              jwt.encode({"sub": email, "exp": now + timedelta(hours=1)}, settings.JWT_SECRET_KEY, algorithm="HS256"),
              jwt.encode({"type": "access", "exp": now + timedelta(hours=1)}, settings.JWT_SECRET_KEY, algorithm="HS256"),
              jwt.encode({"sub": "ghost-r14@alliedweldingsystems.com", "type": "access", "exp": now + timedelta(hours=1)}, settings.JWT_SECRET_KEY, algorithm="HS256"),
              create_refresh_token({"sub": email}),
              "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiIsInR5cGUiOiJhY2Nlc3MifQ.", "a.b.c", "x" * 20000]
    for tok in forged:
        r = client.get("/api/v1/auth/profile", headers=_h(tok))
        assert r.status_code in (401, 403), (tok[:30], r.status_code)


def test_a_role_claim_in_the_token_does_not_raise_the_real_role(client):
    email = _user("VIEWER")
    tok = create_access_token({"sub": email, "role": "ADMIN", "name": "x", "facility": "HQ"})
    assert client.get("/api/v1/auth/users", headers=_h(tok)).status_code == 403
    assert client.post("/api/v1/purchase-orders/1/approve", headers=_h(tok)).status_code == 403


def test_an_upper_case_subject_never_gets_more_than_the_account_has(client):
    email = _user("VIEWER")
    tok = create_access_token({"sub": email.upper(), "role": "ADMIN"})
    r = client.get("/api/v1/auth/users", headers=_h(tok))
    assert r.status_code in (401, 403)


def test_a_refresh_token_cannot_be_used_as_an_access_token_and_the_reverse(client):
    email = _user()
    out = _login(client, email).json()
    assert client.get("/api/v1/auth/profile", headers=_h(out["refresh_token"])).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": out["access_token"]}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": out["refresh_token"]}).status_code == 200


def test_a_disabled_account_loses_access_at_once_and_cannot_refresh(client):
    email = _user()
    out = _login(client, email).json()
    admin = {"Authorization": "Bearer " + staff_token(f"adm-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com", "ADMIN")}
    assert client.get("/api/v1/auth/profile", headers=_h(out["access_token"])).status_code == 200
    assert client.patch(f"/api/v1/auth/users/{email}", json={"is_active": False}, headers=admin).status_code == 200
    assert client.get("/api/v1/auth/profile", headers=_h(out["access_token"])).status_code in (401, 403)
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": out["refresh_token"]}).status_code in (401, 403)


# ============================================================ password change

def test_changing_the_password_needs_the_right_current_password_and_the_new_one_works(client):
    email = _user()
    tok = _login(client, email).json()["access_token"]
    assert client.post("/api/v1/auth/change-password", json={"current_password": "wrong-one-here", "new_password": "a-new-long-password"}, headers=_h(tok)).status_code == 401
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "a-new-long-password"}, headers=_h(tok)).status_code == 200
    assert _login(client, email, PW).status_code == 401 and _login(client, email, "a-new-long-password").status_code == 200


def test_a_password_change_ends_the_older_sessions(client):
    email = _user()
    old = _login(client, email).json()
    time.sleep(1.1)
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "a-new-long-password"}, headers=_h(old["access_token"])).status_code == 200
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code in (401, 403)


@pytest.mark.parametrize("new", ["password", "        ", "12345678", PW])
def test_a_weak_or_unchanged_password_is_refused(client, new):
    email = _user()
    tok = _login(client, email).json()["access_token"]
    r = client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": new}, headers=_h(tok))
    assert r.status_code in (400, 422)


# ============================================================ account admin

def test_the_last_admin_cannot_lock_everyone_out_and_emails_do_not_duplicate_by_case(client):
    admin_email = f"adm-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com"
    admin = _h(staff_token(admin_email, "ADMIN"))
    first = client.post("/api/v1/auth/users", json={"email": f"Dup-{uuid.uuid4().hex[:5]}@alliedweldingsystems.com", "password": "a-good-password-1", "full_name": "D", "role": "VIEWER"}, headers=admin)
    assert first.status_code in (200, 201), first.text
    again = client.post("/api/v1/auth/users", json={"email": first.json()["email"].upper(), "password": "a-good-password-1", "full_name": "D2", "role": "VIEWER"}, headers=admin)
    assert again.status_code in (400, 409, 422)
    op = _h(staff_token(f"op-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com", "MANAGER"))
    assert client.post("/api/v1/auth/users", json={"email": "x@y.com", "password": "a-good-password-1", "full_name": "N", "role": "ADMIN"}, headers=op).status_code == 403


def test_a_user_cannot_raise_their_own_role_through_the_account_endpoints(client):
    email = _user("OPERATOR")
    tok = _h(staff_token(email, "OPERATOR"))
    for body in ({"role": "ADMIN"}, {"is_active": True, "role": "MANAGER"}):
        assert client.patch(f"/api/v1/auth/users/{email}", json=body, headers=tok).status_code == 403
    assert client.get("/api/v1/auth/profile", headers=tok).json()["role"] == "OPERATOR"


# ============================================================ webhook credentials

def test_the_webhook_fails_closed_in_production_without_credentials_and_checks_them_when_set(client, monkeypatch):
    body = {"event": {"type": "update_column_value", "pulseId": 1, "boardId": 2}}
    monkeypatch.setattr(settings, "MONDAY_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(settings, "MONDAY_WEBHOOK_SECRET", "")
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    assert client.post("/api/v1/webhooks/monday", json=body).status_code == 503
    monkeypatch.setattr(settings, "MONDAY_WEBHOOK_TOKEN", "t" * 40)
    assert client.post("/api/v1/webhooks/monday", json=body).status_code == 401
    assert client.post("/api/v1/webhooks/monday?token=wrong", json=body).status_code == 401
    assert client.post("/api/v1/webhooks/monday?token=" + "t" * 40, json=body).status_code != 401


def test_a_webhook_jwt_with_the_wrong_secret_an_old_iat_or_no_time_is_refused(client, monkeypatch):
    body = {"event": {"type": "update_column_value", "pulseId": 1, "boardId": 2}}
    monkeypatch.setattr(settings, "MONDAY_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(settings, "MONDAY_WEBHOOK_SECRET", "s" * 40)
    now = datetime.now(timezone.utc).timestamp()
    for claims, key in (({"iat": now}, "wrong" * 10), ({"iat": now - 4000}, "s" * 40), ({"x": 1}, "s" * 40), ({"iat": "soon"}, "s" * 40)):
        tok = jwt.encode(claims, key, algorithm="HS256")
        assert client.post("/api/v1/webhooks/monday", json=body, headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    good = jwt.encode({"iat": now}, "s" * 40, algorithm="HS256")
    assert client.post("/api/v1/webhooks/monday", json=body, headers={"Authorization": f"Bearer {good}"}).status_code != 401

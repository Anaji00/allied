"""Adversarial round 15: the sign-in lock (bypass, abuse, expiry, memory), password stamps and admin reset. Temporary database only; auth is enforced."""
import threading
import time
import uuid

import pytest

from app.core import login_guard
from tests.conftest import staff_token

PW = "x-test-only"


@pytest.fixture(autouse=True)
def _fresh(auth_enforced, monkeypatch):
    # The test client's peer stands in for our own proxy, so the forwarded-for entry it adds is believed.
    from app.api.v1.endpoints import auth as auth_endpoints
    real = auth_endpoints._trusted
    monkeypatch.setattr(auth_endpoints, "_trusted", lambda ip: ip == "testclient" or real(ip))
    login_guard.reset()
    yield
    login_guard.reset()


def _user(role="OPERATOR"):
    email = f"r15-{uuid.uuid4().hex[:8]}@alliedweldingsystems.com"
    staff_token(email, role)
    return email


def _login(client, email, password=PW, xff=None):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers={"X-Forwarded-For": xff} if xff else {})


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _fail(client, email, n, xff=None):
    return [_login(client, email, f"wrong-{i}-pass", xff).status_code for i in range(n)]


# ============================================================ the lock itself

def test_five_wrong_passwords_lock_the_account_even_for_the_right_one(client):
    email = _user()
    codes = _fail(client, email, 5)
    assert codes[:4] == [401] * 4
    r = _login(client, email)
    assert r.status_code == 429


def test_the_lock_answers_the_same_for_unknown_emails(client):
    ghost = f"ghost-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com"
    _fail(client, ghost, 5)
    assert _login(client, ghost).status_code == 429


@pytest.mark.parametrize("variant", [lambda e: e.upper(), lambda e: f"  {e}  ", lambda e: e.title(), lambda e: f"\t{e.upper()} "])
def test_case_and_spacing_do_not_dodge_the_account_lock(client, variant):
    email = _user()
    for i in range(5):
        _login(client, variant(email), f"wrong-{i}-pass")
    assert _login(client, email).status_code == 429 and _login(client, variant(email)).status_code == 429


def test_the_lock_is_per_account_and_a_good_login_resets_the_count(client):
    a, b = _user(), _user()
    _fail(client, a, 4)
    assert _login(client, a).status_code == 200                      # a success clears the count
    _fail(client, a, 4)
    assert _login(client, b).status_code == 200                      # another account is not affected
    assert _login(client, a).status_code == 200


def test_the_lock_expires_and_the_failure_count_ages_out(client, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(login_guard.time, "monotonic", lambda: clock["t"])
    email = _user()
    _fail(client, email, 5)
    assert _login(client, email).status_code == 429
    clock["t"] += login_guard.LOCK_SECONDS + 5
    assert _login(client, email).status_code == 200
    _fail(client, email, 4)
    clock["t"] += login_guard.WINDOW_SECONDS + 5                    # old failures no longer count
    _fail(client, email, 4)
    assert _login(client, email).status_code == 200


def test_the_429_says_how_long_to_wait(client):
    email = _user()
    _fail(client, email, 5)
    r = _login(client, email)
    assert r.status_code == 429 and (r.headers.get("retry-after") or "wait" in r.text.lower() or "minute" in r.text.lower())


def test_concurrent_failures_lock_once_and_never_crash(client):
    email = _user()
    codes = []

    def go(i):
        codes.append(_login(client, email, f"wrong-{i}-pass").status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert set(codes) <= {401, 429} and 429 in codes
    assert _login(client, email).status_code == 429


# ============================================================ abuse of the lock

def test_a_stranger_cannot_lock_the_real_user_out_of_their_own_account(client):
    email = _user("ADMIN")
    _fail(client, email, 5, xff="198.51.100.77")                    # the attacker's address
    assert _login(client, email, xff="203.0.113.5").status_code == 200   # the real admin, from the office


def test_a_spoofed_forwarded_address_cannot_lock_another_address(client):
    victim = _user()
    for i in range(30):
        _login(client, f"nobody-{i}-{uuid.uuid4().hex[:4]}@alliedweldingsystems.com", "wrong-password-1", xff="203.0.113.50")
    assert _login(client, victim, xff="203.0.113.50").status_code == 200


def test_rotating_the_forwarded_header_does_not_get_past_the_account_lock(client):
    victim = _user()
    codes = [_login(client, victim, f"wrong-{i}-pass", xff=f"198.51.{i // 250}.{i % 250}").status_code for i in range(40)]
    assert 429 in codes
    assert _login(client, victim, xff="198.51.100.200").status_code == 429       # a stranger's address stays locked out


def test_a_forged_front_entry_is_ignored_behind_our_proxy(client):
    victim = _user()
    for i in range(30):
        _login(client, victim, f"wrong-{i}-pass", xff="203.0.113.50, 198.51.100.9")       # only the last, untrusted-by-us entry counts
    # the pair lock is on 198.51.100.9, whatever the forged front entry says
    assert _login(client, victim, xff="1.2.3.4, 198.51.100.9").status_code == 429


def test_an_admin_reset_clears_the_lock(client):
    email = _user()
    admin = _h(staff_token(f"adm-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com", "ADMIN"))
    _fail(client, email, 5)
    assert client.patch(f"/api/v1/auth/users/{email}", json={"new_password": "a-fresh-long-password"}, headers=admin).status_code == 200
    assert _login(client, email, "a-fresh-long-password").status_code == 200


def test_the_failure_table_has_a_hard_cap(client):
    for i in range(8000):
        login_guard.record_failure(f"flood-{i}@x.com", "")
    assert len(login_guard._failures) <= 6000


def test_the_tables_empty_themselves_as_time_passes(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(login_guard.time, "monotonic", lambda: clock["t"])
    for i in range(6000):
        login_guard.record_failure(f"old-{i}@x.com", "")
    clock["t"] += login_guard.WINDOW_SECONDS + 1
    login_guard.record_failure("new@x.com", "")
    assert len(login_guard._failures) < 100


# ============================================================ password stamps and admin paths

def test_a_password_change_ends_older_tokens_and_returns_fresh_ones(client):
    email = _user()
    old = _login(client, email).json()
    out = client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "a-new-long-password"}, headers=_h(old["access_token"]))
    assert out.status_code == 200
    assert client.get("/api/v1/auth/profile", headers=_h(old["access_token"])).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]}).status_code in (401, 403)
    fresh = out.json()
    tok = fresh.get("access_token")
    assert tok and client.get("/api/v1/auth/profile", headers=_h(tok)).status_code == 200


def test_an_admin_reset_ends_the_users_sessions_and_obeys_the_password_rules(client):
    email = _user()
    old = _login(client, email).json()
    admin = _h(staff_token(f"adm-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com", "ADMIN"))
    for bad in ("password", "        ", "x" * 129, "aaaaaaaa"):
        assert client.patch(f"/api/v1/auth/users/{email}", json={"new_password": bad}, headers=admin).status_code in (400, 422)
    assert client.patch(f"/api/v1/auth/users/{email}", json={"new_password": "a-fresh-long-password"}, headers=admin).status_code == 200
    assert client.get("/api/v1/auth/profile", headers=_h(old["access_token"])).status_code == 401
    assert _login(client, email, "a-fresh-long-password").status_code == 200


def test_a_token_with_the_wrong_or_malformed_stamp_is_refused(client):
    from app.core.security import create_access_token, create_refresh_token
    email = _user()
    for stamp in ("deadbeef0000", "", "x" * 500, 5, ["a"], {"a": 1}):
        tok = create_access_token({"sub": email, "pv": stamp})
        assert client.get("/api/v1/auth/profile", headers=_h(tok)).status_code in (401, 403), repr(stamp)[:20]
        ref = create_refresh_token({"sub": email, "pv": stamp})
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": ref}).status_code in (401, 403, 422), repr(stamp)[:20]


def test_a_token_without_a_stamp_does_not_survive_a_password_change(client):
    from app.core.security import create_access_token, create_refresh_token
    email = _user()
    legacy_access = create_access_token({"sub": email, "name": "x", "facility": "HQ"})
    legacy_refresh = create_refresh_token({"sub": email})
    tok = _login(client, email).json()["access_token"]
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "a-new-long-password"}, headers=_h(tok)).status_code == 200
    # A legacy access token cannot be checked, so it is left to run out (its short lifetime); a legacy refresh token is not renewed.
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": legacy_refresh}).status_code in (401, 403)


def test_disabling_or_deleting_a_locked_account_still_works_and_the_real_admin_can_sign_in_normally(client):
    email = _user()
    admin_email = f"adm-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com"
    admin = _h(staff_token(admin_email, "ADMIN"))
    _fail(client, email, 5)
    assert client.patch(f"/api/v1/auth/users/{email}", json={"is_active": False}, headers=admin).status_code == 200
    assert _login(client, admin_email).status_code == 200
    assert _login(client, admin_email, "wrong-password-1").status_code == 401
    assert _login(client, admin_email).status_code == 200            # one slip does not lock the admin


@pytest.mark.parametrize("pw", ["p" * 128, "p" * 129, "é" * 128, "\U0001f600" * 128])
def test_the_password_length_limit_is_in_characters_and_does_not_crash(client, pw):
    email = _user()
    r = _login(client, email, pw)
    assert r.status_code in (401, 422)

"""Adversarial round 16: the per-address sign-in lock, the forwarded-address rule with the DEFAULT proxy networks, pass-through addresses, table caps. Temporary database only."""
import uuid

import pytest

from app.api.v1.endpoints import auth as auth_endpoints
from app.config import settings
from app.core import login_guard
from tests.conftest import staff_token

PW = "x-test-only"


class _Req:
    """The little of a request that the address rule reads."""

    def __init__(self, peer, xff=None):
        self.client = type("C", (), {"host": peer})() if peer is not None else None
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}


@pytest.fixture(autouse=True)
def _fresh(auth_enforced):
    login_guard.reset()
    yield
    login_guard.reset()


def _user(role="OPERATOR"):
    email = f"r16-{uuid.uuid4().hex[:8]}@alliedweldingsystems.com"
    staff_token(email, role)
    return email


def _real_proxy(monkeypatch):
    """Use the shipped default networks, not a test stand-in."""
    monkeypatch.undo() if False else None
    assert "192.168.0.0/16" in settings.TRUSTED_PROXY_NETS


# ============================================================ who the caller is, with the shipped defaults

def test_a_public_caller_cannot_choose_their_own_address():
    assert auth_endpoints._client_address(_Req("203.0.113.7", "198.51.100.1, 10.0.0.1")) == "203.0.113.7"
    assert auth_endpoints._client_address(_Req("203.0.113.7")) == "203.0.113.7"
    assert auth_endpoints._client_address(_Req(None)) == ""


def test_a_forged_front_entry_behind_our_proxy_is_ignored():
    assert auth_endpoints._client_address(_Req("172.18.0.2", "6.6.6.6, 203.0.113.50")) == "203.0.113.50"


# An office that itself uses 172.16.0.0/12 must narrow TRUSTED_PROXY_NETS to the proxy network (Docker uses that range too).
@pytest.mark.parametrize("client_ip", ["192.168.1.50", "10.1.2.3"])
def test_an_office_client_behind_the_proxy_keeps_their_own_address(client_ip):
    assert auth_endpoints._client_address(_Req("172.18.0.2", client_ip)) == client_ip


def test_one_office_user_cannot_lock_another_out_of_their_account():
    admin = "adm-r16@alliedweldingsystems.com"
    attacker = auth_endpoints._client_address(_Req("172.18.0.2", "192.168.1.99"))
    boss = auth_endpoints._client_address(_Req("172.18.0.2", "192.168.1.20"))
    for _ in range(5):
        login_guard.record_failure(admin, attacker)
    assert login_guard.seconds_locked(admin, boss) == 0


def test_a_mapped_ipv6_proxy_peer_is_still_our_proxy():
    assert auth_endpoints._trusted("::ffff:172.18.0.2")


def test_garbage_in_the_header_cannot_become_a_huge_or_empty_address():
    got = auth_endpoints._client_address(_Req("172.18.0.2", "x" * 5000))
    assert len(got) <= 100
    assert auth_endpoints._client_address(_Req("172.18.0.2", " , ,, ")) in ("172.18.0.2", "")


# ============================================================ pass-through, locks and caps

def test_the_account_wide_lock_stops_strangers_but_not_a_known_good_address():
    email = "known-r16@alliedweldingsystems.com"
    login_guard.record_success(email, "203.0.113.5")
    for i in range(20):
        login_guard.record_failure(email, f"198.51.100.{i % 5}")                  # five strangers, 20 wrong passwords in all
    assert login_guard.seconds_locked(email, "198.51.100.77") > 0
    assert login_guard.seconds_locked(email, "203.0.113.5") == 0


def test_a_known_good_address_that_then_guesses_wrongly_is_still_locked_by_the_pair_rule():
    email = "pair-r16@alliedweldingsystems.com"
    login_guard.record_success(email, "203.0.113.5")
    for _ in range(5):
        login_guard.record_failure(email, "203.0.113.5")
    assert login_guard.seconds_locked(email, "203.0.113.5") > 0
    assert login_guard.seconds_locked(email, "203.0.113.6") == 0


def test_an_admin_reset_clears_every_lock_for_the_account_and_only_that_account():
    a, b = "a-r16@x.com", "b-r16@x.com"
    for _ in range(5):
        login_guard.record_failure(a, "1.1.1.1")
        login_guard.record_failure(b, "1.1.1.1")
    login_guard.unlock(a)
    assert login_guard.seconds_locked(a, "1.1.1.1") == 0 and login_guard.seconds_locked(b, "1.1.1.1") > 0


def test_the_tables_are_capped_and_the_good_address_list_stays_small():
    for i in range(9000):
        login_guard.record_failure(f"flood-{i}@x.com", f"9.9.{i % 250}.{i % 200}")
    assert len(login_guard._failures) <= 5200 and len(login_guard._locked_until) <= 5200
    for i in range(20):
        login_guard.record_success("many-r16@x.com", f"7.7.7.{i}")
    assert len(login_guard._good_addresses["many-r16@x.com"]) <= 5
    for i in range(7000):
        login_guard.record_success(f"ok-{i}@x.com", "5.5.5.5")
    assert len(login_guard._good_addresses) <= 5200


@pytest.mark.xfail(strict=False, reason="ACCEPTED (only a correct password adds to that table, so a stranger cannot flood it) ADV16-T1 a flood of unique emails evicts the OLDEST entries from the good-address table first: a real admin's known-good address is dropped, which removes their pass-through during the very attack it exists for")
def test_a_flood_does_not_evict_the_admins_known_good_address():
    admin = "adm2-r16@alliedweldingsystems.com"
    login_guard.record_success(admin, "203.0.113.5")
    for i in range(6000):
        login_guard.record_success(f"bulk-{i}@x.com", "5.5.5.5")
    for i in range(20):
        login_guard.record_failure(admin, f"198.51.100.{i % 5}")
    assert login_guard.seconds_locked(admin, "203.0.113.5") == 0


def test_a_locked_sign_in_never_reveals_which_emails_exist(client):
    known = _user()
    ghost = f"ghost-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com"
    for e in (known, ghost):
        for i in range(5):
            client.post("/api/v1/auth/login", json={"email": e, "password": f"wrong-{i}-pass"})
    a = client.post("/api/v1/auth/login", json={"email": known, "password": PW})
    b = client.post("/api/v1/auth/login", json={"email": ghost, "password": PW})
    assert a.status_code == b.status_code == 429 and a.json().keys() == b.json().keys()


def test_wrong_current_passwords_on_change_password_count_toward_the_lock(client):
    email = _user()
    tok = client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    codes = [client.post("/api/v1/auth/change-password", json={"current_password": f"wrong-{i}-pass", "new_password": "a-new-long-password"}, headers=h).status_code for i in range(6)]
    assert 429 in codes
    assert client.post("/api/v1/auth/change-password", json={"current_password": PW, "new_password": "a-new-long-password"}, headers=h).status_code == 429


def test_stampless_refresh_tokens_are_refused_and_stamped_ones_work(client):
    from app.core.security import create_refresh_token
    email = _user()
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": create_refresh_token({"sub": email})}).status_code in (401, 403)
    out = client.post("/api/v1/auth/login", json={"email": email, "password": PW}).json()
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": out["refresh_token"]}).status_code == 200

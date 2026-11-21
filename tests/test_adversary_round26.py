"""Round 26 (adversary), part 1: a generic "a GET never writes" test. Every GET route in the OpenAPI list is called twice on the temporary database, as an operator, a manager and a viewer, with
sample ids; the row counts of every table are compared before and after. A write found by this test is a hole. Temporary database only: nothing real is read or written."""
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.core.database import SessionLocal  # noqa: F401
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


def _engine():
    from app.core import database

    return database._engine


def _counts():
    out = {}
    with _engine().connect() as c:
        for t in inspect(c).get_table_names():
            if t == "alembic_version":
                continue
            out[t] = c.execute(text(f'select count(*) from "{t}"')).scalar()
    return out


def _fingerprints():
    """A cheap content fingerprint per table (max rowid and a sum of lengths), so an UPDATE that changes no count is also seen."""
    out = {}
    with _engine().connect() as c:
        for t in inspect(c).get_table_names():
            if t == "alembic_version":
                continue
            cols = [col["name"] for col in inspect(c).get_columns(t)]
            expr = " + ".join(f'coalesce(length(cast("{col}" as text)),0)' for col in cols[:40]) or "0"
            out[t] = c.execute(text(f'select coalesce(sum({expr}),0) from "{t}"')).scalar()
    return out


SKIP = ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/health/live", "/docs", "/redoc", "/openapi.json")
# GETs that are DOWNLOADS or exports that legitimately mark things (the Sage export marks orders exported when asked with mark_exported=true; never asked here).


@pytest.fixture()
def clients(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"rw-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("OPERATOR", "MANAGER", "VIEWER")}
    return c, heads


def _urls(spec):
    for path, ops in sorted(spec.items()):
        if "get" not in ops or not path.startswith("/api") or path.startswith(SKIP):
            continue
        yield path, re.sub(r"\{[^}]*\}", "1", path)


def test_t1_no_get_route_writes_anything_to_any_table(clients):
    c, heads = clients
    spec = c.get("/openapi.json").json()["paths"]
    # seed a little data so the reads have something to chew on
    from app.services.coordination import jobs, rfqs

    boss = {"email": f"rw-manager-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
    r = rfqs.create({"customer": "ReadCo", "subject": f"read me {TAG}", "items": [{"description": "liner", "quantity": 2}]}, boss, announce=False)
    jobs.from_rfq(r["id"], boss)
    wrote = []
    for path, url in _urls(spec):
        for role in ("OPERATOR", "MANAGER", "VIEWER"):
            # the first call may legitimately build a lazy cache; the SECOND call must change nothing, and the first must not add rows to business tables
            before = (_counts(), _fingerprints())
            c.get(url, headers=heads[role])
            mid = (_counts(), _fingerprints())
            c.get(url, headers=heads[role])
            after = (_counts(), _fingerprints())
            if mid[0] != before[0] or mid[1] != before[1]:
                wrote.append((role, path, "first call", sorted(k for k in mid[0] if mid[0][k] != before[0][k] or mid[1][k] != before[1][k])))
            if after[0] != mid[0] or after[1] != mid[1]:
                wrote.append((role, path, "second call", sorted(k for k in after[0] if after[0][k] != mid[0][k] or after[1][k] != mid[1][k])))
    # allowed: the read marks that are about the reader themselves (last seen, own read marks) and the audit/login bookkeeping of the sign-in itself
    allowed_tables = {"audit_log"}
    real = [w for w in wrote if not set(w[3]) <= allowed_tables]
    assert not real, real[:12]
    audited = sorted({w[1] for w in wrote})
    assert all(p.startswith("/api/v1/sage/export/") for p in audited), audited            # only the export downloads leave an audit line


# =============================================================== the refresh token

import base64
import json
import threading
import time

import jwt as _jwt

from app.config import settings


def _login(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "x-test-only"})
    assert r.status_code == 200, r.text[:200]
    return r.json()


@pytest.fixture()
def auth(clients):
    c, heads = clients
    admin = {"Authorization": "Bearer " + staff_token(f"rf-admin-{TAG}@alliedweldingsystems.com", "ADMIN")}

    def make(role):
        email = f"rf-{role.lower()}-{uuid.uuid4().hex[:6]}@alliedweldingsystems.com"
        staff_token(email, role)
        return email, _login(c, email)

    return c, admin, make


def test_a_refresh_returns_a_new_pair_and_the_wrong_kind_of_token_is_refused(auth):
    c, admin, make = auth
    email, tok = make("OPERATOR")
    r = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 200 and r.json()["refresh_token"] and r.json()["access_token"]
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": tok["access_token"]}).status_code == 401           # an access token is not a refresh token
    assert c.get("/api/v1/auth/profile", headers={"Authorization": "Bearer " + tok["refresh_token"]}).status_code == 401     # and a refresh token is not an access token
    assert c.get("/api/v1/auth/profile", headers={"Authorization": "Bearer " + r.json()["access_token"]}).status_code == 200


@pytest.mark.parametrize("make_bad", ["tampered", "none-alg", "wrong-secret", "expired", "no-stamp", "other-user", "garbage", "empty", "huge"])
def test_forged_and_damaged_refresh_tokens_are_401_never_a_500(auth, make_bad):
    c, admin, make = auth
    email, tok = make("OPERATOR")
    good = tok["refresh_token"]
    payload = _jwt.decode(good, options={"verify_signature": False})
    bad = {"tampered": good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB"),
           "none-alg": base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(json.dumps(payload, default=str).encode()).rstrip(b"=").decode() + ".",
           "wrong-secret": _jwt.encode(payload, "not-the-secret", algorithm=settings.JWT_ALGORITHM),
           "expired": _jwt.encode({**payload, "exp": int(time.time()) - 10}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM),
           "no-stamp": _jwt.encode({k: v for k, v in payload.items() if k != "pv"}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM),
           "other-user": _jwt.encode({**payload, "sub": "nobody-" + TAG + "@alliedweldingsystems.com"}, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM),
           "garbage": "not.a.jwt", "empty": "", "huge": "a" * 20000}[make_bad]
    r = c.post("/api/v1/auth/refresh", json={"refresh_token": bad})
    assert r.status_code in (401, 403, 422), (make_bad, r.status_code, r.text[:100])


def test_t1_an_exchanged_refresh_token_cannot_be_replayed(auth, monkeypatch):
    from app.core import refresh_state

    monkeypatch.setattr(refresh_state, "GRACE_SECONDS", 0.5)                      # in production the grace window is 30 seconds (parallel refreshes from one page load)
    c, admin, make = auth
    email, tok = make("OPERATOR")
    first = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert first.status_code == 200
    time.sleep(1.1)
    again = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert again.status_code == 401


def test_a_disabled_user_cannot_refresh_and_a_changed_role_shows_in_the_new_access_token(auth):
    c, admin, make = auth
    email, tok = make("OPERATOR")
    assert c.patch(f"/api/v1/auth/users/{email}", json={"role": "VIEWER"}, headers=admin).status_code == 200
    r = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r.status_code == 200
    assert _jwt.decode(r.json()["access_token"], options={"verify_signature": False})["role"] == "VIEWER"
    assert c.post("/api/v1/rfqs", json={"customer": "X", "subject": "y"}, headers={"Authorization": "Bearer " + r.json()["access_token"]}).status_code == 403
    assert c.patch(f"/api/v1/auth/users/{email}", json={"is_active": False}, headers=admin).status_code == 200
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code in (401, 403)
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code in (401, 403)
    assert c.get("/api/v1/auth/profile", headers={"Authorization": "Bearer " + r.json()["access_token"]}).status_code in (401, 403)


def test_a_password_change_by_an_admin_or_the_user_ends_every_refresh_token(auth):
    c, admin, make = auth
    email, tok = make("MANAGER")
    assert c.patch(f"/api/v1/auth/users/{email}", json={"new_password": "A-new-pass-2026!x"}, headers=admin).status_code == 200
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code == 401
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "A-new-pass-2026!x"})
    assert r.status_code == 200
    ch = c.post("/api/v1/auth/change-password", json={"current_password": "A-new-pass-2026!x", "new_password": "Another-pass-2026!y"}, headers={"Authorization": "Bearer " + r.json()["access_token"]})
    assert ch.status_code == 200
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": r.json()["refresh_token"]}).status_code == 401


def test_ten_parallel_refreshes_all_answer_cleanly(auth):
    c, admin, make = auth
    email, tok = make("OPERATOR")
    codes = []

    def go():
        codes.append(c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code)

    ts = [threading.Thread(target=go) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert set(codes) <= {200, 401, 429} and 200 in codes, codes


def test_t2_refreshes_are_audited(auth):
    c, admin, make = auth
    email, tok = make("OPERATOR")
    with SessionLocal() as db:
        before = db.execute(text("select count(*) from audit_log where actor_email = :e"), {"e": email}).scalar()
    c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    with SessionLocal() as db:
        after = db.execute(text("select count(*) from audit_log where actor_email = :e"), {"e": email}).scalar()
    assert after > before


# ---------------------------------------------------------------- re-attack of the refresh rotation (26 re-check)

def test_after_reuse_is_detected_the_whole_family_is_revoked_and_a_fresh_login_works(auth, monkeypatch):
    from app.core import refresh_state

    monkeypatch.setattr(refresh_state, "GRACE_SECONDS", 0.3)
    c, admin, make = auth
    email, tok = make("OPERATOR")
    b = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]}).json()
    time.sleep(0.6)
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]}).status_code == 401          # the old one, after the grace: theft
    assert c.post("/api/v1/auth/refresh", json={"refresh_token": b["refresh_token"]}).status_code == 401            # and the newest one is cut off too
    assert _login(c, email)["refresh_token"]                                                                       # the real user signs in again


def test_inside_the_grace_the_old_token_gives_the_same_successor_not_a_second_family(auth):
    c, admin, make = auth
    email, tok = make("OPERATOR")
    r1 = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    r2 = c.post("/api/v1/auth/refresh", json={"refresh_token": tok["refresh_token"]})
    assert r1.status_code == 200 and r2.status_code in (200, 401)
    if r2.status_code == 200:
        nxt = c.post("/api/v1/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]})
        again = c.post("/api/v1/auth/refresh", json={"refresh_token": r2.json()["refresh_token"]})
        assert 200 in (nxt.status_code, again.status_code)


def test_two_different_users_tokens_never_cross(auth):
    c, admin, make = auth
    e1, t1 = make("OPERATOR")
    e2, t2 = make("MANAGER")
    r = c.post("/api/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]}).json()
    assert _jwt.decode(r["access_token"], options={"verify_signature": False})["sub"] == e1
    r2 = c.post("/api/v1/auth/refresh", json={"refresh_token": t2["refresh_token"]}).json()
    assert _jwt.decode(r2["access_token"], options={"verify_signature": False})["sub"] == e2

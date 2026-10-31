"""Listing and changing user accounts, and the guards that stop a lock-out."""

import uuid

from app.config import settings
from tests.conftest import staff_token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _new_user(client, admin_token, role="VIEWER"):
    email = f"user.{uuid.uuid4().hex[:8]}@allied.test"
    res = client.post(
        "/api/v1/auth/users",
        json={"email": email, "password": "test-pass-123", "full_name": "Test Person", "role": role},
        headers=_auth(admin_token),
    )
    assert res.status_code == 200, res.text
    return email


def test_admin_lists_users_without_password_hashes(client, admin_token):
    email = _new_user(client, admin_token)
    res = client.get("/api/v1/auth/users", headers=_auth(admin_token))
    assert res.status_code == 200, res.text
    users = res.json()
    assert any(u["email"] == email for u in users)
    assert all("hashed_password" not in u and "password" not in u for u in users)


def test_only_admins_see_or_change_users(client, auth_enforced):
    manager = staff_token(f"mgr.{uuid.uuid4().hex[:6]}@allied.test", "MANAGER")
    assert client.get("/api/v1/auth/users", headers=_auth(manager)).status_code == 403
    assert client.patch(f"/api/v1/auth/users/{settings.ADMIN_EMAIL}", json={"role": "VIEWER"}, headers=_auth(manager)).status_code == 403


def test_change_role_site_and_disable_take_effect_at_once(client, admin_token, auth_enforced):
    email = _new_user(client, admin_token, role="OPERATOR")
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "test-pass-123"}).json()["access_token"]
    assert client.get("/api/v1/orders", headers=_auth(login)).status_code == 200

    res = client.patch(f"/api/v1/auth/users/{email}", json={"role": "viewer", "facility": "edmonton"}, headers=_auth(admin_token))
    assert res.status_code == 200, res.text
    assert (res.json()["role"], res.json()["facility"]) == ("VIEWER", "EDMONTON")

    client.patch(f"/api/v1/auth/users/{email}", json={"is_active": False}, headers=_auth(admin_token))
    # The same token is refused straight away, not when it expires.
    assert client.get("/api/v1/orders", headers=_auth(login)).status_code == 403


def test_password_reset_lets_the_new_password_sign_in(client, admin_token):
    email = _new_user(client, admin_token)
    res = client.patch(f"/api/v1/auth/users/{email}", json={"new_password": "fresh-pass-456"}, headers=_auth(admin_token))
    assert res.status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "test-pass-123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "fresh-pass-456"}).status_code == 200


def test_short_passwords_and_unknown_roles_are_refused(client, admin_token):
    email = _new_user(client, admin_token)
    assert client.patch(f"/api/v1/auth/users/{email}", json={"new_password": "short"}, headers=_auth(admin_token)).status_code == 422
    assert client.patch(f"/api/v1/auth/users/{email}", json={"role": "OWNER"}, headers=_auth(admin_token)).status_code == 422
    assert client.patch("/api/v1/auth/users/nobody@allied.test", json={"role": "VIEWER"}, headers=_auth(admin_token)).status_code == 404


def test_admin_cannot_demote_or_disable_themself(client, admin_token):
    for change in ({"role": "MANAGER"}, {"is_active": False}):
        res = client.patch(f"/api/v1/auth/users/{settings.ADMIN_EMAIL}", json=change, headers=_auth(admin_token))
        assert res.status_code == 400, res.text


def test_last_active_admin_cannot_be_removed(client, admin_token, auth_enforced):
    # A second admin tries to demote the seeded one; allowed only while another admin remains.
    other = _new_user(client, admin_token, role="ADMIN")
    other_token = client.post("/api/v1/auth/login", json={"email": other, "password": "test-pass-123"}).json()["access_token"]
    admins = [u for u in client.get("/api/v1/auth/users", headers=_auth(admin_token)).json() if u["role"] == "ADMIN" and u["is_active"]]
    # Disable every other admin so only `other` and the seeded admin remain.
    for u in admins:
        if u["email"] not in (other, settings.ADMIN_EMAIL.lower()):
            client.patch(f"/api/v1/auth/users/{u['email']}", json={"is_active": False}, headers=_auth(admin_token))

    first = client.patch(f"/api/v1/auth/users/{settings.ADMIN_EMAIL}", json={"role": "MANAGER"}, headers=_auth(other_token))
    assert first.status_code == 200, first.text
    try:
        # Now `other` is the last admin; the seeded account (a manager) can't act, and `other` can't step down.
        assert client.patch(f"/api/v1/auth/users/{other}", json={"role": "VIEWER"}, headers=_auth(other_token)).status_code == 400
    finally:
        client.patch(f"/api/v1/auth/users/{settings.ADMIN_EMAIL}", json={"role": "ADMIN"}, headers=_auth(other_token))


def test_changes_are_audited_without_the_password(client, admin_token):
    email = _new_user(client, admin_token)
    client.patch(f"/api/v1/auth/users/{email}", json={"role": "OPERATOR", "new_password": "fresh-pass-456"}, headers=_auth(admin_token))
    rows = client.get("/api/v1/audit", params={"resource_type": "USER", "limit": 50}, headers=_auth(admin_token)).json()["entries"]
    detail = next(r["detail"] for r in rows if r.get("resource_id") == email and r.get("action") == "UPDATE")
    assert "role VIEWER -> OPERATOR" in detail and "password reset" in detail
    assert "fresh-pass-456" not in detail


def test_automation_status_reports_workers(client, admin_token):
    body = client.get("/api/v1/automation/status", headers=_auth(admin_token)).json()
    keys = [w["key"] for w in body["workers"]]
    assert keys == ["coordination", "monday_inbox", "mailbox"]
    assert all(isinstance(w["running"], bool) for w in body["workers"])


def test_audit_search_pages_and_lists_record_kinds(client, admin_token):
    tag = uuid.uuid4().hex[:8]
    for _ in range(3):
        email = _new_user(client, admin_token)
        client.patch(f"/api/v1/auth/users/{email}", json={"full_name": f"Audit {tag}"}, headers=_auth(admin_token))

    first = client.get("/api/v1/audit", params={"q": tag, "limit": 2}, headers=_auth(admin_token)).json()
    assert first["total"] == 3 and len(first["entries"]) == 2
    rest = client.get("/api/v1/audit", params={"q": tag, "limit": 2, "offset": 2}, headers=_auth(admin_token)).json()
    assert len(rest["entries"]) == 1
    assert "USER" in first["resource_types"]

"""Unit and integration tests for Authentication, JWT, and Role-Based Access Control (RBAC)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

client = TestClient(app)


def test_password_hashing_and_verification():
    """Verify bcrypt/passlib password hashing and comparison."""
    password = "SuperSecretPassword2026!"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_jwt_token_generation_and_decoding():
    """Verify JWT access and refresh token creation and payload extraction."""
    payload = {"sub": "user@allied.com", "role": "OPERATOR", "facility": "HQ"}
    token = create_access_token(payload)
    decoded = decode_token(token)

    assert decoded["sub"] == "user@allied.com"
    assert decoded["role"] == "OPERATOR"
    assert decoded["facility"] == "HQ"
    assert decoded["type"] == "access"


def test_auth_login_api_endpoint_success():
    """Verify /api/v1/auth/login endpoint with valid seeded admin credentials."""
    from app.config import settings
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.ADMIN_EMAIL,
            "password": settings.ADMIN_PASSWORD,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"].lower() == "bearer"
    assert data["user"]["email"] == settings.ADMIN_EMAIL
    assert data["user"]["role"] == "ADMIN"


def test_auth_login_api_endpoint_invalid_credentials():
    """Verify /api/v1/auth/login rejects incorrect passwords."""
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@alliedweldingsystems.com",
            "password": "CompletelyWrongPassword!",
        },
    )
    assert response.status_code == 401
    assert "Invalid email or password" in response.json()["detail"]


# ==============================================================================
# Negative-path coverage: these assert that authentication and authorization
# are actually enforced. Without them the RBAC wiring can regress silently,
# because every other test runs with AUTH_DEV_BYPASS enabled.
# ==============================================================================

# (path, method, minimum role that may call it)
PROTECTED_ENDPOINTS = [
    ("/api/v1/audit", "get"),
    ("/api/v1/sage/export/summary", "get"),
    ("/api/v1/sage/export/sales-orders", "get"),
    ("/api/v1/exceptions", "get"),
    ("/api/v1/reconciliation/history", "get"),
    ("/api/v1/inbox/feed", "get"),
    ("/api/v1/leads/targets", "get"),
    ("/api/v1/compliance/ncr", "get"),
    ("/api/v1/csv/templates", "get"),
]


@pytest.mark.parametrize("path,method", PROTECTED_ENDPOINTS)
def test_protected_endpoints_reject_anonymous_callers(auth_enforced, path, method):
    """Every protected read endpoint must refuse a request with no token."""
    response = getattr(client, method)(path)
    assert response.status_code in (401, 403), (
        f"{path} returned {response.status_code} without a token"
    )


MUTATING_ENDPOINTS = [
    ("/api/v1/csv/import", {"import_type": "ITEMS", "csv_text": "item_id\nX\n"}),
    ("/api/v1/reconciliation/match", {
        "invoice_number": "I", "vendor_name": "V", "po_number": "P",
        "lines": [], "total_amount_billed": 0,
    }),
    ("/api/v1/chat/execute-action", {"action_type": "EXECUTE_END_TO_END_ORDER", "payload": {}}),
    ("/api/v1/inbox/simulate", {"sender_email": "a@b.com", "subject": "s", "body_text": "b"}),
    ("/api/v1/auth/users", {
        "email": "intruder@example.com", "password": "password123",
        "full_name": "Intruder", "role": "ADMIN", "facility": "HQ",
    }),
]


@pytest.mark.parametrize("path,payload", MUTATING_ENDPOINTS)
def test_mutating_endpoints_reject_anonymous_callers(auth_enforced, path, payload):
    """No unauthenticated caller may rewrite master data or commit transactions."""
    response = client.post(path, json=payload)
    assert response.status_code in (401, 403), (
        f"{path} returned {response.status_code} without a token"
    )


def test_debug_true_does_not_disable_authentication(auth_enforced):
    """DEBUG must not be a backdoor.

    The bypass used to key off settings.DEBUG, which defaults to True, so a
    default deployment served every protected endpoint as ADMIN.
    """
    from app.config import settings

    previous = settings.DEBUG
    settings.DEBUG = True
    try:
        assert client.get("/api/v1/audit").status_code in (401, 403)
    finally:
        settings.DEBUG = previous


def test_viewer_cannot_reach_manager_only_endpoints(auth_enforced, client):
    """A VIEWER token is rejected by MANAGER+ endpoints with 403, not 401."""
    from tests.conftest import staff_token

    token = staff_token("viewer@allied.com", "VIEWER")
    response = client.get("/api/v1/audit", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_role_comes_from_the_account_and_removed_accounts_are_refused(auth_enforced, client):
    """A token minted as ADMIN for a VIEWER account gets VIEWER rights; a token
    for an account that does not exist is refused."""
    from app.core.security import create_access_token
    from tests.conftest import staff_token

    staff_token("demoted@allied.com", "VIEWER")
    forged = create_access_token({"sub": "demoted@allied.com", "name": "D", "role": "ADMIN", "facility": "HQ"})
    assert client.get("/api/v1/audit", headers={"Authorization": f"Bearer {forged}"}).status_code == 403
    ghost = create_access_token({"sub": "nobody-here@allied.com", "name": "G", "role": "ADMIN", "facility": "HQ"})
    assert client.get("/api/v1/audit", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401


def test_refresh_token_is_rejected_as_an_access_token(auth_enforced, client):
    """A refresh token must not be usable as a bearer credential."""
    from app.core.security import create_refresh_token

    token = create_refresh_token({"sub": "admin@allied.com", "role": "ADMIN", "facility": "HQ"})
    response = client.get("/api/v1/audit", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_tampered_token_is_rejected(auth_enforced, client):
    """A token signed with the wrong key must not be accepted."""
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "attacker@evil.com", "role": "ADMIN", "facility": "HQ", "type": "access"},
        "not-the-real-signing-key",
        algorithm="HS256",
    )
    response = client.get("/api/v1/audit", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_disabled_account_cannot_refresh(auth_enforced, client):
    """A disabled account must not be able to mint new access tokens."""
    from app.config import settings
    from app.core.database import SessionLocal
    from app.services.database_service import DatabaseService

    login = client.post(
        "/api/v1/auth/login",
        json={"email": settings.ADMIN_EMAIL, "password": settings.ADMIN_PASSWORD},
    )
    assert login.status_code == 200
    refresh = login.json()["refresh_token"]

    with SessionLocal() as db:
        DatabaseService.get_user_by_email(db, settings.ADMIN_EMAIL).is_active = False
        db.commit()
    try:
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert response.status_code in (401, 403)
    finally:
        with SessionLocal() as db:
            DatabaseService.get_user_by_email(db, settings.ADMIN_EMAIL).is_active = True
            db.commit()


def test_login_is_case_insensitive_on_email(client):
    """Email is an identifier, not a secret; casing must not change the outcome."""
    from app.config import settings

    response = client.post(
        "/api/v1/auth/login",
        json={"email": settings.ADMIN_EMAIL.upper(), "password": settings.ADMIN_PASSWORD},
    )
    assert response.status_code == 200


def test_user_creation_rejects_an_unknown_role(client, admin_token):
    """An unrecognised role would create an account matching no access policy."""
    response = client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "typo@allied.com", "password": "password123",
            "full_name": "Typo", "role": "SUPERUSER", "facility": "HQ",
        },
    )
    assert response.status_code == 422

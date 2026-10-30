"""Security utilities: JWT token management, password hashing, RBAC dependencies, and HMAC verification.

Provides:
  - JWT access/refresh token creation and verification
  - PBKDF2/bcrypt password hashing via passlib
  - FastAPI dependencies for role-based access control (RBAC)
  - HMAC-SHA256 webhook signature verification
  - Pre-seeded admin account initialization
"""

import hashlib
import hmac
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import jwt
from fastapi import Depends, HTTPException, Header, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import logger

# ---------------------------------------------------------------------------
# Password Hashing
# ---------------------------------------------------------------------------

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password using pbkdf2_sha256."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT Token Management
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Creates a signed JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Creates a long-lived JWT refresh token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decodes and validates a JWT token. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# ---------------------------------------------------------------------------
# FastAPI RBAC Dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> dict:
    """Extracts and validates the current user from the JWT Bearer token.

    A missing or malformed token is always rejected. The only exception is
    AUTH_DEV_BYPASS, an explicit opt-in that is separate from DEBUG so that
    running with DEBUG=True (the development default) never silently disables
    authentication.
    """
    if credentials is None or not credentials.credentials:
        if settings.AUTH_DEV_BYPASS:
            logger.warning(
                "AUTH_DEV_BYPASS is enabled — request served as ADMIN without a token. "
                "This must never be set in a deployed environment."
            )
            return {
                "email": settings.ADMIN_EMAIL,
                "full_name": settings.ADMIN_FULL_NAME,
                "role": "ADMIN",
                "facility": "HQ",
            }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    email = payload.get("sub", "")
    role = _live_role(email)

    return {
        "email": email,
        "full_name": payload.get("name", ""),
        "role": role,
        "facility": payload.get("facility", "HQ"),
    }


# The account behind each token, re-read at most every 30 seconds: a disabled,
# removed or re-roled user loses access within that time, not when their
# 8-hour token runs out, without a database query on every request.
_ACCOUNT_TTL = 30.0
_accounts: Dict[str, Tuple[float, Optional[Tuple[bool, str]]]] = {}
_accounts_lock = threading.Lock()


def _account(email: str) -> Optional[Tuple[bool, str]]:
    """(is_active, role) for the account, or None if it does not exist."""
    key = (email or "").strip().lower()
    now = time.monotonic()
    with _accounts_lock:
        hit = _accounts.get(key)
        if hit and now - hit[0] < _ACCOUNT_TTL:
            return hit[1]
    from app.core.database import SessionLocal
    from app.services.database_service import DatabaseService

    with SessionLocal() as db:
        user = DatabaseService.get_user_by_email(db, key)
        found = (bool(user.is_active), user.role or "VIEWER") if user is not None else None
    with _accounts_lock:
        _accounts[key] = (now, found)
    return found


def forget_account(email: str) -> None:
    """Drops the cached account so a change (disable, new role) applies at once."""
    with _accounts_lock:
        _accounts.pop((email or "").strip().lower(), None)


def _live_role(email: str) -> str:
    """The account's current role. Refuses removed and disabled accounts.

    Token claims are a snapshot taken at login, so the role is read from the
    account itself: a demoted user does not keep their old role for 8 hours.
    """
    account = _account(email)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    active, role = account
    if not active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled. Contact your administrator.",
        )
    return role


def assert_account_active(db: Session, email: str) -> None:
    """Raises 403 if the account backing a token has been disabled or deleted.

    Token claims are a snapshot taken at login, so any path that grants access
    from an existing token must re-check the account against the database.
    """
    from app.services.database_service import DatabaseService

    user = DatabaseService.get_user_by_email(db, email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account no longer exists"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled. Contact your administrator.",
        )


def require_roles(allowed_roles: List[str]):
    """Returns a FastAPI dependency that restricts access to specific roles."""

    async def _check_role(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.get('role')}' does not have access. Required: {allowed_roles}",
            )
        return current_user

    return _check_role


# Roles, ordered most to least privileged. Anything outside this set is rejected
# at user-creation time so a typo cannot mint an account that matches no policy.
VALID_ROLES = ["ADMIN", "MANAGER", "OPERATOR", "VIEWER"]
VALID_FACILITIES = ["HQ", "EDMONTON", "ZIBO"]

# Convenience role dependencies
require_admin = require_roles(["ADMIN"])
require_manager = require_roles(["ADMIN", "MANAGER"])
require_operator = require_roles(["ADMIN", "MANAGER", "OPERATOR"])
require_viewer = require_roles(VALID_ROLES)


# ---------------------------------------------------------------------------
# Admin Seeding
# ---------------------------------------------------------------------------


def seed_admin_user() -> None:
    """Creates the pre-seeded admin account if it doesn't exist yet.

    The password is only set when the account is created, except outside
    production where it is kept in step with ADMIN_PASSWORD (see below).
    """
    from app.core.database import get_db
    from app.services.database_service import DatabaseService

    db_gen = get_db()
    db = next(db_gen)
    try:
        existing = DatabaseService.get_user_by_email(db, settings.ADMIN_EMAIL)
        if not existing:
            DatabaseService.create_user(
                db=db,
                email=settings.ADMIN_EMAIL,
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                full_name=settings.ADMIN_FULL_NAME,
                role="ADMIN",
                facility="HQ",
            )
            logger.info(f"Pre-seeded admin account: {settings.ADMIN_EMAIL}")
        elif not settings.is_production:
            # Development convenience only: keeps the seeded admin usable when
            # ADMIN_PASSWORD in .env changes. In production an existing admin's
            # password is never touched, so a password changed after first
            # boot is not silently reverted on every restart.
            try:
                if not verify_password(settings.ADMIN_PASSWORD, existing.hashed_password):
                    existing.hashed_password = hash_password(settings.ADMIN_PASSWORD)
                    db.commit()
                    logger.info(f"Refreshed admin password hash (development): {settings.ADMIN_EMAIL}")
            except Exception:
                existing.hashed_password = hash_password(settings.ADMIN_PASSWORD)
                db.commit()
                logger.info(f"Overwrote invalid admin password hash (development): {settings.ADMIN_EMAIL}")
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


# ---------------------------------------------------------------------------
# HMAC Webhook Verification (preserved from original)
# ---------------------------------------------------------------------------

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_hmac_sha256(payload_bytes: bytes, signature: str, secret: str) -> bool:
    """Verifies HMAC-SHA256 signature against signing secret."""
    if not secret:
        return True  # If no secret configured in dev mode, permit for local testing
    if not signature:
        return False

    try:
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_signature, signature)
    except Exception as exc:
        logger.error(f"HMAC verification exception: {exc}")
        return False


# How old an exp-less monday JWT may be, judged by its iat.
_MONDAY_JWT_MAX_AGE_SECONDS = 300


async def verify_monday_webhook(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> str:
    """FastAPI dependency authenticating a monday.com webhook delivery.

    monday never HMAC-signs webhook bodies. The previous check computed an
    HMAC of the body and compared it to the Authorization header, which can
    never match what monday sends: with a secret configured every real
    delivery was rejected, and without one everything was accepted.

    What monday actually provides:
      - App-created webhooks: Authorization carries a JWT signed (HS256) with
        the app's Signing Secret -> MONDAY_WEBHOOK_SECRET.
      - Plain board webhooks: nothing. The compensating control is a long
        random token in the registered URL -> MONDAY_WEBHOOK_TOKEN.

    Returns the method that authenticated the request.
    """
    # The URL token is checked first: a proxy that adds its own Authorization
    # header must not break a delivery that carries the right token.
    if settings.MONDAY_WEBHOOK_TOKEN:
        supplied = request.query_params.get("token", "").encode("utf-8")
        if supplied and hmac.compare_digest(supplied, settings.MONDAY_WEBHOOK_TOKEN.encode("utf-8")):
            return "url_token"

    if settings.MONDAY_WEBHOOK_SECRET and authorization:
        token = authorization.split(" ", 1)[1] if authorization.lower().startswith("bearer ") else authorization
        try:
            claims = jwt.decode(
                token,
                settings.MONDAY_WEBHOOK_SECRET,
                algorithms=["HS256"],
                # aud is the URL monday called, which differs behind a proxy;
                # the signature and expiry are what prove origin. exp is
                # verified whenever present, but not required: monday does not
                # document exp for every webhook flavour.
                options={"verify_aud": False},
            )
            if "exp" not in claims:
                # Without exp, a fresh iat is the only replay bound. A token
                # carrying neither could be replayed forever, so it is refused.
                iat = claims.get("iat")
                now = datetime.now(timezone.utc).timestamp()
                if not isinstance(iat, (int, float)) or abs(now - iat) > _MONDAY_JWT_MAX_AGE_SECONDS:
                    raise jwt.InvalidTokenError("token carries neither exp nor a recent iat")
            return "jwt"
        except jwt.InvalidTokenError as exc:
            logger.warning(f"Rejected monday webhook: invalid JWT ({exc})")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token.")

    if settings.MONDAY_WEBHOOK_SECRET or settings.MONDAY_WEBHOOK_TOKEN:
        logger.warning("Rejected unauthenticated monday webhook request")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook credentials.",
        )

    if settings.is_production:
        # Fail closed: an open endpoint here can create Sage documents.
        logger.error("monday webhook refused: no MONDAY_WEBHOOK_SECRET or MONDAY_WEBHOOK_TOKEN in production.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook authentication is not configured.",
        )
    return "unauthenticated_dev"


async def get_api_key(
    api_key_header: Optional[str] = Security(API_KEY_HEADER),
) -> Optional[str]:
    """Validates optional internal API key for direct management endpoints."""
    if settings.DEBUG or not settings.SECRET_KEY:
        return api_key_header

    if api_key_header and hmac.compare_digest(api_key_header, settings.SECRET_KEY):
        return api_key_header

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Could not validate credentials.",
    )

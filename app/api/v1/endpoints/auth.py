"""Authentication API endpoints: login, refresh, profile, and user management."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    VALID_FACILITIES,
    VALID_ROLES,
    assert_account_active,
    create_access_token,
    create_refresh_token,
    decode_token,
    forget_account,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.core.audit import audit_event
from app.core import login_guard, security
from app.config import settings
from app.models.auth import (
    CreateUserRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UpdateUserRequest,
    UserProfile,
)
from app.models.db_models import UserDB
from app.services.database_service import DatabaseService

router = APIRouter()

_DUMMY = {}


def _dummy_hash() -> str:
    if "h" not in _DUMMY:
        _DUMMY["h"] = hash_password("not-a-real-account-password")
    return _DUMMY["h"]


def _trusted(ip: str) -> bool:
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped                      # what a dual-stack listener reports for an IPv4 caller
    for net in str(settings.TRUSTED_PROXY_NETS or "").split(","):
        try:
            if net.strip() and addr in ipaddress.ip_network(net.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def _client_address(http: Request) -> str:
    """The caller's address. The forwarded-for header is only believed for the hops our own
    proxies added: the rightmost entry that is not one of them. Anything the caller put at
    the front of the header is ignored, so it cannot be used to dodge the sign-in lock."""
    peer = http.client.host if http.client else ""
    if not _trusted(peer):
        return peer
    for entry in reversed([e.strip() for e in (http.headers.get("x-forwarded-for") or "").split(",") if e.strip()]):
        if not _trusted(entry):
            return entry[:100]
    return peer


def _refuse_if_locked(email: str, address: str) -> None:
    wait = login_guard.reserve(email, address)            # also holds a place while the password is checked, so a burst of simultaneous guesses cannot all pass the lock check
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many wrong passwords. Try again in {max(1, wait // 60)} minute(s).",
            headers={"Retry-After": str(wait)},
        )


def _checked(password: str) -> str:
    _refuse_weak(password)
    return password


def _refuse_weak(new_password: str, current_password: str = None) -> None:
    problem = login_guard.password_problem(new_password, current_password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)


@router.post("/login", response_model=TokenResponse, summary="Authenticate and obtain JWT tokens")
def login(request: LoginRequest, http: Request, db: Session = Depends(get_db)):
    """Authenticates a user with email/password and returns JWT access + refresh tokens."""
    email = request.email.strip().lower()
    address = _client_address(http)
    _refuse_if_locked(email, address)
    user = DatabaseService.get_user_by_email(db, email)
    if not user and email == settings.ADMIN_EMAIL.strip().lower():
        from app.core.security import seed_admin_user
        seed_admin_user()
        user = DatabaseService.get_user_by_email(db, email)

    # An unknown account is hashed against a dummy too, so it takes as long to refuse.
    password_ok = verify_password(request.password, user.hashed_password if user else _dummy_hash())
    if not user or not password_ok:
        login_guard.record_failure(email, address)
        audit_event(
            db=db,
            action="LOGIN_FAILED",
            resource_type="USER",
            resource_id=email,
            actor_email=email,
            actor_role=user.role if user else "GUEST",
            facility=user.facility if user else "",
            detail="Invalid email or password",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled. Contact your administrator.",
        )

    login_guard.record_success(email, address)
    token_data = {
        "sub": user.email,
        "name": user.full_name,
        "role": user.role,
        "facility": user.facility,
        "pv": login_guard.password_stamp(user.hashed_password),
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)
    from app.core import refresh_state

    refresh_state.register(user.email, refresh_token)                       # remembered as this person's latest, so an older one reused later is noticed

    DatabaseService.update_last_login(db, user)

    audit_event(
        db=db,
        action="LOGIN",
        resource_type="USER",
        resource_id=user.email,
        actor_email=user.email,
        actor_role=user.role,
        facility=user.facility,
        detail=f"User logged in: {user.full_name}",
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserProfile(
            user_id=user.user_id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            facility=user.facility,
            is_active=user.is_active,
            last_login=user.last_login.isoformat() if user.last_login else None,
        ),
    )


@router.post("/refresh", response_model=dict, summary="Refresh an access token")
def refresh_token(request: RefreshRequest, http: Request, db: Session = Depends(get_db)):
    """Exchanges a valid refresh token for a new access token.

    The account is re-checked against the database on every refresh: a refresh
    token outlives the access token by weeks, so a disabled or deleted account
    must not be able to keep minting access tokens from one.
    """
    payload = decode_token(request.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type. Expected refresh token.",
        )

    email = payload.get("sub") or ""
    assert_account_active(db, email)
    user = DatabaseService.get_user_by_email(db, email)

    # Re-read role and facility from the database so that a demotion takes
    # effect immediately instead of persisting for the life of the token.
    stamp = payload.get("pv")
    # A refresh token made before tokens carried a password stamp cannot be checked against a
    # password change, so it is not renewed: the person signs in once more.
    if stamp is None or stamp != login_guard.password_stamp(user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The password was changed. Sign in again.",
        )
    token_data = {
        "sub": user.email,
        "name": user.full_name,
        "role": user.role,
        "facility": user.facility,
        "pv": login_guard.password_stamp(user.hashed_password),
    }
    from app.core import refresh_state

    address = _client_address(http)
    if float(payload.get("iat") or 0) < refresh_state.revoked_before(user.email):                 # a token from before this person's sessions were ended
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your sessions were ended for safety. Sign in again.")
    decision = refresh_state.exchange(user.email, payload.get("jti"))
    if decision["action"] == "theft":
        audit_event(db=db, action="REFRESH_REUSE_DETECTED", resource_type="USER", resource_id=user.email, actor_email=user.email, actor_role=user.role, facility=user.facility or "HQ",
                    detail="An old refresh token was used again after its grace window; every session of this person was ended", ip_address=address)
        security.forget_account(user.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This sign-in was used twice, so all your sessions were ended for safety. Sign in again.")
    new_access = create_access_token(token_data)
    # A fresh refresh token comes back too, so a person who keeps working is never asked to sign in again within the refresh window (it slides forward with use). Inside the grace window (parallel
    # refreshes from one page load) the SAME current token is signed again, so nothing forks.
    if decision["action"] == "same":
        new_refresh = create_refresh_token(token_data, jti=decision["jti"], expires_at=decision.get("exp") or None)
    else:
        new_refresh = create_refresh_token(token_data, jti=decision["jti"])
        refresh_state._set_latest_exp(user.email, new_refresh) if not decision.get("unrecorded") else None
    audit_event(db=db, action="TOKEN_REFRESH", resource_type="USER", resource_id=user.email, actor_email=user.email, actor_role=user.role, facility=user.facility or "HQ",
                detail="Access token renewed" + (" (parallel request inside the grace window)" if decision["action"] == "same" else ""), ip_address=address)
    return {"access_token": new_access, "token_type": "Bearer", "refresh_token": new_refresh}


@router.get("/profile", response_model=UserProfile, summary="Get current user profile")
def get_profile(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns the authenticated user's profile information."""
    assert_account_active(db, current_user["email"])
    user = DatabaseService.get_user_by_email(db, current_user["email"])

    return UserProfile(
        user_id=user.user_id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        facility=user.facility,
        is_active=user.is_active,
        last_login=user.last_login.isoformat() if user.last_login else None,
    )


@router.post("/users", response_model=UserProfile, summary="Create a new user (Admin only)")
def create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
):
    """Creates a new user account. Requires ADMIN role."""
    role = request.role.strip().upper()
    facility = request.facility.strip().upper()
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{request.role}'. Must be one of: {VALID_ROLES}",
        )
    if facility not in VALID_FACILITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid facility '{request.facility}'. Must be one of: {VALID_FACILITIES}",
        )

    existing = DatabaseService.get_user_by_email(db, request.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email {request.email} already exists",
        )

    user = DatabaseService.create_user(
        db=db,
        email=request.email,
        hashed_password=hash_password(_checked(request.password)),
        full_name=request.full_name,
        role=role,
        facility=facility,
    )

    audit_event(
        db=db,
        action="CREATE",
        resource_type="USER",
        resource_id=user.email,
        actor_email=admin["email"],
        actor_role=admin["role"],
        detail=f"Created user: {user.full_name} ({user.role} @ {user.facility})",
    )

    return UserProfile(
        user_id=user.user_id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        facility=user.facility,
        is_active=user.is_active,
    )


def _profile(user: UserDB) -> UserProfile:
    return UserProfile(
        user_id=user.user_id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        facility=user.facility,
        is_active=bool(user.is_active),
        last_login=user.last_login.isoformat() if user.last_login else None,
    )


@router.get("/users", response_model=list[UserProfile], summary="List user accounts (Admin only)")
def list_users(db: Session = Depends(get_db), _admin: dict = Depends(require_admin)):
    """Every account, active ones first, then by name."""
    users = db.query(UserDB).order_by(UserDB.is_active.desc(), UserDB.full_name).all()
    return [_profile(u) for u in users]


@router.patch("/users/{email}", response_model=UserProfile, summary="Change a user account (Admin only)")
def update_user(
    email: str,
    request: UpdateUserRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
):
    """Changes name, role, site, active state or password. Every change is audited.

    An admin cannot demote or disable their own account, and the last active
    admin cannot be demoted or disabled: either would leave nobody able to
    manage accounts.
    """
    user = DatabaseService.get_user_by_email(db, email)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No account for {email}")

    role = request.role.strip().upper() if request.role is not None else user.role
    facility = request.facility.strip().upper() if request.facility is not None else user.facility
    active = request.is_active if request.is_active is not None else bool(user.is_active)
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{request.role}'. Must be one of: {VALID_ROLES}",
        )
    if facility not in VALID_FACILITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid facility '{request.facility}'. Must be one of: {VALID_FACILITIES}",
        )

    losing_admin = user.role == "ADMIN" and user.is_active and (role != "ADMIN" or not active)
    if losing_admin and user.email == (admin.get("email") or "").strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't remove your own admin access. Ask another admin to do it.",
        )
    if losing_admin:
        admins = db.query(UserDB).filter(UserDB.role == "ADMIN", UserDB.is_active.is_(True)).count()
        if admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This is the last active admin. Make someone else an admin first.",
            )

    changes = []
    if request.full_name is not None and request.full_name.strip() and request.full_name.strip() != user.full_name:
        changes.append(f"name '{user.full_name}' -> '{request.full_name.strip()}'")
        user.full_name = request.full_name.strip()
    if role != user.role:
        changes.append(f"role {user.role} -> {role}")
        user.role = role
    if facility != user.facility:
        changes.append(f"site {user.facility} -> {facility}")
        user.facility = facility
    if active != bool(user.is_active):
        changes.append("enabled" if active else "disabled")
        user.is_active = active
    if request.new_password:
        changes.append("password reset")
        user.hashed_password = hash_password(_checked(request.new_password))
        login_guard.unlock(user.email)

    if changes:
        db.commit()
        db.refresh(user)
        # The role and active state are cached per account for a short time;
        # drop it so the change applies to their very next request.
        forget_account(user.email)
        audit_event(
            db=db,
            action="UPDATE",
            resource_type="USER",
            resource_id=user.email,
            actor_email=admin["email"],
            actor_role=admin["role"],
            detail=f"Changed {user.email}: " + "; ".join(changes),
        )
    return _profile(user)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/change-password", summary="Change your own password")
def change_password(
    request: ChangePasswordRequest,
    http: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Allows any signed-in staff member to update their own password."""
    email = (current_user.get("email") or "").strip().lower()
    address = _client_address(http)
    _refuse_if_locked(email, address)
    user = DatabaseService.get_user_by_email(db, email)
    if not user or not verify_password(request.current_password, user.hashed_password):
        login_guard.record_failure(email, address)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect.")
    _refuse_weak(request.new_password, request.current_password)
    user.hashed_password = hash_password(request.new_password)
    db.commit()
    forget_account(user.email)
    audit_event(
        db=db,
        action="PASSWORD_CHANGED",
        resource_type="USER",
        resource_id=user.email,
        actor_email=user.email,
        actor_role=user.role,
        facility=user.facility,
        detail="User changed their own password",
    )
    token_data = {"sub": user.email, "name": user.full_name, "role": user.role, "facility": user.facility,
                  "pv": login_guard.password_stamp(user.hashed_password)}
    # Every older session ended with the old password; these two keep this one going.
    return {"ok": True, "access_token": create_access_token(token_data), "refresh_token": create_refresh_token(token_data)}

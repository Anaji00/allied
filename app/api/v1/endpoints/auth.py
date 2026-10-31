"""Authentication API endpoints: login, refresh, profile, and user management."""

from fastapi import APIRouter, Depends, HTTPException, status
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


@router.post("/login", response_model=TokenResponse, summary="Authenticate and obtain JWT tokens")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    """Authenticates a user with email/password and returns JWT access + refresh tokens."""
    email = request.email.strip().lower()
    user = DatabaseService.get_user_by_email(db, email)
    if not user and email == settings.ADMIN_EMAIL.strip().lower():
        from app.core.security import seed_admin_user
        seed_admin_user()
        user = DatabaseService.get_user_by_email(db, email)

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled. Contact your administrator.",
        )

    token_data = {
        "sub": user.email,
        "name": user.full_name,
        "role": user.role,
        "facility": user.facility,
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

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
def refresh_token(request: RefreshRequest, db: Session = Depends(get_db)):
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
    token_data = {
        "sub": user.email,
        "name": user.full_name,
        "role": user.role,
        "facility": user.facility,
    }
    new_access = create_access_token(token_data)
    return {"access_token": new_access, "token_type": "Bearer"}


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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{request.role}'. Must be one of: {VALID_ROLES}",
        )
    if facility not in VALID_FACILITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
        hashed_password=hash_password(request.password),
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{request.role}'. Must be one of: {VALID_ROLES}",
        )
    if facility not in VALID_FACILITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
        user.hashed_password = hash_password(request.new_password)

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

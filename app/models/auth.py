"""Pydantic schemas for authentication, authorization, and user management."""

from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials for JWT token acquisition."""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class TokenResponse(BaseModel):
    """JWT access and refresh token pair."""
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(..., description="Access token lifetime in seconds")
    user: "UserProfile"


class RefreshRequest(BaseModel):
    """Request to exchange a refresh token for a new access token."""
    refresh_token: str


class UserProfile(BaseModel):
    """Public user profile returned after authentication."""
    user_id: str
    email: str
    full_name: str
    role: str  # ADMIN, MANAGER, OPERATOR, VIEWER
    facility: str  # HQ, EDMONTON, ZIBO
    is_active: bool = True
    last_login: Optional[str] = None


class CreateUserRequest(BaseModel):
    """Admin-only request to create a new user account."""
    email: str
    password: str = Field(..., min_length=8)
    full_name: str
    role: str = "VIEWER"
    facility: str = "HQ"


class UpdateUserRequest(BaseModel):
    """Admin-only change to an account. Fields left out stay as they are."""
    full_name: Optional[str] = None
    role: Optional[str] = None
    facility: Optional[str] = None
    is_active: Optional[bool] = None
    # Sets a new password, e.g. after a person forgets theirs; never read back.
    new_password: Optional[str] = Field(None, min_length=8)


# Resolve forward reference
TokenResponse.model_rebuild()

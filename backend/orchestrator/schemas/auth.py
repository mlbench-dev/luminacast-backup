import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import datetime
from typing import Optional

_SPECIAL_CHAR_RE = re.compile(r"[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\\/;'`~]")


def _validate_password_strength(password: str) -> str:
    """Shared strength check for RegisterRequest and ResetPasswordRequest.

    Field(min_length=8) already rejects short passwords before this runs;
    the length check here is just a clearer error message for that case.
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not any(c.isupper() for c in password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not _SPECIAL_CHAR_RE.search(password):
        raise ValueError("Password must contain at least one special character")
    return password


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class MessageResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_password_strength(cls, v: str) -> str:
        return _validate_password_strength(v)


class DeleteAccountRequest(BaseModel):
    current_password: str


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = Field(None, max_length=100)

    @field_validator("display_name")
    @classmethod
    def _strip_and_none_if_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class WorkspaceInfo(BaseModel):
    """The workspace the caller's current token is acting in — resolved
    from the JWT's `wsid` claim. `is_own` is what the frontend's workspace
    switcher and role-based UI hiding key off of."""
    owner_id: str
    owner_label: str
    role: Optional[str] = None  # TeamRole value; null when is_own is True
    is_own: bool


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    stripe_customer_id: Optional[str] = None
    created_at: datetime
    is_active: bool
    display_name: Optional[str] = None
    # Surfaced to the frontend so the product detail page can decide
    # whether to show the "Connect Amazon Associates" amber callout.
    tiktok_affiliate_id: Optional[str] = None
    amazon_associate_tag: Optional[str] = None
    # Populated explicitly by every endpoint that returns this (not a plain
    # model-field passthrough) — `User.avatar_r2_key` is an R2 object key,
    # not a URL a browser can load directly.
    avatar_url: Optional[str] = None
    # Populated by GET /auth/me from the caller's current WorkspaceContext
    # — not a plain model field passthrough (Cast/Product/etc. don't carry
    # this), so it's set explicitly in the endpoint rather than relying on
    # from_attributes.
    workspace: Optional[WorkspaceInfo] = None

    model_config = {"from_attributes": True}
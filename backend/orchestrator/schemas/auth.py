from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    tiktok_handle: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 86400


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    tiktok_handle: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    created_at: datetime
    is_active: bool
    # Surfaced to the frontend so the product detail page can decide
    # whether to show the "Connect Amazon Associates" amber callout.
    tiktok_affiliate_id: Optional[str] = None
    amazon_associate_tag: Optional[str] = None

    model_config = {"from_attributes": True}

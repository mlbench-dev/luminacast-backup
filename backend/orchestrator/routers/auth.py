import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError
from passlib.context import CryptContext
from database import get_db
from config import settings
from models.user import User, UserRole
from schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserResponse,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
)
import sentry_sdk
from services import audit_log
from services.email_service import send_password_reset_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

TOKEN_EXPIRE_HOURS = 24
TOKEN_EXPIRE_HOURS_REMEMBER = 720  # 30 days
RESET_TOKEN_EXPIRE_MINUTES = 30


def create_access_token(data: dict, expire_hours: int = TOKEN_EXPIRE_HOURS) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.APP_SECRET_KEY, algorithm="HS256")


def _password_fingerprint(password_hash: str) -> str:
    """Short hash of the current password hash.

    Embedded in reset tokens so a token becomes worthless the moment the
    password actually changes — no separate token table/revocation list
    needed, the token self-invalidates on use.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def create_reset_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    data = {
        "sub": user.id,
        "purpose": "password_reset",
        "pwf": _password_fingerprint(user.password_hash),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(data, settings.APP_SECRET_KEY, algorithm="HS256")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = jwt.decode(credentials.credentials, settings.APP_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_creator_or_operator(user: User = Depends(get_current_user)) -> User:
    if user.role not in (UserRole.CREATOR, UserRole.OPERATOR):
        raise HTTPException(status_code=403, detail="Creator or operator access required")
    return user


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check duplicate
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    user = User(
        id=user_id,
        email=req.email,
        password_hash=pwd_context.hash(req.password),
        role=UserRole.CREATOR,
        tiktok_handle=req.tiktok_handle,
    )
    db.add(user)
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user_id, action="auth.register", entity_type="auth",
            entity_id=user_id, after={"email": req.email},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    token = create_access_token({"sub": user_id, "email": req.email, "role": "creator"})
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")

    expire_hours = TOKEN_EXPIRE_HOURS_REMEMBER if req.remember_me else TOKEN_EXPIRE_HOURS
    token = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
    }, expire_hours=expire_hours)
    try:
        await audit_log.record(
            db, user_id=user.id, action="auth.login", entity_type="auth",
            entity_id=user.id, after={"remember_me": bool(req.remember_me)},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return TokenResponse(access_token=token, expires_in=expire_hours * 3600)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(req: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Email a password reset link if the address belongs to an account.

    Always returns the same generic message regardless of whether the
    account exists, so this endpoint can't be used to enumerate emails.
    """
    generic = MessageResponse(message="If an account exists for that email, a password reset link has been sent.")

    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return generic

    reset_link = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={create_reset_token(user)}"
    try:
        await send_password_reset_email(user.email, reset_link)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("Failed to send password reset email to %s: %s", user.email, e)

    try:
        await audit_log.record(
            db, user_id=user.id, action="auth.forgot_password", entity_type="auth", entity_id=user.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return generic


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(req.token, settings.APP_SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    if payload.get("purpose") != "password_reset":
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    user = await db.get(User, payload.get("sub")) if payload.get("sub") else None
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    if payload.get("pwf") != _password_fingerprint(user.password_hash):
        # Password already changed since this link was issued (or reused).
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")

    user.password_hash = pwd_context.hash(req.password)
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="auth.reset_password", entity_type="auth", entity_id=user.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return MessageResponse(message="Your password has been reset. You can now sign in.")


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user

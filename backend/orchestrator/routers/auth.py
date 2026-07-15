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
from schemas.auth import RegisterRequest, LoginRequest, TokenResponse, UserResponse
import sentry_sdk
from services import audit_log

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

TOKEN_EXPIRE_HOURS = 24
TOKEN_EXPIRE_HOURS_REMEMBER = 720  # 30 days


def create_access_token(data: dict, expire_hours: int = TOKEN_EXPIRE_HOURS) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(hours=expire_hours)
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, settings.APP_SECRET_KEY, algorithm="HS256")


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


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user

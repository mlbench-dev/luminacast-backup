import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError
from passlib.context import CryptContext
from database import get_db
from config import settings
from models.user import User, UserRole, TeamMember, TeamRole, TeamMemberStatus
from schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, UserResponse,
    ForgotPasswordRequest, ResetPasswordRequest, MessageResponse,
    WorkspaceInfo, ChangePasswordRequest, DeleteAccountRequest,
)
import sentry_sdk
from services import audit_log
from services.email_service import send_password_reset_email, send_reactivation_email

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


def create_verification_token(user: User, purpose: str) -> str:
    """Short-lived, email-ownership-proving token for password_reset and
    reactivate_account. Embeds a fingerprint of the current password hash
    so the token self-invalidates the moment it's used (or the password
    changes some other way) — no separate revocation list needed."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    data = {
        "sub": user.id,
        "purpose": purpose,
        "pwf": _password_fingerprint(user.password_hash),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(data, settings.APP_SECRET_KEY, algorithm="HS256")


def create_reset_token(user: User) -> str:
    return create_verification_token(user, "password_reset")


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


# ── Teams: workspace resolution ─────────────────────────────────────────
#
# `wsid` (workspace id) is a JWT claim carrying which owner's workspace the
# caller is currently acting in — added alongside the existing sub/email/
# role claims. Missing claim (every token minted before this feature, and
# every token for a user who's never switched workspaces) means "acting as
# myself" — fully backward compatible, no forced re-login.
#
# Team-member requests (wsid != sub) re-check TeamMember.status == "active"
# on every single request rather than trusting the JWT claim alone — a
# cached claim can't make "revoke cuts access immediately" true on its own,
# and this is the one case where that guarantee actually matters. Owner
# requests (wsid == sub, the overwhelming majority of traffic) skip that
# lookup entirely: nothing revokes an owner's access to their own account.

_TEAM_ROLE_RANK = {
    TeamRole.VIEWER.value: 0,
    TeamRole.CREATOR.value: 1,
    TeamRole.PUBLISHER.value: 2,
}


@dataclass
class WorkspaceContext:
    workspace_owner_id: str  # scope every resource query by this
    actor_user_id: str  # the real caller — use for audit/attribution fields only
    actor_team_role: str | None  # None means the actor IS the workspace owner
    is_owner: bool


async def get_workspace_context(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceContext:
    try:
        payload = jwt.decode(credentials.credentials, settings.APP_SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    wsid = payload.get("wsid") or user.id
    if wsid == user.id:
        return WorkspaceContext(user.id, user.id, None, True)

    tm = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.owner_id == wsid,
                TeamMember.user_id == user.id,
                TeamMember.status == TeamMemberStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if not tm:
        raise HTTPException(status_code=403, detail="You no longer have access to this workspace")
    return WorkspaceContext(wsid, user.id, tm.role, False)


def require_role(min_role: str):
    """Dependency factory enforcing a minimum TeamRole. The owner always
    passes — the client's role table puts them above all three roles."""

    async def _dep(ctx: WorkspaceContext = Depends(get_workspace_context)) -> WorkspaceContext:
        if ctx.is_owner:
            return ctx
        if _TEAM_ROLE_RANK.get(ctx.actor_team_role, -1) < _TEAM_ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail=f"Requires {min_role} role or higher")
        return ctx

    return _dep


async def require_owner(ctx: WorkspaceContext = Depends(get_workspace_context)) -> WorkspaceContext:
    """Team management, billing, admin — owner only, no team role qualifies."""
    if not ctx.is_owner:
        raise HTTPException(status_code=403, detail="Owner access required")
    return ctx


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check duplicate
    result = await db.execute(select(User).where(User.email == req.email))
    existing = result.scalar_one_or_none()
    if existing and existing.is_active:
        raise HTTPException(status_code=409, detail="Email already registered")
    if existing and not existing.is_active:
        # A deactivated account owns this email. Register has no proof the
        # caller actually controls the inbox, so it can't reactivate
        # directly (that would let anyone "reactivate" someone else's
        # deleted account just by knowing their email). Instead, verify
        # ownership the same way forgot-password does: email a token-
        # bearing link; /auth/reactivate-account (below) does the actual
        # reactivation once that's clicked.
        reactivate_link = (
            f"{settings.FRONTEND_URL.rstrip('/')}/reactivate-account"
            f"?token={create_verification_token(existing, 'reactivate_account')}"
        )
        try:
            await send_reactivation_email(existing.email, reactivate_link)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error("Failed to send reactivation email to %s: %s", existing.email, e)
        raise HTTPException(
            status_code=403,
            detail="This email belongs to a deactivated account. Check your inbox for a link to reactivate it.",
        )

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    user = User(
        id=user_id,
        email=req.email,
        password_hash=pwd_context.hash(req.password),
        role=UserRole.CREATOR,
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


async def _resolve_login_workspace(user: User, db: AsyncSession) -> str:
    """Which workspace a fresh login should land in.

    Defaults to the user's own workspace. If they last worked in someone
    else's (an active team membership), land them back there instead of
    forcing a manual switch on every login — but only if that membership
    is still active; a revoked one silently falls back to "myself" rather
    than erroring the whole login.
    """
    last = user.last_workspace_id
    if not last or last == user.id:
        return user.id
    tm = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.owner_id == last,
                TeamMember.user_id == user.id,
                TeamMember.status == TeamMemberStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    return last if tm else user.id


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")

    wsid = await _resolve_login_workspace(user, db)
    expire_hours = TOKEN_EXPIRE_HOURS_REMEMBER if req.remember_me else TOKEN_EXPIRE_HOURS
    token = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "wsid": wsid,
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


@router.post("/reactivate-account", response_model=TokenResponse)
async def reactivate_account(req: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Completes the reactivation flow started when /register hit a
    deactivated email. Reuses ResetPasswordRequest's shape (token +
    new password) since it's the same "prove you own this inbox, then set
    a password" pattern as password reset — just also flips is_active.
    """
    try:
        payload = jwt.decode(req.token, settings.APP_SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired")

    if payload.get("purpose") != "reactivate_account":
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired")

    user = await db.get(User, payload.get("sub")) if payload.get("sub") else None
    if not user:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired")

    if user.is_active:
        raise HTTPException(status_code=400, detail="This account is already active — sign in instead")

    if payload.get("pwf") != _password_fingerprint(user.password_hash):
        # Password changed (or link reused) since this link was issued.
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired")

    user.is_active = True
    user.password_hash = pwd_context.hash(req.password)
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="auth.reactivate_account", entity_type="auth", entity_id=user.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    wsid = await _resolve_login_workspace(user, db)
    token = create_access_token({
        "sub": user.id, "email": user.email, "role": user.role.value, "wsid": wsid,
    })
    return TokenResponse(access_token=token)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service password change from within Settings — distinct from
    the forgot/reset-password email flow above, which is for a logged-out
    user. Requires the current password so a hijacked/left-open session
    can't lock the real owner out."""
    if not pwd_context.verify(req.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    user.password_hash = pwd_context.hash(req.new_password)
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="auth.change_password", entity_type="auth", entity_id=user.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return MessageResponse(message="Your password has been updated.")


@router.delete("/me", response_model=MessageResponse)
async def delete_account(
    req: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service account deletion from Settings.

    Admin accounts are exempt: the platform has exactly one bootstrapped
    ADMIN account (seed_admin() in main.py), so letting it self-delete
    would lock the whole app's /admin and /control surfaces with no way
    back short of re-running the bootstrap script.

    Deactivates rather than hard-deletes: `users` is referenced by ~60
    tables (casts, billing, avatars, usage, ...) and most of those FKs
    have no ON DELETE CASCADE, so a physical row delete isn't safe here.
    `is_active=False` is already enforced everywhere auth is checked
    (get_current_user, login, refresh, reset-password), so this fully
    revokes access rather than just flipping a cosmetic flag.
    """
    if user.role == UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin accounts cannot be deleted")

    if not pwd_context.verify(req.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    user.is_active = False
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="auth.delete_account", entity_type="auth", entity_id=user.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return MessageResponse(message="Your account has been deleted.")


@router.get("/me", response_model=UserResponse)
async def get_me(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(get_workspace_context),
    db: AsyncSession = Depends(get_db),
):
    resp = UserResponse.model_validate(user)
    if user.avatar_r2_key:
        from services.r2_storage import get_r2_storage_service
        resp.avatar_url = get_r2_storage_service().get_public_url(user.avatar_r2_key)
    if ctx.is_owner:
        resp.workspace = WorkspaceInfo(
            owner_id=user.id,
            owner_label=user.display_name or user.email,
            role=None,
            is_own=True,
        )
    else:
        owner = await db.get(User, ctx.workspace_owner_id)
        resp.workspace = WorkspaceInfo(
            owner_id=ctx.workspace_owner_id,
            owner_label=(owner.display_name or owner.email) if owner else ctx.workspace_owner_id,
            role=ctx.actor_team_role,
            is_own=False,
        )
    return resp

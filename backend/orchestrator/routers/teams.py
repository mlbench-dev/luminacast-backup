"""Teams — invite members into an owner's workspace, manage roles/access.

Mirrors the password-reset token pattern in routers/auth.py: a stateless
signed JWT carries the invite, no separate token table. Unlike a reset
token there's no password hash to fingerprint yet, so self-invalidation
comes from TeamMember.status instead — the token is only honored while
that row is still "pending".
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError, jwt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.user import TeamMember, TeamMemberStatus, TeamRole, User, UserRole
from routers.auth import (
    WorkspaceContext,
    create_access_token,
    get_current_user,
    get_workspace_context,
    pwd_context,
    require_owner,
)
from schemas.teams import (
    AcceptInvitePreviewResponse,
    AcceptInviteRequest,
    ChangeRoleRequest,
    InviteMemberRequest,
    MyWorkspacesResponse,
    SwitchWorkspaceRequest,
    TeamMemberResponse,
    TeamMembersListResponse,
    WorkspaceOption,
)
from services import audit_log
from services.email_service import send_team_invite_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams", tags=["teams"])

INVITE_TOKEN_EXPIRE_DAYS = 7


def _owner_label(owner: User) -> str:
    return owner.display_name or owner.email


def _create_invite_token(team_member_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=INVITE_TOKEN_EXPIRE_DAYS)
    data = {
        "sub": team_member_id,
        "email": email,
        "purpose": "team_invite",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(data, settings.APP_SECRET_KEY, algorithm="HS256")


def _member_to_response(tm: TeamMember, email: str, display_name: Optional[str]) -> TeamMemberResponse:
    return TeamMemberResponse(
        id=tm.id,
        email=email,
        display_name=display_name,
        role=tm.role,
        status=tm.status,
        invited_at=tm.invited_at,
        accepted_at=tm.accepted_at,
    )


@router.get("/members", response_model=TeamMembersListResponse)
async def list_members(
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(TeamMember)
            .where(TeamMember.owner_id == ctx.workspace_owner_id)
            .order_by(TeamMember.invited_at.desc())
        )
    ).scalars().all()

    members = []
    for tm in rows:
        if tm.user_id:
            user = await db.get(User, tm.user_id)
            email = user.email if user else (tm.invited_email or "")
            display_name = user.display_name if user else None
        else:
            email = tm.invited_email or ""
            display_name = None
        members.append(_member_to_response(tm, email, display_name))
    return TeamMembersListResponse(members=members)


@router.post("/invite", response_model=TeamMemberResponse, status_code=201)
async def invite_member(
    req: InviteMemberRequest,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    if req.email.lower() == (await db.get(User, ctx.workspace_owner_id)).email.lower():
        raise HTTPException(400, "You can't invite yourself.")

    existing = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.owner_id == ctx.workspace_owner_id,
                func.lower(TeamMember.invited_email) == req.email.lower(),
                TeamMember.status != TeamMemberStatus.REVOKED.value,
            )
        )
    ).scalar_one_or_none()

    existing_user = (
        await db.execute(select(User).where(User.email == req.email))
    ).scalar_one_or_none()

    if existing and existing.status == TeamMemberStatus.ACTIVE.value:
        # Already accepted — don't silently resend an invite email (and a
        # confusing "reissue" of an invite) to someone who's already a
        # member. Bug: this branch used to be missing entirely, so an
        # accepted member's row fell into the same "idempotent resend" path
        # as a still-pending one below, quietly emailing them again.
        raise HTTPException(409, "This email is already an active team member.")

    if existing:
        # Idempotent resend — update role/timestamp, reissue the invite
        # rather than create a duplicate row for the same email.
        tm = existing
        tm.role = req.role
        tm.invited_at = datetime.utcnow()
        if existing_user and not tm.user_id:
            tm.user_id = existing_user.id
    else:
        tm = TeamMember(
            id=f"tm_{uuid.uuid4().hex[:12]}",
            owner_id=ctx.workspace_owner_id,
            user_id=existing_user.id if existing_user else None,
            invited_email=req.email,
            role=req.role,
            status=TeamMemberStatus.PENDING.value,
        )
        db.add(tm)

    await db.commit()

    owner = await db.get(User, ctx.workspace_owner_id)
    accept_link = f"{settings.FRONTEND_URL.rstrip('/')}/accept-invite?token={_create_invite_token(tm.id, req.email)}"
    try:
        await send_team_invite_email(req.email, accept_link, _owner_label(owner), req.role)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("Failed to send team invite email to %s: %s", req.email, e)

    try:
        await audit_log.record(
            db, user_id=ctx.actor_user_id, action="team.invite", entity_type="team_member",
            entity_id=tm.id, after={"email": req.email, "role": req.role},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _member_to_response(tm, req.email, existing_user.display_name if existing_user else None)


@router.patch("/members/{member_id}/role", response_model=TeamMemberResponse)
async def change_member_role(
    member_id: str,
    req: ChangeRoleRequest,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    tm = await db.get(TeamMember, member_id)
    if not tm or tm.owner_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Team member not found")

    before_role = tm.role
    tm.role = req.role
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=ctx.actor_user_id, action="team.change_role", entity_type="team_member",
            entity_id=tm.id, before={"role": before_role}, after={"role": req.role},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    if tm.user_id:
        user = await db.get(User, tm.user_id)
        email, display_name = user.email, user.display_name
    else:
        email, display_name = tm.invited_email or "", None
    return _member_to_response(tm, email, display_name)


@router.delete("/members/{member_id}", status_code=204)
async def revoke_member(
    member_id: str,
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    """Revoke access. The row is kept (status flips, never deleted) so a
    live TeamMember check on the member's very next request — regardless
    of how long their JWT has left — fails immediately. See
    routers.auth.get_workspace_context."""
    tm = await db.get(TeamMember, member_id)
    if not tm or tm.owner_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Team member not found")

    tm.status = TeamMemberStatus.REVOKED.value
    tm.revoked_at = datetime.utcnow()
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=ctx.actor_user_id, action="team.revoke", entity_type="team_member",
            entity_id=tm.id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)


def _decode_invite_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(400, "This invite link is invalid or has expired")
    if payload.get("purpose") != "team_invite":
        raise HTTPException(400, "This invite link is invalid or has expired")
    return payload


@router.get("/accept-invite/preview", response_model=AcceptInvitePreviewResponse)
async def preview_invite(token: str, db: AsyncSession = Depends(get_db)):
    payload = _decode_invite_token(token)
    tm = await db.get(TeamMember, payload.get("sub"))
    if not tm or tm.status != TeamMemberStatus.PENDING.value:
        # Also covers reuse: once accepted/revoked, status is no longer
        # "pending" and the same token permanently stops working.
        raise HTTPException(400, "This invite link is invalid or has expired")

    owner = await db.get(User, tm.owner_id)
    requires_password = tm.user_id is None
    return AcceptInvitePreviewResponse(
        email=payload.get("email", tm.invited_email or ""),
        owner_label=_owner_label(owner) if owner else "your team",
        role=tm.role,
        requires_password=requires_password,
    )


@router.post("/accept-invite", response_model=dict)
async def accept_invite(req: AcceptInviteRequest, db: AsyncSession = Depends(get_db)):
    payload = _decode_invite_token(req.token)
    tm = await db.get(TeamMember, payload.get("sub"))
    if not tm or tm.status != TeamMemberStatus.PENDING.value:
        raise HTTPException(400, "This invite link is invalid or has expired")

    email = payload.get("email") or tm.invited_email
    if not email:
        raise HTTPException(400, "This invite link is invalid or has expired")

    if tm.user_id:
        user = await db.get(User, tm.user_id)
        if not user:
            raise HTTPException(400, "This invite link is invalid or has expired")
    else:
        if not req.password:
            raise HTTPException(400, "A password is required to accept this invite")
        existing = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing:
            # Someone registered directly between invite and accept —
            # attach rather than create a duplicate account for the email.
            user = existing
        else:
            user = User(
                id=f"usr_{uuid.uuid4().hex[:12]}",
                email=email,
                password_hash=pwd_context.hash(req.password),
                role=UserRole.CREATOR,
            )
            db.add(user)
            await db.flush()
        tm.user_id = user.id

    tm.status = TeamMemberStatus.ACTIVE.value
    tm.accepted_at = datetime.utcnow()
    user.last_workspace_id = tm.owner_id
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="team.accept_invite", entity_type="team_member",
            entity_id=tm.id, after={"owner_id": tm.owner_id, "role": tm.role},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    token = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "wsid": tm.owner_id,
    })
    return {"access_token": token, "token_type": "bearer"}


@router.get("/my-workspaces", response_model=MyWorkspacesResponse)
async def my_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    options = [WorkspaceOption(owner_id=user.id, owner_label=_owner_label(user), role=None, is_own=True)]

    memberships = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.user_id == user.id,
                TeamMember.status == TeamMemberStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    for tm in memberships:
        owner = await db.get(User, tm.owner_id)
        if not owner:
            continue
        options.append(
            WorkspaceOption(owner_id=owner.id, owner_label=_owner_label(owner), role=tm.role, is_own=False)
        )
    return MyWorkspacesResponse(workspaces=options)


@router.post("/switch-workspace")
async def switch_workspace(
    req: SwitchWorkspaceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.owner_id != user.id:
        tm = (
            await db.execute(
                select(TeamMember).where(
                    TeamMember.owner_id == req.owner_id,
                    TeamMember.user_id == user.id,
                    TeamMember.status == TeamMemberStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if not tm:
            raise HTTPException(403, "You don't have access to that workspace")

    user.last_workspace_id = req.owner_id
    await db.commit()

    token = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "wsid": req.owner_id,
    })
    return {"access_token": token, "token_type": "bearer"}

"""Zernio social-media endpoints.

These power the Publish, Published, and Comments pages on the frontend.
All write actions require an authenticated user and a configured
ZERNIO_API_KEY; if the key is missing we return 503 instead of 500 so the
frontend can show an "Connect your social-media integration" prompt.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.cast import Cast, CastApprovalStatus
from models.cast_render import CastRender, CastRenderStatus
from models.product import Product
from models.avatar import Avatar
from models.social_post import SocialPost, SocialComment, SocialChannel, PendingSocialConnect
from models.user import User, TeamRole
from routers.auth import get_current_user, WorkspaceContext, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social", tags=["social"])


def _post_to_dict(p: SocialPost) -> dict[str, Any]:
    return {
        "id": p.id,
        "user_id": p.user_id,
        "cast_id": p.cast_id,
        "render_id": p.render_id,
        "zernio_post_id": p.zernio_post_id,
        "caption": p.caption or "",
        "hashtags": p.hashtags or [],
        "media_url": p.media_url,
        "platforms": p.platforms or [],
        "scheduled_for": p.scheduled_for.isoformat() + "Z" if p.scheduled_for else None,
        "status": p.status,
        "platform_post_ids": p.platform_post_ids or {},
        "analytics": p.analytics or {},
        "error_message": p.error_message,
        "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
        "published_at": p.published_at.isoformat() + "Z" if p.published_at else None,
    }


def _comment_to_dict(c: SocialComment) -> dict[str, Any]:
    return {
        "id": c.id,
        "social_post_id": c.social_post_id,
        "platform": c.platform,
        "platform_comment_id": c.platform_comment_id,
        "author_name": c.author_name,
        "author_handle": c.author_handle,
        "text": c.text or "",
        "ai_suggested_reply": c.ai_suggested_reply,
        "reply_status": c.reply_status,
        "actual_reply": c.actual_reply,
        "is_prompt_injection": c.is_prompt_injection,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "replied_at": c.replied_at.isoformat() if c.replied_at else None,
    }


def _require_zernio():
    """Return the Zernio service or raise 503 with a clear message."""
    from services.zernio import get_zernio_service
    svc = get_zernio_service()
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Zernio is not connected. Add ZERNIO_API_KEY to enable publishing.",
        )
    return svc


def _raise_zernio_error(exc: Exception | str, action: str) -> None:
    """Surface a Zernio failure to the admin, show the user a generic message.

    Zernio is a single, platform-wide API key (settings.ZERNIO_API_KEY) —
    there's no per-user/per-workspace account, so any error it returns
    (including its own plan/quota limits) is about Luminacast's shared
    Zernio account, not this specific user's plan. Showing Zernio's raw
    message ("Your Free plan allows 20 posts per month...") misleads the
    user into thinking it's their account or something they did — they have
    no way to fix it. Log the real detail to Sentry so someone on our side
    actually sees and acts on it, and tell the user only that the feature is
    temporarily unavailable.
    """
    sentry_sdk.capture_message(
        f"Zernio {action} failed — platform-wide Zernio account likely needs attention: {exc}",
        level="error",
    )
    raise HTTPException(
        502,
        "Publishing is temporarily unavailable. Please try again later.",
    )


# \u2500\u2500 Profiles \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


# ── Channels (Distribute hub: connected social accounts) ───────────────────

def _channel_to_dict(ch: SocialChannel) -> dict[str, Any]:
    avatar = ch.primary_avatar
    return {
        "id": ch.id,
        "user_id": ch.user_id,
        "platform": ch.platform,
        "platform_account_id": ch.platform_account_id,
        "handle": ch.handle,
        "display_name": ch.display_name,
        "follower_count": ch.follower_count or 0,
        "profile_image_url": ch.profile_image_url,
        "zernio_account_id": ch.zernio_account_id,
        "primary_avatar_id": ch.primary_avatar_id,
        "primary_avatar": (
            {
                "id": avatar.id,
                "name": avatar.name,
                "face_image_url": getattr(avatar, "face_image_url", None),
                "face_ref_key": getattr(avatar, "face_ref_key", None),
            }
            if avatar is not None
            else None
        ),
        "avatar_history": ch.avatar_history or [],
        "total_posts": ch.total_posts or 0,
        "total_scheduled": ch.total_scheduled or 0,
        "status": ch.status,
        "connected_at": ch.connected_at.isoformat() if ch.connected_at else None,
        "last_seen_at": ch.last_seen_at.isoformat() if ch.last_seen_at else None,
    }


async def _fetch_channel_rows(db: AsyncSession, user_id: str) -> list[SocialChannel]:
    return (
        await db.execute(
            select(SocialChannel)
            .where(SocialChannel.user_id == user_id)
            .options(selectinload(SocialChannel.primary_avatar))
            .order_by(SocialChannel.connected_at.desc())
        )
    ).scalars().all()


@router.get("/channels")
async def list_channels(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List the user's connected social-media channels (with avatar info).

    Reconciles against Zernio for rows THIS workspace already owns —
    refreshes handle/follower_count/status on existing rows, and marks rows
    "disconnected" when Zernio no longer reports them. Does NOT create new
    rows for Zernio accounts this workspace hasn't already claimed: Zernio
    is a single platform-wide API key with no per-customer concept at all,
    so auto-adopting "any account nobody's claimed yet" here used to mean
    the first workspace to load this page after ANY Luminacast customer
    connected a new account would silently annex it as their own — a real
    cross-customer data leak. New ownership is only ever established
    through the explicit connect-and-confirm flow (see connect_platform /
    confirm_connect below), never opportunistically during a list refresh.
    """
    rows = await _fetch_channel_rows(db, ctx.workspace_owner_id)

    try:
        svc = _require_zernio()
        zernio_accounts = await svc.list_profiles()
    except HTTPException:
        # Zernio not configured in this environment — DB is all we have.
        return {"channels": [_channel_to_dict(c) for c in rows]}
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("Zernio channel reconciliation failed, returning DB state: %s", exc)
        return {"channels": [_channel_to_dict(c) for c in rows]}

    by_zernio_id = {ch.zernio_account_id: ch for ch in rows if ch.zernio_account_id}
    seen_zernio_ids: set[str] = set()
    changed = False

    for acc in zernio_accounts:
        zid = acc.get("_id") or acc.get("id")
        platform = acc.get("platform")
        if not zid or not platform:
            continue
        seen_zernio_ids.add(zid)

        handle = acc.get("username") or acc.get("handle") or acc.get("screenName")
        display_name = acc.get("displayName") or acc.get("name") or handle
        follower_count = acc.get("followerCount") or acc.get("followers") or 0
        profile_image_url = (
            acc.get("profileImage") or acc.get("avatarUrl") or acc.get("profileImageUrl")
        )
        existing = by_zernio_id.get(zid)
        if existing is None:
            # Not one of this workspace's own channels — could belong to
            # any other Luminacast customer on the shared Zernio account.
            # Never adopt it here; see the docstring above.
            continue

        if existing.status != "active":
            existing.status = "active"
            changed = True
        if follower_count and existing.follower_count != follower_count:
            existing.follower_count = follower_count
            changed = True
        if handle and existing.handle != handle:
            existing.handle = handle
            changed = True
        if display_name and existing.display_name != display_name:
            existing.display_name = display_name
            changed = True
        if profile_image_url and existing.profile_image_url != profile_image_url:
            existing.profile_image_url = profile_image_url
            changed = True

    # Rows Zernio no longer reports were disconnected outside the app.
    for ch in rows:
        if (
            ch.zernio_account_id
            and ch.zernio_account_id not in seen_zernio_ids
            and ch.status == "active"
        ):
            ch.status = "disconnected"
            changed = True

    if changed:
        await db.commit()
        rows = await _fetch_channel_rows(db, ctx.workspace_owner_id)

    return {"channels": [_channel_to_dict(c) for c in rows]}


@router.delete("/channels/{channel_id}")
async def disconnect_channel(
    channel_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a channel — on Zernio's side, not just locally.

    Previously this only deleted the local row, leaving the account still
    connected on Zernio; the next /channels read (which reconciles against
    Zernio) simply recreated it as active, making disconnect look broken.
    We revoke on Zernio first, then mark the row disconnected (kept, not
    deleted, so avatar_history/post stats survive a reconnect later).
    """
    ch = await db.get(SocialChannel, channel_id)
    if not ch or ch.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Channel not found")

    if ch.zernio_account_id:
        try:
            svc = _require_zernio()
            await svc.disconnect_account(ch.zernio_account_id)
        except HTTPException:
            raise
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise HTTPException(502, f"Could not disconnect on Zernio: {exc}")

    ch.status = "disconnected"
    await db.commit()
    return {"ok": True}


@router.get("/channels/{channel_id}/avatar-check")
async def check_avatar_consistency(
    channel_id: str,
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Tell the frontend whether a channel has a different primary avatar.

    Returns one of:
      "match"        — channel.primary_avatar == avatar_id
      "new_channel"  — channel has no primary avatar yet
      "mismatch"     — channel.primary_avatar != avatar_id
    """
    ch = await db.get(SocialChannel, channel_id)
    if not ch or ch.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Channel not found")
    if not ch.primary_avatar_id:
        return {"status": "new_channel", "primary_avatar": None}
    if ch.primary_avatar_id == avatar_id:
        return {"status": "match", "primary_avatar_id": ch.primary_avatar_id}
    avatar = ch.primary_avatar
    return {
        "status": "mismatch",
        "primary_avatar_id": ch.primary_avatar_id,
        "primary_avatar": (
            {"id": avatar.id, "name": avatar.name}
            if avatar is not None
            else None
        ),
        "channel_total_posts": ch.total_posts or 0,
    }


@router.get("/profiles")
async def list_social_profiles(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List the user's connected social-media profiles via Zernio.

    Zernio's own /accounts list is platform-wide (single shared API key,
    no per-customer concept at all) — returning it unfiltered leaked every
    other Luminacast customer's connected accounts to whoever called this.
    Filter down to just the accounts this workspace has actually claimed
    via our own SocialChannel table.
    """
    svc = _require_zernio()
    try:
        accounts = await svc.list_profiles()
    except Exception as exc:
        logger.exception("Zernio list_profiles failed")
        _raise_zernio_error(exc, "list_profiles")
    owned_zernio_ids = {
        ch.zernio_account_id
        for ch in await _fetch_channel_rows(db, ctx.workspace_owner_id)
        if ch.zernio_account_id
    }
    return [
        acc for acc in accounts
        if (acc.get("_id") or acc.get("id")) in owned_zernio_ids
    ]


class ConnectPlatformRequest(BaseModel):
    platform: str  # tiktok | instagram | youtube | linkedin | facebook | twitter
    redirect_uri: Optional[str] = None


@router.post("/connect")
async def connect_platform(
    req: ConnectPlatformRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return an OAuth URL the frontend opens in a popup window.

    Zernio handles the OAuth dance and redirects the popup back to
    `redirect_uri` once the social account is linked. The frontend listens
    for a postMessage from the popup to know when to refresh the profile
    list.

    Also snapshots which accounts of this platform Zernio already knows
    about, BEFORE the user completes the OAuth flow — Zernio's redirect
    only ever carries platform+status, never which account was connected,
    so this is how confirm_connect (below) later figures out which new
    account belongs to this user rather than opportunistically adopting
    whatever's unclaimed (the actual cause of the cross-user leak this is
    fixing).
    """
    svc = _require_zernio()
    redirect_uri = (
        req.redirect_uri
        or "https://www.luminacast.com/integrations/zernio/callback"
    )
    try:
        before_accounts = await svc.list_profiles()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        before_accounts = []
    before_ids = [
        zid for acc in before_accounts
        if acc.get("platform") == req.platform and (zid := acc.get("_id") or acc.get("id"))
    ]
    # Replace any stale pending row for this (user, platform) rather than
    # accumulate — only the most recent connect attempt's snapshot matters.
    await db.execute(
        sa_delete(PendingSocialConnect).where(
            PendingSocialConnect.user_id == ctx.workspace_owner_id,
            PendingSocialConnect.platform == req.platform,
        )
    )
    db.add(PendingSocialConnect(
        id=f"psc_{uuid.uuid4().hex[:12]}",
        user_id=ctx.workspace_owner_id,
        platform=req.platform,
        before_zernio_account_ids=before_ids,
    ))
    await db.commit()

    try:
        result = await svc.get_oauth_url(req.platform, redirect_uri)
    except Exception as exc:
        logger.exception("Zernio.get_oauth_url failed")
        _raise_zernio_error(exc, "get_oauth_url")
    auth_url = result.get("authUrl") or result.get("url") or result.get("authorize_url")
    if not auth_url:
        raise HTTPException(502, "Zernio did not return an authUrl.")
    return {"auth_url": auth_url, "platform": req.platform}


class ConfirmConnectRequest(BaseModel):
    platform: str


@router.post("/connect/confirm")
async def confirm_connect(
    req: ConfirmConnectRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Attribute a just-completed OAuth connection to the correct user.

    Called by the frontend right after the OAuth popup reports success.
    Looks at every Zernio account for this platform and claims whichever
    ones aren't already owned by someone else — this is the ONLY place a
    SocialChannel row is ever created for a previously-unclaimed Zernio
    account; list_channels no longer does this opportunistically (see its
    docstring for why that was the leak). The before-snapshot
    connect_platform took is no longer used to gate this: ownership (is
    someone else's row already pointing at this account?) is the only
    thing that needs to be true for it to be safe, not timing (was it new
    since this specific attempt started?) — see the comment above
    other_users_claimed_ids for why the timing check was actively harmful.
    """
    pending = (
        await db.execute(
            select(PendingSocialConnect)
            .where(
                PendingSocialConnect.user_id == ctx.workspace_owner_id,
                PendingSocialConnect.platform == req.platform,
            )
            .order_by(PendingSocialConnect.created_at.desc())
        )
    ).scalars().first()

    # No pending snapshot (expired sweep window, direct API call, or the
    # popup reporting success without a prior /connect call) — nothing safe
    # to attribute. Silently no-op rather than guess.
    if pending is None:
        return {"claimed": False}

    # A stale abandoned attempt (user opened Connect, never finished, tried
    # again minutes later some other way) shouldn't be diffed against — 10
    # minutes comfortably covers a real OAuth round-trip.
    if pending.created_at and (datetime.utcnow() - pending.created_at) > timedelta(minutes=10):
        await db.delete(pending)
        await db.commit()
        return {"claimed": False}

    svc = _require_zernio()
    try:
        after_accounts = await svc.list_profiles()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return {"claimed": False}

    # Rows THIS user already has for this zernio_account_id, any status —
    # covers reconnecting a channel they previously disconnected. Zernio
    # keeps the same account _id across a disconnect/reconnect cycle, so
    # this account may not even look "new" against the before-snapshot (or
    # would be wrongly excluded by the other-user safety net below); either
    # way, seeing it again for the same user means "reactivate", not "new".
    own_rows_by_zid = {
        ch.zernio_account_id: ch
        for ch in (
            await db.execute(
                select(SocialChannel).where(
                    SocialChannel.user_id == ctx.workspace_owner_id,
                    SocialChannel.zernio_account_id.isnot(None),
                )
            )
        ).scalars().all()
    }
    # The only thing that actually needs to be true to safely claim an
    # account: nobody else already owns it. We used to also require it be
    # absent from the before-snapshot ("new since this attempt started"),
    # but that's strictly weaker AND actively harmful: if an earlier attempt
    # created the Zernio account but failed to save it locally (a slow/
    # erroring Zernio call, a lost race, anything), that account is real,
    # unclaimed, and permanently "not new" from then on — no future
    # reconnect could ever pick it up again, since every subsequent
    # before-snapshot would already contain it. Ownership, not timing, is
    # the only thing that matters for safety here.
    other_users_claimed_ids = {
        ch.zernio_account_id
        for ch in (
            await db.execute(
                select(SocialChannel).where(
                    SocialChannel.user_id != ctx.workspace_owner_id,
                    SocialChannel.zernio_account_id.isnot(None),
                )
            )
        ).scalars().all()
    }

    matching = [acc for acc in after_accounts if acc.get("platform") == req.platform]
    logger.info(
        "confirm_connect: user=%s platform=%s total_accounts=%d matching_platform=%s "
        "own_zids=%s other_users_zids=%s",
        ctx.workspace_owner_id, req.platform, len(after_accounts),
        [(a.get("_id") or a.get("id")) for a in matching],
        list(own_rows_by_zid.keys()), list(other_users_claimed_ids),
    )

    claimed = []
    for acc in after_accounts:
        if acc.get("platform") != req.platform:
            continue
        zid = acc.get("_id") or acc.get("id")
        if not zid:
            continue
        handle = acc.get("username") or acc.get("handle") or acc.get("screenName")
        display_name = acc.get("displayName") or acc.get("name") or handle
        follower_count = acc.get("followerCount") or acc.get("followers") or 0
        profile_image_url = acc.get("profileImage") or acc.get("avatarUrl") or acc.get("profileImageUrl")

        existing = own_rows_by_zid.get(zid)
        if existing is not None:
            existing.status = "active"
            existing.handle = handle
            existing.display_name = display_name
            existing.follower_count = follower_count
            existing.profile_image_url = profile_image_url
            claimed.append(zid)
            continue

        if zid in other_users_claimed_ids:
            continue

        new_ch = SocialChannel(
            id=f"sch_{uuid.uuid4().hex[:12]}",
            user_id=ctx.workspace_owner_id,
            platform=req.platform,
            platform_account_id=acc.get("platformAccountId") or acc.get("accountId"),
            handle=handle,
            display_name=display_name,
            follower_count=follower_count,
            profile_image_url=profile_image_url,
            zernio_account_id=zid,
            status="active",
        )
        db.add(new_ch)
        claimed.append(zid)

    # Only consume the pending snapshot once it's actually done its job.
    # Deleting it unconditionally here meant a retried call (ours or the
    # frontend's) would always find nothing: the very first attempt already
    # deleted it regardless of whether it found anything to claim, so every
    # retry after that was a guaranteed no-op "no pending snapshot" bail-out
    # rather than a real second look at Zernio. Leaving it in place when
    # nothing was claimed lets a genuine retry — or the user manually
    # clicking connect again within the 10-minute window — actually mean
    # something.
    if claimed:
        await db.delete(pending)
    await db.commit()
    return {"claimed": bool(claimed), "zernio_account_ids": claimed}


@router.get("/connect-error")
async def get_connect_error(
    platform: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
):
    """Best-effort detailed reason for a recent failed platform connect.

    Zernio's OAuth redirect back to our callback only ever carries a
    generic error code (e.g. "connection_failed") — the real explanation
    (e.g. "no YouTube channel on this Google account") only exists in
    their activity log. The callback page calls this right after landing
    on an error so it can show something actionable instead of a bare
    code. Returns {"detail": null} rather than erroring when nothing
    recent is found — this is a nice-to-have enrichment, not load-bearing.
    """
    svc = _require_zernio()
    try:
        detail = await svc.get_recent_connection_error(platform)
    except Exception:
        logger.exception("Zernio get_recent_connection_error failed")
        detail = None
    return {"detail": detail}


# \u2500\u2500 Posts \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class PlatformTarget(BaseModel):
    platform: str  # "tiktok" | "instagram" | "youtube" | "linkedin" | "facebook"
    accountId: Optional[str] = None
    scheduled_for: Optional[str] = None  # ISO 8601; null = post-now for this platform


class CreateSocialPostRequest(BaseModel):
    cast_id: str
    render_id: Optional[str] = None
    caption: str
    hashtags: list[str] = []
    first_comment: Optional[str] = None
    platforms: list[PlatformTarget]
    scheduled_for: Optional[str] = None  # ISO 8601 \u2014 applied to all platforms unless overridden
    publish_now: bool = False


@router.post("/posts")
async def create_social_post(
    req: CreateSocialPostRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Create or schedule a SocialPost for a rendered cast.

    Workflow:
      1. Verify the cast belongs to the user and has a ready render.
      2. Resolve the render's R2 video URL.
      3. Call Zernio.create_post.
      4. Persist a SocialPost row.
    """
    svc = _require_zernio()

    cast = await db.get(Cast, req.cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    # Nothing reaches Zernio without approval. The owner can publish
    # directly — treated as an implicit self-approval, since they're
    # already allowed to approve anything and requiring an explicit
    # submit→approve round-trip on their own solo work before every
    # publish would break today's one-click flow for the (still by far
    # most common) no-team case. A Publisher acting on someone else's
    # workspace, though, must respect an actual prior approval — the
    # review step only means something once more than one person is
    # involved.
    if cast.approval_status != CastApprovalStatus.APPROVED:
        if ctx.is_owner:
            cast.approval_status = CastApprovalStatus.APPROVED
            cast.approved_at = datetime.utcnow()
            cast.approved_by = ctx.actor_user_id
            await db.commit()
        else:
            raise HTTPException(
                403,
                "This cast needs Publisher approval before it can be scheduled or published.",
            )

    # Pick the render to publish: explicit render_id or the latest ready render.
    render: Optional[CastRender] = None
    if req.render_id:
        render = await db.get(CastRender, req.render_id)
        if not render or render.cast_id != req.cast_id:
            raise HTTPException(404, "Render not found for this cast")
    else:
        rows = (
            await db.execute(
                select(CastRender)
                .where(CastRender.cast_id == req.cast_id)
                .order_by(CastRender.created_at.desc())
            )
        ).scalars().all()
        render = next((r for r in rows if r.status == CastRenderStatus.READY), None)
        if render is None:
            raise HTTPException(400, "Cast has no ready render to publish.")

    # CastRender has none of final_video_url/output_url/video_url — those
    # names never existed on the model (confirmed against models/cast_render.py:
    # the only relevant column is output_video_r2_key, a storage KEY, not a
    # full URL). Every publish attempt was hitting this getattr(..., None)
    # fallback chain and unconditionally raising "no video URL" regardless
    # of whether the render actually had one.
    from services.r2_storage import get_r2_storage_service
    r2_key = getattr(render, "output_video_r2_key", None)
    media_url = get_r2_storage_service().get_public_url(r2_key) if r2_key else None
    if not media_url:
        raise HTTPException(400, "Render is ready but has no video URL.")

    # Combine caption + hashtags.
    hashtag_str = " ".join(f"#{h.lstrip('#')}" for h in (req.hashtags or []))
    full_caption = req.caption.strip()
    if hashtag_str:
        full_caption = f"{full_caption}\n\n{hashtag_str}".strip()

    # Resolve scheduled_for. publish_now wins; otherwise use scheduled_for.
    scheduled_iso = None
    if not req.publish_now and req.scheduled_for:
        scheduled_iso = req.scheduled_for
        # Nothing downstream (Zernio's API included) rejects a past
        # timestamp — it just treats it as immediately due and publishes
        # right away, while our own status still gets set to "scheduled"
        # and the frontend shows a "will post at <past time>" confirmation.
        try:
            scheduled_dt = datetime.fromisoformat(scheduled_iso.replace("Z", "+00:00"))
            if scheduled_dt.tzinfo is None:
                scheduled_dt = scheduled_dt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            raise HTTPException(400, "Invalid scheduled_for timestamp.")
        if scheduled_dt <= datetime.now(timezone.utc):
            raise HTTPException(
                400,
                "You cannot schedule a post for a past date and time. Please select a future date and time.",
            )

        # Nothing previously checked for an already-scheduled post on this
        # same cast at this same time — clicking Schedule twice (e.g. a
        # double-click, or re-submitting after the page didn't visibly
        # update) silently created a second SocialPost row and a second
        # Zernio post, so the cast actually got published twice even
        # though only one row showed in the Scheduled list.
        # (platform, accountId) pairs, not just platform — a user can now
        # connect more than one account per platform, so scheduling the
        # same cast to two different TikTok accounts at the same time is
        # legitimate and shouldn't collide with each other here.
        requested_targets = {(p.platform, p.accountId) for p in req.platforms}
        # Match the exact normalization used below when scheduled_for is
        # actually persisted (UTC, then tzinfo stripped) — SocialPost.
        # scheduled_for is a naive column, so comparing against anything
        # else would silently miss rows for non-UTC input.
        scheduled_dt_naive = scheduled_dt.astimezone(timezone.utc).replace(tzinfo=None)
        existing_rows = (
            await db.execute(
                select(SocialPost).where(
                    SocialPost.cast_id == req.cast_id,
                    SocialPost.scheduled_for == scheduled_dt_naive,
                    SocialPost.status.in_(["draft", "scheduled", "publishing", "published"]),
                )
            )
        ).scalars().all()
        for existing in existing_rows:
            existing_targets = {
                (p.get("platform"), p.get("accountId")) for p in (existing.platforms or [])
            }
            if requested_targets & existing_targets:
                raise HTTPException(
                    400,
                    "This cast is already scheduled for this date and time. "
                    "Pick a different time, or edit the existing scheduled post instead.",
                )

    # A platform entry with no accountId (e.g. the client preselected a
    # platform the user never actually connected an account for) reaches
    # Zernio as a null and 400s the ENTIRE request with a cryptic
    # "platforms.N.accountId: expected string, received null" — fail fast
    # here with a message that actually names the platform.
    missing = [p.platform for p in req.platforms if not p.accountId]
    if missing:
        raise HTTPException(
            400,
            f"No connected account for: {', '.join(missing)}. "
            "Connect it first or remove it from this post.",
        )

    # Zernio is a single, platform-wide API key — it has no concept of
    # "which Luminacast customer" an accountId belongs to, so without this
    # check any authenticated user could submit ANY accountId (e.g. one
    # they saw via the unscoped /profiles list) and publish through another
    # company's connected social account. Confirm every accountId in this
    # request is actually one of THIS workspace's own active channels.
    owned_zernio_ids = {
        ch.zernio_account_id
        for ch in await _fetch_channel_rows(db, ctx.workspace_owner_id)
        if ch.zernio_account_id and ch.status == "active"
    }
    unauthorized = [
        p.platform for p in req.platforms if p.accountId not in owned_zernio_ids
    ]
    if unauthorized:
        raise HTTPException(
            403,
            f"No connected account you own for: {', '.join(unauthorized)}. "
            "Connect it from My Channels first.",
        )

    platform_payload = [
        {"platform": p.platform, "accountId": p.accountId} for p in req.platforms
    ]

    # Call Zernio.
    try:
        result = await svc.create_post(
            content=full_caption,
            platforms=platform_payload,
            media_urls=[media_url],
            scheduled_for=scheduled_iso,
        )
    except Exception as exc:
        logger.exception("Zernio.create_post failed")
        # httpx.HTTPStatusError's str() is just "Client error '400 Bad
        # Request' for url '...'" — Zernio's actual reason lives in the
        # response body, which create_post now logs but doesn't raise with.
        # Pull it out here so both the persisted row and the error shown to
        # the user say WHY, not just that it failed.
        detail = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = response.json()
                detail = (
                    (body.get("error") or body.get("message"))
                    if isinstance(body, dict) else None
                ) or detail
            except Exception:
                pass
        # Persist a failed-status row so the user can retry.
        post = SocialPost(
            id=f"spo_{uuid.uuid4().hex[:12]}",
            user_id=ctx.workspace_owner_id,
            cast_id=req.cast_id,
            render_id=getattr(render, "id", None),
            caption=full_caption,
            hashtags=req.hashtags,
            media_url=media_url,
            platforms=platform_payload,
            status="failed",
            error_message=str(detail)[:500],
        )
        db.add(post)
        await db.commit()
        _raise_zernio_error(detail, "create_post")

    # Zernio's create-post response nests everything under "post" (see
    # PostCreateResponse in their OpenAPI spec) — reading "id" / "platformPostIds"
    # directly off the top-level result reads fields that don't exist there
    # at all; they were always None (confirmed live: a real successful 201
    # logged "Zernio post created: None"). That also
    # meant a genuinely-published immediate post was hardcoded to our own
    # "publishing" status instead of the "published" Zernio actually
    # returned — the Published tab filters on status === "published", so
    # every real publish was invisible there, stuck showing as if still in
    # progress forever (nothing ever bulk-refreshes status from Zernio;
    # only the single-post detail endpoint does).
    zpost = result.get("post") or {}
    zernio_status = zpost.get("status")
    platform_post_ids = {
        pl.get("platform"): pl.get("platformPostId")
        for pl in (zpost.get("platforms") or [])
        if pl.get("platform") and pl.get("platformPostId")
    }
    # Same nested-shape gap as _apply_zernio_post_refresh: Zernio doesn't
    # always put publishedAt at the top level — a real TikTok response had
    # it only under platforms[i].publishedAt. Falling back to the naive
    # datetime.now() below for immediate posts happened to look right by
    # coincidence (server time is close to actual publish time), not
    # because this was reading the real value — check the nested location
    # too so we store what Zernio actually reported.
    zernio_published_at = zpost.get("publishedAt")
    if not zernio_published_at:
        platform_dates = [
            pl.get("publishedAt")
            for pl in (zpost.get("platforms") or [])
            if pl.get("publishedAt")
        ]
        if platform_dates:
            zernio_published_at = min(platform_dates)

    post = SocialPost(
        id=f"spo_{uuid.uuid4().hex[:12]}",
        user_id=ctx.workspace_owner_id,
        cast_id=req.cast_id,
        render_id=getattr(render, "id", None),
        zernio_post_id=str(zpost.get("_id") or "") or None,
        caption=full_caption,
        hashtags=req.hashtags,
        media_url=media_url,
        platforms=platform_payload,
        scheduled_for=(
            # SocialPost.scheduled_for/published_at are naive DateTime
            # columns (TIMESTAMP WITHOUT TIME ZONE) — asyncpg rejects a
            # tz-aware value outright (DataError: can't subtract
            # offset-naive and offset-aware datetimes), which was turning
            # every successful Zernio publish into a 500 on our own insert
            # right after the post had already gone out. Normalize to UTC
            # then strip tzinfo, matching the convention used elsewhere in
            # this codebase (e.g. routers/admin.py, services/runpod.py).
            datetime.fromisoformat(scheduled_iso.replace("Z", "+00:00"))
            .astimezone(timezone.utc).replace(tzinfo=None)
            if scheduled_iso else None
        ),
        status=zernio_status or ("scheduled" if scheduled_iso else "publishing"),
        platform_post_ids=platform_post_ids,
        published_at=(
            datetime.fromisoformat(zernio_published_at.replace("Z", "+00:00"))
            .astimezone(timezone.utc).replace(tzinfo=None)
            if zernio_published_at
            else (None if scheduled_iso else datetime.now(timezone.utc).replace(tzinfo=None))
        ),
    )
    db.add(post)
    await db.commit()

    try:
        from services.cost_rates import COST_RATES
        from services.usage_tracker import log_usage
        await log_usage(
            db,
            user_id=ctx.workspace_owner_id,
            event_type="social_publish",
            provider="zernio",
            provider_cost_usd=float(COST_RATES.get("zernio/per_post", 0.016))
            * max(len(platform_payload), 1),
            quantity=len(platform_payload),
            quantity_unit="posts",
            resource_type="social_post",
            resource_id=post.id,
            provider_job_id=str(zpost.get("_id") or "") or None,
        )
        await db.commit()
    except Exception as _exc:
        import sentry_sdk as _sentry
        _sentry.capture_exception(_exc)

    return _post_to_dict(post)


@router.get("/comments/pending-count")
async def get_pending_comment_count(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Count of comments awaiting a reply, across all of the user's posts.

    Powers the dot on the Publish hub's Comments tab. Previously that dot
    was approximated as "the user has at least one post at all" — which is
    true for essentially every active user regardless of whether they have
    any unanswered comments, so it just stayed on permanently.
    """
    from sqlalchemy import func as _func
    count = (
        await db.execute(
            select(_func.count(SocialComment.id))
            .join(SocialPost, SocialComment.social_post_id == SocialPost.id)
            .where(
                SocialPost.user_id == ctx.workspace_owner_id,
                SocialComment.reply_status == "pending",
            )
        )
    ).scalar_one()
    return {"count": int(count or 0)}


async def _apply_zernio_post_refresh(svc, p: SocialPost, raw: dict) -> None:
    """Pull fresh status/ids/published-time from Zernio into a SocialPost.

    Mutates ``p`` in place; caller is responsible for committing. ``raw``
    is the direct return value of ``svc.get_post()`` — GET /v1/posts/{id}
    nests everything under "post" (same PostCreateResponse shape used
    everywhere else in Zernio's API), so it's unwrapped here rather than
    trusting the caller to do it. This helper previously read
    raw.get("status") / raw.get("platformPostIds") directly on the
    still-nested response, which are fields that don't exist at that
    level — every refresh silently did nothing, which is exactly why a
    post could sit at "publishing" in our DB forever even though this
    endpoint existed specifically to catch that up.
    """
    z = raw.get("post") or {}
    z_status = z.get("status")
    if z_status:
        p.status = z_status
    platform_post_ids = {
        pl.get("platform"): pl.get("platformPostId")
        for pl in (z.get("platforms") or [])
        if pl.get("platform") and pl.get("platformPostId")
    }
    if platform_post_ids:
        p.platform_post_ids = platform_post_ids
    # Zernio's top-level post.publishedAt is only sometimes populated —
    # a real TikTok scheduled-post response had no top-level publishedAt
    # at all; it only existed nested under platforms[i].publishedAt (per
    # platform, set once that platform's own publish finished). Fall back
    # to the earliest per-platform publishedAt when the top-level one is
    # missing, or this silently never fires for posts shaped this way.
    z_published_at = z.get("publishedAt")
    if not z_published_at:
        platform_dates = [
            pl.get("publishedAt")
            for pl in (z.get("platforms") or [])
            if pl.get("publishedAt")
        ]
        if platform_dates:
            z_published_at = min(platform_dates)
    if z_published_at and not p.published_at:
        try:
            # published_at is a naive DateTime column — see the
            # create_social_post fix for why this must be stripped of
            # tzinfo before assignment.
            p.published_at = (
                datetime.fromisoformat(z_published_at.replace("Z", "+00:00"))
                .astimezone(timezone.utc).replace(tzinfo=None)
            )
        except Exception:
            pass
    if p.zernio_post_id:
        try:
            a = await svc.get_post_analytics(p.zernio_post_id)
            if a:
                p.analytics = a
        except Exception:
            pass


@router.get("/posts")
async def list_social_posts(
    cast_id: Optional[str] = None,
    status: Optional[str] = None,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    q = select(SocialPost).where(SocialPost.user_id == ctx.workspace_owner_id)
    if cast_id:
        q = q.where(SocialPost.cast_id == cast_id)
    if status:
        q = q.where(SocialPost.status == status)
    rows = (await db.execute(q.order_by(SocialPost.created_at.desc()))).scalars().all()

    # Opportunistically catch up any post still sitting in a non-terminal
    # state (publishing is genuinely async on Zernio's side — a freshly
    # created post can take several seconds to flip to "published", and
    # until now NOTHING ever re-checked it: only the single-post detail
    # endpoint refreshed from Zernio, which the Published tab's bulk list
    # view never calls). That's why a post could publish successfully and
    # still sit invisible under a "published" filter indefinitely.
    stale = [
        p for p in rows
        if p.zernio_post_id and (
            p.status in ("publishing", "scheduled")
            # Zernio can flip status to "published" before publishedAt is
            # backfilled (e.g. TikTok URLs arrive later via webhook) — keep
            # retrying these too, or they get stuck showing "—" forever
            # since they no longer match the check above.
            or (p.status == "published" and not p.published_at)
        )
    ]
    if stale:
        from services.zernio import get_zernio_service
        svc = get_zernio_service()
        if svc is not None:
            import asyncio

            async def _refresh_one(p: SocialPost) -> None:
                try:
                    raw = await svc.get_post(p.zernio_post_id)
                    await _apply_zernio_post_refresh(svc, p, raw)
                except Exception as exc:
                    logger.warning("Zernio refresh failed for %s: %s", p.id, exc)

            await asyncio.gather(*(_refresh_one(p) for p in stale))
            await db.commit()

    return [_post_to_dict(p) for p in rows]


@router.get("/posts/{post_id}")
async def get_social_post(
    post_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Post not found")

    # Refresh status from Zernio when we have a zernio_post_id.
    if p.zernio_post_id:
        from services.zernio import get_zernio_service
        svc = get_zernio_service()
        if svc is not None:
            try:
                raw = await svc.get_post(p.zernio_post_id)
                await _apply_zernio_post_refresh(svc, p, raw)
                await db.commit()
            except Exception as exc:
                logger.warning("Zernio refresh failed for %s: %s", p.id, exc)

    return _post_to_dict(p)


@router.delete("/posts/{post_id}")
async def delete_social_post(
    post_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Post not found")

    # Best-effort delete on Zernio.
    if p.zernio_post_id:
        from services.zernio import get_zernio_service
        svc = get_zernio_service()
        if svc is not None:
            try:
                await svc.delete_post(p.zernio_post_id)
            except Exception as exc:
                logger.warning("Zernio delete failed for %s: %s", p.id, exc)

    p.status = "deleted"
    await db.commit()
    return {"ok": True}


# \u2500\u2500 Comments \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


async def _refresh_comments_for_post(db: AsyncSession, post: SocialPost) -> int:
    """Fetch comments from Zernio for a published post and persist new ones
    (with AI-suggested replies). Returns the count of newly-stored comments.
    """
    if not post.zernio_post_id:
        return 0
    from services.zernio import get_zernio_service
    svc = get_zernio_service()
    if svc is None:
        return 0

    # Comments are scoped per connected account, not per post — a post
    # crossposted to N platforms has N separate comment threads. platforms
    # is exactly [{platform, accountId}, ...] already, one entry per
    # platform this post went to.
    platform_accounts = [
        (pl.get("platform"), pl.get("accountId"))
        for pl in (post.platforms or [])
        if pl.get("platform") and pl.get("accountId")
    ]
    if not platform_accounts:
        return 0

    remote: list[dict] = []
    for platform, account_id in platform_accounts:
        try:
            items = await svc.get_comments(post.zernio_post_id, account_id)
        except Exception as exc:
            import sentry_sdk as _sentry
            _sentry.capture_exception(exc)
            logger.warning(
                "Zernio.get_comments failed for post=%s platform=%s: %s",
                post.id, platform, exc,
            )
            continue
        for item in items:
            item.setdefault("platform", platform)
        remote.extend(items)

    # Index existing platform_comment_ids to avoid duplicates.
    existing = (
        await db.execute(
            select(SocialComment).where(SocialComment.social_post_id == post.id)
        )
    ).scalars().all()
    seen = {c.platform_comment_id for c in existing if c.platform_comment_id}

    # Pull cast + product for AI reply context.
    cast = await db.get(Cast, post.cast_id) if post.cast_id else None
    avatar = await db.get(Avatar, getattr(cast, "avatar_id", None)) if cast else None
    # `cast.products` is a lazy-loaded relationship (models/cast.py) — a
    # plain attribute access on an AsyncSession-bound object raises
    # sqlalchemy.exc.MissingGreenlet instead of AttributeError, so
    # getattr(cast, "products", None) doesn't actually avoid the lazy
    # load or catch its failure; it crashed this endpoint with a 500 on
    # every call. Go straight to the explicit eager-loaded query below.
    product = None
    if post.cast_id:
        from models.cast import CastProduct
        cp_rows = (
            await db.execute(
                select(CastProduct)
                .where(CastProduct.cast_id == post.cast_id)
                .options(selectinload(CastProduct.product))
            )
        ).scalars().all()
        product = next(
            (cp.product for cp in cp_rows if cp.product), None
        )

    from services.social_ai import generate_comment_reply

    new_count = 0
    for raw in remote:
        # Real /v1/inbox/comments/{postId} shape (per the OpenAPI spec):
        # {id, message, createdTime, from: {name, username, isOwner}, platform, ...}.
        # NOT {text, author: {name, handle}, createdAt} — that was guessed
        # against a path that doesn't exist and never returned real data to
        # validate the shape against.
        platform_comment_id = str(raw.get("id") or "")
        if not platform_comment_id or platform_comment_id in seen:
            continue
        text = raw.get("message") or ""
        platform = raw.get("platform") or "tiktok"
        author = raw.get("from") or {}
        created_iso = raw.get("createdTime")
        # SocialComment.created_at is a naive DateTime column — same
        # tz-aware-into-naive-column mismatch fixed above for SocialPost.
        try:
            created_at = (
                datetime.fromisoformat(created_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
                if created_iso else datetime.now(timezone.utc)
            ).replace(tzinfo=None)
        except Exception:
            created_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # AI suggestion.
        ai = {"suggested_reply": None, "is_prompt_injection": False, "action": "suggest"}
        if avatar is not None and product is not None:
            try:
                ai = await generate_comment_reply(text, avatar, product, platform)
            except Exception as exc:
                import sentry_sdk as _sentry
                _sentry.capture_exception(exc)
                logger.warning("AI reply failed: %s", exc)

        comment = SocialComment(
            id=f"scm_{uuid.uuid4().hex[:12]}",
            social_post_id=post.id,
            platform=platform,
            platform_comment_id=platform_comment_id,
            author_name=author.get("name"),
            author_handle=author.get("username"),
            text=text,
            ai_suggested_reply=ai.get("suggested_reply"),
            reply_status="flagged" if ai.get("is_prompt_injection") else "pending",
            is_prompt_injection=bool(ai.get("is_prompt_injection")),
            created_at=created_at,
        )
        db.add(comment)
        new_count += 1

    if new_count:
        await db.commit()
    return new_count


@router.get("/posts/{post_id}/comments")
async def get_comments(
    post_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return all comments for a post, refreshing from Zernio first."""
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Post not found")
    await _refresh_comments_for_post(db, p)
    rows = (
        await db.execute(
            select(SocialComment)
            .where(SocialComment.social_post_id == post_id)
            .order_by(SocialComment.created_at.desc())
        )
    ).scalars().all()
    return [_comment_to_dict(c) for c in rows]


class ReplyRequest(BaseModel):
    text: Optional[str] = None  # If null, send the AI suggestion as-is.


@router.post("/posts/{post_id}/comments/{comment_id}/reply")
async def reply_to_comment(
    post_id: str,
    comment_id: str,
    req: ReplyRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Approve and send a reply to a comment via Zernio."""
    svc = _require_zernio()
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Post not found")
    c = await db.get(SocialComment, comment_id)
    if not c or c.social_post_id != post_id:
        raise HTTPException(404, "Comment not found")
    if c.is_prompt_injection:
        raise HTTPException(400, "Comment was flagged as a prompt injection.")
    if c.reply_status == "sent":
        raise HTTPException(400, "Already replied.")

    text = (req.text or c.ai_suggested_reply or "").strip()
    if not text:
        raise HTTPException(400, "No reply text provided and no AI suggestion available.")

    if not p.zernio_post_id or not c.platform_comment_id:
        raise HTTPException(400, "Comment is missing platform identifiers; cannot reply.")

    # Replying requires the accountId for the SPECIFIC platform this
    # comment came from — a crossposted post has one comment thread per
    # platform, each under a different connected account.
    account_id = next(
        (pl.get("accountId") for pl in (p.platforms or []) if pl.get("platform") == c.platform),
        None,
    )
    if not account_id:
        raise HTTPException(400, f"No connected {c.platform} account found for this post.")

    try:
        await svc.reply_to_comment(p.zernio_post_id, account_id, text, comment_id=c.platform_comment_id)
    except Exception as exc:
        logger.exception("Zernio reply failed")
        _raise_zernio_error(exc, "reply_to_comment")

    c.actual_reply = text
    c.reply_status = "sent"
    # replied_at is a naive DateTime column — same fix as SocialPost above.
    c.replied_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return _comment_to_dict(c)


@router.post("/posts/{post_id}/comments/{comment_id}/skip")
async def skip_comment(
    post_id: str,
    comment_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Post not found")
    c = await db.get(SocialComment, comment_id)
    if not c or c.social_post_id != post_id:
        raise HTTPException(404, "Comment not found")
    c.reply_status = "skipped"
    await db.commit()
    return _comment_to_dict(c)


# \u2500\u2500 Caption generator \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


class GenerateCaptionRequest(BaseModel):
    cast_id: str
    platform: str = "tiktok"


@router.post("/generate-caption")
async def generate_caption_endpoint(
    req: GenerateCaptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI caption + hashtags + first_comment for a cast."""
    cast = await db.get(Cast, req.cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    # Resolve a representative product (first attached, if any).
    from models.cast import CastProduct
    cp_rows = (
        await db.execute(
            select(CastProduct)
            .where(CastProduct.cast_id == cast.id)
            .options(selectinload(CastProduct.product))
        )
    ).scalars().all()
    product = next((cp.product for cp in cp_rows if cp.product), None)
    # If there's no product, we still generate a caption from the script alone.
    if product is None:
        class _StubProduct:
            name = cast.name or "this video"
            description = cast.description or ""
            price = None
            rating = None
            key_benefits: list[str] = []
        product = _StubProduct()

    # Pull blocks + active variants for script context.
    from models.block import Block
    from models.variant import Variant
    blocks_rows = (
        await db.execute(
            select(Block)
            .where(Block.cast_id == cast.id, Block.deleted_at.is_(None))
            .options(selectinload(Block.variants))
            .order_by(Block.position)
        )
    ).scalars().all()
    from services.social_ai import generate_caption
    return await generate_caption(blocks_rows, product, req.platform)

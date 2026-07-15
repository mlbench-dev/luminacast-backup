"""Zernio social-media endpoints.

These power the Publish, Published, and Comments pages on the frontend.
All write actions require an authenticated user and a configured
ZERNIO_API_KEY; if the key is missing we return 503 instead of 500 so the
frontend can show an "Connect your social-media integration" prompt.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.cast import Cast
from models.cast_render import CastRender, CastRenderStatus
from models.product import Product
from models.avatar import Avatar
from models.social_post import SocialPost, SocialComment, SocialChannel
from models.user import User
from routers.auth import get_current_user

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
        "scheduled_for": p.scheduled_for.isoformat() if p.scheduled_for else None,
        "status": p.status,
        "platform_post_ids": p.platform_post_ids or {},
        "analytics": p.analytics or {},
        "error_message": p.error_message,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
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


@router.get("/channels")
async def list_channels(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the user's connected social-media channels (with avatar info)."""
    rows = (
        await db.execute(
            select(SocialChannel)
            .where(SocialChannel.user_id == user.id)
            .options(selectinload(SocialChannel.primary_avatar))
            .order_by(SocialChannel.connected_at.desc())
        )
    ).scalars().all()
    return {"channels": [_channel_to_dict(c) for c in rows]}


@router.delete("/channels/{channel_id}")
async def disconnect_channel(
    channel_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await db.get(SocialChannel, channel_id)
    if not ch or ch.user_id != user.id:
        raise HTTPException(404, "Channel not found")
    await db.delete(ch)
    await db.commit()
    return {"ok": True}


@router.get("/channels/{channel_id}/avatar-check")
async def check_avatar_consistency(
    channel_id: str,
    avatar_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Tell the frontend whether a channel has a different primary avatar.

    Returns one of:
      "match"        — channel.primary_avatar == avatar_id
      "new_channel"  — channel has no primary avatar yet
      "mismatch"     — channel.primary_avatar != avatar_id
    """
    ch = await db.get(SocialChannel, channel_id)
    if not ch or ch.user_id != user.id:
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
async def list_social_profiles(user: User = Depends(get_current_user)):
    """List the user's connected social-media profiles via Zernio."""
    svc = _require_zernio()
    try:
        return await svc.list_profiles()
    except Exception as exc:
        logger.exception("Zernio list_profiles failed")
        raise HTTPException(502, f"Zernio error: {exc}")


class ConnectPlatformRequest(BaseModel):
    platform: str  # tiktok | instagram | youtube | linkedin | facebook | twitter
    redirect_uri: Optional[str] = None


@router.post("/connect")
async def connect_platform(
    req: ConnectPlatformRequest,
    user: User = Depends(get_current_user),
):
    """Return an OAuth URL the frontend opens in a popup window.

    Zernio handles the OAuth dance and redirects the popup back to
    `redirect_uri` once the social account is linked. The frontend listens
    for a postMessage from the popup to know when to refresh the profile
    list.
    """
    svc = _require_zernio()
    redirect_uri = (
        req.redirect_uri
        or "https://www.luminacast.com/integrations/zernio/callback"
    )
    try:
        result = await svc.get_oauth_url(req.platform, redirect_uri)
    except Exception as exc:
        logger.exception("Zernio.get_oauth_url failed")
        raise HTTPException(502, f"Zernio error: {exc}")
    auth_url = result.get("authUrl") or result.get("url") or result.get("authorize_url")
    if not auth_url:
        raise HTTPException(502, "Zernio did not return an authUrl.")
    return {"auth_url": auth_url, "platform": req.platform}


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
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

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

    media_url = (
        getattr(render, "final_video_url", None)
        or getattr(render, "output_url", None)
        or getattr(render, "video_url", None)
    )
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
        # Persist a failed-status row so the user can retry.
        post = SocialPost(
            id=f"spo_{uuid.uuid4().hex[:12]}",
            user_id=user.id,
            cast_id=req.cast_id,
            render_id=getattr(render, "id", None),
            caption=full_caption,
            hashtags=req.hashtags,
            media_url=media_url,
            platforms=platform_payload,
            status="failed",
            error_message=str(exc)[:500],
        )
        db.add(post)
        await db.commit()
        raise HTTPException(502, f"Zernio error: {exc}")

    post = SocialPost(
        id=f"spo_{uuid.uuid4().hex[:12]}",
        user_id=user.id,
        cast_id=req.cast_id,
        render_id=getattr(render, "id", None),
        zernio_post_id=str(result.get("id") or "") or None,
        caption=full_caption,
        hashtags=req.hashtags,
        media_url=media_url,
        platforms=platform_payload,
        scheduled_for=(
            datetime.fromisoformat(scheduled_iso.replace("Z", "+00:00"))
            if scheduled_iso else None
        ),
        status="scheduled" if scheduled_iso else "publishing",
        platform_post_ids=result.get("platformPostIds") or {},
        published_at=None if scheduled_iso else datetime.now(timezone.utc),
    )
    db.add(post)
    await db.commit()

    try:
        from services.cost_rates import COST_RATES
        from services.usage_tracker import log_usage
        await log_usage(
            db,
            user_id=user.id,
            event_type="social_publish",
            provider="zernio",
            provider_cost_usd=float(COST_RATES.get("zernio/per_post", 0.016))
            * max(len(platform_payload), 1),
            quantity=len(platform_payload),
            quantity_unit="posts",
            resource_type="social_post",
            resource_id=post.id,
            provider_job_id=str(result.get("id") or "") or None,
        )
        await db.commit()
    except Exception as _exc:
        import sentry_sdk as _sentry
        _sentry.capture_exception(_exc)

    return _post_to_dict(post)


@router.get("/posts")
async def list_social_posts(
    cast_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(SocialPost).where(SocialPost.user_id == user.id)
    if cast_id:
        q = q.where(SocialPost.cast_id == cast_id)
    rows = (await db.execute(q.order_by(SocialPost.created_at.desc()))).scalars().all()
    return [_post_to_dict(p) for p in rows]


@router.get("/posts/{post_id}")
async def get_social_post(
    post_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "Post not found")

    # Refresh status from Zernio when we have a zernio_post_id.
    if p.zernio_post_id:
        from services.zernio import get_zernio_service
        svc = get_zernio_service()
        if svc is not None:
            try:
                z = await svc.get_post(p.zernio_post_id)
                p.platform_post_ids = z.get("platformPostIds") or p.platform_post_ids
                z_status = z.get("status")
                if z_status:
                    p.status = z_status
                if z.get("publishedAt") and not p.published_at:
                    try:
                        p.published_at = datetime.fromisoformat(
                            z["publishedAt"].replace("Z", "+00:00")
                        )
                    except Exception:
                        pass
                # Pull analytics if present.
                try:
                    a = await svc.get_post_analytics(p.zernio_post_id)
                    if a:
                        p.analytics = a
                except Exception:
                    pass
                await db.commit()
            except Exception as exc:
                logger.warning("Zernio refresh failed for %s: %s", p.id, exc)

    return _post_to_dict(p)


@router.delete("/posts/{post_id}")
async def delete_social_post(
    post_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != user.id:
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

    try:
        remote = await svc.get_comments(post.zernio_post_id)
    except Exception as exc:
        import sentry_sdk as _sentry
        _sentry.capture_exception(exc)
        logger.warning("Zernio.get_comments failed: %s", exc)
        return 0

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
    product = None
    cast_products = getattr(cast, "products", None)
    if cast_products and isinstance(cast_products, list) and cast_products:
        # Avoid lazy-loading; fall back to first associated product if available.
        product = cast_products[0]
    if product is None and post.cast_id:
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
        # Zernio shape: {id, platform, author: {name, handle}, text, createdAt, ...}
        platform_comment_id = str(raw.get("id") or raw.get("commentId") or "")
        if not platform_comment_id or platform_comment_id in seen:
            continue
        text = raw.get("text") or raw.get("content") or ""
        platform = raw.get("platform") or "tiktok"
        author = raw.get("author") or {}
        created_iso = raw.get("createdAt") or raw.get("created_at")
        try:
            created_at = (
                datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
                if created_iso else datetime.now(timezone.utc)
            )
        except Exception:
            created_at = datetime.now(timezone.utc)

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
            author_name=author.get("name") or raw.get("authorName"),
            author_handle=author.get("handle") or raw.get("authorHandle"),
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
    db: AsyncSession = Depends(get_db),
):
    """Return all comments for a post, refreshing from Zernio first."""
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != user.id:
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
    db: AsyncSession = Depends(get_db),
):
    """Approve and send a reply to a comment via Zernio."""
    svc = _require_zernio()
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != user.id:
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

    try:
        await svc.reply_to_comment(p.zernio_post_id, c.platform_comment_id, text)
    except Exception as exc:
        logger.exception("Zernio reply failed")
        raise HTTPException(502, f"Zernio error: {exc}")

    c.actual_reply = text
    c.reply_status = "sent"
    c.replied_at = datetime.now(timezone.utc)
    await db.commit()
    return _comment_to_dict(c)


@router.post("/posts/{post_id}/comments/{comment_id}/skip")
async def skip_comment(
    post_id: str,
    comment_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(SocialPost, post_id)
    if not p or p.user_id != user.id:
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
    db: AsyncSession = Depends(get_db),
):
    """Generate an AI caption + hashtags + first_comment for a cast."""
    cast = await db.get(Cast, req.cast_id)
    if not cast or cast.user_id != user.id:
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
    cast.blocks = blocks_rows  # attach for the AI helper

    from services.social_ai import generate_caption
    return await generate_caption(cast, product, req.platform)

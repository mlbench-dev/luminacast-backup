"""My Channels — CRUD + content indexing + voice profile endpoints."""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from database import get_db
from models.user import User
from models.channel import Channel, ChannelTranscript
from routers.auth import get_current_user
from services import audit_log
import sentry_sdk

router = APIRouter(prefix="/api/channels", tags=["channels"])


# ── Schemas ──

class ChannelCreate(BaseModel):
    platform: str
    handle: str
    display_name: str = ""
    stream_key: str = ""
    stream_url: str = ""


class ChannelUpdate(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    handle: Optional[str] = None
    stream_key: Optional[str] = None
    stream_url: Optional[str] = None


class ChannelResponse(BaseModel):
    id: str
    platform: str
    handle: str
    display_name: str
    bio: str
    profile_image_key: str
    followers_count: int
    video_count: int
    avg_views: int
    stream_key: str
    stream_url: str
    index_status: str
    indexed_video_count: int
    index_target_count: int
    relevant_video_count: int
    last_indexed_at: Optional[str] = None
    voice_profile: Optional[dict] = None
    status: str
    created_at: str

    model_config = {"from_attributes": True}


class TranscriptResponse(BaseModel):
    id: str
    video_url: str
    video_title: str
    video_duration_seconds: float
    video_views: int
    is_relevant: bool
    relevance_reason: str
    speech_ratio: float
    has_main_speaker: bool
    word_count: int
    status: str

    model_config = {"from_attributes": True}


def _channel_to_dict(ch: Channel) -> dict:
    return {
        "id": ch.id,
        "platform": ch.platform,
        "handle": ch.handle,
        "display_name": ch.display_name or "",
        "bio": ch.bio or "",
        "profile_image_key": ch.profile_image_key or "",
        "followers_count": ch.followers_count or 0,
        "video_count": ch.video_count or 0,
        "avg_views": ch.avg_views or 0,
        "stream_key": ch.stream_key or "",
        "stream_url": ch.stream_url or "",
        "index_status": ch.index_status or "pending",
        "indexed_video_count": ch.indexed_video_count or 0,
        "index_target_count": ch.index_target_count or 50,
        "relevant_video_count": ch.relevant_video_count or 0,
        "last_indexed_at": ch.last_indexed_at.isoformat() if ch.last_indexed_at else None,
        "voice_profile": ch.voice_profile,
        "status": ch.status or "active",
        "created_at": ch.created_at.isoformat() if ch.created_at else "",
    }


# ── Endpoints ──

@router.post("", status_code=201)
async def create_channel(
    req: ChannelCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create channel and immediately start background indexing."""
    # Validate platform
    if req.platform not in ("tiktok", "instagram", "youtube"):
        raise HTTPException(400, "Platform must be tiktok, instagram, or youtube")

    handle = req.handle.strip()
    if not handle.startswith("@"):
        handle = f"@{handle}"

    # Check for duplicate
    existing = await db.execute(
        select(Channel).where(
            Channel.user_id == user.id,
            Channel.platform == req.platform,
            Channel.handle == handle,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Channel already connected")

    channel_id = f"ch_{uuid.uuid4().hex[:12]}"
    channel = Channel(
        id=channel_id,
        user_id=user.id,
        platform=req.platform,
        handle=handle,
        display_name=req.display_name or handle,
        stream_key=req.stream_key,
        stream_url=req.stream_url,
        index_status="indexing",
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)

    # Kick off indexing task
    try:
        from tasks.index_channel import index_channel_content
        index_channel_content.delay(channel_id, user.id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        pass  # Task may fail to enqueue in dev — channel still created

    try:
        await audit_log.record(
            db, user_id=user.id, action="channel.connect", entity_type="channel",
            entity_id=channel_id, after={"platform": channel.platform, "handle": channel.handle},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return _channel_to_dict(channel)


@router.get("")
async def list_channels(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Channel).where(Channel.user_id == user.id, Channel.status != "deleted")
        .order_by(Channel.created_at.desc())
    )
    channels = result.scalars().all()
    return {"channels": [_channel_to_dict(ch) for ch in channels], "total": len(channels)}


@router.get("/{channel_id}")
async def get_channel(
    channel_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.user_id != user.id:
        raise HTTPException(404, "Channel not found")
    return _channel_to_dict(channel)


@router.put("/{channel_id}")
async def update_channel(
    channel_id: str,
    req: ChannelUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.user_id != user.id:
        raise HTTPException(404, "Channel not found")

    if req.display_name is not None:
        channel.display_name = req.display_name
    if req.bio is not None:
        channel.bio = req.bio
    if req.handle is not None:
        h = req.handle.strip()
        if not h.startswith("@"):
            h = f"@{h}"
        channel.handle = h
    if req.stream_key is not None:
        channel.stream_key = req.stream_key
    if req.stream_url is not None:
        channel.stream_url = req.stream_url

    await db.commit()
    await db.refresh(channel)
    try:
        await audit_log.record(
            db, user_id=user.id, action="channel.update", entity_type="channel",
            entity_id=channel_id, after={"display_name": channel.display_name, "handle": channel.handle},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _channel_to_dict(channel)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.user_id != user.id:
        raise HTTPException(404, "Channel not found")
    channel.status = "deleted"
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="channel.disconnect", entity_type="channel",
            entity_id=channel_id, before={"handle": channel.handle},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)


@router.post("/{channel_id}/reindex")
async def reindex_channel(
    channel_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.user_id != user.id:
        raise HTTPException(404, "Channel not found")

    # Clear existing transcripts
    await db.execute(
        ChannelTranscript.__table__.delete().where(
            ChannelTranscript.channel_id == channel_id
        )
    )
    channel.index_status = "indexing"
    channel.indexed_video_count = 0
    channel.relevant_video_count = 0
    channel.voice_profile = None
    channel.speaker_embedding_key = ""
    await db.commit()

    try:
        from tasks.index_channel import index_channel_content
        index_channel_content.delay(channel_id, user.id)
    except Exception as e:
        sentry_sdk.capture_exception(e)

    try:
        await audit_log.record(
            db, user_id=user.id, action="channel.reindex", entity_type="channel",
            entity_id=channel_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"status": "indexing", "message": "Re-indexing started"}


@router.get("/{channel_id}/voice-profile")
async def get_voice_profile(
    channel_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.user_id != user.id:
        raise HTTPException(404, "Channel not found")
    return {"channel_id": channel_id, "voice_profile": channel.voice_profile}


@router.get("/{channel_id}/transcripts")
async def get_transcripts(
    channel_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    channel = await db.get(Channel, channel_id)
    if not channel or channel.user_id != user.id:
        raise HTTPException(404, "Channel not found")

    # Count total
    count_q = select(func.count()).where(ChannelTranscript.channel_id == channel_id)
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch page
    offset = (page - 1) * per_page
    result = await db.execute(
        select(ChannelTranscript)
        .where(ChannelTranscript.channel_id == channel_id)
        .order_by(ChannelTranscript.created_at.desc())
        .offset(offset).limit(per_page)
    )
    transcripts = result.scalars().all()

    return {
        "transcripts": [
            {
                "id": t.id,
                "video_url": t.video_url,
                "video_title": t.video_title,
                "video_duration_seconds": t.video_duration_seconds,
                "video_views": t.video_views,
                "is_relevant": t.is_relevant,
                "relevance_reason": t.relevance_reason,
                "speech_ratio": t.speech_ratio,
                "has_main_speaker": t.has_main_speaker,
                "word_count": t.word_count,
                "status": t.status,
            }
            for t in transcripts
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }

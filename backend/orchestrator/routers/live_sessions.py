"""Live Session — CRUD + lifecycle for voice-only live broadcasts."""

import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

import sentry_sdk
from database import get_db
from models.user import User
from models.live_session import LiveSession, LiveSessionStatus
from models.avatar import Avatar, AvatarStatus
from routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live-sessions", tags=["live-sessions"])


# ── Schemas ──

class ProductQueueItem(BaseModel):
    product_id: str
    footage_keys: List[str] = []
    talking_points: str = ""


class CreateLiveSessionRequest(BaseModel):
    avatar_id: str
    title: Optional[str] = None
    product_queue: Optional[List[ProductQueueItem]] = None
    voice_style_notes: Optional[str] = None
    max_duration_minutes: Optional[int] = 60
    output_format: Optional[str] = "9:16"


class UpdateLiveSessionRequest(BaseModel):
    title: Optional[str] = None
    product_queue: Optional[List[ProductQueueItem]] = None
    voice_style_notes: Optional[str] = None
    max_duration_minutes: Optional[int] = None
    output_format: Optional[str] = None
    avatar_id: Optional[str] = None


# ── Helpers ──

def _session_to_dict(ls: LiveSession) -> dict:
    return {
        "id": ls.id,
        "user_id": ls.user_id,
        "avatar_id": ls.avatar_id,
        "title": ls.title,
        "status": ls.status,
        "product_queue": ls.product_queue or [],
        "voice_style_notes": ls.voice_style_notes,
        "max_duration_minutes": ls.max_duration_minutes,
        "output_format": ls.output_format,
        "current_product_index": ls.current_product_index,
        "current_paragraph": ls.current_paragraph,
        "stream_key": ls.stream_key,
        "hls_url": ls.hls_url,
        "started_at": ls.started_at.isoformat() if ls.started_at else None,
        "ended_at": ls.ended_at.isoformat() if ls.ended_at else None,
        "total_paragraphs_generated": ls.total_paragraphs_generated,
        "error_message": ls.error_message,
        "created_at": ls.created_at.isoformat() if ls.created_at else None,
    }


# ── Endpoints ──

@router.post("")
async def create_live_session(
    payload: CreateLiveSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Validate avatar exists and belongs to user
    avatar = await db.get(Avatar, payload.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")
    if avatar.status != AvatarStatus.APPROVED and avatar.id != "default":
        raise HTTPException(400, "Only approved avatars with a voice clone can be used")

    session_id = f"ls_{uuid.uuid4().hex[:12]}"
    stream_key = f"live_{uuid.uuid4().hex[:8]}"

    ls = LiveSession(
        id=session_id,
        user_id=user.id,
        avatar_id=payload.avatar_id,
        title=payload.title,
        product_queue=[item.model_dump() for item in (payload.product_queue or [])],
        voice_style_notes=payload.voice_style_notes,
        max_duration_minutes=payload.max_duration_minutes or 60,
        output_format=payload.output_format or "9:16",
        stream_key=stream_key,
    )
    db.add(ls)
    await db.commit()
    await db.refresh(ls)
    return _session_to_dict(ls)


@router.get("")
async def list_live_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LiveSession)
        .where(LiveSession.user_id == user.id)
        .order_by(LiveSession.created_at.desc())
        .limit(50)
    )
    sessions = result.scalars().all()
    return {"sessions": [_session_to_dict(s) for s in sessions]}


@router.get("/{session_id}")
async def get_live_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    return _session_to_dict(ls)


@router.put("/{session_id}")
async def update_live_session(
    session_id: str,
    payload: UpdateLiveSessionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status not in ("draft", "ended"):
        raise HTTPException(400, "Can only update draft or ended sessions")

    if payload.title is not None:
        ls.title = payload.title
    if payload.product_queue is not None:
        ls.product_queue = [item.model_dump() for item in payload.product_queue]
    if payload.voice_style_notes is not None:
        ls.voice_style_notes = payload.voice_style_notes
    if payload.max_duration_minutes is not None:
        ls.max_duration_minutes = payload.max_duration_minutes
    if payload.output_format is not None:
        ls.output_format = payload.output_format
    if payload.avatar_id is not None:
        avatar = await db.get(Avatar, payload.avatar_id)
        if not avatar or avatar.user_id != user.id:
            raise HTTPException(404, "Avatar not found")
        ls.avatar_id = payload.avatar_id

    await db.commit()
    await db.refresh(ls)
    return _session_to_dict(ls)


@router.delete("/{session_id}")
async def delete_live_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status not in ("draft", "ended", "failed"):
        raise HTTPException(400, "Can only delete draft, ended, or failed sessions")

    await db.delete(ls)
    await db.commit()
    return {"ok": True}


@router.post("/{session_id}/start")
async def start_live_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status not in ("draft", "paused"):
        raise HTTPException(400, "Session must be in draft or paused state to start")
    if not ls.product_queue:
        raise HTTPException(400, "Add at least one product before going live")

    ls.status = "starting"
    ls.started_at = ls.started_at or datetime.utcnow()
    await db.commit()

    from tasks.live_session import run_live_session_task
    run_live_session_task.delay(session_id)

    await db.refresh(ls)
    return _session_to_dict(ls)


@router.post("/{session_id}/pause")
async def pause_live_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status != "live":
        raise HTTPException(400, "Session must be live to pause")

    ls.status = "paused"
    await db.commit()
    await db.refresh(ls)
    return _session_to_dict(ls)


@router.post("/{session_id}/resume")
async def resume_live_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status != "paused":
        raise HTTPException(400, "Session must be paused to resume")

    ls.status = "live"
    await db.commit()

    # Re-launch the generation loop
    from tasks.live_session import run_live_session_task
    run_live_session_task.delay(session_id)

    await db.refresh(ls)
    return _session_to_dict(ls)


@router.post("/{session_id}/stop")
async def stop_live_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status not in ("live", "paused", "starting"):
        raise HTTPException(400, "Session is not active")

    ls.status = "ended"
    ls.ended_at = datetime.utcnow()
    await db.commit()
    await db.refresh(ls)
    return _session_to_dict(ls)


@router.get("/{session_id}/stream-url")
async def get_stream_url(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status not in ("live", "starting"):
        raise HTTPException(400, "Session is not live")
    hls_url = f"https://www.luminacast.com/hls/{ls.stream_key}/stream.m3u8"
    return {"hls_url": hls_url, "stream_key": ls.stream_key}


# ============================================================================
# Go Live (multi-cast / multi-platform / monitor) endpoints.
#
# These extend the legacy voice-only endpoints with the full Go-Live page
# feature set: cast selection, platform RTMP keys, traction config, invite
# team, manual override, OBS browser-source URL.
#
# See docs/Perplexity_GoLive_Architecture.md for the architecture diagram.
# ============================================================================

import secrets
from datetime import datetime, timezone
from config import settings as _settings
from models.live_session import LiveSessionInvite, LiveSessionEvent


# Public host for the relay. Defaults to the VPS IP for the Docker SRS that
# we ship in the compose. Override via env when DNS is set up.
RELAY_HOST = getattr(_settings, "LIVE_RELAY_HOST", None) or "145.223.121.28"
RELAY_RTMP_PORT = getattr(_settings, "LIVE_RELAY_RTMP_PORT", None) or 1935
RELAY_HTTP_PORT = getattr(_settings, "LIVE_RELAY_HTTP_PORT", None) or 8085


class GoLiveCastSelection(BaseModel):
    cast_id: str
    render_id: Optional[str] = None
    rotation_order: Optional[int] = None


class GoLivePlatformConfig(BaseModel):
    platform: str  # tiktok | instagram | youtube | facebook | twitch | custom
    stream_key: str
    rtmp_url: Optional[str] = None
    enabled: bool = True


class GoLiveCreateRequest(BaseModel):
    avatar_id: str
    title: Optional[str] = None
    cast_selections: List[GoLiveCastSelection]
    platforms: List[GoLivePlatformConfig]
    duration_minutes: Optional[int] = None  # null = until stopped
    background_music_id: Optional[str] = None
    chat_reactivity: str = "medium"  # high | medium | low
    product_rotation_minutes: int = 10


class InviteRequest(BaseModel):
    email: str
    role: str = "monitor"  # admin | monitor | moderator


class OverrideRequest(BaseModel):
    action: str  # skip_product | inject_message | pause_reactions | resume_reactions | end_stream
    text: Optional[str] = None


def _golive_session_to_dict(ls: LiveSession) -> dict:
    """Serialize a Go-Live session including the new fields."""
    base = _session_to_dict(ls)
    base.update({
        "config": ls.config or {},
        "relay_stream_key": ls.relay_stream_key,
        "total_viewers": ls.total_viewers or 0,
        "peak_viewers": ls.peak_viewers or 0,
        "total_purchases": ls.total_purchases or 0,
        "total_revenue_cents": ls.total_revenue_cents or 0,
        "total_comments": ls.total_comments or 0,
        "last_bitrate_kbps": ls.last_bitrate_kbps or 0,
        "last_dropped_frames": ls.last_dropped_frames or 0,
    })
    return base


async def _record_event(db: AsyncSession, session_id: str, event_type: str, data: dict | None = None):
    """Append an event to live_session_events for the monitor dashboard."""
    db.add(LiveSessionEvent(
        id=f"lse_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        event_type=event_type,
        data=data or {},
    ))
    await db.commit()


@router.post("/golive")
async def create_golive_session(
    req: GoLiveCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Go-Live session.

    Generates a relay stream key and a session token. The compositor will
    use the relay key to publish to rtmp://relay/.../live/<key>. The user's
    streamer app pulls from the same URL and forwards to their platforms.

    The response includes the OBS browser-source URL so the user can copy
    it straight into OBS without reading docs.
    """
    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    if not req.cast_selections:
        raise HTTPException(400, "At least one cast must be selected.")
    if not req.platforms:
        raise HTTPException(400, "At least one platform must be configured.")

    relay_key = secrets.token_urlsafe(24)
    session_token = secrets.token_urlsafe(40)
    session_id = f"ses_{uuid.uuid4().hex[:12]}"

    ls = LiveSession(
        id=session_id,
        user_id=user.id,
        avatar_id=req.avatar_id,
        title=req.title or "Live session",
        status=LiveSessionStatus.SETUP.value,
        max_duration_minutes=req.duration_minutes or 0,
        output_format="9:16",
        config={
            "cast_selections": [c.model_dump() for c in req.cast_selections],
            "platforms": [p.model_dump() for p in req.platforms],
            "duration_minutes": req.duration_minutes,
            "background_music_id": req.background_music_id,
            "chat_reactivity": req.chat_reactivity,
            "product_rotation_minutes": req.product_rotation_minutes,
        },
        relay_stream_key=relay_key,
        session_token=session_token,
    )
    db.add(ls)

    # Auto-invite the owner as admin.
    db.add(LiveSessionInvite(
        id=f"lsi_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        email=user.email,
        role="admin",
        access_token=secrets.token_urlsafe(40),
        accepted=True,
        accepted_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    await _record_event(db, session_id, "session_created", {"by": user.email})

    return {
        "session_id": session_id,
        "status": ls.status,
        "relay_stream_key": relay_key,
        "session_token": session_token,
        "rtmp_publish_url": f"rtmp://{RELAY_HOST}:{RELAY_RTMP_PORT}/live/{relay_key}",
        "rtmp_play_url": f"rtmp://{RELAY_HOST}:{RELAY_RTMP_PORT}/live/{relay_key}",
        "hls_play_url": f"http://{RELAY_HOST}:{RELAY_HTTP_PORT}/live/{relay_key}.m3u8",
        "obs_browser_source_url": (
            f"https://www.luminacast.com/api/live-sessions/{session_id}/source?t={session_token}"
        ),
        "monitor_url": f"https://www.luminacast.com/live/{session_id}/monitor",
    }


@router.get("/{session_id}/golive-status")
async def get_golive_status(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Real-time status: viewers, purchases, current product, traction.

    Owner OR any accepted invitee can read.
    """
    ls = await db.get(LiveSession, session_id)
    if not ls:
        raise HTTPException(404, "Live session not found")
    if ls.user_id != user.id:
        # Allow accepted invitees too.
        invite = (
            await db.execute(
                select(LiveSessionInvite).where(
                    LiveSessionInvite.session_id == session_id,
                    LiveSessionInvite.email == user.email,
                    LiveSessionInvite.accepted == True,  # noqa: E712
                )
            )
        ).scalars().first()
        if not invite:
            raise HTTPException(404, "Live session not found")

    # Last 30 events for the timeline.
    events_q = await db.execute(
        select(LiveSessionEvent)
        .where(LiveSessionEvent.session_id == session_id)
        .order_by(LiveSessionEvent.created_at.desc())
        .limit(30)
    )
    events = events_q.scalars().all()

    return {
        **_golive_session_to_dict(ls),
        "recent_events": [
            {
                "id": e.id,
                "type": e.event_type,
                "data": e.data,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


@router.post("/{session_id}/invite")
async def invite_to_session(
    session_id: str,
    req: InviteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owner or admin invitee invites a teammate to monitor the session.

    Generates a magic-link access token; the invitee uses it to access the
    monitor dashboard at /live/<session>/monitor?invite=<token>.
    """
    ls = await db.get(LiveSession, session_id)
    if not ls:
        raise HTTPException(404, "Live session not found")
    is_owner = ls.user_id == user.id
    is_admin_invitee = (
        await db.execute(
            select(LiveSessionInvite).where(
                LiveSessionInvite.session_id == session_id,
                LiveSessionInvite.email == user.email,
                LiveSessionInvite.role == "admin",
                LiveSessionInvite.accepted == True,  # noqa: E712
            )
        )
    ).scalars().first()
    if not is_owner and not is_admin_invitee:
        raise HTTPException(403, "Only the owner or an admin can invite.")

    if req.role not in ("admin", "monitor", "moderator"):
        raise HTTPException(400, "role must be one of admin, monitor, moderator")

    invite_id = f"lsi_{uuid.uuid4().hex[:12]}"
    token = secrets.token_urlsafe(40)
    db.add(LiveSessionInvite(
        id=invite_id,
        session_id=session_id,
        email=req.email.strip().lower(),
        role=req.role,
        access_token=token,
        accepted=False,
    ))
    await db.commit()
    await _record_event(db, session_id, "invite_sent", {"email": req.email, "role": req.role})

    invite_link = (
        f"https://www.luminacast.com/live/{session_id}/monitor?invite={token}"
    )
    # NOTE: actual email send is handled by an existing notify service; if
    # that's not wired we still return the link so the owner can share it.
    try:
        from services import notify  # type: ignore
        if hasattr(notify, "send_live_invite"):
            await notify.send_live_invite(req.email, invite_link, ls.title)
    except Exception:
        pass
    return {"invite_id": invite_id, "invite_link": invite_link, "role": req.role}


@router.get("/{session_id}/invites")
async def list_invites(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    invites = (
        await db.execute(
            select(LiveSessionInvite).where(LiveSessionInvite.session_id == session_id)
        )
    ).scalars().all()
    return [
        {
            "id": i.id,
            "email": i.email,
            "role": i.role,
            "accepted": i.accepted,
            "invited_at": i.invited_at.isoformat() if i.invited_at else None,
        }
        for i in invites
    ]


@router.post("/{session_id}/override")
async def manual_override(
    session_id: str,
    req: OverrideRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin override: skip product, inject message, pause AI reactions, end.

    The orchestrator polls for a pending override on every loop tick (or via
    the WS, when wired). For the MVP we just record the event and update the
    session JSON so the next orchestrator iteration reads the directive.
    """
    ls = await db.get(LiveSession, session_id)
    if not ls:
        raise HTTPException(404, "Live session not found")
    is_owner = ls.user_id == user.id
    is_admin_invitee = (
        await db.execute(
            select(LiveSessionInvite).where(
                LiveSessionInvite.session_id == session_id,
                LiveSessionInvite.email == user.email,
                LiveSessionInvite.role == "admin",
                LiveSessionInvite.accepted == True,  # noqa: E712
            )
        )
    ).scalars().first()
    if not is_owner and not is_admin_invitee:
        raise HTTPException(403, "Only the owner or an admin can override.")

    valid_actions = {
        "skip_product", "inject_message", "pause_reactions",
        "resume_reactions", "end_stream", "mute_music", "unmute_music",
    }
    if req.action not in valid_actions:
        raise HTTPException(400, f"action must be one of {sorted(valid_actions)}")
    if req.action == "inject_message" and not (req.text or "").strip():
        raise HTTPException(400, "inject_message requires non-empty `text`.")

    # Stash the directive in the session config so the orchestrator picks it
    # up on its next tick. We use a simple list so multiple overrides queue.
    cfg = dict(ls.config or {})
    queue = list(cfg.get("pending_overrides") or [])
    queue.append({
        "action": req.action,
        "text": req.text,
        "by": user.email,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    cfg["pending_overrides"] = queue
    ls.config = cfg
    await db.commit()
    await _record_event(
        db, session_id, f"admin_{req.action}",
        {"by": user.email, "text": req.text},
    )
    return {"ok": True, "queued": len(queue)}


@router.post("/{session_id}/golive-start")
async def golive_start(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Spawn the Celery compositor task that drives the RTMP feed.

    Allowed states: setup, paused, ended (re-launch). Idempotent for live.
    """
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status == "live":
        return {"ok": True, "already_live": True}
    if ls.status not in ("setup", "paused", "ended", "composing"):
        raise HTTPException(
            400, f"Cannot start session in status={ls.status}"
        )
    if not (ls.config or {}).get("cast_selections"):
        raise HTTPException(400, "Add at least one cast before going live.")
    if not ls.relay_stream_key:
        raise HTTPException(400, "Session missing relay stream key.")

    ls.status = "composing"
    ls.error_message = None
    await db.commit()

    try:
        from tasks.golive_compositor import run_golive_compositor_task
        run_golive_compositor_task.delay(session_id)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        ls.status = "failed"
        ls.error_message = f"Could not enqueue compositor: {exc}"
        await db.commit()
        raise HTTPException(500, "Could not start compositor")

    await _record_event(db, session_id, "golive_start_requested", {"by": user.email})
    return {"ok": True, "status": ls.status}


@router.post("/{session_id}/golive-stop")
async def golive_stop(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cooperatively stop the compositor.

    The orchestrator loop polls `status=="ended"` each tick and tears down
    its ffmpeg child when it sees that flip. We just record the intent.
    """
    ls = await db.get(LiveSession, session_id)
    if not ls or ls.user_id != user.id:
        raise HTTPException(404, "Live session not found")
    if ls.status not in ("live", "composing", "paused"):
        return {"ok": True, "status": ls.status}
    ls.status = "ended"
    ls.ended_at = datetime.now(timezone.utc)
    await db.commit()
    await _record_event(db, session_id, "golive_stop_requested", {"by": user.email})
    return {"ok": True, "status": ls.status}


# ─── WebSocket ─── server→client status broadcast + client→server commands.

# Public OBS browser-source HTML page (auth via session_token query param,
# NOT cookies). Mounted at the orchestrator's /api/live-sessions/{id}/source
# but we ALSO expose the SPA-style /live/{id}/source via nginx — see
# main.py for the convenience alias.
from fastapi.responses import HTMLResponse


@router.get("/{session_id}/source", response_class=HTMLResponse)
async def golive_obs_source(
    session_id: str,
    t: Optional[str] = Query(None, description="session_token"),
    db: AsyncSession = Depends(get_db),
):
    """OBS browser-source HTML page.

    Loads the relay HLS stream in a fullscreen <video> at 1080x1920 (or
    16:9 / 1:1 depending on session output_format). Drops a tiny health
    probe so OBS preview never shows the placeholder colour bars.
    Auth: session_token query parameter (not cookie-based, so OBS works).
    """
    ls = await db.get(LiveSession, session_id)
    if not ls:
        raise HTTPException(404, "Live session not found")
    if not t or t != ls.session_token:
        raise HTTPException(401, "Unauthorized")

    relay_host = getattr(_settings, "LIVE_RELAY_HOST", None) or "145.223.121.28"
    relay_http_port = getattr(_settings, "LIVE_RELAY_HTTP_PORT", None) or 8085
    hls_url = f"http://{relay_host}:{relay_http_port}/live/{ls.relay_stream_key}.m3u8"

    fmt = (ls.output_format or "9:16").strip()
    if fmt == "16:9":
        w, h = 1920, 1080
    elif fmt == "1:1":
        w, h = 1080, 1080
    else:
        w, h = 1080, 1920

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Live source</title>
  <style>
    html, body {{
      margin: 0; padding: 0;
      width: {w}px; height: {h}px;
      background: #000;
      overflow: hidden;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", Roboto, sans-serif;
      color: #fff;
    }}
    #stage {{
      position: relative;
      width: 100%; height: 100%;
      display: flex; align-items: center; justify-content: center;
    }}
    video {{
      width: 100%; height: 100%; object-fit: cover;
    }}
    #placeholder {{
      position: absolute; inset: 0;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      gap: 12px;
      color: rgba(255,255,255,0.85);
      font-size: 28px; letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    #placeholder.hidden {{ display: none; }}
    .dot {{
      width: 14px; height: 14px; border-radius: 50%;
      background: #ef4444; animation: pulse 1.2s ease-in-out infinite;
    }}
    @keyframes pulse {{ 0%, 100% {{ opacity: 0.4; }} 50% {{ opacity: 1; }} }}
  </style>
</head>
<body>
  <div id="stage">
    <video id="v" autoplay muted playsinline></video>
    <div id="placeholder"><div class="dot"></div>Connecting&hellip;</div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js"></script>
  <script>
    (function() {{
      var v = document.getElementById('v');
      var ph = document.getElementById('placeholder');
      var src = {json.dumps(hls_url)};
      v.addEventListener('playing', function() {{ ph.classList.add('hidden'); }});
      v.addEventListener('error', function() {{ ph.classList.remove('hidden'); }});
      function attach() {{
        if (window.Hls && window.Hls.isSupported()) {{
          var hls = new Hls({{ liveSyncDuration: 2, liveMaxLatencyDuration: 5 }});
          hls.loadSource(src);
          hls.attachMedia(v);
          hls.on(window.Hls.Events.ERROR, function(_e, data) {{
            if (data && data.fatal) setTimeout(attach, 2000);
          }});
        }} else if (v.canPlayType('application/vnd.apple.mpegurl')) {{
          v.src = src; v.play().catch(function() {{}});
        }} else {{
          ph.textContent = 'HLS not supported in this browser';
        }}
      }}
      attach();
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.websocket("/{session_id}/ws")
async def golive_websocket(
    websocket: WebSocket,
    session_id: str,
    t: Optional[str] = Query(None, description="session_token from /golive"),
):
    """Live session WebSocket.

    Auth: require either an authenticated cookie session OR a valid
    `t=<session_token>` query parameter. Accepts JSON command messages
    from the client (override actions) and pushes status snapshots ~ every
    2s plus event-driven pushes when an override is consumed.
    """
    from database import async_session_factory
    await websocket.accept()

    # Validate auth.
    async with async_session_factory() as db:
        ls = await db.get(LiveSession, session_id)
        if not ls:
            await websocket.close(code=4404, reason="session_not_found")
            return
        if not t or t != ls.session_token:
            await websocket.close(code=4401, reason="unauthorized")
            return

    async def send_status():
        async with async_session_factory() as db:
            ls = await db.get(LiveSession, session_id)
            if not ls:
                return False
            payload = {
                "type": "status",
                "status": ls.status,
                "current_product_index": ls.current_product_index or 0,
                "total_viewers": ls.total_viewers or 0,
                "peak_viewers": ls.peak_viewers or 0,
                "total_purchases": ls.total_purchases or 0,
                "total_revenue_cents": ls.total_revenue_cents or 0,
                "total_comments": ls.total_comments or 0,
                "last_bitrate_kbps": ls.last_bitrate_kbps or 0,
                "last_dropped_frames": ls.last_dropped_frames or 0,
                "started_at": ls.started_at.isoformat() if ls.started_at else None,
                "ended_at": ls.ended_at.isoformat() if ls.ended_at else None,
            }
            await websocket.send_text(json.dumps(payload))
            return ls.status not in ("ended", "failed")

    async def receiver():
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                action = (msg or {}).get("action")
                text = (msg or {}).get("text")
                if not action:
                    continue
                async with async_session_factory() as db:
                    ls2 = await db.get(LiveSession, session_id)
                    if not ls2:
                        return
                    cfg = dict(ls2.config or {})
                    queue = list(cfg.get("pending_overrides") or [])
                    queue.append({
                        "action": action,
                        "text": text,
                        "by": "ws",
                        "at": datetime.now(timezone.utc).isoformat(),
                    })
                    cfg["pending_overrides"] = queue
                    ls2.config = cfg
                    await db.commit()
                    await _record_event(
                        db, session_id, f"ws_{action}", {"text": text},
                    )
        except WebSocketDisconnect:
            return
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            return

    recv_task = asyncio.create_task(receiver())
    try:
        while True:
            still_running = await send_status()
            if not still_running:
                break
            await asyncio.sleep(2.0)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
    finally:
        recv_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/{session_id}/events")
async def list_events(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
    """Read the live session event log (most-recent first)."""
    ls = await db.get(LiveSession, session_id)
    if not ls:
        raise HTTPException(404, "Live session not found")
    if ls.user_id != user.id:
        invite = (
            await db.execute(
                select(LiveSessionInvite).where(
                    LiveSessionInvite.session_id == session_id,
                    LiveSessionInvite.email == user.email,
                    LiveSessionInvite.accepted == True,  # noqa: E712
                )
            )
        ).scalars().first()
        if not invite:
            raise HTTPException(404, "Live session not found")
    rows = (
        await db.execute(
            select(LiveSessionEvent)
            .where(LiveSessionEvent.session_id == session_id)
            .order_by(LiveSessionEvent.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
    ).scalars().all()
    return [
        {
            "id": e.id,
            "type": e.event_type,
            "data": e.data,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]

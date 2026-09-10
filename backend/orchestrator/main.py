"""Luminacast Omni — FastAPI Application Entry Point."""
import logging
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import engine, Base, async_session_factory
from logging_config import setup_logging
from services.sentry import init_sentry
from websocket.manager import ws_manager

# Initialize structured logging (Better Stack + stdout)
setup_logging()

logger = logging.getLogger(__name__)


async def seed_admin():
    """Create the default admin user if it doesn't exist."""
    from sqlalchemy import select
    from models.user import User, UserRole
    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    admin_email = settings.ADMIN_EMAIL or f"{settings.ADMIN_USERNAME}@luminacast.com"

    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == admin_email)
        )
        admin = result.scalar_one_or_none()

        if not admin:
            admin = User(
                id=f"usr_admin_{uuid.uuid4().hex[:8]}",
                email=admin_email,
                password_hash=pwd_context.hash(settings.ADMIN_PASSWORD),
                role=UserRole.ADMIN,
                is_active=True,
            )
            session.add(admin)
            await session.commit()
            logger.info(json.dumps({
                "service": "main",
                "level": "info",
                "message": f"Admin user seeded ({admin_email})",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))


async def seed_default_avatar():
    """Create a default avatar that casts can reference before a real one is created."""
    from sqlalchemy import select
    from models.avatar import Avatar, AvatarType, AvatarStatus

    async with async_session_factory() as session:
        result = await session.execute(select(Avatar).where(Avatar.id == "default"))
        if not result.scalar_one_or_none():
            # Find the admin user to attach the default avatar
            from models.user import User, UserRole
            admin_result = await session.execute(
                select(User).where(User.role == UserRole.ADMIN).limit(1)
            )
            admin = admin_result.scalar_one_or_none()
            admin_id = admin.id if admin else "system"

            default_avatar = Avatar(
                id="default",
                user_id=admin_id,
                type=AvatarType.DIGITAL,
                status=AvatarStatus.READY,
                persona_profile={
                    "tone": "energetic, bubbly",
                    "energy_level": "high",
                    "catchphrases": ["oh my god you guys", "this is amazing"],
                    "vocabulary_level": "casual",
                    "selling_style": "enthusiastic product demos",
                    "pacing": "fast, short sentences",
                    "greeting_style": "Hey everyone! Welcome back!",
                    "closing_style": "Don't forget to follow and tap that basket!",
                },
            )
            session.add(default_avatar)
            await session.commit()
            logger.info(json.dumps({
                "service": "main",
                "level": "info",
                "message": "Default avatar seeded",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    # Startup
    init_sentry()

    # Schema is managed by Alembic — do NOT use create_all.
    # Alembic migrations run via `alembic upgrade head` in the deploy pipeline.
    # Import models so they are registered with Base (needed for relationship resolution).
    from models import (User, Avatar, Cast, Product, Block, Variant,  # noqa: F401
                        StreamSession, ChatMessage, BillingEvent, TeamMember, CastProduct,
                        ApiUsageLog, VoiceModel, ScrapingJob, LayoutTemplate,
                        Channel, ChannelTranscript, ProductAsset, TrendingProduct,
                        SoundCast, MusicTrack, UserVideoAsset)

    # Idempotent column additions. Alembic has cycle issues we don't want to
    # untangle right now; this is the safest place to ensure these columns
    # exist on every startup. ADD COLUMN IF NOT EXISTS is a no-op when the
    # column is already present.
    from sqlalchemy import text as _sa_text
    async with async_session_factory() as _s:
        try:
            # body_shot_sets async-job columns (introduced for Cloudflare-safe
            # async body-shot pipeline).
            await _s.execute(_sa_text(
                "ALTER TABLE body_shot_sets "
                "ADD COLUMN IF NOT EXISTS status TEXT, "
                "ADD COLUMN IF NOT EXISTS error_message TEXT, "
                "ADD COLUMN IF NOT EXISTS canonical_key TEXT, "
                "ADD COLUMN IF NOT EXISTS validation JSONB"
            ))
            # PR #65: voice post-process / clip-mic toggle.
            # variants.tts_lipsync_r2_key holds the 16 kHz WAV consumed by
            # the lipsync engine (separate from the 44.1 kHz MP3 master in
            # tts_r2_key). avatars.clip_mic_enabled toggles the EQ profile
            # (lavalier vs phone mic) and the mic-style suffix appended to
            # the voice description before TTS.
            await _s.execute(_sa_text(
                "ALTER TABLE variants "
                "ADD COLUMN IF NOT EXISTS tts_lipsync_r2_key TEXT"
            ))
            await _s.execute(_sa_text(
                "ALTER TABLE avatars "
                "ADD COLUMN IF NOT EXISTS clip_mic_enabled BOOLEAN "
                "NOT NULL DEFAULT FALSE"
            ))
            # blocks: Smart Cast metadata columns.
            await _s.execute(_sa_text(
                "ALTER TABLE blocks "
                "ADD COLUMN IF NOT EXISTS hook_type VARCHAR(40), "
                "ADD COLUMN IF NOT EXISTS background_type VARCHAR(20), "
                "ADD COLUMN IF NOT EXISTS transition_in VARCHAR(20), "
                "ADD COLUMN IF NOT EXISTS energy_level VARCHAR(10), "
                "ADD COLUMN IF NOT EXISTS stock_media_query TEXT, "
                "ADD COLUMN IF NOT EXISTS stock_media_url TEXT, "
                "ADD COLUMN IF NOT EXISTS stock_media_thumbnail TEXT, "
                "ADD COLUMN IF NOT EXISTS stock_media_kind VARCHAR(10), "
                "ADD COLUMN IF NOT EXISTS stock_media_pexels_id VARCHAR(40), "
                # Parallel media — visual b-roll that plays ON TOP of a
                # voiceover/speaking block, rather than replacing the avatar.
                "ADD COLUMN IF NOT EXISTS parallel_media JSONB"
            ))
            # live_sessions: Go-Live (multi-cast / multi-platform / monitor) extensions.
            # The legacy live_sessions table existed for the voice-only MVP —
            # these columns extend it for the full Go-Live page.
            await _s.execute(_sa_text(
                "ALTER TABLE live_sessions "
                "ADD COLUMN IF NOT EXISTS config JSONB, "
                "ADD COLUMN IF NOT EXISTS relay_stream_key VARCHAR(64), "
                "ADD COLUMN IF NOT EXISTS session_token VARCHAR(128), "
                "ADD COLUMN IF NOT EXISTS total_viewers INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS peak_viewers INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS total_purchases INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS total_revenue_cents INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS total_comments INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS last_bitrate_kbps INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS last_dropped_frames INTEGER DEFAULT 0, "
                "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP"
            ))
            # Unique index on relay_stream_key (idempotent).
            await _s.execute(_sa_text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_live_sessions_relay_key "
                "ON live_sessions(relay_stream_key) WHERE relay_stream_key IS NOT NULL"
            ))
            # New companion tables — created lazily here so legacy DBs without
            # alembic-applied migrations still work.
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS live_session_invites ("
                "id VARCHAR(40) PRIMARY KEY, "
                "session_id VARCHAR(40) REFERENCES live_sessions(id) ON DELETE CASCADE, "
                "email VARCHAR(255) NOT NULL, "
                "role VARCHAR(20) NOT NULL DEFAULT 'monitor', "
                "access_token VARCHAR(128) UNIQUE, "
                "accepted BOOLEAN NOT NULL DEFAULT FALSE, "
                "invited_at TIMESTAMP NOT NULL DEFAULT now(), "
                "accepted_at TIMESTAMP)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_live_session_invites_session_id "
                "ON live_session_invites(session_id)"
            ))
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS live_session_events ("
                "id VARCHAR(40) PRIMARY KEY, "
                "session_id VARCHAR(40) REFERENCES live_sessions(id) ON DELETE CASCADE, "
                "event_type VARCHAR(40) NOT NULL, "
                "data JSONB, "
                "created_at TIMESTAMP NOT NULL DEFAULT now())"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_live_session_events_session_id "
                "ON live_session_events(session_id)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_live_session_events_event_type "
                "ON live_session_events(event_type)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_live_session_events_created_at "
                "ON live_session_events(created_at)"
            ))
            # Zernio: social_posts and social_comments tables.
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS social_posts ("
                "id VARCHAR(40) PRIMARY KEY, "
                "user_id VARCHAR(40) REFERENCES users(id) ON DELETE CASCADE, "
                "cast_id VARCHAR(40) REFERENCES casts(id) ON DELETE SET NULL, "
                "render_id VARCHAR(40), "
                "zernio_post_id VARCHAR(64), "
                "caption TEXT, "
                "hashtags JSONB, "
                "media_url VARCHAR(500), "
                "platforms JSONB, "
                "scheduled_for TIMESTAMP, "
                "status VARCHAR(20) NOT NULL DEFAULT 'draft', "
                "platform_post_ids JSONB, "
                "analytics JSONB, "
                "created_at TIMESTAMP NOT NULL DEFAULT now(), "
                "published_at TIMESTAMP, "
                "error_message TEXT)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_posts_user_id ON social_posts(user_id)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_posts_cast_id ON social_posts(cast_id)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_posts_status ON social_posts(status)"
            ))
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS social_comments ("
                "id VARCHAR(40) PRIMARY KEY, "
                "social_post_id VARCHAR(40) REFERENCES social_posts(id) ON DELETE CASCADE, "
                "platform VARCHAR(20), "
                "platform_comment_id VARCHAR(64), "
                "author_name VARCHAR(100), "
                "author_handle VARCHAR(100), "
                "text TEXT, "
                "ai_suggested_reply TEXT, "
                "reply_status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                "actual_reply TEXT, "
                "is_prompt_injection BOOLEAN NOT NULL DEFAULT FALSE, "
                "created_at TIMESTAMP, "
                "replied_at TIMESTAMP)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_comments_post_id ON social_comments(social_post_id)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_comments_reply_status ON social_comments(reply_status)"
            ))
            # Publish hub: social_channels (connected social accounts).
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS social_channels ("
                "id VARCHAR(40) PRIMARY KEY, "
                "user_id VARCHAR(40) REFERENCES users(id) ON DELETE CASCADE, "
                "platform VARCHAR(20) NOT NULL, "
                "platform_account_id VARCHAR(100), "
                "handle VARCHAR(100), "
                "display_name VARCHAR(200), "
                "follower_count INTEGER DEFAULT 0, "
                "profile_image_url VARCHAR(500), "
                "zernio_account_id VARCHAR(64), "
                "primary_avatar_id VARCHAR(40) REFERENCES avatars(id) ON DELETE SET NULL, "
                "avatar_history JSONB, "
                "total_posts INTEGER DEFAULT 0, "
                "total_scheduled INTEGER DEFAULT 0, "
                "status VARCHAR(20) NOT NULL DEFAULT 'active', "
                "connected_at TIMESTAMP NOT NULL DEFAULT now(), "
                "last_seen_at TIMESTAMP)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_channels_user_id ON social_channels(user_id)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_social_channels_platform ON social_channels(platform)"
            ))
            # Mubert v3 per-user credentials (cached after first generation).
            await _s.execute(_sa_text(
                "ALTER TABLE users "
                "ADD COLUMN IF NOT EXISTS mubert_customer_id VARCHAR(80), "
                "ADD COLUMN IF NOT EXISTS mubert_access_token TEXT"
            ))
            # Cast: AI background music + caption preset + cast-wide
            # default avatar background look. background_music_* is set
            # when Mubert generates the track; caption_preset drives
            # both the Remotion preview and the FFmpeg drawtext fallback;
            # default_avatar_look_id is the cast-wide background pick
            # the user makes at Setup time and that the outline pipeline
            # then propagates onto each new block.
            await _s.execute(_sa_text(
                "ALTER TABLE casts "
                "ADD COLUMN IF NOT EXISTS background_music_url TEXT, "
                "ADD COLUMN IF NOT EXISTS background_music_mood VARCHAR(40), "
                "ADD COLUMN IF NOT EXISTS background_music_tags JSONB, "
                "ADD COLUMN IF NOT EXISTS caption_preset JSONB, "
                "ADD COLUMN IF NOT EXISTS default_avatar_look_id VARCHAR(40), "
                "ADD COLUMN IF NOT EXISTS template_id VARCHAR(64)"
            ))
            # music_catalog: pre-generated Mubert tracks across mood presets,
            # browsable from the Music page without waiting for generation.
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS music_catalog ("
                "id VARCHAR(40) PRIMARY KEY, "
                "name VARCHAR(120) NOT NULL, "
                "mood VARCHAR(40) NOT NULL, "
                "intensity VARCHAR(10) NOT NULL DEFAULT 'medium', "
                "tempo VARCHAR(10) NOT NULL DEFAULT 'medium', "
                "duration_seconds INTEGER NOT NULL, "
                "prompt TEXT, "
                "r2_key TEXT, "
                "public_url TEXT, "
                "mubert_track_id VARCHAR(80), "
                "created_at TIMESTAMP NOT NULL DEFAULT now())"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_music_catalog_mood ON music_catalog(mood)"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_music_catalog_duration ON music_catalog(duration_seconds)"
            ))
            # uploaded_music: user-uploaded mp3/wav files that can be used as
            # cast background tracks. Owned per user.
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS uploaded_music ("
                "id VARCHAR(40) PRIMARY KEY, "
                "user_id VARCHAR(40) REFERENCES users(id) ON DELETE CASCADE, "
                "name VARCHAR(200) NOT NULL, "
                "r2_key TEXT NOT NULL, "
                "public_url TEXT NOT NULL, "
                "duration_seconds INTEGER, "
                "file_size_bytes INTEGER, "
                "mime_type VARCHAR(40), "
                "created_at TIMESTAMP NOT NULL DEFAULT now())"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_uploaded_music_user_id ON uploaded_music(user_id)"
            ))
            # ai_generated_music: Mubert AI-Generate results, saved per user so
            # they survive a page reload instead of only living in local React
            # state (previously they vanished the moment you left the tab).
            await _s.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS ai_generated_music ("
                "id VARCHAR(40) PRIMARY KEY, "
                "user_id VARCHAR(40) REFERENCES users(id) ON DELETE CASCADE, "
                "name VARCHAR(200) NOT NULL, "
                "prompt TEXT, "
                "mood VARCHAR(40), "
                "intensity VARCHAR(10), "
                "bpm INTEGER, "
                "musical_key VARCHAR(10), "
                "public_url TEXT NOT NULL, "
                "duration_seconds INTEGER, "
                "created_at TIMESTAMP NOT NULL DEFAULT now())"
            ))
            await _s.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS ix_ai_generated_music_user_id ON ai_generated_music(user_id)"
            ))
            # avatar_looks: scene environment + visible-mic toggle for look
            # generation (studio/room/outdoor backdrop, lavalier mic in shot).
            await _s.execute(_sa_text(
                "ALTER TABLE avatar_looks "
                "ADD COLUMN IF NOT EXISTS environment VARCHAR(20) NOT NULL DEFAULT 'studio', "
                "ADD COLUMN IF NOT EXISTS mic_visible BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            # blocks: avatar_motion/avatar_acting merged into avatar_action,
            # with separate start/end action prompts.
            await _s.execute(_sa_text(
                "UPDATE blocks SET category = 'avatar_action' "
                "WHERE category IN ('avatar_motion', 'avatar_acting')"
            ))
            await _s.execute(_sa_text(
                "ALTER TABLE blocks "
                "ADD COLUMN IF NOT EXISTS action_start_prompt TEXT, "
                "ADD COLUMN IF NOT EXISTS action_end_prompt TEXT"
            ))
            await _s.commit()
            logger.info("Idempotent column additions verified (body_shot_sets, blocks, live_sessions, social_*, mubert, music, avatar_looks)")
        except Exception as _e:
            import sentry_sdk
            sentry_sdk.capture_exception(_e)
            logger.warning(f"Idempotent ALTER TABLE skipped: {_e}")
            await _s.rollback()

    # Seed admin and default avatar
    await seed_admin()
    await seed_default_avatar()

    logger.info(json.dumps({
        "service": "main",
        "level": "info",
        "message": f"Luminacast Omni started (env={settings.APP_ENV})",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    logger.info(f"RunPod config: endpoint={settings.RUNPOD_ENDPOINT_ID}, is_public={settings.RUNPOD_ENDPOINT_IS_PUBLIC}")

    yield

    # Shutdown
    await engine.dispose()


app = FastAPI(
    title="Luminacast Omni",
    description="AI Avatar Live Selling Platform for TikTok",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request context + audit-log fallback. Must run inside CORS so the
# Access-Control-Allow-* headers are added to every response (CORS is the
# outermost layer in Starlette's stack — last add_middleware wraps first).
from middleware.request_context import RequestContextMiddleware
app.add_middleware(RequestContextMiddleware)

# Without this, an OpenRouterError (e.g. "insufficient credits") propagates
# as an unhandled exception — FastAPI's default handler turns that into a
# content-free 500, so every LLM failure looked identical to the user
# regardless of cause ("script generation failed" with no reason). Convert
# it here, once, for every endpoint that touches OpenRouter, instead of
# wrapping each of the 10+ call sites individually.
from fastapi import Request
from fastapi.responses import JSONResponse
from services.openrouter import OpenRouterError


@app.exception_handler(OpenRouterError)
async def _openrouter_error_handler(request: Request, exc: OpenRouterError):
    status_code = 503 if exc.status_code >= 500 else 502
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})

# Register routers
from routers import auth, avatar, casts, stream, chat, products, analytics, admin, layouts, channels, webhooks, product_discovery
from routers import music as music_router
from routers import user_videos as user_videos_router
from routers import user_photos as user_photos_router
from routers import avatar_looks as avatar_looks_router
from routers import voice_corpus as voice_corpus_router
from routers import live_references as live_references_router
from routers import live_sessions as live_sessions_router
from routers import stock_media as stock_media_router
from routers import render_jobs as render_jobs_router
from routers import clone_scout as clone_scout_router
from routers import clone_pipeline as clone_pipeline_router
from routers import generated_photos as generated_photos_router
from routers import generated_videos as generated_videos_router
from routers import captions as captions_router
from routers import users as users_router
from routers import social as social_router
from routers import admin_costs as admin_costs_router
from routers import usage as usage_router
from routers import teams as teams_router
from routers import billing as billing_router
from routers import playground as playground_router

app.include_router(auth.router)
app.include_router(clone_pipeline_router.router)
app.include_router(avatar.router)
app.include_router(casts.router)
app.include_router(stream.router)
app.include_router(chat.router)
app.include_router(products.router)
app.include_router(product_discovery.router)
app.include_router(analytics.router)
app.include_router(admin.router)
app.include_router(layouts.router)
app.include_router(channels.router)
app.include_router(webhooks.router)
app.include_router(music_router.router)
app.include_router(user_videos_router.router)
app.include_router(user_photos_router.router)
app.include_router(avatar_looks_router.router)
app.include_router(voice_corpus_router.router)
app.include_router(live_references_router.router)
app.include_router(live_sessions_router.router)
app.include_router(stock_media_router.router)
app.include_router(render_jobs_router.router)
app.include_router(clone_scout_router.router)
app.include_router(generated_photos_router.router)
app.include_router(generated_videos_router.router)
app.include_router(captions_router.router)
app.include_router(users_router.router)
app.include_router(social_router.router)
app.include_router(admin_costs_router.router)
app.include_router(usage_router.router)
app.include_router(teams_router.router)
app.include_router(billing_router.router)
app.include_router(playground_router.router)

# User action audit log read endpoints.
from api import history as history_api
app.include_router(history_api.router)

@app.get("/api/system/musetalk-status")
async def musetalk_status():
    """Check if MuseTalk is available on the GPU server."""
    from config import settings
    if not settings.MUSETALK_AVAILABLE:
        return {"available": False, "current_model": None}
    try:
        from services.musetalk_client import get_musetalk_client
        client = get_musetalk_client()
        healthy = await client.is_healthy()
        return {"available": healthy, "current_model": None}
    except Exception:
        return {"available": False, "current_model": None}



# Health check
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "luminacast-omni", "timestamp": datetime.now(timezone.utc).isoformat()}


# WebSocket: Stream session (creator/operator clients)
@app.websocket("/ws/stream/{session_id}")
async def ws_stream(websocket: WebSocket, session_id: str, token: str = Query(...)):
    user_data = await ws_manager.authenticate(websocket, token)
    if not user_data:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    user_id = user_data.get("sub", "unknown")
    await ws_manager.connect_session(websocket, session_id, user_id)

    # Broadcast operator presence
    operators = ws_manager.get_session_operators(session_id)
    await ws_manager.broadcast_to_session(session_id, {
        "type": "OPERATOR_PRESENCE",
        "payload": {"operators": operators},
    })

    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")
            payload = data.get("payload", {})

            if event_type == "PIN_CONFIRM":
                await ws_manager.broadcast_to_session(session_id, {
                    "type": "PIN_CONFIRMED",
                    "payload": payload,
                })
            elif event_type == "APPROVE_CHAT":
                await ws_manager.broadcast_to_session(session_id, {
                    "type": "CHAT_APPROVED",
                    "payload": payload,
                })
            elif event_type == "REJECT_CHAT":
                await ws_manager.broadcast_to_session(session_id, {
                    "type": "CHAT_REJECTED",
                    "payload": payload,
                })
            elif event_type == "LOCK_CHAT_DRAFT":
                await ws_manager.broadcast_to_session(session_id, {
                    "type": "CHAT_DRAFT_LOCKED",
                    "payload": {"message_id": payload.get("message_id"), "locked_by": user_id},
                })
            elif event_type == "STREAM_CONTROL":
                await ws_manager.broadcast_to_session(session_id, {
                    "type": "STREAM_CONTROL",
                    "payload": payload,
                })
    except WebSocketDisconnect:
        ws_manager.disconnect_session(websocket, session_id, user_id)
        operators = ws_manager.get_session_operators(session_id)
        await ws_manager.broadcast_to_session(session_id, {
            "type": "OPERATOR_PRESENCE",
            "payload": {"operators": operators},
        })


# WebSocket: Internal chat monitor bridge
@app.websocket("/ws/internal/chat")
async def ws_internal_chat(websocket: WebSocket, token: str = Query(...)):
    is_valid = await ws_manager.authenticate_internal(token)
    if not is_valid:
        await websocket.close(code=4001, reason="Invalid service token")
        return

    await ws_manager.connect_internal(websocket)

    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")
            payload = data.get("payload", {})

            if event_type == "INCOMING_CHAT":
                session_id = payload.get("session_id")
                if session_id:
                    # Process incoming chat message
                    from engine.chat_classifier import classify_message

                    result = await classify_message(
                        message=payload.get("message", ""),
                        product_rules=payload.get("product_rules", []),
                        context=payload.get("context", {}),
                    )

                    # Store and broadcast
                    await ws_manager.broadcast_to_session(session_id, {
                        "type": "NEW_CHAT_MESSAGE",
                        "payload": {
                            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                            "viewer_username": payload.get("username", "viewer"),
                            "message_text": payload.get("message", ""),
                            "is_purchase": result.is_purchase,
                            "ai_draft": result.response,
                            "ai_draft_status": "pending_approval" if result.needs_approval else ("auto_sent" if result.response else None),
                        },
                    })
            elif event_type == "PURCHASE_EVENT":
                session_id = payload.get("session_id")
                if session_id:
                    await ws_manager.broadcast_to_session(session_id, {
                        "type": "NEW_CHAT_MESSAGE",
                        "payload": {
                            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
                            "viewer_username": payload.get("username", ""),
                            "message_text": payload.get("message", ""),
                            "is_purchase": True,
                        },
                    })
    except WebSocketDisconnect:
        ws_manager.disconnect_internal(websocket)

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "luminacast-orchestrator"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
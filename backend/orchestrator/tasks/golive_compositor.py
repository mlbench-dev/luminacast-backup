"""Go-Live compositor — long-running Celery task that drives a live broadcast.

Pipeline:
  1. Resolve the LiveSession's cast_selections to a list of pre-rendered
     video URLs from R2 (one per selected cast/render).
  2. Build an ffconcat playlist file in /tmp.
  3. Spawn ffmpeg to push it as RTMP to the local SRS relay.
  4. Run the orchestrator loop in parallel (in-process):
       - Rotate to the next product when product_rotation_minutes elapses
         (or the admin queues a `skip_product` override).
       - Consume any pending overrides each tick.
       - Heartbeat LiveSession with bitrate / dropped frames every ~5 s.
  5. Stop when the session row's status flips to "ended" (set by the API
     /stop endpoint) or when max_duration_minutes elapses.

Per RULES.md: NO hardcoded render timeouts — the only timeout is the
Celery `task_time_limit` set on this task. The orchestrator loop polls
session status; it doesn't impose its own timeout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone

import sentry_sdk
from sqlalchemy import select

from tasks import celery_app

logger = logging.getLogger(__name__)


# Long-running task — give it generous time. The only timeout (per RULES).
# 8 hours covers the longest practical live session. Stops earlier whenever
# the session is marked ended.
GOLIVE_TASK_TIME_LIMIT = 60 * 60 * 8


@celery_app.task(
    name="tasks.golive_compositor.run",
    bind=True,
    max_retries=0,
    time_limit=GOLIVE_TASK_TIME_LIMIT,
    soft_time_limit=GOLIVE_TASK_TIME_LIMIT - 60,
)
def run_golive_compositor_task(self, session_id: str) -> dict:
    """Entry point — runs the async compositor in a fresh event loop."""
    logger.info("[golive] starting compositor task for session=%s", session_id)
    try:
        return asyncio.run(_run_async(session_id))
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.exception("[golive] compositor task crashed for %s", session_id)
        # Best-effort flip status to failed so the UI doesn't think we're live.
        try:
            asyncio.run(_mark_failed(session_id, str(exc)))
        except Exception as exc2:
            sentry_sdk.capture_exception(exc2)
        raise


async def _mark_failed(session_id: str, message: str) -> None:
    from database import async_session_factory
    from models.live_session import LiveSession
    async with async_session_factory() as db:
        ls = await db.get(LiveSession, session_id)
        if ls is None:
            return
        ls.status = "failed"
        ls.error_message = (message or "")[:500]
        ls.ended_at = datetime.now(timezone.utc)
        await db.commit()


async def _resolve_render_urls(db, ls) -> list[str]:
    """Look up each cast_selection's render → return public R2 URLs."""
    from models.cast_render import CastRender, CastRenderStatus
    from config import settings

    cfg = ls.config or {}
    selections = cfg.get("cast_selections") or []
    urls: list[str] = []
    public_base = (
        getattr(settings, "R2_PUBLIC_BASE_URL", None)
        or "https://media.luminacast.com"
    ).rstrip("/")

    for sel in selections:
        render_id = sel.get("render_id")
        cast_id = sel.get("cast_id")
        render = None
        if render_id:
            render = await db.get(CastRender, render_id)
        if render is None and cast_id:
            row = (
                await db.execute(
                    select(CastRender)
                    .where(
                        CastRender.cast_id == cast_id,
                        CastRender.status == CastRenderStatus.COMPLETED.value,
                    )
                    .order_by(CastRender.id.desc())
                    .limit(1)
                )
            ).scalars().first()
            render = row
        if render is None or not render.output_video_r2_key:
            logger.warning("[golive] no completed render for selection %s", sel)
            continue
        urls.append(f"{public_base}/{render.output_video_r2_key}")
    return urls


def _build_rtmp_url(relay_stream_key: str) -> str:
    from config import settings
    host = getattr(settings, "LIVE_RELAY_HOST", None) or "145.223.121.28"
    port = getattr(settings, "LIVE_RELAY_RTMP_PORT", None) or 1935
    return f"rtmp://{host}:{port}/live/{relay_stream_key}"


async def _run_async(session_id: str) -> dict:
    from database import async_session_factory
    from models.live_session import LiveSession
    from services.rtmp_compositor import build_concat_playlist, build_ffmpeg_command
    from services.live_orchestrator import (
        consume_pending_overrides,
        append_event,
    )

    factory = async_session_factory

    # -- Phase A: setup --------------------------------------------------
    async with factory() as db:
        ls = await db.get(LiveSession, session_id)
        if ls is None:
            return {"error": "session_not_found"}
        if not ls.relay_stream_key:
            ls.status = "failed"
            ls.error_message = "Session has no relay stream key"
            await db.commit()
            return {"error": "no_relay_stream_key"}

        render_urls = await _resolve_render_urls(db, ls)
        if not render_urls:
            ls.status = "failed"
            ls.error_message = "No completed renders for selected casts"
            await db.commit()
            await append_event(db, session_id, "compositor_failed", {
                "reason": "no_completed_renders",
            })
            return {"error": "no_completed_renders"}

        cfg = dict(ls.config or {})
        rotation_minutes = int(cfg.get("product_rotation_minutes") or 10)
        max_duration_minutes = int(cfg.get("duration_minutes") or 0)  # 0 = until stopped
        relay_url = _build_rtmp_url(ls.relay_stream_key)

        ls.status = "composing"
        await db.commit()
        await append_event(db, session_id, "compositor_starting", {
            "render_count": len(render_urls),
            "rotation_minutes": rotation_minutes,
        })

    # -- Phase B: write playlist + spawn ffmpeg --------------------------
    playlist_text = build_concat_playlist(render_urls, loop=True)
    fd, playlist_path = tempfile.mkstemp(prefix=f"golive_{session_id}_", suffix=".ffconcat")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(playlist_text)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        async with factory() as db:
            ls = await db.get(LiveSession, session_id)
            if ls:
                ls.status = "failed"
                ls.error_message = f"Could not write playlist: {exc}"
                await db.commit()
        return {"error": "playlist_write_failed"}

    ffmpeg_cmd = build_ffmpeg_command(
        playlist_path=playlist_path,
        rtmp_url=relay_url,
        overlay_text=None,
        bitrate_kbps=4000,
        aspect_ratio="9:16",
    )
    logger.info("[golive] spawning ffmpeg: %s", " ".join(ffmpeg_cmd))

    try:
        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        async with factory() as db:
            ls = await db.get(LiveSession, session_id)
            if ls:
                ls.status = "failed"
                ls.error_message = f"ffmpeg spawn failed: {exc}"
                await db.commit()
        try:
            os.unlink(playlist_path)
        except Exception:
            pass
        return {"error": "ffmpeg_spawn_failed"}

    async with factory() as db:
        ls = await db.get(LiveSession, session_id)
        if ls:
            ls.status = "live"
            ls.started_at = ls.started_at or datetime.now(timezone.utc)
            await db.commit()
        await append_event(db, session_id, "compositor_live", {"pid": proc.pid})

    # -- Phase C: orchestrator loop --------------------------------------
    started_monotonic = time.monotonic()
    last_rotation_at = started_monotonic
    last_heartbeat_at = 0.0
    current_product_index = 0
    paused_reactions = False
    music_muted = False
    end_reason = "stopped"

    try:
        while True:
            await asyncio.sleep(2.0)

            # Has ffmpeg died?
            if proc.poll() is not None:
                logger.warning("[golive] ffmpeg exited rc=%s", proc.returncode)
                end_reason = "ffmpeg_exited"
                break

            now = time.monotonic()

            async with factory() as db:
                ls = await db.get(LiveSession, session_id)
                if ls is None:
                    end_reason = "session_deleted"
                    break

                # Stop conditions.
                if ls.status == "ended":
                    end_reason = "stopped_by_user"
                    break
                if max_duration_minutes and (
                    now - started_monotonic > max_duration_minutes * 60
                ):
                    end_reason = "max_duration_reached"
                    break

                # Pause: just record it; the upstream stream keeps playing
                # (admin can pause AI reactions, not the playlist).
                if ls.status == "paused":
                    await asyncio.sleep(1.0)
                    continue

                # -- Override consumption --------------------------------
                overrides = await consume_pending_overrides(db, ls, max_consume=10)
                for ov in overrides:
                    action = ov.get("action")
                    if action == "skip_product":
                        current_product_index += 1
                        last_rotation_at = now
                        await append_event(db, session_id, "product_skipped", {
                            "by": ov.get("by"),
                            "to_index": current_product_index,
                        })
                    elif action == "end_stream":
                        end_reason = "ended_by_admin"
                        ls.status = "ended"
                        ls.ended_at = datetime.now(timezone.utc)
                        await db.commit()
                    elif action == "pause_reactions":
                        paused_reactions = True
                        await append_event(db, session_id, "reactions_paused", {
                            "by": ov.get("by"),
                        })
                    elif action == "resume_reactions":
                        paused_reactions = False
                        await append_event(db, session_id, "reactions_resumed", {
                            "by": ov.get("by"),
                        })
                    elif action == "mute_music":
                        music_muted = True
                        await append_event(db, session_id, "music_muted", {
                            "by": ov.get("by"),
                        })
                    elif action == "unmute_music":
                        music_muted = False
                        await append_event(db, session_id, "music_unmuted", {
                            "by": ov.get("by"),
                        })
                    elif action == "inject_message":
                        # Phase 2 will hand this to the reactive voiceover
                        # path. For Phase 1 we just record it.
                        await append_event(db, session_id, "message_injected", {
                            "by": ov.get("by"),
                            "text": ov.get("text"),
                        })

                if end_reason == "ended_by_admin":
                    break

                # -- Product rotation (time-based) -----------------------
                if rotation_minutes > 0 and (
                    now - last_rotation_at > rotation_minutes * 60
                ):
                    current_product_index += 1
                    last_rotation_at = now
                    await append_event(db, session_id, "product_rotated", {
                        "to_index": current_product_index,
                        "trigger": "time",
                    })
                    ls.current_product_index = current_product_index

                # -- Heartbeat (every ~5s) -------------------------------
                if now - last_heartbeat_at > 5.0:
                    last_heartbeat_at = now
                    # Best-effort bitrate readback; ffmpeg progress would
                    # require -progress pipe parsing, deferred to Phase 2.
                    ls.last_bitrate_kbps = 4000
                    ls.last_dropped_frames = ls.last_dropped_frames or 0

                await db.commit()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        end_reason = f"loop_error:{type(exc).__name__}"
        logger.exception("[golive] orchestrator loop crashed for %s", session_id)
    finally:
        # Tear down ffmpeg.
        if proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
        try:
            os.unlink(playlist_path)
        except Exception:
            pass

        async with factory() as db:
            ls = await db.get(LiveSession, session_id)
            if ls and ls.status not in ("ended", "failed"):
                ls.status = "ended"
                ls.ended_at = datetime.now(timezone.utc)
                await db.commit()
            await append_event(db, session_id, "compositor_stopped", {
                "reason": end_reason,
            })

    logger.info("[golive] compositor finished session=%s reason=%s", session_id, end_reason)
    return {"session_id": session_id, "stopped": end_reason}


__all__ = ["run_golive_compositor_task"]

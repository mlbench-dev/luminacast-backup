"""
FFmpeg Stream Worker — Celery task managing RTMP streaming subprocess.
Implements the StreamTask pattern with zombie process prevention.
"""
import os
import signal
import subprocess
import asyncio
import math
import logging
import json
from datetime import datetime, timezone
from celery import Task
from tasks import celery_app
from services import sentry

logger = logging.getLogger(__name__)


class StreamTask(Task):
    """Celery Task base that manages FFmpeg lifecycle and prevents zombies."""

    _ffmpeg_process = None

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        self._kill_ffmpeg()
        sentry.capture_exception(exc)

    def on_revoke(self, task_id, args, kwargs, **kw):
        self._kill_ffmpeg()

    def _kill_ffmpeg(self):
        if self._ffmpeg_process and self._ffmpeg_process.returncode is None:
            try:
                os.killpg(os.getpgid(self._ffmpeg_process.pid), signal.SIGTERM)
                self._ffmpeg_process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self._ffmpeg_process.pid), signal.SIGKILL)
                    self._ffmpeg_process.wait(timeout=2)
                except (ProcessLookupError, OSError):
                    pass
            except Exception:
                pass


@celery_app.task(base=StreamTask, bind=True, max_retries=5, name="tasks.stream_worker.run_stream")
def run_stream(self, session_id: str, cast_id: str, rtmp_url: str, mode: str = "sequential"):
    """
    Main streaming task. Manages FFmpeg subprocess and clip sequencing.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_stream_async(self, session_id, cast_id, rtmp_url, mode))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        self._kill_ffmpeg()
        raise
    except Exception as exc:
        self._kill_ffmpeg()
        sentry.capture_exception(exc)
        logger.error(json.dumps({
            "service": "stream_worker",
            "level": "error",
            "message": f"Stream task failed: {exc}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
        }))
        raise self.retry(exc=exc, countdown=5)
    finally:
        self._kill_ffmpeg()
        loop.run_until_complete(_finalize_session(session_id))
        loop.close()


async def _run_stream_async(task, session_id: str, cast_id: str, rtmp_url: str, mode: str):
    """Async streaming loop."""
    from database import async_session_factory
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from models.cast import Cast
    from models.block import Block
    from models.stream_session import StreamSession
    from models.variant import Variant
    from engine.mashup import MashupEngine, BlockInfo, VariantInfo
    from engine.compositor import build_filter_graph, build_stream_output_args
    from services.r2_storage import get_r2_storage_service
    from websocket.manager import ws_manager

    cache_dir = f"/tmp/stream_cache/{session_id}"
    os.makedirs(cache_dir, exist_ok=True)

    async with async_session_factory() as session:
        # Load cast with all blocks and variants
        result = await session.execute(
            select(Cast)
            .options(
                selectinload(Cast.blocks).selectinload(Block.variants),
            )
            .where(Cast.id == cast_id)
        )
        cast = result.scalar_one_or_none()
        if not cast:
            raise ValueError(f"Cast {cast_id} not found")

        # Build MashupEngine
        block_infos = []
        for block in cast.blocks:
            variants = [
                VariantInfo(
                    id=v.id,
                    block_id=block.id,
                    weight=v.weight,
                    times_played=v.times_played,
                    purchases_during=v.purchases_during,
                    performance_score=v.performance_score or 0,
                    scene_image_key=block.scene_image_key,
                )
                for v in block.variants if v.status.value == "ready"
            ]
            block_infos.append(BlockInfo(
                id=block.id,
                position=block.position,
                block_type=block.type.value,
                product_id=block.product_id,
                variants=variants,
            ))

        engine = MashupEngine(block_infos, mode=mode)

        # Streaming loop
        stream_session = await session.get(StreamSession, session_id)
        crash_count = 0

        while stream_session and not stream_session.ended_at:
            block = engine.get_next()
            if not block:
                await asyncio.sleep(5)
                continue

            variant = engine.select_variant(block)
            if not variant or not hasattr(variant, 'id'):
                await asyncio.sleep(1)
                continue

            # Download clip from R2
            # (In real implementation, download and cache clips)

            # Broadcast block transition
            await ws_manager.broadcast_to_session(session_id, {
                "type": "BLOCK_TRANSITION",
                "payload": {
                    "current_block_id": block.id,
                    "current_block_position": block.position,
                    "total_blocks": len(block_infos),
                    "block_type": block.block_type,
                    "product_to_pin": {"product_id": block.product_id} if block.product_id else None,
                }
            })

            # Update variant play count
            variant_obj = await session.get(Variant, variant.id)
            if variant_obj:
                variant_obj.times_played += 1
                await session.commit()

            # Wait for clip duration (simulated)
            duration = getattr(variant, 'duration_seconds', 30) or 30
            await asyncio.sleep(duration)

            # Refresh session to check if stopped
            await session.refresh(stream_session)


async def _finalize_session(session_id: str):
    """Finalize stream session: calculate billing, save analytics."""
    from database import async_session_factory
    from models.stream_session import StreamSession
    from models.billing_event import BillingEvent, BillingEventType
    from datetime import datetime, timezone
    import math
    import uuid

    async with async_session_factory() as session:
        stream = await session.get(StreamSession, session_id)
        if not stream:
            return

        if not stream.ended_at:
            stream.ended_at = datetime.now(timezone.utc)

        # Calculate duration and cost
        if stream.started_at and stream.ended_at:
            delta = stream.ended_at - stream.started_at
            stream.duration_minutes = delta.total_seconds() / 60

        # Streaming cost: $0.03/min, round up partial minutes
        minutes_billed = math.ceil(stream.duration_minutes)
        stream.streaming_cost_cents = minutes_billed * 3  # 3 cents per minute

        # Create billing event
        billing = BillingEvent(
            id=f"bill_{uuid.uuid4().hex[:12]}",
            user_id=stream.user_id,
            type=BillingEventType.STREAMING_USAGE,
            amount_cents=stream.streaming_cost_cents,
            related_id=session_id,
        )
        session.add(billing)
        await session.commit()

        # Subscription/PAYG metering (separate from the legacy BillingEvent
        # above). Runs here — not in routers/stream.py's /stop handler — so
        # it also covers a stream that ends unexpectedly (task crash/kill,
        # edge case: "a live stream ends unexpectedly") since this `finally`
        # block runs regardless of how the Celery task exited.
        try:
            from services import billing_service

            await billing_service.deduct_livestream_usage(
                session,
                user_id=stream.user_id,
                owner_id=stream.user_id,
                stream_session_id=session_id,
                duration_minutes=stream.duration_minutes or 0.0,
            )
        except Exception as bill_exc:
            sentry.capture_exception(bill_exc)
            logger.error(
                "Stream session %s: billing metering failed: %s", session_id, bill_exc
            )

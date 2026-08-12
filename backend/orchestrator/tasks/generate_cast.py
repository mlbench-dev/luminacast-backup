"""Celery tasks for Cast generation pipeline.

Cast generation is webhook-based:
1. Celery task generates TTS audio for each variant (synchronous, fast)
2. Submits InfiniteTalk jobs to RunPod with webhook callbacks
3. Returns immediately — Celery worker is freed in ~2 minutes

RunPod webhook → /api/webhooks/runpod/infinitetalk → updates variants →
checks if cast is complete → marks cast as ready/failed.
"""
import asyncio
import logging
import json
import uuid
from datetime import datetime, timezone, timedelta
from tasks import celery_app
from services import sentry
import sentry_sdk

logger = logging.getLogger(__name__)


def _make_session_factory():
    """Create a fresh async session factory per call to avoid cross-process connection sharing."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _update_cast_status(cast_id: str, **kwargs):
    """Update cast fields in DB using a fresh session."""
    from models.cast import Cast
    factory = _make_session_factory()
    async with factory() as session:
        cast = await session.get(Cast, cast_id)
        if cast:
            for key, value in kwargs.items():
                setattr(cast, key, value)
            await session.commit()


async def _mark_cast_failed(cast_id: str, error_msg: str):
    from models.cast import Cast, CastStatus
    factory = _make_session_factory()
    async with factory() as session:
        cast = await session.get(Cast, cast_id)
        if cast:
            cast.status = CastStatus.GENERATION_FAILED
            cast.generation_error = error_msg[:500]
            cast.generation_progress = 0.0
            await session.commit()


async def _populate_final_video_url(cast_id: str):
    """Populate cast.final_video_url from the first ready variant.
    Safe to call multiple times — prefers composited (final_video_key) over raw (video_key)."""
    from models.cast import Cast
    from models.variant import Variant
    from models.block import Block
    from sqlalchemy import select
    factory = _make_session_factory()
    async with factory() as session:
        cast = await session.get(Cast, cast_id)
        if not cast:
            return
        all_variants = (await session.execute(
            select(Variant).join(Block).where(Block.cast_id == cast_id)
        )).scalars().all()
        best_key = None
        composited_key = None
        for v in all_variants:
            status_str = (v.status.value if hasattr(v.status, 'value') else str(v.status)).upper()
            if status_str != "READY":
                continue
            if getattr(v, 'final_video_key', None) and not composited_key:
                composited_key = v.final_video_key
            if getattr(v, 'video_key', None) and not best_key:
                best_key = v.video_key
        chosen = composited_key or best_key
        if chosen:
            cast.final_video_url = f"https://media.luminacast.com/{chosen}"
            await session.commit()
            logger.info("Populated final_video_url for cast %s: %s", cast_id, chosen)
        else:
            logger.warning("No ready variant with video key found for cast %s", cast_id)


@celery_app.task(bind=True, max_retries=2, name="tasks.generate_cast.generate")
def generate_cast_task(self, cast_id: str, user_id: str):
    """
    Celery task that runs the Cast generation pipeline (webhook-based).

    This task:
    1. Generates TTS audio for each variant (synchronous, fast)
    2. Submits InfiniteTalk jobs to RunPod with webhook callbacks
    3. Returns immediately — does NOT block for video rendering

    RunPod webhook → /api/webhooks/runpod/infinitetalk → updates variants →
    checks if cast is complete → marks cast as ready/failed.
    """
    sentry_sdk.set_tag("cast_id", cast_id)
    sentry_sdk.set_tag("user_id", user_id)
    sentry_sdk.set_context("cast", {"cast_id": cast_id, "user_id": user_id, "pipeline": "generate"})
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_generate_cast_async(self, cast_id, user_id))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(json.dumps({
            "service": "generate_cast",
            "level": "error",
            "message": f"Cast generation failed: {exc}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cast_id": cast_id,
            "creator_id": user_id,
        }))
        # Mark failed using a fresh loop since current one may be tainted
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_cast_failed(cast_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=30)
    finally:
        loop.close()


async def _generate_cast_async(task, cast_id: str, user_id: str):
    """Async implementation of Cast generation — webhook-based, non-blocking."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from models.cast import Cast, CastStatus
    from models.block import Block
    from models.variant import Variant, VariantStatus
    from engine.cast_generator import generate_cast_clips
    from websocket.manager import ws_manager

    factory = _make_session_factory()

    async with factory() as session:
        # Load cast with blocks and variants
        result = await session.execute(
            select(Cast)
            .options(
                selectinload(Cast.blocks).selectinload(Block.variants),
                selectinload(Cast.blocks).selectinload(Block.avatar_look),
                selectinload(Cast.avatar),
            )
            .where(Cast.id == cast_id)
        )
        cast = result.scalar_one_or_none()

        if not cast:
            logger.error(f"Cast {cast_id} not found")
            return

        if not cast.avatar or not cast.avatar.voice_id:
            cast.status = CastStatus.GENERATION_FAILED
            cast.generation_error = "Avatar not ready or missing voice"
            await session.commit()
            return

        avatar_voice_id = cast.avatar.voice_id
        avatar_face_ref_key = cast.avatar.face_ref_key or ""
        avatar_clip_mic_enabled = bool(getattr(cast.avatar, "clip_mic_enabled", False))
        blocks = [b for b in cast.blocks if getattr(b, 'is_active', True) and b.deleted_at is None]
        cast_quality = cast.quality.value if cast.quality else "simple"
        cast_effects_config = cast.effects_config or {}

        # Create default variants for blocks that have none — use key_points only, never placeholder text
        created_variants = False
        for block in blocks:
            if not block.variants:
                script = ""
                if block.key_points:
                    points = block.key_points if isinstance(block.key_points, list) else []
                    script = ". ".join(points)
                if not script:
                    logger.warning("Block %s (type=%s) has no variants and no key_points — skipping (no placeholder)", block.id, block.type.value)
                    continue
                variant = Variant(
                    id=f"var_{uuid.uuid4().hex[:12]}",
                    block_id=block.id,
                    script_text=script,
                    variant_label="A",
                    status=VariantStatus.PENDING,
                )
                session.add(variant)
                block.variants.append(variant)
                created_variants = True
                logger.info("Created default variant for block %s (type=%s) from key_points", block.id, block.type.value)

        cast.status = CastStatus.GENERATING
        cast.generation_progress = 0.0
        cast.progress_step = "Generating speech audio..."
        if created_variants:
            await session.flush()
        await session.commit()

    # Progress callback uses a fresh session each time to avoid stale connections
    async def on_progress(progress: float, step: str):
        await _update_cast_status(cast_id, generation_progress=progress, progress_step=step)
        await ws_manager.broadcast_to_user(user_id, {
            "type": "GENERATION_PROGRESS",
            "payload": {
                "cast_id": cast_id,
                "progress": progress,
                "current_step": step,
                "failed_count": 0,
            }
        })

    gen_result = await generate_cast_clips(
        cast_id=cast_id,
        blocks=blocks,
        avatar_voice_id=avatar_voice_id,
        progress_callback=on_progress,
        quality=cast_quality,
        avatar_face_ref_key=avatar_face_ref_key,
        user_id=user_id,
        effects_config=cast_effects_config,
        avatar_clip_mic_enabled=avatar_clip_mic_enabled,
    )

    # Update cast: TTS phase complete, now waiting for webhooks
    # Do NOT set status to READY yet — webhooks handle that
    submitted = gen_result.get("submitted_jobs", 0)
    failed = gen_result["failed_count"]

    already_done = sum(1 for b in blocks for v in getattr(b, 'variants', []) if getattr(v, 'video_key', None))
    total = submitted + already_done

    if total == 0:
        await _update_cast_status(
            cast_id,
            status=CastStatus.GENERATION_FAILED,
            generation_error="No variants to render",
            generation_progress=0.0,
        )
    elif submitted == 0 and already_done > 0:
        await _update_cast_status(
            cast_id,
            status=CastStatus.READY,
            generation_progress=1.0,
            progress_step=f"All {already_done} clips already rendered",
        )
        await _populate_final_video_url(cast_id)
        logger.info(f"Cast {cast_id}: already complete, {already_done} variants ready")
    else:
        await _update_cast_status(
            cast_id,
            status=CastStatus.GENERATING_VIDEOS,
            generation_progress=0.5,
            progress_step=f"All audio generated. {submitted} video jobs rendering on GPU...",
        )

    # Persist variant statuses from the TTS/submit phase
    async with factory() as session:
        for block in blocks:
            for variant in getattr(block, 'variants', []):
                await session.merge(variant)
        await session.commit()

    # Notify frontend of TTS completion
    await ws_manager.broadcast_to_user(user_id, {
        "type": "GENERATION_PROGRESS",
        "payload": {
            "cast_id": cast_id,
            "progress": 0.5,
            "current_step": f"Audio complete. {submitted} video clips rendering on GPU...",
            "failed_count": failed,
        }
    })

    logger.info(json.dumps({
        "service": "generate_cast",
        "level": "info",
        "message": f"Cast {cast_id} TTS phase complete. {submitted} webhook jobs submitted. Task returning.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cast_id": cast_id,
        "submitted_jobs": submitted,
        "failed_count": failed,
    }))


@celery_app.task(name="cleanup_stale_rendering_jobs")
def cleanup_stale_jobs():
    """Find variants stuck in 'generating' for > 60 minutes.

    This catches edge cases where RunPod's webhook fails to deliver
    (network blip, our server was down, etc).

    For each stuck variant, poll RunPod directly to check the real status.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_cleanup_stale_async())
    except Exception as exc:
        logger.error(f"Stale job cleanup failed: {exc}")
        sentry.capture_exception(exc)
    finally:
        loop.close()


async def _cleanup_stale_async():
    from sqlalchemy import select
    from models.variant import Variant, VariantStatus
    from models.block import Block
    from models.cast import Cast, CastStatus
    from services.runpod import get_runpod_service
    from config import settings

    factory = _make_session_factory()
    cutoff = datetime.utcnow() - timedelta(minutes=60)  # naive UTC to match DB column

    async with factory() as db:
        stuck_variants = (await db.execute(
            select(Variant).where(
                Variant.status == VariantStatus.GENERATING,
                Variant.runpod_job_id.isnot(None),
                Variant.updated_at < cutoff,
            )
        )).scalars().all()

        if not stuck_variants:
            return

        logger.warning(f"Found {len(stuck_variants)} stuck variants, checking RunPod status")

        runpod = get_runpod_service()
        affected_cast_ids = set()

        for variant in stuck_variants:
            try:
                result = await runpod.check_job_status(variant.runpod_job_id)
                status = result.get("status", "")

                if status == "COMPLETED":
                    # Webhook must have been lost — process the result now
                    output = result.get("output", {})
                    video_url = ""
                    if isinstance(output, dict):
                        video_url = output.get("video_url") or output.get("video_r2_key") or ""
                    if video_url:
                        video_key = video_url
                        if video_key.startswith("http"):
                            video_key = (
                                video_key
                                .replace(settings.R2_PUBLIC_URL + "/", "")
                                .replace("https://media.luminacast.com/", "")
                            )
                        variant.video_key = video_key
                        variant.status = VariantStatus.READY
                        variant.generation_error = None
                        logger.info(f"Recovered stuck variant {variant.id} — was COMPLETED")
                    else:
                        variant.status = VariantStatus.FAILED
                        variant.generation_error = "Completed but no video URL (recovered from stale)"
                elif status == "FAILED":
                    variant.status = VariantStatus.FAILED
                    error = result.get("error", "")
                    if not error and isinstance(output, dict):
                        error = output.get("error") or output.get("message") or ""
                    variant.generation_error = (error or "Job failed (recovered from stale)")[:500]
                    logger.info(f"Recovered stuck variant {variant.id} — was FAILED")
                elif status in ("IN_QUEUE", "IN_PROGRESS"):
                    # Still running — don't kill it, just log
                    logger.info(f"Variant {variant.id} still {status} after 60min — letting it continue")
                    continue
                else:
                    # Unknown status after 60 min — mark as failed
                    variant.status = VariantStatus.FAILED
                    variant.generation_error = f"Job stuck in '{status}' for 60+ minutes"

                # Track the cast for completion check
                block = await db.get(Block, variant.block_id)
                if block:
                    affected_cast_ids.add(block.cast_id)

            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Failed to check stuck variant {variant.id}: {e}")

        await db.commit()

        # Check cast completion for any updated variants
        from routers.webhooks import _check_cast_completion
        for cast_id in affected_cast_ids:
            try:
                cast = await db.get(Cast, cast_id)
                if cast and cast.status == CastStatus.GENERATING:
                    await _check_cast_completion(db, cast)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Failed to check cast completion for {cast_id}: {e}")


@celery_app.task(name="tasks.generate_cast.refresh_intelligence")
def refresh_intelligence():
    """Hourly task to refresh platform-wide intelligence metrics."""
    logger.info(json.dumps({
        "service": "generate_cast",
        "level": "info",
        "message": "Refreshing platform intelligence",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))
    # Neo4j intelligence refresh would go here


@celery_app.task(bind=True, max_retries=2, name="tasks.generate_cast.generate_tts")
def generate_cast_tts_task(self, cast_id: str, user_id: str):
    """Phase 1: Generate TTS audio only. Sets cast status to TTS_READY when done."""
    sentry_sdk.set_tag("cast_id", cast_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_generate_tts_only(cast_id, user_id))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(json.dumps({
            "service": "generate_cast_tts",
            "level": "error",
            "message": f"TTS generation failed: {exc}",
            "cast_id": cast_id,
        }))
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_cast_failed(cast_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=30)
    finally:
        loop.close()


async def _generate_tts_only(cast_id: str, user_id: str):
    """Generate TTS for all active blocks, then set status to TTS_READY."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from models.cast import Cast, CastStatus
    from models.block import Block
    from models.variant import Variant, VariantStatus
    from services.fish_audio import get_fish_audio_service
    from services.r2_storage import get_r2_storage_service
    from services import sentry as sentry_mod
    from websocket.manager import ws_manager

    factory = _make_session_factory()

    async with factory() as session:
        result = await session.execute(
            select(Cast)
            .options(
                selectinload(Cast.blocks).selectinload(Block.variants),
                selectinload(Cast.blocks).selectinload(Block.avatar_look),
                selectinload(Cast.avatar),
            )
            .where(Cast.id == cast_id)
        )
        cast = result.scalar_one_or_none()
        if not cast or not cast.avatar:
            logger.error("TTS aborted: cast %s not found or has no avatar", cast_id)
            return

        avatar_voice_id = cast.avatar.voice_id
        avatar_clip_mic_enabled = bool(getattr(cast.avatar, "clip_mic_enabled", False))
        if not avatar_voice_id:
            error_msg = "Avatar has no voice configured. Please set up a voice first."
            logger.error("TTS aborted for cast %s: avatar %s has no voice_id", cast_id, cast.avatar.id)
            sentry_sdk.capture_message(f"TTS aborted: avatar {cast.avatar.id} missing voice_id", level="error")
            cast.status = CastStatus.GENERATION_FAILED
            cast.generation_error = error_msg
            await session.commit()
            await ws_manager.broadcast_to_user(user_id, {
                "type": "GENERATION_ERROR",
                "payload": {"cast_id": cast_id, "error": error_msg},
            })
            return

        blocks = [b for b in cast.blocks if getattr(b, 'is_active', True) and b.deleted_at is None]
        r2 = get_r2_storage_service()
        fish = get_fish_audio_service()

        # Create default variants for blocks that have none — use key_points only, never placeholder text
        created_variants = False
        for block in blocks:
            if not block.variants:
                script = ""
                if block.key_points:
                    points = block.key_points if isinstance(block.key_points, list) else []
                    script = ". ".join(points)
                if not script:
                    logger.warning("Block %s (type=%s) has no variants and no key_points — skipping (no placeholder)", block.id, block.type.value)
                    continue
                variant = Variant(
                    id=f"var_{uuid.uuid4().hex[:12]}",
                    block_id=block.id,
                    script_text=script,
                    variant_label="A",
                    status=VariantStatus.PENDING,
                )
                session.add(variant)
                block.variants.append(variant)
                created_variants = True
                logger.info("Created default variant for block %s (type=%s) from key_points", block.id, block.type.value)

        # Multi-stage progress weights — TTS dominates, alignment/finalize
        # are quick. Pre-fixed bug: progress only ticked at variant
        # completion, so the UI reported 0/1% for ~30-60s while TTS jobs
        # warmed up cold-start GPUs. We now bump progress on every stage
        # transition AND on every block completion.
        STAGE_TTS_WEIGHT = 0.70
        STAGE_ALIGN_WEIGHT = 0.10
        STAGE_FINALIZE_WEIGHT = 0.20
        STAGE_TTS_FLOOR = 0.02  # 2% — show movement immediately on start

        cast.status = CastStatus.GENERATING_TTS
        cast.generation_progress = STAGE_TTS_FLOOR
        cast.progress_step = "Generating AI voice..."
        # Track stage start time for ETA calculations.
        from datetime import datetime as _dt, timezone as _tz
        _tts_started_at = _dt.now(_tz.utc)
        # Reset ALL variants so edited scripts get fresh TTS audio.
        # Previously only FAILED variants were reset, which caused stale
        # audio when users edited script_text and re-ran TTS generation —
        # READY variants with old audio_key were skipped (see line ~549).
        for blk in blocks:
            for v in getattr(blk, 'variants', []):
                v.status = VariantStatus.PENDING
                v.audio_key = None
                v.tts_duration_seconds = None
                v.generation_error = None
        await session.flush()
        await session.commit()

        total_variants = sum(
            1 for b in blocks
            for v in (getattr(b, 'variants', []) or [])
            if getattr(v, 'is_active', True)
        )
        if total_variants == 0:
            error_msg = "No script blocks found. Generate a script before generating audio."
            logger.error("TTS aborted for cast %s: no variants to generate", cast_id)
            sentry_sdk.capture_message(f"TTS aborted: cast {cast_id} has 0 variants", level="error")
            cast.status = CastStatus.GENERATION_FAILED
            cast.generation_error = error_msg
            await session.commit()
            await ws_manager.broadcast_to_user(user_id, {
                "type": "GENERATION_ERROR",
                "payload": {"cast_id": cast_id, "error": error_msg},
            })
            return

        completed = 0
        logger.info(json.dumps({
            "service": "generate_cast_tts",
            "level": "info",
            "message": "TTS generation started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cast_id": cast_id,
            "user_id": user_id,
            "total_variants": total_variants,
            "block_count": len(blocks),
        }))

        for block in blocks:
            # Voice chain precedence: per-block mic_on override > the
            # scene (AvatarLook) this block uses > avatar-wide default.
            # The scene's own environment/mic_visible (set when the scene
            # was created — see routers/avatar_looks.py) picks a chain from
            # SCENE_FILTER_LIBRARY instead of the flat clip_mic/phone_mic
            # split, so e.g. an outdoor scene gets windscreen-shaped EQ.
            from services.mic_presets import resolve_scene_voice_settings
            _look = getattr(block, "avatar_look", None)
            block_clip_mic_enabled, scene_chain_id = resolve_scene_voice_settings(
                block_mic_on=getattr(block, "mic_on", None),
                avatar_clip_mic_enabled=avatar_clip_mic_enabled,
                look_environment=getattr(_look, "environment", None),
                look_mic_visible=getattr(_look, "mic_visible", None),
            )
            logger.info(
                "voice mode=%s scene_chain=%s block=%s",
                "clip_mic" if block_clip_mic_enabled else "phone_mic",
                scene_chain_id, block.id,
            )

            for variant in getattr(block, 'variants', []):
                if variant.status == VariantStatus.READY and variant.audio_key:
                    completed += 1
                    continue

                # Guard: skip variants with empty script_text
                script_text = (variant.script_text or "").strip()
                if not script_text:
                    # avatar_action/body_motion blocks are driven by
                    # body_motion_prompt (what the avatar visually does),
                    # not script_text — roughly half of them legitimately
                    # have no narration at all (silent action cutaways).
                    # Only hard-fail here for categories that are actually
                    # supposed to speak; a missing script on those is a
                    # real script-generation bug, not a valid silent state.
                    if block.render_mode == "body_motion" or block.category == "avatar_action":
                        fallback_dur = float(getattr(variant, "estimated_duration_seconds", 0) or 5.0)
                        variant.status = VariantStatus.READY
                        variant.duration_seconds = fallback_dur
                        variant.tts_duration_seconds = fallback_dur
                        variant.audio_key = None
                        logger.info(
                            "Variant %s in block %s (avatar_action/body_motion) has no "
                            "script — treating as a silent action block, using "
                            "estimated_duration_seconds=%.2fs for slot timing",
                            variant.id, block.id, fallback_dur,
                        )
                        completed += 1
                        continue
                    logger.warning(
                        "Variant %s in block %s has empty script_text, skipping TTS",
                        variant.id, block.id,
                    )
                    variant.status = VariantStatus.FAILED
                    variant.generation_error = "No script text. Edit the script before generating audio."
                    completed += 1
                    continue

                # Try TTS up to 2 times before marking the variant FAILED.
                # The first attempt uses the legacy fish.generate_tts(), which
                # has its own HOSTKEY/RunPod → Fish Audio API cascade. If both
                # tries fail we fall back to the explicit cloud-fallback chain
                # (Fish Audio Cloud → ElevenLabs) so a HOSTKEY outage no longer
                # forces "TTS failed" to the user. Every failure inside the
                # chain is captured to Sentry with provider/tier tags.
                from services.provider_chain import try_chain, AllProvidersFailedError
                from services.render_providers import (
                    FishAudioProvider,
                    FishAudioCloudProvider,
                    ElevenLabsTTSProvider,
                )

                tts_result = None
                last_err: Exception | None = None
                for attempt in (1, 2):
                    try:
                        tts_result = await fish.generate_tts(
                            text=script_text,
                            voice_id=avatar_voice_id,
                            clip_mic_enabled=block_clip_mic_enabled,
                            scene_chain_id=scene_chain_id,
                            block_id=block.id,
                        )
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        sentry_sdk.capture_exception(e)
                        logger.warning(
                            "TTS attempt %d/2 failed for variant %s: %r",
                            attempt, variant.id, e,
                        )
                        if attempt == 1:
                            # Brief backoff to let RunPod / Fish Audio recover
                            await asyncio.sleep(3.0)

                if tts_result is None:
                    # Final fallback: walk the explicit provider chain. The
                    # tier-1 provider (FishAudioProvider) wraps the
                    # same fish.generate_tts call, so we mainly rely on the
                    # cloud tiers here — but keeping it in the chain makes
                    # the recovery path uniform with SPEAKING and easy to
                    # introspect.
                    try:
                        chain_result = await try_chain(
                            [
                                FishAudioProvider(),
                                FishAudioCloudProvider(),
                                ElevenLabsTTSProvider(),
                            ],
                            step_label="tts",
                            render_id=cast_id,
                            block_id=variant.id,
                            text=script_text,
                            voice_id=avatar_voice_id,
                            clip_mic_enabled=block_clip_mic_enabled,
                            scene_chain_id=scene_chain_id,
                        )
                        tts_result = {
                            "audio_key": chain_result.get("audio_key"),
                            "duration_seconds": chain_result.get("duration_seconds"),
                            "tmp_path": chain_result.get("tmp_path"),
                            "lipsync_audio_key": chain_result.get("lipsync_audio_key"),
                        }
                        last_err = None
                        logger.info(
                            "Variant %s TTS recovered via provider chain (%s, tier %s)",
                            variant.id,
                            chain_result.get("_provider_used"),
                            chain_result.get("_tier_used"),
                        )
                    except AllProvidersFailedError as chain_exc:
                        sentry_sdk.capture_exception(chain_exc)
                        last_err = last_err or chain_exc

                if last_err is not None or tts_result is None:
                    variant.status = VariantStatus.FAILED
                    err_str = (
                        f"{type(last_err).__name__}: {last_err}"
                        if last_err
                        else "TTS returned no result"
                    )
                    variant.generation_error = err_str[:500]
                    logger.error("TTS failed for variant %s: %s", variant.id, err_str)
                else:
                    try:
                        audio_key = tts_result.get("audio_key", "")
                        duration = tts_result.get("duration_seconds", 0)
                        tmp_path = tts_result.get("tmp_path", "")

                        try:
                            from database import async_session_factory
                            from services.usage_tracker import log_usage
                            async with async_session_factory() as _usg:
                                await log_usage(
                                    _usg,
                                    user_id=user_id,
                                    event_type="tts_generation",
                                    # HOSTKEY decommissioned (PR #94); TTS now
                                    # runs through the FishAudio cloud cascade.
                                    # Label as cloud_tts so it no longer lands
                                    # in the dead 'hostkey' provider bucket.
                                    provider="cloud_tts",
                                    provider_cost_usd=0.0,
                                    quantity=float(duration or 0.0),
                                    quantity_unit="audio_seconds",
                                    resource_type="cast",
                                    resource_id=cast_id,
                                    block_id=block.id,
                                    duration_seconds=float(duration or 0.0),
                                )
                                await _usg.commit()
                        except Exception as _exc:
                            sentry_sdk.capture_exception(_exc)

                        if audio_key and tmp_path:
                            import os
                            if os.path.exists(tmp_path) and not await r2.key_exists(audio_key):
                                await r2.upload_file(tmp_path, audio_key, content_type="audio/mpeg")

                        if not audio_key:
                            src_path = tts_result.get("audio_path") or tmp_path
                            if src_path:
                                audio_key = f"creators/{user_id}/casts/{cast_id}/tts/{variant.id}.mp3"
                                await r2.upload_file(src_path, audio_key, content_type="audio/mpeg")

                        variant.audio_key = audio_key
                        variant.tts_r2_key = audio_key
                        # PR #65: persist the post-processed 16 kHz WAV
                        # lipsync feed when present so cast_render can hand
                        # it to InfiniteTalk / MuseTalk / Kling.
                        lipsync_key = tts_result.get("lipsync_audio_key") or ""
                        if lipsync_key:
                            variant.tts_lipsync_r2_key = lipsync_key
                        variant.tts_duration_seconds = duration
                        variant.duration_seconds = duration

                        # Capture [sfx:NAME] markers from the ORIGINAL script
                        # before any stripping (TTS itself strips them). We need
                        # their position relative to the spoken words to time the
                        # effect later; alignment happens once caption_words land.
                        try:
                            from utils.sfx_extraction import extract_sfx_markers
                            markers = extract_sfx_markers(variant.script_text or "")
                            variant.sfx_markers = [
                                {"name": m.name, "char_offset": m.char_offset, "word_index": m.word_index}
                                for m in markers
                            ] or None
                        except Exception as e:
                            sentry_sdk.capture_exception(e)

                        # TTS-aware re-budget: warn if the actual audio
                        # exceeds the variant's planned duration by >10%.
                        # Lets us spot voices/scripts that systematically
                        # overshoot. Non-fatal — we still ship the audio.
                        try:
                            target_block_secs = float(
                                getattr(variant, "estimated_duration_seconds", 0) or 0
                            )
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            target_block_secs = 0.0
                        if target_block_secs > 0 and duration:
                            ratio = float(duration) / target_block_secs
                            if ratio > 1.10:
                                msg = (
                                    f"TTS overshoot: block {block.id} actual "
                                    f"{duration:.1f}s vs target {target_block_secs:.1f}s "
                                    f"(ratio={ratio:.2f})"
                                )
                                logger.warning(msg)
                                try:
                                    sentry_sdk.capture_message(msg, level="warning")
                                except Exception:  # noqa: deliberate fallback — guards the Sentry call itself; capturing here would recurse
                                    pass

                        # Duration guard — block anything over 18s from reaching InfiniteTalk.
                        # Matches the guard in engine/cast_generator.py (Phase 0 / B-123 fix).
                        MAX_TTS_SECONDS = 18.0
                        if duration and duration > MAX_TTS_SECONDS:
                            logger.warning(
                                "Block %s variant %s exceeded duration cap: %.1fs > %.1fs. Script: %r",
                                variant.block_id, variant.id,
                                duration, MAX_TTS_SECONDS,
                                (variant.script_text or "")[:120],
                            )
                            variant.status = VariantStatus.FAILED
                            variant.generation_error = (
                                f"Script generated {duration:.1f}s of audio but 15s cap is enforced. "
                                f"Rewrite manually or regenerate."
                            )
                        else:
                            variant.status = VariantStatus.DRAFT  # TTS done, waiting for video gen
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        variant.status = VariantStatus.FAILED
                        variant.generation_error = (
                            f"{type(e).__name__}: {e}"
                        )[:500]
                        logger.error(
                            "TTS post-processing failed for variant %s: %r",
                            variant.id, e,
                        )

                completed += 1
                # Aggregate progress: TTS stage spans [STAGE_TTS_FLOOR, STAGE_TTS_WEIGHT].
                # We report fractional progress within that window so a 4-block
                # cast at 1/4 done shows ~19% rather than 25% (saving room for
                # the remaining stages — caption alignment + finalize).
                tts_frac = completed / max(total_variants, 1)
                progress = STAGE_TTS_FLOOR + tts_frac * (STAGE_TTS_WEIGHT - STAGE_TTS_FLOOR)
                cast.generation_progress = progress

                # ETA: only compute once we have a meaningful sample. Below
                # 5% the elapsed-vs-progress ratio is too noisy.
                eta_str = ""
                elapsed_s = (_dt.now(_tz.utc) - _tts_started_at).total_seconds()
                if progress > 0.05 and elapsed_s > 1.0:
                    eta_total = elapsed_s / progress
                    eta_remaining = max(0, int(round(eta_total - elapsed_s)))
                    eta_str = f" — about {eta_remaining}s left" if eta_remaining > 1 else ""

                cast.progress_step = (
                    f"Generating AI voice {completed}/{total_variants}{eta_str}"
                )
                await session.commit()

                await ws_manager.broadcast_to_user(user_id, {
                    "type": "GENERATION_PROGRESS",
                    "payload": {
                        "cast_id": cast_id,
                        "progress": progress,
                        "current_step": cast.progress_step,
                        "stage": "tts",
                        "stage_progress": tts_frac,
                        "eta_seconds": (
                            int(round((elapsed_s / progress) - elapsed_s))
                            if progress > 0.05 and elapsed_s > 1.0
                            else None
                        ),
                    },
                })

        # Auto-generate word-level captions via Whisper (non-fatal).
        #
        # Previously this loop ran WhisperX serially for every variant and only
        # committed once at the very end — so if the celery worker was killed
        # part-way (long audio + cold-start GPU = minutes per call) the partial
        # results for blocks already processed were lost too. Users reported
        # captions only appearing for block 1; the rest had empty caption_words
        # and the editor fell back to an even-distribution placeholder.
        #
        # Now: dispatch all per-variant transcribes concurrently with
        # asyncio.gather (return_exceptions=True so one failure doesn't poison
        # the rest), then write results and commit. Per-variant fallback still
        # populates caption_words from script timing if the GPU call failed.
        # Alignment stage — bump progress so the bar visibly moves into the
        # 70-80% region while WhisperX runs.
        cast.generation_progress = STAGE_TTS_WEIGHT  # 0.70
        cast.progress_step = "Aligning captions..."
        await session.commit()
        try:
            await ws_manager.broadcast_to_user(user_id, {
                "type": "GENERATION_PROGRESS",
                "payload": {
                    "cast_id": cast_id,
                    "progress": STAGE_TTS_WEIGHT,
                    "current_step": cast.progress_step,
                    "stage": "align",
                    "stage_progress": 0.0,
                },
            })
        except Exception as e:
            sentry_sdk.capture_exception(e)
        try:
            # Transcription cascade: HOSTKEY WhisperX → fal.ai Whisper. Each
            # variant gets its own try_chain call so a single tier-1 outage
            # transparently routes everyone to the cloud. The fal-ai/whisper
            # provider normalises the response to the same {segments, words}
            # shape so downstream code stays unchanged.
            from services.provider_chain import try_chain, AllProvidersFailedError
            from services.render_providers import (
                HostkeyWhisperxProvider,
                FalWhisperProvider,
            )

            pending: list[tuple[object, str]] = []
            for block in blocks:
                for variant in getattr(block, 'variants', []):
                    if not variant.audio_key:
                        continue
                    pending.append((variant, r2.get_public_url(variant.audio_key)))

            async def _transcribe_one(audio_url: str, variant_id: str):
                return await try_chain(
                    [HostkeyWhisperxProvider(), FalWhisperProvider()],
                    step_label="transcription",
                    render_id=cast_id,
                    block_id=variant_id,
                    audio_url=audio_url,
                    language="en",
                    word_timestamps=True,
                )

            if pending:
                results = await asyncio.gather(
                    *(_transcribe_one(audio_url, v.id) for v, audio_url in pending),
                    return_exceptions=True,
                )
                for (variant, _), result in zip(pending, results):
                    if isinstance(result, Exception):
                        sentry_sdk.capture_exception(result)
                        logger.warning(
                            "Caption alignment failed for variant %s: %s",
                            variant.id, result,
                        )
                        if variant.script_text and variant.tts_duration_seconds:
                            from utils.script_cleaning import clean_script_tokens, strip_script_markers
                            # Strip [sfx:*]/(emotion) direction markers before
                            # tokenizing, or they leak into rendered captions.
                            words = clean_script_tokens(variant.script_text)
                            clean_text = strip_script_markers(variant.script_text)
                            dur = variant.tts_duration_seconds
                            tpw = dur / max(len(words), 1)
                            variant.caption_words = [
                                {"word": w, "start": round(i * tpw, 3), "end": round((i + 1) * tpw, 3), "probability": 0.5}
                                for i, w in enumerate(words)
                            ]
                            variant.caption_segments = [{"start": 0, "end": dur, "text": clean_text}]
                    else:
                        variant.caption_words = result.get("words", [])
                        variant.caption_segments = result.get("segments", [])

                # Resolve SFX markers to absolute timings now that word-level
                # timestamps exist. Each marker is anchored to the spoken word it
                # precedes; the even-distribution fallback covers the no-words
                # path. Non-fatal — a failure here must never block the render.
                for variant, _ in pending:
                    if not getattr(variant, "sfx_markers", None):
                        continue
                    try:
                        from utils.sfx_extraction import SfxMarker, align_sfx_to_words
                        markers = [
                            SfxMarker(name=m["name"], char_offset=m["char_offset"], word_index=m["word_index"])
                            for m in variant.sfx_markers
                        ]
                        variant.sfx_timings = align_sfx_to_words(
                            markers,
                            variant.caption_words,
                            tts_duration_seconds=variant.tts_duration_seconds,
                        ) or None
                    except Exception as e:
                        sentry_sdk.capture_exception(e)

                await session.commit()
                logger.info(
                    "Caption alignment completed for %d variant(s) across %d block(s)",
                    len(pending), len(blocks),
                )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("Caption generation step failed (non-fatal): %s", e)

        # Bump to 80% (TTS + alignment done) before the final tally pass.
        cast.generation_progress = STAGE_TTS_WEIGHT + STAGE_ALIGN_WEIGHT  # 0.80
        cast.progress_step = "Finalizing audio..."
        await session.commit()
        try:
            await ws_manager.broadcast_to_user(user_id, {
                "type": "GENERATION_PROGRESS",
                "payload": {
                    "cast_id": cast_id,
                    "progress": cast.generation_progress,
                    "current_step": cast.progress_step,
                    "stage": "finalize",
                    "stage_progress": 0.0,
                },
            })
        except Exception as e:
            sentry_sdk.capture_exception(e)

        # Count successful vs failed variants
        success_count = sum(
            1 for b in blocks for v in getattr(b, 'variants', [])
            if v.audio_key
        )
        failed_count = sum(
            1 for b in blocks for v in getattr(b, 'variants', [])
            if str(v.status) in ("failed", "VariantStatus.FAILED")
        )

        cast.status = CastStatus.TTS_READY
        cast.generation_progress = 1.0
        cast.progress_step = "Audio complete. Review and time overlays."
        await session.commit()

        logger.info(json.dumps({
            "service": "generate_cast_tts",
            "level": "info",
            "message": "TTS generation completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cast_id": cast_id,
            "user_id": user_id,
            "total_variants": total_variants,
            "success_count": success_count,
            "failed_count": failed_count,
        }))

    await ws_manager.broadcast_to_user(user_id, {
        "type": "TTS_READY",
        "payload": {"cast_id": cast_id},
    })


@celery_app.task(bind=True, max_retries=2, name="tasks.generate_cast.generate_videos", time_limit=5400, soft_time_limit=5100)
def generate_cast_videos_task(self, cast_id: str, user_id: str):
    """Phase 2: Submit InfiniteTalk video jobs for all TTS-ready variants."""
    sentry_sdk.set_tag("cast_id", cast_id)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_generate_videos_only(cast_id, user_id))
    except Exception as exc:
        sentry.capture_exception(exc)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_cast_failed(cast_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=30)
    finally:
        loop.close()



async def _get_body_shot_key(session, avatar_id: str, angle: str) -> str | None:
    """Return the R2 key of a body shot for the given avatar and angle.

    Looks up the latest BodyShotSet and returns the key from its angles dict.
    Falls back to front_shot_key for the "front" angle.
    Returns None if no matching body shot exists.
    """
    from sqlalchemy import select
    from models.avatar import BodyShotSet

    result = await session.execute(
        select(BodyShotSet)
        .where(BodyShotSet.avatar_id == avatar_id)
        .order_by(BodyShotSet.created_at.desc())
        .limit(1)
    )
    bss = result.scalar_one_or_none()
    if not bss:
        return None

    # Check angles JSON dict first
    if bss.angles and angle in bss.angles:
        return bss.angles[angle]

    # For "front", fall back to front_shot_key
    if angle == "front" and bss.front_shot_key:
        return bss.front_shot_key

    return None


async def _resolve_face_image_for_block(cast, block, session) -> str:
    """Return the R2 key of the face image to use for InfiniteTalk for this block.
    Priority:
    0. block.avatar_angle (non-front) -> body shot for that angle
    1. block.avatar_look_id -> look.face_ref_key
    2. The default look of the cast avatar
    3. The cast.avatar.face_ref_key (legacy fallback)

    Step 8: once a concrete base look is resolved, the chosen key is routed
    through ``resolve_mic_on_face_key`` so that a block with ``mic_on == True``
    renders the baked clip-on lavalier variant (lazy-generated + cached) while
    ``mic_on == False`` keeps the clean base look. The mic-on swap is a
    no-op for the body-shot-angle and legacy-avatar fallback paths, which have
    no base look id to derive a variant from.

    When the block leaves ``mic_on`` unset (``None``), the resolved look's own
    ``mic_visible`` (chosen at scene-creation time — see
    routers/avatar_looks.py) is used as the default instead of always keeping
    the clean look. This keeps the visual mic and the audio filter chain
    (``services.mic_presets.resolve_scene_voice_settings``, which already
    falls back to the same ``look.mic_visible`` column) in sync — a "mic
    visible" scene should show the mic AND sound like a clip-mic even if no
    block explicitly overrides it.
    """
    from models.avatar_look import AvatarLook, TALKING_HEAD_LOOK_TYPE, DEFAULT_FRAMING
    from services.mic_on_look import resolve_mic_on_face_key
    from sqlalchemy import select

    mic_on = getattr(block, "mic_on", None)

    def _effective_mic_on(look):
        if mic_on is not None:
            return mic_on
        return bool(getattr(look, "mic_visible", False)) if look is not None else None

    # Phase F: per-block avatar angle snapshot
    avatar_angle = getattr(block, "avatar_angle", None) or "front"
    if avatar_angle != "front" and cast.avatar_id:
        body_shot_key = await _get_body_shot_key(session, cast.avatar_id, avatar_angle)
        if body_shot_key:
            logger.info("Block %s using body shot angle=%s key=%s", block.id, avatar_angle, body_shot_key)
            return body_shot_key
        logger.warning(
            "Block %s requested angle=%s but no body shot found, falling back",
            block.id, avatar_angle,
        )

    # Round-6 Bug B round-3: for a non-MEDIUM framing prefer a ready, reusable
    # talking-head look generated for that exact framing. Only override the
    # block's pinned look when such a look exists — MEDIUM (or no framing) falls
    # through to existing behaviour so casts with no framing variety are
    # untouched.
    block_framing = (getattr(block, "framing", None) or DEFAULT_FRAMING).strip().upper()
    if cast.avatar_id and block_framing != DEFAULT_FRAMING:
        th_result = await session.execute(
            select(AvatarLook)
            .where(AvatarLook.avatar_id == cast.avatar_id)
            .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
            .where(AvatarLook.framing == block_framing)
            .where(AvatarLook.status == "ready")
            .order_by(AvatarLook.created_at.desc())
            .limit(1)
        )
        th_look = th_result.scalars().first()
        if th_look and th_look.face_ref_key:
            logger.info(
                "Block %s using talking-head look %s framing=%s",
                block.id, th_look.id, block_framing,
            )
            return await resolve_mic_on_face_key(
                mic_on, cast.avatar_id, th_look.id, th_look.face_ref_key, session
            )

    if block.avatar_look_id:
        look = await session.get(AvatarLook, block.avatar_look_id)
        if look and look.face_ref_key and look.status == "ready":
            logger.info("Block %s using look %s (%s)", block.id, look.id, look.name)
            return await resolve_mic_on_face_key(
                _effective_mic_on(look), cast.avatar_id, look.id, look.face_ref_key, session
            )

    # Try the avatar default look
    if cast.avatar_id:
        result = await session.execute(
            select(AvatarLook)
            .where(AvatarLook.avatar_id == cast.avatar_id)
            .where(AvatarLook.is_default == True)
            .where(AvatarLook.status == "ready")
        )
        default_look = result.scalar_one_or_none()
        if default_look and default_look.face_ref_key:
            return await resolve_mic_on_face_key(
                _effective_mic_on(default_look), cast.avatar_id, default_look.id,
                default_look.face_ref_key, session
            )

    # Legacy fallback
    return cast.avatar.face_ref_key or ""


async def _generate_videos_only(cast_id: str, user_id: str):
    """Submit InfiniteTalk jobs for all variants that have TTS audio."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from models.cast import Cast, CastStatus
    from models.block import Block
    from models.variant import Variant, VariantStatus
    from services.runpod import get_runpod_service
    from services.r2_storage import get_r2_storage_service
    from services.render_planner import pick_render_size_for_pip, get_full_render_size, get_canvas_dimensions, get_infinitetalk_sizes
    from services.twick_compositor_adapter import get_pip_placement_for_block
    from websocket.manager import ws_manager
    from engine.cast_generator import _default_motion_for_block_role

    factory = _make_session_factory()
    runpod = get_runpod_service()
    r2 = get_r2_storage_service()

    async with factory() as session:
        result = await session.execute(
            select(Cast)
            .options(
                selectinload(Cast.blocks).selectinload(Block.variants),
                selectinload(Cast.avatar),
            )
            .where(Cast.id == cast_id)
        )
        cast = result.scalar_one_or_none()
        if not cast:
            return

        cast.status = CastStatus.GENERATING_VIDEOS
        cast.generation_progress = 0.5
        cast.progress_step = "Submitting video render jobs..."
        await session.commit()

        avatar_face_ref_key = cast.avatar.face_ref_key or "" if cast.avatar else ""
        effects_config = cast.effects_config or {}
        cast_output_format = cast.output_format or "9:16"
        blocks = [b for b in cast.blocks if getattr(b, 'is_active', True) and b.deleted_at is None]
        submitted = 0

        for block in blocks:
            for variant in getattr(block, 'variants', []):
                if not variant.audio_key:
                    continue
                # Skip variants marked FAILED by duration guard or previous errors
                variant_status = str(variant.status) if variant.status else ""
                if variant_status in ("FAILED", "VariantStatus.FAILED"):
                    continue

                audio_url = r2.get_public_url(variant.audio_key)
                resolved_face_key = await _resolve_face_image_for_block(cast, block, session)
                scene_key = getattr(block, 'scene_image_key', None) or resolved_face_key

                # Pre-composite background if configured.
                # Priority: block.background_id (per-block) > cast.effects_config.background (cast-level)
                try:
                    from services.background_compositor import composite_face_on_background
                    from models.avatar_background import AvatarBackground
                    import tempfile

                    bg_image_url_for_compositor = ""
                    bg_type = "original"
                    bg_color = "#1a1a2e"
                    bg_gradient = None

                    # Check per-block background first
                    if getattr(block, 'background_id', None):
                        avatar_bg = await session.get(AvatarBackground, block.background_id)
                        if avatar_bg and not avatar_bg.deleted_at:
                            bg_image_url_for_compositor = r2.get_public_url(avatar_bg.r2_key)
                            bg_type = "image"
                        else:
                            logger.warning(
                                "Block %s references deleted/missing background_id=%s, falling back to cast-level",
                                block.id, block.background_id,
                            )

                    # Fall back to cast-level config if no per-block background
                    if bg_type == "original":
                        cast_bg_config = effects_config.get("background", {})
                        bg_type = cast_bg_config.get("type", "original")
                        bg_color = cast_bg_config.get("color", "#1a1a2e")
                        bg_gradient = cast_bg_config.get("gradient")
                        if bg_type == "image" and cast_bg_config.get("image_key"):
                            bg_image_url_for_compositor = r2.get_public_url(cast_bg_config["image_key"])

                    if bg_type in ("color", "image", "gradient") and scene_key:
                        face_url = r2.get_public_url(scene_key)
                        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                            composited_path = tmp.name
                        await composite_face_on_background(
                            face_image_url=face_url,
                            bg_type=bg_type,
                            bg_color=bg_color,
                            bg_image_url=bg_image_url_for_compositor,
                            gradient=bg_gradient,
                            output_path=composited_path,
                        )
                        composited_key = f"creators/{user_id}/casts/{cast_id}/input_face_bg_{variant.id}.jpg"
                        await r2.upload_file(composited_path, composited_key, content_type="image/jpeg")
                        scene_key = composited_key
                        import os
                        os.unlink(composited_path)
                        logger.info(
                            "Composited %s background for variant %s (source: %s)",
                            bg_type, variant.id,
                            "block" if getattr(block, 'background_id', None) else "cast",
                        )
                except Exception as bg_err:
                    sentry_sdk.capture_exception(bg_err)
                    logger.warning("Background compositing failed for variant %s: %s. Using original scene.", variant.id, bg_err)

                # Render mode branching for InfiniteTalk submission
                render_mode = block.render_mode or "avatar_full"

                # Phase 0.4: Live PIP via MuseTalk — synchronous, no RunPod
                if render_mode == "pip" and getattr(block, "pip_engine", "infinitetalk_rendered") == "musetalk_live":
                    from config import settings as _cfg
                    if _cfg.MUSETALK_AVAILABLE:
                        try:
                            from services.musetalk_client import get_musetalk_client
                            musetalk = get_musetalk_client()
                            face_url = r2.get_public_url(scene_key) if scene_key else None
                            if face_url and audio_url:
                                result = await musetalk.submit_lipsync(
                                    face_image_url=face_url,
                                    audio_url=audio_url,
                                    render_size="240p",
                                )
                                variant.video_key = result["output_r2_key"]
                                variant.status = VariantStatus.READY
                                logger.info(
                                    "Block %s Live PIP via MuseTalk: %s (%.1fs render)",
                                    block.id, result["output_r2_key"], result.get("render_seconds", 0)
                                )
                                continue  # MuseTalk succeeded, skip InfiniteTalk
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            logger.exception("MuseTalk failed for block %s: %s. Falling back to InfiniteTalk.", block.id, e)
                            # Fall through to InfiniteTalk path below

                # Body motion / avatar_action: Wan 2.7 i2v → Kling LipSync → R2.
                # avatar_action shares this code path — both render via I2V
                # interpolation between start/end frames. avatar_action's
                # frames are generated by FLUX Kontext from the avatar's
                # face + a scene prompt; legacy avatar_acting blocks may
                # have generic body shots instead. Either way, we try the
                # explicitly-pinned looks first, fall back to the latest
                # ready per-block action frames, then to the avatar's
                # face_ref_key if nothing else is available.
                if render_mode == "body_motion":
                    from services.wan_body_motion import generate_body_motion_clip
                    from services.kling_lipsync import apply_lipsync
                    from models.avatar import Avatar
                    from models.avatar_look import AvatarLook
                    from sqlalchemy import select as _sa_select

                    start_look_id = block.body_motion_start_look_id
                    end_look_id = block.body_motion_end_look_id
                    raw_motion_prompt = block.body_motion_prompt or "walks naturally"

                    # I2V is anchored by the start frame which already shows
                    # the avatar; prepending appearance text drowns the
                    # motion description in the model's prompt budget and
                    # produces a different-looking person performing
                    # arbitrary motion. avatar_for_prompt is still resolved
                    # for the face_ref fallback used inside the resolver.
                    avatar_for_prompt = await session.get(Avatar, cast.avatar_id)
                    motion_prompt = raw_motion_prompt

                    async def _resolve_action_frame_url(
                        kind: str, pinned_id: str | None
                    ) -> str | None:
                        """Pick the URL for a start/end frame.

                        Priority: explicitly-pinned look → most recent ready
                        per-block action_block_<id>_<kind> look → most recent
                        ready body_motion_block_<id>_<kind> look → avatar
                        face_ref (last-ditch so the renderer doesn't crash).
                        """
                        # 1) Pinned look.
                        if pinned_id:
                            pinned = await session.get(AvatarLook, pinned_id)
                            if pinned and pinned.face_ref_key:
                                return r2.get_public_url(pinned.face_ref_key)
                        # 2) Latest ready action_block frame. Round-6 Bug B: a
                        #    look is keyed by its camera framing too — never
                        #    reuse a look generated for a different framing.
                        block_framing = (getattr(block, "framing", None) or "MEDIUM")
                        for prefix in (
                            f"action_block_{block.id}_{kind}",
                            f"body_motion_block_{block.id}_{kind}",
                        ):
                            res = await session.execute(
                                _sa_select(AvatarLook)
                                .where(AvatarLook.avatar_id == cast.avatar_id)
                                .where(AvatarLook.look_type == prefix)
                                .where(AvatarLook.framing == block_framing)
                                .where(AvatarLook.status == "ready")
                                .order_by(AvatarLook.created_at.desc())
                                .limit(1)
                            )
                            row = res.scalars().first()
                            if row and row.face_ref_key:
                                return r2.get_public_url(row.face_ref_key)
                        # 3) Avatar face_ref fallback (start only — last frame
                        #    intentionally returns None so I2V runs without a
                        #    target frame and produces a free-form clip).
                        if kind == "start" and avatar_for_prompt and avatar_for_prompt.face_ref_key:
                            return r2.get_public_url(avatar_for_prompt.face_ref_key)
                        return None

                    start_url = await _resolve_action_frame_url("start", start_look_id)
                    end_url = await _resolve_action_frame_url("end", end_look_id)

                    if not start_url:
                        logger.warning("Block %s body_motion has no usable start frame, skipping", block.id)
                        variant.status = VariantStatus.FAILED
                        variant.error_message = "Action block requires a start frame"
                        continue

                    try:
                        # Step 1: Generate silent body motion clip via Wan 2.7
                        wan_result = await generate_body_motion_clip(
                            start_image_url=start_url,
                            end_image_url=end_url,
                            prompt=motion_prompt,
                            duration_seconds=5,
                            resolution="720p",
                        )
                        wan_video_url = wan_result["video_url"]
                        logger.info("Block %s Wan body motion generated: %s", block.id, wan_video_url[:80])

                        # Step 2: Apply Kling LipSync with TTS audio
                        if variant.audio_key:
                            lipsync_result = await apply_lipsync(
                                video_url=wan_video_url,
                                audio_url=audio_url,
                            )
                            final_video_url = lipsync_result["video_url"]
                            logger.info("Block %s Kling LipSync applied: %s", block.id, final_video_url[:80])
                        else:
                            final_video_url = wan_video_url
                            logger.info("Block %s no audio — using silent Wan output", block.id)

                        # Step 3: Download result and upload to R2
                        import httpx
                        import tempfile

                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                            async with httpx.AsyncClient(timeout=120) as http:
                                resp = await http.get(final_video_url)
                                resp.raise_for_status()
                                tmp.write(resp.content)
                            tmp_path = tmp.name

                        video_r2_key = f"creators/{user_id}/casts/{cast_id}/body_motion/{variant.id}.mp4"
                        await r2.upload_file(tmp_path, video_r2_key, content_type="video/mp4")

                        import os as _os
                        _os.unlink(tmp_path)

                        variant.video_key = video_r2_key
                        variant.status = VariantStatus.READY
                        logger.info(
                            "Block %s body_motion complete: %s (wan=%.1fs, lipsync=%s)",
                            block.id, video_r2_key,
                            wan_result.get("duration_seconds", 0),
                            "yes" if variant.audio_key else "no",
                        )
                        continue  # Skip InfiniteTalk submission

                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        logger.exception("Block %s body_motion failed: %s", block.id, e)
                        variant.status = VariantStatus.FAILED
                        variant.error_message = f"Body motion generation failed: {str(e)[:200]}"
                        continue

                if render_mode == "voiceover":
                    # Skip InfiniteTalk entirely — compositor will use user video as base
                    variant.runpod_job_id = None
                    variant.video_key = None
                    variant.status = VariantStatus.READY
                    logger.info("Block %s voiceover mode — skipping InfiniteTalk submission", block.id)
                    continue

                # Determine render size based on mode
                render_size = "720p"  # default for avatar_full
                if render_mode == "pip":
                    timeline_entry = (cast.timeline_json or {}).get("default", {})
                    twick_data = timeline_entry.get("twick_data") or {}
                    block_regions = _compute_block_regions(blocks)
                    _x, _y, pip_w, pip_h = get_pip_placement_for_block(twick_data, str(block.id), block_regions)
                    render_size, rw, rh = pick_render_size_for_pip(pip_w, pip_h, cast_output_format)
                    default_pixels = 720 * 1280
                    chosen_pixels = rw * rh
                    savings_pct = int((1 - chosen_pixels / default_pixels) * 100)
                    logger.info(
                        "Block %s PIP mode — InfiniteTalk at %s (%dx%d) [%d%% pixel reduction vs default]",
                        block.id, render_size, rw, rh, savings_pct,
                    )


                # Phase 3.2: Dynamic sizing for avatar_full mode
                if render_mode == "avatar_full":
                    timeline_entry = (cast.timeline_json or {}).get("default", {})
                    twick_data = timeline_entry.get("twick_data") or {}
                    block_regions = _compute_block_regions(blocks)
                    av_w, av_h = _get_avatar_full_dimensions(twick_data, str(block.id), block_regions)
                    if av_w > 0 and av_h > 0:
                        render_size, rw, rh = pick_render_size_for_pip(av_w, av_h, cast_output_format)
                        logger.info(
                            "Block %s avatar_full dynamic sizing: dragged=%dx%d -> render=%s (%dx%d)",
                            block.id, av_w, av_h, render_size, rw, rh,
                        )

                if scene_key and audio_url:
                    image_url = r2.get_public_url(scene_key)
                    motion = getattr(variant, 'motion_prompt', '') or _default_motion_for_block_role(
                        block.type.value if hasattr(block.type, 'value') else str(block.type)
                    )

                    # Phase 3.1: Detailed render submission logging
                    pip_w_log = pip_h_log = 0
                    av_w_log = av_h_log = 0
                    if render_mode == "pip":
                        try:
                            _tl = (cast.timeline_json or {}).get("default", {})
                            _td = _tl.get("twick_data") or {}
                            _br = _compute_block_regions(blocks)
                            _, _, pip_w_log, pip_h_log = get_pip_placement_for_block(_td, str(block.id), _br)
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                    elif render_mode == "avatar_full":
                        try:
                            _tl = (cast.timeline_json or {}).get("default", {})
                            _td = _tl.get("twick_data") or {}
                            _br = _compute_block_regions(blocks)
                            av_w_log, av_h_log = _get_avatar_full_dimensions(_td, str(block.id), _br)
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                    rw_log = rh_log = 0
                    from services.render_planner import get_infinitetalk_sizes as _get_it_sizes
                    _it_sizes = _get_it_sizes(cast_output_format)
                    _sz_map = {p: (w, h) for p, w, h in _it_sizes}
                    _sz_map.update({"240p": (240, 426), "360p": (360, 640)})
                    if render_size in _sz_map:
                        rw_log, rh_log = _sz_map[render_size]
                    logger.info(
                        "Block %s render submission: mode=%s, pip_engine=%s, requested_size=%s, "
                        "actual_render=%s (%dx%d), dragged_dims=%s",
                        block.id, render_mode, getattr(block, 'pip_engine', 'n/a'),
                        "dynamic" if render_mode in ("pip", "avatar_full") else "default",
                        render_size, rw_log, rh_log,
                        f"{pip_w_log}x{pip_h_log}" if render_mode == "pip" else f"{av_w_log}x{av_h_log}" if render_mode == "avatar_full" else "n/a"
                    )

                    job_id = await runpod.submit_video_job_webhook(
                        image_url=image_url,
                        audio_url=audio_url,
                        variant_id=variant.id,
                        prompt=motion,
                        size=render_size,
                    )
                    variant.runpod_job_id = job_id
                    variant.status = VariantStatus.GENERATING
                    submitted += 1

        cast.progress_step = f"{submitted} video jobs submitted. Rendering on GPU..."
        await session.commit()

        # Re-entrancy: count blocks that need InfiniteTalk vs voiceover-only
        total_needing_gpu = sum(
            1 for b in blocks for v in getattr(b, "variants", [])
            if (b.render_mode or "avatar_full") != "voiceover" and v.audio_key
        )
        voiceover_count = sum(
            1 for b in blocks
            if (b.render_mode or "avatar_full") == "voiceover"
        )
        already_done = sum(1 for b in blocks for v in getattr(b, "variants", []) if getattr(v, "video_key", None))

        if total_needing_gpu == 0 and voiceover_count > 0:
            # All blocks are voiceover — straight to compositor, skip GPU wait
            logger.info("Cast %s all-voiceover — going straight to compositor", cast.id)
            await _run_twick_compositor(cast.id)
            cast.status = CastStatus.READY
            cast.generation_progress = 1.0
            cast.progress_step = "All voiceover — compositing complete"
            await session.commit()
            await _populate_final_video_url(cast.id)
        elif total_needing_gpu == 0 and voiceover_count == 0:
            cast.status = CastStatus.GENERATION_FAILED
            cast.generation_error = "No variants to render"
            cast.generation_progress = 0.0
            await session.commit()
        elif submitted == 0 and already_done >= total_needing_gpu:
            cast.status = CastStatus.READY
            cast.generation_progress = 1.0
            cast.progress_step = f"All {already_done} clips already rendered"
            logger.info("Cast %s: already complete, %d variants ready", cast.id, already_done)
            await session.commit()
            await _populate_final_video_url(cast.id)


    await ws_manager.broadcast_to_user(user_id, {
        "type": "GENERATION_PROGRESS",
        "payload": {"cast_id": cast_id, "progress": 0.5, "current_step": f"{submitted} video clips rendering on GPU..."},
    })




def _get_avatar_full_dimensions(twick_data, block_id, block_regions):
    """Get avatar_full element dimensions from Twick timeline, if available."""
    try:
        tracks = twick_data.get("tracks") or []
        for track in tracks:
            elements = track.get("elements") or []
            for el in elements:
                metadata = el.get("metadata") or {}
                if metadata.get("is_avatar_full") and metadata.get("block_id") == block_id:
                    w = el.get("width", 0)
                    h = el.get("height", 0)
                    if w > 0 and h > 0:
                        return int(w), int(h)
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return 0, 0


def _compute_block_regions(blocks, variants_map=None):
    """Compute block time regions from blocks + their active variant TTS durations.

    Args:
        blocks: Sorted list of Block ORM objects (active, sorted by position).
        variants_map: Optional dict mapping block_id -> list of variants.
                      If None, uses block.variants.

    Returns:
        List of {block_id, start_s, end_s, index} dicts.
    """
    regions = []
    cursor = 0.0
    for idx, block in enumerate(blocks):
        variants = variants_map.get(block.id, []) if variants_map else getattr(block, "variants", [])
        # Pick first variant with a TTS duration
        dur = 5.0  # default fallback
        for v in variants:
            d = getattr(v, "tts_duration_seconds", 0) or getattr(v, "duration_seconds", 0) or 0
            if d > 0:
                dur = d
                break
        regions.append({
            "block_id": block.id,
            "start_s": cursor,
            "end_s": cursor + dur,
            "index": idx,
        })
        cursor += dur
    return regions


def _product_overlay_enabled() -> bool:
    """Whether to force a product overlay onto PRODUCT/PRODUCT_DEMO blocks.

    Default ON. The cast should be obviously about the product, so blocks that
    talk about it get the product's own image/video burned in even when the
    timeline snapshot didn't carry an explicit product element. Disable with
    ``PRODUCT_OVERLAY_ENABLED=0``.
    """
    import os
    return os.getenv("PRODUCT_OVERLAY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "")


def _product_overlay_min_blocks() -> int:
    """Minimum number of product blocks that must receive an overlay.

    The injection guarantees AT LEAST this many PRODUCT/PRODUCT_DEMO blocks end
    up with a product overlay. Env-tunable via ``PRODUCT_OVERLAY_MIN_PRODUCT_BLOCKS``
    (default 2). A value <= 0 disables the minimum guarantee.
    """
    import os
    try:
        return int(os.getenv("PRODUCT_OVERLAY_MIN_PRODUCT_BLOCKS", "2"))
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return 2


async def _inject_product_overlays_for_blocks(
    blocks,
    block_regions,
    product_overlays: dict,
    session,
    r2,
    cast_id: str,
) -> None:
    """Ensure PRODUCT/PRODUCT_DEMO blocks carry a product overlay.

    The user complaint was "where is the product itself?" — the timeline often
    has no product element, so nothing of the product ever lands on screen.
    For each PRODUCT/PRODUCT_DEMO block that has no overlay yet, resolve the
    effective product and inject a still overlay using the product's hero
    image (``cover_image_key``), preferring a video ProductAsset's poster frame
    when one exists. The overlay sits in the MIDDLE of the block for at least
    2 seconds (block-relative timing, since each block is composited as its own
    clip). Mutates ``product_overlays`` in place.

    Gated by :func:`_product_overlay_enabled`; honours
    :func:`_product_overlay_min_blocks`. All resolution errors are captured to
    Sentry and skipped so the render still proceeds.
    """
    if not _product_overlay_enabled():
        return

    from models.product import Product as _Product
    from models.product_asset import ProductAsset as _ProductAsset
    from tasks.cast_render import resolve_effective_product_id
    from sqlalchemy import select as _sa_select

    region_by_block = {str(r.get("block_id")): r for r in (block_regions or [])}
    injected = 0
    min_blocks = _product_overlay_min_blocks()

    for block_idx, block in enumerate(blocks):
        try:
            btype = getattr(block, "type", None)
            btype_val = str(btype.value) if hasattr(btype, "value") else str(btype or "")
            if btype_val not in ("PRODUCT", "PRODUCT_DEMO"):
                continue

            # Already has an overlay from the timeline — leave it untouched.
            if product_overlays.get(block_idx):
                injected += 1
                continue

            product_id = await resolve_effective_product_id(block, session, cast_id)
            if not product_id:
                continue

            # Prefer a video ProductAsset's poster (its thumbnail), else the
            # product hero image. The card compositor burns a still, so we
            # always resolve down to an image key here.
            still_key = None
            res = await session.execute(
                _sa_select(_ProductAsset)
                .where(_ProductAsset.product_id == product_id)
                .where(_ProductAsset.media_type == "video")
                .order_by(_ProductAsset.position.asc())
                .limit(1)
            )
            vid = res.scalars().first()
            vthumb = getattr(vid, "thumbnail_r2_key", None) if vid else None
            if vthumb:
                still_key = vthumb

            product = await session.get(_Product, product_id)
            if not still_key:
                still_key = getattr(product, "cover_image_key", None) if product else None
            if not still_key:
                continue

            src = r2.get_public_url(still_key)
            if not src:
                continue

            region = region_by_block.get(str(block.id))
            block_dur = 5.0
            if region:
                try:
                    block_dur = max(float(region.get("end_s", 0)) - float(region.get("start_s", 0)), 0.0) or 5.0
                except (TypeError, ValueError) as e:
                    sentry_sdk.capture_exception(e)
                    block_dur = 5.0
            # Centre a >= 2s window inside the block (block-relative timing).
            hold = min(max(2.0, block_dur * 0.5), block_dur)
            start_rel = max((block_dur - hold) / 2.0, 0.0)
            end_rel = min(start_rel + hold, block_dur)

            title = (getattr(product, "title", None) or getattr(product, "name", None) or "") if product else ""
            price = ""
            if product:
                cur = getattr(product, "current_price", None) or 0
                base = getattr(product, "price", None) or 0
                if cur and cur > 0:
                    price = f"${cur:.2f}"
                elif base and base > 0:
                    price = f"${base:.2f}"

            product_overlays.setdefault(block_idx, []).append({
                "product_id": product_id,
                "src": src,
                "title": title,
                "price": price,
                "start_s": start_rel,
                "end_s": end_rel,
                "x": 20,
                "y": 1080,
                "injected": True,
            })
            injected += 1
        except Exception as e:
            sentry_sdk.capture_exception(e)
            continue

    logger.info(
        "Product overlay injection: %d product block(s) covered (min target %d) for cast %s",
        injected, min_blocks, cast_id,
    )


async def _run_twick_compositor(cast_id: str):
    """Apply Twick timeline overlays (text + music) to rendered variants.

    Called after all variants have video_key. Reads timeline_json from the cast,
    translates to per-block overlays, and burns them into each variant's video,
    storing the result as final_video_key.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from models.cast import Cast
    from models.block import Block
    from models.variant import Variant, VariantStatus
    from services.twick_compositor_adapter import translate_timeline_to_overlays_v2, get_pip_placement_for_block
    from services.cast_ffmpeg_composer import _resolve_music_volume
    from models.product import Product
    from models.user_video import UserVideoAsset
    from services.video_compositor import (
        composite_text_overlays_multi,
        composite_product_overlays_multi,
        mux_music_track,
        _download_file,
        composite_pip_avatar,
        trim_user_video_segment,
    )
    from services.r2_storage import get_r2_storage_service

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    async with factory() as session:
        result = await session.execute(
            select(Cast)
            .options(
                selectinload(Cast.blocks).selectinload(Block.variants),
            )
            .where(Cast.id == cast_id)
        )
        cast = result.scalar_one_or_none()
        if not cast or not cast.timeline_json:
            return

        blocks = sorted(
            [b for b in cast.blocks if getattr(b, 'is_active', True) and b.deleted_at is None],
            key=lambda b: b.position,
        )
        if not blocks:
            return

        # Log render mode for each block
        import logging
        _compositor_logger = logging.getLogger("generate_cast.compositor")
        for block in blocks:
            mode = block.render_mode or "avatar_full"
            vid_asset = block.user_video_asset_id
            _compositor_logger.info(
                f"Block {block.id}: render_mode={mode}, user_video_asset_id={vid_asset}"
            )

        # Process each variant_id entry in timeline_json
        for variant_id, timeline_entry in cast.timeline_json.items():
            twick_data = timeline_entry.get("twick_data")
            block_regions = timeline_entry.get("block_regions", [])
            if not twick_data:
                continue

            # Compute block_regions fresh from blocks + TTS durations (source of truth)
            block_regions_computed = _compute_block_regions(blocks)
            overlays = translate_timeline_to_overlays_v2(twick_data, block_regions_computed, blocks)
            text_overlays = overlays.get("text_overlays", {})
            product_overlays = overlays.get("product_overlays", {})
            music_track = overlays.get("music_track")

            # Enrich product overlays with title/price from the DB
            for _bidx, _block_povs in product_overlays.items():
                for _pov in _block_povs:
                    _pid = _pov.get("product_id")
                    if not _pid:
                        continue
                    _product = await session.get(Product, _pid)
                    if _product:
                        _pov["title"] = _product.title or _product.name or ""
                        if _product.current_price and _product.current_price > 0:
                            _pov["price"] = f"${_product.current_price:.2f}"
                        elif _product.price and _product.price > 0:
                            _pov["price"] = f"${_product.price:.2f}"
                        else:
                            _pov["price"] = ""

            # Guarantee the product itself shows up: inject a product overlay
            # onto PRODUCT/PRODUCT_DEMO blocks that the timeline left bare.
            await _inject_product_overlays_for_blocks(
                blocks,
                block_regions_computed,
                product_overlays,
                session,
                r2,
                cast_id,
            )

            # Apply overlays per block/variant — mode-aware pipeline
            for block_idx, block in enumerate(blocks):
                block_text_overlays = text_overlays.get(block_idx, [])
                mode = block.render_mode or "avatar_full"
                user_video_id = block.user_video_asset_id

                for variant in getattr(block, "variants", []):
                    # Determine base video based on render mode
                    base_r2_key = None
                    user_video = None

                    if mode == "voiceover":
                        if not user_video_id:
                            logger.warning("Block %s voiceover mode has no user_video_asset_id, skipping", block.id)
                            continue
                        user_video = await session.get(UserVideoAsset, user_video_id)
                        if not user_video:
                            logger.warning("Block %s user_video_asset %s not found, skipping", block.id, user_video_id)
                            continue
                        base_r2_key = user_video.r2_key
                    elif mode == "pip":
                        if not user_video_id:
                            logger.warning("Block %s pip mode has no user_video_asset_id, skipping", block.id)
                            continue
                        user_video = await session.get(UserVideoAsset, user_video_id)
                        if not user_video:
                            continue
                        base_r2_key = user_video.r2_key
                    else:  # avatar_full / body_motion
                        if not variant.video_key:
                            logger.warning("Block %s %s but variant has no video_key, skipping", block.id, mode)
                            continue
                        base_r2_key = variant.video_key

                    if not base_r2_key:
                        continue

                    try:
                        import tempfile
                        import os

                        with tempfile.TemporaryDirectory(prefix="twick_") as tmpdir:
                            # Download the base video
                            base_url = f"https://media.luminacast.com/{base_r2_key}"
                            input_path = os.path.join(tmpdir, "base.mp4")
                            await _download_file(base_url, input_path)
                            current_path = input_path

                            # Get TTS duration
                            tts_duration = (
                                getattr(variant, "tts_duration_seconds", None)
                                or getattr(variant, "duration_seconds", None)
                                or 5.0
                            )

                            # For voiceover/pip: trim user video using NLE segment data if available
                            if mode in ("voiceover", "pip"):
                                block_video_segments = overlays.get("video_segments", {}).get(block_idx, [])
                                if block_video_segments and len(block_video_segments) > 0:
                                    # User has split/trimmed the clip — use segment data
                                    seg = block_video_segments[0]  # single-segment for now
                                    media_offset = float(seg.get("media_offset", 0))
                                    seg_duration = (
                                        float(seg.get("end_s_in_block", 0))
                                        - float(seg.get("start_s_in_block", 0))
                                    )
                                    if seg_duration <= 0:
                                        seg_duration = tts_duration
                                    logger.info(
                                        "Trimming user video: offset=%.2fs, duration=%.2fs (from NLE segment)",
                                        media_offset, seg_duration,
                                    )
                                else:
                                    media_offset = 0
                                    seg_duration = tts_duration
                                    logger.info(
                                        "Trimming user video: offset=0.00s, duration=%.2fs (default)",
                                        seg_duration,
                                    )
                                trimmed_path = os.path.join(tmpdir, "trimmed.mp4")
                                await trim_user_video_segment(
                                    src_path=current_path,
                                    media_offset=media_offset,
                                    duration=seg_duration,
                                    output=trimmed_path,
                                )
                                current_path = trimmed_path

                                # Mux TTS audio onto user video
                                if variant.audio_key:
                                    audio_url = f"https://media.luminacast.com/{variant.audio_key}"
                                    audio_path = os.path.join(tmpdir, "voice.mp3")
                                    await _download_file(audio_url, audio_path)

                                    voice_muxed = os.path.join(tmpdir, "voice_muxed.mp4")
                                    voice_mux_cmd = [
                                        "ffmpeg", "-y",
                                        "-i", current_path,
                                        "-i", audio_path,
                                        "-map", "0:v", "-map", "1:a",
                                        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                                        "-pix_fmt", "yuv420p",
                                        "-profile:v", "high", "-level", "4.0",
                                        "-c:a", "aac", "-b:a", "128k",
                                        "-shortest",
                                        "-movflags", "+faststart",
                                        voice_muxed,
                                    ]
                                    proc = await asyncio.create_subprocess_exec(
                                        *voice_mux_cmd,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                    )
                                    _, vstderr = await proc.communicate()
                                    if proc.returncode != 0:
                                        raise RuntimeError(f"voice mux failed: {vstderr.decode()[-300:]}")
                                    current_path = voice_muxed

                            # For pip mode: overlay the small avatar clip
                            if mode == "pip" and variant.video_key:
                                pip_url = f"https://media.luminacast.com/{variant.video_key}"
                                pip_clip_path = os.path.join(tmpdir, "pip_avatar.mp4")
                                await _download_file(pip_url, pip_clip_path)

                                pip_x, pip_y, pip_w, pip_h = get_pip_placement_for_block(
                                    twick_data, str(block.id), block_regions_computed,
                                )

                                pip_shape = "rect"
                                for track in (twick_data.get("tracks") or []):
                                    for el in (track.get("elements") or []):
                                        meta = el.get("metadata") or {}
                                        if meta.get("is_pip_avatar") and str(meta.get("block_id")) == str(block.id):
                                            pip_shape = meta.get("pip_shape", "rect")
                                            break

                                pip_composited = os.path.join(tmpdir, "pip_composited.mp4")
                                await composite_pip_avatar(
                                    base=current_path,
                                    pip_clip=pip_clip_path,
                                    x=pip_x, y=pip_y,
                                    width=pip_w, height=pip_h,
                                    output=pip_composited,
                                    shape=pip_shape,
                                )
                                current_path = pip_composited

                            # Apply text overlays
                            if block_text_overlays:
                                text_output = os.path.join(tmpdir, "text_out.mp4")
                                await composite_text_overlays_multi(
                                    current_path, block_text_overlays, text_output,
                                )
                                current_path = text_output

                            # Apply product overlays
                            block_product_overlays = product_overlays.get(block_idx, [])
                            if block_product_overlays:
                                # PRODUCT_DEMO blocks bake a generic bottle in
                                # the avatar's hand; enlarge the real-product
                                # overlay so it covers more of the generic bake
                                # (user complaint @0:24). Scale is env-tunable.
                                _block_type_val = ""
                                try:
                                    _bt = getattr(block, "type", None)
                                    _block_type_val = (
                                        str(_bt.value) if hasattr(_bt, "value")
                                        else str(_bt or "")
                                    )
                                except Exception as _bt_exc:
                                    sentry_sdk.capture_exception(_bt_exc)
                                    _block_type_val = ""
                                _prod_overlay_width = None
                                if _block_type_val == "PRODUCT_DEMO":
                                    from services.twick_compositor_adapter import (
                                        product_demo_overlay_width,
                                    )
                                    _prod_overlay_width = product_demo_overlay_width()

                                prod_overlay_inputs = []
                                for pov in block_product_overlays:
                                    prod_img_path = None
                                    src = pov.get("src", "")
                                    if src:
                                        try:
                                            ext = ".png" if src.endswith(".png") else ".jpg"
                                            prod_img_path = os.path.join(tmpdir, f"prod_{len(prod_overlay_inputs)}{ext}")
                                            await _download_file(src, prod_img_path)
                                        except Exception as dl_err:
                                            sentry_sdk.capture_exception(dl_err)
                                            logger.warning("Failed to download product image: %s", dl_err)
                                            prod_img_path = None
                                    _ov_input = {
                                        "product_image_path": prod_img_path,
                                        "title": pov.get("title", ""),
                                        "price": pov.get("price", ""),
                                        "start_s": pov.get("start_s", 0),
                                        "end_s": pov.get("end_s", 5),
                                        "x": pov.get("x", 20),
                                        "y": pov.get("y", 1080),
                                    }
                                    if _prod_overlay_width is not None:
                                        _ov_input["width"] = _prod_overlay_width
                                    prod_overlay_inputs.append(_ov_input)
                                prod_output = os.path.join(tmpdir, "prod_out.mp4")
                                await composite_product_overlays_multi(
                                    current_path, prod_overlay_inputs, prod_output,
                                )
                                current_path = prod_output

                            # Mux music track if present
                            if music_track and music_track.get("src"):
                                # Precedence: element prop → cast.music_volume
                                # column → env → hard-coded default. Never the
                                # old mux_music_track default of 0.3.
                                music_vol = _resolve_music_volume(
                                    [{"props": {"volume": music_track.get("volume")}}],
                                    cast_volume_override=getattr(
                                        cast, "music_volume", None
                                    ),
                                )
                                music_output = os.path.join(tmpdir, "music_out.mp4")
                                await mux_music_track(
                                    current_path,
                                    music_track["src"],
                                    music_output,
                                    volume=music_vol,
                                )
                                current_path = music_output

                            # Upload final result
                            if current_path != input_path:
                                upload_key = f"creators/{cast.user_id}/casts/{cast.id}/variants/{variant.id}_final.mp4"
                                with open(current_path, "rb") as f:
                                    output_bytes = f.read()
                                await r2.upload_bytes(output_bytes, upload_key, content_type="video/mp4")
                                variant.final_video_key = upload_key
                                logger.info(
                                    "Twick compositor: variant %s mode=%s -> %s (%d bytes, %d text, %d prod, music=%s)",
                                    variant.id, mode, upload_key, len(output_bytes),
                                    len(block_text_overlays), len(block_product_overlays),
                                    bool(music_track),
                                )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                        logger.warning(
                            "Twick compositor failed for variant %s mode=%s: %s. Keeping original video.",
                            variant.id, mode, e,
                        )
                        existing_warnings = list(variant.composition_warnings or [])
                        existing_warnings.append({"step": "twick_compositor", "error": str(e)[:200]})
                        variant.composition_warnings = existing_warnings

        # Update cast-level final_video_url from first variant with final_video_key
        for block in blocks:
            for variant in getattr(block, "variants", []):
                if getattr(variant, 'final_video_key', None):
                    cast.final_video_url = f"https://media.luminacast.com/{variant.final_video_key}"
                    break
            if cast.final_video_url:
                break

        await session.commit()
        logger.info("Twick compositor complete for cast %s", cast_id)


@celery_app.task(name="generate_cast.recomposite", bind=True, max_retries=1)
def recomposite_cast_task(self, cast_id: str):
    """Re-composite a cast using existing rendered clips + updated timeline."""
    asyncio.run(_recomposite_cast_async(cast_id))


async def _recomposite_cast_async(cast_id: str):
    """Load cast, run Twick compositor, set status READY or GENERATION_FAILED."""
    from models.cast import Cast, CastStatus

    try:
        await _run_twick_compositor(cast_id)
        await _update_cast_status(
            cast_id,
            status=CastStatus.READY,
            generation_progress=1.0,
        )
        await _populate_final_video_url(cast_id)
        logger.info("Recomposite complete for cast %s", cast_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Recomposite failed for cast %s: %s", cast_id, e)
        sentry.capture_exception(e)
        await _mark_cast_failed(cast_id, f"Recomposite failed: {e}")

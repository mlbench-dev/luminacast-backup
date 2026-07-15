"""Celery tasks for avatar creation — complete pipeline with dynamic timeouts."""
import asyncio
import base64
import json
import logging
import os
import tempfile
import cv2
import time
from datetime import datetime, timezone
from tasks import celery_app
from services import sentry
import sentry_sdk

logger = logging.getLogger(__name__)

# Default voice IDs for each voice_style (Fish Audio public models)
VOICE_STYLE_MAP = {
    "energetic": "b87fa0362f11413e9ee146e9944815f3",    # Energetic Female Narration
    "calm": "e686ae649ee44f219a108aacba206c1a",          # Calm Storyteller Male
    "professional": "71b494d2ae264f71946f1966f981b015",  # Matthew Schmitz - Professional
    "friendly": "b545c585f631496c914815291da4e893",      # Friendly Women
    "playful": "b87fa0362f11413e9ee146e9944815f3",       # Energetic Female (fallback)
}

TEST_SCRIPT = "Hi everyone! Welcome to my stream. I'm so excited to show you some amazing products today. Stay tuned, you won't want to miss this!"


def _make_session_factory():
    """Create a fresh async session factory per call to avoid cross-process connection sharing."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _update_progress(avatar_id: str, step: str, percent: float):
    """Update avatar progress in DB using a fresh session."""
    from models.avatar import Avatar
    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar:
            avatar.progress_step = step
            avatar.progress_percent = percent
            await session.commit()
    logger.info(json.dumps({
        "service": "generate_avatar", "level": "info",
        "message": f"Progress: {step} ({percent}%)",
        "avatar_id": avatar_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))


async def _mark_failed(avatar_id: str, error_msg: str):
    from models.avatar import Avatar, AvatarStatus, AvatarPhase
    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar:
            avatar.status = AvatarStatus.FAILED
            avatar.active_phase = AvatarPhase.FAILED
            # Show friendly error messages to users
            friendly = error_msg
            if "stalled" in error_msg.lower() or "timed out" in error_msg.lower():
                friendly = "Video rendering timed out — please try again"
            elif "endpoint" in error_msg.lower() and "down" in error_msg.lower():
                friendly = "Rendering service is temporarily unavailable — please try again later"
            elif "failed" in error_msg.lower():
                friendly = "Video rendering failed — please try again"
            else:
                friendly = f"Something went wrong — please try again"
            avatar.progress_step = friendly
            avatar.progress_percent = 0
            await session.commit()


def _resolve_preview_audio_keys(
    tts_result: dict,
    *,
    user_id: str,
    avatar_id: str,
) -> tuple[str, str]:
    """Return ``(playback_key, lipsync_key)`` for avatar preview renders.

    ``generate_tts`` already uploads:
    - ``audio_key``: MP3 master for playback/final mux
    - ``lipsync_audio_key``: 16 kHz mono WAV for the lipsync engine

    Older preview paths ignored those artifacts, re-uploaded ``tmp_path`` as
    ``test_audio.mp3``, and then drove the renderer from that raw upload. For
    preview renders we should preserve the MP3 for user playback, but feed the
    dedicated WAV into the lipsync renderer whenever it exists.
    """
    playback_key = (
        tts_result.get("audio_key")
        or f"creators/{user_id}/avatar/{avatar_id}/test_audio.mp3"
    )
    lipsync_key = tts_result.get("lipsync_audio_key") or playback_key
    return str(playback_key), str(lipsync_key)


async def _prepare_preview_lipsync_audio(
    *,
    r2,
    lipsync_audio_key: str,
    avatar_id: str,
) -> str:
    """Return a signed URL for preview lipsync audio, preferring prepared WAV.

    Cast renders run `prepare_lipsync_audio(...)` before they hit any speaking
    provider. That step loudness-normalizes, pads edges, and trims trailing
    silence so the lipsync engine gets a stable driver signal. Preview renders
    were skipping that normalization entirely.

    Fallback is the raw lipsync WAV/MP3 signed URL if prep fails.
    """
    raw_url = r2.get_signed_url(lipsync_audio_key, expires_in=7200)
    try:
        from services.lipsync_audio_prep import prepare_lipsync_audio
        prepared_url = await prepare_lipsync_audio(
            raw_url,
            render_id=f"avatar_preview_{avatar_id}",
            block_id=f"avatar_preview_{avatar_id}",
            r2=r2,
        )
        return prepared_url or raw_url
    except Exception:
        return raw_url


async def _set_progress(
    avatar_id: str,
    phase: "AvatarPhase",
    step: str,
    percent: int | None = None,
):
    """Update progress_step ONLY if avatar is currently in the given phase.

    Prevents stale writers from earlier phases from overwriting current state.
    """
    from models.avatar import Avatar, AvatarPhase as AP
    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if not avatar:
            return
        if avatar.active_phase != phase:
            logger.debug(
                f"_set_progress: dropping stale write from phase={phase.value} "
                f"(current phase={avatar.active_phase.value}): {step}"
            )
            return
        avatar.progress_step = step
        if percent is not None:
            avatar.progress_percent = percent
        await session.commit()


async def _advance_phase(
    avatar_id: str,
    new_phase: "AvatarPhase",
    step: str,
    percent: int | None = None,
):
    """Unconditionally advance to a new phase. Call this exactly once per transition."""
    from models.avatar import Avatar
    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if not avatar:
            return
        old_phase = avatar.active_phase
        avatar.active_phase = new_phase
        avatar.progress_step = step
        if percent is not None:
            avatar.progress_percent = percent
        await session.commit()
        logger.info(
            f"Phase transition: {old_phase.value} -> {new_phase.value} "
            f"(avatar {avatar_id}): {step}"
        )


# ═══════════════════════════════════════════════════════════════════════
# AI CHARACTER PIPELINE — 5 Steps
# ═══════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.generate_digital", time_limit=5400, soft_time_limit=5100)
def generate_digital_avatar_task(
    self, avatar_id: str, user_id: str,
    description: str = "", voice_style: str = "energetic",
    persona_preset: str = "energetic_beauty",
    background: str = "studio", camera_position: str = "waist_up",
    style: str = "photorealistic", ai_model: str = "meta-llama/llama-3-70b-instruct",
):
    """Full AI Character pipeline: appearance → face image → test audio → test video → ready."""
    sentry_sdk.set_tag("avatar_id", avatar_id)
    sentry_sdk.set_tag("user_id", user_id)
    sentry_sdk.set_context("avatar", {"avatar_id": avatar_id, "user_id": user_id, "pipeline": "digital"})
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_digital_pipeline(
            avatar_id, user_id, description, voice_style,
            persona_preset, background, camera_position, style, ai_model,
        ))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Digital avatar pipeline failed: {exc}", exc_info=True)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_failed(avatar_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=60)
    finally:
        loop.close()


async def _digital_pipeline(
    avatar_id: str, user_id: str,
    description: str, voice_style: str,
    persona_preset: str, background: str,
    camera_position: str, style: str, ai_model: str,
):
    from models.avatar import Avatar, AvatarStatus
    from services.openrouter import get_openrouter_service
    from services.fish_audio import get_fish_audio_service
    from services.r2_storage import get_r2_storage_service
    from services.runpod import get_runpod_service

    factory = _make_session_factory()
    openrouter = get_openrouter_service()
    fish = get_fish_audio_service()
    r2 = get_r2_storage_service()
    runpod = get_runpod_service()

    # ── Write pipeline tracking rows (Phase F) ──
    from services.pipeline_tracker import start_step, complete_step, fail_step, mark_in_progress
    from models.render_job import RenderJobType, RenderProvider

    step_jobs = {}
    try:
        async with factory() as session:
            step_jobs["face"] = await start_step(session, avatar_id, RenderJobType.AI_FACE_GENERATION, RenderProvider.HOSTKEY_LOCAL)
            step_jobs["voice"] = await start_step(session, avatar_id, RenderJobType.AI_VOICE_GENERATION, RenderProvider.HOSTKEY_LOCAL)
            step_jobs["render"] = await start_step(session, avatar_id, RenderJobType.AI_PREVIEW_RENDER, RenderProvider.RUNPOD_INFINITETALK)
    except Exception as e:
        logger.warning(f"Pipeline tracking init failed (non-fatal): {e}")

    # ── Step 1: Generate appearance description (15%) ──
    sentry_sdk.add_breadcrumb(category='digital_pipeline', message='Step 1: Generating appearance description',
        data={'avatar_id': avatar_id}, level='info')
    await _update_progress(avatar_id, "Generating appearance description...", 15)

    appearance_prompt = f"""Generate a photorealistic portrait of a person for a TikTok live selling avatar.

Style: {style}
Background: {background}
Camera position: {camera_position}
Voice personality: {voice_style}
Additional description: {description or 'A professional, friendly-looking live stream presenter'}

Create a high-quality, {camera_position.replace('_', ' ')} shot portrait photo of this person in a {background.replace('_', ' ')} setting. 
The person should look approachable and camera-ready for a live selling stream.
Make the image sharp, well-lit, and professional."""

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if not avatar:
            return
        avatar.appearance_prompt = appearance_prompt
        await session.commit()

    # ── Step 2: Generate face image via Gemini (30%) ──
    if step_jobs.get("face"):
        try:
            async with factory() as session:
                await mark_in_progress(session, step_jobs["face"])
        except Exception:
            pass
    sentry_sdk.add_breadcrumb(category='digital_pipeline', message='Step 2: Generating face image via Gemini',
        data={'avatar_id': avatar_id}, level='info')
    await _update_progress(avatar_id, "Creating avatar face image...", 30)

    face_key = f"creators/{user_id}/avatar/{avatar_id}/face_ref.jpg"
    try:
        # Use Gemini 3 Pro Image Preview for image generation
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "google/gemini-3-pro-image-preview",
                    "messages": [{"role": "user", "content": appearance_prompt}],
                    "modalities": ["image", "text"],
                },
            )
            resp.raise_for_status()
            result = resp.json()

        # OpenRouter returns images in message.images[] as:
        # {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        image_uploaded = False
        message = result.get("choices", [{}])[0].get("message", {})
        images = message.get("images", [])
        for img_entry in images:
            data_url = ""
            if isinstance(img_entry, dict):
                data_url = img_entry.get("image_url", {}).get("url", "")
            if data_url and ";base64," in data_url:
                raw_b64 = data_url.split(";base64,", 1)[1]
                img_bytes = base64.b64decode(raw_b64)
                if len(img_bytes) > 1000:  # sanity check
                    await r2.upload_bytes(img_bytes, face_key, "image/jpeg")
                    image_uploaded = True
                    logger.info(f"Gemini generated face image: {len(img_bytes)} bytes")
                    break

        if not image_uploaded:
            logger.warning(f"Gemini did not return usable image data for avatar {avatar_id}. Response keys: {list(message.keys())}")
            # Mark failed — don't send a broken placeholder to InfiniteTalk
            await _mark_failed(avatar_id, "AI image generation did not return a usable image. Please try again.")
            return

    except Exception as e:
        sentry_sdk.capture_exception(e)
        sentry_sdk.add_breadcrumb(category='digital_pipeline', message=f'Face image generation failed: {e}',
            data={'avatar_id': avatar_id}, level='error')
        logger.error(f"Image generation failed for avatar {avatar_id}: {e}", exc_info=True)
        await _mark_failed(avatar_id, f"Image generation failed: {str(e)[:150]}")
        return

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.face_ref_key = face_key
        await session.commit()

    # Mark face generation complete (Phase F tracking)
    if step_jobs.get("face"):
        try:
            async with factory() as session:
                await complete_step(session, step_jobs["face"])
        except Exception:
            pass

    # ── Step 3: Generate test voice audio via Fish Audio (50%) ──
    if step_jobs.get("voice"):
        try:
            async with factory() as session:
                await mark_in_progress(session, step_jobs["voice"])
        except Exception:
            pass
    sentry_sdk.add_breadcrumb(category='digital_pipeline', message='Step 3: Generating test voice audio',
        data={'avatar_id': avatar_id, 'voice_style': voice_style}, level='info')
    await _update_progress(avatar_id, "Generating test voice audio...", 50)

    voice_id = VOICE_STYLE_MAP.get(voice_style, VOICE_STYLE_MAP["energetic"])

    tts_result = await fish.generate_tts(text=TEST_SCRIPT, voice_id=voice_id)
    test_audio_key, lipsync_audio_key = _resolve_preview_audio_keys(
        tts_result,
        user_id=user_id,
        avatar_id=avatar_id,
    )
    tmp_path = tts_result["tmp_path"]
    try:
        # Keep legacy cleanup of the local temp file, but do not re-upload it:
        # generate_tts already persisted the playback MP3/WAV artifacts.
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.voice_id = voice_id
        avatar.test_audio_key = test_audio_key
        avatar.test_script = TEST_SCRIPT
        await session.commit()

    # Mark voice generation complete (Phase F tracking)
    if step_jobs.get("voice"):
        try:
            async with factory() as session:
                await complete_step(session, step_jobs["voice"])
        except Exception:
            pass

    # ── Step 4: Generate test talking video via InfiniteTalk (75%) ──
    if step_jobs.get("render"):
        try:
            async with factory() as session:
                await mark_in_progress(session, step_jobs["render"])
        except Exception:
            pass
    sentry_sdk.add_breadcrumb(category='digital_pipeline', message='Step 4: Generating talking video via InfiniteTalk',
        data={'avatar_id': avatar_id}, level='info')
    await _update_progress(avatar_id, "Rendering talking video...", 65)

    test_video_key = f"creators/{user_id}/avatar/{avatar_id}/test_video.mp4"
    face_url = r2.get_signed_url(face_key, expires_in=7200)
    audio_url = await _prepare_preview_lipsync_audio(
        r2=r2,
        lipsync_audio_key=lipsync_audio_key,
        avatar_id=avatar_id,
    )

    # ── InfiniteTalk via RunPod ONLY (no GPU server) ──
    video_uploaded = False
    job_id = await runpod.submit_video_job(
        image_url=face_url,
        audio_url=audio_url,
        prompt=f"A {voice_style} live stream presenter talking to the camera in a {background.replace('_', ' ')} setting",
        size="480p",
    )

    await _update_progress(avatar_id, "Rendering video...", 75)

    # Estimate TTS output duration (~5s for test script). The state-machine
    # in wait_for_completion uses this to set the stall threshold; no outer
    # timeout — Celery task_time_limit is the failsafe per project rule
    # "No hardcoded render timeouts".
    tts_duration = len(TEST_SCRIPT.split()) / 2.5  # rough WPM estimate
    logger.info(f"InfiniteTalk TTS duration estimate: {tts_duration:.1f}s from {len(TEST_SCRIPT.split())} words")
    result = await runpod.wait_for_completion(
        job_id,
        poll_interval=5,
        audio_duration_s=tts_duration,
        quality="480p",
    )
    output = result.get("output")

    # Download generated video and upload to R2
    if output and isinstance(output, dict):
        # Public endpoint: output.result = CloudFront video URL
        # Private endpoint: output.video = base64 encoded video
        video_download_url = output.get("result") or output.get("video_url") or output.get("video_path")
        video_base64 = output.get("video")

        if video_download_url and video_download_url.startswith("http"):
            logger.info(f"Downloading video from {video_download_url[:80]}...")
            # tempfile is imported at module level; do NOT re-import locally
            # (would shadow the module name and break any earlier reference
            # in the same function).
            import httpx
            import subprocess
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                video_resp = await client.get(video_download_url)
                video_resp.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as raw_f:
                    raw_f.write(video_resp.content)
                    raw_path = raw_f.name
                fast_path = raw_path.replace(".mp4", "_fast.mp4")
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", raw_path, "-c", "copy", "-movflags", "+faststart", fast_path],
                        capture_output=True, timeout=30
                    )
                    with open(fast_path, "rb") as f:
                        fast_bytes = f.read()
                    await r2.upload_bytes(fast_bytes, test_video_key, "video/mp4")
                    logger.info(f"Video (faststart) uploaded: {test_video_key} ({len(fast_bytes)} bytes)")
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logger.warning(f"ffmpeg faststart failed ({e}), uploading raw")
                    await r2.upload_bytes(video_resp.content, test_video_key, "video/mp4")
                finally:
                    for p in [raw_path, fast_path]:
                        try:
                            os.unlink(p)
                        except OSError:
                            pass
                video_uploaded = True
        elif video_base64:
            video_data = video_base64
            if video_data.startswith("data:"):
                video_data = video_data.split(",", 1)[1]
            video_bytes = base64.b64decode(video_data)
            await r2.upload_bytes(video_bytes, test_video_key, "video/mp4")
            video_uploaded = True
            logger.info(f"Video (base64) uploaded to R2: {test_video_key} ({len(video_bytes)} bytes)")

    if not video_uploaded:
        logger.warning(f"No video data in InfiniteTalk output for avatar {avatar_id}. Output: {str(output)[:200]}")

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if video_uploaded:
            avatar.test_video_key = test_video_key
        await session.commit()

    # Mark render complete (Phase F tracking)
    if step_jobs.get("render"):
        try:
            async with factory() as session:
                await complete_step(session, step_jobs["render"])
        except Exception:
            pass

    # ── Step 5: Build persona + mark ready (100%) ──
    sentry_sdk.add_breadcrumb(category='digital_pipeline', message='Step 5: Finalizing avatar profile',
        data={'avatar_id': avatar_id, 'video_uploaded': video_uploaded}, level='info')
    await _update_progress(avatar_id, "Finalizing avatar profile...", 90)

    persona_presets = {
        "energetic_beauty": {
            "tone": "energetic, bubbly",
            "energy_level": "high",
            "catchphrases": ["oh my god you guys", "this is amazing", "you need this"],
            "vocabulary_level": "casual",
            "selling_style": "enthusiastic product demos with personal stories",
            "pacing": "fast, short sentences",
            "greeting_style": "Hey everyone! Welcome back!",
            "closing_style": "Don't forget to follow and tap that basket!",
        },
        "calm_tech": {
            "tone": "calm, analytical, trustworthy",
            "energy_level": "medium",
            "catchphrases": ["let me show you", "here's the thing", "what I love about this"],
            "vocabulary_level": "professional",
            "selling_style": "detailed technical analysis with honest pros/cons",
            "pacing": "measured, thorough",
            "greeting_style": "Welcome everyone. Let's take a look at...",
            "closing_style": "Thanks for watching. Link in the description.",
        },
        "friendly_lifestyle": {
            "tone": "warm, relatable, chatty",
            "energy_level": "medium-high",
            "catchphrases": ["okay so", "I'm obsessed", "game changer"],
            "vocabulary_level": "conversational",
            "selling_style": "lifestyle integration, showing products in daily routines",
            "pacing": "natural, storytelling",
            "greeting_style": "Hey guys! Happy to be here with you all!",
            "closing_style": "Love you all, see you next time!",
        },
        "professional_presenter": {
            "tone": "polished, confident, authoritative",
            "energy_level": "medium",
            "catchphrases": ["let me walk you through", "here's why", "the key benefit"],
            "vocabulary_level": "professional",
            "selling_style": "structured presentation with clear value propositions",
            "pacing": "steady, clear articulation",
            "greeting_style": "Good day, everyone. Thank you for joining us.",
            "closing_style": "Thank you for your time. Don't miss these deals.",
        },
    }

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.persona_profile = persona_presets.get(persona_preset, persona_presets["energetic_beauty"])
        avatar.status = AvatarStatus.READY
        avatar.active_phase = AvatarPhase.READY
        avatar.progress_step = "Avatar ready — review your test video"
        avatar.progress_percent = 100
        await session.commit()

    logger.info(json.dumps({
        "service": "generate_avatar", "level": "info",
        "message": "Digital avatar pipeline complete",
        "avatar_id": avatar_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))


# ═══════════════════════════════════════════════════════════════════════
# CLONE FROM TIKTOK PIPELINE — Two-phase split
#  Phase 1 (extract_candidates_task): fetch → extract faces → vision filter → upload candidates → CANDIDATES_READY
#  Phase 2 (generate_from_selection_task): voice clone → persona → TTS → InfiniteTalk → READY
# ═══════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.clone")
def clone_avatar_task(self, avatar_id: str, user_id: str, tiktok_url: str = None, video_keys: list = None):
    """Phase 1: fetch videos → extract face candidates → vision filter → upload → candidates_ready."""
    sentry_sdk.set_tag("avatar_id", avatar_id)
    sentry_sdk.set_tag("user_id", user_id)
    sentry_sdk.set_context("avatar", {"avatar_id": avatar_id, "user_id": user_id, "pipeline": "clone_phase1"})
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_extract_candidates_pipeline(avatar_id, user_id, tiktok_url, video_keys))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Clone avatar pipeline (phase 1) failed: {exc}", exc_info=True)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_failed(avatar_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=60)
    finally:
        loop.close()


# Alias for clarity
extract_candidates_task = clone_avatar_task


async def _extract_candidates_pipeline(avatar_id: str, user_id: str, tiktok_url: str = None, video_keys: list = None):
    """Phase 1: fetch TikTok → download videos to R2 → return video URLs for frontend scrubber.

    No more MediaPipe/OpenCV/Vision LLM. The user picks the frame in the browser.
    """
    from models.avatar import Avatar, AvatarStatus
    from services.r2_storage import get_r2_storage_service

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    # Check if user already uploaded a face photo
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        has_own_face = avatar and avatar.face_ref_key

    if has_own_face:
        # User provided their own photo — skip to phase 2 directly
        logger.info(f"User-uploaded face for {avatar_id} — skipping video scrubber")
        from tasks.generate_avatar import generate_from_selection_task
        generate_from_selection_task.delay(avatar_id, user_id)
        return

    if not tiktok_url:
        # No TikTok URL and no uploaded face — can't proceed
        await _mark_failed(avatar_id, "Please provide a TikTok URL or upload a face photo.")
        return

    # ── Step 1: Fetch TikTok video list via Apify ──
    sentry_sdk.add_breadcrumb(category='clone_pipeline', message='Phase 1: Fetching TikTok videos via Apify',
        data={'avatar_id': avatar_id, 'tiktok_url': tiktok_url}, level='info')
    await _update_progress(avatar_id, "Fetching TikTok videos...", 10)
    videos = None
    try:
        from services.apify_tiktok import get_apify_tiktok_service
        videos = await get_apify_tiktok_service().fetch_tiktok_videos(tiktok_url)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        sentry_sdk.add_breadcrumb(category='clone_pipeline', message=f'TikTok fetch failed: {e}',
            data={'avatar_id': avatar_id}, level='error')
        logger.warning(f"TikTok fetch failed: {e}")
        await _mark_failed(avatar_id, "Could not access TikTok profile. The account may be private, banned, or the URL incorrect.")
        return

    if not videos or len(videos) == 0:
        await _mark_failed(avatar_id, "TikTok returned no content. The account may be private or empty.")
        return

    # ── Step 2: Download TikTok videos via yt-dlp and upload to R2 ──
    await _update_progress(avatar_id, "Downloading TikTok videos...", 20)
    import subprocess

    video_cdn_urls = []
    for i, v in enumerate(videos[:3]):  # max 3 videos
        video_url = v.get("video_download_url", "") or v.get("video_url", "")
        if not video_url:
            continue

        tmp_video = os.path.join(tempfile.gettempdir(), f"scrubber_{avatar_id}_{i}.mp4")
        try:
            # Download video via yt-dlp
            is_tiktok_page = "tiktok.com" in video_url
            if is_tiktok_page:
                result = subprocess.run(
                    ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
                     "--no-playlist", "--max-filesize", "50M",
                     "-o", tmp_video, "--no-warnings", "--quiet",
                     video_url],
                    capture_output=True, timeout=60,
                )
                if result.returncode != 0 or not os.path.exists(tmp_video):
                    logger.warning(f"yt-dlp failed for video {i}: {result.stderr.decode()[:200]}")
                    continue
            else:
                import httpx
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    resp = await client.get(video_url)
                    resp.raise_for_status()
                    with open(tmp_video, "wb") as f:
                        f.write(resp.content)

            # Run ffmpeg faststart for browser streaming
            fast_path = tmp_video.replace(".mp4", "_fast.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_video, "-c", "copy",
                 "-movflags", "+faststart", fast_path],
                capture_output=True, timeout=30,
            )
            upload_path = fast_path if os.path.exists(fast_path) else tmp_video

            # Upload to R2 — store R2 key, not CDN URL (B-068)
            r2_key = f"creators/{user_id}/avatar/{avatar_id}/scrubber_video_{i}.mp4"
            await r2.upload_file(upload_path, r2_key, content_type="video/mp4")
            video_cdn_urls.append(r2_key)
            logger.info(f"Video {i} uploaded for scrubber: {r2_key} ({os.path.getsize(upload_path)} bytes)")

        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Failed to download/upload video {i}: {e}")
        finally:
            for p in [tmp_video, tmp_video.replace(".mp4", "_fast.mp4")]:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    if not video_cdn_urls:
        await _mark_failed(avatar_id, "Could not download any TikTok videos. Try a different creator or upload a photo instead.")
        return

    # ── Step 3: Set status to CANDIDATES_READY with video URLs ──
    # We reuse candidate_frames to store video URLs for the frontend scrubber
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.candidate_frames = video_cdn_urls  # Video URLs for scrubber
        avatar.tiktok_source_url = tiktok_url
        avatar.status = AvatarStatus.CANDIDATES_READY
        avatar.progress_step = "Scrub through the video and capture your best frame"
        avatar.progress_percent = 25
        await session.commit()

    logger.info(json.dumps({
        "service": "generate_avatar", "level": "info",
        "message": f"Videos ready for scrubber — {len(video_cdn_urls)} videos",
        "avatar_id": avatar_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))


# ═══════════════════════════════════════════════════════════════════════
# PHASE 2 — Generate from user's frame selection
# ═══════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.generate_from_selection", time_limit=5400, soft_time_limit=5100)
def generate_from_selection_task(self, avatar_id: str, user_id: str):
    """Phase 2: voice clone → persona → TTS → InfiniteTalk → ready.
    Called after user selects a face frame via the select-frame endpoint.
    """
    sentry_sdk.set_tag("avatar_id", avatar_id)
    sentry_sdk.set_tag("user_id", user_id)
    sentry_sdk.set_context("avatar", {"avatar_id": avatar_id, "user_id": user_id, "pipeline": "clone_phase2"})
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_generate_from_selection_pipeline(avatar_id, user_id))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Generate-from-selection pipeline failed: {exc}", exc_info=True)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_failed(avatar_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=60)
    finally:
        loop.close()


async def _generate_from_selection_pipeline(avatar_id: str, user_id: str):
    """Phase 2 pipeline: voice clone → persona → test audio → test video → ready."""
    from models.avatar import Avatar, AvatarStatus, AvatarType, AvatarPhase
    from services.fish_audio import get_fish_audio_service
    from services.openrouter import get_openrouter_service
    from services.r2_storage import get_r2_storage_service
    from services.runpod import get_runpod_service
    from config import settings

    factory = _make_session_factory()
    fish = get_fish_audio_service()
    openrouter = get_openrouter_service()
    r2 = get_r2_storage_service()
    runpod = get_runpod_service()

    # Load avatar data
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if not avatar:
            return
        face_key = avatar.face_ref_key
        tiktok_url = avatar.tiktok_source_url
        voice_sample_key = avatar.voice_sample_key
        voice_id = avatar.voice_id  # May already be set by parallel voice pipeline

    # If no voice_sample_key on the avatar, check creator_voice_corpus for uploaded samples
    if not voice_sample_key and not voice_id:
        try:
            from models.voice_corpus import VoiceCorpusEntry
            from sqlalchemy import select
            async with factory() as session:
                stmt = select(VoiceCorpusEntry).where(
                    VoiceCorpusEntry.avatar_id == avatar_id,
                    VoiceCorpusEntry.status == "ready",
                    VoiceCorpusEntry.audio_r2_key.isnot(None),
                ).order_by(VoiceCorpusEntry.duration_seconds.desc()).limit(1)
                corpus = (await session.execute(stmt)).scalar_one_or_none()
                if corpus:
                    voice_sample_key = corpus.audio_r2_key
                    logger.info(f"Using voice corpus entry {corpus.id} as voice sample: {voice_sample_key}")
                    # Also persist to avatar for future reference
                    avatar_obj = await session.get(Avatar, avatar_id)
                    if avatar_obj:
                        avatar_obj.voice_sample_key = voice_sample_key
                        await session.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Failed to load voice corpus: {e}")

    if not face_key:
        await _mark_failed(avatar_id, "No face image selected — cannot continue pipeline")
        return

    # Fetch TikTok videos again for voice cloning / persona analysis
    videos = None
    if tiktok_url:
        try:
            from services.apify_tiktok import get_apify_tiktok_service
            videos = await get_apify_tiktok_service().fetch_tiktok_videos(tiktok_url)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"TikTok re-fetch failed (phase 2): {e}")

    # ── Body angle extraction (Session E) ──
    try:
        from services.body_angle_extractor import extract_body_angles, crop_body_portrait, PoseAngle
        from models.avatar_look import AvatarLook
        import uuid

        logger.info("Starting body angle extraction for avatar %s", avatar_id)
        await _update_progress(avatar_id, "Extracting body angles from video...", 42)

        # Download first scrubber video from R2 for body angle analysis
        scrubber_key = f"creators/{user_id}/avatar/{avatar_id}/scrubber_video_0.mp4"
        body_tmp_video = os.path.join(tempfile.gettempdir(), f"body_angles_{avatar_id}.mp4")
        try:
            await r2.download_file(scrubber_key, body_tmp_video)
        except Exception:
            body_tmp_video = None
            logger.info("No scrubber video available for body angle extraction")

        if body_tmp_video and os.path.exists(body_tmp_video):
            angle_results = extract_body_angles(body_tmp_video, every_nth_frame=15)

            async with factory() as session:
                for pose_angle, angle_frame in angle_results.items():
                    # Skip front — we already have face_ref_key for front
                    if pose_angle == PoseAngle.FRONT:
                        continue

                    # Check if this angle already exists
                    from sqlalchemy import select as sa_select
                    existing = await session.execute(
                        sa_select(AvatarLook)
                        .where(AvatarLook.avatar_id == avatar_id)
                        .where(AvatarLook.look_type == "body_motion")
                        .where(AvatarLook.pose_angle == pose_angle.value)
                    )
                    if existing.scalar_one_or_none():
                        continue

                    # Crop to body portrait
                    cropped = crop_body_portrait(angle_frame.image)
                    _, jpeg_bytes = cv2.imencode(".jpg", cropped, [cv2.IMWRITE_JPEG_QUALITY, 95])

                    # Upload to R2
                    look_id = f"al_{uuid.uuid4().hex[:12]}"
                    r2_key = f"creators/{user_id}/avatars/{avatar_id}/looks/{look_id}.jpg"
                    await r2.upload_bytes(jpeg_bytes.tobytes(), r2_key, content_type="image/jpeg")

                    # Create AvatarLook record
                    look = AvatarLook(
                        id=look_id,
                        avatar_id=avatar_id,
                        name=f"Auto: {pose_angle.value.replace('_', ' ').title()}",
                        face_ref_key=r2_key,
                        look_type="body_motion",
                        pose_angle=pose_angle.value,
                        is_default=False,
                        status="ready",
                    )
                    session.add(look)

                await session.commit()

            angles_found = [pa.value for pa in angle_results.keys() if pa != PoseAngle.FRONT]
            logger.info(
                "Body angle extraction complete for avatar %s: %d angles found (%s)",
                avatar_id, len(angles_found), angles_found,
            )

            # Clean up temp file
            try:
                os.unlink(body_tmp_video)
            except OSError:
                pass

    except Exception as body_err:
        logger.warning(
            "Body angle extraction failed for avatar %s: %s. Continuing without body angles.",
            avatar_id, body_err,
        )

    # ── Step 3: Clone voice (50%) ──
    sentry_sdk.add_breadcrumb(category='clone_pipeline', message='Phase 2 Step 3: Cloning voice',
        data={'avatar_id': avatar_id, 'has_voice_id': bool(voice_id), 'has_voice_sample': bool(voice_sample_key)}, level='info')
    await _update_progress(avatar_id, "Cloning voice...", 45)

    if voice_id:
        logger.info(f"Voice already cloned by parallel pipeline: {voice_id}")
        await _update_progress(avatar_id, "Using cloned voice...", 50)
    elif voice_sample_key:
        try:
            voice_url = r2.get_public_url(voice_sample_key)
            voice_id = await fish.clone_voice(voice_url, name=f"Clone of {tiktok_url or 'creator'}")
            logger.info(f"Voice cloned from user-uploaded sample: {voice_id}")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"VOICE CLONE FAILED (user sample): {e}", exc_info=True)

    # Wait for parallel voice pipeline if voice_id still not set
    if not voice_id:
        await _update_progress(avatar_id, "Waiting for voice pipeline...", 48)
        for _ in range(12):
            async with factory() as session:
                avatar = await session.get(Avatar, avatar_id)
                if avatar.voice_id:
                    voice_id = avatar.voice_id
                    break
            await asyncio.sleep(5)

    if not voice_id:
        async with factory() as session:
            avatar = await session.get(Avatar, avatar_id)
            if avatar.type == AvatarType.CLONE:
                await _mark_failed(avatar_id, "Voice cloning failed. Please try again with a different video.")
                return
            else:
                # AI avatars can use preset voices
                voice_style = avatar.voice_style or "friendly"
                voice_id = VOICE_STYLE_MAP.get(voice_style, VOICE_STYLE_MAP["friendly"])
                await _update_progress(avatar_id, "Voice cloning skipped — using default voice", 50)

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.voice_id = voice_id
        await session.commit()

    # ── Step 4: Analyze persona (60%) ──
    sentry_sdk.add_breadcrumb(category='clone_pipeline', message='Phase 2 Step 4: Analyzing persona',
        data={'avatar_id': avatar_id, 'voice_id': voice_id}, level='info')
    await _update_progress(avatar_id, "Analyzing creator persona...", 60)
    if videos:
        try:
            transcripts = [v.get("description", "") for v in videos if v.get("description")]
            if transcripts:
                persona = await openrouter.analyze_persona(transcripts)
                async with factory() as session:
                    avatar = await session.get(Avatar, avatar_id)
                    avatar.persona_profile = persona
                    await session.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Persona analysis failed: {e}")

    # ── Step 5: Generate test audio with cloned voice (75%) ──
    sentry_sdk.add_breadcrumb(category='clone_pipeline', message='Phase 2 Step 5: Generating test audio with cloned voice',
        data={'avatar_id': avatar_id, 'voice_id': voice_id}, level='info')
    await _update_progress(avatar_id, "Generating test audio with cloned voice...", 75)

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        # Source-of-truth order: locked_test_script (explicit user lock from
        # the Voice step) > test_script (latest write from any path) > the
        # hardcoded TEST_SCRIPT fallback. Without this preference, the user's
        # custom voice line would lose to whatever auto-generated greeting
        # was last written to test_script during voice description generation.
        script_to_speak = avatar.locked_test_script or avatar.test_script or TEST_SCRIPT

    tts_result = await fish.generate_tts(text=script_to_speak, voice_id=voice_id)
    test_audio_key, lipsync_audio_key = _resolve_preview_audio_keys(
        tts_result,
        user_id=user_id,
        avatar_id=avatar_id,
    )
    tmp_path = tts_result["tmp_path"]
    try:
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.test_audio_key = test_audio_key
        avatar.test_script = script_to_speak
        await session.commit()

    # ── Step 6: Generate test talking video via InfiniteTalk (90%) ──
    sentry_sdk.add_breadcrumb(category='clone_pipeline', message='Phase 2 Step 6: Generating talking video via InfiniteTalk',
        data={'avatar_id': avatar_id}, level='info')
    await _update_progress(avatar_id, "Rendering talking video...", 85)

    test_video_key = f"creators/{user_id}/avatar/{avatar_id}/test_video.mp4"
    face_url = r2.get_signed_url(face_key, expires_in=7200)
    audio_url = await _prepare_preview_lipsync_audio(
        r2=r2,
        lipsync_audio_key=lipsync_audio_key,
        avatar_id=avatar_id,
    )

    # ── InfiniteTalk: HOSTKEY first, RunPod fallback ──
    # The RunPod `triazevwb6a8ap` template started failing fast (<60s) with
    # "비디오를 찾을 수 없습니다." ("Video not found") around early May 2026,
    # blocking every preview render. HOSTKEY runs the same InfiniteTalk model
    # at /api/infinitetalk-render and is already used by render_dispatcher
    # for cast block renders, so we try it first and only fall back to RunPod
    # if HOSTKEY is unreachable or returns no video.
    video_uploaded = False
    hostkey_error: str | None = None
    runpod_error: str | None = None

    import httpx
    import subprocess

    # Try HOSTKEY first — same endpoint and payload schema render_dispatcher uses.
    try:
        from services.render_dispatcher import (
            HOSTKEY_URL,
            HOSTKEY_RENDER_ENABLED,
            HOSTKEY_TIMEOUT,
        )
        if HOSTKEY_RENDER_ENABLED:
            logger.info("Preview render: trying HOSTKEY InfiniteTalk first")
            async with httpx.AsyncClient(timeout=HOSTKEY_TIMEOUT) as client:
                resp = await client.post(
                    f"{HOSTKEY_URL}/api/infinitetalk-render",
                    json={
                        "image_url": face_url,
                        "wav_url": audio_url,
                        "prompt": "A person talking naturally to the camera on a live stream",
                        "width": 480,
                        "height": 854,
                    },
                )
                if resp.status_code == 503:
                    raise RuntimeError("HOSTKEY GPU busy (503)")
                resp.raise_for_status()
                hostkey_result = resp.json()
            # HOSTKEY contract (see render_dispatcher._render_on_hostkey):
            # response is JSON {"video": <base64-encoded mp4>}.
            video_b64 = hostkey_result.get("video") if isinstance(hostkey_result, dict) else None
            if video_b64:
                video_data = video_b64
                if video_data.startswith("data:"):
                    video_data = video_data.split(",", 1)[1]
                video_bytes = base64.b64decode(video_data)
                await r2.upload_bytes(video_bytes, test_video_key, "video/mp4")
                video_uploaded = True
                logger.info(
                    "Preview render: HOSTKEY success (~%d bytes)", len(video_bytes)
                )
            else:
                hostkey_error = f"HOSTKEY returned no video field: {str(hostkey_result)[:200]}"
                logger.warning("Preview render: %s", hostkey_error)
        else:
            hostkey_error = "HOSTKEY_RENDER_ENABLED=false"
            logger.info("Preview render: HOSTKEY disabled, going straight to RunPod")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        hostkey_error = f"HOSTKEY failed: {e!s}"
        logger.warning("Preview render: HOSTKEY failed, falling back to RunPod: %s", e)

    # Fall back to RunPod only if HOSTKEY didn't produce a video.
    if not video_uploaded:
        logger.info(
            "Preview render: falling back to RunPod (HOSTKEY error: %s)", hostkey_error
        )
        try:
            job_id = await runpod.submit_video_job(
                image_url=face_url,
                audio_url=audio_url,
                prompt="A person talking naturally to the camera on a live stream",
                size="480p",
            )

            await _update_progress(avatar_id, "Rendering video...", 90)

            # Estimate TTS output duration from script length. State-machine drives
            # stall detection; Celery task_time_limit is the only outer timeout.
            tts_duration = len(script_to_speak.split()) / 2.5
            logger.info(f"InfiniteTalk TTS duration estimate: {tts_duration:.1f}s from {len(script_to_speak.split())} words")
            result = await runpod.wait_for_completion(
                job_id,
                poll_interval=5,
                audio_duration_s=tts_duration,
                quality="480p",
            )
            output = result.get("output")

            # Download generated video and upload to R2
            if output and isinstance(output, dict):
                video_download_url = output.get("result") or output.get("video_url") or output.get("video_path")
                video_base64 = output.get("video")

                if video_download_url and video_download_url.startswith("http"):
                    logger.info(f"Downloading clone video from {video_download_url[:80]}...")
                    # NOTE: do NOT re-import tempfile locally here. There's an earlier
                    # use of tempfile.gettempdir() inside the body-angle-extraction
                    # block in this same function (~line 766). A local `import
                    # tempfile` makes tempfile a function-scoped name, so the earlier
                    # reference fails with `cannot access local variable 'tempfile'`.
                    # Use the module-level imports (tempfile is imported at the top
                    # of this file).
                    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                        video_resp = await client.get(video_download_url)
                        video_resp.raise_for_status()
                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as raw_f:
                            raw_f.write(video_resp.content)
                            raw_path = raw_f.name
                        fast_path = raw_path.replace(".mp4", "_fast.mp4")
                        try:
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", raw_path, "-c", "copy", "-movflags", "+faststart", fast_path],
                                capture_output=True, timeout=30
                            )
                            with open(fast_path, "rb") as f:
                                fast_bytes = f.read()
                            await r2.upload_bytes(fast_bytes, test_video_key, "video/mp4")
                            logger.info(f"Clone video (faststart) uploaded: {test_video_key} ({len(fast_bytes)} bytes)")
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                            logger.warning(f"ffmpeg faststart failed ({e}), uploading raw")
                            await r2.upload_bytes(video_resp.content, test_video_key, "video/mp4")
                        finally:
                            for p in [raw_path, fast_path]:
                                try:
                                    os.unlink(p)
                                except OSError:
                                    pass
                        video_uploaded = True
                elif video_base64:
                    video_data = video_base64
                    if video_data.startswith("data:"):
                        video_data = video_data.split(",", 1)[1]
                    video_bytes = base64.b64decode(video_data)
                    await r2.upload_bytes(video_bytes, test_video_key, "video/mp4")
                    video_uploaded = True

            if not video_uploaded:
                runpod_error = f"No video data in RunPod output: {str(output)[:200]}"
                logger.warning(
                    f"No video data in InfiniteTalk output for clone {avatar_id}. Output: {str(output)[:200]}"
                )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            runpod_error = f"RunPod failed: {e!s}"
            logger.warning("Preview render: RunPod fallback also failed: %s", e)

    if not video_uploaded:
        # Both engines failed — surface a single clear error. RunPod's Korean
        # text was already translated by services.runpod.normalize_error_message
        # before the RuntimeError reached us.
        raise RuntimeError(
            f"Preview render failed on both engines. HOSTKEY: {hostkey_error}. RunPod: {runpod_error}"
        )

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if video_uploaded:
            avatar.test_video_key = test_video_key
        await session.commit()

    # ── Step 7: Ready for review (100%) ──
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.status = AvatarStatus.READY
        avatar.active_phase = AvatarPhase.READY
        avatar.progress_step = "Avatar ready — review your test video"
        avatar.progress_percent = 100
        await session.commit()

    logger.info(json.dumps({
        "service": "generate_avatar", "level": "info",
        "message": "Clone avatar pipeline (phase 2) complete",
        "avatar_id": avatar_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))


async def _extract_audio_to_local(video_url: str, avatar_id: str) -> str | None:
    """Download single TikTok video via yt-dlp, extract audio to local WAV.
    Returns the local file path to the WAV, or None on failure.
    Caller is responsible for cleanup.
    """
    import subprocess
    tmp_video = os.path.join(tempfile.gettempdir(), f"voice_extract_{avatar_id}.mp4")
    tmp_audio = os.path.join(tempfile.gettempdir(), f"voice_extract_{avatar_id}.wav")
    try:
        is_tiktok_page = "tiktok.com" in video_url
        if is_tiktok_page:
            result = subprocess.run(
                ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
                 "--no-playlist", "--max-filesize", "50M",
                 "-o", tmp_video, "--no-warnings", "--quiet", video_url],
                capture_output=True, timeout=60)
            if result.returncode != 0 or not os.path.exists(tmp_video):
                logger.error(f"yt-dlp download failed: {result.stderr.decode()[:300]}")
                return None
        else:
            import httpx
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                with open(tmp_video, "wb") as f:
                    f.write(resp.content)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_video, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", tmp_audio],
            capture_output=True, timeout=30)
        if result.returncode != 0 or not os.path.exists(tmp_audio) or os.path.getsize(tmp_audio) < 1000:
            logger.error(f"ffmpeg audio extraction failed: {result.stderr.decode()[:300]}")
            return None
        logger.info(f"Audio extracted: {tmp_audio} ({os.path.getsize(tmp_audio)} bytes)")
        return tmp_audio
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Audio extraction failed: {e}", exc_info=True)
        return None
    finally:
        try:
            os.unlink(tmp_video)
        except OSError:
            pass


async def _extract_multi_video_audio(videos: list, avatar_id: str, max_duration_s: int = 120) -> str | None:
    """Download up to 3 TikTok videos, extract audio from each, concatenate, trim silence.

    Returns path to a clean WAV file (mono, 44.1kHz, PCM 16-bit, up to max_duration_s).
    Caller is responsible for cleanup.
    """
    import subprocess

    audio_parts = []
    tmp_files = []

    try:
        # Download and extract audio from up to 3 videos
        for i, v in enumerate(videos[:3]):
            video_url = v.get("video_download_url", "") or v.get("video_url", "")
            if not video_url:
                continue

            tmp_video = os.path.join(tempfile.gettempdir(), f"voice_multi_{avatar_id}_{i}.mp4")
            tmp_audio = os.path.join(tempfile.gettempdir(), f"voice_multi_{avatar_id}_{i}.wav")
            tmp_files.extend([tmp_video, tmp_audio])

            # Download
            is_tiktok = "tiktok.com" in video_url
            if is_tiktok:
                result = subprocess.run(
                    ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
                     "--no-playlist", "--max-filesize", "50M",
                     "-o", tmp_video, "--no-warnings", "--quiet", video_url],
                    capture_output=True, timeout=60)
                if result.returncode != 0 or not os.path.exists(tmp_video):
                    logger.warning(f"yt-dlp failed for video {i}: {result.stderr.decode()[:200]}")
                    continue
            else:
                import httpx
                async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                    resp = await client.get(video_url)
                    resp.raise_for_status()
                    with open(tmp_video, "wb") as f:
                        f.write(resp.content)

            # Extract audio as WAV
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_video, "-vn",
                 "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", tmp_audio],
                capture_output=True, timeout=30)
            if result.returncode == 0 and os.path.exists(tmp_audio) and os.path.getsize(tmp_audio) > 1000:
                audio_parts.append(tmp_audio)
                logger.info(f"Audio part {i}: {os.path.getsize(tmp_audio)} bytes")
            else:
                logger.warning(f"ffmpeg extraction failed for video {i}")

        if not audio_parts:
            logger.error(f"No audio extracted from any video for {avatar_id}")
            return None

        # Concatenate audio parts
        concat_path = os.path.join(tempfile.gettempdir(), f"voice_concat_{avatar_id}.wav")
        tmp_files.append(concat_path)

        if len(audio_parts) == 1:
            # Single file — just use it
            concat_path = audio_parts[0]
        else:
            # Create concat list file for ffmpeg
            list_path = os.path.join(tempfile.gettempdir(), f"voice_list_{avatar_id}.txt")
            tmp_files.append(list_path)
            with open(list_path, "w") as f:
                for part in audio_parts:
                    f.write(f"file '{part}'\n")
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                 "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", concat_path],
                capture_output=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"ffmpeg concat failed: {result.stderr.decode()[:300]}")
                return None

        # Trim silence + limit to max_duration_s
        final_path = os.path.join(tempfile.gettempdir(), f"voice_final_{avatar_id}.wav")
        tmp_files.append(final_path)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", concat_path,
             "-af", "silenceremove=start_periods=1:start_silence=0.5:start_threshold=-40dB:"
                    "stop_periods=-1:stop_silence=0.5:stop_threshold=-40dB",
             "-t", str(max_duration_s),
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
             final_path],
            capture_output=True, timeout=30)
        if result.returncode != 0 or not os.path.exists(final_path) or os.path.getsize(final_path) < 1000:
            logger.warning(f"Silence trim failed, using concat directly: {result.stderr.decode()[:200]}")
            final_path = concat_path  # Fallback to untrimmed

        final_size = os.path.getsize(final_path)
        duration_est = final_size / (44100 * 2)  # 16-bit mono = 2 bytes/sample
        logger.info(f"Final audio for voice clone: {final_path} ({final_size} bytes, ~{duration_est:.1f}s)")
        return final_path

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Multi-video audio extraction failed: {e}", exc_info=True)
        return None
    finally:
        # Clean up intermediate files (except the final output)
        for p in tmp_files:
            if p != final_path:  # type: ignore
                try:
                    os.unlink(p)
                except (OSError, NameError):
                    pass


async def _run_audio_processing(local_wav_path: str, avatar_id: str, user_id: str, r2, pass_label: str = "") -> dict | None:
    """Send audio to RunPod worker for advanced processing.

    Pipeline: vocal isolation (BS-RoFormer) → silence trim → loudnorm → segment selection → Whisper transcript

    Returns dict with 'vocals_path' (local WAV) and 'transcript' (str), or None.
    """
    import httpx
    from config import settings

    label = f" ({pass_label})" if pass_label else ""
    endpoint_id = settings.BS_ROFORMER_ENDPOINT_ID

    # Calculate audio duration for dynamic timeouts
    audio_duration = os.path.getsize(local_wav_path) / (44100 * 2)

    # Upload raw audio to R2 so BS-RoFormer vocal isolation worker can access it
    raw_key = f"creators/{user_id}/avatar/{avatar_id}/bs_roformer_input{pass_label.replace(' ', '_')}.wav"
    await r2.upload_file(local_wav_path, raw_key, content_type="audio/wav")
    raw_url = r2.get_public_url(raw_key)
    logger.info(f"BS-RoFormer{label} input uploaded: {raw_url}")

    output_key = f"creators/{user_id}/avatar/{avatar_id}/bs_roformer_vocals{pass_label.replace(chr(32), chr(95))}.wav"

    # ── Try 1: Dedicated GPU server (fastest, no cold start) ──
    from services.gpu_server import get_gpu_server_client
    gpu_client = get_gpu_server_client()
    if gpu_client:
        try:
            logger.info(f"BS-RoFormer{label}: trying dedicated GPU server")
            gpu_result = await gpu_client.bs_roformer(
                raw_url, output_key, max_duration=90,
                input_duration_seconds=audio_duration,
            )
            if gpu_result.get("vocals_url"):
                local_clean = os.path.join(tempfile.gettempdir(), f"gpu_vocals_{avatar_id}{pass_label.replace(' ', '_')}.wav")
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                    resp = await dl_client.get(gpu_result["vocals_url"])
                    resp.raise_for_status()
                    with open(local_clean, "wb") as f:
                        f.write(resp.content)
                logger.info(f"BS-RoFormer{label} via GPU server complete: {local_clean}")
                try:
                    from services.usage_logger import log_api_usage
                    await log_api_usage(
                        user_id=user_id, service="bs_roformer", operation="vocal_isolation_gpu_server",
                        success=True, avatar_id=avatar_id,
                    )
                except Exception:
                    pass
                return {
                    "vocals_path": local_clean,
                    "transcript": gpu_result.get("transcript", ""),
                }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"GPU server BS-RoFormer{label} failed, falling back to RunPod: {e}")

    # ── Check: GPU server may have completed but response was lost ──
    try:
        if await r2.key_exists(output_key):
            logger.info(f"BS-RoFormer{label}: found result in R2 (GPU response was lost), using it")
            local_clean = os.path.join(tempfile.gettempdir(), f"r2_recovery_{avatar_id}{pass_label.replace(chr(32), chr(95))}.wav")
            await r2.download_file(output_key, local_clean)
            if os.path.exists(local_clean) and os.path.getsize(local_clean) > 1000:
                return {"vocals_path": local_clean, "transcript": ""}
    except Exception:
        pass  # R2 check failed, continue to RunPod

    # ── Check: GPU server may have completed but response was lost ──
    try:
        if await r2.key_exists(output_key):
            logger.info(f"BS-RoFormer{label}: found result in R2 (GPU response was lost), using it")
            local_clean = os.path.join(tempfile.gettempdir(), f"r2_recovery_{avatar_id}{pass_label.replace(chr(32), chr(95))}.wav")
            await r2.download_file(output_key, local_clean)
            if os.path.exists(local_clean) and os.path.getsize(local_clean) > 1000:
                return {"vocals_path": local_clean, "transcript": ""}
    except Exception:
        pass  # R2 check failed, continue to RunPod

    # ── Try 2: RunPod (dynamic timeouts) ──
    # Submit job to BS-RoFormer vocal isolation RunPod
    output_key = f"creators/{user_id}/avatar/{avatar_id}/bs_roformer_vocals{pass_label.replace(' ', '_')}.wav"
    runpod_key = settings.RUNPOD_API_KEY

    # Dynamic timeout based on audio duration
    render_time = audio_duration * settings.BS_ROFORMER_RENDER_RATIO
    cold_start_buffer = 90  # 48GB tier
    max_wait = render_time + cold_start_buffer + 60
    max_polls = max(18, int(max_wait / 5))
    logger.info(
        f"BS-RoFormer RunPod{label}: audio={audio_duration:.0f}s, "
        f"estimated render={render_time:.0f}s, max_wait={max_wait:.0f}s, max_polls={max_polls}"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"https://api.runpod.ai/v2/{endpoint_id}/run",
            headers={"Authorization": f"Bearer {runpod_key}"},
            json={"input": {"audio_url": raw_url, "output_key": output_key}},
        )
        resp.raise_for_status()
        job_data = resp.json()
        job_id = job_data.get("id")
        logger.info(f"BS-RoFormer{label} job submitted: {job_id}")

    # Poll for completion — state-aware timeouts (B-096) with dynamic max
    import asyncio
    poll_start = time.monotonic()
    queue_start = time.monotonic()  # tracks when we first saw IN_QUEUE
    progress_start = None           # tracks when job transitioned to IN_PROGRESS
    for _ in range(max_polls):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}",
                headers={"Authorization": f"Bearer {runpod_key}"},
            )
            status_data = resp.json()
            status = status_data.get("status")
            elapsed = int(time.monotonic() - poll_start)
            logger.info(f"BS-RoFormer{label} job {job_id}: {status} ({elapsed}s)")

            if status == "IN_QUEUE":
                queue_elapsed = time.monotonic() - queue_start
                if queue_elapsed > 60:
                    logger.warning(f"BS-RoFormer{label} job {job_id} stuck IN_QUEUE for {int(queue_elapsed)}s — timing out")
                    break
                est_remaining = max(30, 180 - elapsed)
                try:
                    await _update_voice_progress_step(
                        avatar_id,
                        f"Waiting for vocal isolation GPU... (~{est_remaining}s)",
                    )
                except Exception:
                    pass  # Non-critical, don't break the pipeline
            elif status == "IN_PROGRESS":
                if progress_start is None:
                    progress_start = time.monotonic()
                progress_elapsed = time.monotonic() - progress_start
                if progress_elapsed > 120:
                    logger.warning(f"BS-RoFormer{label} job {job_id} IN_PROGRESS for {int(progress_elapsed)}s — timing out")
                    break
                try:
                    await _update_voice_progress_step(
                        avatar_id,
                        f"Isolating vocals from audio... ({elapsed}s)",
                    )
                except Exception:
                    pass

            if status == "COMPLETED":
                output = status_data.get("output", {})
                vocals_url = output.get("vocals_url")
                if not vocals_url:
                    logger.warning(f"BS-RoFormer{label} completed but no vocals_url in output: {output}")
                    return None

                # Download cleaned vocals to local file
                suffix = pass_label.replace(" ", "_") if pass_label else ""
                local_clean = os.path.join(tempfile.gettempdir(), f"audio_clean_{avatar_id}{suffix}.wav")
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl:
                    dl_resp = await dl.get(vocals_url)
                    dl_resp.raise_for_status()
                    with open(local_clean, "wb") as f:
                        f.write(dl_resp.content)

                transcript = output.get("transcript", "")
                duration = output.get("duration_seconds", 0)
                logger.info(f"BS-RoFormer{label} complete: {local_clean} ({os.path.getsize(local_clean)} bytes, ~{duration}s)")
                try:
                    from services.usage_logger import log_api_usage
                    await log_api_usage(
                        user_id=user_id, service="bs_roformer", operation="vocal_isolation",
                        success=True, avatar_id=avatar_id, runpod_job_id=job_id,
                        duration_seconds=round(time.monotonic() - poll_start, 1),
                    )
                except Exception:
                    pass
                return {"vocals_path": local_clean, "transcript": transcript}

            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                error = status_data.get("output", {}).get("error", "unknown")
                logger.error(f"BS-RoFormer{label} job failed: {status} — {error}")
                try:
                    from services.usage_logger import log_api_usage
                    await log_api_usage(
                        user_id=user_id, service="bs_roformer", operation="vocal_isolation",
                        success=False, avatar_id=avatar_id, runpod_job_id=job_id,
                        duration_seconds=round(time.monotonic() - poll_start, 1),
                        error_message=f"{status}: {error}",
                    )
                except Exception:
                    pass
                return None

    elapsed_total = round(time.monotonic() - poll_start, 1)
    logger.warning(f"BS-RoFormer{label} job {job_id} timed out after {elapsed_total}s")
    try:
        from services.usage_logger import log_api_usage
        await log_api_usage(
            user_id=user_id, service="bs_roformer", operation="vocal_isolation",
            success=False, avatar_id=avatar_id, runpod_job_id=job_id,
            duration_seconds=elapsed_total,
            error_message=f"Timed out after {elapsed_total}s",
        )
    except Exception:
        pass
    return None


async def _run_vocal_isolation_cpu(local_wav_path: str, avatar_id: str, pass_label: str = "", user_id: str = "") -> dict | None:
    """CPU fallback: run BS-RoFormer vocal isolation locally via subprocess.

    Calls the `audio-separator` CLI installed in the Docker image.
    Slower than GPU (~2-3 min for 60s audio) but 100% reliable — no RunPod,
    no throttling, no cold starts.

    Returns dict with 'vocals_path' (local WAV), or None on failure.
    """
    import subprocess

    label = f" ({pass_label})" if pass_label else ""
    output_dir = os.path.join(tempfile.gettempdir(), f"cpu_sep_{avatar_id}_{pass_label}")
    os.makedirs(output_dir, exist_ok=True)

    cpu_start = time.monotonic()
    try:
        logger.info(f"BS-RoFormer CPU{label}: starting local vocal isolation on {local_wav_path}")
        await _update_voice_progress_step(
            avatar_id,
            "Isolating vocals on CPU... (this may take 1-2 minutes)",
        )

        loop = asyncio.get_event_loop()

        def _run():
            result = subprocess.run(
                [
                    "audio-separator", local_wav_path,
                    "--model_filename", "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
                    "--output_dir", output_dir,
                    "--output_format", "WAV",
                ],
                capture_output=True, timeout=300,  # 5 min max for CPU
            )
            return result

        result = await loop.run_in_executor(None, _run)

        if result.returncode != 0:
            logger.error(f"BS-RoFormer CPU{label} failed: {result.stderr.decode()[:300]}")
            try:
                from services.usage_logger import log_api_usage
                await log_api_usage(
                    user_id=user_id, service="bs_roformer", operation="vocal_isolation_cpu",
                    success=False, avatar_id=avatar_id,
                    duration_seconds=round(time.monotonic() - cpu_start, 1),
                    error_message=result.stderr.decode()[:200],
                )
            except Exception:
                pass
            return None

        # Find vocals file in output_dir
        vocals_path = None
        for f in os.listdir(output_dir):
            if "vocal" in f.lower() or "voice" in f.lower():
                vocals_path = os.path.join(output_dir, f)
                break
        # Fallback: take the first WAV file
        if not vocals_path:
            for f in os.listdir(output_dir):
                if f.endswith(".wav"):
                    vocals_path = os.path.join(output_dir, f)
                    break

        if vocals_path and os.path.exists(vocals_path) and os.path.getsize(vocals_path) > 1000:
            logger.info(f"BS-RoFormer CPU{label} complete: {vocals_path} ({os.path.getsize(vocals_path)} bytes)")
            try:
                from services.usage_logger import log_api_usage
                await log_api_usage(
                    user_id=user_id, service="bs_roformer", operation="vocal_isolation_cpu",
                    success=True, avatar_id=avatar_id,
                    duration_seconds=round(time.monotonic() - cpu_start, 1),
                )
            except Exception:
                pass
            return {"vocals_path": vocals_path, "transcript": ""}
        else:
            logger.warning(f"BS-RoFormer CPU{label}: no vocals output in {output_dir}")
            return None

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"BS-RoFormer CPU{label} failed: {e}")
        return None


async def _extract_and_upload_audio(video_url: str, avatar_id: str, user_id: str, r2) -> str | None:
    """Download TikTok video via yt-dlp, extract audio with ffmpeg, upload to R2.

    Returns the public R2 URL for the extracted audio, or None on failure.
    """
    import subprocess

    tmp_video = os.path.join(tempfile.gettempdir(), f"voice_extract_{avatar_id}.mp4")
    tmp_audio = os.path.join(tempfile.gettempdir(), f"voice_extract_{avatar_id}.wav")

    try:
        # Download video
        is_tiktok_page = "tiktok.com" in video_url
        if is_tiktok_page:
            result = subprocess.run(
                ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
                 "--no-playlist", "--max-filesize", "50M",
                 "-o", tmp_video, "--no-warnings", "--quiet",
                 video_url],
                capture_output=True, timeout=60,
            )
            if result.returncode != 0 or not os.path.exists(tmp_video):
                logger.warning(f"yt-dlp download failed for voice: {result.stderr.decode()[:200]}")
                return None
        else:
            import httpx
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                with open(tmp_video, "wb") as f:
                    f.write(resp.content)

        # Extract audio with ffmpeg — WAV format (PCM 16-bit, 44.1kHz, mono)
        # Fish Audio voice cloning works best with clean WAV files
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_video, "-vn",
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
             tmp_audio],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(tmp_audio) or os.path.getsize(tmp_audio) < 1000:
            logger.error(f"ffmpeg audio extraction failed: rc={result.returncode}, stderr={result.stderr.decode()[:300]}")
            return None

        audio_size = os.path.getsize(tmp_audio)
        logger.info(f"Audio extracted: {tmp_audio} ({audio_size} bytes, WAV 44.1kHz mono)")

        # Upload to R2
        audio_key = f"creators/{user_id}/avatar/{avatar_id}/voice_source.wav"
        await r2.upload_file(tmp_audio, audio_key, content_type="audio/wav")
        public_url = r2.get_public_url(audio_key)
        logger.info(f"Voice audio uploaded: {public_url}")
        return public_url

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Audio extraction/upload failed: {e}")
        return None
    finally:
        for p in [tmp_video, tmp_audio]:
            try:
                os.unlink(p)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════════════
# REGENERATE — Audio + Video only (reuses existing face)
# ═══════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.regenerate_video", time_limit=5400, soft_time_limit=5100)
def regenerate_avatar_video_task(self, avatar_id: str, user_id: str, test_script: str = ""):
    """Regenerate just the audio + video for an existing avatar (keeps face image)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_regenerate_pipeline(avatar_id, user_id, test_script))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Regenerate pipeline failed: {exc}", exc_info=True)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_failed(avatar_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=60)
    finally:
        loop.close()


async def _regenerate_pipeline(avatar_id: str, user_id: str, test_script: str):
    from models.avatar import Avatar, AvatarStatus, AvatarPhase
    from services.fish_audio import get_fish_audio_service
    from services.r2_storage import get_r2_storage_service
    from services.runpod import get_runpod_service

    factory = _make_session_factory()
    fish = get_fish_audio_service()
    r2 = get_r2_storage_service()
    runpod = get_runpod_service()

    # Get existing avatar data
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if not avatar:
            return
        face_key = avatar.face_ref_key
        voice_id = avatar.voice_id
        # Same preference order as _generate_from_selection_pipeline: explicit
        # task arg > locked_test_script > test_script > hardcoded fallback.
        script = test_script or avatar.locked_test_script or avatar.test_script or TEST_SCRIPT

    # If voice pipeline hasn't finished, try corpus-based cloning then wait
    if not voice_id:
        # Try to clone from voice corpus entries first
        try:
            from models.voice_corpus import VoiceCorpusEntry
            from sqlalchemy import select as sa_select
            async with factory() as session:
                stmt = sa_select(VoiceCorpusEntry).where(
                    VoiceCorpusEntry.avatar_id == avatar_id,
                    VoiceCorpusEntry.status == "ready",
                    VoiceCorpusEntry.audio_r2_key.isnot(None),
                ).order_by(VoiceCorpusEntry.duration_seconds.desc()).limit(1)
                corpus = (await session.execute(stmt)).scalar_one_or_none()
                if corpus and corpus.audio_r2_key:
                    logger.info(f"Found voice corpus for {avatar_id}, cloning...")
                    await _update_progress(avatar_id, "Cloning your voice...", 50)
                    voice_url = r2.get_public_url(corpus.audio_r2_key)
                    cloned_voice_id = await fish.clone_voice(voice_url)
                    if cloned_voice_id:
                        voice_id = cloned_voice_id
                        a = await session.get(Avatar, avatar_id)
                        if a:
                            a.voice_id = voice_id
                            a.voice_sample_key = corpus.audio_r2_key
                            await session.commit()
                        logger.info(f"Voice cloned from corpus: {voice_id}")
        except Exception as e:
            import sentry_sdk
            sentry_sdk.capture_exception(e)
            logger.warning(f"Corpus voice cloning failed: {e}")

    # Still no voice? Wait for parallel pipeline (up to 90 seconds)
    if not voice_id:
        logger.info(f"Voice not ready for {avatar_id}, waiting...")
        await _update_progress(avatar_id, "Waiting for voice cloning to finish...", 52)
        for attempt in range(18):
            await asyncio.sleep(5)
            async with factory() as session:
                avatar = await session.get(Avatar, avatar_id)
                if avatar.voice_id:
                    voice_id = avatar.voice_id
                    logger.info(f"Voice ready (attempt {attempt+1}): {voice_id}")
                    break
                # Re-check corpus in case it became ready
                try:
                    from models.voice_corpus import VoiceCorpusEntry
                    from sqlalchemy import select as sa_select
                    stmt = sa_select(VoiceCorpusEntry).where(
                        VoiceCorpusEntry.avatar_id == avatar_id,
                        VoiceCorpusEntry.status == "ready",
                        VoiceCorpusEntry.audio_r2_key.isnot(None),
                    ).limit(1)
                    corpus = (await session.execute(stmt)).scalar_one_or_none()
                    if corpus and corpus.audio_r2_key:
                        logger.info(f"Corpus ready during wait, cloning...")
                        voice_url = r2.get_public_url(corpus.audio_r2_key)
                        cloned_vid = await fish.clone_voice(voice_url)
                        if cloned_vid:
                            voice_id = cloned_vid
                            avatar.voice_id = voice_id
                            avatar.voice_sample_key = corpus.audio_r2_key
                            await session.commit()
                            logger.info(f"Voice cloned during wait: {voice_id}")
                            break
                except Exception as e2:
                    import sentry_sdk
                    sentry_sdk.capture_exception(e2)

    if not voice_id:
        await _mark_failed(avatar_id, "Voice cloning failed. Please try again with a video that has clearer speech.")
        return

    logger.info(f"Regenerating {avatar_id} with script ({len(script)} chars): {script[:100]}...")

    if not face_key:
        await _mark_failed(avatar_id, "No face image — cannot regenerate video")
        return

    # Step 1: Generate new audio
    await _update_progress(avatar_id, "Generating new voice audio...", 55)
    tts_result = await fish.generate_tts(text=script, voice_id=voice_id)
    test_audio_key, lipsync_audio_key = _resolve_preview_audio_keys(
        tts_result,
        user_id=user_id,
        avatar_id=avatar_id,
    )
    tmp_path = tts_result["tmp_path"]
    try:
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        avatar.test_audio_key = test_audio_key
        avatar.test_script = script
        await session.commit()

    # Step 2: Generate new video via InfiniteTalk
    await _update_progress(avatar_id, "Rendering video...", 75)
    test_video_key = f"creators/{user_id}/avatar/{avatar_id}/test_video.mp4"
    face_url = r2.get_signed_url(face_key, expires_in=7200)
    audio_url = await _prepare_preview_lipsync_audio(
        r2=r2,
        lipsync_audio_key=lipsync_audio_key,
        avatar_id=avatar_id,
    )
    from config import settings
    # #region debug-point C:preview-dispatch-input
    try: import json as _dj, urllib.request as _du; _p='.dbg/avatar-lipsync-missing.env'; _u='http://127.0.0.1:7777/event'; _s='avatar-lipsync-missing'; exec("try:\n c=open(_p).read(); _u=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SERVER_URL=')),_u); _s=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SESSION_ID=')),_s)\nexcept: pass"); _du.urlopen(_du.Request(_u, data=_dj.dumps({'sessionId':_s,'runId':'pre-fix','hypothesisId':'C','location':'generate_avatar._regenerate_pipeline','msg':'[DEBUG] Preview dispatch input prepared','data':{'avatar_id':avatar_id,'face_key':face_key,'test_audio_key':test_audio_key,'lipsync_audio_key':lipsync_audio_key,'audio_url_suffix':audio_url[-120:],'tts_duration_s':tts_result.get('duration_seconds'),'app_env':getattr(settings,'APP_ENV',None)},'ts':int(time.time()*1000)}).encode(), headers={'Content-Type':'application/json'}), timeout=1).read()
    except Exception: pass
    # #endregion
    use_webhook = (settings.APP_ENV or "").lower() in ("production",)

    if use_webhook:
        # -- InfiniteTalk via RunPod webhook mode (non-blocking) --
        job_id = await runpod.submit_video_job_webhook(
            image_url=face_url, audio_url=audio_url,
            variant_id=f"avatar_{avatar_id}",  # identifies this as avatar test video in webhook
            prompt="A person talking naturally to the camera",
            size="480p",
        )

        logger.info(f"Avatar test video submitted via webhook: job_id={job_id}, avatar_id={avatar_id}")

        # Store job_id on avatar so webhook handler can match it
        async with factory() as session:
            avatar = await session.get(Avatar, avatar_id)
            if avatar:
                avatar.runpod_job_id = job_id
                avatar.status = AvatarStatus.PROCESSING
                avatar.active_phase = AvatarPhase.RENDER
                avatar.progress_step = "Rendering test video..."
                avatar.progress_percent = 85
                await session.commit()

        # Return immediately -- webhook handler will finalize when RunPod completes
        logger.info(f"Avatar {avatar_id} render submitted via webhook, returning immediately")
        return

    # Local/dev mode: route preview renders through the same dispatcher cascade
    # used by cast renders so we do not depend solely on the known-flaky RunPod
    # InfiniteTalk template ("Video not found").
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar:
            avatar.runpod_job_id = None
            avatar.status = AvatarStatus.PROCESSING
            avatar.active_phase = AvatarPhase.RENDER
            avatar.progress_step = "Waiting for video render..."
            avatar.progress_percent = 85
            await session.commit()

    from services.render_dispatcher import RenderDispatcher
    tts_duration = tts_result.get("duration_seconds") or 15.0
    dispatcher = RenderDispatcher()
    dispatch_result = await dispatcher.submit_and_wait(
        image_url=face_url,
        audio_url=audio_url,
        prompt="A person talking naturally to the camera",
        size="480p",
        audio_duration_s=tts_duration,
        is_pip=False,
        block_id=f"avatar_preview_{avatar_id}",
    )

    backend = dispatch_result.get("backend", "unknown")
    output = dispatch_result.get("output") or {}
    if isinstance(output, str):
        output = {"video_base64": output}

    nested = output.get("result", {}) if isinstance(output, dict) and isinstance(output.get("result"), dict) else {}
    video_url = (
        output.get("video_url")
        or nested.get("video_url", "")
        or output.get("result")
        or ""
    ) if isinstance(output, dict) else ""
    has_r2_key = isinstance(output, dict) and bool(output.get("output_r2_key"))
    video_b64 = (
        output.get("video_base64")
        or output.get("video")
        or nested.get("video_base64", "")
        or nested.get("video", "")
    ) if isinstance(output, dict) else ""

    video_bytes = b""
    if has_r2_key:
        import httpx
        video_url = r2.get_public_url(output["output_r2_key"])
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            vid_resp = await client.get(video_url)
            vid_resp.raise_for_status()
            video_bytes = vid_resp.content
    elif isinstance(video_url, str) and video_url.startswith("http"):
        import httpx
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            vid_resp = await client.get(video_url)
            vid_resp.raise_for_status()
            video_bytes = vid_resp.content
    elif video_b64:
        video_bytes = base64.b64decode(video_b64)

    if not video_bytes or len(video_bytes) < 1000:
        output_summary = (
            {k: type(v).__name__ for k, v in output.items()}
            if isinstance(output, dict)
            else str(output)[:200]
        )
        raise RuntimeError(
            f"Video render completed on {backend} but no usable video data returned: {output_summary}"
        )

    logger.info(
        "Avatar preview rendered via %s (%d bytes)",
        backend,
        len(video_bytes),
    )

    await r2.upload_bytes(video_bytes, test_video_key, content_type="video/mp4")

    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar:
            avatar.test_video_key = test_video_key
            avatar.status = AvatarStatus.READY
            avatar.active_phase = AvatarPhase.READY
            avatar.progress_step = "Avatar ready — review your test video"
            avatar.progress_percent = 100
            avatar.runpod_job_id = None
            await session.commit()

# ═══════════════════════════════════════════════════════════════════════
# CLONE PIPELINE PREVIEW — corpus voice + selected face → test video
# ═══════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.generate_clone_preview", time_limit=5400, soft_time_limit=5100)
def generate_clone_preview_task(self, avatar_id: str, user_id: str):
    """Generate preview video for clone avatar using corpus voice + selected face.

    Delegates to _regenerate_pipeline which handles:
    1. Corpus-based voice cloning (Fish Audio)
    2. TTS audio generation
    3. InfiniteTalk video rendering via RunPod webhook
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_regenerate_pipeline(avatar_id, user_id, ""))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Clone preview pipeline failed: {exc}", exc_info=True)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_failed(avatar_id, str(exc)))
        finally:
            loop2.close()
        raise
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════
# NEW SEGMENT-BASED PIPELINES — process-segment kicks off both in parallel
# ═══════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.process_image_pipeline")
def process_image_pipeline_task(
    self, avatar_id: str, user_id: str,
    video_r2_key: str, start_sec: float, end_sec: float,
):
    """Image pipeline: cut segment → extract frames → MediaPipe face detection →
    Gemini scoring → upload top 8 candidates → status face_candidates_ready.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _image_pipeline(avatar_id, user_id, video_r2_key, start_sec, end_sec)
        )
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Image pipeline failed: {exc}", exc_info=True)
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_failed(avatar_id, str(exc)))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=60)
    finally:
        loop.close()


async def _image_pipeline(
    avatar_id: str, user_id: str,
    video_r2_key: str, start_sec: float, end_sec: float,
):
    """Extract face candidates from a video segment using MediaPipe + Gemini scoring."""
    import subprocess
    from models.avatar import Avatar, AvatarStatus, AvatarPhase
    from services.r2_storage import get_r2_storage_service
    from services.face_extraction import extract_top_faces, filter_frames_with_vision, upload_candidate_frames
    from config import settings

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    await _set_progress(avatar_id, AvatarPhase.IMAGE, "Downloading video segment for face extraction...", 30)

    # Download the full video from R2
    tmp_video = os.path.join(tempfile.gettempdir(), f"img_pipeline_{avatar_id}.mp4")
    tmp_segment = os.path.join(tempfile.gettempdir(), f"img_segment_{avatar_id}.mp4")

    try:
        await r2.download_file(video_r2_key, tmp_video)

        # Cut the segment with ffmpeg — input-seeking (fast) with re-encode
        # to guarantee clean frames at segment boundaries (B-097)
        duration = end_sec - start_sec
        result = subprocess.run(
            ["ffmpeg", "-y",
             "-ss", str(start_sec),
             "-i", tmp_video,
             "-t", str(duration),
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
             "-c:a", "copy",
             tmp_segment],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0 or not os.path.exists(tmp_segment):
            raise RuntimeError(f"ffmpeg segment cut failed: {result.stderr.decode()[:300]}")

        await _set_progress(avatar_id, AvatarPhase.IMAGE, "Detecting faces in video segment...", 40)

        # Extract frames at scene changes + 2fps, run MediaPipe face detection
        # extract_top_faces already does MediaPipe detection + quality scoring
        loop = asyncio.get_event_loop()
        # Pass 1: standard thresholds
        candidates = await loop.run_in_executor(
            None,
            lambda: extract_top_faces(
                tmp_segment,
                max_faces=16,
                sample_fps=2.0,
                min_confidence=0.35,
                min_sharpness=8.0,
            ),
        )

        # Pass 2: ultra-relaxed retry for TV/group footage, wide angles, soft focus
        if not candidates:
            logger.warning(f"Pass 1 found no faces for {avatar_id}, retrying with relaxed thresholds")
            candidates = await loop.run_in_executor(
                None,
                lambda: extract_top_faces(
                    tmp_segment,
                    max_faces=16,
                    sample_fps=1.0,
                    min_confidence=0.1,
                    min_sharpness=0.0,
                ),
            )

        if not candidates:
            logger.warning(f"No faces found in segment for {avatar_id} after both passes")
            await _mark_failed(avatar_id, "No clear faces found in this segment. The video may not show a clear frontal view — try a different clip.")
            return

        await _set_progress(avatar_id, AvatarPhase.IMAGE, "Scoring face candidates with AI...", 55)

        # Score with Gemini Flash
        openrouter_key = settings.OPENROUTER_API_KEY
        if openrouter_key:
            candidates = await filter_frames_with_vision(candidates, openrouter_key)

        # Upload top 8 candidates to R2
        await _set_progress(avatar_id, AvatarPhase.IMAGE, "Uploading face candidates...", 70)
        top_candidates = candidates[:8]
        candidate_urls = await upload_candidate_frames(top_candidates, avatar_id, user_id)

        # Extract real scores (combined MediaPipe + Gemini vision scores) for each candidate
        candidate_scores = []
        for c in top_candidates:
            # Prefer vision_score (from Gemini filter) if available, else use MediaPipe composite score
            score = c.get("vision_score") or c.get("score") or 0.0
            candidate_scores.append(round(float(score), 4))

        # Update avatar with candidate URLs, scores, and status
        async with factory() as session:
            avatar = await session.get(Avatar, avatar_id)
            if avatar:
                avatar.candidate_frames = candidate_urls
                avatar.candidate_scores = candidate_scores
                avatar.status = AvatarStatus.FACE_CANDIDATES_READY
                avatar.progress_step = "Pick your best face from the candidates"
                avatar.progress_percent = 75
                await session.commit()

        logger.info(json.dumps({
            "service": "generate_avatar", "level": "info",
            "message": f"Image pipeline complete — {len(candidate_urls)} face candidates",
            "avatar_id": avatar_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

    finally:
        for p in [tmp_video, tmp_segment]:
            try:
                os.unlink(p)
            except OSError:
                pass


@celery_app.task(bind=True, max_retries=1, name="tasks.generate_avatar.process_voice_pipeline")
def process_voice_pipeline_task(
    self, avatar_id: str, user_id: str,
    video_r2_key: str, start_sec: float, end_sec: float,
):
    """Voice pipeline: cut audio from segment → BS-RoFormer → normalize → Fish Audio clone.
    Runs in parallel with the image pipeline.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            _voice_pipeline(avatar_id, user_id, video_r2_key, start_sec, end_sec)
        )
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(f"Voice pipeline failed: {exc}", exc_info=True)
        # Don't mark avatar as failed — the image pipeline may still succeed
        # Just log the error and update voice_clone_progress
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_update_voice_progress(avatar_id, -1))
        finally:
            loop2.close()
        raise self.retry(exc=exc, countdown=60)
    finally:
        loop.close()


async def _update_voice_progress(avatar_id: str, progress: int):
    """Update voice_clone_progress field independently of main progress."""
    from models.avatar import Avatar
    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar:
            avatar.voice_clone_progress = progress
            await session.commit()


async def _update_voice_progress_step(avatar_id: str, step_message: str):
    """Update progress_step BUT ONLY if voice cloning is still in progress.
    Once voice_clone_progress >= 100, the regenerate/generation task owns progress_step."""
    from models.avatar import Avatar
    factory = _make_session_factory()
    async with factory() as session:
        avatar = await session.get(Avatar, avatar_id)
        if avatar and (avatar.voice_clone_progress or 0) < 100:
            avatar.progress_step = step_message
            await session.commit()


async def _voice_pipeline(
    avatar_id: str, user_id: str,
    video_r2_key: str, start_sec: float, end_sec: float,
):
    """Extract audio → BS-RoFormer Pass 1 → diarization → segment assembly → Pass 2 → Fish Audio clone."""
    import subprocess
    import wave
    import numpy as np
    from models.avatar import Avatar, AvatarPhase
    from services.r2_storage import get_r2_storage_service
    from services.fish_audio import get_fish_audio_service
    from config import settings

    factory = _make_session_factory()
    r2 = get_r2_storage_service()
    fish = get_fish_audio_service()

    await _update_voice_progress(avatar_id, 10)

    # Download the full video from R2
    tmp_video = os.path.join(tempfile.gettempdir(), f"voice_pipeline_{avatar_id}.mp4")
    tmp_audio = os.path.join(tempfile.gettempdir(), f"voice_segment_{avatar_id}.wav")
    tmp_files = [tmp_video, tmp_audio]

    try:
        await r2.download_file(video_r2_key, tmp_video)

        # ── Step 1: Extract audio from segment ──
        duration = end_sec - start_sec
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_video,
             "-ss", str(start_sec), "-t", str(duration),
             "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
             tmp_audio],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(tmp_audio) or os.path.getsize(tmp_audio) < 1000:
            raise RuntimeError(f"Audio extraction failed: {result.stderr.decode()[:300]}")

        audio_size = os.path.getsize(tmp_audio)
        logger.info(f"Voice segment audio: {audio_size} bytes, ~{audio_size / (44100 * 2):.1f}s")

        # ── Step 2: BS-RoFormer Pass 1 — strip music/effects ──
        # Try: RunPod GPU (90s timeout) → VPS CPU fallback → raw audio
        await _update_voice_progress(avatar_id, 20)
        await _set_progress(avatar_id, AvatarPhase.VOICE, "Starting vocal isolation...")
        voice_transcript = ""
        clean_vocals_path = tmp_audio  # ultimate fallback = raw audio
        clean_result = None
        try:
            clean_result = await _run_audio_processing(tmp_audio, avatar_id, user_id, r2, pass_label="pass1")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"BS-RoFormer Pass 1 RunPod failed: {e}")

        if not clean_result:
            logger.info("RunPod unavailable for Pass 1 — trying CPU fallback")
            clean_result = await _run_vocal_isolation_cpu(tmp_audio, avatar_id, pass_label="pass1", user_id=user_id)

        if clean_result:
            if clean_result.get("vocals_path"):
                clean_vocals_path = clean_result["vocals_path"]
                tmp_files.append(clean_vocals_path)
                logger.info(f"Pass 1 clean vocals: {clean_vocals_path} ({os.path.getsize(clean_vocals_path)} bytes)")
            voice_transcript = clean_result.get("transcript", "")
        else:
            logger.warning("Pass 1: both RunPod and CPU failed — using raw audio")

        # ── Step 3: Pyannote diarization — find dominant speaker ──
        await _update_voice_progress(avatar_id, 40)
        assembled_path = None
        try:
            from services.speaker_diarization import extract_primary_speaker_segments
            loop = asyncio.get_event_loop()
            segments = await loop.run_in_executor(
                None,
                lambda: extract_primary_speaker_segments(clean_vocals_path, min_segment_duration=2.0),
            )

            if segments:
                logger.info(f"Diarization found {len(segments)} dominant speaker segments")

                # ── Step 4: Assemble primary speaker segments ──
                await _update_voice_progress(avatar_id, 55)
                assembled_path = await _assemble_speaker_segments(
                    clean_vocals_path, segments, avatar_id, tmp_files,
                )
            else:
                logger.warning(f"Diarization returned no segments for {avatar_id} — using Pass 1 output directly")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Diarization failed (using Pass 1 output): {e}")

        # Use assembled audio if available, else fall through to Pass 1 output
        audio_for_pass2 = assembled_path if assembled_path else clean_vocals_path

        # ── Step 5: BS-RoFormer Pass 2 — second cleaning pass ──
        # Try: RunPod GPU (90s timeout) → VPS CPU fallback → use previous audio
        await _update_voice_progress(avatar_id, 70)
        await _set_progress(avatar_id, AvatarPhase.VOICE, "Second vocal isolation pass...")
        final_audio = audio_for_pass2
        pass2_result = None
        try:
            pass2_result = await _run_audio_processing(audio_for_pass2, avatar_id, user_id, r2, pass_label="pass2")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"BS-RoFormer Pass 2 RunPod failed: {e}")

        if not pass2_result:
            logger.info("RunPod unavailable for Pass 2 — trying CPU fallback")
            pass2_result = await _run_vocal_isolation_cpu(audio_for_pass2, avatar_id, pass_label="pass2", user_id=user_id)

        if pass2_result and pass2_result.get("vocals_path"):
            final_audio = pass2_result["vocals_path"]
            tmp_files.append(final_audio)
            logger.info(f"Pass 2 clean vocals: {final_audio} ({os.path.getsize(final_audio)} bytes)")
            if pass2_result.get("transcript"):
                voice_transcript = pass2_result["transcript"]
        else:
            logger.warning("Pass 2: both RunPod and CPU failed — using previous audio")

        # ── Step 6: Clone voice via Fish Audio ──
        async with factory() as session:
            avatar = await session.get(Avatar, avatar_id)
            tiktok_url = avatar.tiktok_source_url if avatar else ""

        voice_id = await fish.clone_voice_from_file(
            final_audio,
            name=f"Clone of {tiktok_url or 'creator'}",
            transcript=voice_transcript,
        )
        logger.info(f"Voice cloned from segment: {voice_id}")

        # Update avatar with voice_id
        async with factory() as session:
            avatar = await session.get(Avatar, avatar_id)
            if avatar:
                avatar.voice_id = voice_id
                avatar.voice_clone_progress = 100

                # Auto-fire render if face already selected
                from models.avatar import AvatarStatus, AvatarPhase
                if avatar.face_ref_key and avatar.active_phase == AvatarPhase.VOICE:
                    avatar.active_phase = AvatarPhase.RENDER
                    avatar.status = AvatarStatus.PROCESSING
                    avatar.progress_step = "Generating test video..."
                    avatar.progress_percent = 80
                    await session.commit()
                    from tasks.generate_avatar import regenerate_avatar_video_task
                    regenerate_avatar_video_task.delay(avatar_id, user_id, avatar.test_script or "")
                else:
                    # User hasn't picked face yet -- voice done, phase stays IMAGE
                    await session.commit()

        logger.info(json.dumps({
            "service": "generate_avatar", "level": "info",
            "message": "Voice pipeline complete",
            "avatar_id": avatar_id, "voice_id": voice_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass


async def _assemble_speaker_segments(
    clean_vocals_path: str,
    segments: list[tuple[float, float]],
    avatar_id: str,
    tmp_files: list[str],
    max_duration: float = 90.0,
) -> str | None:
    """Cut dominant speaker segments, score by RMS, take best 60-90s, concatenate with 300ms gaps.

    Returns path to assembled WAV, or None on failure.
    """
    import subprocess
    import wave
    import numpy as np

    try:
        # Cut each segment and score by RMS energy
        segment_info = []  # (path, rms, duration)
        for i, (start, end) in enumerate(segments):
            seg_path = os.path.join(tempfile.gettempdir(), f"diar_seg_{avatar_id}_{i}.wav")
            tmp_files.append(seg_path)
            seg_duration = end - start
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", clean_vocals_path,
                 "-ss", str(start), "-t", str(seg_duration),
                 "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
                 seg_path],
                capture_output=True, timeout=15,
            )
            if result.returncode != 0 or not os.path.exists(seg_path) or os.path.getsize(seg_path) < 1000:
                continue

            # Score by RMS energy
            try:
                with wave.open(seg_path, "rb") as wf:
                    frames = wf.readframes(wf.getnframes())
                    audio_data = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(audio_data ** 2)))
            except Exception:
                rms = 0.0

            segment_info.append((seg_path, rms, seg_duration))

        if not segment_info:
            logger.warning(f"No usable diarization segments for {avatar_id}")
            return None

        # Sort by RMS descending (louder = clearer speech), take best up to 60-90s
        segment_info.sort(key=lambda x: x[1], reverse=True)
        selected = []
        total_dur = 0.0
        for seg_path, rms, seg_dur in segment_info:
            if total_dur >= max_duration:
                break
            selected.append(seg_path)
            total_dur += seg_dur

        if total_dur < 5.0:
            logger.warning(f"Only {total_dur:.1f}s of dominant speaker audio — too short")
            return None

        logger.info(f"Selected {len(selected)} segments, total {total_dur:.1f}s")

        # Generate 300ms silence gap
        silence_path = os.path.join(tempfile.gettempdir(), f"silence_gap_{avatar_id}.wav")
        tmp_files.append(silence_path)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-t", "0.3", "-acodec", "pcm_s16le", silence_path],
            capture_output=True, timeout=10,
        )

        # Build concat list: segment + gap + segment + gap + ...
        list_path = os.path.join(tempfile.gettempdir(), f"diar_concat_{avatar_id}.txt")
        tmp_files.append(list_path)
        with open(list_path, "w") as f:
            for i, seg_path in enumerate(selected):
                f.write(f"file '{seg_path}'\n")
                if i < len(selected) - 1 and os.path.exists(silence_path):
                    f.write(f"file '{silence_path}'\n")

        # Concatenate
        concat_path = os.path.join(tempfile.gettempdir(), f"diar_assembled_{avatar_id}.wav")
        tmp_files.append(concat_path)
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
             concat_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(concat_path):
            logger.warning(f"Segment concatenation failed: {result.stderr.decode()[:200]}")
            return None

        # Normalize: highpass + lowpass + loudnorm
        normalized_path = os.path.join(tempfile.gettempdir(), f"diar_normalized_{avatar_id}.wav")
        tmp_files.append(normalized_path)
        ffmpeg_filter = (
            "highpass=f=80,"
            "lowpass=f=14000,"
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        )
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", concat_path, "-af", ffmpeg_filter,
             "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
             normalized_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(normalized_path):
            logger.warning(f"Normalization failed, using raw concatenation: {result.stderr.decode()[:200]}")
            return concat_path

        logger.info(f"Assembled audio: {normalized_path} ({os.path.getsize(normalized_path)} bytes)")
        return normalized_path

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Segment assembly failed: {e}", exc_info=True)
        return None

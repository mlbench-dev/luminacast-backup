"""
3-Tier Render Dispatcher: HOSTKEY (free) -> Modal (cheap, unlimited parallel) -> RunPod (fallback)

All blocks dispatch in parallel via asyncio.gather in the caller.
HOSTKEY semaphore ensures only 1 block goes to the local GPU at a time.
Modal handles unlimited parallel blocks (auto-scaling serverless).
RunPod is the last-resort fallback.

The output format is unified: {output: {video: base64, ...}, backend: str}
"""
import asyncio
import logging
import os
import time
import sentry_sdk
import httpx

from services.hostkey_flags import hostkey_disabled, log_hostkey_skip

logger = logging.getLogger(__name__)

# HOSTKEY (Tier 1) — DECOMMISSIONED. Disabled whenever the central kill-switch
# says so (CAST_RENDER_HOSTKEY_DISABLED truthy or HOSTKEY_RENDER_ENABLED falsey,
# both default to "HOSTKEY off"). The constant is computed at call time inside
# the dispatcher rather than frozen at import so a re-enable doesn't need a
# process restart.
HOSTKEY_URL = os.environ.get("HOSTKEY_GPU_URL", "http://194.247.183.12:7860")
HOSTKEY_RENDER_ENABLED = not hostkey_disabled()
HOSTKEY_TIMEOUT = 1800  # 30 min max for a single render
# Bounded wait for the local GPU semaphore. When N blocks dispatch concurrently
# they queue here instead of falling through to the next tier. After this
# window a block gives up its slot and spills to the paid fallback.
# 30s is a sensible "wait for the previous block to finish" since most
# HOSTKEY renders are <30s anyway. Multiple blocks arriving within seconds
# of each other should serialize through the local GPU rather than racing
# straight to RunPod and burning paid time. Override per-deploy via
# HOSTKEY_QUEUE_WAIT_S if a longer queue is acceptable.
HOSTKEY_QUEUE_WAIT_S = int(os.environ.get("HOSTKEY_QUEUE_WAIT_S", "30"))

# Modal (Tier 2)
MODAL_ENDPOINT_URL = os.environ.get("MODAL_ENDPOINT_URL", "")
MODAL_TOKEN_ID = os.environ.get("MODAL_TOKEN_ID", "")
MODAL_TOKEN_SECRET = os.environ.get("MODAL_TOKEN_SECRET", "")
MODAL_TIMEOUT = 1800
# Per-call wall-clock ceiling on the Modal polling loop. If we keep getting
# 303 redirects past this many seconds we treat the function call as a
# zombie and raise ModalStuck so the dispatcher can fall through to RunPod.
# This is a bounded fallback signal, not a hard task kill — Celery's
# task_time_limit remains the only true hard timeout.
# 10 min default gives a real Modal cold-start + first-frame window
# enough room to finish before we abandon. Override per-deploy via
# MODAL_MAX_WAIT_S in the env if a longer ceiling is needed (or
# shorter, if Modal is provisioned with warm workers).
MODAL_MAX_WAIT_S = int(os.environ.get("MODAL_MAX_WAIT_S", "600"))


class ModalStuck(RuntimeError):
    """Raised when Modal keeps returning 303 past MODAL_MAX_WAIT_S.

    Treated as a fall-through trigger, not a fatal error.
    """

# MuseTalk (PIP-only path, runs on the same HOSTKEY box)
# The gpu-worker exposes MuseTalk at /api/musetalk-lipsync on port 7860
# (same listener as the InfiniteTalk route). Disable via
# MUSETALK_ENABLED=false to force-route PIP through the InfiniteTalk
# cascade instead.
# MuseTalk ran on the same HOSTKEY box, so it is gated by the same kill-switch:
# once HOSTKEY is disabled the PIP path skips MuseTalk and routes straight to
# the cloud lipsync cascade (fal Hallo → WaveSpeed InfiniteTalk). The legacy
# MUSETALK_ENABLED override can still force it off independently, but it can
# never turn MuseTalk on while HOSTKEY is disabled.
MUSETALK_URL = os.environ.get("MUSETALK_URL", "http://194.247.183.12:7860")
MUSETALK_ENABLED = (
    os.environ.get("MUSETALK_ENABLED", "true").lower() == "true"
    and not hostkey_disabled()
)
MUSETALK_TIMEOUT = 600  # 10 min — MuseTalk is faster than InfiniteTalk by ~5x

# Per-event-loop Semaphore(1) for HOSTKEY single-slot serialization.
#
# A bool flag was correct but pessimistic: concurrent siblings saw it True
# and skipped HOSTKEY entirely, falling straight through to the paid
# RunPod tier. With a Semaphore we can wait briefly (HOSTKEY_QUEUE_WAIT_S)
# so multiple blocks arriving within seconds of each other serialize
# through the local GPU instead of racing for paid fallbacks.
#
# The earlier "module-singleton Semaphore" attempt failed because Python
# binds the Semaphore to whatever event loop is current at construction
# time; a Celery worker that respawns its loop ends up with a stale
# semaphore bound to a dead loop. We therefore lazy-init one per running
# loop and key the dict by id(loop). Loop addresses can recycle across
# Celery tasks but the worst case is a fresh task inheriting an unlocked
# semaphore — still single-slot correct within that task's gather().
_hostkey_loop_semaphores: dict[int, asyncio.Semaphore] = {}


def _ensure_fal_key() -> None:
    """Seed fal env vars from app settings when this process only loaded `.env`
    through Pydantic settings.
    ...
    """
    if os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY"):
        return
    try:
        from config import settings
        fal_api_key = getattr(settings, "FAL_API_KEY", "")
    except Exception:
        fal_api_key = ""
    if fal_api_key:
        os.environ["FAL_KEY"] = fal_api_key
        logger.info("Seeded FAL_KEY from settings for render dispatcher")


def _ensure_wavespeed_key() -> None:
    """Same problem, same fix, for WaveSpeed: WavespeedInfinitetalkProvider.
    is_available() reads WAVESPEED_API_KEY straight from os.environ, but it
    was never being exported there — only pulled into Settings. Provider
    was silently reporting 'unavailable' every render as a result.
    """
    if os.environ.get("WAVESPEED_API_KEY"):
        return
    try:
        from config import settings
        wavespeed_key = getattr(settings, "WAVESPEED_API_KEY", "")
    except Exception:
        wavespeed_key = ""
    if wavespeed_key:
        os.environ["WAVESPEED_API_KEY"] = wavespeed_key
        logger.info("Seeded WAVESPEED_API_KEY from settings for render dispatcher")

def _get_loop_semaphore() -> asyncio.Semaphore | None:
    """Return the HOSTKEY semaphore bound to the current running loop.

    Returns None if there is no running loop (rare: e.g. a Celery
    bootstrap path that calls into the dispatcher synchronously). The
    caller should treat None as "no semaphore available, proceed without
    serialization" — the worst case is two blocks racing for the GPU,
    which the HOSTKEY service itself rejects with 503.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("No running event loop for HOSTKEY semaphore: %s", exc)
        return None
    key = id(loop)
    sem = _hostkey_loop_semaphores.get(key)
    if sem is None:
        sem = asyncio.Semaphore(1)
        _hostkey_loop_semaphores[key] = sem
    return sem


class RenderDispatcher:
    """Routes InfiniteTalk jobs through HOSTKEY -> Modal -> RunPod cascade."""

    def __init__(self):
        self._runpod = None

    @property
    def runpod(self):
        if self._runpod is None:
            from services.runpod import RunPodService
            self._runpod = RunPodService()
        return self._runpod

    async def submit_and_wait(
        self,
        image_url: str,
        audio_url: str,
        prompt: str,
        size: str,
        audio_duration_s: float,
        is_pip: bool = False,
        block_id: str | None = None,
    ) -> dict:
        """Submit a face-bake job and return {output, backend}. The pipeline
        is normally HOSTKEY → Modal → RunPod (all InfiniteTalk). For PIP blocks
        we try MuseTalk on HOSTKEY FIRST because it's far cheaper, faster,
        and visually fine at small sizes — falling back to the InfiniteTalk
        cascade if MuseTalk fails for any reason so renders never break.
        """
        _ensure_fal_key()
        _ensure_wavespeed_key()
        w, h = {"480p": (480, 848), "720p": (720, 1280), "1080p": (1080, 1920)}.get(size, (480, 848))
        # #region debug-point A:dispatcher-entry
        try: import json as _dj, urllib.request as _du, time as _dt; _p='.dbg/avatar-lipsync-missing.env'; _u='http://127.0.0.1:7777/event'; _s='avatar-lipsync-missing'; exec("try:\n c=open(_p).read(); _u=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SERVER_URL=')),_u); _s=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SESSION_ID=')),_s)\nexcept: pass"); _du.urlopen(_du.Request(_u, data=_dj.dumps({'sessionId':_s,'runId':'pre-fix','hypothesisId':'A','location':'render_dispatcher.submit_and_wait','msg':'[DEBUG] Dispatcher entry','data':{'size':size,'audio_duration_s':audio_duration_s,'is_pip':is_pip,'block_id':block_id,'hostkey_enabled':HOSTKEY_RENDER_ENABLED,'modal_enabled':bool(MODAL_ENDPOINT_URL),'musetalk_enabled':MUSETALK_ENABLED,'image_url_suffix':image_url[-120:],'audio_url_suffix':audio_url[-120:]},'ts':int(_dt.time()*1000)}).encode(), headers={'Content-Type':'application/json'}), timeout=1).read()
        except Exception: pass
        # #endregion

        sem = _get_loop_semaphore()
        # Always log the dispatch decision context so we can see in logs
        # exactly WHY a particular tier was tried or skipped. (Without
        # this we couldn't tell HOSTKEY_RENDER_ENABLED was false on prod.)
        logger.info(
            "Dispatch decision: HOSTKEY_RENDER_ENABLED=%s, MUSETALK_ENABLED=%s, "
            "is_pip=%s, hostkey_locked=%s, MODAL_ENDPOINT_URL=%s",
            HOSTKEY_RENDER_ENABLED, MUSETALK_ENABLED, is_pip,
            sem.locked() if sem is not None else "no-loop", bool(MODAL_ENDPOINT_URL),
        )

        # ── PIP path: try MuseTalk first ──
        if is_pip and MUSETALK_ENABLED:
            # MuseTalk freezes mid-clip on long audio prompts (observed
            # on render rnd_24578b0444ce — 3.3s / 2.8s / 2.2s / 2.8s /
            # 3.2s frozen segments across multiple PIP blocks). The
            # provider classes in render_providers.py already detect
            # this and raise FrozenBakeError, but the MuseTalk path
            # bypasses those classes — it goes straight through
            # musetalk_client. So we run the same freeze-detect helper
            # here and trip the fallover when the bake is frozen.
            #
            # Fallover order: MuseTalk → FalHallo (cloud image+audio →
            # video) → WavespeedInfiniteTalk (cloud InfiniteTalk). One
            # retry through the provider chain — if both alternates
            # also freeze or fail, we surface the failure to the caller.
            try:
                logger.info("Dispatching PIP block to MuseTalk")
                result = await self._render_on_musetalk(image_url, audio_url)
                logger.info(
                    "MuseTalk render complete: r2_key=%s duration_s=%s",
                    result.get("output_r2_key"), result.get("duration_s"),
                )
                if await self._musetalk_output_is_frozen(
                    result, audio_duration_s=audio_duration_s,
                ):
                    # Tag and emit a Sentry warning so we can grep for
                    # how often MuseTalk falls over due to freezes.
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag(
                            "freeze_detected_in_bake", "musetalk"
                        )
                        scope.set_extra(
                            "musetalk_output_r2_key",
                            result.get("output_r2_key"),
                        )
                        scope.set_extra(
                            "audio_duration_s", float(audio_duration_s or 0),
                        )
                        sentry_sdk.capture_message(
                            "Frozen segment(s) detected in MuseTalk bake; "
                            "falling over to provider chain",
                            level="warning",
                        )
                    logger.warning(
                        "MuseTalk produced frozen output — falling over to "
                        "FalHallo → WavespeedInfiniteTalk (1 retry)",
                    )
                    fallover = await self._musetalk_freeze_fallover(
                        image_url=image_url,
                        audio_url=audio_url,
                        prompt=prompt,
                        width=w,
                        height=h,
                        audio_duration_s=audio_duration_s,
                    )
                    if fallover is not None:
                        return fallover
                    # No alternate provider produced a usable bake —
                    # ship the frozen MuseTalk output rather than
                    # failing the block. Downstream compose still
                    # produces a watchable clip; the freeze artifact
                    # is logged to Sentry above.
                    logger.warning(
                        "MuseTalk freeze fallover exhausted — keeping "
                        "original MuseTalk output",
                    )
                return {"output": result, "backend": "musetalk"}
            except Exception as exc:
                # Emit the warning BEFORE sentry_sdk.capture_exception so that
                # concurrent block failures all surface a log line (Sentry can
                # rate-limit / dedupe but log lines are 1:1 with failures).
                logger.warning(
                    "MuseTalk render failed (%r) — falling back to InfiniteTalk cascade",
                    exc,
                )
                sentry_sdk.capture_exception(exc)

        # ── Tier 1: HOSTKEY (max 1 block at a time, free) ──
        # The Semaphore(1) bound to this event loop serializes concurrent
        # blocks: the first one acquires immediately, siblings wait up to
        # HOSTKEY_QUEUE_WAIT_S for it to release before falling through
        # to the paid tier. If the semaphore is unavailable (no running
        # loop) we skip straight to the next tier.
        if HOSTKEY_RENDER_ENABLED and sem is not None:
            try:
                # PR #70: short wait (3 s) — a freshly-released slot is
                # grabbed quickly; a longer queue here just stalls every
                # sibling block while the local GPU is already saturated.
                await asyncio.wait_for(sem.acquire(), timeout=3.0)
            except asyncio.TimeoutError as _sem_to:
                sentry_sdk.capture_exception(_sem_to)
                logger.info(
                    "HOSTKEY busy after short wait — falling through to next tier",
                )
            else:
                try:
                    if await self._hostkey_available():
                        logger.info("Dispatching to HOSTKEY (Tier 1, free)")
                        result = await self._render_on_hostkey(image_url, audio_url, prompt, w, h)
                        video_data = result.get("video", "")
                        est_bytes = len(video_data) * 3 // 4 if video_data else 0
                        logger.info("HOSTKEY render complete: ~%d bytes video", est_bytes)
                        return {"output": result, "backend": "hostkey"}
                    else:
                        logger.info("HOSTKEY GPU not available — trying next tier")
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    logger.warning("HOSTKEY render failed: %s — trying next tier", exc)
                finally:
                    sem.release()
        elif HOSTKEY_RENDER_ENABLED:
            logger.info("HOSTKEY semaphore unavailable (no running loop) — skipping to next tier")

        # ── Tier 2: RunPod InfiniteTalk (paid, slow, perfect lip sync) ──
        # Speaking blocks REQUIRE audio-driven lip sync. The only providers
        # that take an audio input and animate the avatar's mouth from it
        # are HOSTKEY InfiniteTalk and RunPod InfiniteTalk — so when
        # HOSTKEY is busy/down, RunPod is the only acceptable fallback.
        # We do NOT cascade through fal.ai Wan I2V (no audio input — lips
        # would be desynced) or fal.ai Kling (synthesises its own voice).
        # Modal previously sat between HOSTKEY and RunPod; the env var
        # MODAL_ENDPOINT_URL is empty in production so the if-block below
        # is a no-op today, kept for the day we re-enable Modal.
        if MODAL_ENDPOINT_URL:
            try:
                logger.info("Dispatching to Modal (Tier 2)")
                result = await self._render_on_modal(image_url, audio_url, prompt, w, h)
                video_data = result.get("video", "")
                est_bytes = len(video_data) * 3 // 4 if video_data else 0
                logger.info("Modal render complete: ~%d bytes video", est_bytes)
                return {"output": result, "backend": "modal"}
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                logger.warning("Modal render failed: %s — falling back to RunPod", exc)

        # ── Tier 3: last cloud fallback before RunPod ──
        # For true PIP blocks we only have a face image + audio and a looped
        # still fallback is acceptable at the small inset size. For NON-PIP
        # talking-head renders, that looped-face path can yield effectively
        # static output, so we must stay on first-pass image+audio speaking
        # providers instead (FalHallo → WaveSpeed InfiniteTalk).
        try:
            if is_pip:
                logger.info("Dispatching PIP block to fal.ai sync-lipsync (Tier 3, looped face)")
                from services.render_providers import pip_lipsync_via_fal
                return await pip_lipsync_via_fal(
                    face_ref_url=image_url,
                    audio_url=audio_url,
                    block_id=block_id or "unknown",
                    duration_s=audio_duration_s,
                    width=w,
                    height=h,
                )

            logger.info(
                "Dispatching non-PIP speaking block to cloud image+audio chain "
                "(Tier 3, FalHallo → WaveSpeedInfiniteTalk)"
            )
            fallover = await self._musetalk_freeze_fallover(
                image_url=image_url,
                audio_url=audio_url,
                prompt=prompt,
                width=w,
                height=h,
                audio_duration_s=audio_duration_s,
            )
            if fallover is not None:
                # #region debug-point A:tier3-success
                try: import json as _dj, urllib.request as _du, time as _dt; _p='.dbg/avatar-lipsync-missing.env'; _u='http://127.0.0.1:7777/event'; _s='avatar-lipsync-missing'; exec("try:\n c=open(_p).read(); _u=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SERVER_URL=')),_u); _s=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SESSION_ID=')),_s)\nexcept: pass"); _du.urlopen(_du.Request(_u, data=_dj.dumps({'sessionId':_s,'runId':'pre-fix','hypothesisId':'A','location':'render_dispatcher.submit_and_wait','msg':'[DEBUG] Tier 3 cloud chain produced result','data':{'block_id':block_id,'backend':fallover.get('backend') if isinstance(fallover, dict) else None},'ts':int(_dt.time()*1000)}).encode(), headers={'Content-Type':'application/json'}), timeout=1).read()
                except Exception: pass
                # #endregion
                return fallover
            logger.warning(
                "Non-PIP cloud image+audio chain exhausted — falling through to RunPod"
            )
            # #region debug-point D:tier3-exhausted
            try: import json as _dj, urllib.request as _du, time as _dt; _p='.dbg/avatar-lipsync-missing.env'; _u='http://127.0.0.1:7777/event'; _s='avatar-lipsync-missing'; exec("try:\n c=open(_p).read(); _u=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SERVER_URL=')),_u); _s=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SESSION_ID=')),_s)\nexcept: pass"); _du.urlopen(_du.Request(_u, data=_dj.dumps({'sessionId':_s,'runId':'pre-fix','hypothesisId':'D','location':'render_dispatcher.submit_and_wait','msg':'[DEBUG] Tier 3 cloud chain exhausted','data':{'block_id':block_id,'size':size,'audio_duration_s':audio_duration_s},'ts':int(_dt.time()*1000)}).encode(), headers={'Content-Type':'application/json'}), timeout=1).read()
            except Exception: pass
            # #endregion
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "Tier 3 fallback failed: %s — falling back to RunPod", exc,
            )

        # ── Tier 4: RunPod InfiniteTalk (last resort, paid, perfect lip sync) ──
        # KNOWN-BROKEN for PIP: the `triazevwb6a8ap` template fails fast with
        # "Video not found". Kept only as the absolute last resort for the day
        # it (or a replacement template) comes back online.
        logger.info("Dispatching to RunPod InfiniteTalk (Tier 4, last-resort fallback)")
        return await self._render_on_runpod(image_url, audio_url, prompt, size, audio_duration_s)

    async def _hostkey_available(self) -> bool:
        """Check if HOSTKEY GPU is reachable and not locked.

        The gpu-status endpoint auto-clears stale locks (dead-PID or expired max_hold).
        As a belt-and-suspenders check, we also verify on the dispatcher side.

        HOSTKEY is decommissioned: when the env kill-switch is set (the
        default) this short-circuits to False so every tier-1 dispatch falls
        through to the cloud/RunPod cascade without ever touching the dead box.
        """
        if hostkey_disabled():
            log_hostkey_skip("cloud/runpod cascade")
            return False
        logger.info("Checking HOSTKEY availability at %s (enabled=%s)", HOSTKEY_URL, HOSTKEY_RENDER_ENABLED)
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{HOSTKEY_URL}/api/gpu-status")
                if resp.status_code != 200:
                    logger.warning("HOSTKEY returned %d", resp.status_code)
                    return False
                data = resp.json()
                lock = data.get("lock", {})
                # "stale_cleared" means the endpoint just cleared a stale lock — GPU is free
                if lock.get("stale_cleared"):
                    logger.info("HOSTKEY stale lock was auto-cleared — GPU is FREE")
                    return True
                if lock.get("pid") and lock.get("task"):
                    acquired_at = lock.get("acquired_at", 0)
                    max_hold = lock.get("max_hold", 1800)
                    held_seconds = time.time() - acquired_at if acquired_at else 0
                    if acquired_at and held_seconds > max_hold:
                        logger.warning("HOSTKEY lock is stale (held %.0fs > max %ds) — treating as FREE",
                                       held_seconds, max_hold)
                        return True
                    logger.info("HOSTKEY GPU busy: %s (pid=%s, held %.0fs)",
                                lock.get("task"), lock.get("pid"), held_seconds)
                    return False
                logger.info("HOSTKEY GPU is FREE — will use as Tier 1")
                return True
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("HOSTKEY unreachable: %s", exc)
            return False

    async def _render_on_musetalk(
        self, image_url: str, audio_url: str,
        render_size: str = "240p", fps: int = 25,
    ) -> dict:
        """POST a lip-sync job to the gpu-worker's /api/musetalk-lipsync
        endpoint on the HOSTKEY box (port 7860).

        Response shape from the real endpoint:
            {"status": "ok", "output_r2_key": "...", "duration_seconds": ...,
             "render_seconds": ...}

        We return {"output_r2_key": ..., "duration_s": ...} and let
        cast_render.py download the mp4 from R2 via _resolve_video_bytes.
        Connect errors are wrapped so the type name is always visible in
        downstream logs even when str(exc) is empty.
        """
        async with httpx.AsyncClient(timeout=MUSETALK_TIMEOUT) as client:
            try:
                resp = await client.post(
                    f"{MUSETALK_URL}/api/musetalk-lipsync",
                    json={
                        "face_image_url": image_url,
                        "audio_url": audio_url,
                        "render_size": render_size,
                        "fps": fps,
                    },
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # str(ConnectError) is usually "" for a refused TCP — surface the
                # type so the outer caller's log/DB row is not just a colon.
                sentry_sdk.capture_exception(exc)
                raise RuntimeError(
                    f"MuseTalk connect failed: {type(exc).__name__}: {exc!s}"
                ) from exc
        if resp.status_code == 503:
            raise RuntimeError("MuseTalk busy (503)")
        if resp.status_code >= 400:
            raise RuntimeError(
                f"MuseTalk HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        if data.get("status") != "ok" or not data.get("output_r2_key"):
            raise RuntimeError(f"MuseTalk bad response: {data}")
        return {
            "output_r2_key": data["output_r2_key"],
            "duration_s": data.get("duration_seconds"),
        }

    async def _musetalk_output_is_frozen(
        self,
        musetalk_result: dict,
        *,
        audio_duration_s: float,
    ) -> bool:
        """True iff freezedetect finds a >=0.6s frozen segment in the
        MuseTalk output. The MuseTalk client returns an R2 key (not a
        URL), so we resolve a public URL via the R2 storage service
        before invoking the shared freeze-detect helper.

        Any failure inside this check is captured to Sentry and
        suppressed — a freeze-detect probe must NEVER itself fail the
        block. The caller treats a False return as "not frozen, keep
        going".
        """
        try:
            r2_key = (musetalk_result or {}).get("output_r2_key") or ""
            if not r2_key:
                return False
            from services.r2_storage import get_r2_storage_service
            r2 = get_r2_storage_service()
            try:
                video_url = r2.get_public_url(r2_key)
            except Exception as _url_exc:
                sentry_sdk.capture_exception(_url_exc)
                return False
            if not video_url:
                return False
            # Effective clip duration drives the freeze-detect ffmpeg
            # timeout. Prefer the engine-reported value, fall back to
            # the caller's audio_duration_s; neither is hardcoded.
            try:
                clip_s = float(
                    musetalk_result.get("duration_s") or audio_duration_s or 0
                )
            except (TypeError, ValueError):
                clip_s = float(audio_duration_s or 0)
            from services.render_providers import freeze_detect_video_url
            freezes = await freeze_detect_video_url(
                video_url,
                clip_duration_s=clip_s,
                provider_name="musetalk",
            )
            return bool(freezes)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "MuseTalk freeze-detect probe failed (%s) — assuming not frozen",
                exc,
            )
            return False

    async def _musetalk_freeze_fallover(
        self,
        *,
        image_url: str,
        audio_url: str,
        prompt: str,
        width: int,
        height: int,
        audio_duration_s: float,
    ) -> dict | None:
        """Walk FalHallo → WavespeedInfiniteTalk once, return the first
        success as a {"output", "backend", ...} dict or None if every
        alternate failed / was unavailable.

        Retries are capped at 1 attempt across the two providers — this
        is invoked only after MuseTalk produced a frozen bake, so we
        do not want to amplify the latency cost by retrying each
        provider independently. ``provider_chain.try_chain`` handles
        the per-provider Sentry tagging, availability checks, and
        fall-through semantics.
        """
        try:
            from services.provider_chain import try_chain, AllProvidersFailedError
            from services.render_providers import (
                FalHalloProvider,
                WavespeedInfinitetalkProvider,
            )
            fallover_chain = [
                FalHalloProvider(),
                WavespeedInfinitetalkProvider(),
            ]
            try:
                fallover_result = await try_chain(
                    fallover_chain,
                    step_label="musetalk_freeze_fallover",
                    image_url=image_url,
                    audio_url=audio_url,
                    prompt=prompt,
                    width=width,
                    height=height,
                    duration_s=float(audio_duration_s or 0),
                    audio_duration_s=float(audio_duration_s or 0),
                )
            except AllProvidersFailedError as chain_exc:
                sentry_sdk.capture_exception(chain_exc)
                logger.warning(
                    "MuseTalk freeze fallover: every alternate provider "
                    "failed (%s)", chain_exc,
                )
                return None

            provider_used = fallover_result.get("_provider_used", "unknown")
            backend_label = {
                "fal_hallo": "fal_hallo",
                "fal_musetalk": "fal_hallo",
                "wavespeed_infinitetalk": "wavespeed",
            }.get(provider_used, provider_used)
            clean = {
                k: v for k, v in fallover_result.items()
                if not k.startswith("_")
            }
            logger.info(
                "MuseTalk freeze fallover served by %s",
                provider_used,
            )
            return {
                "output": clean,
                "backend": backend_label,
                "provider_used": provider_used,
                "tier_used": fallover_result.get("_tier_used", -1),
            }
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "MuseTalk freeze fallover raised unexpectedly (%s)", exc,
            )
            return None

    async def _render_on_hostkey(
        self, image_url: str, audio_url: str, prompt: str, width: int, height: int
    ) -> dict:
        """Send render job to HOSTKEY. Blocks until complete (synchronous)."""
        async with httpx.AsyncClient(timeout=HOSTKEY_TIMEOUT) as client:
            resp = await client.post(
                f"{HOSTKEY_URL}/api/infinitetalk-render",
                json={
                    "image_url": image_url,
                    "wav_url": audio_url,
                    "prompt": prompt,
                    "width": width,
                    "height": height,
                },
            )
            if resp.status_code == 503:
                raise RuntimeError("HOSTKEY GPU busy (503)")
            resp.raise_for_status()
            return resp.json()

    async def _render_on_modal(
        self, image_url: str, audio_url: str, prompt: str, width: int, height: int
    ) -> dict:
        """Send render job to the Modal serverless endpoint.

        Modal returns 303 See Other for long-running jobs that exceed the
        synchronous HTTP window (≈150s by default). The Location header points
        at a polling URL (`...?__modal_function_call_id=fc-xxx`). Hitting that
        URL blocks until the function completes and returns the real response;
        it may itself 303 again if the function is still running — so we loop
        on redirects up to MODAL_TIMEOUT.

        httpx's follow_redirects does NOT work here because Modal expects the
        next GET (not POST) to carry the same auth, and our overall wall-time
        must be bounded. We handle the redirect dance manually.
        """
        headers = {"Content-Type": "application/json"}
        if MODAL_TOKEN_ID and MODAL_TOKEN_SECRET:
            headers["Authorization"] = f"Bearer {MODAL_TOKEN_ID}:{MODAL_TOKEN_SECRET}"

        # Per-HTTP-request timeout. Give Modal a generous window to finish
        # synchronously before the first 303, then use short polls on redirects.
        per_request_timeout = httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)
        # Liveness ceiling on the polling loop: if Modal keeps 303-ing past
        # this many seconds we treat the function call as a zombie and let
        # the dispatcher fall through to the next tier.
        liveness_deadline_s = time.monotonic() + MODAL_MAX_WAIT_S

        async with httpx.AsyncClient(timeout=per_request_timeout, follow_redirects=False) as client:
            # 1. Submit
            resp = await client.post(
                MODAL_ENDPOINT_URL,
                json={
                    "image_url": image_url,
                    "audio_url": audio_url,
                    "prompt": prompt,
                    "width": width,
                    "height": height,
                },
                headers=headers,
            )

            # 2. Follow 303 "not ready yet" redirects. Modal's polling endpoint
            #    hangs until the call completes or the read timeout expires,
            #    at which point it 303s again pointing to itself.
            while resp.status_code in (302, 303):
                next_url = resp.headers.get("location") or resp.headers.get("Location")
                if not next_url:
                    raise RuntimeError(f"Modal {resp.status_code} with no Location header")
                if time.monotonic() > liveness_deadline_s:
                    raise ModalStuck(
                        f"Modal liveness ceiling exceeded (>{MODAL_MAX_WAIT_S}s of 303s) — "
                        "treating function call as stuck, falling through to next tier"
                    )
                # Resolve relative URLs against the original endpoint.
                if next_url.startswith("/"):
                    from urllib.parse import urlparse
                    p = urlparse(MODAL_ENDPOINT_URL)
                    next_url = f"{p.scheme}://{p.netloc}{next_url}"
                logger.info("Modal 303 — polling function call at %s", next_url)
                resp = await client.get(next_url, headers=headers)

            # 3. Final response: anything 4xx/5xx surfaces the body for debugging.
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"Modal HTTP {resp.status_code}: {resp.text[:500]}"
                )
            body = resp.json()
            if body.get("error"):
                raise RuntimeError(f"Modal error: {body.get('error')}")
            return body

    # ─────────────────────────────────────────────────────────────────────
    # Text-to-Video (generated_video blocks)
    # ─────────────────────────────────────────────────────────────────────
    #
    # T2V is now used only for `generated_video` blocks (no avatar). The
    # legacy avatar_motion category was merged into avatar_action which
    # uses the I2V (Kling/Wan) interpolation path between FLUX-generated
    # scene frames so the avatar's face is preserved.
    # The dispatch is HOSTKEY first (free, on-prem GPU), then fal.ai Wan 2.2
    # T2V as a paid fallback so the pipeline never blocks on hardware.
    #
    # Output shape matches the I2V flow: {"output": {"video": <b64>},
    # "backend": "<name>"}. cast_render.py decodes the same way.
    async def submit_t2v(
        self,
        prompt: str,
        width: int = 480,
        height: int = 848,
        duration_s: float = 5.0,
        reference_image_url: str | None = None,
    ) -> dict:
        """Generate a T2V (generated_video) block.

        When `reference_image_url` is provided, dispatches as image-to-video
        so the avatar's face/wardrobe is preserved across motion blocks.
        Otherwise falls back to text-to-video.

        Primary: HOSTKEY /api/text-to-video (WanVideo T2V on GPU). HOSTKEY
        is currently text-only — when a reference image is supplied we
        skip HOSTKEY and go straight to fal i2v (HOSTKEY i2v parity is a
        future change).
        Fallback: fal.ai Wan 2.2 (T2V or I2V via wan_tti2v_client).

        Motion overshoot (Phase 1.1): ``duration_s`` is the block's slot
        length. We deliberately request a LONGER clip — slot ×
        MOTION_OVERSHOOT_FACTOR snapped up to the provider's nearest
        supported tier — so the bake is always ≥ slot. The surplus is
        trimmed off the head downstream by
        ``services.block_extension.trim_block_to_slot``. A motion clip is
        never padded / reversed / stretched up to length.
        """
        from services.render_providers import (
            MOTION_OVERSHOOT_FACTOR,
            WAN_25_T2V_DURATIONS,
            _overshoot_target_s,
            _snap_up_tier,
        )

        slot_s = float(duration_s) if duration_s else 5.0
        overshot_s = float(
            _snap_up_tier(_overshoot_target_s(slot_s), WAN_25_T2V_DURATIONS)
        )
        logger.info(
            "T2V motion overshoot: slot=%.2fs × %.2f → request=%.2fs "
            "(tiers=%s)",
            slot_s, MOTION_OVERSHOOT_FACTOR, overshot_s,
            WAN_25_T2V_DURATIONS,
        )
        duration_s = overshot_s
        # §5.4: the requested tier (overshot, snapped) is what the provider is
        # asked to bill for — NOT the slot. Carry it back in every result dict
        # so the usage-emission site can charge requested-tier-seconds.
        requested_tier_s = overshot_s
        # ── Tier 1: HOSTKEY T2V ──
        # Same per-loop Semaphore(1) as the I2V path — the GPU can only
        # do one job at a time across both pipelines, so they share the
        # serialization gate. Siblings wait up to HOSTKEY_QUEUE_WAIT_S
        # before falling through to fal.ai.
        # When reference_image_url is set, HOSTKEY (text-only) cannot
        # honor the avatar look — skip it entirely and use fal i2v.
        if HOSTKEY_RENDER_ENABLED and not reference_image_url:
            sem = _get_loop_semaphore()
            if sem is not None:
                try:
                    # PR #70: short wait (3 s) for symmetry with the I2V
                    # path — sibling blocks fall through to cloud fast.
                    await asyncio.wait_for(sem.acquire(), timeout=3.0)
                except asyncio.TimeoutError as _sem_to:
                    sentry_sdk.capture_exception(_sem_to)
                    logger.info(
                        "HOSTKEY busy after short wait — falling back to next tier",
                    )
                else:
                    try:
                        if await self._hostkey_available():
                            logger.info("Dispatching T2V to HOSTKEY (Tier 1, free)")
                            result = await self._t2v_on_hostkey(prompt, width, height, duration_s)
                            return {
                                "output": result,
                                "backend": "hostkey_t2v",
                                "requested_tier_s": requested_tier_s,
                            }
                        else:
                            logger.info("HOSTKEY GPU not available for T2V — falling back")
                    except Exception as exc:
                        sentry_sdk.capture_exception(exc)
                        logger.warning("HOSTKEY T2V failed: %s — falling back", exc)
                    finally:
                        sem.release()
            else:
                logger.info("HOSTKEY semaphore unavailable (no running loop) — falling back to fal.ai T2V")

        # ── Tier 2: fal.ai Wan 2.2 (T2V or I2V) ──
        try:
            logger.info(
                "Dispatching to fal.ai Wan 2.2 (%s, fallback)",
                "i2v" if reference_image_url else "t2v",
            )
            fal_result = await self._t2v_on_fal(
                prompt, width, height, duration_s,
                reference_image_url=reference_image_url,
            )
            fal_result.setdefault("requested_tier_s", requested_tier_s)
            return fal_result
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.error("fal.ai motion-block dispatch failed: %s", exc)
            raise

    async def _t2v_on_hostkey(
        self, prompt: str, width: int, height: int, duration_s: float,
    ) -> dict:
        """Send T2V job to the HOSTKEY gpu-worker."""
        async with httpx.AsyncClient(timeout=HOSTKEY_TIMEOUT) as client:
            resp = await client.post(
                f"{HOSTKEY_URL}/api/text-to-video",
                json={
                    "prompt": prompt,
                    "width": width,
                    "height": height,
                    "duration_seconds": duration_s,
                },
            )
            if resp.status_code == 503:
                raise RuntimeError("HOSTKEY T2V busy (503)")
            resp.raise_for_status()
            return resp.json()

    async def _t2v_on_fal(
        self,
        prompt: str,
        width: int,
        height: int,
        duration_s: float,
        reference_image_url: str | None = None,
    ) -> dict:
        """Fallback: fal.ai Wan 2.2.

        When `reference_image_url` is provided, uses Wan 2.2 i2v so the
        avatar's face/wardrobe is preserved across motion blocks.
        Otherwise uses Wan 2.2 t2v.

        Returns shape compatible with the HOSTKEY response:
        {"output": {"video": <b64>}, "backend": "fal_t2v" | "fal_i2v"}.
        """
        import base64
        from services.wan_tti2v_client import WanTTI2VClient

        # Wan only supports 5s or 10s — round up to the nearest supported.
        wan_duration = 10 if duration_s > 7 else 5

        # Aspect ratio — vertical canvas (9:16) is the default for cast
        # blocks, but allow 16:9 for landscape requests.
        aspect = "9:16" if height >= width else "16:9"

        client = WanTTI2VClient()
        mode = "i2v" if reference_image_url else "t2v"
        # The Wan 2.2 client takes ``duration`` as an int and maps it to a
        # frame count internally (``num_frames``), so the param type here is
        # correctly int — distinct from the Wan 2.7 I2V endpoint which expects
        # an enum string (Render_Quality_Duration_Validation.md §1.1).
        logger.info(
            "motion bake request provider=fal_%s requested=%.3fs "
            "param_type=%s param_value=%r",
            mode, duration_s, type(wan_duration).__name__, wan_duration,
        )
        if reference_image_url:
            video_bytes = await client.image_to_video(
                reference_image_url=reference_image_url,
                prompt=prompt,
                duration=wan_duration,
                aspect_ratio=aspect,  # type: ignore[arg-type]
            )
            backend = "fal_i2v"
        else:
            video_bytes = await client.text_to_video(
                prompt=prompt,
                duration=wan_duration,
                aspect_ratio=aspect,  # type: ignore[arg-type]
            )
            backend = "fal_t2v"
        logger.info(
            "motion bake result provider=%s requested=%ds bytes=%d",
            backend, wan_duration, len(video_bytes),
        )
        video_b64 = base64.b64encode(video_bytes).decode("ascii")
        # §5.4: the fal Wan 2.2 endpoint only serves 5s / 10s, so the actual
        # requested tier is ``wan_duration`` (not the upstream overshot float).
        # Surface it for requested-tier billing at the usage-emission site.
        return {
            "output": {"video": video_b64},
            "backend": backend,
            "requested_tier_s": float(wan_duration),
        }

    async def _render_on_runpod(
        self, image_url: str, audio_url: str, prompt: str, size: str, audio_duration_s: float
    ) -> dict:
        """Submit to RunPod serverless and poll until complete.

        Resolution is FORCED to 480p regardless of cast quality, because
        720p on RunPod takes ~30 min/block and trips the 1800s ceiling.
        480p takes ~10 min/block and reliably succeeds. The quality
        difference on a phone screen is minimal and we can FFmpeg-upscale
        with lanczos in the compose pass if needed. 480p that WORKS is
        infinitely better than 720p that FAILS at 1808s by 8 seconds.
        """
        runpod_size = "480p"
        if size != runpod_size:
            logger.info(
                "RunPod: forcing %s -> 480p (720p hits 30 min ceiling on RunPod)",
                size,
            )
        job_id = await self.runpod.submit_video_job(
            image_url=image_url,
            audio_url=audio_url,
            prompt=prompt,
            size=runpod_size,
        )
        result = await self.runpod.wait_for_completion(
            job_id,
            audio_duration_s=audio_duration_s,
            quality=runpod_size,
        )

        # wait_for_completion now raises on CANCELLED / TIMED_OUT / FAILED,
        # so reaching this check with a non-COMPLETED status would be a
        # contract bug in the client. Surface the actual terminal status
        # plus any error detail so the cascade can log it sensibly.
        if result.get("status") != "COMPLETED":
            output = result.get("output") or {}
            detail = (
                result.get("error")
                or (output.get("error") if isinstance(output, dict) else None)
                or (output.get("message") if isinstance(output, dict) else None)
                or "no detail"
            )
            exc = RuntimeError(
                f"RunPod job ended in {result.get('status')!r}: {detail}"
            )
            sentry_sdk.capture_exception(exc)
            raise exc

        return {"output": result.get("output", {}), "backend": "runpod"}

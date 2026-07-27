"""RunPod service adapter for InfiniteTalk video generation.

Two modes:
- Webhook (cast generation): submit_video_job_webhook() — fire and forget, RunPod POSTs result to our webhook
- Polling (avatar test video): submit_video_job() + wait_for_completion() — blocks until done

State machine (A1):
  QUEUED → IN_PROGRESS → COMPLETED
  QUEUED → ENDPOINT_DOWN (health non-200 for 2+ consecutive checks)
  IN_PROGRESS → STALLED (no change in 5 min) → auto-retry once → FAILED
  Any → FAILED (explicit RunPod FAILED or exhausted retries)

No hardcoded outer timeout. The Celery task_time_limit is the dead-end failsafe.
"""

import asyncio
import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
import sentry_sdk
from services.runpod_error_translate import normalize_error_message

from config import settings

logger = logging.getLogger(__name__)

# WEBHOOK_BASE = "https://www.luminacast.com/api/webhooks/runpod"
def _webhook_base() -> str:
    domain = getattr(settings, "APP_DOMAIN", None) or "localhost"
    if domain in ("localhost", "127.0.0.1"):
        return "https://www.luminacast.com/api/webhooks/runpod"
    scheme = "http" if domain.replace(".", "").isdigit() else "https"
    return f"{scheme}://{domain}/api/webhooks/runpod"

WEBHOOK_BASE = _webhook_base()


def calculate_stall_threshold(audio_duration_s: float, quality: str = "480p") -> int:
    """Calculate max wait based on audio length and quality tier.

    Empirical timing from RunPod InfiniteTalk v1.2.2 logs:
    - 480p:  ~22s per denoising step x 6 steps per window
    - 720p:  ~60s per step x 6 steps per window
    - 1080p: ~250s per step x 6 steps per window

    Frames = audio_duration_s x 30 fps
    Windows = ceil(frames / 81)  (InfiniteTalk uses 81-frame windows with overlap)
    """
    fps = 30
    frames = max(int(audio_duration_s * fps), 81)
    windows = math.ceil(frames / 81)

    STEP_TIME = {"480p": 22, "720p": 60, "1080p": 250}
    step_time = STEP_TIME.get(quality, 22)
    steps = 6

    render_seconds = windows * steps * step_time
    cold_start_buffer = 180   # 3 min for worker cold start
    vae_encode_buffer = 120   # 2 min for VAE decode + MP4 encoding + base64

    threshold = render_seconds + cold_start_buffer + vae_encode_buffer

    # Floor 10 min, ceiling 10 min by default — reduced from 4 hours per
    # ops review. RunPod jobs that take longer than 10 min of stalled
    # IN_PROGRESS are almost certainly broken and should fall through.
    # Override per-deploy via RUNPOD_STALL_CAP_S in the VPS .env if a
    # legitimate slow render needs more (e.g. 1080p with very long audio).
    cap_s = int(os.environ.get("RUNPOD_STALL_CAP_S", "600"))
    return max(600, min(threshold, cap_s))


# Wall-clock ceiling for the entire wait_for_completion polling loop.
# This is the absolute upper bound — a job that hits this ceiling is
# considered broken and the dispatcher falls through to the next tier.
#
# We do NOT hardcode a single value because per-block render time scales
# with audio length and quality (an 8s block at 480p is fundamentally
# different from a 30s block at 720p). Instead the ceiling is computed
# per-job from `calculate_stall_threshold(audio, quality)` plus a queue
# wait buffer (RunPod's worker pool can take 1-3 min to schedule a job)
# plus a safety multiplier for slow workers. Capped at
# RUNPOD_CEILING_HARD_CAP_S (env override) so a pathological audio length
# can't blow past Celery's task_time_limit and break the whole render.
#
# Empirically: an 8s 480p block needs ~10 min, a 30s 480p block ~30 min,
# and we've seen RunPod workers occasionally take 40 min on a 30s block.
# Defaults calibrated against observed successful renders today:
# - 30-min successful run on a ~10s 480p block (block #7 of yesterday's
#   render finished at 1796s and was cut off by the old 1800s cap).
# - 26-min run on a ~12s block (block #5 finished at 1533s).
# Multiplier 2.0 + queue buffer 300s gives ~33 min ceiling for a 10s
# block, comfortably above the slowest success we've seen, while the
# 3300s hard cap keeps us under Celery's 3600s task_time_limit.
RUNPOD_CEILING_QUEUE_BUFFER_S = int(os.environ.get("RUNPOD_CEILING_QUEUE_BUFFER_S", "300"))
RUNPOD_CEILING_SAFETY_MULTIPLIER = float(os.environ.get("RUNPOD_CEILING_SAFETY_MULTIPLIER", "2.0"))
RUNPOD_CEILING_HARD_CAP_S = int(os.environ.get("RUNPOD_CEILING_HARD_CAP_S", "3300"))
RUNPOD_CEILING_FLOOR_S = int(os.environ.get("RUNPOD_CEILING_FLOOR_S", "600"))


def calculate_wall_clock_ceiling(audio_duration_s: float, quality: str = "480p") -> int:
    """How long this single RunPod job is allowed to run before we abandon
    it and let the dispatcher fall through.

    Built from the same audio-length formula as the stall threshold, plus
    queue-wait buffer, plus a safety multiplier. Floor 10 min, hard cap
    just under Celery's 3600s task_time_limit so the whole task survives.
    """
    base = calculate_stall_threshold(audio_duration_s, quality)
    ceiling = int(base * RUNPOD_CEILING_SAFETY_MULTIPLIER) + RUNPOD_CEILING_QUEUE_BUFFER_S
    return max(RUNPOD_CEILING_FLOOR_S, min(ceiling, RUNPOD_CEILING_HARD_CAP_S))
# Friendly progress messages shown to users during GPU rendering
_RENDER_WAIT_MESSAGES = [
    "Warming up the GPU... grab a coffee ☕",
    "Still rendering... the GPU is working hard 🔧",
    "Almost there... patience pays off 🎬",
    "The render is cooking... hang tight 🍳",
    "GPU is doing its thing... won't be long now ⚡",
]

async def _update_avatar_render_progress(avatar_id: str, poll_count: int, status: str):
    """Update avatar progress_step with friendly messages during rendering."""
    if not avatar_id:
        return
    try:
        from models.avatar import Avatar, AvatarPhase
        factory = None
        try:
            from core.db import async_session_factory
            factory = async_session_factory
        except ImportError:
            return
        if not factory:
            return
        msg_idx = min(poll_count // 6, len(_RENDER_WAIT_MESSAGES) - 1)  # Change every ~60s
        msg = _RENDER_WAIT_MESSAGES[msg_idx]
        if status == "IN_QUEUE":
            msg = "Queued for rendering... the GPU will pick this up shortly 🎯"
        async with factory() as session:
            avatar = await session.get(Avatar, avatar_id)
            if avatar and avatar.active_phase not in ("failed",):
                avatar.progress_step = msg
                await session.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)  # Best-effort, don't break the polling loop

HEALTH_FAIL_INTERVAL = 20  # seconds between health checks
HEALTH_FAIL_CONSECUTIVE = 2  # consecutive failures → ENDPOINT_DOWN


def _log(level: str, service: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": service,
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


def _now():
    # render_jobs uses TIMESTAMP columns without timezone, so writes here must
    # stay UTC-naive to avoid asyncpg "can't subtract offset-naive and
    # offset-aware datetimes" errors.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RunPodService:
    """InfiniteTalk via RunPod — supports both public and private endpoints."""

    def __init__(self):
        self.api_key = settings.RUNPOD_API_KEY
        endpoint_id = getattr(settings, 'RUNPOD_ENDPOINT_ID', '') or 'infinitetalk'
        self.endpoint_id = endpoint_id
        self.base_url = f"https://api.runpod.ai/v2/{endpoint_id}"
        self.is_public = getattr(settings, 'RUNPOD_ENDPOINT_IS_PUBLIC', False)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    SIZE_MAP = {
        "480p": (480, 854),
        "720p": (720, 1280),
        "1080p": (1080, 1920),
    }

    def _build_input_payload(self, image_url: str, audio_url: str, prompt: str, size: str) -> dict:
        w, h = self.SIZE_MAP.get(size, (480, 854))
        logger.info(f"InfiniteTalk: size={size}, width={w}, height={h}")
        # #region debug-point B:payload-build
        try: import json as _dj, urllib.request as _du, time as _dt; _p='.dbg/avatar-lipsync-missing.env'; _u='http://127.0.0.1:7777/event'; _s='avatar-lipsync-missing'; exec("try:\n c=open(_p).read(); _u=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SERVER_URL=')),_u); _s=next((l.split('=',1)[1] for l in c.split('\\n') if l.startswith('DEBUG_SESSION_ID=')),_s)\nexcept: pass"); _du.urlopen(_du.Request(_u, data=_dj.dumps({'sessionId':_s,'runId':'pre-fix','hypothesisId':'B','location':'runpod._build_input_payload','msg':'[DEBUG] Building RunPod payload','data':{'is_public':self.is_public,'size':size,'width':w,'height':h,'image_url_suffix':image_url[-120:],'audio_url_suffix':audio_url[-120:]},'ts':int(_dt.time()*1000)}).encode(), headers={'Content-Type':'application/json'}), timeout=1).read()
        except Exception: pass
        # #endregion

        if self.is_public:
            return {
                "input": {
                    "prompt": prompt,
                    "image": image_url,
                    "audio": audio_url,
                    "size": size,
                    "width": w,
                    "height": h,
                    "enable_safety_checker": True,
                }
            }
        else:
            return {
                "input": {
                    "input_type": "image",
                    "person_count": "single",
                    "prompt": prompt,
                    "image_url": image_url,
                    # The private GPU worker schema expects `audio_url`
                    # (see infra/gpu-worker/worker.py: InfiniteTalkRequest).
                    # Keep `wav_url` too for backwards-compat with older
                    # worker variants / templates that may still read it.
                    "audio_url": audio_url,
                    "wav_url": audio_url,
                    "width": w,
                    "height": h,
                    "force_offload": False,
                }
            }

    # ------------------------------------------------------------------
    # render_jobs helpers — write/update rows in the render_jobs table
    # ------------------------------------------------------------------

    async def _create_render_job(
        self, db, job_type: str, provider: str, external_job_id: str,
        avatar_id: str | None = None, cast_id: str | None = None,
        variant_id: str | None = None, metadata: dict | None = None,
    ) -> str:
        """Insert a render_jobs row. Returns the render_job id."""
        from sqlalchemy import text as sa_text
        rj_id = f"rj_{uuid.uuid4().hex[:16]}"
        now = _now()
        await db.execute(
            sa_text("""
                INSERT INTO render_jobs
                    (id, job_type, provider, external_job_id, avatar_id, cast_id, variant_id,
                     state, created_at, queued_at, last_state_change_at, metadata)
                VALUES
                    (:id, :job_type, :provider, :ext_id, :avatar_id, :cast_id, :variant_id,
                     'QUEUED', :now, :now, :now, :meta)
            """),
            {
                "id": rj_id, "job_type": job_type, "provider": provider,
                "ext_id": external_job_id, "avatar_id": avatar_id,
                "cast_id": cast_id, "variant_id": variant_id,
                "now": now, "meta": json.dumps(metadata or {}),
            },
        )
        await db.commit()
        return rj_id

    async def _update_render_job_state(
        self, db, rj_id: str, state: str,
        error_message: str | None = None,
        progress_percent: int | None = None,
    ):
        """Transition a render_job to a new state."""
        from sqlalchemy import text as sa_text
        now = _now()
        set_clauses = ["state = :state", "last_state_change_at = :now", "last_status_check_at = :now"]
        params: dict = {"rj_id": rj_id, "state": state, "now": now}

        if state == "IN_PROGRESS":
            set_clauses.append("started_at = COALESCE(started_at, :now)")
        elif state == "COMPLETED":
            set_clauses.append("completed_at = :now")
        elif state in ("FAILED", "ENDPOINT_DOWN", "STALLED"):
            set_clauses.append("failed_at = :now")

        if error_message is not None:
            set_clauses.append("error_message = :err")
            params["err"] = error_message
        if progress_percent is not None:
            set_clauses.append("progress_percent = :pct")
            params["pct"] = progress_percent

        sql = f"UPDATE render_jobs SET {', '.join(set_clauses)} WHERE id = :rj_id"
        await db.execute(sa_text(sql), params)
        await db.commit()

    # ------------------------------------------------------------------
    # submit methods
    # ------------------------------------------------------------------

    async def submit_video_job(
        self, image_url: str, audio_url: str,
        prompt: str = "A person talking naturally to the camera",
        size: str = "480p",
    ) -> str:
        """Submit InfiniteTalk job (polling mode). Returns job_id."""
        with sentry_sdk.start_span(op='runpod', description='InfiniteTalk submit') as span:
            span.set_data('endpoint_id', self.endpoint_id)
            span.set_data('size', size)
            _log("info", "runpod", "Submitting InfiniteTalk job (polling)",
                 endpoint=self.endpoint_id, is_public=self.is_public, size=size)

            payload = self._build_input_payload(image_url, audio_url, prompt, size)

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/run",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            job_id = data["id"]
            span.set_data('job_id', job_id)
            _log("info", "runpod", "InfiniteTalk job submitted (polling)", job_id=job_id, status=data.get("status"))
            return job_id

    async def submit_video_job_webhook(
        self, image_url: str, audio_url: str, variant_id: str,
        prompt: str = "A person talking naturally to the camera",
        size: str = "480p",
    ) -> str:
        """Submit InfiniteTalk job with webhook callback. Returns job_id immediately."""
        with sentry_sdk.start_span(op='runpod', description='InfiniteTalk submit (webhook)') as span:
            span.set_data('endpoint_id', self.endpoint_id)
            span.set_data('size', size)
            span.set_data('variant_id', variant_id)

            payload = self._build_input_payload(image_url, audio_url, prompt, size)
            payload["webhook"] = f"{WEBHOOK_BASE}/infinitetalk"

            _log("info", "runpod", "Submitting InfiniteTalk job (webhook)",
                 endpoint=self.endpoint_id, variant_id=variant_id,
                 size=size, webhook=payload["webhook"])

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/run",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

            job_id = data.get("id", "")
            span.set_data('job_id', job_id)
            _log("info", "runpod", "InfiniteTalk job submitted (webhook)",
                 job_id=job_id, variant_id=variant_id, endpoint=self.endpoint_id)

            return job_id

    # ------------------------------------------------------------------
    # status / health
    # ------------------------------------------------------------------

    async def check_job_status(self, job_id: str) -> dict:
        """Check the current status of a RunPod job."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/status/{job_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def poll_job_status(self, job_id: str) -> dict:
        """Poll job status. Returns {"status": str, "output": dict | None}."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/status/{job_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()

        status = data.get("status", "UNKNOWN")
        output = data.get("output")
        _log("info", "runpod", "Job status", job_id=job_id, status=status)
        return {"status": status, "output": output, "raw": data}

    async def check_health(self) -> dict:
        """Check RunPod endpoint health — workers, queue depth, job counts."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/health",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"RunPod health check failed: {e}")
            return {}

    # ------------------------------------------------------------------
    # wait_for_completion — state-machine driven, no hardcoded timeout
    # ------------------------------------------------------------------

    async def wait_for_completion(
        self, job_id: str, poll_interval: int = 10,
        db=None, render_job_id: str | None = None,
        job_type: str = "avatar_preview",
        avatar_id: str | None = None,
        cast_id: str | None = None,
        variant_id: str | None = None,
        audio_duration_s: float = 15.0,
        quality: str = "480p",
        user_id: str | None = None,
    ) -> dict:
        """Poll for job completion using the 5-state machine.

        No hardcoded outer timeout — the Celery task_time_limit is the failsafe.

        State transitions:
        - Before polling: check /health. If non-200 for 2 consecutive checks → ENDPOINT_DOWN
        - Poll every 10s. If status changed, update last_state_change_at.
        - IN_PROGRESS for stall_threshold with no state change → STALLED → auto-retry once → FAILED
        """
        with sentry_sdk.start_span(op='runpod', description=f'InfiniteTalk poll {job_id}') as span:
            span.set_data('job_id', job_id)

            stall_threshold = calculate_stall_threshold(audio_duration_s, quality)
            job_ceiling_s = calculate_wall_clock_ceiling(audio_duration_s, quality)
            logger.info(
                "Job %s: stall threshold=%ds, wall-clock ceiling=%ds (audio=%.1fs, quality=%s)",
                job_id, stall_threshold, job_ceiling_s, audio_duration_s, quality,
            )

            # Create render_job row if db provided and no existing row
            rj_id = render_job_id
            if db and not rj_id:
                rj_id = await self._create_render_job(
                    db, job_type=job_type, provider="runpod_infinitetalk",
                    external_job_id=job_id, avatar_id=avatar_id,
                    cast_id=cast_id, variant_id=variant_id,
                )

            # Step 1: Health check — 2 consecutive failures → ENDPOINT_DOWN
            consecutive_health_fails = 0
            for _ in range(HEALTH_FAIL_CONSECUTIVE + 1):
                health = await self.check_health()
                if health:
                    workers = health.get("workers", {})
                    jobs = health.get("jobs", {})
                    total_available = (
                        workers.get("ready", 0)
                        + workers.get("idle", 0)
                        + workers.get("running", 0)
                    )
                    logger.info(
                        "RunPod health: %d queued, %d in progress, %d available workers",
                        jobs.get("inQueue", 0), jobs.get("inProgress", 0), total_available,
                    )
                    span.set_data("queue_depth", jobs.get("inQueue", 0))
                    span.set_data("workers_available", total_available)
                    consecutive_health_fails = 0
                    break
                else:
                    consecutive_health_fails += 1
                    if consecutive_health_fails >= HEALTH_FAIL_CONSECUTIVE:
                        if db and rj_id:
                            await self._update_render_job_state(
                                db, rj_id, "ENDPOINT_DOWN",
                                error_message="Health endpoint non-200 for 2+ consecutive checks",
                            )
                        raise RuntimeError(
                            "RunPod endpoint is down — health check failed for "
                            f"{HEALTH_FAIL_CONSECUTIVE} consecutive checks"
                        )
                    await asyncio.sleep(HEALTH_FAIL_INTERVAL)

            # Step 2: Poll loop — state machine
            last_status_change = time.time()
            last_status = None
            poll_count = 0
            retry_attempted = False
            poll_started_at = time.time()

            while True:
                # Wall-clock ceiling: per-job, computed from audio length
                # and quality (NOT hardcoded). 8s/480p → ~16 min ceiling,
                # 30s/480p → ~50 min, scaled by RUNPOD_CEILING_SAFETY_MULTIPLIER.
                # Capped at RUNPOD_CEILING_HARD_CAP_S to stay under Celery
                # task_time_limit. The dispatcher's outer cascade will
                # fall through to the next provider when this raises.
                elapsed_total = time.time() - poll_started_at
                if elapsed_total > job_ceiling_s:
                    err = (
                        f"RunPod job {job_id} exceeded wall-clock ceiling "
                        f"({int(elapsed_total)}s > {job_ceiling_s}s for "
                        f"{audio_duration_s:.1f}s/{quality}) — "
                        f"abandoning so the dispatcher can fall through."
                    )
                    _log("warning", "runpod", err, job_id=job_id)
                    if db and rj_id:
                        try:
                            await self._update_render_job_state(
                                db, rj_id, "FAILED", error_message=err
                            )
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                    raise RuntimeError(err)

                result = await self.check_job_status(job_id)
                current_status = result.get("status", "UNKNOWN")

                # Update render_job check timestamp
                if db and rj_id:
                    from sqlalchemy import text as sa_text
                    await db.execute(
                        sa_text("UPDATE render_jobs SET last_status_check_at = :now WHERE id = :rj_id"),
                        {"now": _now(), "rj_id": rj_id},
                    )
                    await db.commit()

                if current_status != last_status:
                    last_status_change = time.time()
                    last_status = current_status
                    _log("info", "runpod", f"Job {job_id}: {current_status}",
                         job_id=job_id, poll_count=poll_count)

                    # Update render_job state
                    if db and rj_id:
                        rj_state = {
                            "IN_QUEUE": "QUEUED",
                            "IN_PROGRESS": "IN_PROGRESS",
                            "COMPLETED": "COMPLETED",
                            "FAILED": "FAILED",
                        }.get(current_status, None)
                        if rj_state:
                            await self._update_render_job_state(db, rj_id, rj_state)

                time_in_status = time.time() - last_status_change

                # COMPLETED
                if current_status == "COMPLETED":
                    span.set_data('actual_polls', poll_count)
                    span.set_data('status', 'completed')
                    _log("info", "runpod", "Job completed", job_id=job_id)
                    if db and rj_id:
                        await self._update_render_job_state(db, rj_id, "COMPLETED", progress_percent=100)
                    try:
                        from services.usage_logger import log_api_usage
                        await log_api_usage(
                            user_id=user_id, service="infinitetalk", operation="video_generation",
                            success=True, duration_seconds=round(poll_count * poll_interval, 1),
                            runpod_job_id=job_id,
                            cost_cents=max(1, int(poll_count * poll_interval / 3600 * 100)),
                        )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                    output = result.get("output")
                    return {"status": "COMPLETED", "output": output, "raw": result}

                # FAILED
                if current_status == "FAILED":
                    output = result.get("output")
                    error = (
                        result.get("error")
                        or (output.get("error") if isinstance(output, dict) else "")
                        or (output.get("message") if isinstance(output, dict) else "")
                        or (json.dumps(output)[:500] if output else "")
                        or "RunPod job failed with no error message"
                    )
                    span.set_data('actual_polls', poll_count)
                    span.set_data('status', 'failed')
                    _log("error", "runpod", f"Job failed: {error}", job_id=job_id)
                    if db and rj_id:
                        await self._update_render_job_state(db, rj_id, "FAILED", error_message=str(error)[:500])
                    try:
                        from services.usage_logger import log_api_usage
                        await log_api_usage(
                            user_id=user_id, service="infinitetalk", operation="video_generation",
                            success=False, duration_seconds=round(poll_count * poll_interval, 1),
                            runpod_job_id=job_id, error_message=str(error)[:500],
                        )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                    raise RuntimeError(f"Render job ended in 'FAILED': {normalize_error_message(str(error))}")

                # CANCELLED / TIMED_OUT — RunPod terminal states. Exit the
                # polling loop immediately and raise so the dispatcher can
                # fall through to the next tier without waiting on a job
                # that will never complete. Error message includes the
                # actual status name so log readers see WHY it ended.
                if current_status in ("CANCELLED", "TIMED_OUT"):
                    span.set_data('actual_polls', poll_count)
                    span.set_data('status', current_status.lower())
                    err = f"Render job ended in {current_status!r}: terminal RunPod state"
                    _log("warning", "runpod", err, job_id=job_id)
                    if db and rj_id:
                        try:
                            await self._update_render_job_state(db, rj_id, "FAILED", error_message=err)
                        except Exception as e:
                            sentry_sdk.capture_exception(e)
                    try:
                        from services.usage_logger import log_api_usage
                        await log_api_usage(
                            user_id=user_id, service="infinitetalk", operation="video_generation",
                            success=False, duration_seconds=round(poll_count * poll_interval, 1),
                            runpod_job_id=job_id, error_message=err,
                        )
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                    raise RuntimeError(err)

                # Periodic health check during IN_PROGRESS / IN_QUEUE
                if poll_count > 0 and poll_count % 12 == 0:  # every ~2 minutes
                    health = await self.check_health()
                    if not health:
                        consecutive_health_fails += 1
                        if consecutive_health_fails >= HEALTH_FAIL_CONSECUTIVE:
                            if db and rj_id:
                                await self._update_render_job_state(
                                    db, rj_id, "ENDPOINT_DOWN",
                                    error_message="Health non-200 during polling",
                                )
                            raise RuntimeError("RunPod endpoint went down during polling")
                    else:
                        consecutive_health_fails = 0

                # STALLED detection — IN_PROGRESS with no state change beyond threshold
                if current_status == "IN_PROGRESS" and time_in_status > stall_threshold:
                    if not retry_attempted:
                        # Auto-retry once
                        _log("warning", "runpod",
                             f"Job {job_id} stalled ({time_in_status:.0f}s), auto-retrying",
                             job_id=job_id)
                        if db and rj_id:
                            await self._update_render_job_state(db, rj_id, "STALLED")

                        retry_attempted = True
                        # Re-dispatch by cancelling and resubmitting not supported by RunPod.
                        # Instead, we just reset the stall timer and give it one more window.
                        last_status_change = time.time()
                        last_status = None  # Force re-detection
                    else:
                        # Retry also stalled → FAILED
                        err = "Video rendering timed out — the GPU took too long. Please try again."
                        if db and rj_id:
                            await self._update_render_job_state(db, rj_id, "FAILED", error_message=err)
                        raise RuntimeError(err)

                # Update avatar with friendly wait message
                if avatar_id and poll_count % 3 == 0:  # Every ~30s
                    await _update_avatar_render_progress(avatar_id, poll_count, current_status or "IN_PROGRESS")

                poll_count += 1
                await asyncio.sleep(poll_interval)

    # Legacy method for cast_generator.py
    async def submit_clip_job(
        self, image_url: str, audio_url: str, output_r2_key: str,
        mode: str = "image_to_video", resolution: str = "480p",
    ) -> str:
        return await self.submit_video_job(
            image_url=image_url, audio_url=audio_url,
            prompt="A person talking and presenting products on a live stream",
            size=resolution,
        )


_instance: RunPodService | None = None


def get_runpod_service() -> RunPodService:
    global _instance
    if _instance is None:
        _instance = RunPodService()
    return _instance

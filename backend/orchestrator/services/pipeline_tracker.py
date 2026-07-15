"""Pipeline step tracker — writes render_jobs rows for each avatar pipeline step.

Each Celery task calls `start_step()` before dispatch and `complete_step()` / `fail_step()`
on completion. The resume endpoint reads these rows to determine where to restart.
"""
import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from models.render_job import RenderJob, RenderJobState, RenderJobType, RenderProvider

logger = logging.getLogger(__name__)

# Pipeline step ordering — used by resume logic
CLONE_PIPELINE_STEPS = [
    RenderJobType.CLONE_UPLOAD,
    RenderJobType.CLONE_FACE_EXTRACT,
    RenderJobType.CLONE_VOICE_EXTRACT,
    RenderJobType.CLONE_VOICE_TRAINING,
    RenderJobType.CLONE_PREVIEW_RENDER,
]

AI_PIPELINE_STEPS = [
    RenderJobType.AI_FACE_GENERATION,
    RenderJobType.AI_FACE_SELECTION,
    RenderJobType.AI_VOICE_GENERATION,
    RenderJobType.AI_VOICE_SELECTION,
    RenderJobType.AI_BODY_SHOTS,
    RenderJobType.AI_PREVIEW_RENDER,
]

# Human-readable step names
STEP_LABELS = {
    RenderJobType.CLONE_UPLOAD: "Uploading your video",
    RenderJobType.CLONE_FACE_EXTRACT: "Extracting your face from the video",
    RenderJobType.CLONE_VOICE_EXTRACT: "Isolating your voice",
    RenderJobType.CLONE_VOICE_TRAINING: "Training your voice",
    RenderJobType.CLONE_PREVIEW_RENDER: "Rendering your preview",
    RenderJobType.AI_FACE_GENERATION: "Generating face options",
    RenderJobType.AI_FACE_SELECTION: "Waiting for face selection",
    RenderJobType.AI_VOICE_GENERATION: "Generating voice options",
    RenderJobType.AI_VOICE_SELECTION: "Waiting for voice selection",
    RenderJobType.AI_BODY_SHOTS: "Generating body shots",
    RenderJobType.AI_PREVIEW_RENDER: "Rendering your preview",
}

# Default ETA estimates in seconds (fallback when no historical data)
STEP_ETA_DEFAULTS = {
    RenderJobType.CLONE_UPLOAD: 30,
    RenderJobType.CLONE_FACE_EXTRACT: 120,
    RenderJobType.CLONE_VOICE_EXTRACT: 180,
    RenderJobType.CLONE_VOICE_TRAINING: 600,
    RenderJobType.CLONE_PREVIEW_RENDER: 2700,
    RenderJobType.AI_FACE_GENERATION: 120,
    RenderJobType.AI_FACE_SELECTION: 0,  # user action
    RenderJobType.AI_VOICE_GENERATION: 120,
    RenderJobType.AI_VOICE_SELECTION: 0,  # user action
    RenderJobType.AI_BODY_SHOTS: 300,
    RenderJobType.AI_PREVIEW_RENDER: 2700,
}


async def start_step(
    session: AsyncSession,
    avatar_id: str,
    job_type: RenderJobType,
    provider: RenderProvider = RenderProvider.HOSTKEY_LOCAL,
    metadata: dict | None = None,
) -> str:
    """Create a new render_jobs row in QUEUED state. Returns the job ID."""
    job_id = f"rj_{uuid.uuid4().hex[:16]}"
    now = datetime.now(timezone.utc)
    job = RenderJob(
        id=job_id,
        job_type=job_type,
        provider=provider,
        avatar_id=avatar_id,
        state=RenderJobState.QUEUED,
        created_at=now,
        queued_at=now,
        last_state_change_at=now,
        metadata_=metadata or {},
    )
    session.add(job)
    await session.commit()
    logger.info(f"Pipeline step started: {job_type.value} for avatar {avatar_id} (job {job_id})")
    return job_id


async def mark_in_progress(session: AsyncSession, job_id: str, external_job_id: str | None = None):
    """Transition a step to IN_PROGRESS."""
    now = datetime.now(timezone.utc)
    await session.execute(
        update(RenderJob)
        .where(RenderJob.id == job_id)
        .values(
            state=RenderJobState.IN_PROGRESS,
            started_at=now,
            last_state_change_at=now,
            external_job_id=external_job_id,
        )
    )
    await session.commit()


async def complete_step(session: AsyncSession, job_id: str):
    """Mark a pipeline step as COMPLETED."""
    now = datetime.now(timezone.utc)
    await session.execute(
        update(RenderJob)
        .where(RenderJob.id == job_id)
        .values(
            state=RenderJobState.COMPLETED,
            completed_at=now,
            last_state_change_at=now,
            progress_percent=100,
        )
    )
    await session.commit()
    logger.info(f"Pipeline step completed: job {job_id}")


async def fail_step(session: AsyncSession, job_id: str, error_message: str):
    """Mark a pipeline step as FAILED."""
    now = datetime.now(timezone.utc)
    await session.execute(
        update(RenderJob)
        .where(RenderJob.id == job_id)
        .values(
            state=RenderJobState.FAILED,
            failed_at=now,
            last_state_change_at=now,
            error_message=error_message[:500],
        )
    )
    await session.commit()
    logger.info(f"Pipeline step failed: job {job_id} — {error_message[:100]}")


async def supersede_step(session: AsyncSession, job_id: str):
    """Mark a failed/stalled step as SUPERSEDED (replaced by a retry)."""
    now = datetime.now(timezone.utc)
    await session.execute(
        update(RenderJob)
        .where(RenderJob.id == job_id)
        .values(
            state=RenderJobState.SUPERSEDED,
            last_state_change_at=now,
        )
    )
    await session.commit()


async def get_pipeline_jobs(session: AsyncSession, avatar_id: str) -> list[RenderJob]:
    """Get all render_jobs for an avatar ordered by creation time."""
    result = await session.execute(
        select(RenderJob)
        .where(RenderJob.avatar_id == avatar_id)
        .order_by(RenderJob.created_at)
    )
    return list(result.scalars().all())


async def get_pipeline_state(session: AsyncSession, avatar_id: str) -> dict:
    """Compute the current pipeline state from render_jobs rows.

    Returns a dict with per-step status, the overall state, and the first failed step.
    """
    jobs = await get_pipeline_jobs(session, avatar_id)
    if not jobs:
        return {"steps": {}, "overall": "no_jobs", "failed_step": None, "running_step": None}

    # Determine pipeline type from job types
    job_types = {j.job_type for j in jobs}
    is_clone = any(jt.value.startswith("clone_") for jt in job_types)
    pipeline_steps = CLONE_PIPELINE_STEPS if is_clone else AI_PIPELINE_STEPS

    # Build per-step status — latest non-superseded job for each step
    steps = {}
    for step_type in pipeline_steps:
        step_jobs = [j for j in jobs if j.job_type == step_type and j.state != RenderJobState.SUPERSEDED]
        if step_jobs:
            latest = step_jobs[-1]
            steps[step_type.value] = {
                "job_id": latest.id,
                "state": latest.state.value if isinstance(latest.state, RenderJobState) else latest.state,
                "error_message": latest.error_message,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
                "started_at": latest.started_at.isoformat() if latest.started_at else None,
                "completed_at": latest.completed_at.isoformat() if latest.completed_at else None,
                "progress_percent": latest.progress_percent,
                "label": STEP_LABELS.get(step_type, step_type.value),
                "default_eta_seconds": STEP_ETA_DEFAULTS.get(step_type, 60),
            }
        else:
            steps[step_type.value] = {
                "job_id": None,
                "state": "PENDING",
                "error_message": None,
                "created_at": None,
                "started_at": None,
                "completed_at": None,
                "progress_percent": None,
                "label": STEP_LABELS.get(step_type, step_type.value),
                "default_eta_seconds": STEP_ETA_DEFAULTS.get(step_type, 60),
            }

    # Determine overall state
    failed_step = None
    running_step = None
    all_complete = True
    for step_type in pipeline_steps:
        st = steps[step_type.value]["state"]
        if st in ("FAILED", "STALLED", "ENDPOINT_DOWN"):
            failed_step = step_type.value
            all_complete = False
            break
        if st in ("QUEUED", "IN_PROGRESS"):
            running_step = step_type.value
            all_complete = False
        if st == "PENDING":
            all_complete = False

    if all_complete:
        overall = "complete"
    elif failed_step:
        overall = "failed"
    elif running_step:
        overall = "running"
    else:
        overall = "pending"

    return {
        "steps": steps,
        "overall": overall,
        "failed_step": failed_step,
        "running_step": running_step,
        "pipeline_type": "clone" if is_clone else "ai",
    }

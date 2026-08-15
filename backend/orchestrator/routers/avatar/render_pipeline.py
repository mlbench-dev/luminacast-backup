"""Avatar render_pipeline endpoints — split from the former routers/avatar.py."""

from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy import update as sa_update
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User, TeamRole
from models.avatar import Avatar, AvatarType, AvatarStatus, BodyShotSet
from models.avatar_look import AvatarLook
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.fish_audio import get_fish_audio_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)
import re

logger = logging.getLogger(__name__)

from ._shared import normalize_tiktok_input, _r2_key_to_url, AvatarResponse, _avatar_to_response, _BODY_MOTION_POSES, _BODY_MOTION_POSE_LABELS, _seed_body_motion_looks_from_body_shot_set

router = APIRouter()

@router.post("/{avatar_id}/regenerate-preview-video")
async def regenerate_preview_video(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a real talking-head preview render using InfiniteTalk.
    Uses locked face image + locked voice audio + locked_test_script."""
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")
        if not avatar.face_ref_key:
            raise HTTPException(status_code=400, detail="Face image required")
        if not avatar.voice_id:
            raise HTTPException(status_code=400, detail="Voice not locked yet")
        if not avatar.locked_test_script:
            raise HTTPException(status_code=400, detail="Test script not locked yet")

        from services.r2_storage import get_r2_storage_service
        from services.fish_audio import get_fish_audio_service
        from services.runpod import get_runpod_service

        r2 = get_r2_storage_service()
        fish = get_fish_audio_service()
        runpod = get_runpod_service()

        avatar.progress_step = "Generating voice audio..."
        avatar.progress_percent = 10
        await db.commit()

        # Step 1: Generate TTS audio from locked script
        tts_result = await fish.generate_tts(
            text=avatar.locked_test_script,
            voice_id=avatar.voice_id,
        )
        audio_key = tts_result.get("audio_key") or f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/preview_audio.mp3"
        lipsync_audio_key = tts_result.get("lipsync_audio_key") or audio_key
        if tts_result.get("tmp_path"):
            import os
            try:
                os.unlink(tts_result["tmp_path"])
            except OSError:
                pass

        tts_duration = tts_result.get("duration_seconds", len(avatar.locked_test_script.split()) / 2.5)

        avatar.progress_step = "Rendering talking-head video..."
        avatar.progress_percent = 30
        await db.commit()

        # Step 2: Submit InfiniteTalk job
        face_url = r2.get_signed_url(avatar.face_ref_key, expires_in=7200)
        try:
            from services.lipsync_audio_prep import prepare_lipsync_audio
            raw_audio_url = r2.get_signed_url(lipsync_audio_key, expires_in=7200)
            audio_url = await prepare_lipsync_audio(
                raw_audio_url,
                render_id=f"avatar_preview_{avatar_id}",
                block_id=f"avatar_preview_{avatar_id}",
                r2=r2,
            )
        except Exception:
            audio_url = r2.get_signed_url(lipsync_audio_key, expires_in=7200)

        job_id = await runpod.submit_video_job(
            image_url=face_url,
            audio_url=audio_url,
            prompt="A person talking naturally to the camera",
            size="480p",
        )

        avatar.progress_step = "Waiting for video render..."
        avatar.progress_percent = 50
        await db.commit()

        # Step 3: Poll for completion. State-machine drives stall detection;
        # no outer timeout (per project rule "No hardcoded render timeouts").
        result = await runpod.wait_for_completion(
            job_id,
            poll_interval=5,
            audio_duration_s=tts_duration,
            quality="480p",
            user_id=ctx.workspace_owner_id,        )

        output = result.get("output")
        if not output:
            raise HTTPException(status_code=500, detail="AI render returned no output")

        # Step 4: Download and upload to R2
        video_url = output.get("video_url") or output.get("url") or output.get("result", {}).get("url")
        if not video_url:
            raise HTTPException(status_code=500, detail="AI render produced no video")

        import httpx
        async with httpx.AsyncClient() as client:
            vid_resp = await client.get(video_url, timeout=120)
            vid_resp.raise_for_status()
            video_bytes = vid_resp.content

        preview_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/preview_video.mp4"
        await r2.upload_bytes(video_bytes, preview_key, "video/mp4")

        avatar.preview_video_key = preview_key
        avatar.test_video_key = preview_key
        avatar.progress_step = "Preview video ready"
        avatar.progress_percent = 100
        await db.commit()

        return {
            "preview_video_url": r2.get_public_url(preview_key),
            "status": "complete",
        }

    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"regenerate-preview-video failed: {e}")
        avatar.progress_step = f"Preview render failed: {str(e)[:100]}"
        avatar.progress_percent = 0
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e)[:200])

@router.get("/{avatar_id}/render-jobs")
async def get_avatar_render_jobs(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return full per-step pipeline history for an avatar.

    Used by PipelineProgressView to hydrate state on page return.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.pipeline_tracker import get_pipeline_state
    state = await get_pipeline_state(db, avatar_id)

    return {
        "avatar_id": avatar_id,
        "avatar_status": avatar.status.value if hasattr(avatar.status, 'value') else avatar.status,
        "avatar_phase": avatar.active_phase.value if hasattr(avatar.active_phase, 'value') else avatar.active_phase,
        **state,
    }

@router.post("/{avatar_id}/resume-pipeline")
async def resume_pipeline(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Re-dispatch only the failed/stalled step. Smart resume.

    Returns {resumed_from_step, render_job_id} or {status: "already_complete"}.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.pipeline_tracker import (
        get_pipeline_state, get_pipeline_jobs, supersede_step, start_step,
        CLONE_PIPELINE_STEPS, AI_PIPELINE_STEPS,
    )
    from models.render_job import RenderJobType, RenderJobState, RenderProvider
    from services.r2_storage import get_r2_storage_service

    state = await get_pipeline_state(db, avatar_id)
    r2 = get_r2_storage_service()

    # If all complete, return early
    if state["overall"] == "complete":
        return {"status": "already_complete", "avatar_id": avatar_id}

    # If currently running, don't double-dispatch
    if state["overall"] == "running":
        raise HTTPException(status_code=409, detail="Pipeline is still running. Wait for the current step to finish.")

    # Find the first failed/stalled step
    failed_step_name = state.get("failed_step")
    if not failed_step_name:
        raise HTTPException(status_code=400, detail="No failed step found to resume.")

    # Parse job type
    try:
        failed_job_type = RenderJobType(failed_step_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown step type: {failed_step_name}")

    # Validate inputs exist for this step
    input_errors = await _validate_resume_inputs(avatar, failed_job_type, r2)
    if input_errors:
        raise HTTPException(status_code=409, detail=input_errors)

    # Supersede the old failed job
    failed_job_id = state["steps"].get(failed_step_name, {}).get("job_id")
    if failed_job_id:
        await supersede_step(db, failed_job_id)

    # Create a new job row in QUEUED state
    new_job_id = await start_step(db, avatar_id, failed_job_type, RenderProvider.HOSTKEY_LOCAL)

    # Reset avatar status to PROCESSING
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = f"Resuming: {failed_step_name}"
    await db.commit()

    # Re-dispatch the Celery task for this step
    _dispatch_step_task(avatar, ctx.workspace_owner_id, failed_job_type, new_job_id)

    return {
        "resumed_from_step": failed_step_name,
        "render_job_id": new_job_id,
        "avatar_id": avatar_id,
    }

async def _validate_resume_inputs(avatar, job_type: "RenderJobType", r2) -> str | None:
    """Check that the inputs for a given step still exist. Returns error string or None."""
    from models.render_job import RenderJobType as RJT

    if job_type == RJT.CLONE_FACE_EXTRACT:
        if not avatar.video_ref_key:
            scrubber_key = f"creators/{avatar.user_id}/avatar/{avatar.id}/scrubber_video_0.mp4"
            if not await r2.key_exists(scrubber_key):
                return "Uploaded video no longer exists. Please re-upload and start from the beginning."
    elif job_type == RJT.CLONE_VOICE_EXTRACT:
        if not avatar.video_ref_key:
            scrubber_key = f"creators/{avatar.user_id}/avatar/{avatar.id}/scrubber_video_0.mp4"
            if not await r2.key_exists(scrubber_key):
                return "Uploaded video no longer exists. Please re-upload."
    elif job_type == RJT.CLONE_VOICE_TRAINING:
        if not avatar.voice_sample_key:
            return "Voice corpus not found. Need to re-run voice extraction first."
    elif job_type == RJT.CLONE_PREVIEW_RENDER:
        if not avatar.face_ref_key:
            return "Face image missing. Need to re-run face extraction."
        if not avatar.voice_id and not avatar.voice_sample_key:
            return "Voice not available. Need to re-run voice training."
    elif job_type == RJT.AI_FACE_GENERATION:
        pass  # No upstream deps
    elif job_type == RJT.AI_VOICE_GENERATION:
        pass  # No upstream deps
    elif job_type == RJT.AI_BODY_SHOTS:
        if not avatar.face_ref_key:
            return "Face image missing. Need to re-run face generation."
    elif job_type == RJT.AI_PREVIEW_RENDER:
        if not avatar.face_ref_key:
            return "Face image missing."
        if not avatar.voice_id:
            return "Voice not available."

    return None

def _dispatch_step_task(avatar, user_id: str, job_type: "RenderJobType", job_id: str):
    """Dispatch the correct Celery task for a given pipeline step."""
    from models.render_job import RenderJobType as RJT

    # Clone pipeline steps
    if job_type == RJT.CLONE_UPLOAD:
        from tasks.generate_avatar import clone_avatar_task
        clone_avatar_task.delay(avatar.id, user_id, avatar.tiktok_source_url)
    elif job_type in (RJT.CLONE_FACE_EXTRACT, RJT.CLONE_VOICE_EXTRACT):
        from tasks.generate_avatar import clone_avatar_task
        clone_avatar_task.delay(avatar.id, user_id, avatar.tiktok_source_url)
    elif job_type in (RJT.CLONE_VOICE_TRAINING, RJT.CLONE_PREVIEW_RENDER):
        from tasks.generate_avatar import generate_from_selection_task
        generate_from_selection_task.delay(avatar.id, user_id)
    # AI pipeline steps
    elif job_type in (RJT.AI_FACE_GENERATION, RJT.AI_VOICE_GENERATION, RJT.AI_BODY_SHOTS, RJT.AI_PREVIEW_RENDER):
        from tasks.generate_avatar import generate_digital_avatar_task
        generate_digital_avatar_task.delay(
            avatar.id, user_id,
            avatar.description or "",
            avatar.voice_style or "energetic",
            "energetic_beauty",
            avatar.background or "studio",
            avatar.camera_position or "waist_up",
            avatar.style or "photorealistic",
            avatar.ai_model or "meta-llama/llama-3-70b-instruct",
        )
    else:
        logger.warning(f"No task dispatch for step {job_type.value}")

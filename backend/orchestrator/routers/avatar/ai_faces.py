"""Avatar ai_faces endpoints — split from the former routers/avatar.py."""

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

class CreateAIAvatarRequest(BaseModel):
    name: Optional[str] = "AI Avatar"

class CreateAIAvatarResponse(BaseModel):
    avatar_id: str

class AvatarSlotSummaryResponse(BaseModel):
    included: int
    purchased: int
    total: int
    used: int
    remaining: int

@router.get("/slots", response_model=AvatarSlotSummaryResponse)
async def get_avatar_slots(
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Avatar slot usage for the current workspace — same permission level
    and same underlying check as POST /ai/create and the Clone Yourself
    creation endpoints, so the "New avatar" cards can grey themselves out
    before the user clicks through to a creation flow that's just going to
    402, instead of only finding out after landing on it."""
    from services import billing_service
    summary = await billing_service.get_avatar_slot_summary(db, ctx.workspace_owner_id)
    return AvatarSlotSummaryResponse(**summary)

@router.post("/ai/create", response_model=CreateAIAvatarResponse, status_code=201)
async def create_ai_avatar(
    req: CreateAIAvatarRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Create an AI avatar record — no pipeline launched yet."""
    from services import billing_service
    await billing_service.check_avatar_slot_available(db, ctx.workspace_owner_id)

    avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
    avatar = Avatar(
        id=avatar_id,
        user_id=ctx.workspace_owner_id,        type=AvatarType.DIGITAL,
        # DRAFT, not PROCESSING: no pipeline has run yet, this is just the
        # empty shell the setup wizard fills in. PROCESSING here made the
        # card un-resumable (Setup.tsx excludes PROCESSING from
        # isClickableForResume) AND put it on the 30-minute stale-PROCESSING
        # auto-fail sweep in list_avatars — so a user who took their time in
        # the wizard came back to a failed, hidden avatar. The first real
        # pipeline step (voice preview / render) flips it to PROCESSING itself.
        status=AvatarStatus.DRAFT,
        name=req.name or "AI Avatar",
        progress_step="Waiting for face and voice selection",
        progress_percent=0,
    )
    db.add(avatar)
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="avatar.create", entity_type="avatar",
            entity_id=avatar_id, after={"name": avatar.name, "type": "ai"},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return CreateAIAvatarResponse(avatar_id=avatar_id)

class GenerateFacesRequest(BaseModel):
    description: str = ""
    reference_photo_url: Optional[str] = None

class GenerateFacesResponse(BaseModel):
    face_urls: list[str]

@router.post("/ai/generate-faces", response_model=GenerateFacesResponse)
async def ai_generate_faces(
    req: GenerateFacesRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Generate 8 face images using FLUX Kontext Pro."""
    import fal_client
    import os
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    from services.ai_prompts import get_prompt

    description = req.description or "A professional, friendly-looking person suitable for live streaming"

    base_prompt = description
    if req.reference_photo_url:
        base_prompt = f"{description}. Reference photo style."


    import asyncio
    face_urls = []

    async def generate_one(seed: int):
        try:
            args = {
                "prompt": base_prompt,
                "guidance_scale": 3.5,
                "num_inference_steps": 28,
                "output_format": "jpeg",
                "seed": seed,
            }
            if req.reference_photo_url:
                args["image_url"] = req.reference_photo_url

            model = "fal-ai/flux-pro/kontext" if req.reference_photo_url else "fal-ai/flux/dev"
            result = await fal_client.run_async(model, arguments=args)
            images = result.get("images", [])
            if images:
                return images[0].get("url", "")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"Face generation failed for seed {seed}: {e}")
        return None

    import random
    seeds = [random.randint(1, 999999) for _ in range(8)]
    results = await asyncio.gather(*[generate_one(s) for s in seeds])
    face_urls = [url for url in results if url]

    if not face_urls:
        raise HTTPException(status_code=500, detail="Face generation failed. Please try again.")

    return GenerateFacesResponse(face_urls=face_urls)

class AIEditFaceRequest(BaseModel):
    face_url: str
    instructions: str

class AIEditFaceResponse(BaseModel):
    original_url: str
    edited_url: str

@router.post("/ai/edit-face", response_model=AIEditFaceResponse)
async def ai_edit_face(
    req: AIEditFaceRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Edit a generated face using FLUX Kontext Pro."""
    import fal_client
    import os
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    from services.ai_prompts import get_prompt
    edit_prompt = f"{req.instructions}. Keep the same person identity and face structure."
    prompt = edit_prompt

    result = await fal_client.run_async(
        "fal-ai/flux-pro/kontext",
        arguments={
            "prompt": prompt,
            "image_url": req.face_url,
            "guidance_scale": 3.5,
            "num_inference_steps": 28,
            "output_format": "jpeg",
        },
    )

    images = result.get("images", [])
    if not images:
        raise HTTPException(status_code=500, detail="Face editing failed — no image returned")

    edited_url = images[0].get("url", "")
    if not edited_url:
        raise HTTPException(status_code=500, detail="Face editing returned empty URL")

    return AIEditFaceResponse(original_url=req.face_url, edited_url=edited_url)

class AISelectFaceRequest(BaseModel):
    face_url: str

@router.post("/ai/{avatar_id}/select-face")
async def ai_select_face(
    avatar_id: str,
    req: AISelectFaceRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Set the selected face URL as the avatar's face_ref_key."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    # Download the fal.ai image and re-upload to R2 for persistence
    import httpx
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(req.face_url)
            resp.raise_for_status()
            face_bytes = resp.content

        face_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/face_ref.jpg"
        await r2.upload_bytes(face_bytes, face_key, "image/jpeg")
        avatar.face_ref_key = face_key
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        # Fall back to storing the URL directly
        avatar.face_ref_key = req.face_url
        await db.commit()
        logger.warning(f"Failed to persist face to R2: {e}")

    return {"status": "ok"}

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
    avatar_id: Optional[str] = None
    description: str = ""
    reference_photo_url: Optional[str] = None
    # "9:16" | "16:9" | "1:1" | "4:5" — see models/avatar.py's Avatar.layout.
    layout: Optional[str] = None

class GenerateFacesResponse(BaseModel):
    face_urls: list[str]

@router.post("/ai/generate-faces", response_model=GenerateFacesResponse)
async def ai_generate_faces(
    req: GenerateFacesRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate 8 face images using FLUX Kontext Pro."""
    import fal_client
    import os
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    from services.ai_prompts import get_prompt
    from services.aspect_conform import IMAGE_SIZE_BY_LAYOUT

    description = req.description or "A professional, friendly-looking person suitable for live streaming"

    base_prompt = description
    if req.reference_photo_url:
        base_prompt = f"{description}. Reference photo style."

    image_size = IMAGE_SIZE_BY_LAYOUT.get(req.layout, IMAGE_SIZE_BY_LAYOUT["9:16"])

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
                "image_size": image_size,
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

    if req.avatar_id:
        avatar = await db.get(Avatar, req.avatar_id)
        if avatar and avatar.user_id == ctx.workspace_owner_id:
            avatar.face_candidates = face_urls
            if req.layout:
                avatar.layout = req.layout
            await db.commit()

    return GenerateFacesResponse(face_urls=face_urls)

class AIEditFaceRequest(BaseModel):
    avatar_id: Optional[str] = None
    face_url: str
    instructions: str

class AIEditFaceResponse(BaseModel):
    original_url: str
    edited_url: str

# Reject obviously out-of-scope requests before spending a FLUX call on them.
# This is a fast, free first gate for the common case (explicit "change the
# background" wording) — a keyword list can never enumerate every phrasing
# ("back scene", "different setting", "put me on a beach", ...), so
# _is_out_of_scope_edit below covers whatever this misses, and
# _lock_background_to_original still runs regardless as the hard guarantee.
_OUT_OF_SCOPE_PATTERN = re.compile(
    r"\b(background|backround|bg|backdrop|scene|scenery|setting|environment|surroundings|location)\b",
    re.IGNORECASE,
)

async def _is_out_of_scope_edit(instructions: str) -> bool:
    """Cheap LLM fallback for instructions that dodge the keyword pattern but
    still ask for something outside face/hair/accessories (e.g. "put me on a
    beach", "make it look like an office"). Same default model
    (claude-3-haiku) the content-type classifier in services/content_type.py
    uses for this kind of single-word classification — fast and cheap enough
    to run on every edit request that isn't already caught by the regex.
    Fails open (returns False) on any error so a classifier hiccup never
    blocks a legitimate edit.
    """
    try:
        from services.openrouter import get_openrouter_service
        result = await get_openrouter_service().generate_text(
            prompt=f'Edit instructions: """{instructions.strip()[:300]}"""',
            system_prompt=(
                "You classify photo-edit instructions for a face-only editing tool. "
                "Reply with exactly one word.\n"
                "Reply ALLOW if the instructions only change the person's face, hair, "
                "expression, or accessories (glasses, earrings, makeup, hats worn on "
                "the head, etc).\n"
                "Reply REJECT if the instructions ask to change the background, scene, "
                "location, setting, clothing, pose, or add anything unrelated to the "
                "person's face/hair/accessories."
            ),
            temperature=0,
            max_tokens=5,
        )
        return (result or "").strip().upper().startswith("REJECT")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Out-of-scope LLM classification failed, allowing through: {e}")
        return False

async def _lock_background_to_original(original_url: str, edited_url: str, owner_id: str) -> str:
    """Composite the FLUX-edited pixels back onto the original image outside
    the person silhouette, so the background/pose is pixel-identical to the
    original no matter what the model actually drew there. Kontext's "keep
    the background unchanged" instruction is a soft prompt constraint the
    model doesn't reliably honor — a hair-color edit could still redraw the
    whole scene. Returns the composited R2 URL, or the raw ``edited_url``
    unchanged if the composite step fails for any reason.
    """
    import httpx
    from io import BytesIO
    from PIL import Image, ImageFilter
    from rembg import remove as rembg_remove
    from services.r2_storage import get_r2_storage_service

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        original_resp, edited_resp = await client.get(original_url), await client.get(edited_url)
        original_resp.raise_for_status()
        edited_resp.raise_for_status()
        original_bytes, edited_bytes = original_resp.content, edited_resp.content

    original_img = Image.open(BytesIO(original_bytes)).convert("RGB")
    edited_img = Image.open(BytesIO(edited_bytes)).convert("RGB")
    if edited_img.size != original_img.size:
        edited_img = edited_img.resize(original_img.size)

    # Person-silhouette alpha matte from the ORIGINAL photo — this is the
    # region allowed to take the edited pixels; everything outside it is
    # forced back to the original. Feather the edge slightly to avoid a
    # visible seam.
    matte = rembg_remove(original_bytes)
    mask = Image.open(BytesIO(matte)).convert("RGBA").split()[-1].filter(ImageFilter.GaussianBlur(3))

    composited = Image.composite(edited_img, original_img, mask)
    buf = BytesIO()
    composited.save(buf, "JPEG", quality=92)

    key = f"creators/{owner_id}/avatar/face_edits/{uuid.uuid4().hex}.jpg"
    r2 = get_r2_storage_service()
    await r2.upload_bytes(buf.getvalue(), key, "image/jpeg")
    return r2.get_public_url(key)

@router.post("/ai/edit-face", response_model=AIEditFaceResponse)
async def ai_edit_face(
    req: AIEditFaceRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Edit a generated face using FLUX Kontext Pro."""
    if _OUT_OF_SCOPE_PATTERN.search(req.instructions) or await _is_out_of_scope_edit(req.instructions):
        raise HTTPException(
            status_code=400,
            detail="This tool only edits facial features (hair, glasses, expression, accessories, etc.) — background and scene changes aren't supported.",
        )

    import fal_client
    import os
    from config import settings as _settings

    if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = _settings.FAL_API_KEY

    prompt = (
        f"{req.instructions}. Apply this change only to the person's face, hair, and "
        "accessories. Keep the same person identity, face structure, pose, framing, "
        "clothing, background, and lighting completely unchanged."
    )

    edit_args = {
        "prompt": prompt,
        "image_url": req.face_url,
        "guidance_scale": 3.5,
        "num_inference_steps": 28,
        "output_format": "jpeg",
    }
    if req.avatar_id:
        from services.aspect_conform import IMAGE_SIZE_BY_LAYOUT
        edit_avatar = await db.get(Avatar, req.avatar_id)
        if edit_avatar and edit_avatar.layout in IMAGE_SIZE_BY_LAYOUT:
            edit_args["image_size"] = IMAGE_SIZE_BY_LAYOUT[edit_avatar.layout]

    result = await fal_client.run_async(
        "fal-ai/flux-pro/kontext",
        arguments=edit_args,
    )

    images = result.get("images", [])
    if not images:
        raise HTTPException(status_code=500, detail="Face editing failed — no image returned")

    edited_url = images[0].get("url", "")
    if not edited_url:
        raise HTTPException(status_code=500, detail="Face editing returned empty URL")

    try:
        edited_url = await _lock_background_to_original(req.face_url, edited_url, ctx.workspace_owner_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Background-lock composite failed, returning raw edit: {e}")

    if req.avatar_id:
        avatar = await db.get(Avatar, req.avatar_id)
        if avatar and avatar.user_id == ctx.workspace_owner_id:
            # selected_face_url deliberately stays pointing at the base
            # face_candidates entry (not this edited result) — the frontend
            # restore logic looks it up by index in face_candidates, and the
            # "latest edit is active" rule (mirroring handleEditFace's local
            # behavior) means edited_face_versions alone is enough to know
            # what to show.
            avatar.edited_face_versions = list(avatar.edited_face_versions or []) + [edited_url]
            await db.commit()

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

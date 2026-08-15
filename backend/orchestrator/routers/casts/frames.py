"""Cast frames endpoints — split from the former routers/casts.py."""

import asyncio
import logging
import uuid

logger = logging.getLogger(__name__)
from datetime import datetime, timezone as tz
from typing import Optional, List
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile, File as FastAPIFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from database import get_db
from models.user import User, TeamRole
from models.cast import Cast, CastStatus, CastProduct, CastQuality, CastVersion, CastApprovalStatus
from models.block import Block, BlockType, LayoutMode
from models.variant import Variant, VariantStatus
from models.product import Product
from models.product_asset import ProductAsset
from models.avatar import Avatar, AvatarStatus
from models.channel import Channel
from models.billing_event import BillingEvent, BillingEventType
from schemas.cast import (
    CastCreate, CastResponse, CastListResponse, OutlineResponse, OutlineScene,
    CastPayRequest, GenerationStatusResponse, BlockCreate, ProductCreate,
    BulkBlocksSave,
)
from routers.auth import get_current_user, WorkspaceContext, require_role, require_owner
from services import audit_log
from services.cast_templates import get_template

router = APIRouter()

def _body_motion_look_type(block_id: str, kind: str) -> str:
    return f"body_motion_block_{block_id}_{kind}"

def _action_look_type(block_id: str, kind: str) -> str:
    """look_type for an avatar_action block's per-block scene frame.

    Format `action_block_<block_id>_<kind>` keeps it well under the 80-char
    avatar_looks.look_type limit (block_id is 16 chars: `blk_` + 12 hex).
    """
    return f"action_block_{block_id}_{kind}"

def _avatar_appearance_text(avatar: Avatar | None) -> str:
    """Return the avatar's physical-description text used to lock identity
    into FLUX Kontext / I2V prompts.

    Prefers `appearance_prompt` (LLM-generated visual prompt set during the
    clone pipeline), falls back to `body_description` (Gemini full-body
    description), then `description` (free-form). Empty if nothing is set.
    """
    if avatar is None:
        return ""
    for field in ("appearance_prompt", "body_description", "description"):
        v = getattr(avatar, field, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _prepend_avatar_appearance(prompt: str, avatar: Avatar | None) -> str:
    """Prefix `prompt` with the avatar's physical description.

    Used everywhere we hand a prompt to an image/video model that does NOT
    natively know the avatar's identity (FLUX Kontext scene frames, I2V
    motion prompt). The Director (Opus) is told NOT to repeat appearance
    details in its prompts; this helper is the single place that adds them.
    """
    base = (prompt or "").strip()
    appearance = _avatar_appearance_text(avatar)
    if not appearance:
        return base
    if not base:
        return appearance
    # If the writer already echoed the appearance back (legacy outlines),
    # don't prepend a second copy. Cheap heuristic on the first 60 chars.
    if base.lower().startswith(appearance.lower()[:60]):
        return base
    return f"{appearance}, {base}"

async def _resolve_action_frame_product_ref(db, block, cast, raw_prompt: str):
    """Resolve the product reference image for an action-frame regen.

    Returns ``(product_ref_url, product_name, product_id)``:
    - If ``block.product_id`` is set → use that product's image.
    - Else if the cast has a primary product AND the action prompt (or the
      user-edited scene text already on the block) mentions the product name
      or the literal phrase "the product" → fall back to the primary product,
      and persist ``block.product_id`` so future regens skip the fallback.
    - Otherwise → ``(None, "", None)``.

    Best-effort: any failure degrades to no product ref rather than aborting.
    """
    try:
        from models.product import Product as _Product
        from services.r2_storage import get_r2_storage_service

        def _product_image_url(product) -> str | None:
            if product is None:
                return None
            r2 = get_r2_storage_service()
            if getattr(product, "cover_image_key", None):
                return r2.get_public_url(product.cover_image_key)
            keys = getattr(product, "media_keys", None)
            if isinstance(keys, list) and keys:
                return r2.get_public_url(keys[0])
            return None

        # 1. Block already linked → use it directly.
        block_pid = getattr(block, "product_id", None)
        if block_pid:
            product = await db.get(_Product, block_pid)
            name = (getattr(product, "name", None) or "") if product else ""
            return _product_image_url(product), name, block_pid

        # 2. Fall back to the cast's primary product when the text references it.
        cp = (
            await db.execute(
                select(CastProduct)
                .where(CastProduct.cast_id == cast.id)
                .order_by(CastProduct.position)
                .limit(1)
            )
        ).scalars().first()
        if not cp or not cp.product_id:
            return None, "", None
        product = await db.get(_Product, cp.product_id)
        if product is None:
            return None, "", None
        product_name = (getattr(product, "name", None) or "").strip()

        # Combine the regen prompt with the persisted scene text (the user edit
        # lives on the block's action_*_prompt columns).
        scene_text = " ".join(
            str(t or "")
            for t in (
                raw_prompt,
                getattr(block, "action_start_prompt", None),
                getattr(block, "action_end_prompt", None),
                getattr(block, "body_motion_prompt", None),
            )
        ).lower()
        name_hit = bool(product_name) and any(
            len(tok) >= 4 and tok in scene_text
            for tok in "".join(
                ch if ch.isalnum() else " " for ch in product_name.lower()
            ).split()
        )
        generic_hit = "the product" in scene_text
        if not (name_hit or generic_hit):
            return None, "", None

        # Persist the fallback so future regens don't re-trigger the heuristic.
        block.product_id = cp.product_id
        return _product_image_url(product), product_name, cp.product_id
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return None, "", None

@router.post("/{cast_id}/blocks/{block_id}/body_motion_frame")
async def generate_body_motion_frame(
    cast_id: str,
    block_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new AI start- or end-frame for a body-motion block.

    Body: { kind: "start" | "end", prompt: string, regenerate: bool=false }

    Creates an AvatarLook row in 'pending' state, dispatches the existing
    avatar-look generation pipeline (FLUX Kontext with identity preservation
    against the avatar's face_ref_key), and returns the row immediately.
    The frontend polls the look-list endpoint for completion. When
    regenerate=true the call is treated identically — every frame is just
    another alternate the user can pick from the carousel.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    kind = (payload.get("kind") or "").strip().lower()
    if kind not in ("start", "end"):
        raise HTTPException(400, "kind must be 'start' or 'end'")
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    if len(prompt) > 1000:
        prompt = prompt[:1000]

    avatar_id = getattr(cast, "avatar_id", None)
    if not avatar_id:
        raise HTTPException(400, "Cast has no avatar — cannot generate frames")
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or not getattr(avatar, "face_ref_key", None):
        raise HTTPException(400, "Avatar has no face reference image yet")

    # Persist the prompt the user just used as the seed so reopening the
    # carousel modal pre-fills the textarea with the most recent value.
    if kind == "start":
        block.body_motion_start_prompt = prompt
    else:
        block.body_motion_end_prompt = prompt

    from models.avatar_look import AvatarLook

    look_type_value = _body_motion_look_type(block_id, kind)
    look = AvatarLook(
        id=f"al_{uuid.uuid4().hex[:12]}",
        avatar_id=avatar_id,
        name=prompt[:60],
        background_prompt=prompt,
        is_default=False,
        is_original=False,
        status="pending",
        look_type=look_type_value,
    )
    db.add(look)
    await db.commit()
    await db.refresh(look)

    try:
        from tasks.avatar_looks import generate_avatar_look_task
        generate_avatar_look_task.delay(look.id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        # Surface a useful error rather than leaving a "pending" row
        # that never resolves. The row is kept so the user can see the
        # failed entry and retry from the UI.
        look.status = "failed"
        look.error_message = "AI render dispatcher unavailable; please retry."
        await db.commit()
        raise HTTPException(503, "AI render dispatcher is temporarily unavailable")

    try:
        await audit_log.record(
            db, user_id=user.id, action="block.body_motion_frame.generate",
            entity_type="block", entity_id=block_id, cast_id=cast_id,
            after={
                "kind": kind,
                "look_id": look.id,
                "regenerate": bool(payload.get("regenerate", False)),
                "prompt_preview": prompt[:120],
            },
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    return {
        "id": look.id,
        "avatar_id": look.avatar_id,
        "name": look.name,
        "face_ref_key": look.face_ref_key,
        "image_url": r2.get_public_url(look.face_ref_key) if look.face_ref_key else None,
        "is_default": look.is_default,
        "is_original": look.is_original,
        "status": look.status,
        "error_message": look.error_message,
        "look_type": look.look_type,
        "background_prompt": look.background_prompt,
        "kind": kind,
        "block_id": block_id,
        "created_at": look.created_at.isoformat() if look.created_at else None,
    }

@router.get("/{cast_id}/blocks/{block_id}/body_motion_frames")
async def list_body_motion_frames(
    cast_id: str,
    block_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List the AI-generated start/end frames previously created for this
    block, grouped by kind so the editor's carousel can render two
    independent strips. Includes pending/failed rows so the UI can show
    spinners and error states.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    from models.avatar_look import AvatarLook
    from services.r2_storage import get_r2_storage_service

    prefix = f"body_motion_block_{block_id}_"
    try:
        result = await db.execute(
            select(AvatarLook)
            .where(AvatarLook.avatar_id == cast.avatar_id)
            .where(AvatarLook.look_type.like(f"{prefix}%"))
            .order_by(AvatarLook.created_at.asc())
        )
        rows = result.scalars().all()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        rows = []

    r2 = get_r2_storage_service()
    by_kind: dict[str, list] = {"start": [], "end": []}
    for row in rows:
        suffix = (row.look_type or "")[len(prefix):]
        kind = suffix if suffix in ("start", "end") else None
        if kind is None:
            continue
        by_kind[kind].append({
            "id": row.id,
            "avatar_id": row.avatar_id,
            "name": row.name,
            "face_ref_key": row.face_ref_key,
            "image_url": r2.get_public_url(row.face_ref_key) if row.face_ref_key else None,
            "status": row.status,
            "error_message": row.error_message,
            "look_type": row.look_type,
            "background_prompt": row.background_prompt,
            "kind": kind,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return {
        "block_id": block_id,
        "selected_start_look_id": block.body_motion_start_look_id,
        "selected_end_look_id": block.body_motion_end_look_id,
        "start_prompt_seed": getattr(block, "body_motion_start_prompt", None),
        "end_prompt_seed": getattr(block, "body_motion_end_prompt", None),
        "frames": by_kind,
    }

@router.post("/{cast_id}/blocks/{block_id}/action_frame")
async def generate_action_frame(
    cast_id: str,
    block_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new AI scene frame (start or end) for an avatar_action block.

    Body: { kind: "start" | "end", prompt: string, regenerate: bool=false }

    The block's avatar's physical description is automatically prepended
    to `prompt` so the AI render keeps the avatar's face/identity in the
    scene. The scene-frame is created as an AvatarLook row keyed by
    look_type=`action_block_<block_id>_<kind>` and dispatched through the
    existing FLUX Kontext pipeline. The frontend polls list endpoint for
    completion. `regenerate=true` is informational only — every frame is
    just another alternate the user can pick from the carousel.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    kind = (payload.get("kind") or "").strip().lower()
    if kind not in ("start", "end"):
        raise HTTPException(400, "kind must be 'start' or 'end'")
    raw_prompt = (payload.get("prompt") or "").strip()
    if not raw_prompt:
        raise HTTPException(400, "prompt is required")
    if len(raw_prompt) > 1000:
        raw_prompt = raw_prompt[:1000]

    avatar_id = getattr(cast, "avatar_id", None)
    if not avatar_id:
        raise HTTPException(400, "Cast has no avatar — cannot generate frames")
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or not getattr(avatar, "face_ref_key", None):
        raise HTTPException(400, "Avatar has no face reference image yet")

    # Persist the user's seed so reopening the modal pre-fills it.
    if kind == "start":
        block.action_start_prompt = raw_prompt
    else:
        block.action_end_prompt = raw_prompt

    # Resolve a product reference image so FLUX renders the CHOSEN product in
    # the action frame instead of hallucinating a generic prop
    # (cst_d7424cfa4f36). Prefer block.product_id; fall back to the cast's
    # primary product when the action prompt (or the user-edited scene text)
    # mentions the product, and persist that fallback so future regens don't
    # re-trigger it. Extends PR #163 (which only forced the ref for PRODUCT
    # block_type during initial generation) to the regen route.
    product_ref_url, product_name, used_product_id = await _resolve_action_frame_product_ref(
        db, block, cast, raw_prompt
    )

    # Identity-locking prefix: prepend appearance text so FLUX Kontext
    # renders the SAME face/body in the scene. The user-typed prompt is
    # what we persist on the block, but the look's background_prompt is
    # the augmented version that actually gets sent to FLUX.
    full_prompt = _prepend_avatar_appearance(raw_prompt, avatar)
    if product_ref_url and product_name:
        full_prompt = (
            f"{full_prompt}. The product '{product_name}' is clearly visible "
            "in the frame, prominent and unobstructed."
        )

    logger.info(
        "[action-frame] cast=%s block=%s product_ref_used=%s product_id=%s",
        cast_id, block_id, bool(product_ref_url), used_product_id,
    )

    from models.avatar_look import AvatarLook

    look_type_value = _action_look_type(block_id, kind)
    look = AvatarLook(
        id=f"al_{uuid.uuid4().hex[:12]}",
        avatar_id=avatar_id,
        name=raw_prompt[:60],
        background_prompt=full_prompt,
        is_default=False,
        is_original=False,
        status="pending",
        look_type=look_type_value,
        product_id=used_product_id,
        framing=(getattr(block, "framing", None) or "MEDIUM"),
    )
    db.add(look)
    await db.commit()
    await db.refresh(look)

    try:
        from tasks.avatar_looks import generate_action_frame_look_task
        # product_ref_url already encodes whether the chosen/primary product
        # image should be passed to FLUX; force emphasis is baked into the
        # prompt above so the worker reliably renders the product.
        generate_action_frame_look_task.delay(look.id, product_ref_url or "")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        look.status = "failed"
        look.error_message = "AI render dispatcher unavailable; please retry."
        await db.commit()
        raise HTTPException(503, "AI render dispatcher is temporarily unavailable")

    try:
        await audit_log.record(
            db, user_id=user.id, action="block.action_frame.generate",
            entity_type="block", entity_id=block_id, cast_id=cast_id,
            after={
                "kind": kind,
                "look_id": look.id,
                "regenerate": bool(payload.get("regenerate", False)),
                "prompt_preview": raw_prompt[:120],
            },
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    return {
        "id": look.id,
        "avatar_id": look.avatar_id,
        "name": look.name,
        "face_ref_key": look.face_ref_key,
        "image_url": r2.get_public_url(look.face_ref_key) if look.face_ref_key else None,
        "is_default": look.is_default,
        "is_original": look.is_original,
        "status": look.status,
        "error_message": look.error_message,
        "look_type": look.look_type,
        "background_prompt": look.background_prompt,
        "kind": kind,
        "block_id": block_id,
        "created_at": look.created_at.isoformat() if look.created_at else None,
    }

@router.get("/{cast_id}/blocks/{block_id}/action_frames")
async def list_action_frames(
    cast_id: str,
    block_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List the AI-generated start/end scene frames for an avatar_action block.

    Mirrors list_body_motion_frames but keys on the `action_block_<id>_*`
    look_type prefix so legacy body_motion frames are not surfaced here.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    from models.avatar_look import AvatarLook
    from services.r2_storage import get_r2_storage_service

    prefix = f"action_block_{block_id}_"
    try:
        result = await db.execute(
            select(AvatarLook)
            .where(AvatarLook.avatar_id == cast.avatar_id)
            .where(AvatarLook.look_type.like(f"{prefix}%"))
            .order_by(AvatarLook.created_at.asc())
        )
        rows = result.scalars().all()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        rows = []

    r2 = get_r2_storage_service()
    by_kind: dict[str, list] = {"start": [], "end": []}
    for row in rows:
        suffix = (row.look_type or "")[len(prefix):]
        kind = suffix if suffix in ("start", "end") else None
        if kind is None:
            continue
        by_kind[kind].append({
            "id": row.id,
            "avatar_id": row.avatar_id,
            "name": row.name,
            "face_ref_key": row.face_ref_key,
            "image_url": r2.get_public_url(row.face_ref_key) if row.face_ref_key else None,
            "status": row.status,
            "error_message": row.error_message,
            "look_type": row.look_type,
            "background_prompt": row.background_prompt,
            "kind": kind,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })

    return {
        "block_id": block_id,
        # The renderer reuses the body_motion_*_look_id columns to pin
        # which frame is selected — same plumbing, different semantic.
        "selected_start_look_id": block.body_motion_start_look_id,
        "selected_end_look_id": block.body_motion_end_look_id,
        "start_prompt_seed": getattr(block, "action_start_prompt", None),
        "end_prompt_seed": getattr(block, "action_end_prompt", None),
        "frames": by_kind,
    }

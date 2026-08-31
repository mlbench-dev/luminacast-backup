"""Cast blocks endpoints — split from the former routers/casts.py."""

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
from routers.casts.render import require_no_active_render
from services import audit_log
from services.cast_templates import get_template

router = APIRouter()

@router.put("/{cast_id}/layout")
async def save_layout(
    cast_id: str,
    layout_config: dict = Body(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    cast.layout_config = layout_config
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.layout_update", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id, after={"layout_config": layout_config},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"status": "saved"}

@router.put("/{cast_id}/effects")
async def save_effects_config(
    cast_id: str,
    effects_config: dict = Body(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Save effects configuration for a cast."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    cast.effects_config = effects_config
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.effects_update", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id, after={"effects_config": effects_config},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"status": "saved", "effects_config": effects_config}

@router.post("/{cast_id}/background")
async def upload_cast_background(
    cast_id: str,
    file: UploadFile,
    media_type: str = Query("image"),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a background image or video for a cast."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    ext = "jpg" if media_type == "image" else "mp4"
    r2_key = f"creators/{ctx.workspace_owner_id}/casts/{cast_id}/background.{ext}"

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    file_bytes = await file.read()
    await r2.upload_bytes(file_bytes, r2_key, content_type=file.content_type or f"{'image/jpeg' if media_type == 'image' else 'video/mp4'}")

    # Update effects_config with the new background key
    effects = cast.effects_config or {}
    bg = effects.get("background", {})
    if media_type == "image":
        bg["image_key"] = r2_key
    else:
        bg["video_key"] = r2_key
    effects["background"] = bg
    cast.effects_config = effects
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(cast, "effects_config")

    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.background_upload", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id, after={"r2_key": r2_key, "media_type": media_type},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    from config import settings
    return {"key": r2_key, "url": f"https://media.luminacast.com/{r2_key}"}

@router.post("/{cast_id}/blocks/{block_id}/frames/upload")
async def upload_block_frame(
    cast_id: str,
    block_id: str,
    slot: str = Query(..., regex="^(first|last)$"),
    file: UploadFile = FastAPIFile(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a first or last frame image for a generated_video block.

    The slot parameter must be either 'first' or 'last'. The image is stored
    in R2 and the corresponding column on `blocks` is updated. Returns the
    R2 key + a public URL the frontend can preview.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    import uuid as _uuid
    img_id = _uuid.uuid4().hex[:12]
    ext = "png"
    if file.content_type and "jpeg" in file.content_type:
        ext = "jpg"
    r2_key = f"creators/{ctx.workspace_owner_id}/casts/{cast_id}/blocks/{block_id}/{slot}_frame_{img_id}.{ext}"

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    file_bytes = await file.read()
    await r2.upload_bytes(file_bytes, r2_key, content_type=file.content_type or "image/png")

    if slot == "first":
        block.gen_video_first_frame_key = r2_key
    else:
        block.gen_video_last_frame_key = r2_key
    await db.commit()

    return {
        "r2_key": r2_key,
        "r2_url": f"https://media.luminacast.com/{r2_key}",
        "slot": slot,
        "size_bytes": len(file_bytes),
    }

@router.post("/{cast_id}/scene-objects/upload-image")
async def upload_scene_image(
    cast_id: str,
    file: UploadFile,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload an image for use as a scene object."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    import uuid as _uuid
    img_id = _uuid.uuid4().hex[:12]
    ext = "png"
    if file.content_type and "jpeg" in file.content_type:
        ext = "jpg"
    r2_key = f"creators/{ctx.workspace_owner_id}/casts/{cast_id}/scene/{img_id}.{ext}"

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    file_bytes = await file.read()
    await r2.upload_bytes(file_bytes, r2_key, content_type=file.content_type or "image/png")

    from config import settings
    return {
        "r2_key": r2_key,
        "r2_url": f"https://media.luminacast.com/{r2_key}",
        "size_bytes": len(file_bytes),
    }

@router.post("/{cast_id}/scene-objects/{object_id}/mask-bg")
async def mask_scene_image_bg(
    cast_id: str,
    object_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Remove background from a scene object image using rembg."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    effects = cast.effects_config or {}
    scene_objects = effects.get("scene_objects", [])
    obj = next((o for o in scene_objects if o.get("id") == object_id), None)
    if not obj or obj.get("kind") != "image":
        raise HTTPException(404, "Image scene object not found")

    image_data = obj.get("image", {})
    r2_key = image_data.get("r2_key", "")
    if not r2_key:
        raise HTTPException(400, "No image key found")

    import tempfile
    import os

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    with tempfile.TemporaryDirectory(prefix="rembg_") as tmpdir:
        input_path = os.path.join(tmpdir, "input.png")
        output_path = os.path.join(tmpdir, "output.png")

        # Download from R2
        import httpx
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(f"https://media.luminacast.com/{r2_key}")
            resp.raise_for_status()
            with open(input_path, "wb") as f:
                f.write(resp.content)

        # Run rembg via Python API (not subprocess — CLI may not be on PATH).
        # Pre-warm in production: docker exec orchestrator python3 -c "from rembg import new_session; new_session('u2net')"
        # Lazy import: rembg downloads U2Net (~170MB) on first use; failure here
        # must not prevent the rest of casts.py from loading.
        try:
            from rembg import remove as rembg_remove
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise HTTPException(500, "Background removal unavailable") from exc

        with open(input_path, "rb") as f:
            input_bytes = f.read()
        try:
            result_bytes = await asyncio.to_thread(rembg_remove, input_bytes)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise HTTPException(500, "Background removal failed") from exc
        with open(output_path, "wb") as f:
            f.write(result_bytes)

        # Upload masked image
        masked_key = r2_key.replace(".", "_masked.")
        with open(output_path, "rb") as f:
            masked_bytes = f.read()
        await r2.upload_bytes(masked_bytes, masked_key, content_type="image/png")

    # Update scene object
    for o in scene_objects:
        if o.get("id") == object_id:
            o["image"] = {**image_data, "r2_key": masked_key, "mask_bg": True}
            break
    effects["scene_objects"] = scene_objects
    cast.effects_config = effects
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(cast, "effects_config")
    await db.commit()

    return {
        "r2_key": masked_key,
        "r2_url": f"https://media.luminacast.com/{masked_key}",
    }

@router.put("/{cast_id}/blocks")
async def save_blocks_bulk(
    cast_id: str,
    req: BulkBlocksSave,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Replace all blocks for a cast and create default variants with script text."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    if cast.status in (CastStatus.GENERATING, CastStatus.LIVE, CastStatus.GENERATING_VIDEOS):
        raise HTTPException(400, "Cannot modify blocks while cast is generating or live")

    # Delete existing blocks (cascade deletes variants)
    result = await db.execute(
        select(Block).where(Block.cast_id == cast_id)
    )
    existing_blocks = result.scalars().all()
    for blk in existing_blocks:
        await db.delete(blk)
    await db.flush()

    # Create new blocks with variants
    created_blocks = []
    for blk_data in req.blocks:
        try:
            bt = BlockType(blk_data.type)
        except ValueError:
            bt = BlockType.PRODUCT

        blk_id = f"blk_{uuid.uuid4().hex[:12]}"
        block = Block(
            id=blk_id,
            cast_id=cast_id,
            product_id=blk_data.product_id,
            type=bt,
            position=blk_data.position,
            mood=blk_data.mood,
        )
        db.add(block)

        # Create variants
        if blk_data.variants:
            for v_data in blk_data.variants:
                variant = Variant(
                    id=f"var_{uuid.uuid4().hex[:12]}",
                    block_id=blk_id,
                    script_text=v_data.script_text,
                    variant_label=v_data.variant_label,
                    status=VariantStatus.PENDING,
                )
                db.add(variant)
        elif blk_data.script_text:
            # Convenience: single script_text creates a default "A" variant
            variant = Variant(
                id=f"var_{uuid.uuid4().hex[:12]}",
                block_id=blk_id,
                script_text=blk_data.script_text,
                variant_label="A",
                status=VariantStatus.PENDING,
            )
            db.add(variant)

        created_blocks.append({"id": blk_id, "type": bt.value, "position": blk_data.position})

    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.bulk_save", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"blocks_saved": len(created_blocks)},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"cast_id": cast_id, "blocks_saved": len(created_blocks), "blocks": created_blocks}

@router.post("/{cast_id}/blocks")
async def add_block(
    cast_id: str,
    block_type: str = Body("product"),
    product_id: Optional[str] = Body(None),
    sort_order: int = Body(0),
    category: str = Body("avatar_speaking"),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_no_active_render),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    try:
        bt = BlockType(block_type)
    except ValueError:
        bt = BlockType.PRODUCT

    # Pick a render_mode so the dispatcher routes the block to the right
    # backend. avatar_motion → motion (T2V); avatar_voiceover → voiceover;
    # pip_talking_head → pip; everything else → avatar_full.
    if category == "avatar_motion":
        rmode = "motion"
    elif category == "avatar_voiceover":
        rmode = "voiceover"
    elif category == "pip_talking_head":
        rmode = "pip"
    elif category in ("stock_photo", "stock_video"):
        rmode = "voiceover"
    else:
        rmode = "avatar_full"

    blk = Block(
        id=f"blk_{uuid.uuid4().hex[:12]}",
        cast_id=cast_id, product_id=product_id,
        type=bt, position=sort_order, sort_order=sort_order,
        category=category,
        render_mode=rmode,
    )
    db.add(blk)
    await db.flush()  # so we have blk.id for the variant FK

    # Create a default empty active variant. Without this the frontend has
    # nothing to bind the textarea to (the textarea renders only when a
    # variant exists), so the user can't type the script for newly added
    # blocks. The variant starts empty and gets filled as the user types.
    default_variant = Variant(
        id=f"var_{uuid.uuid4().hex[:12]}",
        block_id=blk.id,
        script_text="",
        status=VariantStatus.PENDING,
        variant_label="A",
        is_active=True,
    )
    db.add(default_variant)

    await db.commit()
    await db.refresh(blk)
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.create", entity_type="block",
            entity_id=blk.id, cast_id=cast_id,
            after={"type": blk.type.value, "position": blk.position, "category": blk.category, "render_mode": blk.render_mode},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"id": blk.id, "type": blk.type.value, "position": blk.position, "category": blk.category}

@router.put("/{cast_id}/blocks/{block_id}")
async def update_block(
    cast_id: str, block_id: str,
    sort_order: Optional[int] = Body(None),
    video_asset_id: Optional[str] = Body(None),
    image_asset_id: Optional[str] = Body(None),
    overlay_type: Optional[str] = Body(None),
    overlay_config: Optional[dict] = Body(None),
    layout_template_ids: Optional[list] = Body(None),
    motion_prompt: Optional[str] = Body(None),
    render_mode: Optional[str] = Body(None),
    user_video_asset_id: Optional[str] = Body(None),
    avatar_look_id: Optional[str] = Body(None),
    pip_engine: Optional[str] = Body(None),
    body_motion_start_look_id: Optional[str] = Body(None),
    body_motion_end_look_id: Optional[str] = Body(None),
    body_motion_prompt: Optional[str] = Body(None),
    body_motion_start_prompt: Optional[str] = Body(None),
    body_motion_end_prompt: Optional[str] = Body(None),
    action_start_prompt: Optional[str] = Body(None),
    action_end_prompt: Optional[str] = Body(None),
    avatar_angle: Optional[str] = Body(None),
    category: Optional[str] = Body(None),
    # Per-block pinned product. Must be one of the products attached to the
    # cast at setup (cast_products m2m). Pass empty string to clear. The
    # renderer (avatar_action frame dispatcher) prefers this over the cast's
    # default product when generating product-aware scene frames; the script
    # rewriter / regenerator weaves this product's name + description into
    # the prompt so the script naturally references it.
    product_id: Optional[str] = Body(None),
    # Generated-video frame controls. Pass empty string to clear, or omit to
    # leave unchanged. Keys are R2 paths returned from the upload endpoint.
    gen_video_first_frame_key: Optional[str] = Body(None),
    gen_video_last_frame_key: Optional[str] = Body(None),
    # Parallel media — list of stock photo / video items shown ON TOP of
    # this block's voiceover or speaking. Pass [] to clear, omit to leave
    # unchanged. Each item is
    # {kind, url, thumbnail, pexels_id?, source, start_offset_s, duration_s?}.
    parallel_media: Optional[list] = Body(None),
    # Per-block metadata bag. Used today for product carousel settings:
    #   { product_carousel: bool, carousel_speed_seconds: float (1.5-6),
    #     carousel_transition: "crossfade"|"slide"|"zoom",
    #     carousel_asset_ids: [str, ...] }
    # Pass {} to clear; omit to leave unchanged. Unknown keys are dropped on
    # the way in so the column doesn't accumulate junk.
    metadata: Optional[dict] = Body(None),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_no_active_render),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    # Generated-video first/last frame keys. Empty string clears the field.
    if gen_video_first_frame_key is not None:
        block.gen_video_first_frame_key = gen_video_first_frame_key or None
    if gen_video_last_frame_key is not None:
        block.gen_video_last_frame_key = gen_video_last_frame_key or None

    # Category update — avatar_motion implies render_mode=motion so the
    # render dispatcher routes to T2V instead of InfiniteTalk.
    #
    # Changing category MUST NOT soft-delete the block. Earlier flows
    # (cast-builder regen path / a stray category-change side effect)
    # were tipping blocks into deleted_at + is_active=False when the user
    # switched a row to "Body Motion" in the editor. We defensively
    # clear those fields here so a category update always leaves the
    # block live, regardless of any prior soft-delete state.
    if category is not None:
        block.category = category
        if category == "avatar_motion" and not (render_mode and render_mode == "motion"):
            block.render_mode = "motion"
        # Keep render_mode coupled to the category on a manual switch. Before
        # this, category was a bare label and render_mode kept whatever value
        # it already had — so switching a block to "Avatar voiceover" (or any
        # other type) in the editor did NOTHING on the next render (the render
        # dispatcher only ever reads render_mode), and the change only took
        # effect after a full cast regen. An explicit render_mode in the same
        # request still wins. Mirrors the category -> render_mode map the
        # generation path uses (routers/casts/generation.py).
        elif render_mode is None:
            _mode_for_category = {
                "avatar_speaking": "avatar_full",
                "avatar_voiceover": "voiceover",
                "pip_talking_head": "pip",
                "stock_photo": "voiceover",
                "stock_video": "voiceover",
                "avatar_action": "body_motion",
                "avatar_acting": "body_motion",
            }.get(category)
            if _mode_for_category:
                block.render_mode = _mode_for_category
        if getattr(block, "deleted_at", None) is not None:
            block.deleted_at = None
        if getattr(block, "is_active", True) is False:
            block.is_active = True

    # Avatar angle validation
    VALID_ANGLES = ("front", "three_quarter_left", "three_quarter_right", "profile_left", "profile_right", "back")
    if avatar_angle is not None:
        if avatar_angle not in VALID_ANGLES:
            raise HTTPException(400, f"avatar_angle must be one of: {', '.join(VALID_ANGLES)}")
        block.avatar_angle = avatar_angle

    # PIP engine validation
    if pip_engine is not None:
        VALID_PIP_ENGINES = ("infinitetalk_rendered", "musetalk_live")
        if pip_engine not in VALID_PIP_ENGINES:
            raise HTTPException(400, "Invalid pip engine selection")
        block.pip_engine = pip_engine

    # Render mode validation
    VALID_RENDER_MODES = ("avatar_full", "voiceover", "pip", "body_motion", "motion")
    if render_mode is not None:
        if render_mode not in VALID_RENDER_MODES:
            raise HTTPException(400, f"render_mode must be one of: {', '.join(VALID_RENDER_MODES)}")
        block.render_mode = render_mode

    if user_video_asset_id is not None:
        if user_video_asset_id:
            from models.user_video import UserVideoAsset
            uva = await db.get(UserVideoAsset, user_video_asset_id)
            if not uva or uva.user_id != ctx.workspace_owner_id:
                raise HTTPException(404, "User video asset not found or not owned by you")
            if uva.deleted_at is not None:
                raise HTTPException(400, "User video asset has been deleted")
        block.user_video_asset_id = user_video_asset_id or None

    # Only enforce the user-video requirement when the caller is EXPLICITLY
    # switching this block into voiceover/pip render mode in this request.
    # Previously this checked the block's *existing* render_mode too, so a
    # plain category change on a block that was already voiceover with
    # stock/AI b-roll (a perfectly valid state) failed with an opaque
    # "render_mode 'voiceover' requires a user_video_asset_id" error.
    if render_mode in ("voiceover", "pip"):
        effective_vid = user_video_asset_id if user_video_asset_id is not None else getattr(block, "user_video_asset_id", None)
        if not effective_vid:
            raise HTTPException(
                400,
                "Switching this block to voiceover/PIP needs an uploaded video "
                "for the narration to play over. Add a clip first, or leave the "
                "block as avatar / b-roll.",
            )

    if sort_order is not None:
        block.sort_order = sort_order
        block.position = sort_order
    if video_asset_id is not None:
        block.video_asset_id = video_asset_id
    if image_asset_id is not None:
        block.image_asset_id = image_asset_id
    if overlay_type is not None:
        block.overlay_type = overlay_type
    if overlay_config is not None:
        block.overlay_config = overlay_config
    if layout_template_ids is not None:
        block.layout_template_ids = layout_template_ids
    if parallel_media is not None:
        # Light validation — each item needs at least kind + url.
        valid: list = []
        for item in (parallel_media or []):
            if not isinstance(item, dict):
                continue
            kind = (item.get("kind") or "").lower()
            url = item.get("url")
            if kind not in ("video", "photo") or not url:
                continue
            valid.append({
                "kind": kind,
                "url": url,
                "thumbnail": item.get("thumbnail") or url,
                "pexels_id": item.get("pexels_id"),
                "source": item.get("source") or "pexels",
                "start_offset_s": float(item.get("start_offset_s") or 0),
                "duration_s": (
                    float(item["duration_s"])
                    if item.get("duration_s") is not None
                    else None
                ),
            })
        block.parallel_media = valid
    if metadata is not None:
        # Whitelist + light validation. Anything not on the allowlist is
        # silently dropped so we don't grow garbage in the JSONB column.
        try:
            current = dict(getattr(block, "block_metadata", None) or {})
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            current = {}
        if "product_carousel" in metadata:
            current["product_carousel"] = bool(metadata.get("product_carousel"))
        if "carousel_speed_seconds" in metadata:
            try:
                speed = float(metadata.get("carousel_speed_seconds") or 3.0)
            except (TypeError, ValueError):
                speed = 3.0
            # Spec range 1.5-6
            current["carousel_speed_seconds"] = max(1.5, min(6.0, speed))
        if "carousel_transition" in metadata:
            t = (metadata.get("carousel_transition") or "crossfade")
            if t not in ("crossfade", "slide", "zoom"):
                t = "crossfade"
            current["carousel_transition"] = t
        if "carousel_asset_ids" in metadata:
            ids = metadata.get("carousel_asset_ids") or []
            if isinstance(ids, list):
                current["carousel_asset_ids"] = [
                    str(x) for x in ids if isinstance(x, (str, int)) and str(x)
                ]
        # PR #76: per-block voiceover toggle for action blocks. NULL =
        # LLM default; True = force voiceover; False = silent. Stored
        # under block_metadata so no migration is required.
        if "voiceover_enabled" in metadata:
            raw = metadata.get("voiceover_enabled")
            if raw is None:
                current.pop("voiceover_enabled", None)
            else:
                current["voiceover_enabled"] = bool(raw)
        # PR #83: talking-head PIP window layout. Whitelist the four
        # enum values and drop anything else so the column stays clean.
        if "pip_layout" in metadata:
            from models.block import PipLayout
            raw = metadata.get("pip_layout")
            try:
                current["pip_layout"] = PipLayout(raw).value if raw else None
                if current["pip_layout"] is None:
                    current.pop("pip_layout", None)
            except (ValueError, TypeError) as exc:
                sentry_sdk.capture_exception(exc)
                raise HTTPException(
                    400,
                    "pip_layout must be one of fullscreen / pip_small / pip_medium / hidden",
                )
        block.block_metadata = current
    if avatar_look_id is not None:
        if avatar_look_id:
            from models.avatar_look import AvatarLook
            look = await db.get(AvatarLook, avatar_look_id)
            if not look:
                raise HTTPException(404, "Look not found")
            if look.status != "ready":
                raise HTTPException(400, "Look is not ready")
        block.avatar_look_id = avatar_look_id or None

    # Body motion fields
    if body_motion_start_look_id is not None:
        if body_motion_start_look_id:
            from models.avatar_look import AvatarLook
            look = await db.get(AvatarLook, body_motion_start_look_id)
            if not look:
                raise HTTPException(404, "Start look not found")
            if look.status != "ready":
                raise HTTPException(400, "Start look is not ready")
        block.body_motion_start_look_id = body_motion_start_look_id or None
    if body_motion_end_look_id is not None:
        if body_motion_end_look_id:
            from models.avatar_look import AvatarLook
            look = await db.get(AvatarLook, body_motion_end_look_id)
            if not look:
                raise HTTPException(404, "End look not found")
            if look.status != "ready":
                raise HTTPException(400, "End look is not ready")
        block.body_motion_end_look_id = body_motion_end_look_id or None
    if body_motion_prompt is not None:
        block.body_motion_prompt = body_motion_prompt or None
    if body_motion_start_prompt is not None:
        block.body_motion_start_prompt = body_motion_start_prompt or None
    if body_motion_end_prompt is not None:
        block.body_motion_end_prompt = body_motion_end_prompt or None
    if action_start_prompt is not None:
        block.action_start_prompt = action_start_prompt or None
    if action_end_prompt is not None:
        block.action_end_prompt = action_end_prompt or None

    if motion_prompt is not None:
        # avatar_motion blocks store the T2V prompt at the block level (it
        # describes the action for the whole block, not per variant). For
        # avatar_speaking / pip_talking_head / avatar_voiceover the motion_prompt is
        # the InfiniteTalk gesture/posture brief and lives on the variant.
        if (block.category or "avatar_speaking") == "avatar_motion":
            block.body_motion_prompt = motion_prompt or None
        else:
            result = await db.execute(
                select(Variant).where(Variant.block_id == block_id).order_by(Variant.variant_label).limit(1)
            )
            variant = result.scalar_one_or_none()
            if variant:
                variant.motion_prompt = motion_prompt

    # Per-block pinned product. Validate against cast.products (the m2m the
    # user picked at setup) — pinning a product the cast wasn't given would
    # silently break product-aware frame generation and script rewrites.
    if product_id is not None:
        if product_id == "":
            block.product_id = None
        else:
            cp_result = await db.execute(
                select(CastProduct.product_id).where(CastProduct.cast_id == cast_id)
            )
            allowed = {row[0] for row in cp_result.all()}
            if product_id not in allowed:
                raise HTTPException(
                    400,
                    "That product isn't attached to this cast. Pick one from the cast's product list.",
                )
            block.product_id = product_id

    await db.commit()
    try:
        changed = {
            "render_mode": render_mode,
            "category": category,
            "avatar_angle": avatar_angle,
            "pip_engine": pip_engine,
            "user_video_asset_id": user_video_asset_id,
            "avatar_look_id": avatar_look_id,
            "sort_order": sort_order,
            "motion_prompt": motion_prompt,
        }
        changed = {k: v for k, v in changed.items() if v is not None}
        await audit_log.record(
            db, user_id=user.id, action="block.update", entity_type="block",
            entity_id=block_id, cast_id=cast_id, after=changed,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"status": "updated"}

@router.delete("/{cast_id}/blocks/{block_id}", status_code=204)
async def delete_block(
    cast_id: str, block_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_no_active_render),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")
    from datetime import datetime
    # Block.deleted_at is a TZ-naive DateTime column. Mixing a TZ-aware
    # datetime here triggers asyncpg DataError 'can't subtract offset-naive
    # and offset-aware datetimes' when SQLAlchemy compares for change
    # detection. Use utcnow() so the value is naive UTC, matching the column.
    block.deleted_at = datetime.utcnow()
    block.is_active = False

    # ── Bonded pair cascade: remove V1/A1 elements from timeline_json ──
    if cast.timeline_json:
        updated_tl = dict(cast.timeline_json)
        changed = False
        for variant_key, tl_data in updated_tl.items():
            if not isinstance(tl_data, dict) or "tracks" not in tl_data:
                continue
            new_tracks = []
            for track in tl_data["tracks"]:
                orig_len = len(track.get("elements", []))
                track["elements"] = [
                    el for el in track.get("elements", [])
                    if not (isinstance(el.get("metadata"), dict)
                            and el["metadata"].get("block_id") == block_id)
                ]
                if len(track["elements"]) != orig_len:
                    changed = True
                new_tracks.append(track)
            tl_data["tracks"] = new_tracks
        if changed:
            from sqlalchemy.orm.attributes import flag_modified
            cast.timeline_json = updated_tl
            flag_modified(cast, "timeline_json")

    try:
        await audit_log.record(
            db, user_id=user.id, action="block.delete", entity_type="block",
            entity_id=block_id, cast_id=cast_id,
            before={"position": block.position, "type": str(block.type)},
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)

    await db.commit()

class ReorderBlocksRequest(BaseModel):
    block_ids: List[str]

@router.patch("/{cast_id}/blocks/reorder")
async def reorder_blocks(
    cast_id: str,
    req: ReorderBlocksRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_no_active_render),
):
    """Reorder blocks by providing their IDs in desired order."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    if cast.status in (CastStatus.GENERATING, CastStatus.LIVE, CastStatus.GENERATING_VIDEOS):
        raise HTTPException(400, "Cannot reorder blocks while cast is generating or live")

    result = await db.execute(select(Block).where(Block.cast_id == cast_id, Block.deleted_at.is_(None)))
    blocks = {b.id: b for b in result.scalars().all()}

    if set(req.block_ids) != set(blocks.keys()):
        raise HTTPException(400, "block_ids must match exactly the cast's current block IDs")

    before_order = [b.id for b in sorted(blocks.values(), key=lambda x: x.position or 0)]
    for idx, bid in enumerate(req.block_ids):
        blocks[bid].position = idx
        blocks[bid].sort_order = idx

    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.reorder", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            before={"order": before_order}, after={"order": req.block_ids},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"cast_id": cast_id, "blocks_reordered": len(req.block_ids)}

class ToggleActiveRequest(BaseModel):
    is_active: bool

@router.patch("/{cast_id}/blocks/{block_id}/active")
async def toggle_block_active(
    cast_id: str,
    block_id: str,
    req: ToggleActiveRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Toggle whether a block is included in cast generation + streaming."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    before_active = block.is_active
    block.is_active = req.is_active
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.toggle_active", entity_type="block",
            entity_id=block_id, cast_id=cast_id,
            before={"is_active": before_active}, after={"is_active": req.is_active},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"block_id": block_id, "is_active": block.is_active}

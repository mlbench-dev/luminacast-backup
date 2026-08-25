"""Cast generation endpoints — split from the former routers/casts.py."""

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

_VALID_VOICING_MODES = ("tts_dialogue", "prosody_only", "motion_sfx_only")

def _enrich_persona_visual(persona: dict, avatar) -> None:
    """Surface the avatar's visual/physical description into the persona dict
    so the outline generator can anchor b-roll queries to the on-camera host.

    The Avatar model has no `visual_description` column; we use the existing
    `description` / `appearance_prompt` / `body_description` columns (first
    non-empty wins) and the avatar's name. Only fills keys that are missing so
    a persona_profile that already carries them is left untouched.
    """
    if avatar is None:
        return
    try:
        if not (persona.get("visual_description") or "").strip():
            for col in ("description", "appearance_prompt", "body_description"):
                val = (getattr(avatar, col, None) or "").strip()
                if val:
                    persona["visual_description"] = val
                    break
        if not (persona.get("name") or "").strip():
            name = (getattr(avatar, "name", None) or "").strip()
            if name:
                persona["name"] = name
    except Exception as e:
        sentry_sdk.capture_exception(e)

def _clamp_voicing_mode(value: object) -> str:
    """Clamp an Opus-emitted voicing_mode to the supported set.

    Defaults to ``tts_dialogue`` when the field is absent or invalid so
    the renderer always sees a known value (the column is NOT NULL).
    """
    try:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _VALID_VOICING_MODES:
                return v
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
    return "tts_dialogue"

def _action_block_metadata_from_scene(scene: dict) -> dict | None:
    """Build the per-block metadata bag from an LLM scene.

    Carries ``voiceover_enabled`` (PR #76 — per-action-block voiceover
    choice) and ``pip_layout`` (PR #83 — talking-head PIP window size /
    visibility). Returns None if no relevant fields were present so the
    column stays NULL.
    """
    try:
        if not isinstance(scene, dict):
            return None
        bag: dict = {}

        if "voiceover_enabled" in scene:
            raw_vo = scene.get("voiceover_enabled")
            if raw_vo is not None:
                bag["voiceover_enabled"] = bool(raw_vo)

        if "pip_layout" in scene:
            raw_pip = scene.get("pip_layout")
            try:
                if raw_pip:
                    # Coerce onto a canonical layout primitive (regression-5):
                    # fullscreen / split_h / pip_quarter_bl / pip_quarter_br /
                    # hidden. This is the value resolve_block_pip_layout reads,
                    # so split_h product/b-roll beats survive persistence
                    # instead of being dropped by the narrower PipLayout enum.
                    from layouts.primitives import coerce_to_primitive
                    bag["pip_layout"] = coerce_to_primitive(raw_pip)
            except (ValueError, TypeError) as exc:
                # Bad layout value — drop silently and log to Sentry so we can
                # tune the prompt. The renderer defaults to fullscreen.
                sentry_sdk.capture_exception(exc)

        return bag or None
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return None

async def _propagate_product_to_blocks(db: AsyncSession, cast_id: str) -> None:
    """Set product_id on action / b-roll / product blocks that reference the
    primary product but were left null (cst_d7424cfa4f36). Extends PR #163's
    PRODUCT-only fix to every block category/type that visually involves the
    product, plus a bulletproof prompt-text branch. Best-effort: never aborts
    cast creation. Must be called after blocks are flushed and before commit.
    """
    try:
        from engine.cast_generator import assign_product_id_to_blocks
        await db.flush()
        prop_cast = (
            await db.execute(
                select(Cast)
                .where(Cast.id == cast_id)
                .options(
                    selectinload(Cast.blocks),
                    selectinload(Cast.products).selectinload(CastProduct.product),
                )
            )
        ).scalars().first()
        if prop_cast is not None:
            n_assigned = assign_product_id_to_blocks(prop_cast)
            if n_assigned:
                logger.info(
                    "[product-propagation] cast=%s assigned=%d", cast_id, n_assigned
                )
    except Exception as prop_exc:
        sentry_sdk.capture_exception(prop_exc)

async def _resolve_user_video_urls(
    db: AsyncSession, user_video_ids: Optional[list], user_id: str,
) -> list[str]:
    """Resolve PR #162 `user_video_ids` (UserVideoAsset ids) to public R2 URLs.

    Only assets owned by ``user_id`` and not soft-deleted are returned, in the
    order the ids were given. Missing / foreign / deleted ids are dropped
    silently — the b-roll pipeline falls back to Pexels for any block that
    doesn't receive a preferred URL. Returns [] when nothing usable resolves.
    """
    if not user_video_ids:
        return []
    from models.user_video import UserVideoAsset
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    urls: list[str] = []
    for vid in user_video_ids:
        if not vid:
            continue
        asset = await db.get(UserVideoAsset, vid)
        if not asset or asset.user_id != user_id or asset.deleted_at is not None:
            continue
        urls.append(r2.get_public_url(asset.r2_key))
    return urls

@router.post("/{cast_id}/retry")
async def retry_cast_generation(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Retry generation for a failed cast. Only re-generates failed variants."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    if cast.status not in (CastStatus.GENERATION_FAILED, CastStatus.READY):
        raise HTTPException(400, f"Cannot retry cast in status '{cast.status}'")

    # Reset cast status
    cast.status = CastStatus.GENERATING
    cast.generation_progress = 0.0
    cast.generation_error = None

    # Reset ONLY failed variants (exclude soft-deleted blocks)
    blocks = (await db.execute(
        select(Block).where(Block.cast_id == cast_id, Block.deleted_at.is_(None))
    )).scalars().all()

    failed_count = 0
    kept_count = 0
    for block in blocks:
        variants = (await db.execute(
            select(Variant).where(Variant.block_id == block.id)
        )).scalars().all()
        for variant in variants:
            if variant.status == VariantStatus.FAILED:
                variant.status = VariantStatus.PENDING
                variant.generation_error = None
                variant.runpod_job_id = None
                variant.retry_count = (variant.retry_count or 0) + 1
                failed_count += 1
            elif variant.status == VariantStatus.READY:
                kept_count += 1

    await db.commit()

    # Re-queue generation task
    from tasks.generate_cast import generate_cast_task
    generate_cast_task.delay(cast_id, ctx.workspace_owner_id)

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.retry", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"retrying_variants": failed_count, "keeping_ready_variants": kept_count},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "cast_id": cast_id,
        "status": "generating",
        "retrying_variants": failed_count,
        "keeping_ready_variants": kept_count,
    }

@router.post("/{cast_id}/generate-outline", response_model=OutlineResponse)
async def generate_outline(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    # Serialize concurrent outline/script generation for this cast — the
    # frontend can fire more than one of these in overlapping windows (e.g.
    # an initial auto-generate plus ScriptPhase's stall-fallback), and this
    # endpoint unconditionally wipes and re-inserts Block rows below. Two
    # overlapping calls racing that wipe left a generate-scripts call
    # committing Variant rows against block_ids the other call had already
    # deleted — an FK violation surfacing as a 500. Transaction-scoped so it
    # auto-releases on commit/rollback regardless of connection pooling.
    from sqlalchemy import text as sa_text
    await db.execute(sa_text("SELECT pg_advisory_xact_lock(hashtext(:cast_id))"), {"cast_id": cast_id})

    result = await db.execute(
        select(CastProduct).where(CastProduct.cast_id == cast_id).options(selectinload(CastProduct.product))
    )
    cast_products = result.scalars().all()
    products = [{"name": cp.product.name, "price": cp.product.price, "description": cp.product.description} for cp in cast_products if cp.product]

    avatar = await db.get(Avatar, cast.avatar_id)
    persona = dict(avatar.persona_profile) if avatar and avatar.persona_profile else {}
    # Pull Style DNA into the persona dict so the generator can read it.
    if avatar is not None and getattr(avatar, "style_dna", None):
        persona["style_dna"] = avatar.style_dna
    _enrich_persona_visual(persona, avatar)
    target_audience = getattr(avatar, 'target_audience', None) or {}

    from engine.cast_generator import generate_outline as gen_outline
    from engine.live_style import resolve_active_assessment
    live_assessment = await resolve_active_assessment(db, cast_id, cast.avatar_id)
    scenes = await gen_outline(
        cast_id, products, persona or {}, cast.template_name or "custom",
        script_direction=cast.script_direction,
        cast_type=getattr(cast, 'cast_type', 'recorded') or 'recorded',
        description=getattr(cast, 'description', None),
        target_audience=target_audience,
        duration_target_seconds=getattr(cast, 'duration_target_seconds', None),
        platform_target=getattr(cast, 'platform_target', 'tiktok') or 'tiktok',
        user_id=ctx.workspace_owner_id,        # Stage-1 creative template (null = Auto). Constrains the outline to
        # the template's block sequence + bias ratios when set.
        template=get_template(getattr(cast, 'template_id', None)),
        live_assessment=live_assessment,
        live_mode_defaults=getattr(cast, 'live_mode_defaults', None),
        production_level=getattr(cast, 'production_level', None) or 'standard',
    )

    # PR #162 b-roll fix: this manual (non-Smart-Cast) path never resolved
    # user_video_ids into actual b-roll before — only generate-smart-outline
    # did. Since picking LIVE mode auto-disables Smart Cast, that meant a
    # LIVE cast's uploaded clips (the whole point of the "Upload your clips"
    # card in Setup) were silently never used unless the user manually
    # re-enabled Smart Cast afterward. Mirror the smart-outline endpoint's
    # handling here so uploaded clips work regardless of Auto Cast state.
    from engine.cast_generator import auto_populate_stock_media
    preferred_broll_urls = await _resolve_user_video_urls(
        db, getattr(cast, "user_video_ids", None), ctx.workspace_owner_id,
    )
    if preferred_broll_urls:
        scenes = await auto_populate_stock_media(
            scenes, cast_id=cast_id, products=products,
            preferred_broll_urls=preferred_broll_urls,
        )

    # Replace any existing blocks before re-persisting. Without this a second
    # call to generate-outline (client retry / double-submit) appended a whole
    # new block set on top of the old one — every (position, type, category)
    # tuple ended up on two rows in one logical pass (cst_0f43a80b624d). The
    # Smart Cast path already wipes; mirror it here.
    existing_blocks = await db.execute(select(Block).where(Block.cast_id == cast_id))
    for _old in existing_blocks.scalars().all():
        await db.delete(_old)
    await db.flush()

    # Collect product IDs for round-robin auto-assignment
    selected_product_ids = [cp.product_id for cp in cast_products if cp.product]
    product_block_counter = 0

    # Defensive: dedupe by (position, type, category) at insert time so even a
    # buggy generator can't double a beat.
    _seen_block_keys: set[tuple] = set()

    for i, s in enumerate(scenes):
        # block_type arrives lowercase from the LLM; BlockType values are
        # upper-case, so coerce (not a bare BlockType()) — otherwise every
        # block silently collapsed to PRODUCT (cst_0f43a80b624d).
        block_type_enum = BlockType.coerce(s.get("block_type"))

        product_name = s.get("product_name")
        block_product_id = None
        if product_name:
            for cp in cast_products:
                if cp.product and cp.product.name and cp.product.name.lower() == product_name.lower():
                    block_product_id = cp.product_id
                    break

        # Auto-assign product_id by round-robin for product/flash_sale/cta blocks
        if not block_product_id and block_type_enum in (BlockType.PRODUCT, BlockType.FLASH_SALE, BlockType.CTA) and selected_product_ids:
            block_product_id = selected_product_ids[product_block_counter % len(selected_product_ids)]
            product_block_counter += 1

        # Persist the LLM-chosen category so generate_scripts() picks the
        # right per-category writing rule (voiceover vs talking head vs
        # action vs stock). Sanitized in cast_generator._sanitize_outline_categories.
        scene_category = s.get("category") or "avatar_speaking"
        # Legacy categories mapped to the merged avatar_action.
        if scene_category in ("avatar_motion", "avatar_acting"):
            scene_category = "avatar_action"
        if scene_category == "avatar_voiceover":
            block_render_mode = "voiceover"
        elif scene_category == "live_pip":
            block_render_mode = "pip"
        elif scene_category == "avatar_action":
            # avatar_action always uses I2V (the body_motion render path)
            # with FLUX-Kontext-generated scene frames.
            block_render_mode = "body_motion"
        elif scene_category in ("stock_photo", "stock_video"):
            # Pure B-roll — no avatar face is meant to appear. Routes
            # through the same voiceover bake path, which resolves the
            # visual from stock_media_url for these categories.
            block_render_mode = "voiceover"
        else:
            block_render_mode = "avatar_full"

        # Action prompts: prefer the new action_*_prompt fields; fall back
        # to the legacy body_motion_*_prompt fields so old outlines still
        # populate the new columns.
        action_start_prompt_val = (
            s.get("action_start_prompt") or s.get("body_motion_start_prompt") or None
        )
        action_end_prompt_val = (
            s.get("action_end_prompt") or s.get("body_motion_end_prompt") or None
        )

        # Skip a beat we've already persisted at this pass so one (position,
        # type, category) tuple never lands on two rows. Keyed on the same
        # identity the generator dedupes on. Position uses a running counter so
        # skips don't leave gaps.
        _block_key = (
            block_type_enum,
            scene_category,
            tuple(s.get("key_points") or []),
        )
        if _block_key in _seen_block_keys:
            continue
        _seen_block_keys.add(_block_key)
        _position = len(_seen_block_keys) - 1

        blk = Block(
            id=f"blk_{uuid.uuid4().hex[:12]}",
            cast_id=cast_id, product_id=block_product_id,
            type=block_type_enum, position=_position,
            category=scene_category,
            # Round-6 Bug B: persist the per-block camera framing assigned by the
            # outline (default MEDIUM) so look generation / reuse can vary shots.
            framing=(s.get("framing") or "MEDIUM"),
            layout_mode=LayoutMode.FULL_AVATAR,
            render_mode=block_render_mode,
            body_motion_prompt=(s.get("body_motion_prompt") or s.get("motion_prompt") or None),
            body_motion_start_prompt=(s.get("body_motion_start_prompt") or None),
            body_motion_end_prompt=(s.get("body_motion_end_prompt") or None),
            action_start_prompt=action_start_prompt_val,
            action_end_prompt=action_end_prompt_val,
            voicing_mode=_clamp_voicing_mode(s.get("voicing_mode")),
            block_metadata=_action_block_metadata_from_scene(s),
            mood=s.get("mood", "energetic"), key_points=s.get("key_points", []),
            # Inherit the cast-wide background look so RunPod has a source
            # image to render against. Without this, avatar_speaking blocks
            # land at RunPod with no face/look reference and the worker
            # fails with a generic 'Video not found' error.
            avatar_look_id=cast.default_avatar_look_id,
        )
        db.add(blk)

    # Step 3 — record which layout-template preset this cast resolved to, in
    # the SAME transaction that persists the cast + blocks. Read-only for now
    # (Step 4 wires it into the composer). Detection mirrors what the generator
    # ran (keyword-first, cheap); never blocks generation.
    try:
        from services.content_type import detect_content_type
        from engine.cast_generator import attach_layout_template_to_cast
        _content_type = await detect_content_type(getattr(cast, "description", None) or "", products)
        await attach_layout_template_to_cast(cast, _content_type.get("type"), db)
    except Exception as _ct_exc:
        sentry_sdk.capture_exception(_ct_exc)
        cast.layout_template_id = None

    # Step 5 — feed the template's caption_preset / mic / scene into the blocks
    # as defaults (explicit overrides win). Same transaction as the cast+blocks.
    try:
        from engine.cast_generator import stamp_template_defaults_on_blocks
        await db.flush()
        _step5_blocks = (
            await db.execute(select(Block).where(Block.cast_id == cast_id))
        ).scalars().all()
        await stamp_template_defaults_on_blocks(cast, _step5_blocks, db)
    except Exception as _td_exc:
        sentry_sdk.capture_exception(_td_exc)

    # regr-wiring: bind the avatar's mic_on_* look to every mic-on block now
    # that mic_on has been stamped (above). Runs in the same transaction.
    try:
        from engine.cast_generator import bind_mic_on_look_to_blocks
        await db.flush()
        _mic_blocks = (
            await db.execute(select(Block).where(Block.cast_id == cast_id))
        ).scalars().all()
        await bind_mic_on_look_to_blocks(cast, _mic_blocks, db)
    except Exception as _mic_exc:
        sentry_sdk.capture_exception(_mic_exc)

    await _propagate_product_to_blocks(db, cast_id)

    cast.status = CastStatus.OUTLINE_REVIEW
    await db.commit()

    # Best-effort: pre-warm the per-block scene frame carousels.
    # avatar_action → action_frame task (new FLUX scene frames keyed off
    # action_block_<id>_<kind>). Legacy avatar_acting blocks (if any
    # somehow survived sanitisation) fall through to the body-motion
    # task so the user still sees something.
    try:
        from tasks.avatar_looks import (
            generate_action_frame_task,
            generate_body_motion_frame_task,
            generate_talking_head_task,
        )
        from models.avatar_look import (
            AvatarLook,
            TALKING_HEAD_LOOK_TYPE,
            DEFAULT_FRAMING,
        )
        seeded_q = await db.execute(
            select(Block).where(Block.cast_id == cast_id)
        )
        # Pre-warm reusable talking-head looks early (one per distinct non-MEDIUM
        # framing on plain lip-sync blocks) so they are READY by finalize/render.
        prewarmed_avatar_id = getattr(cast, "avatar_id", None)
        talking_head_seen: set[str] = set()
        for blk in seeded_q.scalars().all():
            if blk.category == "avatar_action" or blk.render_mode == "body_motion":
                if blk.action_start_prompt:
                    generate_action_frame_task.delay(blk.id, "start", blk.action_start_prompt)
                if blk.action_end_prompt:
                    generate_action_frame_task.delay(blk.id, "end", blk.action_end_prompt)
                # Legacy fallback — fires only when the new prompts are
                # absent but the old ones somehow exist.
                if not blk.action_start_prompt and blk.body_motion_start_prompt:
                    generate_body_motion_frame_task.delay(blk.id, "start", blk.body_motion_start_prompt)
                if not blk.action_end_prompt and blk.body_motion_end_prompt:
                    generate_body_motion_frame_task.delay(blk.id, "end", blk.body_motion_end_prompt)
            elif prewarmed_avatar_id:
                framing = (getattr(blk, "framing", None) or DEFAULT_FRAMING).strip().upper()
                if framing == DEFAULT_FRAMING or framing in talking_head_seen:
                    continue
                talking_head_seen.add(framing)
                existing = await db.scalar(
                    select(AvatarLook.id)
                    .where(AvatarLook.avatar_id == prewarmed_avatar_id)
                    .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
                    .where(AvatarLook.framing == framing)
                    .where(AvatarLook.status == "ready")
                    .limit(1)
                )
                if existing:
                    continue
                generate_talking_head_task.delay(prewarmed_avatar_id, framing)
                logger.info(
                    "[talking-head-prewarm] cast=%s avatar=%s framing=%s — queued look gen (outline)",
                    cast_id, prewarmed_avatar_id, framing,
                )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)

    outline_scenes = []
    total_duration = 0
    for s in scenes:
        duration = s.get("estimated_duration_seconds", 30)
        total_duration += duration
        outline_scenes.append(OutlineScene(
            block_type=s.get("block_type", "product"),
            mood=s.get("mood", "energetic"),
            key_points=s.get("key_points", []),
            style_directives=s.get("style_directives", []),
            estimated_duration_seconds=duration,
            product_name=s.get("product_name"),
        ))
    return OutlineResponse(cast_id=cast_id, scenes=outline_scenes, estimated_total_duration_seconds=total_duration)

SMART_CAST_BLOCK_COSTS_USD = {
    "avatar_speaking": 0.12,
    "avatar_voiceover": 0.0,
    # Merged avatar_motion + avatar_acting → avatar_action (I2V w/ FLUX scene frames).
    "avatar_action": 0.18,
    # Legacy keys kept for any cached cost lookups still using the old names.
    "avatar_motion": 0.18,
    "avatar_acting": 0.15,
    "pip_talking_head": 0.08,
    "stock_video": 0.0,
    "stock_photo": 0.0,
    "generated_photo": 0.05,
    "generated_video": 0.20,
}

class SmartOutlineResponse(BaseModel):
    cast_id: str
    blocks: list[dict]
    estimated_total_duration_seconds: int
    estimated_cost_usd: float

@router.post("/{cast_id}/generate-smart-outline", response_model=SmartOutlineResponse)
async def generate_smart_outline_endpoint(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Smart Cast outline: the LLM designs categories, hook type, stock media
    queries, and transitions. Pexels is queried for any block that needs stock
    media. Existing blocks (if any) are wiped and replaced.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    if not cast.description:
        raise HTTPException(400, "Cast must have a description (the brief) before Smart Cast can generate.")

    # See generate_outline's identical lock above — same block-wipe race.
    from sqlalchemy import text as sa_text
    await db.execute(sa_text("SELECT pg_advisory_xact_lock(hashtext(:cast_id))"), {"cast_id": cast_id})

    # Collect products + persona for context
    result = await db.execute(
        select(CastProduct).where(CastProduct.cast_id == cast_id).options(selectinload(CastProduct.product))
    )
    cast_products = result.scalars().all()
    products = [
        {
            "name": cp.product.name,
            "price": cp.product.price,
            "description": cp.product.description,
            "key_benefits": getattr(cp.product, "key_benefits", None) or [],
            "category": getattr(cp.product, "category", None) or "",
        }
        for cp in cast_products if cp.product
    ]
    # PR E — collect the bound products' uploaded VIDEO assets so the outline
    # can put the real product footage on screen (preferred over Pexels b-roll).
    product_video_assets: list[dict] = []
    product_ids = [cp.product_id for cp in cast_products if cp.product_id]
    if product_ids:
        pa_result = await db.execute(
            select(ProductAsset)
            .where(
                ProductAsset.product_id.in_(product_ids),
                ProductAsset.media_type == "video",
            )
            .order_by(ProductAsset.position)
        )
        _name_by_id = {cp.product_id: cp.product.name for cp in cast_products if cp.product}
        for pa in pa_result.scalars().all():
            url = pa.r2_url or ""
            if not url:
                continue
            product_video_assets.append({
                "id": pa.id,
                "product_id": pa.product_id,
                "product_name": _name_by_id.get(pa.product_id),
                "url": url,
                "thumbnail": pa.thumbnail_r2_key or None,
                "duration_seconds": pa.duration_seconds,
                "width": pa.width,
                "height": pa.height,
            })

    avatar = await db.get(Avatar, cast.avatar_id)
    persona = dict(avatar.persona_profile) if avatar and avatar.persona_profile else {}
    # Surface Avatar Style DNA into the persona dict so cast_generator's
    # _build_style_dna_section() can find it. Style DNA is stored in its
    # own column to keep persona_profile clean, but the generator only
    # ever sees `persona`, so we merge it in here.
    if avatar is not None and getattr(avatar, "style_dna", None):
        persona["style_dna"] = avatar.style_dna
    _enrich_persona_visual(persona, avatar)
    target_audience = getattr(avatar, "target_audience", None) or {}

    # 1. Generate the smart outline.
    from engine.cast_generator import generate_smart_outline, auto_populate_stock_media
    from engine.live_style import resolve_active_assessment
    live_assessment = await resolve_active_assessment(db, cast_id, cast.avatar_id)

    duration = getattr(cast, "duration_target_seconds", None) or 60
    platform = getattr(cast, "platform_target", None) or "tiktok"
    quality = getattr(cast, "quality", None)
    if hasattr(quality, "value"):
        quality = quality.value  # enum → str
    quality = (quality or "hd").lower()

    outline, content_type = await generate_smart_outline(
        cast_id=cast_id,
        products=products,
        persona=persona or {},
        description=cast.description,
        target_audience=target_audience,
        duration_target_seconds=duration,
        platform_target=platform,
        quality_tier=quality,
        aspect_ratio=getattr(cast, "aspect_ratio", None) or "9:16",
        user_id=ctx.workspace_owner_id,        # Stage-1 creative template (null = Auto). Constrains the smart outline
        # to the template's block sequence + bias ratios when set.
        template=get_template(getattr(cast, "template_id", None)),
        product_video_assets=product_video_assets,
        live_assessment=live_assessment,
        live_mode_defaults=getattr(cast, "live_mode_defaults", None),
        production_level=getattr(cast, "production_level", None) or "standard",
    )
    if not outline:
        raise HTTPException(502, "Smart outline generation failed — try again.")

    # 2. Auto-populate Pexels stock media for blocks that asked for it.
    #    Passing products makes the search product-relevant (product name +
    #    feature words) instead of brand/topic, and enables multi-angle pairs.
    #    PR #162 — when the user picked uploaded videos (user_video_ids), resolve
    #    them to R2 URLs and prefer them as b-roll over Pexels.
    preferred_broll_urls = await _resolve_user_video_urls(
        db, getattr(cast, "user_video_ids", None), ctx.workspace_owner_id,
    )
    outline = await auto_populate_stock_media(
        outline, cast_id=cast_id, products=products,
        preferred_broll_urls=preferred_broll_urls,
    )

    # 3. Wipe any existing blocks for this cast (they'll be replaced).
    existing = await db.execute(select(Block).where(Block.cast_id == cast_id))
    for blk in existing.scalars().all():
        await db.delete(blk)
    await db.flush()

    # 4. Create new Block rows with category + Smart Cast metadata populated.
    selected_product_ids = [cp.product_id for cp in cast_products if cp.product]
    product_block_counter = 0

    for i, s in enumerate(outline):
        block_type_str = (s.get("block_type") or "product").upper()
        try:
            block_type_enum = BlockType(block_type_str)
        except ValueError:
            block_type_enum = BlockType.PRODUCT

        # Round-robin product assignment for product/cta/flash_sale blocks.
        block_product_id = None
        product_name = s.get("product_name")
        if product_name:
            for cp in cast_products:
                if cp.product and cp.product.name and cp.product.name.lower() == product_name.lower():
                    block_product_id = cp.product_id
                    break
        if (
            not block_product_id
            and block_type_enum in (BlockType.PRODUCT, BlockType.FLASH_SALE, BlockType.CTA)
            and selected_product_ids
        ):
            block_product_id = selected_product_ids[product_block_counter % len(selected_product_ids)]
            product_block_counter += 1

        # render_mode bridges to the existing render dispatcher.
        category = s.get("category") or "avatar_speaking"
        # Legacy categories collapse into the merged avatar_action.
        if category in ("avatar_motion", "avatar_acting"):
            category = "avatar_action"
        if category == "avatar_voiceover":
            render_mode = "voiceover"
        elif category == "pip_talking_head":
            render_mode = "pip"
        elif category == "avatar_action":
            # avatar_action always uses I2V with FLUX-Kontext-generated
            # scene frames — same render path body_motion uses today.
            render_mode = "body_motion"
        elif category in ("stock_photo", "stock_video"):
            # Pure B-roll — no avatar face is meant to appear. Routes
            # through the same voiceover bake path, which resolves the
            # visual from stock_media_url for these categories.
            render_mode = "voiceover"
        else:
            render_mode = "avatar_full"

        # Motion blocks carry the LLM-authored motion description in
        # body_motion_prompt (column already exists). For avatar_action
        # blocks it drives the I2V interpolation between scene frames.
        motion_prompt_text = s.get("motion_prompt") or ""

        # Frame prompts: prefer the new action_*_prompt fields, fall back to
        # legacy body_motion_*_prompt so cached LLM responses still work.
        action_start_prompt_val = (
            s.get("action_start_prompt") or s.get("body_motion_start_prompt") or None
        )
        action_end_prompt_val = (
            s.get("action_end_prompt") or s.get("body_motion_end_prompt") or None
        )
        bm_start_prompt = (s.get("body_motion_start_prompt") or None)
        bm_end_prompt = (s.get("body_motion_end_prompt") or None)

        blk = Block(
            id=f"blk_{uuid.uuid4().hex[:12]}",
            cast_id=cast_id,
            product_id=block_product_id,
            type=block_type_enum,
            category=category,
            position=i,
            # Round-6 Bug B follow-up: persist the per-block camera framing the
            # smart outline assigned (default MEDIUM). Without this every smart
            # block landed framing=NULL and the renderer reused one shared look.
            framing=(s.get("framing") or "MEDIUM"),
            layout_mode=LayoutMode.FULL_AVATAR,
            render_mode=render_mode,
            body_motion_prompt=(s.get("body_motion_prompt") or motion_prompt_text or None),
            body_motion_start_prompt=bm_start_prompt,
            body_motion_end_prompt=bm_end_prompt,
            action_start_prompt=action_start_prompt_val,
            action_end_prompt=action_end_prompt_val,
            voicing_mode=_clamp_voicing_mode(s.get("voicing_mode")),
            block_metadata=_action_block_metadata_from_scene(s),
            mood=s.get("mood", "energetic"),
            key_points=s.get("key_points", []),
            hook_type=s.get("hook_type"),
            background_type=s.get("background_type"),
            transition_in=s.get("transition_in"),
            energy_level=s.get("energy_level"),
            stock_media_query=s.get("stock_media_query"),
            stock_media_url=s.get("stock_media_url"),
            stock_media_thumbnail=s.get("stock_media_thumbnail"),
            stock_media_kind=s.get("stock_media_kind"),
            stock_media_pexels_id=str(s.get("stock_media_pexels_id")) if s.get("stock_media_pexels_id") else None,
            # AI-suggested parallel b-roll — the auto_populate step writes
            # one parallel_media item for avatar/PIP blocks so the editor
            # shows a suggested visual the moment the script loads.
            parallel_media=s.get("parallel_media"),
            # Inherit the cast-wide background look picked at SetupPhase.
            # The user can still override per-block in ScriptPhase.
            avatar_look_id=cast.default_avatar_look_id,
        )
        db.add(blk)

    # Step 3 — record which layout-template preset this cast resolved to, in
    # the SAME transaction that persists the cast + blocks. Read-only for now
    # (Step 4 wires it into the composer). Never blocks generation: a selector
    # failure leaves cast.layout_template_id = None.
    from engine.cast_generator import attach_layout_template_to_cast
    await attach_layout_template_to_cast(
        cast, (content_type or {}).get("type"), db
    )

    # Step 5 — feed the template's caption_preset / mic / scene into the blocks
    # as defaults (explicit overrides win). Same transaction as the cast+blocks.
    try:
        from engine.cast_generator import stamp_template_defaults_on_blocks
        await db.flush()
        _step5_blocks = (
            await db.execute(select(Block).where(Block.cast_id == cast_id))
        ).scalars().all()
        await stamp_template_defaults_on_blocks(cast, _step5_blocks, db)
    except Exception as _td_exc:
        sentry_sdk.capture_exception(_td_exc)

    # regr-wiring: bind the avatar's mic_on_* look to every mic-on block now
    # that mic_on has been stamped (above). Runs in the same transaction.
    try:
        from engine.cast_generator import bind_mic_on_look_to_blocks
        await db.flush()
        _mic_blocks = (
            await db.execute(select(Block).where(Block.cast_id == cast_id))
        ).scalars().all()
        await bind_mic_on_look_to_blocks(cast, _mic_blocks, db)
    except Exception as _mic_exc:
        sentry_sdk.capture_exception(_mic_exc)

    await _propagate_product_to_blocks(db, cast_id)

    cast.status = CastStatus.OUTLINE_REVIEW
    await db.commit()

    # 4a-bis. Best-effort: pre-generate AI scene frames for any
    # avatar_action block that came back with seed prompts. This populates
    # the editor carousel before the user opens the block, so the typical
    # path (open block → see frames already there → click one) is fast.
    # If celery dispatch fails for any reason the user still has the
    # in-editor "Generate now" button to trigger creation manually.
    try:
        from tasks.avatar_looks import (
            generate_action_frame_task,
            generate_body_motion_frame_task,
            generate_talking_head_task,
        )
        from models.avatar_look import (
            AvatarLook,
            TALKING_HEAD_LOOK_TYPE,
            DEFAULT_FRAMING,
        )
        action_blocks_q = await db.execute(
            select(Block).where(Block.cast_id == cast_id)
        )
        prewarmed_avatar_id = getattr(cast, "avatar_id", None)
        talking_head_seen: set[str] = set()
        for blk in action_blocks_q.scalars().all():
            if blk.category != "avatar_action" and blk.render_mode != "body_motion":
                # Plain lip-sync block — pre-warm a reusable per-framing
                # talking-head look (one per distinct non-MEDIUM framing).
                if not prewarmed_avatar_id:
                    continue
                framing = (getattr(blk, "framing", None) or DEFAULT_FRAMING).strip().upper()
                if framing == DEFAULT_FRAMING or framing in talking_head_seen:
                    continue
                talking_head_seen.add(framing)
                existing = await db.scalar(
                    select(AvatarLook.id)
                    .where(AvatarLook.avatar_id == prewarmed_avatar_id)
                    .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
                    .where(AvatarLook.framing == framing)
                    .where(AvatarLook.status == "ready")
                    .limit(1)
                )
                if existing:
                    continue
                generate_talking_head_task.delay(prewarmed_avatar_id, framing)
                logger.info(
                    "[talking-head-prewarm] cast=%s avatar=%s framing=%s — queued look gen (outline)",
                    cast_id, prewarmed_avatar_id, framing,
                )
                continue
            if blk.action_start_prompt:
                generate_action_frame_task.delay(blk.id, "start", blk.action_start_prompt)
            if blk.action_end_prompt:
                generate_action_frame_task.delay(blk.id, "end", blk.action_end_prompt)
            if not blk.action_start_prompt and blk.body_motion_start_prompt:
                generate_body_motion_frame_task.delay(blk.id, "start", blk.body_motion_start_prompt)
            if not blk.action_end_prompt and blk.body_motion_end_prompt:
                generate_body_motion_frame_task.delay(blk.id, "end", blk.body_motion_end_prompt)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)

    # 4b. Kick off the stock-import Celery task so each block's Pexels asset
    # is downloaded, uploaded to R2, and assigned a user_video_asset_id.
    # Doing this synchronously here would blow past Cloudflare's edge
    # timeout (multiple 30+ MB downloads). The editor reads user_video_asset_id
    # first, falling back to stock_media_url while the import is in flight.
    try:
        from tasks.smart_cast_tasks import import_smart_stock_task
        import_smart_stock_task.delay(cast_id)
    except Exception as exc:
        # Non-fatal — the editor can still play directly from the Pexels CDN.
        sentry_sdk.capture_exception(exc)

    # 4b-bis. If the cast opted into AI-generated b-roll (Setup-tab picker),
    # kick off product-only photo/video generation for stock_photo/stock_video
    # blocks in the background — each Kling video call takes minutes, so this
    # can't run in-band. The Pexels asset from step 2 stays as the fallback
    # until (or if) this replaces it with Block.video_asset_id/image_asset_id.
    if getattr(cast, "broll_media_source", "stock") == "ai_generated":
        try:
            from tasks.product_broll_tasks import generate_ai_broll_for_cast_task
            generate_ai_broll_for_cast_task.delay(cast_id)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)

    # 4c. Kick off AI background music generation in the background.
    # Mubert v3 polling can take 5-30s; we don't block the outline
    # response on it. The cast row gets background_music_url written
    # whenever the task finishes; the editor picks it up on its next
    # cast refresh.
    # Respect the user's music choice: "off" skips generation entirely (no
    # waste); "track_id:<id>" pins a fixed library track without a Mubert call;
    # "auto" (default) keeps the mood-driven Mubert generation.
    from services import music_library

    def _dispatch_auto_music() -> None:
        try:
            from tasks.auto_music import generate_music_for_cast_task
            generate_music_for_cast_task.delay(cast_id)
        except Exception as exc:
            # Non-fatal — the cast renders fine without music.
            sentry_sdk.capture_exception(exc)

    music_choice = getattr(cast, "music_track_choice", music_library.CHOICE_AUTO) or music_library.CHOICE_AUTO
    if music_choice == music_library.CHOICE_OFF:
        logger.info("Music choice 'off' — skipping auto-music for cast %s", cast_id)
    elif music_choice == "custom":
        # User picked a real track from the full Mubert-backed library
        # (Setup's track-picker modal) — background_music_url/mood/tags
        # were already set directly via PATCH at pick time, nothing to
        # resolve or generate here.
        logger.info("Music choice 'custom' — using already-set background_music_url for cast %s", cast_id)
    elif music_choice.startswith(music_library.CHOICE_TRACK_PREFIX):
        try:
            cast.background_music_url = music_library.resolve_choice_url(music_choice)
            await db.commit()
        except Exception as exc:
            # Unknown/invalid track id — fall back to auto-generation so the
            # cast still gets music rather than silence.
            sentry_sdk.capture_exception(exc)
            _dispatch_auto_music()
    else:
        _dispatch_auto_music()

    # 5. Compute estimated cost (per-block) and total duration.
    total_duration = 0
    total_cost = 0.0
    quality_multiplier = {"simple": 1.0, "hd": 1.5, "hd_plus": 2.5}.get(quality, 1.5)
    for s in outline:
        total_duration += int(s.get("estimated_duration_seconds", 0) or 0)
        total_cost += SMART_CAST_BLOCK_COSTS_USD.get(s.get("category", "avatar_speaking"), 0.12)
    total_cost = round(total_cost * quality_multiplier, 2)

    return SmartOutlineResponse(
        cast_id=cast_id,
        blocks=outline,
        estimated_total_duration_seconds=total_duration,
        estimated_cost_usd=total_cost,
    )

@router.post("/{cast_id}/generate-scripts")
async def generate_scripts(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    # See generate_outline's identical lock — waits here if an outline call
    # for this same cast is still mid-flight, instead of building Variant
    # rows against block_ids that call is about to delete.
    from sqlalchemy import text as sa_text
    await db.execute(sa_text("SELECT pg_advisory_xact_lock(hashtext(:cast_id))"), {"cast_id": cast_id})

    # Get voice profile from channel
    voice_profile = None
    if cast.channel_id:
        channel = await db.get(Channel, cast.channel_id)
        if channel:
            voice_profile = channel.voice_profile

    # Eager-load Block.product so the outline list-comp below can read
    # b.product.name without triggering an async lazy load (which throws
    # sqlalchemy.exc.MissingGreenlet on AsyncSession).
    result = await db.execute(
        select(Block)
        .where(Block.cast_id == cast_id, Block.deleted_at.is_(None))
        .options(selectinload(Block.product))
        .order_by(Block.position)
    )
    blocks = result.scalars().all()

    avatar = await db.get(Avatar, cast.avatar_id)
    persona = dict(avatar.persona_profile) if avatar and avatar.persona_profile else {}
    # Pull Style DNA into the persona dict so the generator can read it.
    if avatar is not None and getattr(avatar, "style_dna", None):
        persona["style_dna"] = avatar.style_dna
    _enrich_persona_visual(persona, avatar)

    outline = [
        {
            "block_type": b.type.value,
            "mood": b.mood,
            "key_points": b.key_points,
            # Smart Cast: pass the category so generate_scripts() picks the
            # right per-category writing rules (voiceover blocks shouldn't
            # say "hey guys", stock blocks should return text overlays, etc).
            "category": getattr(b, "category", None) or "avatar_speaking",
            "hook_type": getattr(b, "hook_type", None),
            "background_type": getattr(b, "background_type", None),
            "product_name": (b.product.name if b.product is not None else None),
        }
        for b in blocks
    ]

    # Pull cast description + attached products so the universal content
    # engine can detect the user's intent (motion, tutorial, fashion, etc.)
    # and adapt the script writer's persona — instead of forcing live-selling.
    cast_description = (cast.description or cast.goal or "") if hasattr(cast, "description") else ""
    cast_products: list[dict] = []
    try:
        from models.product import Product  # type: ignore
        for b in blocks:
            if b.product is not None:
                cast_products.append({
                    "name": getattr(b.product, "name", "") or "",
                    "description": getattr(b.product, "description", "") or "",
                })
    except Exception as _exc:
        sentry_sdk.capture_exception(_exc)

    from engine.cast_generator import generate_scripts as gen_scripts
    scripts = await gen_scripts(
        cast_id,
        outline,
        persona or {},
        voice_profile=voice_profile,
        description=cast_description,
        products=cast_products or None,
        user_id=ctx.workspace_owner_id,    )

    # Track which blocks already have an active variant to avoid duplicates
    blocks_with_active = set()
    for script in scripts:
        block_idx = script["block_index"]
        if block_idx < len(blocks):
            is_first_for_block = block_idx not in blocks_with_active
            variant = Variant(
                id=f"var_{uuid.uuid4().hex[:12]}",
                block_id=blocks[block_idx].id,
                script_text=script["script_text"],
                status=VariantStatus.PENDING,
                variant_label="A" if is_first_for_block else chr(65 + script.get("variant_index", 1)),
                motion_prompt=script.get("motion_prompt", ""),
                is_active=is_first_for_block,  # Only first variant per block is active
            )
            db.add(variant)
            blocks_with_active.add(block_idx)

    cast.status = CastStatus.SCRIPT_REVIEW
    await db.commit()

    # CHANGE 5.1 — auto-clip suggestions. Runs AFTER scripts are persisted
    # so the LLM sees the actual `script_text` per block. Wrapped in
    # try/except (and the suggest_clips helper itself catches everything)
    # because a clip-suggestion failure must never block script generation.
    try:
        from engine.cast_generator import suggest_clips as gen_suggest_clips
        clip_blocks = [
            {
                "id": b.id,
                "category": getattr(b, "category", "avatar_speaking"),
                "estimated_duration_seconds": (b.variants[0].duration_seconds if b.variants else None)
                    or 10,
                "summary": b.mood or "",
                "script_text": (b.variants[0].script_text if b.variants else "") or "",
            }
            for b in blocks
        ]
        clips = await gen_suggest_clips(
            clip_blocks,
            cast_description,
            cast_id=cast_id,
            user_id=ctx.workspace_owner_id,        )
        cast.suggested_clips = clips
        await db.commit()
    except Exception as _clip_exc:
        sentry_sdk.capture_exception(_clip_exc)
        logger.warning("suggest_clips wiring failed for cast %s: %s", cast_id, _clip_exc)

    return {"cast_id": cast_id, "scripts_generated": len(scripts), "voice_profile_used": voice_profile is not None}

CAST_CREATION_FEE_CENTS = 1499

@router.post("/{cast_id}/pay")
async def pay_for_cast(
    cast_id: str,
    req: CastPayRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    if cast.creation_paid:
        raise HTTPException(400, "Cast already paid")

    from services.stripe_billing import get_stripe_billing_service

    if not user.stripe_customer_id:
        customer = await get_stripe_billing_service().create_customer(user.email, user.id)
        user.stripe_customer_id = customer["id"]

    payment = await get_stripe_billing_service().create_payment_intent(
        amount_cents=CAST_CREATION_FEE_CENTS,
        customer_id=user.stripe_customer_id,
        metadata={"cast_id": cast_id, "user_id": user.id},
    )

    billing = BillingEvent(
        id=f"bill_{uuid.uuid4().hex[:12]}",
        user_id=user.id, type=BillingEventType.CAST_CREATION,
        amount_cents=CAST_CREATION_FEE_CENTS,
        stripe_payment_intent_id=payment.get("id"),
        related_id=cast_id,
    )
    db.add(billing)

    cast.creation_paid = True
    cast.status = CastStatus.GENERATING
    cast.stripe_payment_id = payment.get("id", "")
    await db.commit()

    from tasks.generate_cast import generate_cast_task
    generate_cast_task.delay(cast_id, user.id)

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.pay", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"amount_cents": CAST_CREATION_FEE_CENTS},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"cast_id": cast_id, "status": "generating", "payment_id": payment.get("id")}

@router.get("/{cast_id}/generation-status", response_model=GenerationStatusResponse)
async def get_generation_status(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")

    result = await db.execute(
        select(func.count(Variant.id)).join(Block).where(Block.cast_id == cast_id)
    )
    total = result.scalar() or 0

    result = await db.execute(
        select(func.count(Variant.id)).join(Block).where(
            Block.cast_id == cast_id, Variant.status == VariantStatus.FAILED
        )
    )
    failed = result.scalar() or 0

    return GenerationStatusResponse(
        cast_id=cast_id,
        status=cast.status.value,
        progress=cast.generation_progress or cast.progress_percent or 0,
        current_step=cast.progress_step,
        failed_count=failed,
        total_variants=total,
    )

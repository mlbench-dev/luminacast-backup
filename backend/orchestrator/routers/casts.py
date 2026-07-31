"""Cast Builder — CRUD, blocks, variants, script generation, Cast generation."""

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
from models.user import User
from models.cast import Cast, CastStatus, CastProduct, CastQuality, CastVersion
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
from routers.auth import get_current_user
from services import audit_log
from services.cast_templates import get_template

router = APIRouter(prefix="/api/casts", tags=["casts"])


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


def _get_pip_layout(block) -> Optional[str]:
    """Read a block's raw ``pip_layout`` from its JSONB ``metadata`` bag.

    There is NO ``pip_layout`` column on ``blocks`` — the value lives inside the
    JSONB ``metadata`` column (e.g. ``{"pip_layout": "split_h"}``). SQLAlchemy
    reserves ``metadata`` on the declarative Base, so the Python attribute is
    ``block_metadata`` while the underlying column is ``metadata`` (see
    ``models/block.py``). The serialized block the frontend sees exposes it as
    ``metadata``.

    Returns the raw string value, or ``None`` when metadata is absent / not a
    dict / has no ``pip_layout`` key. Never raises — a malformed bag must not
    fail the arrange call.
    """
    try:
        meta = getattr(block, "block_metadata", None)
        if meta is None:
            meta = getattr(block, "metadata", None)
        if isinstance(meta, dict):
            value = meta.get("pip_layout")
            if value is not None and str(value).strip() != "":
                return str(value)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
    return None


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


def _mark_variant_audio_stale(variant: Variant, cast: Cast | None) -> None:
    """Atomically clear stale TTS audio fields when ``script_text`` changes.

    Bug fix: when the script changes via any path (manual edit, AI rewrite,
    refine-all, etc.) the previously-baked TTS audio in R2 is from the
    OLD script and will produce wrong audio (and downstream identity
    drift on lipsync). Caller MUST commit the same transaction to make
    the staleness atomic with the script_text update. After commit,
    invoke ``_enqueue_tts_regen(cast_id, user_id)`` to kick off
    regeneration.
    """
    variant.tts_r2_key = ""
    variant.tts_duration_seconds = 0
    variant.audio_key = None
    variant.caption_words = None
    variant.caption_segments = None
    variant.word_timestamps = None
    variant.status = VariantStatus.PENDING
    if cast is not None:
        try:
            cast.audio_stale_since = datetime.now(tz.utc)
        except Exception as e:
            sentry_sdk.capture_exception(e)


def _enqueue_tts_regen(cast_id: str, user_id: str) -> None:
    """Enqueue the cast-wide TTS regeneration task.

    The whole-cast task is the canonical path for refreshing TTS audio —
    it resets variant state, runs voice generation per variant, and
    re-uploads to R2. Variants whose audio is still fresh are regenerated
    too (cheap given the typical block count); the alternative of
    per-variant tasks does not exist today.
    """
    try:
        from tasks.generate_cast import generate_cast_tts_task
        generate_cast_tts_task.delay(cast_id, user_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception("Failed to enqueue TTS regen for cast %s: %s", cast_id, e)


async def _generate_cast_name(db: AsyncSession, user_id: str, user_timezone: str | None) -> str:
    """Generate auto cast name: Cast {MonthAbbr}{Day}-{N} (per-user daily sequence)."""
    try:
        import zoneinfo
        user_tz = zoneinfo.ZoneInfo(user_timezone) if user_timezone else tz.utc
    except Exception:
        user_tz = tz.utc
    now = datetime.now(user_tz)
    prefix = f"Cast {now.strftime('%b')}{now.day}"
    # Count today's casts for this user in their timezone
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    result = await db.execute(
        select(func.count(Cast.id)).where(
            Cast.user_id == user_id,
            Cast.created_at >= day_start.astimezone(tz.utc).replace(tzinfo=None),
            Cast.created_at <= day_end.astimezone(tz.utc).replace(tzinfo=None),
        )
    )
    count = result.scalar() or 0
    return f"{prefix}-{count + 1}"

CAST_CREATION_FEE_CENTS = 1499


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


# ── Cast CRUD ──

@router.post("", response_model=CastResponse, status_code=201)
async def create_cast(
    req: CastCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Avatar MUST be approved
    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")
    if avatar.status != AvatarStatus.APPROVED and avatar.status != AvatarStatus.READY and avatar.id != "default":
        raise HTTPException(400, "Only approved or ready avatars can be used. Complete the clone flow and approve your avatar first.")

    cast_id = f"cst_{uuid.uuid4().hex[:12]}"

    # Auto-generate name if not provided
    cast_name = req.name
    if not cast_name:
        try:
            user_timezone = getattr(user, "timezone", None)
            cast_name = await _generate_cast_name(db, user.id, user_timezone)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            cast_name = f"Cast {datetime.now(tz.utc).strftime('%b%d')}-1"

    # Quality-based pricing
    quality_prices = {"simple": 1499, "hd": 1999, "hd_plus": 2999}
    fee = quality_prices.get(req.quality, 1499)

    cast = Cast(
        id=cast_id,
        user_id=user.id,
        avatar_id=req.avatar_id,
        name=cast_name,
        status=CastStatus.DRAFT,
        template_name=req.template_name,
        # Stage-1 creative template pick. Validated against the known set so a
        # bad id is dropped (falls back to Auto) rather than persisted blindly.
        template_id=req.template_id if get_template(req.template_id) else None,
        max_duration_minutes=req.max_duration_minutes,
        loop=req.loop,
        quality=CastQuality(req.quality) if req.quality else CastQuality.SIMPLE,
        output_format=req.output_format or "9:16",
        creation_fee_cents=fee,
        channel_id=req.channel_id,
        script_direction=req.script_direction,
        cast_type=req.cast_type or "recorded",
        description=req.description,
        duration_target_seconds=req.duration_target_seconds,
        platform_target=req.platform_target or "tiktok",
        target_platforms=req.target_platforms or [req.platform_target or "tiktok"],
        format_family="horizontal" if (req.output_format or "9:16") == "16:9" else "vertical",
        # Optional: cast-wide avatar background look picked at SetupPhase.
        # We don't validate ownership here — the SetupPhase only shows the
        # user looks of the avatar they picked, so a malicious payload
        # would just make the renderer fall back to the avatar default.
        default_avatar_look_id=req.default_avatar_look_id,
        # Production level: quick / standard / premium. Persisted on the
        # cast for the outline generator. Wiring into the prompt itself is
        # a follow-up — column lands now so client + DB are in sync.
        production_level=req.production_level or "standard",
        # Music handling: "off" | "auto" | "track_id:<id>". Default "auto".
        music_track_choice=req.music_track_choice or "auto",
        music_volume=req.music_volume,
        # PR #162 — Stage-1 LIVE/Recorded toggle payload. Persisted as-is; both
        # nullable. The outline generator reads live_mode_defaults; the b-roll
        # pipeline reads user_video_ids.
        live_mode_defaults=req.live_mode_defaults,
        user_video_ids=req.user_video_ids,
    )
    db.add(cast)

    if req.products:
        for i, prod_data in enumerate(req.products):
            prod_id = f"prod_{uuid.uuid4().hex[:12]}"
            product = Product(
                id=prod_id, user_id=user.id,
                name=prod_data.name, price=prod_data.price,
                commission_rate=prod_data.commission_rate,
                description=prod_data.description,
                tiktok_product_url=prod_data.tiktok_product_url,
                media_keys=prod_data.media_keys,
            )
            db.add(product)
            cp = CastProduct(
                id=f"cp_{uuid.uuid4().hex[:12]}",
                cast_id=cast_id, product_id=prod_id, position=i,
            )
            db.add(cp)

    if req.product_ids:
        # Previously, a product_id that didn't exist or wasn't owned by the
        # caller was skipped silently — the user got a cast attached to the
        # wrong/no product with no error, log, or Sentry breadcrumb, making it
        # undebuggable. Partition into attached vs dropped, fail loudly on any
        # drop so the frontend can surface it.
        dropped_product_ids: list[str] = []
        position = 0
        for pid in req.product_ids:
            product = await db.get(Product, pid)
            if product and product.user_id == user.id:
                cp = CastProduct(
                    id=f"cp_{uuid.uuid4().hex[:12]}",
                    cast_id=cast_id, product_id=pid, position=position,
                )
                db.add(cp)
                position += 1
            else:
                dropped_product_ids.append(pid)

        if dropped_product_ids:
            logger.warning(
                "cast_create.dropped_product_ids cast_id=%s user_id=%s dropped=%s",
                cast_id, user.id, dropped_product_ids,
            )
            with sentry_sdk.push_scope() as scope:
                scope.set_extra("cast_id", cast_id)
                scope.set_extra("user_id", user.id)
                scope.set_extra("dropped_product_ids", dropped_product_ids)
                sentry_sdk.capture_message(
                    "cast_create dropped unknown/unowned product_ids",
                    level="warning",
                )
            await db.rollback()
            raise HTTPException(
                status_code=422,
                detail={
                    "detail": "Some products could not be attached to the cast",
                    "dropped_product_ids": dropped_product_ids,
                },
            )

    if req.blocks:
        for blk_data in req.blocks:
            blk_id = f"blk_{uuid.uuid4().hex[:12]}"
            block = Block(
                id=blk_id, cast_id=cast_id,
                product_id=blk_data.product_id,
                type=BlockType(blk_data.type),
                position=blk_data.position,
                layout_mode=LayoutMode(blk_data.layout_mode) if blk_data.layout_mode else None,
                scene_image_key=blk_data.scene_image_key,
                auto_basket_enabled=blk_data.auto_basket_enabled,
                auto_basket_timeout=blk_data.auto_basket_timeout,
                mood=blk_data.mood, key_points=blk_data.key_points,
                chat_rules=blk_data.chat_rules,
            )
            db.add(block)

    await db.commit()
    await db.refresh(cast)
    try:
        await audit_log.record(
            db,
            user_id=user.id,
            action="cast.create",
            entity_type="cast",
            entity_id=cast.id,
            cast_id=cast.id,
            after={"name": cast.name, "quality": str(cast.quality), "output_format": cast.output_format, "avatar_id": cast.avatar_id},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return cast


@router.get("/music-library")
async def get_music_library(user: User = Depends(get_current_user)):
    """List the fixed background-music tracks the user can pin to a cast.

    Feeds the SetupPhase "Specific track…" dropdown and the visual-studio
    music panel. Each entry's id is used as ``music_track_choice =
    "track_id:<id>"``.
    """
    from services.music_library import list_tracks
    return {
        "tracks": [
            {"id": t.id, "name": t.name, "mood": t.mood, "url": t.url}
            for t in list_tracks()
        ]
    }


@router.get("/templates")
async def get_cast_templates(user: User = Depends(get_current_user)):
    """List the Stage-1 creative templates the user can pick before writing
    a brief.

    Each template seeds the outline generator with a default block sequence
    and bias ratios (avatar-speaking / b-roll / uploaded-video) so the user
    chooses a structural format upfront. ``template_id`` from the picked
    template threads through ``POST /api/casts`` onto the cast and constrains
    the outline at generation time.
    """
    from services.cast_templates import list_templates
    templates = list_templates()
    return {"templates": templates, "total": len(templates)}


class EstimateCostRequest(BaseModel):
    duration_s: int
    quality: str = "simple"
    layout: str = "9:16"


class EstimateCostResponse(BaseModel):
    cost_cents: int
    breakdown: dict


@router.post("/estimate-cost", response_model=EstimateCostResponse)
async def estimate_cost(
    req: EstimateCostRequest,
    user: User = Depends(get_current_user),
):
    """Live cost estimate: duration × quality multiplier × GPU rate."""
    try:
        # Base rate per second of video (in cents)
        quality_multipliers = {"simple": 1.0, "hd": 1.4, "hd_plus": 2.0}
        base_rate_per_second = 0.25  # $0.0025 per second base

        multiplier = quality_multipliers.get(req.quality, 1.0)
        duration_cost = req.duration_s * base_rate_per_second * multiplier

        # Quality base fees
        quality_fees = {"simple": 1499, "hd": 1999, "hd_plus": 2999}
        base_fee = quality_fees.get(req.quality, 1499)

        # Total: base fee + duration cost
        total_cents = base_fee + int(duration_cost)

        return EstimateCostResponse(
            cost_cents=total_cents,
            breakdown={
                "base_fee_cents": base_fee,
                "duration_cost_cents": int(duration_cost),
                "quality_multiplier": multiplier,
                "duration_s": req.duration_s,
            },
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Failed to estimate cost")


@router.get("", response_model=CastListResponse)
async def list_casts(
    status: Optional[str] = Query(None, description="Filter by cast status (e.g. 'ready')"),
    has_render: Optional[bool] = Query(
        None,
        description="If true, only return casts that have a final composed video URL.",
    ),
    include_clips: Optional[bool] = Query(
        False,
        description="If true, surface suggested_clips, approved_clips, and clip_parent_cast_id on each row.",
    ),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List casts owned by the current user.

    The Publish hub queries with `status=ready&has_render=true` to find
    rendered casts that are ready to schedule. `has_render=true` means
    `final_video_url IS NOT NULL` — soft proxy for "the cast has been
    rendered at least once."

    Each row carries enough avatar metadata to render the cast card
    thumbnail without a per-cast follow-up call: `avatar_thumbnail_url`,
    `avatar_name`, plus the legacy `avatar` object that older clients
    still read for `face_ref_key`.
    """
    from models.avatar_look import AvatarLook
    from services.r2_storage import get_r2_storage_service as _get_r2

    stmt = select(Cast).where(Cast.user_id == user.id)
    if status:
        # Cast.status is a Python Enum; both the value and the name are
        # commonly used. Match against the value to keep the API stable.
        norm = status.strip()
        try:
            target = next(
                s for s in CastStatus
                if s.value.lower() == norm.lower() or s.name.lower() == norm.lower()
            )
            stmt = stmt.where(Cast.status == target)
        except StopIteration:
            # Unknown status — return empty rather than 400 so the FE
            # doesn't crash on a typo'd query string.
            return CastListResponse(casts=[], total=0)
    if has_render is True:
        stmt = stmt.where(Cast.final_video_url.is_not(None))
    elif has_render is False:
        stmt = stmt.where(Cast.final_video_url.is_(None))

    result = await db.execute(stmt.order_by(Cast.created_at.desc()))
    casts = result.scalars().all()

    # Single-shot lookups so the list call stays O(1) queries regardless
    # of cast count.
    avatar_ids = list({c.avatar_id for c in casts if c.avatar_id})
    look_ids = list({c.default_avatar_look_id for c in casts if c.default_avatar_look_id})

    avatars_by_id: dict[str, Avatar] = {}
    if avatar_ids:
        avatars_by_id = {
            a.id: a for a in (
                await db.execute(select(Avatar).where(Avatar.id.in_(avatar_ids)))
            ).scalars().all()
        }

    looks_by_id: dict[str, AvatarLook] = {}
    if look_ids:
        looks_by_id = {
            l.id: l for l in (
                await db.execute(select(AvatarLook).where(AvatarLook.id.in_(look_ids)))
            ).scalars().all()
        }

    r2 = _get_r2()

    def _resolve_avatar_meta(cast: Cast) -> tuple[Optional[str], Optional[str]]:
        # Default-look thumbnail wins (it's the cast-wide background look
        # the user picked at SetupPhase). Fall back to the avatar's own
        # face image. `face_ref_key` is an R2 key — sign it for the FE.
        look = looks_by_id.get(cast.default_avatar_look_id) if cast.default_avatar_look_id else None
        avatar = avatars_by_id.get(cast.avatar_id) if cast.avatar_id else None
        thumb_key = (look.face_ref_key if look else None) or (avatar.face_ref_key if avatar else None)
        thumb_url = r2.get_public_url(thumb_key) if thumb_key else None
        return thumb_url, (avatar.name if avatar else None)

    out = []
    for cast in casts:
        thumb_url, avatar_name = _resolve_avatar_meta(cast)
        avatar = avatars_by_id.get(cast.avatar_id) if cast.avatar_id else None
        d = CastResponse.model_validate(cast).model_dump()
        d["avatar_thumbnail_url"] = thumb_url
        d["avatar_name"] = avatar_name
        # Legacy field — MyCasts.tsx reads cast.avatar?.face_ref_key directly.
        d["avatar"] = (
            {"id": avatar.id, "name": avatar.name, "face_ref_key": avatar.face_ref_key}
            if avatar else None
        )
        if include_clips:
            d["final_video_url"] = cast.final_video_url
            d["suggested_clips"] = cast.suggested_clips or []
            d["approved_clips"] = cast.approved_clips or []
            d["clip_parent_cast_id"] = cast.clip_parent_cast_id
            d["clip_block_ids"] = cast.clip_block_ids or []
        out.append(d)
    return {"casts": out, "total": len(out)}


@router.get("/{cast_id}")
async def get_cast(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Cast)
        .options(
            selectinload(Cast.blocks).selectinload(Block.variants),
            selectinload(Cast.products).selectinload(CastProduct.product),
        )
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    # Build response with blocks and variants (exclude soft-deleted).
    # Variants are sorted with the active one first so the frontend's
    # `block.variants[0]` lookup always lands on the active variant.
    # SQLAlchemy's default relationship order is insertion-time, which
    # for blocks with multiple variants is NOT guaranteed to put the
    # active one first — leading to the user seeing the wrong text.
    from services.r2_storage import get_r2_storage_service as _get_r2
    _r2 = _get_r2()

    # PR #66 (Fix 1): default block background falls back to the avatar's
    # profile face image when neither stock_media_url nor an LLM-suggested
    # background is set. Pre-resolve here so the per-block payload can
    # carry a `default_background_url` the editor renders instead of the
    # arrow placeholder.
    avatar_profile_url: Optional[str] = None
    if cast.avatar_id:
        try:
            _av_for_bg = await db.get(Avatar, cast.avatar_id)
            if _av_for_bg and getattr(_av_for_bg, "face_ref_key", None):
                avatar_profile_url = _r2.get_public_url(_av_for_bg.face_ref_key)
        except Exception as _av_exc:
            sentry_sdk.capture_exception(_av_exc)

    # PR #66 (Fix 2): pre-fetch every per-block start/end frame AvatarLook
    # so the editor can hydrate the frame thumbnails on initial mount
    # without waiting for a separate avatar-looks fetch to catch up. We
    # build a {look_id: image_url} map keyed on the freshly-resolved CDN
    # URL for the look's face_ref_key.
    look_url_map: dict[str, str] = {}
    if cast.avatar_id:
        try:
            from models.avatar_look import AvatarLook as _AvatarLook
            _looks_q = await db.execute(
                select(_AvatarLook).where(_AvatarLook.avatar_id == cast.avatar_id)
            )
            for _lk in _looks_q.scalars().all():
                if _lk.face_ref_key:
                    look_url_map[_lk.id] = _r2.get_public_url(_lk.face_ref_key)
        except Exception as _lk_exc:
            sentry_sdk.capture_exception(_lk_exc)

    blocks_data = []
    for block in cast.blocks:
        if block.deleted_at is not None:
            continue
        variants_data = []
        sorted_variants = sorted(
            block.variants,
            key=lambda x: (not bool(x.is_active), x.created_at or 0),
        )
        for v in sorted_variants:
            _effective_key = getattr(v, 'final_video_key', None) or v.video_key
            _stream_url = _r2.get_signed_url(_effective_key, expires_in=3600) if _effective_key else None
            _audio_key = v.audio_key or getattr(v, 'tts_r2_key', None) or None
            variants_data.append({
                "id": v.id,
                "variant_label": v.variant_label,
                "script_text": v.script_text,
                "status": v.status.value if v.status else "pending",
                "audio_key": _audio_key,
                "video_key": v.video_key,
                "final_video_key": getattr(v, 'final_video_key', None),
                "clip_url": f"https://media.luminacast.com/{_effective_key}" if _effective_key else None,
                "stream_url": _stream_url,
                "composition_warnings": v.composition_warnings or [],
                "audio_url": f"https://media.luminacast.com/{_audio_key}" if _audio_key else None,
                "tts_duration_seconds": getattr(v, "tts_duration_seconds", None),
                "duration_seconds": v.duration_seconds,
                "generation_error": v.generation_error,
                "motion_prompt": getattr(v, 'motion_prompt', '') or '',
                "caption_words": v.caption_words,
                "caption_segments": v.caption_segments,
                "is_active": v.is_active,
            })
        blocks_data.append({
            "id": block.id,
            "type": block.type.value,
            "position": block.position,
            "product_id": block.product_id,
            "layout_mode": block.layout_mode.value if block.layout_mode else "full_avatar",
            "mood": block.mood,
            "key_points": block.key_points,
            "is_active": getattr(block, 'is_active', True),
            "category": getattr(block, "category", "avatar_speaking"),
            # Round-6 Bug B: surface the per-block camera framing for debug
            # visibility (and future per-block framing UI). Default MEDIUM.
            "framing": getattr(block, "framing", None) or "MEDIUM",
            "avatar_angle": getattr(block, "avatar_angle", "front"),
            "background_id": getattr(block, 'background_id', None),
            "render_mode": getattr(block, 'render_mode', 'avatar_full') or 'avatar_full',
            "user_video_asset_id": getattr(block, 'user_video_asset_id', None),
            "avatar_look_id": getattr(block, 'avatar_look_id', None),
            "pip_engine": getattr(block, 'pip_engine', 'infinitetalk_rendered'),
            "body_motion_start_look_id": block.body_motion_start_look_id,
            "body_motion_end_look_id": block.body_motion_end_look_id,
            "body_motion_prompt": block.body_motion_prompt,
            # PR #66 Fix 2: hydrate start/end frame URLs in the initial
            # payload so block #2 (and any non-first block) renders the
            # thumbnails on mount without waiting for a separate
            # avatar-looks fetch. Falls back to None when the block has
            # no frame pinned yet.
            "first_frame_url": (
                look_url_map.get(block.body_motion_start_look_id)
                if block.body_motion_start_look_id else None
            ),
            "last_frame_url": (
                look_url_map.get(block.body_motion_end_look_id)
                if block.body_motion_end_look_id else None
            ),
            # PR #66 Fix 1: default background. Order of preference:
            #   1. Block-level stock_media_url (LLM-suggested via Pexels).
            #   2. Block.parallel_media[0].url (Smart Cast b-roll).
            #   3. Avatar profile face image (universal fallback).
            # The editor's BlockVisualPreview reads this when neither
            # stock_media_thumbnail nor parallel_media is set so the
            # "Choose background" arrow placeholder is replaced with
            # something the user actually wants to look at.
            "default_background_url": (
                getattr(block, "stock_media_thumbnail", None)
                or getattr(block, "stock_media_url", None)
                or (
                    (block.parallel_media or [{}])[0].get("thumbnail")
                    if isinstance(block.parallel_media, list)
                    and block.parallel_media
                    and isinstance(block.parallel_media[0], dict)
                    else None
                )
                or (
                    (block.parallel_media or [{}])[0].get("url")
                    if isinstance(block.parallel_media, list)
                    and block.parallel_media
                    and isinstance(block.parallel_media[0], dict)
                    else None
                )
                or avatar_profile_url
            ),
            # Per-block frame seeds for the AI-generated start/end frames.
            # The frontend prefills the carousel "+ Add Frame" modal with
            # these so the user starts editing from the writer's seed
            # rather than a blank textarea.
            "body_motion_start_prompt": getattr(block, "body_motion_start_prompt", None),
            "body_motion_end_prompt": getattr(block, "body_motion_end_prompt", None),
            # avatar_action: per-block scene frame prompts (mirrors
            # body_motion_*_prompt but seeded with FLUX Kontext scene
            # frames instead of generic body shots).
            "action_start_prompt": getattr(block, "action_start_prompt", None),
            "action_end_prompt": getattr(block, "action_end_prompt", None),
            # avatar_action blocks reuse the body_motion_prompt column for
            # the verb-led motion description. Surface it under
            # `motion_prompt` so the frontend can read either field consistently.
            "motion_prompt": block.body_motion_prompt or "",
            # Generated-video frame controls. Frontend uses these keys to show
            # preview thumbnails next to the script textarea on generated_video
            # blocks. URL is built client-side from R2_PUBLIC_URL + key.
            "gen_video_first_frame_key": getattr(block, "gen_video_first_frame_key", None),
            "gen_video_last_frame_key": getattr(block, "gen_video_last_frame_key", None),
            # Smart Cast metadata. The editor uses these to (a) show the
            # AI's suggested b-roll tag as a chip next to the search
            # button, (b) render a preview thumbnail in the storyboard
            # frame on the left of each block, and (c) use the query as
            # the default seed when the user opens the Pexels picker.
            "hook_type": getattr(block, "hook_type", None),
            "background_type": getattr(block, "background_type", None),
            "transition_in": getattr(block, "transition_in", None),
            "energy_level": getattr(block, "energy_level", None),
            "stock_media_query": getattr(block, "stock_media_query", None),
            "stock_media_url": getattr(block, "stock_media_url", None),
            "stock_media_thumbnail": getattr(block, "stock_media_thumbnail", None),
            "stock_media_kind": getattr(block, "stock_media_kind", None),
            "stock_media_pexels_id": getattr(block, "stock_media_pexels_id", None),
            # parallel_media is the canonical b-roll list for an
            # avatar/PIP block. Auto-populate seeds it with one
            # AI-suggested item; the user can swap or extend it.
            "parallel_media": getattr(block, "parallel_media", None) or [],
            # Generic per-block metadata bag. Today carries product_carousel
            # flags (toggle, speed, transition, asset_ids) and the PR #76
            # avatar_action voiceover_enabled toggle; future per-block
            # ad-hoc settings can land here without a dedicated column.
            "metadata": getattr(block, "block_metadata", None) or {},
            # PR #76: surface voiceover_enabled as a top-level field so
            # the Script step can read it without unpacking metadata.
            # None = LLM default (renderer treats as True if dialogue
            # present); True/False = explicit user override.
            "voiceover_enabled": (
                (getattr(block, "block_metadata", None) or {}).get("voiceover_enabled")
            ),
            "variants": variants_data,
        })

    from urllib.parse import quote as _url_quote
    from config import settings
    from models.trending_product import TrendingProduct
    products_data = []
    for cp in cast.products:
        if cp.product:
            cover_url = ""
            if cp.product.cover_image_key:
                cover_url = f"{settings.R2_PUBLIC_URL}/{cp.product.cover_image_key}"
            elif cp.product.tiktok_product_id:
                tp = await db.scalar(
                    select(TrendingProduct).where(
                        TrendingProduct.tiktok_product_id == cp.product.tiktok_product_id,
                        TrendingProduct.cover_image_url != "",
                    )
                )
                if tp and tp.cover_image_url:
                    cover_url = f"/api/discover/proxy-image?url={_url_quote(tp.cover_image_url, safe='')}"
            products_data.append({
                "id": cp.product.id,
                "name": cp.product.name,
                "price": cp.product.price,
                "cover_image_key": getattr(cp.product, 'cover_image_key', None) or "",
                "cover_image_url": cover_url,
            })

    # On-the-fly migration: convert old overlays/product_overlay to scene_objects
    effects = cast.effects_config or {}
    if "scene_objects" not in effects and ("overlays" in effects or "product_overlay" in effects):
        scene_objects = []
        # Convert old product_overlay
        if effects.get("product_overlay", {}).get("enabled"):
            for blk in cast.blocks:
                if blk.deleted_at is not None:
                    continue
                if blk.product_id and blk.type in (BlockType.PRODUCT, BlockType.FLASH_SALE, BlockType.CTA):
                    scene_objects.append({
                        "id": f"so_migrated_{blk.id[:8]}",
                        "kind": "product",
                        "x": 0.7, "y": 0.7,
                        "width": effects.get("product_overlay", {}).get("size", 0.25),
                        "height": 0.25,
                        "rotation": 0, "opacity": 1.0, "z_index": 10,
                        "block_ids": [blk.id],
                        "start_ms": 0, "duration_ms": 5000,
                        "product": {"product_id": blk.product_id, "show_price": effects.get("product_overlay", {}).get("show_price", False), "show_title": effects.get("product_overlay", {}).get("show_title", False)},
                    })
        # Convert old overlays
        for ov in effects.get("overlays", []):
            scene_objects.append({
                "id": ov.get("id", f"so_migrated_{uuid.uuid4().hex[:8]}"),
                "kind": ov.get("kind", "text"),
                "x": ov.get("x", 0.5), "y": ov.get("y", 0.5),
                "width": 0.3, "height": 0.1,
                "rotation": 0, "opacity": 1.0, "z_index": 20,
                "block_ids": ov.get("block_ids", []),
                "start_ms": ov.get("start_ms", 0),
                "duration_ms": ov.get("duration_ms", 3000),
                "text": {"content": ov.get("text", ""), "style": "bold_pop"} if ov.get("kind") == "text" else None,
                "sticker": {"emoji": ov.get("sticker_emoji", "⭐")} if ov.get("kind") == "sticker" else None,
                "sfx": {"preset": ov.get("sfx_preset", "ping"), "volume": ov.get("sfx_volume", 0.8)} if ov.get("kind") == "sfx" else None,
            })
        effects["scene_objects"] = scene_objects

    # Compute aggregated render_status from render_jobs
    render_status = await _get_render_status_for_cast(db, cast.id)

    return {
        "id": cast.id,
        "name": cast.name,
        "status": cast.status.value,
        "avatar_id": cast.avatar_id,
        "quality": cast.quality.value if cast.quality else "simple",
        "template_name": cast.template_name,
        "max_duration_minutes": cast.max_duration_minutes,
        "loop": cast.loop,
        "creation_fee_cents": cast.creation_fee_cents,
        "creation_paid": cast.creation_paid,
        "generation_progress": cast.generation_progress or 0,
        "generation_error": cast.generation_error,
        "output_format": cast.output_format or "9:16",
        "format_family": getattr(cast, "format_family", "vertical"),
        "parent_cast_id": getattr(cast, "parent_cast_id", None),
        "target_platforms": getattr(cast, "target_platforms", []) or [],
        "audio_stale_since": getattr(cast, "audio_stale_since", None),
        "progress_step": cast.progress_step,
        "total_clips": cast.total_clips or 0,
        "completed_clips": cast.completed_clips or 0,
        "effects_config": effects,
        "script_direction": cast.script_direction,
        "final_video_url": cast.final_video_url,
        # AI background music + caption preset. The editor reads these
        # to put music on the lowest audio track and apply caption
        # styles uniformly. background_music_url is null until the
        # tasks.auto_music task finishes (typically <30s after outline).
        "background_music_url": getattr(cast, "background_music_url", None),
        "background_music_mood": getattr(cast, "background_music_mood", None),
        "background_music_tags": getattr(cast, "background_music_tags", None) or [],
        "music_track_choice": getattr(cast, "music_track_choice", "auto") or "auto",
        "music_volume": getattr(cast, "music_volume", None),
        "caption_preset": getattr(cast, "caption_preset", None),
        "default_avatar_look_id": getattr(cast, "default_avatar_look_id", None),
        "created_at": cast.created_at.isoformat() if cast.created_at else None,
        "blocks": blocks_data,
        "products": products_data,
        "render_status": render_status,
        # CHANGE 5/6 — Auto-Clips. `suggested_clips` is what the LLM
        # proposed; `approved_clips` is what the user kept (each carries
        # the child cast id once approved). `clip_parent_cast_id` is set
        # on the child cast itself so the FE can render a back-link.
        "suggested_clips": cast.suggested_clips or [],
        "approved_clips": cast.approved_clips or [],
        "clip_parent_cast_id": cast.clip_parent_cast_id,
        "clip_block_ids": cast.clip_block_ids or [],
    }


async def _get_render_status_for_cast(db: AsyncSession, cast_id: str) -> dict | None:
    """Aggregated render status from render_jobs for a cast.

    Takes the worst state across all variant render_jobs, sums ETAs.
    """
    from sqlalchemy import text as sa_text
    rows = await db.execute(
        sa_text("""
            SELECT state, error_message, progress_percent, job_type
            FROM render_jobs
            WHERE cast_id = :cast_id
            ORDER BY created_at DESC
        """),
        {"cast_id": cast_id},
    )
    jobs = rows.fetchall()
    if not jobs:
        return None

    # State priority: FAILED > ENDPOINT_DOWN > STALLED > IN_PROGRESS > QUEUED > COMPLETED
    state_priority = {
        "FAILED": 6, "ENDPOINT_DOWN": 5, "STALLED": 4,
        "IN_PROGRESS": 3, "QUEUED": 2, "COMPLETED": 1,
    }
    worst_state = max(jobs, key=lambda j: state_priority.get(j.state, 0))

    from services.render_eta import estimate_wait_seconds
    eta = await estimate_wait_seconds(db, "cast_variant", "runpod_infinitetalk")

    return {
        "state": worst_state.state,
        "eta": eta.get("estimated_seconds"),
        "position": eta.get("position", 0),
        "confidence": eta.get("confidence", "none"),
        "error_message": worst_state.error_message if worst_state.state in ("FAILED", "STALLED") else None,
    }


@router.put("/{cast_id}", response_model=CastResponse)
async def update_cast(
    cast_id: str,
    req: CastCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    if cast.status not in (CastStatus.DRAFT, CastStatus.OUTLINE_REVIEW, CastStatus.SCRIPT_REVIEW, CastStatus.TEMPLATE_SELECT):
        raise HTTPException(400, "Cast cannot be edited in current status")

    before = {"name": cast.name, "template_name": cast.template_name, "output_format": cast.output_format, "script_direction": cast.script_direction}
    cast.name = req.name
    cast.template_name = req.template_name
    cast.max_duration_minutes = req.max_duration_minutes
    cast.loop = req.loop
    if hasattr(req, 'effects_config') and req.effects_config is not None:
        cast.effects_config = req.effects_config
    if hasattr(req, 'output_format') and req.output_format is not None:
        cast.output_format = req.output_format
    if req.description is not None:
        cast.description = req.description
    if req.duration_target_seconds is not None:
        cast.duration_target_seconds = req.duration_target_seconds
    if req.platform_target is not None:
        cast.platform_target = req.platform_target
    if req.script_direction is not None:
        cast.script_direction = req.script_direction
    if getattr(req, "background_music_url", None) is not None:
        cast.background_music_url = req.background_music_url
    if getattr(req, "background_music_mood", None) is not None:
        cast.background_music_mood = req.background_music_mood
    if getattr(req, "background_music_tags", None) is not None:
        cast.background_music_tags = req.background_music_tags
    if getattr(req, "caption_preset", None) is not None:
        cast.caption_preset = req.caption_preset
    if getattr(req, "live_mode_defaults", None) is not None:
        cast.live_mode_defaults = req.live_mode_defaults
    if getattr(req, "user_video_ids", None) is not None:
        cast.user_video_ids = req.user_video_ids
    await db.commit()
    await db.refresh(cast)
    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.update", entity_type="cast",
            entity_id=cast.id, cast_id=cast.id,
            before=before,
            after={"name": cast.name, "template_name": cast.template_name, "output_format": cast.output_format, "script_direction": cast.script_direction},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return cast


class CastPatchRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    duration_target_seconds: Optional[int] = None
    platform_target: Optional[str] = None
    script_direction: Optional[str] = None
    output_format: Optional[str] = None
    clear_audio_stale: Optional[bool] = None  # Phase 2.5 — clear audio_stale_since
    # Music + caption preset + cast-wide avatar background. The Music
    # tab calls PATCH with background_music_url + mood; the SetupPhase
    # picker sends default_avatar_look_id; the caption style bar sends
    # caption_preset.
    background_music_url: Optional[str] = None
    background_music_mood: Optional[str] = None
    background_music_tags: Optional[List[str]] = None
    music_track_choice: Optional[str] = None
    music_volume: Optional[float] = None
    caption_preset: Optional[dict] = None
    default_avatar_look_id: Optional[str] = None
    # PR #162 — Stage-1 LIVE/Recorded toggle payload (see CastCreate).
    live_mode_defaults: Optional[dict] = None
    user_video_ids: Optional[List[str]] = None


@router.patch("/{cast_id}", response_model=CastResponse)
async def patch_cast(
    cast_id: str,
    req: CastPatchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    for field in (
        "name",
        "description",
        "duration_target_seconds",
        "platform_target",
        "script_direction",
        "output_format",
        "background_music_url",
        "background_music_mood",
        "background_music_tags",
        "music_track_choice",
        "music_volume",
        "caption_preset",
        # PR #162 — Stage-1 LIVE/Recorded toggle payload.
        "live_mode_defaults",
        "user_video_ids",
    ):
        val = getattr(req, field)
        if val is not None:
            setattr(cast, field, val)

    # default_avatar_look_id needs validation: only allow setting it to a
    # look that belongs to this cast's avatar. Allow explicit empty string
    # / None to clear it.
    if req.default_avatar_look_id is not None:
        if req.default_avatar_look_id == "":
            cast.default_avatar_look_id = None
        else:
            from models.avatar_look import AvatarLook
            look = await db.get(AvatarLook, req.default_avatar_look_id)
            if not look or look.avatar_id != cast.avatar_id:
                raise HTTPException(400, "Look does not belong to this cast's avatar")
            cast.default_avatar_look_id = req.default_avatar_look_id

    # Phase 2.5 — Clear audio stale flag when user refreshes timeline
    if req.clear_audio_stale:
        cast.audio_stale_since = None

    await db.commit()
    await db.refresh(cast)
    try:
        changed = {f: getattr(req, f) for f in ("name", "description", "output_format", "script_direction", "background_music_mood", "default_avatar_look_id") if getattr(req, f, None) is not None}
        await audit_log.record(
            db, user_id=user.id, action="cast.update", entity_type="cast",
            entity_id=cast.id, cast_id=cast.id, after=changed,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return cast


@router.delete("/{cast_id}", status_code=204)
async def delete_cast(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a cast and all dependent rows.

    Cascade: child auto-clip casts, blocks/variants, cast_renders, render_jobs,
    cast_products, generation_cost, api_usage_log, stream_sessions, cast_versions.
    Social posts referencing the cast have cast_id nulled (kept as a record).
    """
    try:
        cast = await db.get(Cast, cast_id)
        if not cast or cast.user_id != user.id:
            raise HTTPException(404, "Cast not found")
        if cast.status in (CastStatus.LIVE, CastStatus.GENERATING):
            raise HTTPException(400, "Cannot delete active cast")

        from sqlalchemy import delete as sa_delete, update as sa_update
        from models.cast_render import CastRender
        from models.render_job import RenderJob
        from models.api_usage_log import ApiUsageLog
        from models.generation_cost import GenerationCost
        from models.stream_session import StreamSession
        from models.social_post import SocialPost

        # 1. Recurse into child auto-clip casts (clip_parent_cast_id) so their
        #    own non-cascade FKs (renders, render_jobs, etc.) are cleaned up
        #    too. Without this, the DB-level CASCADE on clip_parent_cast_id
        #    would drop the row but leave orphaned render rows behind.
        child_ids = (
            await db.execute(
                select(Cast.id).where(Cast.clip_parent_cast_id == cast_id)
            )
        ).scalars().all()
        for child_id in child_ids:
            await _purge_cast_dependents(db, child_id)
            await db.execute(sa_delete(Cast).where(Cast.id == child_id))

        # 2. Null out cross-format parent links pointing to us (SET NULL is
        #    declared on the column, but only fires when the DB-level FK
        #    rule is in place — be explicit so this works even on legacy
        #    schemas where the constraint is missing).
        await db.execute(
            sa_update(Cast).where(Cast.parent_cast_id == cast_id).values(parent_cast_id=None)
        )

        # 3. Null cast_id on social posts (publish records are kept for history).
        await db.execute(
            sa_update(SocialPost).where(SocialPost.cast_id == cast_id).values(cast_id=None)
        )

        # 4. Purge this cast's own dependents.
        await _purge_cast_dependents(db, cast_id)

        try:
            await audit_log.record(
                db, user_id=user.id, action="cast.delete", entity_type="cast",
                entity_id=cast_id, cast_id=cast_id,
                before={"name": cast.name, "status": str(cast.status)},
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)

        await db.delete(cast)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        await db.rollback()
        raise HTTPException(500, "Failed to delete cast")


async def _purge_cast_dependents(db: AsyncSession, cast_id: str) -> None:
    """Delete all rows that reference a cast via cast_id (child-first).

    Used by delete_cast for both the target cast and its auto-clip children.
    Variants are deleted via their parent blocks. Cast versions and cast_products
    cascade at the DB level but we mirror them here for older schemas.
    """
    from sqlalchemy import delete as sa_delete
    from models.cast_render import CastRender
    from models.render_job import RenderJob
    from models.api_usage_log import ApiUsageLog
    from models.generation_cost import GenerationCost
    from models.stream_session import StreamSession

    block_ids = (
        await db.execute(select(Block.id).where(Block.cast_id == cast_id))
    ).scalars().all()
    if block_ids:
        await db.execute(sa_delete(Variant).where(Variant.block_id.in_(block_ids)))
        await db.execute(sa_delete(Block).where(Block.id.in_(block_ids)))

    await db.execute(sa_delete(CastRender).where(CastRender.cast_id == cast_id))
    await db.execute(sa_delete(RenderJob).where(RenderJob.cast_id == cast_id))
    await db.execute(sa_delete(CastProduct).where(CastProduct.cast_id == cast_id))
    await db.execute(sa_delete(StreamSession).where(StreamSession.cast_id == cast_id))
    await db.execute(sa_delete(GenerationCost).where(GenerationCost.cast_id == cast_id))
    await db.execute(sa_delete(ApiUsageLog).where(ApiUsageLog.cast_id == cast_id))
    await db.execute(sa_delete(CastVersion).where(CastVersion.cast_id == cast_id))


# ── Retry Endpoints ──

@router.post("/{cast_id}/retry")
async def retry_cast_generation(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retry generation for a failed cast. Only re-generates failed variants."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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
    generate_cast_task.delay(cast_id, user.id)

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


@router.post("/{cast_id}/variants/{variant_id}/retry")
async def retry_variant(
    cast_id: str,
    variant_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retry a single failed variant."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    variant = await db.get(Variant, variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")
    if variant.status != VariantStatus.FAILED and str(variant.status) != "failed":
        raise HTTPException(400, "Variant not in failed state")

    variant.status = VariantStatus.PENDING
    variant.generation_error = None
    variant.runpod_job_id = None
    variant.retry_count = (variant.retry_count or 0) + 1

    if cast.status == CastStatus.GENERATION_FAILED:
        cast.status = CastStatus.GENERATING
        cast.generation_error = None

    await db.commit()

    from tasks.generate_cast import generate_cast_task
    generate_cast_task.delay(cast_id, user.id)

    try:
        await audit_log.record(
            db, user_id=user.id, action="block.variant_retry", entity_type="block",
            entity_id=variant.block_id, cast_id=cast_id,
            after={"variant_id": variant_id, "retry_count": variant.retry_count},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"variant_id": variant_id, "status": "pending", "retry_count": variant.retry_count}


# ── Layout Config ──

@router.put("/{cast_id}/layout")
async def save_layout(
    cast_id: str,
    layout_config: dict = Body(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Effects Config ──

@router.put("/{cast_id}/effects")
async def save_effects_config(
    cast_id: str,
    effects_config: dict = Body(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save effects configuration for a cast."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Background Upload ──

@router.post("/{cast_id}/background")
async def upload_cast_background(
    cast_id: str,
    file: UploadFile,
    media_type: str = Query("image"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a background image or video for a cast."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    ext = "jpg" if media_type == "image" else "mp4"
    r2_key = f"creators/{user.id}/casts/{cast_id}/background.{ext}"

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


# ── Scene Object Image Upload ──

@router.post("/{cast_id}/blocks/{block_id}/frames/upload")
async def upload_block_frame(
    cast_id: str,
    block_id: str,
    slot: str = Query(..., regex="^(first|last)$"),
    file: UploadFile = FastAPIFile(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a first or last frame image for a generated_video block.

    The slot parameter must be either 'first' or 'last'. The image is stored
    in R2 and the corresponding column on `blocks` is updated. Returns the
    R2 key + a public URL the frontend can preview.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    import uuid as _uuid
    img_id = _uuid.uuid4().hex[:12]
    ext = "png"
    if file.content_type and "jpeg" in file.content_type:
        ext = "jpg"
    r2_key = f"creators/{user.id}/casts/{cast_id}/blocks/{block_id}/{slot}_frame_{img_id}.{ext}"

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
    db: AsyncSession = Depends(get_db),
):
    """Upload an image for use as a scene object."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    import uuid as _uuid
    img_id = _uuid.uuid4().hex[:12]
    ext = "png"
    if file.content_type and "jpeg" in file.content_type:
        ext = "jpg"
    r2_key = f"creators/{user.id}/casts/{cast_id}/scene/{img_id}.{ext}"

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
    db: AsyncSession = Depends(get_db),
):
    """Remove background from a scene object image using rembg."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Bulk Block Save (replaces all blocks + creates variants) ──

@router.put("/{cast_id}/blocks")
async def save_blocks_bulk(
    cast_id: str,
    req: BulkBlocksSave,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace all blocks for a cast and create default variants with script text."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Block Management ──

@router.post("/{cast_id}/blocks")
async def add_block(
    cast_id: str,
    block_type: str = Body("product"),
    product_id: Optional[str] = Body(None),
    sort_order: int = Body(0),
    category: str = Body("avatar_speaking"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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
            if not uva or uva.user_id != user.id:
                raise HTTPException(404, "User video asset not found or not owned by you")
            if uva.deleted_at is not None:
                raise HTTPException(400, "User video asset has been deleted")
        block.user_video_asset_id = user_video_asset_id or None

    # For voiceover/pip, ensure user_video_asset_id is set
    effective_mode = render_mode if render_mode is not None else getattr(block, "render_mode", "avatar_full")
    if effective_mode in ("voiceover", "pip"):
        effective_vid = user_video_asset_id if user_video_asset_id is not None else getattr(block, "user_video_asset_id", None)
        if not effective_vid:
            raise HTTPException(400, f"render_mode '{effective_mode}' requires a user_video_asset_id")

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




# ── Body-motion frame generation ────────────────────────────────────
#
# A body_motion (avatar_acting) block is rendered by interpolating between
# a START frame and an END frame. Each frame is an AI-generated still
# (FLUX Kontext, identity preserved) authored from a prompt. The Opus
# script-writer seeds each block with body_motion_start_prompt /
# body_motion_end_prompt; these endpoints let the user generate, list,
# and regenerate frames from those (or edited) prompts.
#
# Persistence model: every generated frame is an AvatarLook row whose
# look_type follows "body_motion_block_<block_id>_<kind>" so the carousel
# can list all alternates for a kind by prefix-matching look_type. The
# block's body_motion_start_look_id / body_motion_end_look_id pin which
# alternate is currently selected for the renderer.


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


@router.post("/{cast_id}/blocks/{block_id}/body_motion_frame")
async def generate_body_motion_frame(
    cast_id: str,
    block_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
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
    if not cast or cast.user_id != user.id:
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
    db: AsyncSession = Depends(get_db),
):
    """List the AI-generated start/end frames previously created for this
    block, grouped by kind so the editor's carousel can render two
    independent strips. Includes pending/failed rows so the UI can show
    spinners and error states.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Avatar Action frame generation (avatar_action category) ────────────
#
# avatar_action merged the legacy avatar_motion (T2V, no face) and
# avatar_acting (I2V from generic body shots) categories. The renderer
# always uses the avatar's face via I2V with SCENE-SPECIFIC first/last
# frames generated by FLUX Kontext from the avatar's face_ref + a
# scene-prompt (jungle, office, beach, etc.). These endpoints mirror the
# body_motion_frame endpoints above, but the look_type prefix is
# `action_block_<id>_<kind>` and the avatar's physical description is
# auto-prepended to each prompt so the Director never has to repeat it.


@router.post("/{cast_id}/blocks/{block_id}/action_frame")
async def generate_action_frame(
    cast_id: str,
    block_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
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
    if not cast or cast.user_id != user.id:
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
    db: AsyncSession = Depends(get_db),
):
    """List the AI-generated start/end scene frames for an avatar_action block.

    Mirrors list_body_motion_frames but keys on the `action_block_<id>_*`
    look_type prefix so legacy body_motion frames are not surfaced here.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


@router.delete("/{cast_id}/blocks/{block_id}", status_code=204)
async def delete_block(
    cast_id: str, block_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Variant Management ──

@router.post("/{cast_id}/blocks/{block_id}/variants")
async def add_variant(
    cast_id: str, block_id: str,
    script_text: str = Body(""),
    variant_label: str = Body("A"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    variant = Variant(
        id=f"var_{uuid.uuid4().hex[:12]}",
        block_id=block_id,
        script_text=script_text,
        variant_label=variant_label,
        status=VariantStatus.DRAFT,
    )
    db.add(variant)
    await db.commit()
    await db.refresh(variant)
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.variant_create", entity_type="block",
            entity_id=block_id, cast_id=cast_id,
            after={"variant_id": variant.id, "variant_label": variant.variant_label},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"id": variant.id, "script_text": variant.script_text, "variant_label": variant.variant_label}


class UpdateVariantRequest(BaseModel):
    script_text: str | None = None
    render_mode: str | None = None


@router.put("/{cast_id}/blocks/{block_id}/variants/{variant_id}")
async def update_variant(
    cast_id: str, block_id: str, variant_id: str,
    body: UpdateVariantRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    variant = await db.get(Variant, variant_id)
    if not variant or variant.block_id != block_id:
        raise HTTPException(404, "Variant not found")
    text_changed = False
    if body.script_text is not None and body.script_text != variant.script_text:
        variant.script_text = body.script_text
        text_changed = True
        # Mark audio as stale — TTS must regenerate before rendering. The
        # clear + commit is atomic with the script_text change so a render
        # that races this edit can't pick up stale audio paired with the
        # new script.
        _mark_variant_audio_stale(variant, cast)
    if body.render_mode is not None:
        variant.render_mode = body.render_mode
    await db.commit()
    if text_changed:
        _enqueue_tts_regen(cast_id, user.id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.variant_update", entity_type="block",
            entity_id=block_id, cast_id=cast_id,
            after={"variant_id": variant.id, "audio_stale": text_changed, "render_mode": body.render_mode},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {
        "id": variant.id,
        "script_text": variant.script_text,
        "audio_stale": text_changed,
    }


# ── AI Script Rewrite (uses voice profile) ──

class RewriteRequest(BaseModel):
    prompt: str


@router.post("/{cast_id}/blocks/{block_id}/variants/{variant_id}/rewrite")
async def rewrite_variant_script(
    cast_id: str, block_id: str, variant_id: str,
    req: RewriteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    variant = await db.get(Variant, variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")
    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")
    if variant.block_id != block_id:
        raise HTTPException(400, "Variant does not belong to this block")

    # Get voice profile from channel
    voice_profile = None
    if cast.channel_id:
        channel = await db.get(Channel, cast.channel_id)
        if channel:
            voice_profile = channel.voice_profile

    from config import settings as app_settings
    if not app_settings.OPENROUTER_API_KEY:
        raise HTTPException(503, "AI rewrite requires API key configuration")

    # If the user has pinned a product to this block, fold its name +
    # short description into the prompt so the rewrite weaves the product
    # in naturally (rather than the rewriter inventing or dropping it).
    product_context = ""
    if block.product_id:
        try:
            pinned = await db.get(Product, block.product_id)
            if pinned:
                desc = (pinned.description or "")[:300].strip()
                product_context = (
                    f"\n\nThis block features the product \"{pinned.name}\""
                    + (f" — {desc}" if desc else "")
                    + ". Mention it naturally in the rewritten script."
                )
        except Exception as e:
            sentry_sdk.capture_exception(e)

    try:
        from services.openrouter import get_openrouter_service
        openrouter = get_openrouter_service()
        system_prompt = _build_script_system_prompt(voice_profile)
        result_text = await openrouter.generate_text(
            prompt=(
                f"Current script:\n{variant.script_text}\n\n"
                f"Instruction: {req.prompt}{product_context}"
            ),
            system_prompt=system_prompt, max_tokens=512, temperature=0.7,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(502, "AI rewrite failed")

    new_text = result_text.strip()
    text_changed = new_text != (variant.script_text or "")
    variant.script_text = new_text
    if text_changed:
        # Atomically clear TTS audio so the next render regenerates it
        # with the rewritten script. Without this, the renderer would
        # bake the OLD voice over the NEW script.
        _mark_variant_audio_stale(variant, cast)
    await db.commit()
    if text_changed:
        _enqueue_tts_regen(cast_id, user.id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="block.variant_rewrite", entity_type="block",
            entity_id=block_id, cast_id=cast_id,
            after={"variant_id": variant.id, "audio_stale": text_changed},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"script_text": variant.script_text}



@router.post("/{cast_id}/refine-all-blocks")
async def refine_all_blocks(
    cast_id: str,
    instruction: str = Body(..., embed=True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Refine all active block scripts using Claude. Does NOT regenerate from scratch."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    from config import settings as app_settings
    if not app_settings.OPENROUTER_API_KEY:
        raise HTTPException(503, "AI rewrite requires API key configuration")

    result = await db.execute(
        select(Block).where(Block.cast_id == cast_id, Block.is_active == True).order_by(Block.position)
    )
    blocks = result.scalars().all()

    current_scripts = []
    for b in blocks:
        vr = await db.execute(
            select(Variant).where(Variant.block_id == b.id, Variant.is_active == True).limit(1)
        )
        v = vr.scalar_one_or_none()
        if v:
            current_scripts.append({"block_id": b.id, "variant_id": v.id, "text": v.script_text or ""})

    if not current_scripts:
        return {"updated": []}

    prompt = "Current script blocks:\n"
    for i, s in enumerate(current_scripts):
        prompt += f"Block {i+1}: {s['text']}\n\n"
    prompt += f"Instruction: {instruction}\nReturn the refined blocks in the same order, separated by ---BLOCK_BREAK---"

    try:
        from services.openrouter import get_openrouter_service
        llm = get_openrouter_service()
        result_text = await llm.generate_text(
            prompt=prompt,
            system_prompt="You are a script editor. Refine all blocks per the instruction. Keep the same number of blocks.",
            max_tokens=2000,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(502, "AI refinement failed")

    refined = result_text.strip().split("---BLOCK_BREAK---")
    updated = []
    any_changed = False
    for i, s in enumerate(current_scripts):
        new_text = refined[i].strip() if i < len(refined) else s["text"]
        v = await db.get(Variant, s["variant_id"])
        if v:
            if new_text != (v.script_text or ""):
                v.script_text = new_text
                # Atomic with the script change — the next render must
                # NOT bake the previous TTS audio over the refined text.
                _mark_variant_audio_stale(v, cast)
                any_changed = True
            updated.append({"block_id": s["block_id"], "text": new_text})

    await db.commit()
    if any_changed:
        _enqueue_tts_regen(cast_id, user.id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.refine_all_blocks", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"updated": len(updated)},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"updated": updated}


# ── Rewrite in Voice (Claude + voice corpus) ──

class RewriteInVoiceRequest(BaseModel):
    pass  # No additional params needed


@router.post("/{cast_id}/blocks/{block_id}/rewrite-in-voice")
async def rewrite_block_in_voice(
    cast_id: str,
    block_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite a block script using Claude + the avatar voice corpus transcripts."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id:
        raise HTTPException(404, "Block not found")

    # Get the block first variant script text
    variants = (await db.execute(
        select(Variant).where(Variant.block_id == block_id).order_by(Variant.created_at)
    )).scalars().all()
    if not variants or not variants[0].script_text:
        raise HTTPException(400, "Block has no script text to rewrite")

    original_text = variants[0].script_text

    # Load voice corpus transcripts
    from models.voice_corpus import VoiceCorpusEntry
    corpus_result = await db.execute(
        select(VoiceCorpusEntry)
        .where(VoiceCorpusEntry.avatar_id == cast.avatar_id)
        .where(VoiceCorpusEntry.status == "ready")
        .where(VoiceCorpusEntry.transcript.isnot(None))
        .order_by(func.random())
        .limit(10)
    )
    corpus_entries = corpus_result.scalars().all()

    if not corpus_entries:
        raise HTTPException(400, "No voice corpus available. Upload voice examples on the avatar profile first.")

    examples_text = "\n\n---\n\n".join(
        f"EXAMPLE {i+1}:\n{entry.transcript[:1500]}"
        for i, entry in enumerate(corpus_entries)
    )

    system_prompt = (
        "You are rewriting a TikTok Shop product script in the natural speaking style "
        "of a specific creator. Below are real examples of how this creator actually talks. "
        "Match their cadence, vocabulary, sentence length, energy, and verbal tics.\n\n"
        f"CREATOR'S SPEECH EXAMPLES:\n{examples_text}\n\n"
        "INSTRUCTIONS:\n"
        "- Match the creator's actual speech patterns shown above\n"
        "- Use contractions, informal phrasing, and any verbal tics they use\n"
        "- Avoid AI tells: 'furthermore', 'in addition', 'firstly/secondly', "
        "'in conclusion', 'it is important to note', 'I would be happy to'\n"
        "- Keep approximately the same length and the same product information\n"
        "- Make it sound spoken aloud, not written\n"
        "- Output ONLY the rewritten script, no preamble, no quotes, no explanation"
    )

    import anthropic
    from config import settings as app_settings

    if not app_settings.ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=app_settings.ANTHROPIC_API_KEY)

    # Per-block pinned product context — keep the rewrite anchored to the
    # actual product attached to this block instead of letting the model
    # paraphrase a generic mention away.
    product_context = ""
    if block.product_id:
        try:
            pinned = await db.get(Product, block.product_id)
            if pinned:
                desc = (pinned.description or "")[:300].strip()
                product_context = (
                    f"\n\nThis block features \"{pinned.name}\""
                    + (f" — {desc}" if desc else "")
                    + ". Keep the product reference natural and accurate."
                )
        except Exception as e:
            sentry_sdk.capture_exception(e)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            temperature=0.7,
            messages=[{
                "role": "user",
                "content": (
                    f"Rewrite this product script in my voice:\n\n{original_text}"
                    f"{product_context}"
                )
            }],
            system=system_prompt,
        )
        rewritten_text = response.content[0].text.strip()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        sentry_sdk.capture_exception(e)
        logger.exception("Claude rewrite failed: %s", e)
        raise HTTPException(500, f"Rewrite failed: {str(e)[:200]}")

    return {
        "original": original_text,
        "rewritten": rewritten_text,
        "corpus_entries_used": len(corpus_entries),
    }


# ── Per-block audio regeneration + variant management (Phase 2.1) ──


class RegenerateAudioRequest(BaseModel):
    script_text: str
    voice_settings: Optional[dict] = None


@router.post("/{cast_id}/blocks/{block_id}/regenerate-audio")
async def regenerate_block_audio(
    cast_id: str,
    block_id: str,
    req: RegenerateAudioRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate TTS audio for a single block. Creates a new active variant."""
    # Validate ownership
    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    block = next((b for b in cast.blocks if b.id == block_id and b.deleted_at is None), None)
    if not block:
        raise HTTPException(404, "Block not found")

    # Get avatar voice_id for TTS
    avatar = await db.get(Avatar, cast.avatar_id)
    if not avatar or not avatar.voice_id:
        raise HTTPException(400, "Avatar has no voice configured")

    # Mark current active variant(s) for this block as inactive
    active_variants = (await db.execute(
        select(Variant).where(
            Variant.block_id == block_id,
            Variant.is_active == True,
        )
    )).scalars().all()
    for v in active_variants:
        v.is_active = False

    # Create new variant
    new_variant = Variant(
        id=f"var_{uuid.uuid4().hex[:12]}",
        block_id=block_id,
        script_text=req.script_text,
        variant_label="A",
        status=VariantStatus.PENDING,
        is_active=True,
    )
    db.add(new_variant)
    await db.flush()

    # Call TTS pipeline
    try:
        from services.fish_audio import get_fish_audio_service
        fish = get_fish_audio_service()
        tts_result = await fish.generate_tts(
            text=req.script_text,
            voice_id=avatar.voice_id,
        )

        new_variant.audio_key = tts_result["audio_key"]
        new_variant.tts_r2_key = tts_result["audio_key"]
        new_variant.tts_duration_seconds = tts_result["duration_seconds"]
        new_variant.duration_seconds = tts_result["duration_seconds"]
        new_variant.status = VariantStatus.READY

        await db.commit()

        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        audio_url = r2.get_public_url(new_variant.audio_key)

        # Phase 2.5 — Cascade regeneration to sibling casts
        sibling_cascade_results = await _cascade_audio_to_siblings(
            db, cast, block, req.script_text, avatar.voice_id, tts_result,
        )

        return {
            "variant_id": new_variant.id,
            "audio_url": audio_url,
            "duration_seconds": new_variant.tts_duration_seconds,
            "sibling_cascades": sibling_cascade_results,
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        # Rollback: restore previous active variant
        new_variant.status = VariantStatus.FAILED
        new_variant.generation_error = str(e)[:500]
        new_variant.is_active = False
        for v in active_variants:
            v.is_active = True
        await db.commit()
        raise HTTPException(500, f"Audio regeneration failed: {str(e)[:200]}")


async def _cascade_audio_to_siblings(
    db: AsyncSession,
    source_cast: Cast,
    source_block: Block,
    script_text: str,
    voice_id: str,
    tts_result: dict,
) -> list:
    """Phase 2.5 — Cascade audio regeneration to sibling casts.

    After a block's audio is regenerated in one cast, find sibling casts
    (same parent_cast_id family), find the matching block by position,
    and replicate the audio there. Skips misaligned blocks (Phase 2.5.4).
    """
    import logging
    logger = logging.getLogger(__name__)
    results = []

    parent_id = getattr(source_cast, "parent_cast_id", None)
    if not parent_id:
        return results

    try:
        from sqlalchemy import or_
        sibling_rows = (await db.execute(
            select(Cast)
            .options(selectinload(Cast.blocks).selectinload(Block.variants))
            .where(
                Cast.user_id == source_cast.user_id,
                or_(
                    Cast.parent_cast_id == parent_id,
                    Cast.id == parent_id,
                ),
                Cast.id != source_cast.id,
                Cast.deleted_at.is_(None),
            )
        )).scalars().all()

        for sibling in sibling_rows:
            # Find matching block by position (Phase 2.5.4)
            sibling_blocks = sorted(
                [b for b in sibling.blocks if b.deleted_at is None],
                key=lambda b: b.position or 0,
            )
            matching_block = next(
                (b for b in sibling_blocks if b.position == source_block.position),
                None,
            )
            if not matching_block:
                # Phase 2.5.4 — block positions misaligned, skip
                logger.info(
                    "Sibling cascade skipped: cast %s has no block at position %d (misaligned)",
                    sibling.id, source_block.position,
                )
                results.append({
                    "sibling_cast_id": sibling.id,
                    "status": "skipped",
                    "reason": "misaligned_block_position",
                })
                continue

            try:
                # Deactivate current active variants on the matching block
                for v in (matching_block.variants or []):
                    if v.is_active:
                        v.is_active = False

                # Create new variant with the cascaded audio
                cascade_var = Variant(
                    id=f"var_{uuid.uuid4().hex[:12]}",
                    block_id=matching_block.id,
                    script_text=script_text,
                    variant_label="A",
                    status=VariantStatus.READY,
                    is_active=True,
                    audio_key=tts_result["audio_key"],
                    tts_r2_key=tts_result["audio_key"],
                    tts_duration_seconds=tts_result["duration_seconds"],
                    duration_seconds=tts_result["duration_seconds"],
                )
                db.add(cascade_var)

                # Mark sibling cast as having stale audio (Phase 2.5.2)
                from datetime import datetime, timezone
                sibling.audio_stale_since = datetime.now(timezone.utc)

                results.append({
                    "sibling_cast_id": sibling.id,
                    "sibling_block_id": matching_block.id,
                    "status": "cascaded",
                    "variant_id": cascade_var.id,
                })
            except Exception as cascade_err:
                sentry_sdk.capture_exception(cascade_err)
                logger.warning(
                    "Sibling cascade failed for cast %s block %s: %s",
                    sibling.id, matching_block.id, cascade_err,
                )
                results.append({
                    "sibling_cast_id": sibling.id,
                    "status": "failed",
                    "error": str(cascade_err)[:200],
                })

        if results:
            await db.commit()

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Sibling cascade lookup failed: %s", e)
        results.append({"status": "cascade_error", "error": str(e)[:200]})

    return results


# ── Per-block render (Phase 4.8.3) ──


class RenderBlockRequest(BaseModel):
    render_action: str = "full"  # full | re_gesture | swap_angle
    avatar_angle: Optional[str] = None


@router.post("/{cast_id}/blocks/{block_id}/render")
async def render_block(
    cast_id: str,
    block_id: str,
    req: RenderBlockRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-render a single block's video (InfiniteTalk). Returns task_id for polling."""
    try:
        result = await db.execute(
            select(Cast)
            .options(selectinload(Cast.blocks))
            .where(Cast.id == cast_id, Cast.user_id == user.id)
        )
        cast = result.scalar_one_or_none()
        if not cast:
            raise HTTPException(404, "Cast not found")

        block = next((b for b in cast.blocks if b.id == block_id and b.deleted_at is None), None)
        if not block:
            raise HTTPException(404, "Block not found")

        # Cost estimation for the block
        quality = cast.quality or "simple"
        quality_mult = {"simple": 1.0, "hd": 2.0, "hd_plus": 3.5}.get(quality, 1.0)
        active_variant = next(
            (v for v in (block.variants or []) if getattr(v, "is_active", False)),
            None,
        )
        duration_s = getattr(active_variant, "tts_duration_seconds", 10) or 10
        cost_cents = int(duration_s * 0.25 * quality_mult)

        if req.avatar_angle:
            block.avatar_angle = req.avatar_angle
            await db.commit()

        from tasks.generate_cast import generate_cast_videos_task
        task = generate_cast_videos_task.delay(cast_id, user.id)

        try:
            await audit_log.record(
                db, user_id=user.id, action="render.start", entity_type="block",
                entity_id=block_id, cast_id=cast_id,
                after={"task_id": task.id, "cost_cents": cost_cents, "quality": quality, "render_action": req.render_action},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

        return {
            "task_id": task.id,
            "block_id": block_id,
            "cost_cents": cost_cents,
            "quality": quality,
        }
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Block render failed: {str(e)[:200]}")


@router.get("/{cast_id}/blocks/{block_id}/variants")
async def list_block_variants(
    cast_id: str,
    block_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all variants for a block (for variant picker UI)."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id or block.deleted_at is not None:
        raise HTTPException(404, "Block not found")

    variants = (await db.execute(
        select(Variant)
        .where(Variant.block_id == block_id)
        .order_by(Variant.created_at.desc().nullslast(), Variant.id.desc())
    )).scalars().all()

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    return [
        {
            "variant_id": v.id,
            "script_text": v.script_text,
            "audio_url": r2.get_public_url(v.audio_key) if v.audio_key else None,
            "duration_seconds": v.tts_duration_seconds or v.duration_seconds,
            "is_active": v.is_active,
            "status": v.status.value if v.status else "pending",
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in variants
    ]


class SelectVariantRequest(BaseModel):
    variant_id: str


@router.patch("/{cast_id}/blocks/{block_id}/select-variant")
async def select_block_variant(
    cast_id: str,
    block_id: str,
    req: SelectVariantRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Select a variant as active for a block. Deactivates all others."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    block = await db.get(Block, block_id)
    if not block or block.cast_id != cast_id or block.deleted_at is not None:
        raise HTTPException(404, "Block not found")

    # Verify the target variant belongs to this block
    target = await db.get(Variant, req.variant_id)
    if not target or target.block_id != block_id:
        raise HTTPException(404, "Variant not found in this block")

    # Deactivate all variants for this block
    all_variants = (await db.execute(
        select(Variant).where(Variant.block_id == block_id)
    )).scalars().all()
    for v in all_variants:
        v.is_active = (v.id == req.variant_id)

    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="block.variant_select", entity_type="block",
            entity_id=block_id, cast_id=cast_id,
            after={"variant_id": req.variant_id},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    return {
        "variant_id": target.id,
        "audio_url": r2.get_public_url(target.audio_key) if target.audio_key else None,
        "duration_seconds": target.tts_duration_seconds or target.duration_seconds,
    }


# ── Script Generation (uses voice profile) ──

@router.post("/{cast_id}/generate-outline", response_model=OutlineResponse)
async def generate_outline(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

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
        user_id=user.id,
        # Stage-1 creative template (null = Auto). Constrains the outline to
        # the template's block sequence + bias ratios when set.
        template=get_template(getattr(cast, 'template_id', None)),
        live_assessment=live_assessment,
        live_mode_defaults=getattr(cast, 'live_mode_defaults', None),
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


# ── Smart Cast — LLM designs the whole video (categories, hook types, stock
#    media). The outline is generated, Pexels is queried for any blocks that
#    need stock media, and Block rows are created with category/background/
#    transition/energy populated so the editor can render the right visuals
#    immediately. Replaces the manual flow of "generate avatar_speaking blocks,
#    user manually swaps types".
class SmartOutlineResponse(BaseModel):
    cast_id: str
    blocks: list[dict]
    estimated_total_duration_seconds: int
    estimated_cost_usd: float


# Per-block GPU cost model (matches Smart Cast doc § cost model).
# Cost is in USD; multiplied by quality_tier_multiplier on the SetupPhase UI.
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


@router.post("/{cast_id}/generate-smart-outline", response_model=SmartOutlineResponse)
async def generate_smart_outline_endpoint(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Smart Cast outline: the LLM designs categories, hook type, stock media
    queries, and transitions. Pexels is queried for any block that needs stock
    media. Existing blocks (if any) are wiped and replaced.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    if not cast.description:
        raise HTTPException(400, "Cast must have a description (the brief) before Smart Cast can generate.")

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
        user_id=user.id,
        # Stage-1 creative template (null = Auto). Constrains the smart outline
        # to the template's block sequence + bias ratios when set.
        template=get_template(getattr(cast, "template_id", None)),
        product_video_assets=product_video_assets,
        live_assessment=live_assessment,
        live_mode_defaults=getattr(cast, "live_mode_defaults", None),
    )
    if not outline:
        raise HTTPException(502, "Smart outline generation failed — try again.")

    # 2. Auto-populate Pexels stock media for blocks that asked for it.
    #    Passing products makes the search product-relevant (product name +
    #    feature words) instead of brand/topic, and enables multi-angle pairs.
    #    PR #162 — when the user picked uploaded videos (user_video_ids), resolve
    #    them to R2 URLs and prefer them as b-roll over Pexels.
    preferred_broll_urls = await _resolve_user_video_urls(
        db, getattr(cast, "user_video_ids", None), user.id,
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
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

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
        user_id=user.id,
    )

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
            user_id=user.id,
        )
        cast.suggested_clips = clips
        await db.commit()
    except Exception as _clip_exc:
        sentry_sdk.capture_exception(_clip_exc)
        logger.warning("suggest_clips wiring failed for cast %s: %s", cast_id, _clip_exc)

    return {"cast_id": cast_id, "scripts_generated": len(scripts), "voice_profile_used": voice_profile is not None}


# ── Payment + Generation ──

@router.post("/{cast_id}/pay")
async def pay_for_cast(
    cast_id: str,
    req: CastPayRequest,
    user: User = Depends(get_current_user),
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


# Old monolithic /generate endpoint removed — use /generate-tts + /generate-videos flow


@router.get("/{cast_id}/generation-status", response_model=GenerationStatusResponse)
async def get_generation_status(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


def _build_script_system_prompt(voice_profile: dict | None) -> str:
    """Build system prompt for script generation with voice profile."""
    from services.ai_prompts import get_prompt
    base_prompt = get_prompt("script_rewriter")
    system = base_prompt["system"].split("\n")[0] + "\n"

    if voice_profile and voice_profile.get("tone") and voice_profile["tone"] != "unknown":
        system += f"""
Speaking style: {voice_profile.get('tone', 'enthusiastic')}.
Average sentence: {voice_profile.get('avg_sentence_length', 10)} words.
Common phrases: {', '.join(voice_profile.get('common_phrases', [])[:15])}.
Sentence starters: {', '.join(voice_profile.get('sentence_starters', [])[:8])}.
{int(voice_profile.get('question_frequency', 0) * 100)}% of sentences are questions.
Energy: {voice_profile.get('avg_energy', 'medium')}.
Sign-offs: {', '.join(voice_profile.get('sign_offs', [])[:5])}.

Examples of how they actually talk:
{chr(10).join('- "' + p + '"' for p in voice_profile.get('sample_phrases', [])[:8])}

MATCH THIS STYLE EXACTLY. Use their phrases naturally. Sound like THEM.
"""
    else:
        system += "Use general enthusiastic TikTok live-selling style.\n"

    system += (
        "Rewrite the following script based on the user's instruction. "
        "Keep it conversational, enthusiastic, and under 300 characters. "
        "Return ONLY the rewritten script text, nothing else."
    )
    return system


# ── Block reorder ──
class ReorderBlocksRequest(BaseModel):
    block_ids: List[str]

@router.patch("/{cast_id}/blocks/reorder")
async def reorder_blocks(
    cast_id: str,
    req: ReorderBlocksRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reorder blocks by providing their IDs in desired order."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Block active toggle ──
class ToggleActiveRequest(BaseModel):
    is_active: bool

@router.patch("/{cast_id}/blocks/{block_id}/active")
async def toggle_block_active(
    cast_id: str,
    block_id: str,
    req: ToggleActiveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Toggle whether a block is included in cast generation + streaming."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
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


# ── Two-phase generation endpoints ──

@router.post("/{cast_id}/generate-tts")
async def start_tts_generation(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Phase 1: Generate TTS audio for all blocks. Sets status to TTS_READY when done."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    if cast.status in (CastStatus.LIVE, CastStatus.GENERATING_VIDEOS):
        raise HTTPException(400, f"Cannot generate TTS while cast is live or generating videos")

    # Validate avatar has a voice before dispatching
    if cast.avatar_id:
        avatar = await db.get(Avatar, cast.avatar_id)
        if not avatar or not avatar.voice_id:
            raise HTTPException(400, "Avatar has no voice configured. Please set up a voice first.")

    # Validate blocks exist with script text (exclude soft-deleted)
    result = await db.execute(
        select(Block).where(Block.cast_id == cast_id, Block.deleted_at.is_(None)).options(selectinload(Block.variants))
    )
    blocks = result.scalars().all()
    active_blocks = [b for b in blocks if getattr(b, 'is_active', True)]
    if not active_blocks:
        raise HTTPException(400, "No script blocks found. Generate a script first.")

    # Check if any variant has script_text
    has_script = any(
        v.script_text and v.script_text.strip()
        for b in active_blocks
        for v in (b.variants or [])
    )
    # If blocks have no variants at all, the TTS task will create fallback variants
    # from key_points. But if variants exist with empty script_text, warn the user.
    has_variants = any(b.variants for b in active_blocks)
    if has_variants and not has_script:
        raise HTTPException(400, "All script blocks have empty text. Please write or generate scripts first.")

    # Set status BEFORE dispatching so the frontend sees the transition immediately
    cast.status = CastStatus.GENERATING_TTS
    cast.audio_stale_since = None  # Clear stale flag since we're regenerating
    await db.commit()

    from tasks.generate_cast import generate_cast_tts_task
    generate_cast_tts_task.delay(cast_id, user.id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="render.tts_start", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"cast_id": cast_id, "status": "generating_tts"}


@router.post("/{cast_id}/generate-videos")
async def start_video_generation(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Phase 2: Submit InfiniteTalk jobs. Requires TTS_READY status."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    if cast.status != CastStatus.TTS_READY:
        raise HTTPException(400, f"Cast must be in TTS_READY status. Current: '{cast.status.value}'")

    from tasks.generate_cast import generate_cast_videos_task
    generate_cast_videos_task.delay(cast_id, user.id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="render.videos_start", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"cast_id": cast_id, "status": "generating_videos"}


@router.get("/{cast_id}/tts-status")
async def tts_status(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return per-variant TTS state for the waveform editor."""
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks).selectinload(Block.variants))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    variants_data = []
    for block in cast.blocks:
        if block.deleted_at is not None:
            continue
        for v in block.variants:
            tts_url = ""
            if v.audio_key:
                tts_url = r2.get_public_url(v.audio_key)
            variants_data.append({
                "variant_id": v.id,
                "block_id": block.id,
                "status": v.status.value if v.status else "pending",
                "tts_url": tts_url,
                "duration_seconds": v.tts_duration_seconds or v.duration_seconds or 0,
            })

    return {
        "cast_id": cast_id,
        "cast_status": cast.status.value,
        "variants": variants_data,
    }


@router.post("/{cast_id}/generate-captions")
async def generate_captions(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """For each block variant with ready TTS audio, call Whisper on the GPU
    server with word-level timestamps. Store the word timings on the variant
    for caption track rendering."""
    from sqlalchemy.orm import selectinload
    from services.gpu_server import get_gpu_server_client
    from services.r2_storage import get_r2_storage_service

    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks).selectinload(Block.variants))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    r2 = get_r2_storage_service()
    gpu = get_gpu_server_client()
    if not gpu:
        raise HTTPException(503, "GPU server not configured")

    # Collect every variant that has TTS audio, then run Whisper concurrently
    # for all of them. The previous serial loop meant a slow GPU response on
    # block 1 would burn the full request timeout before block 2 was even
    # dispatched — manifesting as "captions only show up for the first block".
    pending: list[tuple[object, object, str]] = []  # (block, variant, audio_url)
    for block in sorted(cast.blocks, key=lambda b: b.position or 0):
        if block.deleted_at is not None:
            continue
        for variant in (block.variants or []):
            if not variant.audio_key:
                continue
            pending.append((block, variant, r2.get_public_url(variant.audio_key)))

    async def _transcribe(audio_url: str):
        return await gpu.whisper_transcribe(
            audio_url=audio_url,
            language="en",
            word_timestamps=True,
        )

    results = []
    if pending:
        gathered = await asyncio.gather(
            *(_transcribe(audio_url) for _, _, audio_url in pending),
            return_exceptions=True,
        )
        for (block, variant, _), outcome in zip(pending, gathered):
            if isinstance(outcome, Exception):
                sentry_sdk.capture_exception(outcome)
                logger.warning("Caption alignment failed for variant %s: %s", variant.id, outcome)
                if variant.script_text and variant.tts_duration_seconds:
                    from utils.script_cleaning import clean_script_tokens, strip_script_markers
                    # Strip [sfx:*]/(emotion) direction markers before
                    # tokenizing, or they leak into rendered captions.
                    words = clean_script_tokens(variant.script_text)
                    clean_text = strip_script_markers(variant.script_text)
                    duration = variant.tts_duration_seconds
                    time_per_word = duration / max(len(words), 1)
                    fallback_words = [
                        {
                            "word": w,
                            "start": round(i * time_per_word, 3),
                            "end": round((i + 1) * time_per_word, 3),
                            "probability": 0.5,
                        }
                        for i, w in enumerate(words)
                    ]
                    variant.caption_words = fallback_words
                    variant.caption_segments = [{
                        "start": 0,
                        "end": duration,
                        "text": clean_text,
                    }]
                    results.append({
                        "block_id": block.id,
                        "variant_id": variant.id,
                        "word_count": len(fallback_words),
                        "fallback": True,
                    })
                else:
                    results.append({
                        "block_id": block.id,
                        "variant_id": variant.id,
                        "error": str(outcome)[:200],
                    })
            else:
                variant.caption_words = outcome.get("words", [])
                variant.caption_segments = outcome.get("segments", [])
                results.append({
                    "block_id": block.id,
                    "variant_id": variant.id,
                    "word_count": len(variant.caption_words or []),
                })

    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="render.captions_generate", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"variants_processed": len(results)},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"results": results}


class EditorCaptionRequest(BaseModel):
    """Request body for editor caption generation.
    Accepts one or more audio URLs (for multi-block caption generation).
    Each entry includes the audio URL, its block_id, and the audio element id
    on the timeline so the frontend can link captions to their source.
    """
    audio_segments: List[dict]
    # Each: { "audio_url": str, "block_id": str, "audio_element_id": str, "start_offset_s": float }


@router.post("/{cast_id}/editor-generate-captions")
async def editor_generate_captions(
    cast_id: str,
    body: EditorCaptionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Phase 2.6.2 — Generate word-level captions for the editor.

    Accepts audio_url(s) from timeline audio elements, transcribes via
    GPU Whisper with word-level timestamps, and returns caption tokens
    remapped to each block's timeline offset.

    Returns Remotion-compatible caption tokens with per-block metadata.
    """
    from services.gpu_server import get_gpu_server_client

    # Validate cast ownership
    result = await db.execute(
        select(Cast).where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    gpu = get_gpu_server_client()
    if not gpu:
        raise HTTPException(503, "GPU server not configured — Whisper transcription unavailable")

    # Dispatch every segment concurrently — when the editor asks for captions
    # across N blocks at once, a serial loop makes block N wait for blocks
    # 1..N-1 to round-trip through the GPU server. That was the practical
    # cause of "captions only appeared on block 1": for an 8-block cast the
    # request would hit its timeout long before later segments returned.
    pending_segs: list[dict] = []
    for seg in body.audio_segments:
        if not seg.get("audio_url"):
            continue
        pending_segs.append(seg)

    async def _transcribe_seg(audio_url: str):
        return await gpu.whisper_transcribe(
            audio_url=audio_url,
            language="en",
            word_timestamps=True,
        )

    caption_results = []
    if pending_segs:
        gathered = await asyncio.gather(
            *(_transcribe_seg(seg["audio_url"]) for seg in pending_segs),
            return_exceptions=True,
        )
        for seg, outcome in zip(pending_segs, gathered):
            block_id = seg.get("block_id", "")
            audio_element_id = seg.get("audio_element_id", "")
            start_offset_s = float(seg.get("start_offset_s", 0))

            if isinstance(outcome, Exception):
                sentry_sdk.capture_exception(outcome)
                caption_results.append({
                    "block_id": block_id,
                    "audio_element_id": audio_element_id,
                    "captions": [],
                    "words": [],
                    "word_count": 0,
                    "error": str(outcome)[:200],
                })
                continue

            words = outcome.get("words", [])
            remapped_words = [
                {
                    "word": w.get("word", "").strip(),
                    "start": round(w.get("start", 0) + start_offset_s, 3),
                    "end": round(w.get("end", 0) + start_offset_s, 3),
                    "probability": w.get("probability", 0),
                    "block_id": block_id,
                    "source_audio_id": audio_element_id,
                }
                for w in words
            ]
            captions = [
                {
                    "text": w["word"],
                    "startMs": round(w["start"] * 1000),
                    "endMs": round(w["end"] * 1000),
                    "timestampMs": round(w["start"] * 1000),
                    "confidence": w["probability"],
                }
                for w in remapped_words
            ]
            caption_results.append({
                "block_id": block_id,
                "audio_element_id": audio_element_id,
                "captions": captions,
                "words": remapped_words,
                "word_count": len(words),
            })

    return {"results": caption_results}


@router.get("/{cast_id}/clips/{variant_id}")
async def stream_clip(
    cast_id: str,
    variant_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Return a presigned R2 URL for a clip, bypassing CDN cache."""
    from sqlalchemy import select as sa_select
    from models.variant import Variant
    from models.block import Block
    from services.r2_storage import get_r2_storage_service
    from fastapi.responses import RedirectResponse
    from functools import partial

    stmt = sa_select(Variant).join(Block, Variant.block_id == Block.id).where(
        Variant.id == variant_id,
        Block.cast_id == cast_id,
    )
    result = await db.execute(stmt)
    variant = result.scalar_one_or_none()
    if not variant or not variant.video_key:
        raise HTTPException(404, "Clip not found")

    r2 = get_r2_storage_service()
    loop = asyncio.get_running_loop()
    presigned = await loop.run_in_executor(None, partial(
        r2.client.generate_presigned_url,
        "get_object",
        Params={"Bucket": r2.bucket, "Key": variant.video_key},
        ExpiresIn=3600,
    ))
    return RedirectResponse(url=presigned, status_code=302)



# ── Twick Timeline Save/Get ──

class SaveTimelineRequest(BaseModel):
    variant_id: str
    twick_data: dict
    block_regions: list = []
    editor_state: dict | None = None  # Native Editor Starter UndoableState for restore


@router.put("/{cast_id}/timeline")
async def save_cast_timeline(
    cast_id: str,
    payload: SaveTimelineRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    from datetime import datetime
    current = cast.timeline_json or {}
    current[payload.variant_id] = {
        "twick_data": payload.twick_data,
        "block_regions": payload.block_regions,
        "editor_state": payload.editor_state,
        "saved_at": datetime.utcnow().isoformat(),
    }
    cast.timeline_json = current
    cast.updated_at = datetime.utcnow()  # Explicit update so stale render detection works
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(cast, "timeline_json")
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.timeline_save", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"variant_id": payload.variant_id},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"ok": True, "variant_id": payload.variant_id}


@router.get("/{cast_id}/timeline/{variant_id}")
async def get_cast_timeline(
    cast_id: str,
    variant_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    timeline = (cast.timeline_json or {}).get(variant_id)
    return timeline or {"twick_data": None, "block_regions": [], "editor_state": None, "saved_at": None}


def build_stock_overlay_element(
    block,
    start_s: float,
    end_s: float,
    canvas_w: int,
    canvas_h: int,
) -> Optional[dict]:
    """Build a non-bonded visual stock-media overlay element for ``block``.

    The bonded avatar covers only the face region, so any non-face area of the
    frame composites to black unless a visual overlay fills it. Two cases emit
    an overlay; everything else returns ``None`` (no overlay):

      * ``category == 'stock_video'`` — the whole frame is the stock clip (no
        avatar bake), so the overlay is FULLSCREEN.
      * resolved ``pip_layout == 'split_h'`` — the stock clip occupies the
        non-face half (the ``content_rect``) while the avatar speaks on the
        face half.

    Coordinates are in render-canvas pixels (``canvas_w`` x ``canvas_h``); the
    caller stamps the matching ``compositionWidth/Height`` on the timeline so
    ``extract_overlay_elements`` scales them 1:1.

    Returns ``None`` (and never raises) when the block has no stock media, isn't
    one of the handled layouts, or geometry can't be computed — a single bad
    block must never fail the arrange call.
    """
    try:
        stock_url = (getattr(block, "stock_media_url", None) or "").strip()
        if not stock_url:
            return None

        from layouts.primitives import LayoutPrimitive, coerce_to_primitive, compute_geometry

        category = (getattr(block, "category", None) or "").strip().lower()
        stock_kind = (getattr(block, "stock_media_kind", None) or "").strip().lower()
        el_type = "image" if stock_kind in ("image", "photo") else "video"

        if category == "stock_video":
            return {
                "id": f"stock_{block.id}",
                "type": el_type,
                "s": start_s,
                "e": end_s,
                "props": {
                    "src": stock_url,
                    "x": 0,
                    "y": 0,
                    "width": canvas_w,
                    "height": canvas_h,
                },
                "metadata": {"block_id": block.id, "kind": "stock_fullscreen"},
            }

        pip_layout = _get_pip_layout(block)
        if pip_layout and coerce_to_primitive(pip_layout) == LayoutPrimitive.SPLIT_H.value:
            geo = compute_geometry(LayoutPrimitive.SPLIT_H.value, canvas_w, canvas_h)
            content = geo.get("content_rect")
            if content is None:
                return None
            return {
                "id": f"stock_{block.id}",
                "type": el_type,
                "s": start_s,
                "e": end_s,
                "props": {
                    "src": stock_url,
                    "x": content.x,
                    "y": content.y,
                    "width": content.w,
                    "height": content.h,
                },
                "metadata": {"block_id": block.id, "kind": "stock_content_half"},
            }
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return None


@router.post("/{cast_id}/auto-arrange-timeline")
async def auto_arrange_cast_timeline(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Server-side equivalent of the frontend's castToEditorStarterTimeline.

    Builds a minimal but valid bonded V1/A1 timeline from the cast's blocks
    and variants and stores it under timeline_json["default"], so an
    API-driven (headless) caller can reach /finalize without opening the
    visual editor. Each active block contributes one bonded video element +
    one bonded audio element sharing the same metadata.block_id, plus an
    optional captions element built from the variant's word timestamps.
    """
    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks).selectinload(Block.variants))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    DEFAULT_BLOCK_DURATION_S = 3.0

    blocks = [
        b for b in (cast.blocks or [])
        if getattr(b, "is_active", True) and getattr(b, "deleted_at", None) is None
    ]
    blocks.sort(key=lambda b: (b.position if b.position is not None else 0))

    video_elements: list[dict] = []
    audio_elements: list[dict] = []
    caption_elements: list[dict] = []
    sfx_elements: list[dict] = []
    stock_elements: list[dict] = []
    block_regions: list[dict] = []

    # Visual overlays are emitted at native render-canvas size so
    # extract_overlay_elements (which scales editor-canvas coords down to the
    # render resolution) passes them through 1:1 — see CANVAS_W/H + the
    # compositionWidth/Height stamped on twick_data below.
    CANVAS_W = 480
    CANVAS_H = 848

    cursor = 0.0
    blocks_arranged = 0

    for block in blocks:
        variants = list(block.variants or [])
        if not variants:
            continue

        # Pick the first variant that has a rendered video; otherwise the
        # first variant — matches the frontend's "active variant" choice.
        active_variant = next(
            (v for v in variants if getattr(v, "video_key", None)),
            variants[0],
        )

        try:
            duration = float(active_variant.tts_duration_seconds or 0)
        except (TypeError, ValueError) as e:
            sentry_sdk.capture_exception(e)
            duration = 0.0
        if duration <= 0:
            duration = DEFAULT_BLOCK_DURATION_S

        start_s = cursor
        end_s = cursor + duration

        video_element_id = f"v1_{block.id}"
        audio_element_id = f"a1_{block.id}"

        video_src = ""
        if getattr(active_variant, "video_key", None):
            try:
                video_src = r2.get_public_url(active_variant.video_key) or ""
            except Exception as e:
                sentry_sdk.capture_exception(e)
                video_src = ""

        audio_src = ""
        if getattr(active_variant, "audio_key", None):
            try:
                audio_src = r2.get_public_url(active_variant.audio_key) or ""
            except Exception as e:
                sentry_sdk.capture_exception(e)
                audio_src = ""

        video_elements.append({
            "id": video_element_id,
            "type": "video",
            "s": start_s,
            "e": end_s,
            "props": {"src": video_src},
            "metadata": {
                "block_id": block.id,
                "bonded": True,
                "paired_audio_element_id": audio_element_id,
                # Renderer reads this to keep mic-on VO clean and apply the
                # phone-mic lo-fi filter to non-mic-on VO.
                "mic_on": getattr(block, "mic_on", None) is True,
            },
        })

        audio_elements.append({
            "id": audio_element_id,
            "type": "audio",
            "s": start_s,
            "e": end_s,
            "props": {"src": audio_src},
            "metadata": {
                "block_id": block.id,
                "bonded": True,
                "paired_video_element_id": video_element_id,
                "mic_on": getattr(block, "mic_on", None) is True,
            },
        })

        # Captions — one element per block, built from word timestamps.
        # caption_words shape: [{word, start, end, probability|score}, ...].
        caption_words = getattr(active_variant, "caption_words", None)
        if caption_words:
            from utils.script_cleaning import strip_script_markers
            tokens: list[dict] = []
            try:
                for w in caption_words:
                    if not isinstance(w, dict):
                        continue
                    word_text = w.get("word")
                    if word_text is None:
                        continue
                    # Defensively strip direction markers — a word may carry a
                    # [sfx:*]/(emotion) marker if it came from a fallback path.
                    word_text = strip_script_markers(str(word_text))
                    if not word_text:
                        continue
                    word_start = float(w.get("start", 0) or 0)
                    word_end = float(w.get("end", word_start) or word_start)
                    start_ms = round((word_start + start_s) * 1000)
                    end_ms = max(round((word_end + start_s) * 1000), start_ms + 1)
                    confidence = w.get("probability", w.get("score"))
                    tokens.append({
                        "text": word_text,
                        "startMs": start_ms,
                        "endMs": end_ms,
                        "timestampMs": round((start_ms + end_ms) / 2),
                        "confidence": confidence,
                    })
            except (TypeError, ValueError) as e:
                sentry_sdk.capture_exception(e)
                tokens = []
            if tokens:
                caption_elements.append({
                    "id": f"cap_{block.id}",
                    "type": "captions",
                    "s": start_s,
                    "e": end_s,
                    "props": {
                        "text": " ".join(t["text"] for t in tokens),
                        "_captions_tokens": tokens,
                    },
                    "metadata": {
                        "block_id": block.id,
                        "track_type": "captions",
                    },
                })

        # SFX — resolved [sfx:NAME] markers become full-volume audio accents on
        # their own track. Each timing is clip-relative; shift by the block's
        # start. Unknown names are logged + skipped, never fatal.
        sfx_timings = getattr(active_variant, "sfx_timings", None)
        if sfx_timings:
            from services.sfx_library import lookup as sfx_lookup
            for ti, timing in enumerate(sfx_timings):
                if not isinstance(timing, dict):
                    continue
                name = timing.get("name")
                entry = sfx_lookup(name)
                if entry is None:
                    msg = f"Unknown SFX '{name}' in cast {cast_id} block {block.id}; skipping"
                    logger.warning(msg)
                    try:
                        sentry_sdk.capture_message(msg, level="warning")
                    except Exception as e:
                        sentry_sdk.capture_exception(e)
                    continue
                try:
                    rel_start = float(timing.get("start_s", 0) or 0)
                except (TypeError, ValueError) as e:
                    sentry_sdk.capture_exception(e)
                    rel_start = 0.0
                sfx_start = start_s + max(rel_start, 0.0)
                sfx_elements.append({
                    "id": f"sfx_{block.id}_{ti}",
                    "type": "audio",
                    "s": sfx_start,
                    "e": sfx_start + entry.duration_s,
                    "props": {"src": entry.url},
                    "metadata": {"kind": "sfx", "name": entry.name, "volume": entry.default_volume},
                })

        # Visual stock-media overlay. The bonded avatar fills only the face
        # region (fullscreen, or the face half of a split); without a visual
        # overlay any non-face area composites to black. A single bad block
        # must never fail the whole arrange call.
        stock_el = build_stock_overlay_element(block, start_s, end_s, CANVAS_W, CANVAS_H)
        if stock_el is not None:
            stock_elements.append(stock_el)

        block_regions.append({
            "block_id": block.id,
            "block_position": block.position if block.position is not None else 0,
            "variant_id": active_variant.id,
            "start_s": start_s,
            "end_s": end_s,
        })

        cursor = end_s
        blocks_arranged += 1

    tracks_out: list[dict] = [
        {"id": "video", "type": "video", "elements": video_elements},
        {"id": "voice", "type": "audio", "elements": audio_elements},
    ]

    # Visual stock-media overlays on their own non-bonded video track so
    # extract_overlay_elements surfaces them at render time. Emitted only when
    # at least one block contributed a stock clip/image.
    if stock_elements:
        tracks_out.append({
            "id": "stock",
            "type": "video",
            "elements": stock_elements,
        })

    # Auto-place background music as an unbonded audio element on its own
    # track. The renderer's music collector (translate_timeline_to_overlays /
    # cast_ffmpeg_composer) treats any non-bonded audio track that isn't the
    # voice track as music, mixes it under the narration, and ducks it. Without
    # this element nothing reaches the mixer even when the cast has a track.
    # Skipped when the user turned music off.
    music_choice = getattr(cast, "music_track_choice", "auto") or "auto"
    if cast.background_music_url and music_choice != "off" and cursor > 0:
        tracks_out.append({
            "id": "music",
            "type": "audio",
            "elements": [{
                "id": "music_bg",
                "type": "audio",
                "s": 0,
                "e": cursor,
                "props": {"src": cast.background_music_url},
                "metadata": {"kind": "music", "source": "auto"},
            }],
        })

    # SFX — one extra unbonded audio track of short full-volume accents. The
    # composer mixes these without sidechain ducking (unlike music). Emitted
    # only when at least one marker resolved.
    if sfx_elements:
        tracks_out.append({
            "id": "sfx",
            "type": "audio",
            "elements": sfx_elements,
        })

    tracks_out.append({"id": "captions", "type": "captions", "elements": caption_elements})

    twick_data = {
        "tracks": tracks_out,
        "version": 1,
        # Stamp the native render-canvas size so extract_overlay_elements scales
        # 1:1 (sx=sy=1.0) — the stock overlay coords above are already in
        # render-canvas pixels (CANVAS_W x CANVAS_H).
        "compositionWidth": CANVAS_W,
        "compositionHeight": CANVAS_H,
    }

    current = cast.timeline_json or {}
    current["default"] = {
        "twick_data": twick_data,
        "block_regions": block_regions,
        "editor_state": None,
        "saved_at": datetime.utcnow().isoformat(),
    }
    cast.timeline_json = current
    cast.updated_at = datetime.utcnow()
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(cast, "timeline_json")
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.timeline_auto_arrange", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"blocks_arranged": blocks_arranged, "duration_s": cursor},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"ok": True, "blocks_arranged": blocks_arranged, "duration_s": cursor}


# ── Bonded-block timeline endpoints (Phase E) ──


@router.get("/{cast_id}/timeline")
async def get_cast_timeline_unified(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the cast timeline with rebuild_needed flag.

    If any block's approved variant audio_key has changed since the timeline
    was last saved, rebuild_needed=True and a fresh timeline is returned
    instead of the stale stored one.
    """
    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks).selectinload(Block.variants))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    stored = (cast.timeline_json or {}).get("default", {}).get("twick_data")
    rebuild_needed = False
    orphaned_elements: list = []

    if stored:
        # Check if any block's audio changed since timeline was saved
        active_blocks = [b for b in cast.blocks if b.deleted_at is None]
        stored_voice_ids = set()
        for track in (stored.get("tracks") or []):
            for el in (track.get("elements") or []):
                meta = el.get("metadata") or {}
                if meta.get("bonded") and meta.get("block_id"):
                    stored_voice_ids.add(meta["block_id"])

        for block in active_blocks:
            variant = block.variants[0] if block.variants else None
            if not variant:
                continue
            if block.id not in stored_voice_ids:
                rebuild_needed = True
                break

    return {
        "timeline": stored,
        "rebuild_needed": rebuild_needed,
        "orphaned_elements": orphaned_elements,
    }


class PatchTimelineRequest(BaseModel):
    twick_data: dict
    block_regions: list = []


@router.patch("/{cast_id}/timeline")
async def patch_cast_timeline(
    cast_id: str,
    payload: PatchTimelineRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save the full timeline JSON (bonded block model).

    Validates that each block_id appears at most once as a bonded pair
    and that bonded pair start/end times match within 50ms tolerance.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    # Validate bonded pairs
    twick_data = payload.twick_data
    bonded_blocks: dict = {}  # block_id -> count
    for track in (twick_data.get("tracks") or []):
        for el in (track.get("elements") or []):
            meta = el.get("metadata") or {}
            if meta.get("bonded") and meta.get("block_id"):
                bid = meta["block_id"]
                bonded_blocks[bid] = bonded_blocks.get(bid, 0) + 1

    for bid, count in bonded_blocks.items():
        if count > 2:
            raise HTTPException(
                400,
                f"Block {bid} has {count} bonded elements — expected at most 2 (snapshot + voice)",
            )

    from datetime import datetime
    from sqlalchemy.orm.attributes import flag_modified

    current = cast.timeline_json or {}
    current["default"] = {
        "twick_data": twick_data,
        "block_regions": payload.block_regions,
        "saved_at": datetime.utcnow().isoformat(),
    }
    cast.timeline_json = current
    flag_modified(cast, "timeline_json")
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.timeline_patch", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"ok": True, "cast_id": cast_id}


# ── Auto-Clips (CHANGE 5/6) ──


@router.post("/{cast_id}/clips/{clip_index}/approve")
async def approve_clip(
    cast_id: str,
    clip_index: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve one of the LLM-suggested clips and create a child Cast.

    The child cast carries `clip_parent_cast_id` + `clip_block_ids`. When
    its render is dispatched, _render_async branches into the FFmpeg trim
    path which stitches the relevant segments out of the parent's already-
    composed mp4 (no GPU re-render).

    `clip_index` is the position within the parent's `suggested_clips`
    list — stable for a single API call. The clip's *content* is keyed by
    block IDs, not positions, so reordering parent blocks doesn't drift.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    suggested = list(cast.suggested_clips or [])
    if clip_index < 0 or clip_index >= len(suggested):
        raise HTTPException(404, "Clip not found")

    clip = suggested[clip_index]
    block_ids = list(clip.get("block_ids") or [])
    if not block_ids:
        raise HTTPException(400, "Clip has no block IDs")

    try:
        child_id = f"cst_{uuid.uuid4().hex[:12]}"
        child = Cast(
            id=child_id,
            user_id=user.id,
            avatar_id=cast.avatar_id,
            channel_id=cast.channel_id,
            name=f"{cast.name or 'Cast'} — {clip.get('name') or 'Clip'}",
            status=CastStatus.READY,
            description=f"Clip: {clip.get('name') or ''}".strip(),
            quality=cast.quality,
            output_format=cast.output_format,
            platform_target=cast.platform_target,
            clip_parent_cast_id=cast.id,
            clip_block_ids=block_ids,
            creation_paid=True,
        )
        db.add(child)

        approved = list(cast.approved_clips or [])
        # Don't double-add the same clip name on repeated clicks.
        if not any(c.get("name") == clip.get("name") for c in approved):
            stamped = dict(clip)
            stamped["child_cast_id"] = child_id
            approved.append(stamped)
        cast.approved_clips = approved
        from sqlalchemy.orm.attributes import flag_modified as _flag_modified
        _flag_modified(cast, "approved_clips")

        await db.commit()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        await db.rollback()
        raise HTTPException(500, "Failed to approve clip") from exc

    try:
        await audit_log.record(
            db, user_id=user.id, action="render.clip_approve", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"clip_index": clip_index, "child_cast_id": child_id},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"child_cast_id": child_id, "clip": clip}


@router.delete("/{cast_id}/clips/{clip_index}")
async def dismiss_clip(
    cast_id: str,
    clip_index: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an LLM-suggested clip the user doesn't want.

    Edits `suggested_clips` in place. Approved clips are untouched —
    delete the child cast separately if you want to drop one.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")
    suggested = list(cast.suggested_clips or [])
    if clip_index < 0 or clip_index >= len(suggested):
        raise HTTPException(404, "Clip not found")
    suggested.pop(clip_index)
    cast.suggested_clips = suggested
    from sqlalchemy.orm.attributes import flag_modified as _flag_modified
    _flag_modified(cast, "suggested_clips")
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="render.clip_dismiss", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            before={"clip_index": clip_index},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"ok": True}


# ── Finalize & Render (Phase F — two-pass pipeline) ──


async def _ensure_framings_ready(db: AsyncSession, cast: Cast) -> int:
    """Round-6 Bug B follow-up — pre-warm per-block framing looks before render.

    The renderer's body_motion/action `_resolve` (tasks/cast_render.py) looks up
    a look by the (avatar_id, look_type, framing) triple. Blocks whose framing
    has no matching READY look used to fall back to a wrong-framing look (every
    block shared al_…'s MEDIUM shot). To make framings actually vary we scan the
    avatar_action / body_motion blocks here and KICK OFF look generation for any
    block whose start/end frame look is missing for its framing, instead of
    letting the renderer reuse the wrong shot.

    Best-effort and non-blocking: tasks are enqueued via Celery `.delay()` (the
    same pattern generate-outline uses to pre-warm frames) and the render
    proceeds; the look-gen tasks stamp the block's framing on the new AvatarLook
    so the next finalize resolves cleanly. Returns the number of frame-gen tasks
    enqueued (0 when everything is already ready). Never raises — a pre-warm
    failure must not block the render.
    """
    from models.avatar_look import AvatarLook, TALKING_HEAD_LOOK_TYPE, DEFAULT_FRAMING
    enqueued = 0
    try:
        from tasks.avatar_looks import (
            generate_action_frame_task,
            generate_body_motion_frame_task,
            generate_talking_head_task,
        )
        avatar_id = getattr(cast, "avatar_id", None)
        if not avatar_id:
            return 0
        blocks_q = await db.execute(
            select(Block).where(Block.cast_id == cast.id)
        )
        # Distinct framings already requested talking-head pre-warm this pass, so
        # N talk blocks at the same framing enqueue at most one gen task.
        talking_head_seen: set[str] = set()
        for blk in blocks_q.scalars().all():
            if getattr(blk, "deleted_at", None) is not None:
                continue
            is_action = blk.category == "avatar_action" or blk.render_mode == "body_motion"
            block_framing = (getattr(blk, "framing", None) or DEFAULT_FRAMING).strip().upper()

            if not is_action:
                # Round-6 Bug B round-3: plain lip-sync (talking-head) blocks need
                # a reusable per-framing face reference. MEDIUM (or no framing)
                # keeps the avatar's default look, so only pre-warm the variety
                # framings. The look is keyed by (avatar_id, framing) — not by
                # block — so it is shared across every talk block at that framing.
                if block_framing == DEFAULT_FRAMING:
                    continue
                if block_framing in talking_head_seen:
                    continue
                talking_head_seen.add(block_framing)
                existing = await db.scalar(
                    select(AvatarLook.id)
                    .where(AvatarLook.avatar_id == avatar_id)
                    .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
                    .where(AvatarLook.framing == block_framing)
                    .where(AvatarLook.status == "ready")
                    .limit(1)
                )
                if existing:
                    continue
                generate_talking_head_task.delay(avatar_id, block_framing)
                enqueued += 1
                logger.info(
                    "[talking-head-prewarm] cast=%s avatar=%s framing=%s — queued look gen",
                    cast.id, avatar_id, block_framing,
                )
                continue

            for kind in ("start", "end"):
                prompt = (
                    blk.action_start_prompt if kind == "start" else blk.action_end_prompt
                ) or (
                    blk.body_motion_start_prompt if kind == "start" else blk.body_motion_end_prompt
                )
                if not prompt:
                    continue
                # Does a READY look already exist for this block+kind+framing?
                for prefix in (
                    f"action_block_{blk.id}_{kind}",
                    f"body_motion_block_{blk.id}_{kind}",
                ):
                    existing = await db.scalar(
                        select(AvatarLook.id)
                        .where(AvatarLook.avatar_id == avatar_id)
                        .where(AvatarLook.look_type == prefix)
                        .where(AvatarLook.framing == block_framing)
                        .where(AvatarLook.status == "ready")
                        .limit(1)
                    )
                    if existing:
                        break
                else:
                    # No ready look for this framing — generate a fresh one.
                    if blk.action_start_prompt or blk.action_end_prompt:
                        generate_action_frame_task.delay(blk.id, kind, prompt)
                    else:
                        generate_body_motion_frame_task.delay(blk.id, kind, prompt)
                    enqueued += 1
                    logger.info(
                        "[framing-prewarm] cast=%s block=%s kind=%s framing=%s — queued look gen",
                        cast.id, blk.id, kind, block_framing,
                    )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
    return enqueued


@router.post("/{cast_id}/finalize")
async def finalize_cast(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Finalize a cast for rendering.

    1. Validates at least one bonded block exists in the timeline
    2. Snapshots the current timeline into a new cast_renders row
    3. Dispatches the render_cast_task Celery task
    4. Returns { render_id, status: 'queued' }
    """
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks).selectinload(Block.variants))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    # Build or load the timeline snapshot
    stored = (cast.timeline_json or {}).get("default", {}).get("twick_data")
    if not stored:
        raise HTTPException(400, "No timeline data saved — please arrange the timeline first")

    # Validate at least one bonded block, and count all layers
    from collections import Counter as _Counter
    has_bonded = False
    layer_types: _Counter = _Counter()
    empty_src_violations: list[str] = []
    for track in stored.get("tracks", []):
        for el in track.get("elements", []):
            meta = el.get("metadata") or {}
            el_type = (el.get("type") or "").lower()
            if meta.get("bonded") and meta.get("block_id"):
                has_bonded = True
                layer_types["bonded_avatar"] += 1
            else:
                layer_types[el_type or "unknown"] += 1
                if el_type in ("image", "video", "audio", "gif", "sticker", "overlay", "logo"):
                    if not (el.get("props") or {}).get("src"):
                        empty_src_violations.append(f"{el_type}:{el.get('id')}")

    if not has_bonded:
        raise HTTPException(400, "No bonded blocks found in timeline — nothing to render")

    # Round-6 Bug B follow-up: pre-warm any per-block framing looks that don't
    # exist yet so the renderer doesn't fall back to a wrong-framing shared look.
    # Best-effort, non-blocking — the render proceeds regardless.
    prewarmed = await _ensure_framings_ready(db, cast)
    if prewarmed:
        logger.info(
            "Cast %s finalize: queued %d framing look-gen task(s) before render",
            cast_id, prewarmed,
        )

    logger.info(
        "Cast %s finalize: bonded=%d, overlays_by_type=%s, empty_src=%d",
        cast_id, layer_types.get("bonded_avatar", 0),
        {k: v for k, v in layer_types.items() if k != "bonded_avatar"},
        len(empty_src_violations),
    )
    if empty_src_violations:
        logger.warning(
            "Cast %s has %d overlays with empty src (will be dropped): %s",
            cast_id, len(empty_src_violations), empty_src_violations[:10],
        )

    # Create cast_renders row
    from models.cast_render import CastRender, CastRenderStatus
    render_id = f"rnd_{uuid.uuid4().hex[:12]}"
    cast_render = CastRender(
        id=render_id,
        cast_id=cast_id,
        user_id=user.id,
        status=CastRenderStatus.QUEUED.value,
        version=cast.version,
        quality=cast.quality.value if hasattr(cast.quality, "value") else str(cast.quality) if cast.quality else None,
        timeline_snapshot=stored,
    )
    db.add(cast_render)
    await db.commit()

    # Dispatch Celery task to dedicated renders queue with fair priority
    from tasks.cast_render import render_cast_task, extract_bonded_blocks_from_timeline
    block_count = len(extract_bonded_blocks_from_timeline(stored))
    priority = max(0, min(9, 10 - block_count))  # fewer blocks = higher priority
    render_cast_task.apply_async(
        args=[render_id],
        queue="renders",
        priority=priority,
    )

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.finalize", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
            after={"render_id": render_id, "block_count": block_count},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {
        "render_id": render_id,
        "status": "queued",
        "version": cast.version,
        "layer_summary": {
            "bonded_blocks": layer_types.get("bonded_avatar", 0),
            "overlays": {k: v for k, v in layer_types.items() if k != "bonded_avatar"},
            "warnings": {
                "empty_src_count": len(empty_src_violations),
            },
        },
    }


@router.get("/{cast_id}/renders")
async def list_cast_renders(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all renders for a cast."""
    from models.cast_render import CastRender
    result = await db.execute(
        select(CastRender)
        .where(CastRender.cast_id == cast_id, CastRender.user_id == user.id)
        .order_by(CastRender.created_at.desc())
    )
    renders = result.scalars().all()

    import hashlib, json as _json

    def _timeline_hash(tl: dict | None) -> str | None:
        """Deterministic hash of timeline content (tracks + elements only)."""
        if not tl:
            return None
        # Hash only the tracks array — ignore metadata/timestamps
        tracks = tl.get("tracks", [])
        canonical = _json.dumps(tracks, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    out = []
    for r in renders:
        queue_position = None
        if r.status == "queued":
            queue_count = await db.execute(
                select(func.count(CastRender.id))
                .where(CastRender.status.in_(["queued", "baking"]))
                .where(CastRender.created_at < r.created_at)
            )
            queue_position = queue_count.scalar() + 1
        # Compact per-block + eta summary for in-flight renders. For completed
        # renders we return block_statuses as-is (small payload) so the UI can
        # show the final per-block breakdown if the user reopens the pill.
        block_statuses = r.block_statuses or []
        done_durations = [b["duration_s"] for b in block_statuses
                          if b.get("state") == "done" and b.get("duration_s") and b.get("provider") != "cache"]
        avg_done_s = (sum(done_durations) / len(done_durations)) if done_durations else None
        remaining_blocks = sum(1 for b in block_statuses if b.get("state") in ("queued", "baking"))
        eta_seconds = None
        if avg_done_s and remaining_blocks > 0:
            eta_seconds = max(5, int(remaining_blocks * avg_done_s))

        out.append({
            "id": r.id,
            "status": r.status,
            "version": r.version,
            "quality": r.quality,
            "duration_seconds": r.duration_seconds,
            "is_selected": bool(r.is_selected),
            "thumbnail_key": r.thumbnail_key,
            "baking_chunks_total": r.baking_chunks_total,
            "baking_chunks_completed": r.baking_chunks_completed,
            "progress_percent": r.progress_percent or 0,
            "progress_step": r.progress_step or "",
            "queue_position": queue_position,
            "output_video_r2_key": r.output_video_r2_key,
            "error_message": r.error_message,
            "timeline_hash": _timeline_hash(r.timeline_snapshot),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "blocks": block_statuses,
            "eta_seconds": eta_seconds,
        })
    return out


@router.get("/{cast_id}/renders/{render_id}")
async def get_cast_render(
    cast_id: str,
    render_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific render status."""
    from models.cast_render import CastRender
    render = await db.get(CastRender, render_id)
    if not render or render.cast_id != cast_id or render.user_id != user.id:
        raise HTTPException(404, "Render not found")

    queue_position = None
    if render.status == "queued":
        queue_count = await db.execute(
            select(func.count(CastRender.id))
            .where(CastRender.status.in_(["queued", "baking"]))
            .where(CastRender.created_at < render.created_at)
        )
        queue_position = queue_count.scalar() + 1

    # Compute per-block view + overall ETA.
    # Rules:
    #   - "done" rows with duration_s let us estimate remaining blocks.
    #   - If any block is baking, we use its elapsed time to refine ETA.
    #   - If nothing has completed yet, we don't have enough signal — return null.
    #   - If we have avg_done_s, ETA = (blocks remaining − 0.5) × avg_done_s
    #     (the 0.5 accounts for any block currently in-flight being partway done).
    from datetime import datetime, timezone
    block_statuses = render.block_statuses or []
    done_durations = [b["duration_s"] for b in block_statuses
                      if b.get("state") == "done" and b.get("duration_s") and b.get("provider") != "cache"]
    avg_done_s = (sum(done_durations) / len(done_durations)) if done_durations else None
    remaining = sum(1 for b in block_statuses if b.get("state") in ("queued", "baking"))
    eta_seconds = None
    if avg_done_s and remaining > 0:
        # Subtract elapsed time from any currently-baking blocks
        now = datetime.now(timezone.utc)
        baking_elapsed = 0.0
        baking_count = 0
        for b in block_statuses:
            if b.get("state") == "baking" and b.get("started_at"):
                try:
                    started = datetime.fromisoformat(b["started_at"])
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    baking_elapsed += (now - started).total_seconds()
                    baking_count += 1
                except Exception:
                    pass
        eta_raw = remaining * avg_done_s - baking_elapsed
        eta_seconds = max(5, int(eta_raw))  # clamp to sane floor so we don't show 0s

    # Per-block ETA: for still-queued blocks we just use avg_done_s; for baking
    # blocks, remaining = avg_done_s - elapsed (clamped to ≥5s).
    blocks_view = []
    now = datetime.now(timezone.utc)
    for b in block_statuses:
        row = dict(b)
        eta_b = None
        state = b.get("state")
        if avg_done_s:
            if state == "queued":
                eta_b = int(avg_done_s)
            elif state == "baking" and b.get("started_at"):
                try:
                    started = datetime.fromisoformat(b["started_at"])
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    elapsed = (now - started).total_seconds()
                    eta_b = max(5, int(avg_done_s - elapsed))
                except Exception:
                    pass
        row["eta_seconds"] = eta_b
        blocks_view.append(row)

    return {
        "id": render.id,
        "status": render.status,
        "version": render.version,
        "quality": render.quality,
        "duration_seconds": render.duration_seconds,
        "is_selected": bool(render.is_selected),
        "thumbnail_key": render.thumbnail_key,
        "baking_chunks_total": render.baking_chunks_total,
        "baking_chunks_completed": render.baking_chunks_completed,
        "progress_percent": render.progress_percent or 0,
        "progress_step": render.progress_step or "",
        "queue_position": queue_position,
        "output_video_r2_key": render.output_video_r2_key,
        "error_message": render.error_message,
        "render_attempt": render.render_attempt,
        "created_at": render.created_at.isoformat() if render.created_at else None,
        "completed_at": render.completed_at.isoformat() if render.completed_at else None,
        "blocks": blocks_view,
        "eta_seconds": eta_seconds,
    }


@router.patch("/{cast_id}/renders/{render_id}/select")
async def select_cast_render(
    cast_id: str,
    render_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set a render as the selected (starred) render for publishing/download.

    Deselects all other renders for this cast and updates cast.final_video_url
    to point to the selected render's output.
    """
    from models.cast_render import CastRender
    try:
        # Verify render exists and belongs to user
        render = await db.get(CastRender, render_id)
        if not render or render.cast_id != cast_id or render.user_id != user.id:
            raise HTTPException(404, "Render not found")
        if render.status != "ready":
            raise HTTPException(400, "Only completed renders can be selected")

        # Deselect all renders for this cast
        all_renders_result = await db.execute(
            select(CastRender).where(CastRender.cast_id == cast_id)
        )
        for r in all_renders_result.scalars().all():
            r.is_selected = 0

        # Select the target render
        render.is_selected = 1

        # Update cast.final_video_url to point to this render
        cast = await db.get(Cast, cast_id)
        if cast and render.output_video_r2_key:
            cast.final_video_url = render.output_video_r2_key

        await db.commit()
        try:
            await audit_log.record(
                db, user_id=user.id, action="render.select_main", entity_type="cast",
                entity_id=cast_id, cast_id=cast_id,
                after={"render_id": render_id},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
        return {"ok": True, "render_id": render_id, "is_selected": True}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Failed to select render: {str(e)[:200]}")


@router.get("/{cast_id}/renders/{render_id}/manifest")
async def get_render_manifest(
    cast_id: str,
    render_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Introspect exactly which layers reached the compositor for a given render.

    Returns bonded blocks, extracted overlays, and canvas dimensions.
    """
    from models.cast_render import CastRender
    from tasks.cast_render import extract_bonded_blocks_from_timeline, extract_overlay_elements
    from collections import Counter

    render = await db.get(CastRender, render_id)
    if not render or render.cast_id != cast_id or render.user_id != user.id:
        raise HTTPException(404, "Render not found")

    timeline = render.timeline_snapshot or {}
    try:
        bonded = extract_bonded_blocks_from_timeline(timeline)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        bonded = []

    render_size = {"480p": (480, 848), "720p": (720, 1280), "1080p": (1080, 1920)}.get(
        str(render.quality or "480p"), (480, 848)
    )
    try:
        overlays = extract_overlay_elements(timeline, render_width=render_size[0], render_height=render_size[1])
    except Exception as e:
        sentry_sdk.capture_exception(e)
        overlays = []

    type_breakdown = dict(Counter(o.get("type") for o in overlays))
    track_breakdown = dict(Counter(o.get("track_type", "untagged") for o in overlays))

    return {
        "render_id": render_id,
        "status": render.status,
        "canvas": {
            "snapshot_width": timeline.get("compositionWidth"),
            "snapshot_height": timeline.get("compositionHeight"),
            "render_width": render_size[0],
            "render_height": render_size[1],
        },
        "bonded_blocks": [
            {
                "block_id": (v.get("metadata") or {}).get("block_id"),
                "video_element_id": v.get("id"),
                "audio_element_id": a.get("id"),
                "start_s": v.get("s"),
                "duration_s": (a.get("e", 0) - a.get("s", 0)),
                "face_src": (v.get("props") or {}).get("src", "")[:80],
                "motion_prompt": (v.get("metadata") or {}).get("motion_prompt", "")[:80],
            }
            for v, a in bonded
        ],
        "overlay_summary": {
            "total": len(overlays),
            "by_type": type_breakdown,
            "by_track_type": track_breakdown,
        },
        "overlays": [
            {
                "id": o.get("id"),
                "type": o.get("type"),
                "track_type": o.get("track_type"),
                "start_s": o.get("start_s"),
                "end_s": o.get("end_s"),
                "x": o.get("x"),
                "y": o.get("y"),
                "width": o.get("width"),
                "height": o.get("height"),
                "has_text": bool(o.get("text")),
                "has_src": bool(o.get("src")),
                "text_preview": (o.get("text", "") or "")[:60],
                "src_preview": (o.get("src", "") or "")[:80],
            }
            for o in overlays
        ],
    }


@router.post("/{cast_id}/recomposite")
async def recomposite_cast(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-composite a cast without re-running InfiniteTalk.

    Requires all active variants to have video_key (base clips rendered)
    and timeline_json to exist on the cast.
    """
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks).selectinload(Block.variants))
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    cast = result.scalar_one_or_none()
    if not cast:
        raise HTTPException(404, "Cast not found")

    if not cast.timeline_json:
        raise HTTPException(400, "No timeline saved — open the editor and save first")

    # Check all active variants have rendered base clips
    active_blocks = [b for b in cast.blocks if getattr(b, "is_active", True) and b.deleted_at is None]
    for block in active_blocks:
        for variant in block.variants:
            if not variant.video_key:
                raise HTTPException(
                    400,
                    f"Variant {variant.id} in block {block.id} has no rendered video. "
                    "Run full generation first.",
                )

    from tasks.generate_cast import recomposite_cast_task
    recomposite_cast_task.delay(cast_id)

    cast.status = CastStatus.GENERATING_VIDEOS
    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast.recomposite", entity_type="cast",
            entity_id=cast_id, cast_id=cast_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"ok": True, "cast_id": cast_id, "status": "generating_videos"}


# ── Phase 2.4 — Cross-format cast duplication and sibling linkage ──


class DuplicateAsRequest(BaseModel):
    format_family: str  # "horizontal" or "vertical"


@router.post("/{cast_id}/duplicate-as", status_code=201)
async def duplicate_cast_as(
    cast_id: str,
    req: DuplicateAsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Duplicate a cast as the opposite format family (Pattern C).

    Copies blocks, reuses variants (audio is format-agnostic), copies product picks.
    New cast starts with empty timeline_json and status=tts_ready.
    """
    if req.format_family not in ("horizontal", "vertical"):
        raise HTTPException(400, "format_family must be 'horizontal' or 'vertical'")

    # Load source cast with blocks, variants, products
    result = await db.execute(
        select(Cast)
        .options(
            selectinload(Cast.blocks).selectinload(Block.variants),
            selectinload(Cast.products),
        )
        .where(Cast.id == cast_id, Cast.user_id == user.id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Cast not found")

    if req.format_family == getattr(source, "format_family", "vertical"):
        raise HTTPException(400, "Cast is already in the requested format family")

    try:
        # Determine root parent: if source itself has a parent, use that parent;
        # otherwise source IS the root.
        root_parent_id = getattr(source, "parent_cast_id", None) or source.id

        new_cast_id = f"cst_{uuid.uuid4().hex[:12]}"
        new_output_format = "16:9" if req.format_family == "horizontal" else "9:16"

        new_cast = Cast(
            id=new_cast_id,
            user_id=user.id,
            avatar_id=source.avatar_id,
            channel_id=source.channel_id,
            name=f"{source.name or 'Untitled'} ({req.format_family})",
            status=CastStatus.TTS_READY,
            template_name=source.template_name,
            quality=source.quality,
            output_format=new_output_format,
            format_family=req.format_family,
            parent_cast_id=root_parent_id,
            target_platforms=[],
            timeline_json=None,  # blank — user must lay out from scratch
            script_direction=source.script_direction,
            cast_type=getattr(source, "cast_type", "recorded"),
            description=getattr(source, "description", None),
            base_price=source.base_price,
            effects_price=source.effects_price,
            total_price=source.total_price,
            creation_fee_cents=source.creation_fee_cents,
            creation_paid=source.creation_paid,
        )
        db.add(new_cast)

        # Also update source's parent_cast_id if it was the root (had no parent)
        if not getattr(source, "parent_cast_id", None):
            source.parent_cast_id = source.id

        # Copy blocks with their variants (reuse, not clone)
        active_blocks = sorted(
            [b for b in source.blocks if b.deleted_at is None],
            key=lambda b: b.position or 0,
        )
        for block in active_blocks:
            new_blk_id = f"blk_{uuid.uuid4().hex[:12]}"
            new_block = Block(
                id=new_blk_id,
                cast_id=new_cast_id,
                product_id=block.product_id,
                type=block.type,
                position=block.position,
                mood=block.mood,
                key_points=block.key_points,
            )
            db.add(new_block)

            # Copy variants — reuse audio data (audio is format-agnostic)
            for v in (block.variants or []):
                new_var = Variant(
                    id=f"var_{uuid.uuid4().hex[:12]}",
                    block_id=new_blk_id,
                    variant_label=v.variant_label,
                    variant_style=v.variant_style,
                    script_text=v.script_text,
                    motion_prompt=v.motion_prompt,
                    estimated_duration_seconds=v.estimated_duration_seconds,
                    tts_r2_key=v.tts_r2_key,
                    tts_duration_seconds=v.tts_duration_seconds,
                    audio_key=v.audio_key,
                    duration_seconds=v.duration_seconds,
                    caption_words=v.caption_words,
                    caption_segments=v.caption_segments,
                    status=v.status if v.status == VariantStatus.READY else VariantStatus.PENDING,
                    is_active=v.is_active,
                    weight=v.weight,
                )
                db.add(new_var)

        # Copy product picks
        for cp in (source.products or []):
            new_cp = CastProduct(
                id=f"cp_{uuid.uuid4().hex[:12]}",
                cast_id=new_cast_id,
                product_id=cp.product_id,
                position=cp.position,
            )
            db.add(new_cp)

        await db.commit()

        try:
            await audit_log.record(
                db, user_id=user.id, action="cast.duplicate", entity_type="cast",
                entity_id=cast_id, cast_id=cast_id,
                after={"new_cast_id": new_cast_id, "format_family": req.format_family},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

        return {"cast_id": new_cast_id, "format_family": req.format_family}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        await db.rollback()
        raise HTTPException(500, f"Duplication failed: {str(e)[:200]}")


@router.get("/{cast_id}/siblings")
async def get_cast_siblings(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all casts in the same format-family group (siblings).

    A family is defined by parent_cast_id: all casts pointing to the same
    root parent, plus the root parent itself.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    parent_id = getattr(cast, "parent_cast_id", None)
    if not parent_id:
        # This cast has no siblings
        return {
            "self": {
                "cast_id": cast.id,
                "format_family": getattr(cast, "format_family", "vertical"),
                "name": cast.name,
            },
            "siblings": [],
        }

    try:
        # Find all casts sharing the same parent_cast_id or that ARE the parent
        from sqlalchemy import or_
        result = await db.execute(
            select(Cast).where(
                Cast.user_id == user.id,
                or_(
                    Cast.parent_cast_id == parent_id,
                    Cast.id == parent_id,
                ),
                Cast.deleted_at.is_(None),
            )
        )
        family = result.scalars().all()

        self_data = {
            "cast_id": cast.id,
            "format_family": getattr(cast, "format_family", "vertical"),
            "name": cast.name,
        }
        siblings_data = [
            {
                "cast_id": c.id,
                "format_family": getattr(c, "format_family", "vertical"),
                "name": c.name,
            }
            for c in family
            if c.id != cast.id
        ]

        return {"self": self_data, "siblings": siblings_data}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Failed to fetch siblings: {str(e)[:200]}")


# ═══════════════════════════════════════════════════════════════════════
# CAST VERSIONING — fork on script change after audio generated
# ═══════════════════════════════════════════════════════════════════════


class ForkRequest(BaseModel):
    name: str = ""


@router.post("/{cast_id}/fork")
async def fork_cast(
    cast_id: str,
    body: ForkRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Snapshot current cast blocks as a version, increment version.

    Called by frontend when script is edited after audio was already generated.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    # Only fork if status is beyond draft/script phase
    past_script_statuses = {
        CastStatus.GENERATING_TTS, CastStatus.TTS_READY,
        CastStatus.GENERATING_VIDEOS, CastStatus.GENERATING,
        CastStatus.READY, CastStatus.SCHEDULED, CastStatus.COMPLETED,
    }
    if cast.status not in past_script_statuses:
        return {"forked": False, "version": cast.version, "message": "No fork needed — still in script phase"}

    try:
        # Snapshot current blocks
        blocks_result = await db.execute(
            select(Block).where(Block.cast_id == cast_id, Block.deleted_at.is_(None)).order_by(Block.position)
        )
        blocks = blocks_result.scalars().all()

        snapshot = []
        for b in blocks:
            block_data = {
                "id": b.id, "type": b.type.value if hasattr(b.type, "value") else str(b.type),
                "category": b.category, "position": b.position,
                "product_id": b.product_id, "mood": b.mood,
                "key_points": b.key_points, "render_mode": b.render_mode,
            }
            # Include variant script texts
            variants_result = await db.execute(
                select(Variant).where(Variant.block_id == b.id)
            )
            variants = variants_result.scalars().all()
            block_data["variants"] = [
                {"id": v.id, "script_text": v.script_text, "variant_label": v.variant_label,
                 "audio_key": v.audio_key, "video_key": v.video_key,
                 "tts_duration_seconds": v.tts_duration_seconds, "duration_seconds": v.duration_seconds,
                 "motion_prompt": v.motion_prompt}
                for v in variants
            ]
            snapshot.append(block_data)

        # Compute duration from variants
        total_dur = 0.0
        for bd in snapshot:
            for vd in bd.get("variants", []):
                total_dur += vd.get("tts_duration_seconds", 0) or vd.get("duration_seconds", 0) or 0

        # Save current state as a new version
        version_id = f"cv_{uuid.uuid4().hex[:12]}"
        version_name = (body.name if body else "") or ""
        old_version = cast.version
        new_version = old_version + 1

        # Backfill: if no CastVersion row exists for old_version (this is the first
        # fork for this cast — historically v1 was never snapshotted), create one
        # NOW with the current snapshot labeled as v{old_version}. Without this the
        # user loses v1 in the dropdown after creating v2.
        # NOTE: `.first()` over `.scalar_one_or_none()` — historical duplicates exist
        # for some casts (no UNIQUE constraint before this PR), and crashing on those
        # broke the fork endpoint entirely. Order by created_at DESC so we pick the
        # most recent snapshot if multiple rows happen to exist.
        existing_old_result = await db.execute(
            select(CastVersion)
            .where(
                CastVersion.cast_id == cast_id,
                CastVersion.version == old_version,
            )
            .order_by(CastVersion.created_at.desc())
            .limit(1)
        )
        existing_old = existing_old_result.scalars().first()
        if existing_old is None:
            db.add(CastVersion(
                id=f"cv_{uuid.uuid4().hex[:12]}",
                cast_id=cast_id,
                version=old_version,
                blocks_snapshot_json=snapshot,
                status_at_snapshot=cast.status.value if hasattr(cast.status, "value") else str(cast.status),
                name="",
                duration_seconds=total_dur or None,
                quality=cast.quality.value if cast.quality else None,
            ))

        version_record = CastVersion(
            id=version_id,
            cast_id=cast_id,
            version=new_version,
            blocks_snapshot_json=snapshot,
            status_at_snapshot=cast.status.value if hasattr(cast.status, "value") else str(cast.status),
            name=version_name,
            duration_seconds=total_dur or None,
            quality=cast.quality.value if cast.quality else None,
        )
        db.add(version_record)

        # Increment version on the cast
        cast.version = new_version
        from datetime import datetime
        cast.updated_at = datetime.utcnow()
        await db.commit()

        try:
            await audit_log.record(
                db, user_id=user.id, action="cast.fork", entity_type="cast",
                entity_id=cast_id, cast_id=cast_id,
                before={"version": old_version}, after={"version": new_version, "version_id": version_id},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

        return {
            "forked": True,
            "old_version": old_version,
            "new_version": new_version,
            "version_id": version_id,
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Fork failed: {str(e)[:200]}")


@router.get("/{cast_id}/versions")
async def list_cast_versions(
    cast_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all saved versions for a cast."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    try:
        result = await db.execute(
            select(CastVersion)
            .where(CastVersion.cast_id == cast_id)
            .order_by(CastVersion.created_at.desc())
        )
        versions = result.scalars().all()

        # Find which versions have renders and their latest render status
        from models.cast_render import CastRender
        render_result = await db.execute(
            select(CastRender.version, CastRender.status, CastRender.completed_at)
            .where(CastRender.cast_id == cast_id)
            .order_by(CastRender.created_at.desc())
        )
        render_rows = render_result.all()
        # Map version -> best render status (ready > baking > queued > failed)
        version_render_status: dict[int, str] = {}
        for rv, rs, _ in render_rows:
            if rv is not None and rv not in version_render_status:
                version_render_status[rv] = rs

        return {
            "current_version": cast.version,
            "versions": [
                {
                    "id": v.id,
                    "version": v.version,
                    "status_at_snapshot": v.status_at_snapshot,
                    "change_summary": v.change_summary,
                    "name": v.name or "",
                    "block_count": len(v.blocks_snapshot_json) if v.blocks_snapshot_json else 0,
                    "duration_seconds": v.duration_seconds,
                    "quality": v.quality,
                    "render_status": version_render_status.get(v.version),
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in versions
            ],
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Failed to list versions: {str(e)[:200]}")


@router.get("/{cast_id}/versions/{version_id}")
async def get_cast_version(
    cast_id: str,
    version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific version snapshot (read-only)."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    version = await db.get(CastVersion, version_id)
    if not version or version.cast_id != cast_id:
        raise HTTPException(404, "Version not found")

    return {
        "id": version.id,
        "version": version.version,
        "blocks_snapshot": version.blocks_snapshot_json,
        "status_at_snapshot": version.status_at_snapshot,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


@router.post("/{cast_id}/versions/{version_id}/restore")
async def restore_cast_version(
    cast_id: str,
    version_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore an old version — snapshots current state first, then copies old blocks back."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != user.id:
        raise HTTPException(404, "Cast not found")

    version = await db.get(CastVersion, version_id)
    if not version or version.cast_id != cast_id:
        raise HTTPException(404, "Version not found")

    try:
        # First, snapshot current state (auto-fork)
        blocks_result = await db.execute(
            select(Block).where(Block.cast_id == cast_id, Block.deleted_at.is_(None)).order_by(Block.position)
        )
        blocks = blocks_result.scalars().all()
        current_snapshot = []
        for b in blocks:
            block_data = {
                "id": b.id, "type": b.type.value if hasattr(b.type, "value") else str(b.type),
                "category": b.category, "position": b.position,
                "product_id": b.product_id, "mood": b.mood,
                "key_points": b.key_points, "render_mode": b.render_mode,
            }
            variants_result = await db.execute(select(Variant).where(Variant.block_id == b.id))
            variants = variants_result.scalars().all()
            block_data["variants"] = [
                {"id": v.id, "script_text": v.script_text, "variant_label": v.variant_label,
                 "audio_key": v.audio_key, "video_key": v.video_key}
                for v in variants
            ]
            current_snapshot.append(block_data)

        # Save current as version
        snap_id = f"cv_{uuid.uuid4().hex[:12]}"
        db.add(CastVersion(
            id=snap_id, cast_id=cast_id, version=cast.version,
            blocks_snapshot_json=current_snapshot,
            status_at_snapshot=cast.status.value if hasattr(cast.status, "value") else str(cast.status),
        ))

        # Soft-delete current blocks
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for b in blocks:
            b.deleted_at = now

        # Restore blocks from old version snapshot
        old_blocks = version.blocks_snapshot_json or []
        for bd in old_blocks:
            new_block_id = f"blk_{uuid.uuid4().hex[:12]}"
            block = Block(
                id=new_block_id,
                cast_id=cast_id,
                type=bd.get("type", "PRODUCT"),
                category=bd.get("category", "avatar_speaking"),
                position=bd.get("position", 0),
                product_id=bd.get("product_id"),
                mood=bd.get("mood"),
                key_points=bd.get("key_points"),
                render_mode=bd.get("render_mode", "avatar_full"),
            )
            db.add(block)

            # Restore variants
            for vd in bd.get("variants", []):
                var_id = f"var_{uuid.uuid4().hex[:12]}"
                variant = Variant(
                    id=var_id,
                    block_id=new_block_id,
                    script_text=vd.get("script_text", ""),
                    variant_label=vd.get("variant_label", "A"),
                    status=VariantStatus.DRAFT if not vd.get("audio_key") else VariantStatus.READY,
                )
                db.add(variant)

        # Increment version, restore status from snapshot
        cast.version = cast.version + 1
        restored_status = version.status_at_snapshot or "DRAFT"
        try:
            cast.status = CastStatus(restored_status.upper()) if hasattr(CastStatus, restored_status.upper()) else CastStatus(restored_status)
        except (ValueError, KeyError):
            cast.status = CastStatus.DRAFT
        cast.generation_progress = 0.0
        cast.generation_error = None
        await db.commit()

        try:
            await audit_log.record(
                db, user_id=user.id, action="cast.restore_version", entity_type="cast",
                entity_id=cast_id, cast_id=cast_id,
                after={"from_version": version.version, "new_version": cast.version},
            )
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

        return {
            "restored": True,
            "from_version": version.version,
            "new_version": cast.version,
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, f"Restore failed: {str(e)[:200]}")

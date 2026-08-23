"""Cast crud endpoints — split from the former routers/casts.py."""

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

@router.post("", response_model=CastResponse, status_code=201)
async def create_cast(
    req: CastCreate,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    # Avatar MUST be approved
    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Avatar not found")
    if avatar.status != AvatarStatus.APPROVED and avatar.status != AvatarStatus.READY and avatar.id != "default":
        raise HTTPException(400, "Only approved or ready avatars can be used. Complete the clone flow and approve your avatar first.")

    cast_id = f"cst_{uuid.uuid4().hex[:12]}"

    # Auto-generate name if not provided
    cast_name = req.name
    if not cast_name:
        try:
            user_timezone = getattr(user, "timezone", None)
            cast_name = await _generate_cast_name(db, ctx.workspace_owner_id, user_timezone)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            cast_name = f"Cast {datetime.now(tz.utc).strftime('%b%d')}-1"

    # Quality-based pricing
    quality_prices = {"simple": 1499, "hd": 1999, "hd_plus": 2999}
    fee = quality_prices.get(req.quality, 1499)

    cast = Cast(
        id=cast_id,
        user_id=ctx.workspace_owner_id,        avatar_id=req.avatar_id,
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
                id=prod_id, user_id=ctx.workspace_owner_id,
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
            if product and product.user_id == ctx.workspace_owner_id:
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

@router.get("/review-queue")
async def list_review_queue(
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.PUBLISHER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Casts a Creator has submitted, awaiting a Publisher's review.

    Registered before the /{cast_id} routes below — FastAPI matches routes
    in registration order, so a literal "/review-queue" path must come
    first or it'd be swallowed by /{cast_id} treating "review-queue" as an
    id.
    """
    rows = (
        await db.execute(
            select(Cast)
            .where(
                Cast.user_id == ctx.workspace_owner_id,
                Cast.approval_status == CastApprovalStatus.READY_FOR_REVIEW,
            )
            .order_by(Cast.submitted_for_review_at.asc())
        )
    ).scalars().all()

    submitter_ids = {c.submitted_by for c in rows if c.submitted_by}
    submitters: dict[str, str] = {}
    if submitter_ids:
        submitter_rows = (
            await db.execute(select(User).where(User.id.in_(submitter_ids)))
        ).scalars().all()
        submitters = {u.id: (u.display_name or u.email) for u in submitter_rows}

    return {
        "casts": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "submitted_for_review_at": (
                    c.submitted_for_review_at.isoformat() if c.submitted_for_review_at else None
                ),
                "submitted_by": c.submitted_by,
                "submitted_by_name": submitters.get(c.submitted_by, c.submitted_by),
            }
            for c in rows
        ]
    }

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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
    page: Optional[int] = Query(
        None, ge=1,
        description="1-indexed page number. Omit to return every matching cast (legacy behavior — existing callers like the Publish hub rely on getting the full set back).",
    ),
    per_page: int = Query(20, ge=1, le=100, description="Casts per page; only applied when page is given."),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List casts owned by the current user.

    The Publish hub queries with `status=ready&has_render=true` to find
    rendered casts that are ready to schedule — that call (and any other
    caller that omits `page`) still gets every matching cast back, unpaged.
    My Casts is the only page that opts into pagination by passing `page`.

    Each row carries enough avatar metadata to render the cast card
    thumbnail without a per-cast follow-up call: `avatar_thumbnail_url`,
    `avatar_name`, plus the legacy `avatar` object that older clients
    still read for `face_ref_key`.
    """
    from models.avatar_look import AvatarLook
    from services.r2_storage import get_r2_storage_service as _get_r2

    stmt = select(Cast).where(Cast.user_id == ctx.workspace_owner_id)
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
            return CastListResponse(casts=[], total=0, page=page, per_page=per_page)
    if has_render is True:
        stmt = stmt.where(Cast.final_video_url.is_not(None))
    elif has_render is False:
        stmt = stmt.where(Cast.final_video_url.is_(None))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0

    stmt = stmt.order_by(Cast.created_at.desc())
    if page is not None:
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(stmt)
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
    return {"casts": out, "total": total, "page": page, "per_page": per_page}

@router.get("/{cast_id}")
async def get_cast(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Cast)
        .options(
            selectinload(Cast.blocks).selectinload(Block.variants),
            selectinload(Cast.products).selectinload(CastProduct.product),
        )
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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
        "template_id": cast.template_id,
        "description": cast.description,
        "duration_target_seconds": cast.duration_target_seconds,
        "cast_type": cast.cast_type,
        "production_level": cast.production_level or "standard",
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

@router.put("/{cast_id}", response_model=CastResponse)
async def update_cast(
    cast_id: str,
    req: CastCreate,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

@router.patch("/{cast_id}", response_model=CastResponse)
async def patch_cast(
    cast_id: str,
    req: CastPatchRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_no_active_render),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

async def _delete_cast_core(db: AsyncSession, user: User, cast: Cast) -> None:
    """Shared deletion logic — used by both the single and batch delete
    endpoints. Does not commit or catch exceptions; the caller controls the
    transaction boundary so a batch can isolate one cast's failure from the
    rest.

    Cascade: child auto-clip casts, blocks/variants, cast_renders, render_jobs,
    cast_products, generation_cost, api_usage_log, stream_sessions, cast_versions.
    Social posts referencing the cast have cast_id nulled (kept as a record).
    """
    cast_id = cast.id
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


@router.delete("/{cast_id}", status_code=204)
async def delete_cast(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a cast and all dependent rows. See _delete_cast_core."""
    try:
        cast = await db.get(Cast, cast_id)
        if not cast or cast.user_id != ctx.workspace_owner_id:
            raise HTTPException(404, "Cast not found")
        if cast.status in (CastStatus.LIVE, CastStatus.GENERATING):
            raise HTTPException(400, "Cannot delete active cast")

        await _delete_cast_core(db, user, cast)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        await db.rollback()
        raise HTTPException(500, "Failed to delete cast")


class BatchDeleteCastsRequest(BaseModel):
    cast_ids: List[str]


@router.post("/batch-delete")
async def batch_delete_casts(
    req: BatchDeleteCastsRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple casts in one call. Same rules as single delete
    (ownership, can't delete an active cast) — best-effort per cast, so one
    failure (e.g. a cast that started rendering mid-selection) doesn't block
    the rest of the batch from being deleted.
    """
    deleted: list[str] = []
    failed: dict[str, str] = {}
    for cast_id in req.cast_ids:
        try:
            cast = await db.get(Cast, cast_id)
            if not cast or cast.user_id != ctx.workspace_owner_id:
                failed[cast_id] = "not found"
                continue
            if cast.status in (CastStatus.LIVE, CastStatus.GENERATING):
                failed[cast_id] = "cannot delete an active cast"
                continue
            await _delete_cast_core(db, user, cast)
            await db.commit()
            deleted.append(cast_id)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await db.rollback()
            failed[cast_id] = "failed to delete"
    return {"deleted": deleted, "failed": failed}

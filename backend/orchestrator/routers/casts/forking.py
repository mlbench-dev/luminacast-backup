"""Cast forking endpoints — split from the former routers/casts.py."""

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

class DuplicateAsRequest(BaseModel):
    format_family: str  # "horizontal" or "vertical"

@router.post("/{cast_id}/duplicate-as", status_code=201)
async def duplicate_cast_as(
    cast_id: str,
    req: DuplicateAsRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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
            user_id=ctx.workspace_owner_id,            avatar_id=source.avatar_id,
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return all casts in the same format-family group (siblings).

    A family is defined by parent_cast_id: all casts pointing to the same
    root parent, plus the root parent itself.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
                Cast.user_id == ctx.workspace_owner_id,
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

class ForkRequest(BaseModel):
    name: str = ""

@router.post("/{cast_id}/fork")
async def fork_cast(
    cast_id: str,
    body: ForkRequest | None = None,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Snapshot current cast blocks as a version, increment version.

    Called by frontend when script is edited after audio was already generated.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List all saved versions for a cast."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific version snapshot (read-only)."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Restore an old version — snapshots current state first, then copies old blocks back."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

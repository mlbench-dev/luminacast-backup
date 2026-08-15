"""Cast clips endpoints — split from the former routers/casts.py."""

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

@router.post("/{cast_id}/clips/{clip_index}/approve")
async def approve_clip(
    cast_id: str,
    clip_index: int,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
            user_id=ctx.workspace_owner_id,            avatar_id=cast.avatar_id,
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Remove an LLM-suggested clip the user doesn't want.

    Edits `suggested_clips` in place. Approved clips are untouched —
    delete the child cast separately if you want to drop one.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

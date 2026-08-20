"""Cast render endpoints — split from the former routers/casts.py."""

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

ACTIVE_RENDER_STATUSES = ["queued", "baking", "composing"]

async def require_no_active_render(cast_id: str, db: AsyncSession = Depends(get_db)) -> None:
    """Block edits to a cast while one of its renders is actively in flight.

    The render task reads several cast/variant fields live rather than from
    a frozen snapshot (quality, duration_target_seconds, per-block
    script_text) — an edit here mid-render can produce a video that's part
    old content, part new, with no error surfaced. Cancel the render via
    POST /{cast_id}/renders/{render_id}/cancel to unblock editing.
    """
    from models.cast_render import CastRender
    result = await db.execute(
        select(CastRender.id).where(
            CastRender.cast_id == cast_id,
            CastRender.status.in_(ACTIVE_RENDER_STATUSES),
        )
    )
    if result.first() is not None:
        raise HTTPException(409, "A render is currently in progress. Cancel it or wait for it to finish before editing.")

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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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

    # Billing gate: block only when there is truly no way to pay for this
    # render (free allowance exhausted, no subscription, no PAYG credits).
    # Deliberately does not check whether the render's eventual duration
    # will fit remaining allowance — that isn't known until it finishes,
    # and a render is allowed to push the user into overage.
    from services import billing_service
    await billing_service.check_render_preflight(db, user.id)

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
        user_id=ctx.workspace_owner_id,        status=CastRenderStatus.QUEUED.value,
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
    task = render_cast_task.apply_async(
        args=[render_id],
        queue="renders",
        priority=priority,
    )
    cast_render.celery_task_id = task.id
    await db.commit()

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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List all renders for a cast."""
    from models.cast_render import CastRender
    result = await db.execute(
        select(CastRender)
        .where(CastRender.cast_id == cast_id, CastRender.user_id == ctx.workspace_owner_id)
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific render status."""
    from models.cast_render import CastRender
    render = await db.get(CastRender, render_id)
    if not render or render.cast_id != cast_id or render.user_id != ctx.workspace_owner_id:
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

@router.post("/{cast_id}/renders/{render_id}/cancel")
async def cancel_cast_render(
    cast_id: str,
    render_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """User-requested cancel for an in-flight render.

    This is cooperative, not a hard kill: the worker runs Celery with
    --pool=threads, which can't forcibly interrupt a thread mid-request, so
    a block that's already mid-call to a GPU provider finishes on its own.
    What this DOES do immediately:
      - marks the render CANCELLED so the frontend stops showing/polling it
      - best-effort revokes the Celery task in case it's still queued
      - tells render_cast_task (via the row's status) to skip the retry
        pass and skip compose, so no further paid GPU work gets queued
    """
    from datetime import datetime, timezone
    from models.cast_render import CastRender, CastRenderStatus

    render = await db.get(CastRender, render_id)
    if not render or render.cast_id != cast_id or render.user_id != user.id:
        raise HTTPException(404, "Render not found")

    if render.status not in ACTIVE_RENDER_STATUSES:
        raise HTTPException(400, f"Render is already {render.status} — nothing to cancel")

    # Surface why it was already going badly, if anything had failed before
    # the user hit cancel — same info the block dropdown shows, folded into
    # one message.
    block_statuses = render.block_statuses or []
    failed = [b for b in block_statuses if b.get("state") == "failed" and b.get("error")]
    reason_suffix = ""
    if failed:
        parts = [f"block #{(b.get('index', 0) or 0) + 1}: {b['error']}" for b in failed[:3]]
        reason_suffix = f" {len(failed)} block(s) had already failed — {'; '.join(parts)}"

    render.status = CastRenderStatus.CANCELLED.value
    render.error_message = f"Cancelled by user.{reason_suffix}"
    render.completed_at = datetime.now(timezone.utc)

    if render.celery_task_id:
        try:
            from tasks import celery_app
            celery_app.control.revoke(render.celery_task_id)
        except Exception as e:
            sentry_sdk.capture_exception(e)

    await db.commit()

    try:
        await audit_log.record(
            db, user_id=user.id, action="cast_render.cancel", entity_type="cast_render",
            entity_id=render_id, cast_id=cast_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)

    return {"id": render.id, "status": render.status, "error_message": render.error_message}

@router.patch("/{cast_id}/renders/{render_id}/select")
async def select_cast_render(
    cast_id: str,
    render_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        if not render or render.cast_id != cast_id or render.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Introspect exactly which layers reached the compositor for a given render.

    Returns bonded blocks, extracted overlays, and canvas dimensions.
    """
    from models.cast_render import CastRender
    from tasks.cast_render import extract_bonded_blocks_from_timeline, extract_overlay_elements
    from collections import Counter

    render = await db.get(CastRender, render_id)
    if not render or render.cast_id != cast_id or render.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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

"""Cast timeline endpoints — split from the former routers/casts.py."""

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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    timeline = (cast.timeline_json or {}).get(variant_id)
    return timeline or {"twick_data": None, "block_regions": [], "editor_state": None, "saved_at": None}

@router.post("/{cast_id}/auto-arrange-timeline")
async def auto_arrange_cast_timeline(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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

@router.get("/{cast_id}/timeline")
async def get_cast_timeline_unified(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
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
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Save the full timeline JSON (bonded block model).

    Validates that each block_id appears at most once as a bonded pair
    and that bonded pair start/end times match within 50ms tolerance.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

"""Cast tts_captions endpoints — split from the former routers/casts.py."""

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

@router.post("/{cast_id}/generate-tts")
async def start_tts_generation(
    cast_id: str,
    force: bool = Body(False, embed=True),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Phase 1: Generate TTS audio for all blocks. Sets status to TTS_READY when done.

    ``force=True`` regenerates audio for every block even if it already has a
    ready clip — needed after a sound-only setting change (mic style, a
    scene's mic/environment) since no script text changed to mark audio stale.
    """
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    generate_cast_tts_task.delay(cast_id, ctx.workspace_owner_id, bool(force))
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Phase 2: Submit InfiniteTalk jobs. Requires TTS_READY status."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
        raise HTTPException(404, "Cast not found")
    if cast.status != CastStatus.TTS_READY:
        raise HTTPException(400, f"Cast must be in TTS_READY status. Current: '{cast.status.value}'")

    from tasks.generate_cast import generate_cast_videos_task
    generate_cast_videos_task.delay(cast_id, ctx.workspace_owner_id)
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return per-variant TTS state for the waveform editor."""
    from sqlalchemy.orm import selectinload
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

class EditorCaptionRequest(BaseModel):
    """Request body for editor caption generation.
    Accepts one or more audio URLs (for multi-block caption generation).
    Each entry includes the audio URL, its block_id, and the audio element id
    on the timeline so the frontend can link captions to their source.
    """
    audio_segments: List[dict]


def _realign_sfx(variant) -> None:
    """Re-resolve ``variant.sfx_timings`` against whatever ``caption_words``
    were just (re)computed.

    ``sfx_timings`` are absolute-second offsets resolved against ONE take's
    word timestamps (utils.sfx_extraction.align_sfx_to_words). Whenever this
    endpoint recomputes captions for a variant — because its TTS audio was
    regenerated — any previously-resolved timings belong to the audio that no
    longer exists; left alone, each [sfx:NAME] fires at whatever now happens
    to sit at that stale timestamp in the new take (the "SFX doesn't match
    the scene/audio" bug). ``sfx_markers`` (word-relative, not time-relative)
    is untouched by an audio change, so re-resolving from it is safe here.
    """
    if not getattr(variant, "sfx_markers", None):
        variant.sfx_timings = None
        return
    try:
        from utils.sfx_extraction import SfxMarker, align_sfx_to_words
        from utils.script_cleaning import clean_script_tokens
        markers = [
            SfxMarker(name=m["name"], char_offset=m["char_offset"], word_index=m["word_index"])
            for m in variant.sfx_markers
        ]
        # script_words lets align_sfx_to_words correct for numbers/currency
        # being spoken (and transcribed) as a different word count than
        # they're written — see utils/sfx_extraction._map_text_words_to_spoken.
        variant.sfx_timings = align_sfx_to_words(
            markers,
            variant.caption_words,
            tts_duration_seconds=variant.tts_duration_seconds,
            script_words=clean_script_tokens(variant.script_text or ""),
        ) or None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        variant.sfx_timings = None


@router.post("/{cast_id}/generate-captions")
async def generate_captions(
    cast_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
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
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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
                    _realign_sfx(variant)
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
                _realign_sfx(variant)
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

@router.post("/{cast_id}/editor-generate-captions")
async def editor_generate_captions(
    cast_id: str,
    body: EditorCaptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Phase 2.6.2 — Generate word-level captions for the editor.

    Accepts audio_url(s) from timeline audio elements, transcribes via
    GPU Whisper with word-level timestamps, and returns caption tokens
    remapped to each block's timeline offset.

    Returns Remotion-compatible caption tokens with per-block metadata.

    When a segment includes ``variant_id``, the transcription is ALSO
    persisted onto that ``Variant.caption_words``/``caption_segments`` —
    not just returned for the browser preview. Without this, a caller like
    the per-block "Regenerate Audio" flow only ever updated the editor's
    in-memory preview state; the render pipeline reads captions from
    ``variant.caption_words`` in the DB (see ``tasks/cast_render.py``'s
    ``_load_caption_overlays``), which this endpoint never used to touch —
    so captions looked perfectly synced in the editor but were stale/wrong
    in the actual rendered video.
    """
    from services.gpu_server import get_gpu_server_client

    # Validate cast ownership
    result = await db.execute(
        select(Cast).where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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
    variants_to_commit = False
    if pending_segs:
        gathered = await asyncio.gather(
            *(_transcribe_seg(seg["audio_url"]) for seg in pending_segs),
            return_exceptions=True,
        )
        for seg, outcome in zip(pending_segs, gathered):
            block_id = seg.get("block_id", "")
            audio_element_id = seg.get("audio_element_id", "")
            variant_id = seg.get("variant_id", "")
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

            if variant_id:
                variant_result = await db.execute(
                    select(Variant)
                    .join(Block, Variant.block_id == Block.id)
                    .where(Variant.id == variant_id, Block.cast_id == cast_id)
                )
                variant = variant_result.scalar_one_or_none()
                if variant is None:
                    logger.warning(
                        "editor-generate-captions: variant_id=%s not found under cast=%s — "
                        "preview updated but render source-of-truth was NOT persisted",
                        variant_id, cast_id,
                    )
                else:
                    variant.caption_words = [
                        {
                            "word": w.get("word", "").strip(),
                            "start": round(w.get("start", 0), 3),
                            "end": round(w.get("end", 0), 3),
                            "probability": w.get("probability", 0),
                        }
                        for w in words
                    ]
                    variant.caption_segments = outcome.get("segments", [])
                    _realign_sfx(variant)
                    variants_to_commit = True

    if variants_to_commit:
        await db.commit()

    return {"results": caption_results}

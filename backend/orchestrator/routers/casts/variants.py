"""Cast variants endpoints — split from the former routers/casts.py."""

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
            # casts.audio_stale_since is TIMESTAMP WITHOUT TIME ZONE — asyncpg
            # rejects an offset-aware value ("can't subtract offset-naive and
            # offset-aware datetimes") at commit. Store naive UTC.
            cast.audio_stale_since = datetime.now(tz.utc).replace(tzinfo=None)
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

    # Same script → same [sfx:*] markers for every sibling. Resolve once so
    # the cascaded variants carry the effect too (matches regenerate_block_audio).
    cascade_sfx_markers: list = []
    cascade_sfx_timings: list = []
    try:
        from utils.sfx_extraction import resolve_sfx_for_script
        cascade_sfx_markers, cascade_sfx_timings = resolve_sfx_for_script(
            script_text, None, tts_duration_seconds=tts_result.get("duration_seconds"),
        )
    except Exception as _sfx_exc:
        sentry_sdk.capture_exception(_sfx_exc)

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
                    sfx_markers=cascade_sfx_markers or None,
                    sfx_timings=cascade_sfx_timings or None,
                )
                db.add(cascade_var)

                # Mark sibling cast as having stale audio (Phase 2.5.2).
                # Naive UTC — the column is TIMESTAMP WITHOUT TIME ZONE.
                from datetime import datetime, timezone
                sibling.audio_stale_since = datetime.now(timezone.utc).replace(tzinfo=None)

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

@router.post("/{cast_id}/variants/{variant_id}/retry")
async def retry_variant(
    cast_id: str,
    variant_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Retry a single failed variant."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    generate_cast_task.delay(cast_id, ctx.workspace_owner_id)

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

@router.post("/{cast_id}/blocks/{block_id}/variants")
async def add_variant(
    cast_id: str, block_id: str,
    script_text: str = Body(""),
    variant_label: str = Body("A"),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_no_active_render),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
        _enqueue_tts_regen(cast_id, ctx.workspace_owner_id)
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

class RewriteRequest(BaseModel):
    prompt: str

@router.post("/{cast_id}/blocks/{block_id}/variants/{variant_id}/rewrite")
async def rewrite_variant_script(
    cast_id: str, block_id: str, variant_id: str,
    req: RewriteRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
        _enqueue_tts_regen(cast_id, ctx.workspace_owner_id)
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Refine all active block scripts using Claude. Does NOT regenerate from scratch."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
        _enqueue_tts_regen(cast_id, ctx.workspace_owner_id)
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

class RewriteInVoiceRequest(BaseModel):
    pass  # No additional params needed

@router.post("/{cast_id}/blocks/{block_id}/rewrite-in-voice")
async def rewrite_block_in_voice(
    cast_id: str,
    block_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite a block script using Claude + the avatar voice corpus transcripts."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

class RegenerateAudioRequest(BaseModel):
    script_text: str
    voice_settings: Optional[dict] = None

@router.post("/{cast_id}/blocks/{block_id}/regenerate-audio")
async def regenerate_block_audio(
    cast_id: str,
    block_id: str,
    req: RegenerateAudioRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate TTS audio for a single block. Creates a new active variant."""
    # Validate ownership
    result = await db.execute(
        select(Cast)
        .options(selectinload(Cast.blocks))
        .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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

    # Resolve the mic-style / scene EQ chain the same way the full-cast TTS
    # task does — precedence: per-block mic_on > the block's scene > the
    # avatar-wide default. Without this the single-block regen produced raw
    # TTS with NO mic-style post-processing, so toggling the mic style and
    # hitting this button changed nothing.
    _clip_mic = bool(getattr(avatar, "clip_mic_enabled", False))
    _scene_chain = None
    try:
        from services.mic_presets import resolve_scene_voice_settings
        _look = None
        if getattr(block, "avatar_look_id", None):
            from models.avatar_look import AvatarLook
            _look = await db.get(AvatarLook, block.avatar_look_id)
        _clip_mic, _scene_chain = resolve_scene_voice_settings(
            block_mic_on=getattr(block, "mic_on", None),
            avatar_clip_mic_enabled=bool(getattr(avatar, "clip_mic_enabled", False)),
            look_environment=getattr(_look, "environment", None),
            look_mic_visible=getattr(_look, "mic_visible", None),
        )
    except Exception as _mic_exc:
        sentry_sdk.capture_exception(_mic_exc)

    # Call TTS pipeline
    try:
        from services.fish_audio import get_fish_audio_service
        fish = get_fish_audio_service()
        tts_result = await fish.generate_tts(
            text=req.script_text,
            voice_id=avatar.voice_id,
            clip_mic_enabled=_clip_mic,
            scene_chain_id=_scene_chain,
            block_id=block_id,
        )

        new_variant.audio_key = tts_result["audio_key"]
        new_variant.tts_r2_key = tts_result["audio_key"]
        new_variant.tts_duration_seconds = tts_result["duration_seconds"]
        new_variant.duration_seconds = tts_result["duration_seconds"]
        new_variant.status = VariantStatus.READY

        # Capture [sfx:NAME] markers from the script and resolve them to
        # absolute timings so the effect is mixed into this block's audio
        # track (services/casts/timeline.py reads variant.sfx_timings). The
        # full-cast TTS task does this after its own WhisperX pass; this
        # per-block path has no transcription step, so align falls back to
        # spreading the markers across the clip duration. Without this the
        # tag was silently dropped — TTS strips it and nothing else looked
        # for it. Non-fatal: a failure here must never block the regen.
        try:
            from utils.sfx_extraction import resolve_sfx_for_script
            sfx_markers, sfx_timings = resolve_sfx_for_script(
                req.script_text,
                new_variant.caption_words,
                tts_duration_seconds=new_variant.tts_duration_seconds,
            )
            new_variant.sfx_markers = sfx_markers or None
            new_variant.sfx_timings = sfx_timings or None
        except Exception as _sfx_exc:
            sentry_sdk.capture_exception(_sfx_exc)

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

class RenderBlockRequest(BaseModel):
    render_action: str = "full"  # full | re_gesture | swap_angle
    avatar_angle: Optional[str] = None

@router.post("/{cast_id}/blocks/{block_id}/render")
async def render_block(
    cast_id: str,
    block_id: str,
    req: RenderBlockRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Re-render a single block's video (InfiniteTalk). Returns task_id for polling."""
    try:
        result = await db.execute(
            select(Cast)
            .options(selectinload(Cast.blocks))
            .where(Cast.id == cast_id, Cast.user_id == ctx.workspace_owner_id)
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
        task = generate_cast_videos_task.delay(cast_id, ctx.workspace_owner_id)

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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """List all variants for a block (for variant picker UI)."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Select a variant as active for a block. Deactivates all others."""
    cast = await db.get(Cast, cast_id)
    if not cast or cast.user_id != ctx.workspace_owner_id:
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

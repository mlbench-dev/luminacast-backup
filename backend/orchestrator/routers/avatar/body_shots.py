"""Avatar body_shots endpoints — split from the former routers/avatar.py."""

from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy import update as sa_update
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User, TeamRole
from models.avatar import Avatar, AvatarType, AvatarStatus, BodyShotSet
from models.avatar_look import AvatarLook
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.fish_audio import get_fish_audio_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)
import re

logger = logging.getLogger(__name__)

from ._shared import normalize_tiktok_input, _r2_key_to_url, AvatarResponse, _avatar_to_response, _BODY_MOTION_POSES, _BODY_MOTION_POSE_LABELS, _seed_body_motion_looks_from_body_shot_set

router = APIRouter()

# The six body-shot camera angles + the horizontal_angle passed to fal.ai's
# Qwen "qwen-image-edit-2511-multiple-angles" LoRA for each. SINGLE SOURCE OF
# TRUTH — the two-stage pipeline (_run_body_shots_pipeline) and the per-tile
# regenerate endpoint both read this, so they can't drift apart.
#
# CONVENTION (standard portrait): `three_quarter_left` shows the subject's
# LEFT side of face; `profile_left` shows the subject's LEFT side. The Gemini
# validator uses the same names.
#
# These angle values are correct for a per-shot generation (confirmed on real
# output: single-tile Regenerate produces the right facing). The
# wrong/duplicated-facing shots the batch pipeline was producing were NOT an
# angle-map bug — they came from the pipeline generating all six shots with
# ONE locked seed. This LoRA's rotation is seed-sensitive, so a "bad" seed
# mirrors or collapses the rotation for EVERY angle at once. The pipeline now
# gives each angle its own seed (locked_seed + index) to de-correlate that.
BODY_SHOT_ANGLES = (
    "front", "three_quarter_left", "three_quarter_right",
    "profile_left", "profile_right", "back",
)
FAL_QWEN_ANGLES = {
    "front":               {"horizontal_angle": 0,   "vertical_angle": 0},
    "three_quarter_left":  {"horizontal_angle": 55,  "vertical_angle": 0},
    "three_quarter_right": {"horizontal_angle": 315, "vertical_angle": 0},
    "profile_left":        {"horizontal_angle": 90,  "vertical_angle": 0},
    "profile_right":       {"horizontal_angle": 270, "vertical_angle": 0},
    "back":                {"horizontal_angle": 180, "vertical_angle": 0},
}

# fal Qwen "multiple-angles" framing controls. The LoRA's `zoom` defaults to 5
# = MEDIUM shot (~waist-up crop). That default is why single-tile Regenerate
# (which routes through fal Qwen) and the fal-tier batch shots came back HALF
# body, while the self-hosted Qwen tier (explicit 1024x1792) and the look
# pipeline (full-body FLUX prompts) came back FULL body — same "single is half,
# regenerate-all is full" report. Scale per fal: 0-3 = wide shot (full body),
# 4-6 = medium, 7-10 = close-up. Pin a wide value + a tall 9:16 output on every
# fal Qwen call so rotated shots stay head-to-toe like the canonical.
FAL_QWEN_BODY_ZOOM = 2
FAL_QWEN_BODY_IMAGE_SIZE = {"width": 1024, "height": 1792}

# The Qwen rotation LoRA can't produce a true LEFT and a true RIGHT profile
# (or 3/4) facing opposite ways — it collapses both to the same side. A
# horizontal flip of the left-side shot IS an anatomically-correct right-side
# shot on a plain studio background in plain clothes, so the right-side angles
# are always derived by mirroring, never generated.
BODY_SHOT_MIRROR_FROM = {
    "three_quarter_right": "three_quarter_left",
    "profile_right": "profile_left",
}


def _hflip_jpeg(data: bytes) -> bytes:
    import io as _io
    from PIL import Image as _PILImage
    im = _PILImage.open(_io.BytesIO(data)).convert("RGB")
    buf = _io.BytesIO()
    im.transpose(_PILImage.FLIP_LEFT_RIGHT).save(buf, format="JPEG", quality=92)
    return buf.getvalue()


async def _sync_body_motion_look(db: AsyncSession, avatar_id: str, pose: str, r2_key: str) -> None:
    """Point the Body-Motion ``AvatarLook`` for ``pose`` at ``r2_key`` (create
    it if absent).

    Body-Motion looks are a VIEW of the avatar's Body Shots — the renderer and
    the cast builder read ``AvatarLook`` rows, never ``BodyShotSet`` — so a
    Body-Shot (re)generation MUST propagate here, or Edit-Avatar's "Shots" and
    "Body Motion" tabs end up showing different images for the same pose.
    Mirrors _shared._seed_body_motion_looks_from_body_shot_set's row shape.
    """
    look = (
        await db.execute(
            select(AvatarLook).where(
                AvatarLook.avatar_id == avatar_id,
                AvatarLook.look_type == "body_motion",
                AvatarLook.pose_angle == pose,
            ).limit(1)
        )
    ).scalars().first()
    if look is not None:
        look.face_ref_key = r2_key
        look.status = "ready"
        look.error_message = None
    else:
        label = _BODY_MOTION_POSE_LABELS.get(pose, pose)
        db.add(AvatarLook(
            id=f"al_{uuid.uuid4().hex[:12]}",
            avatar_id=avatar_id,
            name=f"AI: {label}",
            face_ref_key=r2_key,
            background_prompt=f"Body motion pose: {pose}",
            is_default=False,
            is_original=False,
            status="ready",
            look_type="body_motion",
            pose_angle=pose,
        ))
    await db.commit()


class GenerateBodyDescriptionRequest(BaseModel):
    avatar_id: str

@router.post("/ai/generate-body-description")
async def generate_body_description(
    req: GenerateBodyDescriptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a body description from avatar description + target audience.
    Uses LLM to create a consistent body description anchored to the face description."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        if not avatar.face_ref_key:
            raise HTTPException(status_code=400, detail="Face image required before body description")

        from services.openrouter import get_openrouter_service
        from services.ai_prompts import get_prompt

        oai = get_openrouter_service()
        prompt_data = get_prompt("gemini_body_description")

        audience_context = ""
        if avatar.target_audience:
            ta = avatar.target_audience
            audience_context = f"\nTarget audience: {ta.get('age_range', 'general')}, interests: {', '.join(ta.get('interests', []))}, {ta.get('description', '')}"

        # Propagate gender, age range, and other demographic context into the prompt
        demographics = []
        if avatar.gender:
            demographics.append(f"Gender: {avatar.gender}")
        if avatar.target_audience and avatar.target_audience.get("age_range"):
            demographics.append(f"Age range: {avatar.target_audience['age_range']}")
        if avatar.style:
            demographics.append(f"Style: {avatar.style}")
        demographics_str = ("\n" + "\n".join(demographics)) if demographics else ""

        user_msg = f"Avatar description: {avatar.description or 'A professional content creator'}{audience_context}{demographics_str}\n\nDescribe this person's full body appearance for consistent multi-angle image generation."

        log_creative_model_use("avatar_body_description", CREATIVE_DESCRIPTION_MODEL)
        body_desc = await oai.generate_text(
            prompt=user_msg,
            system_prompt=prompt_data["system"],
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=600,
        )

        try:
            from services.usage_tracker import calculate_llm_cost, log_usage
            _u = getattr(oai, "last_usage", {}) or {}
            if _u:
                await log_usage(
                    db,
                    user_id=ctx.workspace_owner_id,                    event_type="script_generation",
                    provider="openrouter",
                    provider_cost_usd=calculate_llm_cost(
                        CREATIVE_DESCRIPTION_MODEL,
                        int(_u.get("prompt_tokens") or 0),
                        int(_u.get("completion_tokens") or 0),
                    ),
                    quantity=int(_u.get("total_tokens") or 0),
                    quantity_unit="tokens",
                    resource_type="avatar",
                    resource_id=avatar.id,
                    provider_model=CREATIVE_DESCRIPTION_MODEL,
                )
                await db.commit()
        except Exception as _exc:
            sentry_sdk.capture_exception(_exc)
        body_desc = body_desc.strip()

        avatar.body_description = body_desc
        await db.commit()

        return {"body_description": body_desc}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"generate-body-description failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])

class GenerateBodyShotsRequest(BaseModel):
    avatar_id: str

async def _run_body_shots_pipeline(set_id: str, avatar_id: str, user_id: str) -> None:
    """Background worker that runs the full body-shot pipeline.

    The HTTP endpoint creates the BodyShotSet row with status='running' and
    returns immediately to avoid Cloudflare's ~100s edge timeout. This worker
    runs on the orchestrator event loop, opens its own DB session, executes
    the full Stage 1 + Stage 2 + validation pipeline, and updates the row to
    status='completed' or status='failed' when done.

    The frontend polls GET /ai/body-shot-sets/{set_id} for state.
    """
    import os
    import random
    import fal_client
    import httpx
    from config import settings as _settings
    from database import async_session_factory
    from services.r2_storage import get_r2_storage_service
    from services.ai_prompts import get_prompt
    from services.openrouter import get_openrouter_service
    from models.avatar import BodyShotSet

    async def _mark_failed(message: str) -> None:
        try:
            async with async_session_factory() as fs:
                row = await fs.get(BodyShotSet, set_id)
                if row is not None:
                    row.status = "failed"
                    row.error_message = message[:500]
                    await fs.commit()
        except Exception as inner:
            sentry_sdk.capture_exception(inner)
            logger.error(f"_mark_failed failed for set {set_id}: {inner}")

    db = async_session_factory()
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar:
            await _mark_failed("Avatar not found")
            return

        r2 = get_r2_storage_service()
        if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
            os.environ["FAL_KEY"] = _settings.FAL_API_KEY

        body_desc = avatar.body_description
        style_hint = ""
        if avatar.target_audience and isinstance(avatar.target_audience, dict):
            presets = avatar.target_audience.get("style_presets", [])
            if presets:
                style_hint = f"Style: {', '.join(presets)}"

        # ── Pre-Stage-1: Vision-extract wardrobe from the SELECTED face image ──
        # The setup-time body_description was generated before the user picked a
        # face, so its clothing/glasses fields are stale once they edit or pick a
        # different look. Source-of-truth for wardrobe must be the actual face
        # image at avatar.face_ref_key — that's what the user sees when they
        # hit "approve face". We extract wardrobe + has_glasses via the vision
        # LLM and use that as the clothing string for Stage 1, OVERRIDING any
        # clothing sentences found in body_description. Body attributes
        # (build/height/posture) still come from body_description.
        face_ref_url = r2.get_public_url(avatar.face_ref_key)
        face_wardrobe_summary: str | None = None
        face_wardrobe_extracted: dict | None = None
        try:
            extractor_prompt_data = get_prompt("face_wardrobe_extractor")
            openrouter_for_wardrobe = get_openrouter_service()
            face_ref_public = r2.get_public_url(avatar.face_ref_key, cache_bust=True)
            raw_wardrobe = await openrouter_for_wardrobe.describe_image(
                image_url=face_ref_public,
                system_prompt=extractor_prompt_data["system"],
                user_text=(
                    "Extract the wardrobe and accessories the person is wearing in this image. "
                    "Pay special attention to glasses (yes/no) and the top garment (suit, blazer, "
                    "hoodie, t-shirt, dress, etc.). Respond with ONLY the JSON object."
                ),
                model=CREATIVE_DESCRIPTION_MODEL,
                max_tokens=512,
                temperature=0.0,
            )
            try:
                from services.usage_tracker import calculate_llm_cost, log_usage
                _u = getattr(openrouter_for_wardrobe, "last_usage", {}) or {}
                if _u:
                    async with async_session_factory() as _usg_session:
                        await log_usage(
                            _usg_session,
                            user_id=user_id,
                            event_type="vision_check",
                            provider="openrouter",
                            provider_cost_usd=calculate_llm_cost(
                                CREATIVE_DESCRIPTION_MODEL,
                                int(_u.get("prompt_tokens") or 0),
                                int(_u.get("completion_tokens") or 0),
                            ),
                            quantity=int(_u.get("total_tokens") or 0),
                            quantity_unit="tokens",
                            resource_type="avatar",
                            resource_id=avatar.id,
                            provider_model=CREATIVE_DESCRIPTION_MODEL,
                        )
                        await _usg_session.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)
            cleaned_w = raw_wardrobe.strip()
            if cleaned_w.startswith("```"):
                _lines = cleaned_w.splitlines()
                cleaned_w = (
                    "\n".join(_lines[1:-1])
                    if _lines and _lines[-1].strip() == "```"
                    else "\n".join(_lines[1:])
                )
            import json as _json_w
            face_wardrobe_extracted = _json_w.loads(cleaned_w)
            summary = (face_wardrobe_extracted.get("wardrobe_summary") or "").strip()
            has_glasses = bool(face_wardrobe_extracted.get("has_glasses"))
            glasses_desc = (face_wardrobe_extracted.get("glasses_description") or "").strip()
            if has_glasses and glasses_desc and "glasses" not in summary.lower():
                summary = f"{summary} wearing {glasses_desc}.".strip()
            elif (not has_glasses) and ("no glasses" not in summary.lower()):
                summary = f"{summary} No glasses, no eyewear.".strip()
            face_wardrobe_summary = summary or None
            logger.info(
                f"Vision wardrobe extracted for avatar {avatar.id}: "
                f"has_glasses={has_glasses}, summary={summary[:120]}"
            )
        except Exception as wardrobe_exc:
            sentry_sdk.capture_exception(wardrobe_exc)
            logger.warning(
                f"Face wardrobe extraction failed for avatar {avatar.id}: {wardrobe_exc} — "
                "falling back to body_description clothing sentences."
            )

        # Extract clothing-only sentences from body_desc as a FALLBACK only.
        # body_description contains body type, posture, personality, facial features
        # AND clothing. Only the clothing matters for Stage 1 — identity comes from
        # the face_ref image. Long descriptive text overrides Kontext Max's identity
        # preservation, so we keep it minimal: clothing + accessories only.
        _clothing_kw = re.compile(
            r"\b(?:wear(?:s|ing)?|blous\w*|shirts?|sweaters?|jackets?|coats?"
            r"|dress(?:es)?|skirts?|pants|trousers|jeans|shorts"
            r"|shoes|sneakers|boots|heels|sandals"
            r"|necklaces?|earrings?|bracelets?|watch(?:es)?"
            r"|glasses|sunglasses|pendants?|hoops?"
            r"|hats?|scarves?|scarfs?|belts?|outfits?|clothing"
            r"|cotton|silk|linen|denim|leather|wool|knit"
            r"|sleeves?|cardigan|hoodie)\b",
            re.IGNORECASE,
        )
        _desc_sentences = re.split(r"(?<=[.!?])\s+", body_desc.strip()) if body_desc else []
        _clothing_sentences = [s for s in _desc_sentences if _clothing_kw.search(s)]
        body_desc_clothing_fallback = " ".join(_clothing_sentences) if _clothing_sentences else (body_desc or "")

        # Vision-extracted wardrobe is source of truth. body_description fallback
        # is only used when the vision pass fails.
        body_desc_clothing = face_wardrobe_summary or body_desc_clothing_fallback

        # body_desc_for_angles is what gets fed into Stage 2 per-angle prompts
        # AND surfaced to the UI via BodyShotSet.description_used. When the
        # vision pass succeeded we use ONLY the grounded wardrobe summary —
        # the original body_description is a creative-writing string from
        # avatar setup that often hallucinates accessories ("messenger bag with
        # enamel pins", "leather-bound journal") that are not in the actual
        # photo, and concatenating it back in defeats the purpose of the
        # vision pre-pass and produces hallucinated body shots.
        if face_wardrobe_summary:
            body_desc_for_angles = face_wardrobe_summary
        else:
            body_desc_for_angles = body_desc or ""

        locked_seed = random.randint(1, 999999)
        # set_id is created by the HTTP endpoint and passed in

        # ── Stage 1: Canonical full-body front via FLUX Kontext Max (face-anchored) ──
        # Uses avatar's face_ref as image_url so identity is preserved.
        # Kontext Max preserves identity (hair, eyes, face) from the reference
        # while applying clothing from the prompt. Previous attempts with
        # kontext (non-max) and flux-pro/v1.1-ultra lost identity.
        # face_ref_url already resolved above (used by the wardrobe extractor).
        canonical_prompt_data = get_prompt("flux_body_canonical_front")
        canonical_prompt = canonical_prompt_data["system"].format(
            body_description=body_desc_clothing,
        )

        from services.nano_banana import (
            nano_banana_pro_enabled, edit_image_run_async,
        )
        _nano_enabled = nano_banana_pro_enabled()

        async def _generate_canonical(prompt_text: str, seed: int) -> bytes | None:
            engine = "Nano Banana Pro" if _nano_enabled else "Kontext Max"
            logger.info(
                f"Stage 1: Generating canonical front for avatar {avatar.id} via {engine} "
                f"(seed={seed}, prompt_chars={len(prompt_text)})"
            )
            if _nano_enabled:
                # NBP follows the "natural proportions / not an oversized head"
                # instruction literally — this is the head-to-body-ratio fix.
                # aspect_ratio pinned to 9:16 to match the .jpg storage + the
                # Stage-2 angle crop.
                img_url = await edit_image_run_async(
                    prompt_text, [face_ref_url], aspect_ratio="9:16",
                )
            else:
                # safety_tolerance=5 unlocks the highest available detail
                # without changing the model. Keep JPEG to match the .jpg key.
                result = await fal_client.run_async(
                    "fal-ai/flux-pro/kontext/max",
                    arguments={
                        "prompt": prompt_text,
                        "image_url": face_ref_url,
                        "num_images": 1,
                        "output_format": "jpeg",
                        "seed": seed,
                        "aspect_ratio": "9:16",
                        "safety_tolerance": "5",
                    },
                )
                imgs = result.get("images", [])
                if not imgs:
                    return None
                img_url = imgs[0]["url"]
            async with httpx.AsyncClient() as cli:
                resp = await cli.get(img_url, timeout=60)
                resp.raise_for_status()
                return resp.content

        canonical_bytes = await _generate_canonical(canonical_prompt, locked_seed)
        if not canonical_bytes:
            await _mark_failed("Stage 1 failed: no canonical image generated")
            return

        try:
            from services.usage_tracker import calculate_fal_image_cost, log_usage
            async with async_session_factory() as _usg_session:
                await log_usage(
                    _usg_session,
                    user_id=user_id,
                    event_type="body_shot_generation",
                    provider="fal_ai",
                    provider_cost_usd=calculate_fal_image_cost("kontext/max", 1),
                    quantity=1,
                    quantity_unit="images",
                    resource_type="avatar",
                    resource_id=avatar.id,
                    provider_model="flux-pro/kontext/max",
                )
                await _usg_session.commit()
        except Exception as _exc:
            sentry_sdk.capture_exception(_exc)

        canonical_key = f"creators/{user_id}/avatar/{avatar.id}/body_shots/{set_id}/canonical.jpg"
        await r2.upload_bytes(canonical_bytes, canonical_key, "image/jpeg")
        canonical_url = r2.get_public_url(canonical_key)

        logger.info(f"Stage 1 complete: canonical saved to {canonical_key}")

        # ── Stage 1.5: AI clothing-consistency check ──
        # Sends face reference + canonical body shot to a vision LLM. Asks whether
        # the clothing/hair/accessories match. If not, regenerates the canonical
        # ONCE with a more explicit prompt that enumerates the discrepancies.
        # After one retry we accept whatever we got (logging mismatches via
        # Sentry) so the user is never blocked.
        clothing_consistency_warning: str | None = None
        clothing_check: dict | None = None
        try:
            check_prompt_data = get_prompt("body_shot_clothing_consistency_check")
            openrouter_for_check = get_openrouter_service()
            face_ref_public = r2.get_public_url(avatar.face_ref_key, cache_bust=True)
            canonical_public = r2.get_public_url(canonical_key, cache_bust=True)

            user_msg = (
                "Image 1 is the face reference photo. Image 2 is the generated body shot. "
                "Does the person in image 2 wear the same clothing, hairstyle, and accessories "
                "as the person in image 1? Answer with the JSON schema you were given."
            )
            raw = await openrouter_for_check.compare_two_images(
                image_url_a=face_ref_public,
                image_url_b=canonical_public,
                system_prompt=check_prompt_data["system"],
                user_text=user_msg,
                model="anthropic/claude-sonnet-4",
                max_tokens=512,
                temperature=0.0,
            )
            try:
                from services.usage_tracker import calculate_llm_cost, log_usage
                _u = getattr(openrouter_for_check, "last_usage", {}) or {}
                if _u:
                    async with async_session_factory() as _usg_session:
                        await log_usage(
                            _usg_session,
                            user_id=user_id,
                            event_type="vision_check",
                            provider="openrouter",
                            provider_cost_usd=calculate_llm_cost(
                                "anthropic/claude-sonnet-4",
                                int(_u.get("prompt_tokens") or 0),
                                int(_u.get("completion_tokens") or 0),
                            ),
                            quantity=int(_u.get("total_tokens") or 0),
                            quantity_unit="tokens",
                            resource_type="avatar",
                            resource_id=avatar.id,
                            provider_model="anthropic/claude-sonnet-4",
                        )
                        await _usg_session.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                _lines = cleaned.splitlines()
                cleaned = (
                    "\n".join(_lines[1:-1])
                    if _lines and _lines[-1].strip() == "```"
                    else "\n".join(_lines[1:])
                )
            try:
                import json as _json
                parsed = _json.loads(cleaned)
            except Exception as parse_exc:
                sentry_sdk.capture_exception(parse_exc)
                logger.warning(f"AI clothing check returned non-JSON: {raw[:200]}")
                parsed = {"matches": True, "discrepancies": []}

            matches = bool(parsed.get("matches", True))
            discrepancies = parsed.get("discrepancies") or []
            if not isinstance(discrepancies, list):
                discrepancies = [str(discrepancies)]
            clothing_check = {
                "matches": matches,
                "discrepancies": discrepancies,
                "retry_attempted": False,
            }
            logger.info(
                f"AI clothing check (set {set_id}): matches={matches}, "
                f"discrepancies={discrepancies}"
            )

            if not matches:
                # One explicit retry with the discrepancies fed back into the prompt.
                discrepancy_text = "; ".join(str(d) for d in discrepancies) or "clothing or accessories did not match the reference photo"
                retry_prompt = (
                    f"{canonical_prompt}\n\n"
                    f"The previous attempt got these things wrong: {discrepancy_text}. "
                    f"Generate again, paying special attention to those details and matching the reference photo exactly."
                )
                retry_seed = random.randint(1, 999999)
                logger.info(
                    f"AI clothing check failed for set {set_id} — regenerating canonical once "
                    f"(seed={retry_seed})"
                )
                retry_bytes = await _generate_canonical(retry_prompt, retry_seed)
                if retry_bytes:
                    try:
                        from services.usage_tracker import calculate_fal_image_cost, log_usage
                        async with async_session_factory() as _usg_session:
                            await log_usage(
                                _usg_session,
                                user_id=user_id,
                                event_type="body_shot_generation",
                                provider="fal_ai",
                                provider_cost_usd=calculate_fal_image_cost("kontext/max", 1),
                                quantity=1,
                                quantity_unit="images",
                                resource_type="avatar",
                                resource_id=avatar.id,
                                provider_model="flux-pro/kontext/max",
                            )
                            await _usg_session.commit()
                    except Exception as _exc:
                        sentry_sdk.capture_exception(_exc)
                    await r2.upload_bytes(retry_bytes, canonical_key, "image/jpeg")
                    canonical_url = r2.get_public_url(canonical_key)
                    canonical_public_retry = r2.get_public_url(canonical_key, cache_bust=True)
                    # Re-check after the retry, but accept whatever we get.
                    try:
                        raw2 = await openrouter_for_check.compare_two_images(
                            image_url_a=face_ref_public,
                            image_url_b=canonical_public_retry,
                            system_prompt=check_prompt_data["system"],
                            user_text=user_msg,
                            model="anthropic/claude-sonnet-4",
                            max_tokens=512,
                            temperature=0.0,
                        )
                        try:
                            from services.usage_tracker import calculate_llm_cost, log_usage
                            _u2 = getattr(openrouter_for_check, "last_usage", {}) or {}
                            if _u2:
                                async with async_session_factory() as _usg_session:
                                    await log_usage(
                                        _usg_session,
                                        user_id=user_id,
                                        event_type="vision_check",
                                        provider="openrouter",
                                        provider_cost_usd=calculate_llm_cost(
                                            "anthropic/claude-sonnet-4",
                                            int(_u2.get("prompt_tokens") or 0),
                                            int(_u2.get("completion_tokens") or 0),
                                        ),
                                        quantity=int(_u2.get("total_tokens") or 0),
                                        quantity_unit="tokens",
                                        resource_type="avatar",
                                        resource_id=avatar.id,
                                        provider_model="anthropic/claude-sonnet-4",
                                    )
                                    await _usg_session.commit()
                        except Exception as _exc:
                            sentry_sdk.capture_exception(_exc)
                        cleaned2 = raw2.strip()
                        if cleaned2.startswith("```"):
                            _lines2 = cleaned2.splitlines()
                            cleaned2 = (
                                "\n".join(_lines2[1:-1])
                                if _lines2 and _lines2[-1].strip() == "```"
                                else "\n".join(_lines2[1:])
                            )
                        import json as _json
                        parsed2 = _json.loads(cleaned2)
                        matches2 = bool(parsed2.get("matches", True))
                        discrepancies2 = parsed2.get("discrepancies") or []
                        if not isinstance(discrepancies2, list):
                            discrepancies2 = [str(discrepancies2)]
                        clothing_check = {
                            "matches": matches2,
                            "discrepancies": discrepancies2,
                            "retry_attempted": True,
                            "first_attempt_discrepancies": discrepancies,
                        }
                        if not matches2:
                            clothing_consistency_warning = "clothing_mismatch_after_retry"
                            sentry_sdk.capture_message(
                                f"AI clothing check still failing after retry for set {set_id}: {discrepancies2}",
                                level="warning",
                            )
                    except Exception as recheck_exc:
                        sentry_sdk.capture_exception(recheck_exc)
                        logger.warning(f"Re-check after retry failed: {recheck_exc}")
                        clothing_check = {
                            "matches": False,
                            "discrepancies": discrepancies,
                            "retry_attempted": True,
                            "recheck_error": str(recheck_exc)[:200],
                        }
                        clothing_consistency_warning = "clothing_recheck_failed"
                else:
                    sentry_sdk.capture_message(
                        f"AI clothing-check retry generation failed for set {set_id}",
                        level="warning",
                    )
                    clothing_check = {
                        "matches": False,
                        "discrepancies": discrepancies,
                        "retry_attempted": True,
                        "retry_generation_failed": True,
                    }
                    clothing_consistency_warning = "clothing_retry_generation_failed"
        except Exception as check_exc:
            sentry_sdk.capture_exception(check_exc)
            logger.error(f"AI clothing check skipped for set {set_id}: {check_exc}")
            clothing_consistency_warning = "clothing_check_unavailable"
            clothing_check = {"error": str(check_exc)[:200]}

        # ── Stage 2: 6 angles with 3-tier fallback ──
        # Tier 1: Self-hosted Qwen on HOSTKEY GPU
        # Tier 2: fal.ai hosted Qwen
        # Tier 3: FLUX Kontext legacy fallback
        from services.qwen_body_shots_client import QwenBodyShotsClient

        # Angle list + fal.ai Qwen angle map are module-level constants
        # (BODY_SHOT_ANGLES / FAL_QWEN_ANGLES) so the pipeline and the
        # per-tile regenerate endpoint can't drift apart — see the block at
        # the top of this file for the convention + why the values are what
        # they are.
        ANGLES = BODY_SHOT_ANGLES

        def _build_kontext_prompt(angle: str) -> str:
            """Legacy FLUX Kontext prompt builder (Tier 3 fallback).

            Uses body_desc_for_angles which has the vision-extracted wardrobe
            (including explicit has_glasses flag) prepended to body_description.
            """
            if angle == "front":
                tmpl = get_prompt("flux_body_kontext_angle_front")
                return tmpl["system"].format(body_description=body_desc_for_angles)
            elif angle.startswith("three_quarter_"):
                # Convention: `three_quarter_left` shows the subject's LEFT side
                # of face, which means the subject's body is rotated camera-RIGHT
                # and the subject's RIGHT shoulder is closer to camera. So the
                # `{direction}` token in the prompt template — which names the
                # shoulder closer to camera — is the OPPOSITE of the angle suffix.
                shoulder_direction = "right" if angle == "three_quarter_left" else "left"
                tmpl = get_prompt("flux_body_kontext_angle_three_quarter")
                return tmpl["system"].format(body_description=body_desc_for_angles, direction=shoulder_direction)
            elif angle.startswith("profile_"):
                direction = "left" if angle == "profile_left" else "right"
                tmpl = get_prompt("flux_body_kontext_angle_profile")
                return tmpl["system"].format(body_description=body_desc_for_angles, direction=direction)
            elif angle == "back":
                tmpl = get_prompt("flux_body_kontext_angle_back")
                return tmpl["system"].format(body_description=body_desc_for_angles)
            else:
                raise ValueError(f"Unknown angle: {angle}")

        # HOSTKEY is decommissioned: skip the self-hosted Qwen tier entirely
        # when the kill-switch is set (the default) so body shots route
        # straight to fal.ai hosted Qwen (Tier 2) → FLUX Kontext (Tier 3).
        from services.hostkey_flags import hostkey_disabled, log_hostkey_skip
        qwen_client = QwenBodyShotsClient(_settings.HOSTKEY_GPU_URL)
        if hostkey_disabled():
            log_hostkey_skip("fal.ai qwen-image-edit (body shots)")
            qwen_self_hosted_ok = False
        else:
            qwen_self_hosted_ok = await qwen_client.health()
        logger.info(f"Qwen self-hosted health: {qwen_self_hosted_ok}")

        angles_dict = {}
        front_shot_key = None
        # Raw bytes of each generated shot, kept so a right-side angle can be
        # mirrored from its left-side twin without a round-trip to R2.
        _shot_bytes: dict = {}

        # ANGLES order already puts each *_left before its *_right, so the
        # mirror source is always in _shot_bytes by the time we need it.
        for _angle_idx, angle in enumerate(ANGLES):
            img_bytes = None
            engine_used = None

            # Right-side angles are mirrored from their left-side twin, never
            # generated — see BODY_SHOT_MIRROR_FROM.
            src_angle = BODY_SHOT_MIRROR_FROM.get(angle)
            if src_angle and _shot_bytes.get(src_angle):
                try:
                    img_bytes = _hflip_jpeg(_shot_bytes[src_angle])
                    engine_used = "mirror"
                    logger.info(f"Angle {angle}: mirrored from {src_angle}")
                except Exception as _mex:
                    sentry_sdk.capture_exception(_mex)
                    logger.warning(f"Mirror {angle} from {src_angle} failed, generating directly: {_mex}")
                    img_bytes = None
            # Per-angle seed. The rotation LoRA is seed-sensitive — a single
            # locked seed across all six shots made a "bad" seed mirror /
            # collapse the facing for the WHOLE batch (the "all six face the
            # same way, but single Regenerate is correct" report). Deriving
            # each angle's seed from the batch seed keeps runs reproducible
            # while de-correlating the failure.
            angle_seed = locked_seed + _angle_idx

            # Tier 1: self-hosted Qwen on HOSTKEY
            if qwen_self_hosted_ok:
                try:
                    img_bytes = await qwen_client.generate(
                        reference_image_url=canonical_url,
                        angle=angle,
                        output_width=1024,
                        output_height=1792,
                        seed=angle_seed,
                    )
                    engine_used = "qwen_self_hosted"
                    logger.info(f"Tier 1 (Qwen self-hosted) succeeded for angle {angle}")
                except Exception as e:
                    logger.warning(f"Tier 1 (Qwen self-hosted) failed for angle {angle}: {e}")
                    sentry_sdk.capture_exception(e)

            # Tier 2: fal.ai hosted Qwen (uses numeric angle params, not text prompts)
            if img_bytes is None:
                try:
                    fal_angles = FAL_QWEN_ANGLES[angle]
                    fal_result = await fal_client.run_async(
                        "fal-ai/qwen-image-edit-2511-multiple-angles",
                        arguments={
                            "image_urls": [canonical_url],
                            "horizontal_angle": fal_angles["horizontal_angle"],
                            "vertical_angle": fal_angles["vertical_angle"],
                            # Wide framing + tall output so the rotated shot is
                            # head-to-toe, not the LoRA's default waist-up crop.
                            "zoom": FAL_QWEN_BODY_ZOOM,
                            "image_size": FAL_QWEN_BODY_IMAGE_SIZE,
                            "seed": angle_seed,
                            # Higher steps + guidance = sharper, better fabric/skin
                            # detail at the cost of ~30% extra compute per shot.
                            "num_inference_steps": 40,
                            "guidance_scale": 5.0,
                            "output_format": "jpeg",
                        },
                    )
                    async with httpx.AsyncClient() as client:
                        r = await client.get(fal_result["images"][0]["url"], timeout=60)
                        r.raise_for_status()
                        img_bytes = r.content
                    engine_used = "qwen_fal"
                    logger.info(f"Tier 2 (Qwen fal.ai) succeeded for angle {angle}")
                except Exception as e:
                    logger.warning(f"Tier 2 (Qwen fal.ai) failed for angle {angle}: {e}")
                    sentry_sdk.capture_exception(e)

            # Tier 3: text-prompt fallback (Qwen numeric-angle tier failed).
            # Nano Banana Pro when enabled — better prompt adherence + anatomy
            # than FLUX Kontext for a prompt-driven rotation.
            if img_bytes is None:
                try:
                    prompt = _build_kontext_prompt(angle)
                    if _nano_enabled:
                        img_url = await edit_image_run_async(
                            prompt, [canonical_url], aspect_ratio="9:16",
                        )
                        engine_used = "nano_banana_pro"
                    else:
                        result = await fal_client.run_async(
                            "fal-ai/flux-pro/kontext",
                            arguments={
                                "prompt": prompt,
                                "image_urls": [canonical_url],
                                "guidance_scale": 4.0,
                                "num_inference_steps": 40,
                                "output_format": "jpeg",
                                "seed": angle_seed,
                                "aspect_ratio": "9:16",
                                "safety_tolerance": "5",
                            },
                        )
                        images = result.get("images", [])
                        img_url = images[0]["url"] if images else None
                        engine_used = "flux_kontext_legacy"
                    if img_url:
                        async with httpx.AsyncClient() as client:
                            img_resp = await client.get(img_url, timeout=60)
                            img_resp.raise_for_status()
                            img_bytes = img_resp.content
                        logger.info(f"Tier 3 ({engine_used}) succeeded for angle {angle}")
                except Exception as e:
                    logger.error(f"All engines failed for angle {angle}: {e}")
                    sentry_sdk.capture_exception(e)
                    continue

            if img_bytes is None:
                logger.error(f"All tiers failed for angle {angle}, skipping")
                continue

            r2_key = f"creators/{user_id}/avatar/{avatar.id}/body_shots/{set_id}/{angle}.jpg"
            await r2.upload_bytes(img_bytes, r2_key, "image/jpeg")
            angles_dict[angle] = r2_key
            _shot_bytes[angle] = img_bytes  # source for a later mirror

            if angle == "front":
                front_shot_key = r2_key

            try:
                from services.usage_tracker import calculate_fal_image_cost, log_usage
                if engine_used == "mirror":
                    _provider = "internal"
                    _cost = 0.0
                    _model = "mirror"
                elif engine_used == "qwen_self_hosted":
                    _provider = "hostkey"
                    _cost = 0.0
                    _model = "qwen-body-shots"
                elif engine_used == "qwen_fal":
                    _provider = "fal_ai"
                    _cost = calculate_fal_image_cost("qwen", 1)
                    _model = "qwen-image-edit-2511-multiple-angles"
                else:
                    _provider = "fal_ai"
                    _cost = calculate_fal_image_cost("kontext", 1)
                    _model = "flux-pro/kontext"
                async with async_session_factory() as _usg_session:
                    await log_usage(
                        _usg_session,
                        user_id=user_id,
                        event_type="body_shot_generation",
                        provider=_provider,
                        provider_cost_usd=_cost,
                        quantity=1,
                        quantity_unit="images",
                        resource_type="avatar",
                        resource_id=avatar.id,
                        provider_model=_model,
                    )
                    await _usg_session.commit()
            except Exception as _exc:
                sentry_sdk.capture_exception(_exc)

        if not front_shot_key:
            await _mark_failed("Failed to generate front body shot")
            return

        missing_angles = [a for a in ANGLES if a not in angles_dict]
        if missing_angles:
            await _mark_failed(
                f"Failed to generate {len(missing_angles)}/{len(ANGLES)} angle(s): "
                f"{', '.join(missing_angles)}"
            )
            return

        # ── Stage 3: Gemini Vision Validation ──
        validation_results = {}
        validation_prompt_data = get_prompt("gemini_body_shot_validation")
        openrouter = get_openrouter_service()

        for angle, r2_key in angles_dict.items():
            try:
                shot_url = r2.get_public_url(r2_key)
                user_msg = f"Image URL: {shot_url}\n\nClassify the camera angle of this body shot photo."
                classified = await openrouter.generate_text(
                    prompt=user_msg,
                    system_prompt=validation_prompt_data["system"],
                    model="anthropic/claude-sonnet-4",
                    max_tokens=32,
                    temperature=0.0,
                )
                classified_angle = classified.strip().lower().replace(" ", "_")
                try:
                    from services.usage_tracker import calculate_llm_cost, log_usage
                    _u = getattr(openrouter, "last_usage", {}) or {}
                    if _u:
                        async with async_session_factory() as _usg_session:
                            await log_usage(
                                _usg_session,
                                user_id=user_id,
                                event_type="vision_check",
                                provider="openrouter",
                                provider_cost_usd=calculate_llm_cost(
                                    "anthropic/claude-sonnet-4",
                                    int(_u.get("prompt_tokens") or 0),
                                    int(_u.get("completion_tokens") or 0),
                                ),
                                quantity=int(_u.get("total_tokens") or 0),
                                quantity_unit="tokens",
                                resource_type="avatar",
                                resource_id=avatar.id,
                                provider_model="anthropic/claude-sonnet-4",
                            )
                            await _usg_session.commit()
                except Exception as _exc:
                    sentry_sdk.capture_exception(_exc)
                is_match = classified_angle == angle
                validation_results[angle] = {
                    "expected": angle,
                    "classified": classified_angle,
                    "match": is_match,
                }
                if not is_match:
                    logger.warning(
                        f"Angle validation mismatch: expected={angle}, classified={classified_angle}"
                    )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Angle validation failed for {angle}: {e}")
                validation_results[angle] = {
                    "expected": angle,
                    "classified": "unknown",
                    "match": False,
                }

        mismatches = sum(1 for v in validation_results.values() if not v["match"])
        logger.info(
            f"Body shot validation: {len(validation_results)} checked, "
            f"{mismatches} mismatches for set {set_id}"
        )

        # Update the existing BodyShotSet row created by the HTTP endpoint.
        # Mark complete with all artifacts.
        bss = await db.get(BodyShotSet, set_id)
        if bss is None:
            # Should never happen — the endpoint creates the row before scheduling
            # this task. Defensive only.
            logger.error(f"BodyShotSet {set_id} disappeared mid-pipeline")
            await _mark_failed("Internal: BodyShotSet row missing")
            return
        bss.front_shot_key = front_shot_key
        bss.angles = angles_dict
        # Persist the wardrobe-aware description that was actually fed to the
        # angle prompts, not the stale setup-time body_description, so the
        # wizard's left panel reflects what was used (e.g. suit + no glasses
        # rather than the original hoodie text).
        bss.description_used = body_desc_for_angles or body_desc
        bss.canonical_key = canonical_key
        # Merge per-angle validation with clothing-consistency result + warning flag.
        merged_validation: dict = dict(validation_results)
        if clothing_check is not None:
            merged_validation["clothing_consistency"] = clothing_check
        if clothing_consistency_warning is not None:
            merged_validation["clothing_consistency_warning"] = clothing_consistency_warning
        if face_wardrobe_extracted is not None:
            merged_validation["face_wardrobe_extracted"] = face_wardrobe_extracted
        bss.validation = merged_validation
        bss.status = "completed"
        bss.error_message = None
        await db.commit()
        logger.info(
            f"BodyShotSet {set_id} completed: {len(angles_dict)} angles, "
            f"clothing_consistency_warning={clothing_consistency_warning}"
        )

        # Seed body_motion AvatarLook rows immediately — Edit Avatar's Body
        # Motion tab, the cast builder, and try-on all read AvatarLook, not
        # BodyShotSet. Previously this only happened on avatar approval, so
        # body shots generated but not yet (or never) approved were invisible
        # everywhere except this wizard, forcing a pointless regeneration.
        seeded = await _seed_body_motion_looks_from_body_shot_set(db, avatar_id)
        if seeded:
            await db.commit()
            logger.info("Seeded %d body_motion AvatarLook row(s) for avatar %s", seeded, avatar_id)

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"generate-body-shots pipeline failed for set {set_id}: {e}")
        await _mark_failed(str(e))
    finally:
        try:
            await db.close()
        except Exception:
            pass

@router.post("/ai/generate-body-shots")
async def generate_body_shots(
    req: GenerateBodyShotsRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Kick off the body shots pipeline as a background job.

    Returns immediately with the new set_id and status='running'. The frontend
    polls GET /api/avatar/ai/body-shot-sets/{set_id} for completion. The actual
    work runs in _run_body_shots_pipeline on the orchestrator's event loop.

    Why this is async: the pipeline takes ~60s (Stage 1 canonical generation,
    6 angle generations, 6 vision validations). A blocking HTTP request was
    being killed by Cloudflare's ~100s edge timeout.
    """
    import asyncio
    from models.avatar import BodyShotSet

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if not avatar.face_ref_key:
        raise HTTPException(status_code=400, detail="Face image required")
    if not avatar.body_description:
        raise HTTPException(status_code=400, detail="Body description required first")

    set_id = f"bss_{uuid.uuid4().hex[:12]}"
    bss = BodyShotSet(
        id=set_id,
        avatar_id=avatar.id,
        status="running",
    )
    db.add(bss)
    await db.commit()

    # Schedule background work. asyncio.create_task runs on the same event loop
    # as the request handler; once we return, the response is sent and the
    # task continues. The task opens its own DB session.
    asyncio.create_task(_run_body_shots_pipeline(set_id, avatar.id, ctx.workspace_owner_id))

    return {"set_id": set_id, "status": "running"}

@router.get("/ai/body-shot-sets/{set_id}")
async def get_body_shot_set(
    set_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Poll endpoint for the body shots async job.

    Returns the current status and, when complete, the angle URLs for display.
    Status is one of: 'running', 'completed', 'failed'.
    """
    from services.r2_storage import get_r2_storage_service
    from models.avatar import BodyShotSet

    bss = await db.get(BodyShotSet, set_id)
    if bss is None:
        raise HTTPException(status_code=404, detail="Body shot set not found")

    avatar = await db.get(Avatar, bss.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Body shot set not found")

    r2 = get_r2_storage_service()
    payload = {
        "set_id": bss.id,
        "status": bss.status or ("completed" if bss.angles else "running"),
    }
    if bss.error_message:
        payload["error"] = bss.error_message
    if bss.angles:
        payload["angles"] = {k: r2.get_public_url(v) for k, v in bss.angles.items()}
    if bss.front_shot_key:
        payload["front_shot_url"] = r2.get_public_url(bss.front_shot_key)
    if bss.canonical_key:
        payload["canonical_url"] = r2.get_public_url(bss.canonical_key)
    if bss.validation:
        payload["validation"] = bss.validation
    if bss.description_used:
        payload["description_used"] = bss.description_used
    return payload

@router.get("/{avatar_id}/latest-body-shot-set")
async def get_latest_body_shot_set(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent BodyShotSet for an avatar, or null if none.

    The frontend uses this to decide whether to kick off a NEW body-shot
    job on mount, or just hydrate the UI from an already-completed (or
    in-flight) set. Stops the
    “navigate-away-and-come-back-regenerates-everything” footgun.
    """
    from services.r2_storage import get_r2_storage_service
    from models.avatar import BodyShotSet
    from sqlalchemy import select as _select

    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    row = (
        await db.execute(
            _select(BodyShotSet)
            .where(BodyShotSet.avatar_id == avatar_id)
            .order_by(BodyShotSet.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        return {"set": None}

    r2 = get_r2_storage_service()
    payload: dict = {
        "set_id": row.id,
        "status": row.status or ("completed" if row.angles else "running"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if row.error_message:
        payload["error"] = row.error_message
    if row.angles:
        payload["angles"] = {k: r2.get_public_url(v) for k, v in row.angles.items()}
    if row.front_shot_key:
        payload["front_shot_url"] = r2.get_public_url(row.front_shot_key)
    if row.canonical_key:
        payload["canonical_url"] = r2.get_public_url(row.canonical_key)
    if row.validation:
        payload["validation"] = row.validation
    if row.description_used:
        payload["description_used"] = row.description_used
    return {"set": payload}

class RegenerateBodyShotRequest(BaseModel):
    avatar_id: str
    set_id: str
    angle: str

@router.post("/ai/regenerate-body-shot")
async def regenerate_body_shot(
    req: RegenerateBodyShotRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Regenerate a single body shot angle using canonical front as Kontext reference (Phase D v3).

    Uses the same per-angle prompt templates as the two-stage pipeline.
    Validates the regenerated shot with LLM vision classification.
    """
    import os
    import random
    import fal_client
    import httpx
    from config import settings as _settings
    from services.r2_storage import get_r2_storage_service
    from services.ai_prompts import get_prompt
    from services.openrouter import get_openrouter_service
    from models.avatar import BodyShotSet

    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")
        if not avatar.face_ref_key or not avatar.body_description:
            raise HTTPException(status_code=400, detail="Face and body description required")

        if req.angle not in BODY_SHOT_ANGLES:
            raise HTTPException(status_code=400, detail=f"Invalid angle: {req.angle}")

        r2 = get_r2_storage_service()
        if not os.environ.get("FAL_KEY") and _settings.FAL_API_KEY:
            os.environ["FAL_KEY"] = _settings.FAL_API_KEY

        body_desc = avatar.body_description

        # Find canonical front from the set to use as Kontext reference
        bss = await db.get(BodyShotSet, req.set_id)
        if not bss:
            raise HTTPException(status_code=404, detail="Body shot set not found")

        canonical_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/body_shots/{req.set_id}/canonical.jpg"
        canonical_url = r2.get_public_url(canonical_key)

        # Version token for this regenerate's output keys. The single-tile
        # regenerate used to overwrite a STABLE key ({angle}.jpg), and the
        # looks list serves image_url off that key with no cache-bust — so the
        # CDN + browser kept showing the OLD image and it looked like the
        # regenerate "did nothing / just zoomed". A fresh key per run makes the
        # URL actually change. (The batch pipeline is unaffected — it runs once
        # per set_id.) Consumers treat bss.angles values as opaque keys.
        _ver = uuid.uuid4().hex[:8]

        # Right-side angles are a mirror of their left twin, never generated.
        # Regenerate the LEFT one to change this pair.
        _mirror_src = BODY_SHOT_MIRROR_FROM.get(req.angle)
        if _mirror_src:
            src_key = (bss.angles or {}).get(_mirror_src) or (
                f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/body_shots/{req.set_id}/{_mirror_src}.jpg"
            )
            async with httpx.AsyncClient() as _c:
                _r = await _c.get(r2.get_public_url(src_key, cache_bust=True), timeout=60)
                _r.raise_for_status()
            flipped = _hflip_jpeg(_r.content)
            r2_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/body_shots/{req.set_id}/{req.angle}_{_ver}.jpg"
            await r2.upload_bytes(flipped, r2_key, "image/jpeg")
            bss.angles = {**(bss.angles or {}), req.angle: r2_key}
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(bss, "angles")
            await db.commit()
            try:
                await _sync_body_motion_look(db, avatar.id, req.angle, r2_key)
            except Exception as sync_exc:
                sentry_sdk.capture_exception(sync_exc)
            return {
                "angle": req.angle,
                "url": r2.get_public_url(r2_key, cache_bust=True),
                "engine": "mirror",
                "note": f"mirrored from {_mirror_src} — regenerate {_mirror_src} to change this pair",
            }

        # Re-use the wardrobe summary extracted by the original pipeline run if
        # present; otherwise re-run the vision extractor against the SELECTED
        # face image. This is what fixes the "body shot still wears a hoodie
        # after I edited to a suit / glasses appear when face has none" bug.
        face_wardrobe_summary: str | None = None
        try:
            stored = (bss.validation or {}).get("face_wardrobe_extracted") if isinstance(bss.validation, dict) else None
            if isinstance(stored, dict):
                summary = (stored.get("wardrobe_summary") or "").strip()
                has_glasses = bool(stored.get("has_glasses"))
                glasses_desc = (stored.get("glasses_description") or "").strip()
                if has_glasses and glasses_desc and "glasses" not in summary.lower():
                    summary = f"{summary} wearing {glasses_desc}.".strip()
                elif (not has_glasses) and ("no glasses" not in summary.lower()):
                    summary = f"{summary} No glasses, no eyewear.".strip()
                face_wardrobe_summary = summary or None
            if not face_wardrobe_summary:
                extractor_prompt_data = get_prompt("face_wardrobe_extractor")
                openrouter_for_wardrobe = get_openrouter_service()
                face_ref_public = r2.get_public_url(avatar.face_ref_key, cache_bust=True)
                raw_wardrobe = await openrouter_for_wardrobe.describe_image(
                    image_url=face_ref_public,
                    system_prompt=extractor_prompt_data["system"],
                    user_text=(
                        "Extract the wardrobe and accessories the person is wearing in this image. "
                        "Pay special attention to glasses (yes/no) and the top garment. "
                        "Respond with ONLY the JSON object."
                    ),
                    model=CREATIVE_DESCRIPTION_MODEL,
                    max_tokens=512,
                    temperature=0.0,
                )
                cleaned_w = raw_wardrobe.strip()
                if cleaned_w.startswith("```"):
                    _lines = cleaned_w.splitlines()
                    cleaned_w = (
                        "\n".join(_lines[1:-1])
                        if _lines and _lines[-1].strip() == "```"
                        else "\n".join(_lines[1:])
                    )
                import json as _json_w
                parsed = _json_w.loads(cleaned_w)
                summary = (parsed.get("wardrobe_summary") or "").strip()
                has_glasses = bool(parsed.get("has_glasses"))
                glasses_desc = (parsed.get("glasses_description") or "").strip()
                if has_glasses and glasses_desc and "glasses" not in summary.lower():
                    summary = f"{summary} wearing {glasses_desc}.".strip()
                elif (not has_glasses) and ("no glasses" not in summary.lower()):
                    summary = f"{summary} No glasses, no eyewear.".strip()
                face_wardrobe_summary = summary or None
        except Exception as wexc:
            sentry_sdk.capture_exception(wexc)
            logger.warning(f"Wardrobe extraction skipped during regenerate: {wexc}")

        # Only use the grounded vision-extracted wardrobe — never fall back to
        # concatenating body_description, which hallucinates accessories.
        body_desc_for_angles = face_wardrobe_summary or (body_desc or "")

        # Build per-angle prompt using the new templates
        if req.angle == "front":
            tmpl = get_prompt("flux_body_kontext_angle_front")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles)
        elif req.angle.startswith("three_quarter_"):
            # See _build_kontext_prompt — the {direction} token names the
            # shoulder closer to the camera, which is the OPPOSITE of the
            # angle suffix per standard portrait convention.
            shoulder_direction = "right" if req.angle == "three_quarter_left" else "left"
            tmpl = get_prompt("flux_body_kontext_angle_three_quarter")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles, direction=shoulder_direction)
        elif req.angle.startswith("profile_"):
            direction = "left" if req.angle == "profile_left" else "right"
            tmpl = get_prompt("flux_body_kontext_angle_profile")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles, direction=direction)
        elif req.angle == "back":
            tmpl = get_prompt("flux_body_kontext_angle_back")
            prompt = tmpl["system"].format(body_description=body_desc_for_angles)

        new_seed = random.randint(1, 999999)
        img_bytes = None
        engine_used = None

        # Tier 1: fal.ai Qwen "multiple-angles" — the SAME engine + numeric
        # angle map the two-stage pipeline uses. Text-prompt FLUX (Tier 2
        # below) has no real angle control, which is why single-tile
        # regenerate was producing wrong-facing shots AND, when that lone
        # call errored, 500ing the whole request.
        try:
            fal_angles = FAL_QWEN_ANGLES[req.angle]
            qwen_result = await fal_client.run_async(
                "fal-ai/qwen-image-edit-2511-multiple-angles",
                arguments={
                    "image_urls": [canonical_url],
                    "horizontal_angle": fal_angles["horizontal_angle"],
                    "vertical_angle": fal_angles["vertical_angle"],
                    # Wide framing + tall output so the regenerated tile is
                    # head-to-toe, not the LoRA's default waist-up crop — this
                    # is the "single regenerate = half body" fix.
                    "zoom": FAL_QWEN_BODY_ZOOM,
                    "image_size": FAL_QWEN_BODY_IMAGE_SIZE,
                    "seed": new_seed,
                    "num_inference_steps": 40,
                    "guidance_scale": 5.0,
                    "output_format": "jpeg",
                },
            )
            qwen_images = qwen_result.get("images", [])
            if qwen_images:
                async with httpx.AsyncClient() as client:
                    r = await client.get(qwen_images[0]["url"], timeout=60)
                    r.raise_for_status()
                    img_bytes = r.content
                engine_used = "qwen_fal"
        except Exception as qexc:
            sentry_sdk.capture_exception(qexc)
            logger.warning(
                f"regenerate {req.angle}: fal Qwen tier failed, falling back to FLUX Kontext: {qexc}"
            )

        # Tier 2: text-prompt fallback — Nano Banana Pro when enabled (better
        # prompt adherence + anatomy than FLUX Kontext), else FLUX Kontext.
        if img_bytes is None:
            from services.nano_banana import (
                nano_banana_pro_enabled, edit_image_run_async,
            )
            if nano_banana_pro_enabled():
                img_url = await edit_image_run_async(
                    prompt, [canonical_url], aspect_ratio="9:16",
                )
                engine_used = "nano_banana_pro"
            else:
                result = await fal_client.run_async(
                    "fal-ai/flux-pro/kontext",
                    arguments={
                        "prompt": prompt,
                        "image_urls": [canonical_url],
                        "guidance_scale": 4.0,
                        "num_inference_steps": 40,
                        "output_format": "jpeg",
                        "seed": new_seed,
                        "aspect_ratio": "9:16",
                        "safety_tolerance": "5",
                    },
                )
                images = result.get("images", [])
                img_url = images[0]["url"] if images else None
                engine_used = "flux_kontext"
            if not img_url:
                raise HTTPException(status_code=500, detail="Both image engines returned no image")
            async with httpx.AsyncClient() as client:
                img_resp = await client.get(img_url, timeout=60)
                img_resp.raise_for_status()
                img_bytes = img_resp.content

        logger.info(f"regenerate {req.angle}: generated via {engine_used}")

        r2_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/body_shots/{req.set_id}/{req.angle}_{_ver}.jpg"
        await r2.upload_bytes(img_bytes, r2_key, "image/jpeg")

        # Update the BodyShotSet record
        bss.angles = {**(bss.angles or {}), req.angle: r2_key}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(bss, "angles")
        if req.angle == "front":
            bss.front_shot_key = r2_key
        await db.commit()

        # Body-Motion looks are a view of Body Shots — propagate so the two
        # Edit-Avatar tabs never disagree. Best-effort; never fail the shot.
        try:
            await _sync_body_motion_look(db, avatar.id, req.angle, r2_key)
        except Exception as sync_exc:
            sentry_sdk.capture_exception(sync_exc)
            logger.warning(f"body-motion look sync failed for {req.angle}: {sync_exc}")

        # This angle is the mirror SOURCE for a right-side twin — regenerate
        # the twin too so the pair stays a matched mirror.
        _twin = next((k for k, v in BODY_SHOT_MIRROR_FROM.items() if v == req.angle), None)
        if _twin:
            try:
                twin_bytes = _hflip_jpeg(img_bytes)
                twin_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/body_shots/{req.set_id}/{_twin}_{_ver}.jpg"
                await r2.upload_bytes(twin_bytes, twin_key, "image/jpeg")
                bss.angles = {**(bss.angles or {}), _twin: twin_key}
                flag_modified(bss, "angles")
                await db.commit()
                await _sync_body_motion_look(db, avatar.id, _twin, twin_key)
                logger.info(f"regenerate {req.angle}: re-mirrored twin {_twin}")
            except Exception as twin_exc:
                sentry_sdk.capture_exception(twin_exc)
                logger.warning(f"twin re-mirror {_twin} failed: {twin_exc}")

        # Validate the regenerated shot
        validation = None
        try:
            validation_prompt_data = get_prompt("gemini_body_shot_validation")
            openrouter = get_openrouter_service()
            shot_url = r2.get_public_url(r2_key, cache_bust=True)
            user_msg = f"Image URL: {shot_url}\n\nClassify the camera angle of this body shot photo."
            classified = await openrouter.generate_text(
                prompt=user_msg,
                system_prompt=validation_prompt_data["system"],
                model="anthropic/claude-sonnet-4",
                max_tokens=32,
                temperature=0.0,
            )
            classified_angle = classified.strip().lower().replace(" ", "_")
            validation = {
                "expected": req.angle,
                "classified": classified_angle,
                "match": classified_angle == req.angle,
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"Validation failed for regenerated {req.angle}: {e}")

        resp = {"angle": req.angle, "url": r2.get_public_url(r2_key, cache_bust=True)}
        if validation:
            resp["validation"] = validation
        return resp
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"regenerate-body-shot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


class RegenerateBodyMotionPoseRequest(BaseModel):
    avatar_id: str
    pose: str


@router.post("/ai/regenerate-body-motion-pose")
async def regenerate_body_motion_pose(
    req: RegenerateBodyMotionPoseRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Edit-Avatar → "Body Motion" tab: regenerate ONE pose.

    Body-Motion looks are a view of the avatar's Body Shots. When the avatar
    has a completed Body Shot set we regenerate the pose through THAT single
    pipeline (full-body canonical reference + Qwen numeric angle control, via
    ``regenerate_body_shot``) and re-sync the ``AvatarLook`` — so the "Shots"
    step and the "Body Motion" tab can no longer drift apart (different image,
    different framing). Only avatars that never ran Body Shots fall back to the
    standalone per-look pipeline.
    """
    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    if req.pose not in BODY_SHOT_ANGLES:
        raise HTTPException(status_code=400, detail=f"Invalid pose: {req.pose}")

    bss = (
        await db.execute(
            select(BodyShotSet)
            .where(BodyShotSet.avatar_id == avatar.id, BodyShotSet.status == "completed")
            .order_by(BodyShotSet.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if bss is not None:
        # One pipeline. regenerate_body_shot updates BodyShotSet.angles AND
        # calls _sync_body_motion_look, so the AvatarLook this tab renders is
        # updated in place.
        result = await regenerate_body_shot(
            RegenerateBodyShotRequest(avatar_id=req.avatar_id, set_id=bss.id, angle=req.pose),
            user=user, ctx=ctx, db=db,
        )
        result["source"] = "body_shot"
        return result

    # Legacy avatars (clone flows that never generated a Body Shot set):
    # standalone per-look pipeline, same as the old tab behaviour.
    if not avatar.face_ref_key:
        raise HTTPException(status_code=400, detail="Avatar has no face image yet")
    existing = (
        await db.execute(
            select(AvatarLook).where(
                AvatarLook.avatar_id == avatar.id,
                AvatarLook.look_type == "body_motion",
                AvatarLook.pose_angle == req.pose,
            ).limit(1)
        )
    ).scalars().first()
    if existing is not None:
        await db.delete(existing)
        await db.commit()
    look_id = f"al_{uuid.uuid4().hex[:12]}"
    label = _BODY_MOTION_POSE_LABELS.get(req.pose, req.pose)
    db.add(AvatarLook(
        id=look_id, avatar_id=avatar.id, name=f"AI: {label}",
        background_prompt=f"Body motion pose: {req.pose}",
        is_default=False, is_original=False, status="pending",
        look_type="body_motion", pose_angle=req.pose,
    ))
    await db.commit()
    from tasks.avatar_looks import generate_avatar_look_task
    generate_avatar_look_task.delay(look_id)
    return {"look_id": look_id, "pose": req.pose, "status": "pending", "source": "look_pipeline"}

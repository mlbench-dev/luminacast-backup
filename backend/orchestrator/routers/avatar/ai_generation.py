"""Avatar ai_generation endpoints — split from the former routers/avatar.py."""

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
from pydantic import BaseModel, Field
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

class GenerateDescriptionRequest(BaseModel):
    hint: str

@router.post("/ai/generate-description")
async def generate_description(
    req: GenerateDescriptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Generate a detailed face description from a short hint.

    Uses OpenRouter LLM to expand a casual hint into a detailed,
    FLUX-optimized face description.

    Returns: {description: str, suggested_name: str}
    """
    import json as _json
    from services.openrouter import get_openrouter_service
    from services.ai_prompts import get_prompt

    openrouter = get_openrouter_service()
    prompt = get_prompt("face_description_generator")

    raw = await openrouter.generate_text(
        prompt=f"Create an avatar based on this idea: {req.hint}",
        system_prompt=prompt["system"],
        temperature=0.8,
    )

    # Parse response — expect JSON with description + suggested_name
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        cleaned = cleaned.strip()

    try:
        result = _json.loads(cleaned)
        return {
            "description": result.get("description", cleaned),
            "suggested_name": result.get("suggested_name", "Avatar"),
        }
    except (_json.JSONDecodeError, TypeError):
        return {"description": cleaned, "suggested_name": "Avatar"}

_ACCENT_LABELS = {
    "us": "American English", "uk": "British English", "au": "Australian English",
    "ie": "Irish English", "in": "Indian English", "za": "South African English",
    "es": "Spain Spanish", "mx": "Mexican Spanish", "ar": "Argentinian Spanish",
    "co": "Colombian Spanish", "fr": "France French", "ca": "Canadian French",
    "de": "German", "br": "Brazilian Portuguese", "pt": "European Portuguese",
    "it": "Italian", "zh": "Mandarin Chinese", "yue": "Cantonese Chinese",
    "ja": "Japanese", "jp": "Japanese",
}

class GenerateVoiceDescriptionRequest(BaseModel):
    avatar_id: str
    # Optional base-voice traits the user picked in the step before. When present,
    # they MUST be reflected in the generated description — see ai_prompts.py.
    gender: str = ""
    language: str = ""
    accent: str = ""
    base_voice_id: str = ""
    base_voice_name: str = ""
    base_voice_descriptor: str = ""

@router.post("/ai/generate-voice-description")
async def generate_voice_description(
    req: GenerateVoiceDescriptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate a voice description that matches the avatar's face description
    and the user's selected base-voice traits (gender, language, accent).

    Returns: {
        voice_description: str,
        test_speech: str,
        suggested_filters: {gender, language, tags}
    }
    """
    import json as _json
    from services.openrouter import get_openrouter_service
    from services.ai_prompts import get_prompt

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    openrouter = get_openrouter_service()
    prompt = get_prompt("voice_description_generator")

    # Build the user-message portion of the prompt. We always include the face
    # description and avatar name; we conditionally append base-voice context
    # so the prompt stays clean when the user hasn't picked anything yet.
    accent_label = _ACCENT_LABELS.get((req.accent or "").lower(), "")
    base_voice_lines: list[str] = []
    # Source-of-truth: prefer the user's request gender (just-picked), but
    # fall back to the avatar's stored gender so the LLM never invents one.
    effective_gender = (req.gender or (avatar.gender or "")).strip()
    if effective_gender:
        base_voice_lines.append(f"User-selected gender: {effective_gender}")
    if req.language:
        base_voice_lines.append(f"User-selected language: {req.language}")
    if accent_label:
        base_voice_lines.append(f"User-selected accent: {accent_label}")
    elif req.accent:
        # Pass through any unmapped accent code so the LLM still has the hint.
        base_voice_lines.append(f"User-selected accent: {req.accent}")
    if req.base_voice_name or req.base_voice_descriptor:
        descriptor_bits = [b for b in (req.base_voice_name, req.base_voice_descriptor) if b]
        base_voice_lines.append(
            "User-selected base voice: " + " — ".join(descriptor_bits)
        )

    # V1: thread the avatar's full identity (age, ethnicity, nationality)
    # into the prompt as hard constraints. The face description text already
    # encodes ethnicity/nationality (e.g. "Mixed Japanese-Brazilian") and we
    # surface age_range from target_audience separately so the LLM can
    # render an age-appropriate voice. Without these, the model regenerated
    # generic "young woman" descriptions that contradicted the visible
    # avatar profile.
    age_range = ""
    try:
        ta = avatar.target_audience or {}
        if isinstance(ta, dict):
            age_range = (ta.get("age_range") or "").strip()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        age_range = ""

    identity_lines: list[str] = []
    if age_range:
        identity_lines.append(f"Avatar age range: {age_range}")
    if effective_gender:
        identity_lines.append(f"Avatar gender: {effective_gender}")

    user_prompt = (
        f"Avatar face description: {avatar.appearance_prompt or avatar.description or 'A professional, friendly person'}\n"
        f"Avatar name: {avatar.name or 'Avatar'}"
    )
    if identity_lines:
        user_prompt += (
            "\n\nAvatar identity (treat as hard constraints — the voice description "
            "MUST match these, including any ethnicity / nationality cues from the "
            "face description above):\n" + "\n".join(identity_lines)
        )
    if base_voice_lines:
        user_prompt += "\n\nUser's base-voice picks (reflect these in the description):\n" + "\n".join(base_voice_lines)

    raw = await openrouter.generate_text(
        prompt=user_prompt,
        system_prompt=prompt["system"],
        temperature=0.7,
    )

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        cleaned = cleaned.strip()

    name = avatar.name or "Avatar"
    named_fallback = f"Hi, I'm {name}! Welcome to my stream — let me show you something amazing today!"

    # Default the suggested_filters to the user's picks when supplied so the UI
    # doesn't flip the user's gender/language pills back to defaults.
    fallback_filters = {
        "gender": req.gender or "female",
        "language": req.language or "english",
        "tags": [],
    }

    try:
        result = _json.loads(cleaned)
        description = result.get("voice_description", "Warm, friendly voice with clear pronunciation")
        test_speech = result.get("test_speech", named_fallback)
        suggested_filters = result.get("suggested_filters", fallback_filters)
        logger.info(
            "voice_description.generate base_voice=%r accent=%r gender=%r language=%r len=%d",
            req.base_voice_id or req.base_voice_name or None,
            req.accent or None,
            req.gender or None,
            req.language or None,
            len(description),
        )
    except (_json.JSONDecodeError, TypeError) as e:
        sentry_sdk.capture_exception(e)
        logger.info(
            "voice_description.generate base_voice=%r accent=%r gender=%r language=%r len=%d (raw fallback)",
            req.base_voice_id or req.base_voice_name or None,
            req.accent or None,
            req.gender or None,
            req.language or None,
            len(cleaned),
        )
        description = cleaned
        test_speech = named_fallback
        suggested_filters = fallback_filters

    # Persist so a refresh mid-Voice-step restores this instead of
    # regenerating from scratch with the language/accent chips reset to
    # their defaults. voice_desc_overridden=False here — this was an
    # auto/explicit regen, not the user hand-editing the textarea.
    avatar.voice_description = description
    avatar.voice_test_speech = test_speech
    avatar.voice_language = req.language or None
    avatar.voice_accent = req.accent or None
    avatar.voice_desc_overridden = False
    await db.commit()

    return {
        "voice_description": description,
        "test_speech": test_speech,
        "suggested_filters": suggested_filters,
    }

class GenerateVoicePreviewsRequest(BaseModel):
    avatar_id: str
    voice_description: str
    test_speech: str = ""
    gender: str = ""
    language: str = ""
    accent: str = ""

@router.post("/ai/generate-voice-previews")
async def generate_voice_previews(
    req: GenerateVoicePreviewsRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Generate 4 voice preview samples via ElevenLabs Voice Design.

    Returns: {previews: [{preview_id, audio_url, index}, ...]}
    """
    from services.elevenlabs import get_elevenlabs_service
    from services.r2_storage import get_r2_storage_service

    el = get_elevenlabs_service()
    r2 = get_r2_storage_service()

    # Fetch avatar name for personalized preview text
    avatar = await db.get(Avatar, req.avatar_id)
    avatar_name = avatar.name if avatar else "Avatar"

    # PR #65: append the mic-style suffix (clip-on lavalier vs phone mic)
    # so the voice-cloning engine generates a candidate that already
    # sounds like the user's chosen mic. Default = phone mic.
    try:
        from services.voice_postprocess_upload import apply_mic_style
        clip_mic_enabled = bool(getattr(avatar, "clip_mic_enabled", False))
        described = apply_mic_style(req.voice_description, clip_mic_enabled)
    except Exception as _mic_exc:
        sentry_sdk.capture_exception(_mic_exc)
        described = req.voice_description
    rich_description = f"High quality audio. {described}"

    # V2: gender-lock. The description text occasionally contradicts the
    # user's gender pick (e.g. "young woman" while gender=male) when the
    # LLM regenerated stale and the user manually changed gender after.
    # We aggressively normalise the description here so the upstream voice
    # engine cannot pick a wrong-gender candidate.
    effective_gender = (req.gender or "").strip().lower()
    if effective_gender in ("male", "female"):
        try:
            opposite = "female" if effective_gender == "male" else "male"
            opposite_tokens = {
                "female": [r"\bwoman\b", r"\bwomen\b", r"\bgirl\b", r"\bgirls\b", r"\blady\b", r"\bladies\b", r"\bshe\b", r"\bher\b", r"\bhers\b", r"\bfeminine\b"],
                "male":   [r"\bman\b", r"\bmen\b", r"\bboy\b", r"\bboys\b", r"\bguy\b", r"\bguys\b", r"\bgentleman\b", r"\bgentlemen\b", r"\bdude\b", r"\bdudes\b", r"\bhe\b", r"\bhis\b", r"\bhim\b", r"\bmasculine\b"],
            }[opposite]
            replacements = {
                "female": {"woman": "man", "women": "men", "girl": "boy", "girls": "boys", "lady": "gentleman", "ladies": "gentlemen", "she": "he", "her": "his", "hers": "his", "feminine": "masculine"},
                "male":   {"man": "woman", "men": "women", "boy": "girl", "boys": "girls", "guy": "lady", "guys": "ladies", "gentleman": "lady", "gentlemen": "ladies", "dude": "lady", "dudes": "ladies", "he": "she", "his": "her", "him": "her", "masculine": "feminine"},
            }[opposite]
            for pattern in opposite_tokens:
                # Case-insensitive replace, preserve the swap target as lowercase.
                token_match = re.compile(pattern, flags=re.IGNORECASE)
                def _swap(m, _opp=opposite):
                    word = m.group(0).lower()
                    return replacements.get(word, word)
                rich_description = token_match.sub(_swap, rich_description)
        except Exception as e:
            sentry_sdk.capture_exception(e)

    # Prepend gender authoritatively so upstream filters / heuristics see it
    # at the very front of the prompt (most engines weight head tokens).
    if req.gender and req.gender.lower() not in rich_description.lower():
        rich_description = f"{req.gender.capitalize()} voice. {rich_description}"
    elif req.gender:
        # Even if the gender word already appears, re-prepend a leading
        # "Male voice." / "Female voice." so the directive is unambiguous.
        rich_description = f"{req.gender.capitalize()} voice. {rich_description}"

    # Use accent (specific) over language (generic) for accent directive.
    # Shares the _ACCENT_LABELS map with generate-voice-description so both
    # endpoints render the user's accent pick identically.
    accent_label = _ACCENT_LABELS.get((req.accent or "").lower(), "")
    if accent_label:
        rich_description = f"{rich_description}. Speaking with a clear {accent_label} accent."
    elif req.language and req.language.lower() not in rich_description.lower():
        rich_description = f"{rich_description}. {req.language.capitalize()} accent."

    # Prepend audio quality hint (ElevenLabs responds well to this)
    rich_description = f"High quality audio. {rich_description}"

    # Build personalized fallback text using avatar's name
    fallback_text = f"Hi, I'm {avatar_name}! Welcome to my stream — I've got some amazing products to show you today!"
    preview_text = req.test_speech or fallback_text
    logger.info(f"Voice preview request: rich_description='{rich_description}', text='{preview_text}'")

    previews = await el.generate_voice_previews(
        description=rich_description,
        text=preview_text,
    )

    result_previews = []
    for p in previews:
        audio_bytes = base64.b64decode(p["audio_base_64"])
        r2_key = f"creators/{ctx.workspace_owner_id}/avatar/{req.avatar_id}/voice_preview_{p['index']}.mp3"
        await r2.upload_bytes(audio_bytes, r2_key, "audio/mpeg")

        result_previews.append({
            "preview_id": p["preview_id"],
            "audio_url": r2.get_public_url(r2_key, cache_bust=True),
            "index": p["index"],
        })

    # Persist — these are R2-hosted URLs (not short-lived signed links), so
    # they're safe to restore on a refresh instead of re-hitting ElevenLabs.
    # A fresh batch invalidates whatever was previously selected.
    if avatar:
        avatar.voice_previews = result_previews
        avatar.selected_voice_preview_idx = None
        await db.commit()

    return {"previews": result_previews}

class ApproveVoiceRequest(BaseModel):
    avatar_id: str
    preview_id: str

@router.post("/ai/approve-voice")
async def approve_voice(
    req: ApproveVoiceRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Approve a voice preview and train Fish Audio with it.

    Pipeline:
    1. ElevenLabs generates a 45-second training sample with the approved voice
    2. Upload training sample to R2
    3. Send to Fish Audio voice clone endpoint
    4. Fish Audio returns a voice_id (model ID)
    5. Store voice_id on the avatar — this is the permanent voice
    """
    import tempfile
    import os
    from services.elevenlabs import get_elevenlabs_service, VOICE_TRAINING_TEXT
    from services.fish_audio import get_fish_audio_service
    from services.r2_storage import get_r2_storage_service

    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    el = get_elevenlabs_service()
    fish = get_fish_audio_service()
    r2 = get_r2_storage_service()

    if avatar.voice_id:
        logger.info(f"Avatar {avatar.id} already has voice_id={avatar.voice_id}, skipping ElevenLabs re-conversion")
        voice_id_el = avatar.voice_id
    else:
        try:
            voice_id_el = await el.create_voice_from_preview(
                req.preview_id,
                avatar.name or "AI Avatar",
                avatar.description or "AI generated voice",
            )
            avatar.voice_id = voice_id_el
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"ElevenLabs create voice from preview failed: {e}")
            raise HTTPException(status_code=500, detail=f"Voice creation from preview failed: {str(e)[:200]}")

    # Step 0: Convert preview to permanent voice
    # try:
    #     voice_id_el = await el.create_voice_from_preview(
    #         req.preview_id,
    #         avatar.name or "AI Avatar",
    #         avatar.description or "AI generated voice",
    #     )
    # except Exception as e:
    #     sentry_sdk.capture_exception(e)
    #     logger.error(f"ElevenLabs create voice from preview failed: {e}")
    #     raise HTTPException(status_code=500, detail=f"Voice creation from preview failed: {str(e)[:200]}")

    # Step 1: Generate 45-second training sample with the permanent voice
    try:
        training_audio = await el.generate_training_sample(voice_id_el)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"ElevenLabs training sample failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice training sample generation failed: {str(e)[:200]}")

    # Step 2: Save to temp file + R2
    tmp_path = os.path.join(tempfile.gettempdir(), f"voice_training_{req.avatar_id}.mp3")
    with open(tmp_path, "wb") as f:
        f.write(training_audio)

    training_key = f"creators/{ctx.workspace_owner_id}/avatar/{req.avatar_id}/voice_training_sample.mp3"
    await r2.upload_bytes(training_audio, training_key, "audio/mpeg")

    # Step 3: Clone with Fish Audio
    try:
        voice_id = await fish.clone_voice_from_file(
            tmp_path,
            name=f"AI Avatar voice for {avatar.name or req.avatar_id}",
            transcript=VOICE_TRAINING_TEXT,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Voice cloning failed: {e}")
        raise HTTPException(status_code=500, detail=f"Voice cloning failed: {str(e)[:200]}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    try:
        await el.delete_voice(voice_id_el)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Failed to delete ElevenLabs voice {voice_id_el} after Fish Audio clone: {e}")

    # Step 4: Store on avatar
    avatar.voice_id = voice_id
    avatar.voice_sample_key = training_key
    await db.commit()

    return {"voice_id": voice_id, "status": "Voice trained and locked to this avatar"}

STYLE_PRESET_PHRASES = {
    "studio": "professional studio lighting, clean background",
    "studio_pro": "professional studio lighting, flawless polished skin, clean white background, magazine-quality portrait",
    "natural": "natural daylight, organic feel",
    "natural_real": "natural daylight, realistic skin texture with pores, candid amateur photograph",
    "cinematic": "cinematic film still, dramatic rim lighting, shallow depth of field, moody color grading",
    "stylized": "artistic stylized look, fashion-forward",
    "stylized_cartoon": "anime art style, vibrant colors, cel-shaded, digital illustration",
    "street": "urban street photography, candid raw aesthetic, gritty textures, bokeh city background",
    "glamour": "high-fashion glamour photography, bold makeup, dramatic contouring, luxury lighting",
    "editorial": "editorial magazine spread, soft diffused lighting, neutral tones, minimal background",
    "golden_hour": "golden hour warm sunset lighting, dreamy soft glow, warm amber tones",
    "anime": "anime art style, Japanese animation, vibrant saturated colors, cel-shaded, large expressive eyes",
    "cyberpunk": "neon-lit cyberpunk aesthetic, futuristic, holographic accents, dark urban backdrop with neon reflections",
    "vintage_film": "vintage film grain, 70s-90s film photography, warm desaturated tones, retro color palette",
    "soft_beauty": "dewy K-beauty aesthetic, glass skin, soft pastel tones, gentle diffused lighting, luminous glow",
}

IMPERFECTION_PHRASES = {
    "freckles": "light freckles across the nose and cheeks",
    "slight_asymmetry": "slightly asymmetric facial features for realism",
    "skin_texture": "visible skin texture and pores",
    "laugh_lines": "subtle laugh lines around the eyes",
    "bushy_brows": "naturally full, slightly unruly eyebrows",
    "gap_teeth": "small charming gap between front teeth",
    "phone_selfie": "shot on smartphone camera, slight grain, natural uneven lighting",
    "tired_after_work": "slightly fatigued face, subtle under-eye circles, end-of-day look",
    "bare_face": "zero makeup, natural bare skin, visible pores",
    "outdoor_light": "natural daylight outdoors, dappled sunlight",
    "morning_look": "just woke up energy, slightly disheveled hair, cozy and approachable",
    "weathered": "sun-kissed skin with texture, freckles, slight tan lines",
    "office_casual": "slightly loosened collar, rolled-up sleeves, professional but human",
    "asymmetric": "natural facial asymmetry, one eyebrow slightly higher",
    "natural_pores": "visible natural skin pores, unretouched complexion",
    "stray_hairs": "a few stray hairs, not perfectly styled, natural and lived-in",
    "post_workout": "slight sheen of sweat, flushed healthy complexion, post-exercise glow",
}

class RewriteDescriptionRequest(BaseModel):
    avatar_id: str
    target_audience: Optional[dict] = None
    base_description: str = ""
    style_presets: list[str] = []
    imperfections: list[str] = []
    regenerate: bool = False

@router.post("/ai/rewrite-description")
async def rewrite_description(
    req: RewriteDescriptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Rewrite avatar description. Chip toggles are instant (template-based).
    LLM call only when regenerate=true (Surprise Me / Regenerate)."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        # Save target audience if provided
        if req.target_audience:
            avatar.target_audience = req.target_audience
            await db.commit()

        if req.regenerate:
            # LLM path — use Gemini/OpenRouter for creative rewrite
            from services.openrouter import get_openrouter_service
            from services.ai_prompts import get_prompt

            oai = get_openrouter_service()
            prompt_data = get_prompt("gemini_avatar_description_rewrite")
            audience_context = ""
            if req.target_audience:
                ta = req.target_audience
                audience_context = f"\nTarget audience: {ta.get('age_range', 'general')}, interests: {', '.join(ta.get('interests', []))}, {ta.get('description', '')}"

            user_msg = f"Base description: {req.base_description}\nStyle presets: {', '.join(req.style_presets)}\nMake it real details: {', '.join(req.imperfections)}{audience_context}\n\nRewrite this into a vivid, detailed avatar description."

            log_creative_model_use("avatar_description_rewrite", CREATIVE_DESCRIPTION_MODEL)
            description = await oai.generate_text(
                prompt=user_msg,
                system_prompt=prompt_data["system"],
                model=CREATIVE_DESCRIPTION_MODEL,
                max_tokens=500,
            )
            description = description.strip()

            try:
                from services.usage_tracker import calculate_llm_cost, log_usage
                _u = getattr(oai, "last_usage", {}) or {}
                if _u:
                    await log_usage(
                        db,
                        user_id=ctx.workspace_owner_id,                        event_type="script_generation",
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
        else:
            # Template path — deterministic, instant
            parts = [req.base_description.strip()]
            for preset in req.style_presets:
                phrase = STYLE_PRESET_PHRASES.get(preset)
                if phrase:
                    parts.append(phrase)
            for imp in req.imperfections:
                phrase = IMPERFECTION_PHRASES.get(imp)
                if phrase:
                    parts.append(phrase)
            description = ". ".join(p.rstrip(".") for p in parts if p) + "."

        avatar.description = description
        await db.commit()

        return {"description": description}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"rewrite-description failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])

class SaveTargetAudienceRequest(BaseModel):
    avatar_id: str
    target_audience: dict

@router.post("/ai/save-target-audience")
async def save_target_audience(
    req: SaveTargetAudienceRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Save target audience for an avatar."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        avatar.target_audience = req.target_audience
        await db.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e)[:200])

class SaveSetupRequest(BaseModel):
    avatar_id: str
    target_audience: dict
    name: str = Field(..., max_length=60)
    description: str
    gender: str
    body_description: Optional[str] = None
    style_preset: Optional[str] = None
    imperfections: Optional[list[str]] = None
    # "9:16" | "16:9" | "1:1" | "4:5" — see models/avatar.py's Avatar.layout.
    layout: Optional[str] = None

@router.post("/ai/save-setup")
async def save_setup(
    req: SaveSetupRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Save consolidated Setup page data: target_audience + name + description + gender + body_description."""
    try:
        avatar = await db.get(Avatar, req.avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        avatar.target_audience = req.target_audience
        avatar.name = req.name
        avatar.description = req.description
        avatar.gender = req.gender
        if req.body_description is not None:
            avatar.body_description = req.body_description
        if req.style_preset is not None:
            avatar.style_preset = req.style_preset
        if req.imperfections is not None:
            avatar.imperfections = req.imperfections
        if req.layout is not None:
            avatar.layout = req.layout
        await db.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e)[:200])

class RewriteAudienceDescriptionRequest(BaseModel):
    age_min: int
    age_max: int
    gender_lean: int
    gender_doesnt_matter: bool
    interests: list[str] = []
    geography: str = ""
    income_bracket: str = ""
    occupations: list[str] = []

@router.post("/ai/rewrite-audience-description")
async def rewrite_audience_description(
    req: RewriteAudienceDescriptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Generate an audience description from all audience fields.
    Cached 60s to avoid spamming during rapid toggling."""
    try:
        import json as _json
        from services.openrouter import get_openrouter_service
        from services.ai_prompts import get_prompt

        openrouter = get_openrouter_service()
        prompt_data = get_prompt("audience_description")

        age_range = f"{req.age_min}-{req.age_max}"
        gender_desc = "Gender doesn't matter" if req.gender_doesnt_matter else f"Gender lean: {req.gender_lean} (0 = mostly female, 50 = equal, 100 = mostly male)"
        geography_desc = f"Geography: {req.geography}" if req.geography else ""
        income_desc = f"Income bracket: {req.income_bracket}" if req.income_bracket else ""
        occupations_desc = f"Occupations: {', '.join(req.occupations)}" if req.occupations else ""
        
        user_msg_parts = [
            f"Age range: {age_range}",
            gender_desc,
            f"Interests: {', '.join(req.interests) if req.interests else 'none'}",
            geography_desc,
            income_desc,
            occupations_desc
        ]
        user_msg = "\n".join([p for p in user_msg_parts if p])

        log_creative_model_use("avatar_audience_description", CREATIVE_DESCRIPTION_MODEL)
        raw = await openrouter.generate_text(
            prompt=user_msg,
            system_prompt=prompt_data["system"],
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=300,
        )
        description = raw.strip().strip('"').strip("'")

        return {"description": description}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"rewrite-audience-description failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])

class RewriteAvatarNameAndDescriptionRequest(BaseModel):
    audience_description: str
    gender: str
    presets: list[str] = []
    imperfections: list[str] = []

@router.post("/ai/rewrite-avatar-name-and-description")
@router.post("/ai/rewrite-avatar-identity")
async def rewrite_avatar_identity(
    req: RewriteAvatarNameAndDescriptionRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Single LLM call: returns name + description + body_description."""
    try:
        import json as _json
        from services.openrouter import get_openrouter_service
        from services.ai_prompts import get_prompt

        openrouter = get_openrouter_service()
        prompt_data = get_prompt("avatar_name_and_description")

        user_msg = (
            f"Audience description: {req.audience_description}\n"
            f"Gender: {req.gender}\n"
            f"Style presets: {', '.join(req.presets) if req.presets else 'none'}\n"
            f"Make it real details: {', '.join(req.imperfections) if req.imperfections else 'none'}"
        )

        log_creative_model_use("avatar_name_and_description", CREATIVE_DESCRIPTION_MODEL)
        raw = await openrouter.generate_text(
            prompt=user_msg,
            system_prompt=prompt_data["system"],
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=800,
        )

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            cleaned = cleaned.strip()

        # Try parsing as JSON directly, or extract JSON object from LLM preamble text
        result = None
        try:
            result = _json.loads(cleaned)
        except (_json.JSONDecodeError, TypeError):
            # LLM may wrap JSON in explanatory text — extract first { ... } block
            match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", cleaned, re.DOTALL)
            if match:
                try:
                    result = _json.loads(match.group(0))
                except (_json.JSONDecodeError, TypeError):
                    pass

        if result and isinstance(result, dict):
            return {
                "name": result.get("name", "Avatar"),
                "description": result.get("description", cleaned),
                "body_description": result.get("body_description", ""),
            }
        else:
            return {"name": "Avatar", "description": cleaned, "body_description": ""}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"rewrite-avatar-identity failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])

class LockTestScriptRequest(BaseModel):
    test_script: str

@router.post("/ai/{avatar_id}/lock-test-script")
async def lock_test_script(
    avatar_id: str,
    req: LockTestScriptRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Lock the test script as the single source of truth for all downstream voice playback.

    Writes the user's text to BOTH `locked_test_script` (the explicit
    user-curated value) and `test_script` (read by the InfiniteTalk preview
    pipeline). Earlier these were out of sync: the lock endpoint saved only
    to `locked_test_script`, the preview pipeline read only `test_script`,
    so the AI-auto-generated greeting (or the hardcoded fallback) leaked
    through whenever the frontend's in-memory copy of the script was empty.
    """
    try:
        avatar = await db.get(Avatar, avatar_id)
        if not avatar or avatar.user_id != ctx.workspace_owner_id:
            raise HTTPException(status_code=404, detail="Avatar not found")

        avatar.locked_test_script = req.test_script
        avatar.test_script = req.test_script
        await db.commit()
        return {"status": "locked", "locked_test_script": req.test_script}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e)[:200])

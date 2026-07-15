"""Universal content engine — detect what the user wants and build a matching
script-writer system prompt.

The cast pipeline historically forced every script through a "TikTok live
selling scriptwriter" persona, so a request like "show Zara running in a black
trouser suit" would still come out as "okay you guys, check out this cream!".
This module replaces that fixed persona with a content-type detector and a
dynamic system prompt builder.

Public API:
    CONTENT_TYPES                -- catalog of types (role, style, keywords)
    detect_content_type(...)     -- returns {type, role, style}
    build_system_prompt(...)     -- builds a persona-aware system prompt
"""
from __future__ import annotations

import logging
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)


# Content types the engine knows how to write for. The 'role' and 'style'
# fields are interpolated into build_system_prompt() so the LLM persona
# adapts to the user's actual intent instead of being hardcoded as a
# live-selling host.
CONTENT_TYPES: dict[str, dict] = {
    "product_showcase": {
        "role": "product marketing video creator",
        "style": "enthusiastic, benefit-focused, conversion-driven",
        "keywords": ["sell", "product", "review", "shop", "buy", "deal", "price", "discount", "showcase"],
    },
    "tutorial": {
        "role": "educational content creator",
        "style": "clear, step-by-step, helpful, instructional",
        "keywords": ["how to", "tutorial", "teach", "learn", "guide", "steps", "explain", "walkthrough"],
    },
    "entertainment": {
        "role": "entertainment content creator",
        "style": "fun, engaging, creative, surprising",
        "keywords": ["funny", "skit", "comedy", "dance", "challenge", "trend", "entertainment", "meme"],
    },
    "storytelling": {
        "role": "narrative video storyteller",
        "style": "compelling, emotional, dramatic arc, authentic",
        "keywords": ["story", "storytime", "happened", "journey", "experience", "life", "memoir"],
    },
    "educational": {
        "role": "educational content expert",
        "style": "authoritative, clear, data-driven, insightful",
        "keywords": ["explain", "science", "history", "facts", "research", "lecture", "knowledge", "course"],
    },
    "fashion_lifestyle": {
        "role": "fashion and lifestyle content creator",
        "style": "aesthetic, trendy, aspirational, visual-first",
        "keywords": ["fashion", "outfit", "style", "look", "wear", "brand", "aesthetic", "grwm", "ootd",
                     "trouser", "trousers", "dress", "suit"],
    },
    "motion_creative": {
        "role": "creative motion video director",
        "style": "cinematic, visual storytelling, action-focused",
        "keywords": ["running", "walking", "dancing", "action", "motion", "cinematic", "movement",
                     "jumping", "moving"],
    },
    "cartoon_animation": {
        "role": "animated content creator",
        "style": "playful, colorful, character-driven, imaginative",
        "keywords": ["cartoon", "animation", "animated", "character", "draw", "illustrate", "anime"],
    },
    "brand_awareness": {
        "role": "brand marketing video creator",
        "style": "professional, memorable, brand-aligned, emotional",
        "keywords": ["brand", "awareness", "campaign", "launch", "announcement", "reveal"],
    },
    "live_selling": {
        "role": "live selling host for e-commerce streams",
        "style": "enthusiastic, urgent, interactive, conversion-focused",
        "keywords": ["live", "stream", "live selling", "flash sale", "tiktok shop"],
    },
    "general": {
        "role": "creative video content creator",
        "style": "natural, engaging, audience-aware",
        "keywords": [],
    },
}


def _score_keywords(description: str, keywords: list[str]) -> int:
    """Count how many catalog keywords appear in the description."""
    if not keywords:
        return 0
    desc = description.lower()
    return sum(1 for kw in keywords if kw and kw in desc)


async def _llm_classify_content_type(description: str) -> str:
    """Fall back to an LLM classifier when no keywords matched."""
    try:
        from services.openrouter import get_openrouter_service

        category_list = "\n".join(
            f"- {ct_id}" for ct_id in CONTENT_TYPES if ct_id != "general"
        )
        user_prompt = f"""Classify this video creation request into ONE of these categories:
{category_list}

Request: \"\"\"{description.strip()[:600]}\"\"\"

Return ONLY the category name, nothing else."""
        result = await get_openrouter_service().generate_text(
            prompt=user_prompt,
            system_prompt="You are a precise content classifier. Return only the category name.",
            temperature=0.1,
        )
        result = (result or "").strip().lower().splitlines()[0].strip()
        # Allow a few minor variations
        if result in CONTENT_TYPES:
            return result
        if result.replace("-", "_") in CONTENT_TYPES:
            return result.replace("-", "_")
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("LLM content-type classification failed: %s", exc)
    return "general"


async def detect_content_type(
    description: str,
    products: Optional[list[dict]] = None,
) -> dict:
    """Detect what kind of content the user wants.

    Returns: {"type": <id>, "role": <role>, "style": <style>}.

    Strategy: keyword-score every type; if no keywords match, ask an LLM.
    Attaching products gives a bonus to product-oriented types so a
    "video about my new sneakers" lands on product_showcase even when the
    description itself is sparse.
    """
    desc = (description or "").strip()
    if not desc:
        # Empty description: pick product_showcase if products attached, else general.
        chosen = "product_showcase" if products else "general"
        ct = CONTENT_TYPES[chosen]
        return {"type": chosen, "role": ct["role"], "style": ct["style"]}

    scores: dict[str, int] = {}
    for ct_id, ct in CONTENT_TYPES.items():
        if ct_id == "general":
            continue
        score = _score_keywords(desc, ct["keywords"])
        if products and ct_id in ("product_showcase", "live_selling"):
            score += 2
        scores[ct_id] = score

    best = max(scores, key=scores.get) if scores else "general"
    if scores.get(best, 0) == 0:
        # No keyword match at all — hand off to the LLM for a classification.
        best = await _llm_classify_content_type(desc)

    if best not in CONTENT_TYPES:
        best = "general"

    ct = CONTENT_TYPES[best]
    logger.info("Detected content type %r for description (score=%s)", best, scores.get(best, 0))
    return {"type": best, "role": ct["role"], "style": ct["style"]}


def build_system_prompt(
    content_type: dict,
    voice_profile: Optional[dict] = None,
) -> str:
    """Build a script-writer system prompt tuned to the detected content type.

    `content_type` should be the dict returned by detect_content_type().
    `voice_profile` is the optional creator-voice profile (tone, energy,
    common phrases) so the avatar still sounds like themselves.
    """
    role = content_type.get("role") or CONTENT_TYPES["general"]["role"]
    style = content_type.get("style") or CONTENT_TYPES["general"]["style"]

    base = (
        f"You are a professional {role}.\n"
        f"Your style is: {style}.\n\n"
        "You create video scripts that match the user's intent EXACTLY.\n"
        "You are NOT limited to product selling — you create whatever the user asks for: "
        "tutorials, stories, entertainment, fashion, animation, education, motion video, "
        "or any creative vision.\n\n"
        "Read the user's description carefully and CREATE what they asked for.\n"
        "If the request is visual-first (motion, fashion, cinematic), keep narration sparse "
        "and let the visuals carry meaning.\n"
        "If the request is educational, be authoritative and avoid hype.\n"
        "If the request is entertainment, be surprising and creative.\n"
        "Return the exact format requested — do not add commentary."
    )

    if voice_profile and voice_profile.get("tone"):
        common = voice_profile.get("common_phrases") or []
        if isinstance(common, str):
            common = [common]
        base += (
            "\n\nAVATAR VOICE PROFILE:\n"
            f"Speaking style: {voice_profile.get('tone', 'natural')}\n"
            f"Energy: {voice_profile.get('avg_energy', 'medium')}\n"
            f"Common phrases: {', '.join(list(common)[:10])}\n"
            "Match this speaking style in the script."
        )

    return base


def fill_dynamic_placeholders(template: str, content_type: dict) -> str:
    """Replace {content_role} / {content_style} placeholders in a prompt template."""
    if not template:
        return template
    role = content_type.get("role") or CONTENT_TYPES["general"]["role"]
    style = content_type.get("style") or CONTENT_TYPES["general"]["style"]
    return (
        template.replace("{content_role}", role).replace("{content_style}", style)
    )

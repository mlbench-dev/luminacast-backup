"""AI-generated Pexels stock-search queries from a product's own photo.

Complements ``engine.cast_generator.build_pexels_query``'s text-based
candidates (product name + script key_points) rather than replacing them —
see the two rationales below.

Why this exists: build_pexels_query has to GUESS a product's visual category
by parsing its name/category text, and that guessing was the source of
several real bugs (a "gaming PC" bundle matching "ssd" because that was the
last surviving word, a long spec-heavy name matching nothing on Pexels at
all). A vision model looking at the product's actual photo doesn't need to
guess — it can tell a saucepan from a fry pan at a glance, which is exactly
the kind of distinction that's hard to get reliably from text alone.

Why it's additive, not a replacement: (1) reliability — the vision call can
fail (rate limit, no product image yet, malformed JSON), and every failure
here returns ``[]`` so the caller falls through to the always-available
text-based candidates instead of leaving a block with zero B-roll options;
(2) ``generate_ai_stock_queries`` alone has no notion of "block", only
"product" — it always returns the same 3 queries for a given product photo,
with no awareness of what any individual block's script line is about.

That second gap is what ``generate_block_stock_query`` closes: a cheap
TEXT-only (no image) call that combines the product's cached
``ai_visual_description`` (what it looks like — from a photo, once per
product) with THIS block's own script text (once per block) into one query
grounded in both signals, e.g. "stainless steel saucepan" + script "how
fast is it boiling" -> "water rapidly boiling pan", not just the product
alone. Kept as a separate vision call from ``generate_ai_stock_queries``
(rather than extending that prompt's output shape) so the already-validated
searches prompt/contract is never touched; the per-block combination step
itself never touches the image again, so it stays cheap even running once
per block instead of once per product.

Both vision outputs are cached on ``Product`` (``ai_stock_queries``,
``ai_visual_description`` — generated once per product) since the same
cover photo drives every block/render for that product.
"""
from __future__ import annotations

import json
import logging

import sentry_sdk

from services.creative_models import CREATIVE_DESCRIPTION_MODEL, log_creative_model_use
from services.openrouter import get_openrouter_service

logger = logging.getLogger(__name__)

# Verbatim system prompt supplied by the user after manually validating it
# against ChatGPT + Pexels (correctly returned "stainless steel saucepan"
# rather than a generic/wrong "fry pan" query for a saucepan product photo).
_SYSTEM_PROMPT = """You are an expert visual-search keyword generator for Amazon product promotional videos.

I will provide you with an image of an Amazon product.

Your task is to analyze the product image and generate exactly 3 highly effective Pexels video search queries that can be used to find stock footage suitable for creating a professional promotional video for that product.

IMPORTANT:

Do NOT simply describe the product.
Do NOT assume Pexels has the exact product.
Do NOT search for the Amazon listing or brand.
Focus on finding visually relevant stock footage that can complement the product.
Think about how the product is used, what action it performs, and the environment in which it is used.
Prioritize footage that would actually be useful in a promotional video.
Use common English phrases that people would realistically search for on Pexels.
Avoid overly technical terminology.
Avoid generic searches such as "product", "amazon", "advertisement", "shopping", or "home".
Each query should be different and target a useful visual angle.
Queries should normally contain 2-5 words.
Do not use marketing slogans or sentences.

Before generating the searches, identify internally:

What the product is.
Its primary function.
How people interact with it.
Where it is normally used.
What visual scenes would best communicate its purpose and benefits.

Then select ONLY the 3 strongest Pexels search queries.

Prioritize the queries in this order:

Product/category footage — videos showing the same or a very similar type of product.
Usage/action footage — videos showing the product being used or the main action associated with it.
Lifestyle/context footage — videos showing the environment, situation, or lifestyle where the product is relevant.

If an exact product/category search is unlikely to produce useful Pexels results, replace it with a highly relevant usage or lifestyle search.

OUTPUT FORMAT:

{
"searches": [
"search query 1",
"search query 2",
"search query 3"
]
}

The searches must be ordered from highest to lowest relevance.

Return ONLY valid JSON. Do not include explanations, descriptions, markdown, or additional text."""

_USER_TEXT = "Generate the 3 Pexels video search queries for this product image."


def _parse_searches(raw: str) -> list[str]:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = (
            "\n".join(lines[1:-1])
            if len(lines) > 1 and lines[-1].strip() == "```"
            else "\n".join(lines[1:])
        )
    data = json.loads(cleaned)
    searches = data.get("searches") if isinstance(data, dict) else None
    if not isinstance(searches, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in searches:
        s = str(s or "").strip()
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
        if len(out) >= 3:
            break
    return out


async def generate_ai_stock_queries(image_url: str) -> list[str]:
    """Call the vision LLM once for ``image_url``, return up to 3 queries.

    Never raises — any failure (network, bad JSON, empty result) is
    captured to Sentry and an empty list is returned so callers fall
    through to the existing text-based query builder unchanged.
    """
    if not image_url:
        return []
    try:
        log_creative_model_use("product_ai_stock_queries", CREATIVE_DESCRIPTION_MODEL)
        service = get_openrouter_service()
        raw = await service.describe_image(
            image_url=image_url,
            system_prompt=_SYSTEM_PROMPT,
            user_text=_USER_TEXT,
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=300,
            temperature=0.3,
        )
        return _parse_searches(raw)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("AI stock-query generation failed for %s: %s", image_url, e)
        return []


async def get_or_generate_product_ai_stock_queries(product, db, r2) -> list[str]:
    """Return ``product.ai_stock_queries``, generating + caching on first use.

    ``product`` is a ``models.product.Product`` row (not a dict); ``db`` is
    the active AsyncSession; ``r2`` is the R2 storage service (for
    resolving ``cover_image_key`` to a public URL). Returns ``[]`` (never
    raises) when the product has no cover image or generation fails.
    """
    cached = getattr(product, "ai_stock_queries", None)
    if isinstance(cached, list) and cached:
        return cached

    image_key = getattr(product, "cover_image_key", None)
    if not image_key:
        return []
    image_url = r2.get_public_url(image_key)
    if not image_url:
        return []

    queries = await generate_ai_stock_queries(image_url)
    if queries:
        try:
            product.ai_stock_queries = queries
            db.add(product)
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await db.rollback()
    return queries


# ---------------------------------------------------------------------------
# Per-block queries: combine the product's visual identity (image) with
# THIS block's own script text (no image) so the query is grounded in both
# "what does the product look like" and "what is this specific beat about"
# — e.g. product "stainless steel saucepan" + script "how fast is it
# boiling" -> "water rapidly boiling pan", not just the product alone.
#
# Kept as a SEPARATE vision call from generate_ai_stock_queries rather than
# extending that prompt's output shape, so the already-validated searches
# prompt/output contract is never touched. The per-block step itself is
# text-only (no image), so it's cheap even though it runs once per block
# instead of once per product.
# ---------------------------------------------------------------------------

_DESCRIPTION_SYSTEM_PROMPT = """You will be shown an image of a product.

Describe, in ONE short plain-English sentence (10-20 words), what the product physically looks like (material, shape, type) and how it is typically used or interacted with.

Do not mention the brand name or marketing language. This description will be combined with a video script line to build a stock-footage search query, so focus on visually concrete, searchable terms.

Return ONLY the sentence. No quotes, no JSON, no extra text."""

_BLOCK_QUERY_SYSTEM_PROMPT = """You write short Pexels stock-footage video search queries.

You will be given (1) a short description of what a product looks like and how it's used, and (2) text describing one beat of a video. That text can be EITHER a short spoken narration line (e.g. "I spent years chasing that perfect studio sound") OR a longer descriptive/creative-direction prompt written for an AI video generator (e.g. "Create a short, photorealistic product video of a child wearing the exact hoodie... Bright modern lighting, smooth camera motion, premium fashion commercial"). Handle both the same way.

Either way, extract the concrete visual SUBJECT and ACTION being described — what a camera would actually see (who/what, doing what, wearing/holding what). Ignore anything that is production/style direction rather than searchable content: camera moves, lighting descriptions, "cinematic"/"commercial"/"premium" framing words, aspect ratio or quality instructions. Pexels' catalog is tagged by concrete subjects and actions, not by directing language, so none of that belongs in the query.

CRITICAL — always keep the product's own visual identity in the query. Pull the product's core type/category from its description (e.g. "hoodie", "tracksuit", "smartwatch", "saucepan" — not its full name or brand) and combine it with the beat's action/context. Do NOT collapse the query down to the action alone and drop the product — a beat about "kids moving energetically" while the product is a striped hoodie must become something like "kids playing hoodie" or "child running hoodie", never a bare "kids playing" with no product mentioned at all. The query exists to find footage of the PRODUCT in that action/context, not generic footage of the action by itself.

Write ONE short Pexels search query (2-5 common English words) that would find stock footage matching that product + action combined — not a generic shot of either alone, but footage relevant to THIS beat specifically. Avoid brand names, marketing language, and words unlikely to appear in real stock-footage titles/tags.

Return ONLY the search query text. No quotes, no explanation, no punctuation beyond spaces."""


async def generate_ai_visual_description(image_url: str) -> str:
    """Vision call: one short plain-English sentence describing the product.

    Never raises — any failure returns "" so callers proceed without this
    extra signal, falling back to the existing text-based query candidates.
    """
    if not image_url:
        return ""
    try:
        log_creative_model_use("product_ai_visual_description", CREATIVE_DESCRIPTION_MODEL)
        service = get_openrouter_service()
        raw = await service.describe_image(
            image_url=image_url,
            system_prompt=_DESCRIPTION_SYSTEM_PROMPT,
            user_text="Describe this product.",
            model=CREATIVE_DESCRIPTION_MODEL,
            max_tokens=100,
            temperature=0.3,
        )
        text = (raw or "").strip().strip('"').strip()
        return text[:300]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("AI visual-description generation failed for %s: %s", image_url, e)
        return ""


async def get_or_generate_product_visual_description(product, db, r2) -> str:
    """Return ``product.ai_visual_description``, generating + caching on
    first use. Same shape/contract as get_or_generate_product_ai_stock_queries."""
    cached = getattr(product, "ai_visual_description", None)
    if isinstance(cached, str) and cached.strip():
        return cached

    image_key = getattr(product, "cover_image_key", None)
    if not image_key:
        return ""
    image_url = r2.get_public_url(image_key)
    if not image_url:
        return ""

    description = await generate_ai_visual_description(image_url)
    if description:
        try:
            product.ai_visual_description = description
            db.add(product)
            await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            await db.rollback()
    return description


async def generate_block_stock_query(visual_description: str, beat_text: str) -> str:
    """Text-only (no image) LLM call combining the product's cached visual
    description with THIS block's own script text into one short query.

    Never raises — any failure returns "" so the caller falls back to the
    existing product-level / text-based candidates unchanged. Returns ""
    immediately (no LLM call) when either input is empty — there's nothing
    beat-specific to combine.
    """
    visual_description = (visual_description or "").strip()
    beat_text = (beat_text or "").strip()
    if not visual_description or not beat_text:
        return ""
    try:
        service = get_openrouter_service()
        raw = await service.generate_text(
            prompt=f'Product: {visual_description}\nBeat text: "{beat_text}"',
            system_prompt=_BLOCK_QUERY_SYSTEM_PROMPT,
            model="anthropic/claude-3-haiku",
            max_tokens=30,
            temperature=0.3,
        )
        query = (raw or "").strip().strip('"').strip()
        return query[:80]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "Per-block AI stock-query generation failed for beat %r: %s",
            beat_text[:60], e,
        )
        return ""

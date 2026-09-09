"""Turn a script/LLM b-roll description into a stock-search-friendly query.

Pexels (and stock libraries in general) match a clip against ALL the words in
the query and get rapidly narrower as words are added. A shot-style phrase like
``"hoodie flat lay charcoal"`` matches almost nothing — hoodie footage is
*people wearing hoodies*, not overhead product arrangements — so Pexels loosens
to the words that DO have inventory ("flat lay" desks, "charcoal" backgrounds)
and drops "hoodie" entirely.

``stockify_query`` strips the how-it's-shot / aesthetic / filler words and keeps
the concrete subject (ideally ``<colour> <object>``), capped to a few words, so
the search stays on the thing itself.
"""
from __future__ import annotations

import re

# Words about HOW a shot looks / is framed / its mood — never about what's in
# it. Safe to drop. Kept deliberately tight: anything that could name a real
# subject ("morning", "window", "coffee", "hands", "street") is NOT here.
_STYLE_WORDS = frozenset({
    "flat", "lay", "flatlay", "flat-lay", "layflat",
    "closeup", "close-up", "close", "up", "macro", "extreme",
    "broll", "b-roll", "cutaway", "cutaways", "insert",
    "shot", "shots", "footage", "clip", "clips", "scene", "scenes", "sequence",
    "cinematic", "filmic", "aesthetic", "aesthetics", "vibe", "vibes",
    "mood", "moody", "tone", "toned", "grade", "graded",
    "backdrop", "bokeh", "blurred", "blur", "defocused",
    "slow", "motion", "fast", "slowmo", "slow-mo", "slowmotion",
    "timelapse", "hyperlapse", "warm", "cool", "tones", "vintage", "retro",
    "grainy", "faded", "muted", "vivid", "saturated", "desaturated",
    "overhead", "topdown", "top-down", "birdseye", "birds-eye", "pov",
    "angle", "angles", "wideangle", "closeshot", "midshot",
    "pan", "panning", "dolly", "tracking", "handheld", "gimbal", "static",
    "minimal", "minimalist", "clean", "simple", "sleek", "sleek",
    "pastel", "cozy", "cosy", "ambient", "ambiance", "ambience", "atmosphere",
    "atmospheric", "hero", "detail", "details", "texture", "textures",
    "dramatic", "elegant", "sophisticated", "modern", "contemporary",
    "trendy", "trending", "viral", "ugc", "tiktok", "reel", "reels", "short",
    "shorts", "scroll", "scrolling", "vertical", "horizontal",
    "studio", "professional", "highend", "high-end", "premium", "luxury",
    "luxe", "4k", "8k", "hd", "uhd", "fps", "raw", "footage",
    "stock", "video", "videos", "photo", "photos", "image", "images",
    "photography", "photoshoot", "shoot", "shooting", "render", "rendering",
    "beauty", "lifestyle", "commercial", "advert", "ad", "advertising",
    "closeupshot", "productshot", "product-shot",
})

# Grammatical filler — articles, prepositions, connectors, weak verbs.
_FILLER_WORDS = frozenset({
    "a", "an", "the", "of", "with", "on", "in", "into", "onto", "at", "by",
    "for", "to", "from", "and", "or", "as", "is", "are", "be", "being",
    "that", "this", "these", "those", "it", "its", "showing", "shows",
    "shown", "featuring", "feature", "featured", "depicting", "depicts",
    "some", "very", "really", "quite", "over", "under", "near", "against",
})

# Recognised colours — moved to the front of the query for a cleaner read
# ("hoodie charcoal" -> "charcoal hoodie"). Order-only, never dropped.
_COLOUR_WORDS = frozenset({
    "black", "white", "grey", "gray", "silver", "charcoal", "slate",
    "red", "crimson", "burgundy", "maroon", "scarlet",
    "blue", "navy", "teal", "cyan", "cobalt", "azure",
    "green", "olive", "emerald", "lime", "sage", "mint",
    "yellow", "gold", "golden", "mustard", "amber",
    "orange", "rust", "terracotta", "coral", "peach",
    "purple", "violet", "lavender", "plum", "lilac",
    "pink", "rose", "magenta", "fuchsia", "blush",
    "brown", "tan", "beige", "cream", "ivory", "khaki", "camel", "chocolate",
})

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")


def stockify_query(raw: str | None, *, max_words: int = 4) -> str:
    """Reduce ``raw`` to its concrete subject for a stock search.

    Drops shot-style / aesthetic / filler words, de-duplicates, caps to
    ``max_words``, and floats a colour word to the front. Returns ``""`` when
    nothing meaningful survives — the caller should fall back to the original.

    >>> stockify_query("hoodie flat lay charcoal")
    'charcoal hoodie'
    >>> stockify_query("cinematic close-up of person scrolling phone, moody")
    'person phone'
    >>> stockify_query("morning coffee by the window")
    'morning coffee window'
    """
    if not raw:
        return ""
    tokens = [t.lower() for t in _WORD_RE.findall(raw)]
    kept: list[str] = []
    for t in tokens:
        if t in _STYLE_WORDS or t in _FILLER_WORDS:
            continue
        if len(t) <= 1:
            continue
        if t in kept:
            continue
        kept.append(t)

    if not kept:
        return ""

    colours = [w for w in kept if w in _COLOUR_WORDS]
    others = [w for w in kept if w not in _COLOUR_WORDS]
    ordered = colours + others
    return " ".join(ordered[:max_words])

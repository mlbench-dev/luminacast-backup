"""Which video model the AI-generated b-roll pipeline uses.

The cast's ``broll_media_source`` carries the choice, mirroring how
``music_track_choice`` encodes ``track_id:<id>``:

    "stock"                 -> Pexels (default)
    "ai_generated"          -> AI b-roll, default model (kling_25)
    "ai_generated:<id>"     -> AI b-roll, that model

``<id>`` is one of ``AI_BROLL_MODELS`` below. Each entry names a
text-to-video endpoint (scene beats) and an image-to-video endpoint
(product beats). A missing endpoint for a mode falls back to the default
model's, so a bad slug only degrades one model/mode, never the pipeline.
"""
from __future__ import annotations

DEFAULT_MODEL_ID = "k25"

# Ordered BEST -> WORST (which is also, here, roughly most -> least expensive).
AI_BROLL_MODELS: list[dict] = [
    {
        "id": "veo3",
        "label": "Google Veo 3",
        "rank": 1,
        "cost_note": "~$2.50 / clip", "cost_usd": 2.5,
        "blurb": "Best quality — cinematic, native audio. Slow and pricey.",
        "t2v": "fal-ai/veo3",
        "i2v": "fal-ai/veo3/image-to-video",
    },
    {
        "id": "veo3f",
        "label": "Google Veo 3 — Fast",
        "rank": 2,
        "cost_note": "~$0.50 / clip", "cost_usd": 0.5,
        "blurb": "Near-Veo quality at a fraction of the cost.",
        "t2v": "fal-ai/veo3/fast",
        "i2v": "fal-ai/veo3/fast/image-to-video",
    },
    {
        "id": "k25",
        "label": "Kling 2.5 Turbo Pro",
        "rank": 3,
        "cost_note": "~$0.35 / clip", "cost_usd": 0.35,
        "blurb": "Strong, coherent motion. The default.",
        "t2v": "fal-ai/kling-video/v2.5-turbo/pro/text-to-video",
        "i2v": "fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
    },
    {
        "id": "k21",
        "label": "Kling 2.1 Master",
        "rank": 4,
        "cost_note": "~$0.35 / clip", "cost_usd": 0.35,
        "blurb": "Good motion, slightly older generation.",
        "t2v": "fal-ai/kling-video/v2.1/master/text-to-video",
        "i2v": "fal-ai/kling-video/v2.1/master/image-to-video",
    },
    {
        "id": "k16",
        "label": "Kling 1.6 Standard",
        "rank": 5,
        "cost_note": "~$0.20 / clip", "cost_usd": 0.2,
        "blurb": "Budget tier — softer, less dynamic.",
        "t2v": "fal-ai/kling-video/v1.6/standard/text-to-video",
        "i2v": "fal-ai/kling-video/v1.6/standard/image-to-video",
    },
    {
        "id": "k15",
        "label": "Kling 1.5 Pro",
        "rank": 6,
        "cost_note": "~$0.25 / clip", "cost_usd": 0.25,
        "blurb": "Oldest. Fine for animating a product photo.",
        "t2v": None,  # 1.5 pro is image-to-video only → scene beats fall back
        "i2v": "fal-ai/kling-video/v1.5/pro/image-to-video",
    },
]

_BY_ID = {m["id"]: m for m in AI_BROLL_MODELS}


def parse_broll_source(value: str | None) -> tuple[bool, str]:
    """``(is_ai_generated, model_id)``. ``model_id`` is always a valid id
    (default when unset/unknown). ``is_ai_generated`` is False for "stock"
    / None / anything else."""
    v = (value or "").strip()
    if not v.startswith("ai_generated"):
        return False, DEFAULT_MODEL_ID
    _, _, mid = v.partition(":")
    mid = mid.strip()
    return True, (mid if mid in _BY_ID else DEFAULT_MODEL_ID)


def endpoint_for(model_id: str, mode: str) -> str:
    """``mode`` is 't2v' or 'i2v'. Falls back to the default model's endpoint
    when the chosen model has none for that mode."""
    m = _BY_ID.get(model_id) or _BY_ID[DEFAULT_MODEL_ID]
    ep = m.get(mode)
    if ep:
        return ep
    return _BY_ID[DEFAULT_MODEL_ID][mode]


def public_list() -> list[dict]:
    """id / label / cost / blurb for the picker — no endpoints."""
    return [
        {k: m[k] for k in ("id", "label", "rank", "cost_note", "blurb")}
        for m in AI_BROLL_MODELS
    ]


def cost_for(model_id: str) -> float:
    m = _BY_ID.get(model_id) or _BY_ID[DEFAULT_MODEL_ID]
    return float(m.get("cost_usd") or 0.35)

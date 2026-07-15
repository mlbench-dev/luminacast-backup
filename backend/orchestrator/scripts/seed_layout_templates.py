"""Seed the 10 layout-template presets (data only — no selection/composer wiring).

Run once per environment (idempotent — safe to re-run):
  docker compose exec orchestrator python scripts/seed_layout_templates.py

Preset rows are distinguished by ``is_preset=True`` and ``user_id=NULL``. Each row
is upserted by a stable ``id`` (``lt_preset_<slug>``) so re-running never duplicates.

CONFIG SCHEMA (one source of truth — also enforced by
``tests/unit/test_layout_template_presets.py``):

    {
      "content_types": ["product_showcase"],   # detect_content_type ids that map here
      "face": "fullscreen|split_h|pip_quarter_bl|pip_quarter_br|hidden",
      "caption_preset": "hormozi_bold",         # id from frontend lib/captionPresets.ts
      "overlay": {"anchor": "bottom_center", "width_frac": 0.28, "margin": 40},
      "broll": {"mode": "none|light|heavy|full_frame"},
      "voice": {"mic": "on|off"},               # on => mic-on look + clip-mic EQ
      "scene": "studio|clean|cinematic|neutral|lifestyle|room|editorial|set|none"
    }

Caption-preset substitutions (the spec's names that do not exist verbatim in
lib/captionPresets.ts are mapped to the nearest existing id):
  - minimal_clean -> minimal_lower   (the "Minimal" preset; clean fade_in look)
  - pill          -> pill_highlight  (the "Pill Highlight" preset)
  - subtle_fade   -> minimal_lower   (nearest fade_in preset with a soft shadow)
  - urgency       -> caps_punch      (ALL CAPS, red highlight, slam_in — urgent look)
"""
import asyncio
import os
import sys

import sentry_sdk
from sqlalchemy import select

# Ensure the orchestrator package root is importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import async_session_factory  # noqa: E402
from models.layout_template import LayoutTemplate  # noqa: E402

# JSON-schema describing each preset's ``config`` object. Kept here as the single
# source of truth and imported by the unit test.
CONFIG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "content_types",
        "face",
        "caption_preset",
        "overlay",
        "broll",
        "voice",
        "scene",
    ],
    "properties": {
        "content_types": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
        },
        "face": {
            "type": "string",
            "enum": [
                "fullscreen",
                "split_h",
                "pip_quarter_bl",
                "pip_quarter_br",
                "hidden",
            ],
        },
        "caption_preset": {"type": "string", "minLength": 1},
        "overlay": {
            "type": "object",
            "additionalProperties": False,
            "required": ["anchor", "width_frac", "margin"],
            "properties": {
                "anchor": {"type": "string", "minLength": 1},
                "width_frac": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                "margin": {"type": "number", "minimum": 0},
            },
        },
        "broll": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mode"],
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["none", "light", "heavy", "full_frame"],
                },
            },
        },
        "voice": {
            "type": "object",
            "additionalProperties": False,
            "required": ["mic"],
            "properties": {"mic": {"type": "string", "enum": ["on", "off"]}},
        },
        "scene": {
            "type": "string",
            "enum": [
                "studio",
                "clean",
                "cinematic",
                "neutral",
                "lifestyle",
                "room",
                "editorial",
                "set",
                "none",
            ],
        },
    },
}

# Shared overlay defaults for every step-1 preset. Future steps may refine
# per-template; step 1 keeps a single value for all 10.
_OVERLAY = {"anchor": "bottom_center", "width_frac": 0.28, "margin": 40}


def _config(*, content_types, face, caption_preset, broll, mic, scene):
    return {
        "content_types": list(content_types),
        "face": face,
        "caption_preset": caption_preset,
        "overlay": dict(_OVERLAY),
        "broll": {"mode": broll},
        "voice": {"mic": mic},
        "scene": scene,
    }


# The 10 presets. Each entry: (id slug, display name, config).
PRESETS = [
    (
        "talking_head",
        "Talking Head",
        _config(content_types=["tutorial", "general"], face="fullscreen",
                caption_preset="hormozi_bold", broll="none", mic="on", scene="studio"),
    ),
    (
        "voiceover_explainer",
        "Voiceover Explainer",
        _config(content_types=["educational"], face="hidden",
                caption_preset="minimal_lower", broll="heavy", mic="off", scene="none"),
    ),
    (
        "product_spotlight",
        "Product Spotlight",
        _config(content_types=["product_showcase"], face="split_h",
                caption_preset="pill_highlight", broll="light", mic="on", scene="clean"),
    ),
    (
        "split_demo",
        "Split Demo",
        _config(content_types=["product_showcase"], face="split_h",
                caption_preset="karaoke_pop", broll="light", mic="on", scene="clean"),
    ),
    (
        "creative_motion",
        "Creative / Motion",
        _config(content_types=["motion_creative"], face="hidden",
                caption_preset="minimal_lower", broll="full_frame", mic="off",
                scene="cinematic"),
    ),
    (
        "educational_lecture",
        "Educational Lecture",
        _config(content_types=["educational"], face="pip_quarter_bl",
                caption_preset="block_quote", broll="heavy", mic="off", scene="neutral"),
    ),
    (
        "story_vlog",
        "Story / Vlog",
        _config(content_types=["storytelling"], face="fullscreen",
                caption_preset="minimal_lower", broll="light", mic="on",
                scene="lifestyle"),
    ),
    (
        "ugc_review",
        "UGC Review",
        _config(content_types=["product_showcase"], face="fullscreen",
                caption_preset="hormozi_bold", broll="light", mic="on", scene="room"),
    ),
    (
        "fashion_lookbook",
        "Fashion / Lookbook",
        _config(content_types=["fashion_lifestyle"], face="fullscreen",
                caption_preset="minimal_lower", broll="full_frame", mic="off",
                scene="editorial"),
    ),
    (
        "live_selling",
        "Live Selling",
        _config(content_types=["live_selling"], face="fullscreen",
                caption_preset="caps_punch", broll="light", mic="on", scene="set"),
    ),
]


def _preset_id(slug: str) -> str:
    return f"lt_preset_{slug}"


async def upsert_presets(session) -> dict:
    """Idempotently upsert the 10 presets into the given session.

    Returns a summary dict ``{"inserted": N, "updated": M}``. Caller commits.
    """
    inserted = 0
    updated = 0
    for slug, name, config in PRESETS:
        pid = _preset_id(slug)
        existing = (
            await session.execute(
                select(LayoutTemplate).where(LayoutTemplate.id == pid)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                LayoutTemplate(
                    id=pid,
                    user_id=None,
                    name=name,
                    config=config,
                    is_preset=True,
                )
            )
            inserted += 1
        else:
            existing.name = name
            existing.config = config
            existing.is_preset = True
            existing.user_id = None
            updated += 1
    return {"inserted": inserted, "updated": updated}


async def main() -> None:
    try:
        async with async_session_factory() as session:
            summary = await upsert_presets(session)
            await session.commit()
        print(
            f"Done: {summary['inserted']} inserted, {summary['updated']} updated "
            f"({len(PRESETS)} presets total)"
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        print(f"Seed failed: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    asyncio.run(main())

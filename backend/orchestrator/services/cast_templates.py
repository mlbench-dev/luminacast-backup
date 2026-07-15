"""Cast Generation Stage-1 creative templates.

A small, static catalog of concrete structural formats the user can pick
*before* writing a brief. Each template seeds the outline generator with a
default block sequence and a bias on how much screen time goes to the avatar
speaking vs. b-roll vs. uploaded video, plus a default mic-on toggle and a
caption preset. Picking a template constrains the LLM at outline time
(see ``engine.cast_generator.generate_outline``) instead of letting it choose
the whole structure freely.

This module is pure data + lookup helpers. No DB, no network. The block-type
strings match ``models.block.BlockType`` values; the caption_preset ids match
``frontend/companion-app/src/lib/captionPresets.ts`` so preview ≈ render.

User-facing strings here MUST NOT name any engine/provider (RULES.md).
"""
from __future__ import annotations

from typing import Optional

# Each template's bias is a dict summing (roughly) to 1.0 across three lanes:
#   avatar_speaking — avatar talking to camera
#   broll           — stock / generated / demo cutaways (no avatar face)
#   uploaded_video  — user-supplied footage placed on the timeline
# The outline generator reads these to weight how many blocks of each kind
# it produces. They are guidance, not hard quotas.

TEMPLATES: dict[str, dict] = {
    "talking_head_hook": {
        "id": "talking_head_hook",
        "name": "Talking Head Hook",
        "description": "Single take, avatar speaks straight to camera the whole time.",
        "block_sequence": ["HOOK", "STORY", "PRODUCT", "CTA"],
        "bias": {"avatar_speaking": 0.85, "broll": 0.15, "uploaded_video": 0.0},
        "default_mic_on": True,
        "default_caption_preset": "hormozi_bold",
        "est_duration_range": [20, 45],
        "preview_image_key": None,
    },
    "demo_heavy": {
        "id": "demo_heavy",
        "name": "Demo Heavy",
        "description": "Short hook, then close-up product b-roll with voiceover, ending on a CTA.",
        "block_sequence": ["HOOK", "PRODUCT_DEMO", "FEATURE_SHOWCASE", "PRODUCT_DEMO", "CTA"],
        "bias": {"avatar_speaking": 0.3, "broll": 0.7, "uploaded_video": 0.0},
        "default_mic_on": False,
        "default_caption_preset": "minimal_lower",
        "est_duration_range": [25, 50],
        "preview_image_key": None,
    },
    "multi_angle_story": {
        "id": "multi_angle_story",
        "name": "Multi-Angle Story",
        "description": "Alternates avatar close-up and wide angles every few seconds with jump-cut energy.",
        "block_sequence": ["HOOK", "STORY", "PRODUCT", "STORY", "CTA"],
        "bias": {"avatar_speaking": 0.65, "broll": 0.35, "uploaded_video": 0.0},
        "default_mic_on": True,
        "default_caption_preset": "karaoke_pop",
        "est_duration_range": [25, 55],
        "preview_image_key": None,
    },
    "social_proof_stack": {
        "id": "social_proof_stack",
        "name": "Social Proof Stack",
        "description": "Opens on stats, then alternates testimonial-style voiceover and product b-roll.",
        "block_sequence": ["SOCIAL_PROOF", "TESTIMONIAL", "PRODUCT", "SOCIAL_PROOF", "CTA"],
        "bias": {"avatar_speaking": 0.4, "broll": 0.6, "uploaded_video": 0.0},
        "default_mic_on": False,
        "default_caption_preset": "caps_punch",
        "est_duration_range": [25, 50],
        "preview_image_key": None,
    },
    "before_after_reveal": {
        "id": "before_after_reveal",
        "name": "Before/After Reveal",
        "description": "Split-screen or transition reveal built around a transformation.",
        "block_sequence": ["HOOK", "COMPARISON", "PRODUCT_DEMO", "COMPARISON", "CTA"],
        "bias": {"avatar_speaking": 0.45, "broll": 0.4, "uploaded_video": 0.15},
        "default_mic_on": True,
        "default_caption_preset": "pill_highlight",
        "est_duration_range": [20, 45],
        "preview_image_key": None,
    },
    "mic_on_creator_vlog": {
        "id": "mic_on_creator_vlog",
        "name": "Mic-On Creator Vlog",
        "description": "Casual mic-on look throughout, energetic delivery, captions do the heavy lifting.",
        "block_sequence": ["HOOK", "STORY", "PRODUCT", "QA", "CTA"],
        "bias": {"avatar_speaking": 0.8, "broll": 0.2, "uploaded_video": 0.0},
        "default_mic_on": True,
        "default_caption_preset": "tiktok_classic",
        "est_duration_range": [25, 55],
        "preview_image_key": None,
    },
}


def list_templates() -> list[dict]:
    """Return the launch-set templates as a list, ordered as defined.

    Each item carries the summary fields the Stage-1 picker needs:
    id, name, description, preview_image_key, block_count,
    est_duration_range, default_bias, default_mic_on, default_caption_preset.
    """
    out: list[dict] = []
    for tpl in TEMPLATES.values():
        out.append(
            {
                "id": tpl["id"],
                "name": tpl["name"],
                "description": tpl["description"],
                "preview_image_key": tpl.get("preview_image_key"),
                "block_count": len(tpl["block_sequence"]),
                "est_duration_range": tpl["est_duration_range"],
                "default_bias": tpl["bias"],
                "default_mic_on": tpl["default_mic_on"],
                "default_caption_preset": tpl["default_caption_preset"],
            }
        )
    return out


def get_template(template_id: Optional[str]) -> Optional[dict]:
    """Look up a full template config by id. Returns None for unknown/empty ids."""
    if not template_id:
        return None
    return TEMPLATES.get(template_id)

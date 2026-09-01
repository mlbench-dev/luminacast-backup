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
        "bias": {"avatar_speaking": 1.0, "broll": 0.0, "uploaded_video": 0.0},
        "default_mic_on": True,
        "default_caption_preset": "hormozi_bold",
        "est_duration_range": [20, 45],
        "preview_image_key": None,
        "video_generation_prompt": (
            "TALKING HEAD HOOK FORMAT (STRICT 100% DIRECT-TO-CAMERA PRESENTATION):\n"
            "- The entire video is a single continuous take of the avatar speaking directly to the camera with unbroken eye contact.\n"
            "- 100% of total screen time MUST be avatar_speaking. EVERY single block MUST be category=avatar_speaking.\n"
            "- Absolutely ZERO b-roll cutaways, ZERO stock video overlays, and ZERO voiceover-only blocks.\n"
            "- Framing starts at MEDIUM, shifting subtly to CLOSE for emotional emphasis, keeping the host center stage throughout."
        ),
        "visual_rules": [
            "Maintain category=avatar_speaking for 100% of all blocks (zero b-roll cutaways).",
            "Keep the avatar visible and speaking straight to camera from start to finish.",
            "Use direct-to-camera eye contact and animated, natural facial expressions.",
        ],
        "script_direction": (
            "Write as an unbroken, passionate direct-to-camera monologue. Open in the first 3 seconds with a bold, "
            "provocative hook, share a personal revelation or story, present the product solution with direct conviction, "
            "and close with an urgent, direct call to action."
        ),
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
        "video_generation_prompt": (
            "DEMO HEAVY FORMAT (STRICT PRODUCT SHOWCASE & B-ROLL FOCUS):\n"
            "- The core of this video (70%+ screen time) is close-up hands-on product demonstration and macro b-roll.\n"
            "- The avatar appears ONLY on the opening hook (first 3-4s) and the final CTA block.\n"
            "- All intermediate core blocks MUST be avatar_voiceover, stock_video, or product footage showcasing features, textures, and results.\n"
            "- Visuals prioritize crisp product detail, feature demonstrations, and problem-solving action rather than the host's face."
        ),
        "visual_rules": [
            "Avatar on-camera ONLY for the initial hook and final CTA.",
            "Middle blocks MUST use avatar_voiceover, stock_video, or product demonstration b-roll.",
            "Highlight macro details, hands-on usage, and tangible product benefits.",
        ],
        "script_direction": (
            "Write concise, descriptive voiceover narration synchronized with visual product cues. Explain what "
            "the viewer is seeing on screen, highlighting key benefits, features, and tangible value before wrapping with a crisp CTA."
        ),
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
        "video_generation_prompt": (
            "MULTI-ANGLE STORY FORMAT (DYNAMIC JUMP-CUT & ANGLE SWITCHING):\n"
            "- High-energy, fast-paced creator storytelling with dynamic camera framing variations.\n"
            "- Alternates camera framing across blocks (CLOSE -> WIDE -> ANGLE_LEFT_3Q -> ANGLE_RIGHT_3Q -> MEDIUM) every 2-4 seconds.\n"
            "- Weaves punchy b-roll cutaways between energetic talking-head takes with jump-cut rhythm.\n"
            "- Keeps viewers glued to the screen with constant visual momentum and dynamic perspective shifts."
        ),
        "visual_rules": [
            "Vary camera framing on every consecutive avatar block (never use the same angle twice in a row).",
            "Cycle actively between CLOSE, MEDIUM_WIDE, ANGLE_LEFT_3Q, and ANGLE_RIGHT_3Q.",
            "Fast-paced transitions with dynamic cuts and energetic b-roll punches.",
        ],
        "script_direction": (
            "Fast-paced, rhythmic narrative with quick sentence progression. Jump cuts land on punchlines and key words. "
            "High energy throughout with natural comedic or dramatic pauses."
        ),
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
        "video_generation_prompt": (
            "SOCIAL PROOF STACK FORMAT (DATA, REVIEWS & TESTIMONIAL DRIVEN):\n"
            "- Opens immediately on shocking statistics, rating numbers, or customer testimonial proof points.\n"
            "- Alternates testimonial-style avatar narration with on-screen review graphics, stats, and real-life product cutaways.\n"
            "- 60%+ screen time dedicated to proof elements, ratings, testimonials, and evidence.\n"
            "- Establishes undeniable credibility and peer consensus that drives viewer trust and conversion."
        ),
        "visual_rules": [
            "Open on bold social proof (numbers, 5-star ratings, customer review quotes).",
            "Intercut testimonial narration with proof-point b-roll and customer satisfaction visuals.",
            "Use stock_photo/stock_video and avatar_voiceover for review and stat breakdowns.",
        ],
        "script_direction": (
            "Authoritative, evidence-backed script. Open with compelling numbers ('Over 15,000 customers...', 'Rated 4.9 stars...'). "
            "Quote customer feedback, address common skepticism, and provide social proof before closing with a trust-rich CTA."
        ),
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
        "video_generation_prompt": (
            "BEFORE/AFTER REVEAL FORMAT (TRANSFORMATION & DRAMATIC CONTRAST):\n"
            "- Structured around a clear, dramatic transformation contrast (Problem State vs Solved State).\n"
            "- Opening hook plunges into the painful/frustrating 'BEFORE' reality.\n"
            "- Middle blocks build anticipation and deliver a dramatic visual reveal/transition to the 'AFTER' result.\n"
            "- Uses comparison blocks and demonstration footage to highlight the undeniable before-and-after transformation."
        ),
        "visual_rules": [
            "Establish stark visual contrast between the 'before' problem and 'after' solution.",
            "Feature comparison visuals, split-screen concepts, and a dramatic transition reveal beat.",
            "Balance avatar reaction with vivid transformation b-roll footage.",
        ],
        "script_direction": (
            "Create emotional contrast. Start with visceral frustration about the old way ('I used to spend hours struggling with...'). "
            "Build dramatic tension to the reveal moment ('Until I discovered this...'), showcase the glowing results, and call to action."
        ),
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
        "video_generation_prompt": (
            "MIC-ON CREATOR VLOG FORMAT (AUTHENTIC, CASUAL & CONVERSATIONAL):\n"
            "- Authentic, raw, unscripted-feel creator vlog aesthetic with visible clip-on mic throughout.\n"
            "- 80%+ screen time on avatar with natural, casual body language and intimate creator-to-community delivery.\n"
            "- Conversational flow addressing common audience questions (QA beats) like an organic vlog update.\n"
            "- Captions and natural micro-expressions carry the energy with minimal corporate polish."
        ),
        "visual_rules": [
            "Avatar on-camera for 80%+ of video wearing visible clip-on lavalier mic.",
            "Casual, relaxed framing with expressive gestures and organic creator vibe.",
            "Minimal stock b-roll — keep the focus on authentic personal sharing and product interaction.",
        ],
        "script_direction": (
            "Conversational, spontaneous-sounding dialogue. Use creator vernacular, answer audience questions ('You guys keep asking me about...'), "
            "share honest personal impressions, and invite viewers into the conversation."
        ),
    },
}


def list_templates() -> list[dict]:
    """Return the launch-set templates as a list, ordered as defined.

    Each item carries the summary fields the Stage-1 picker needs:
    id, name, description, preview_image_key, block_count,
    est_duration_range, default_bias, default_mic_on, default_caption_preset,
    video_generation_prompt, visual_rules, script_direction.
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
                "video_generation_prompt": tpl.get("video_generation_prompt"),
                "visual_rules": tpl.get("visual_rules", []),
                "script_direction": tpl.get("script_direction"),
            }
        )
    return out


def get_template(template_id: Optional[str]) -> Optional[dict]:
    """Look up a full template config by id. Returns None for unknown/empty ids."""
    if not template_id:
        return None
    return TEMPLATES.get(template_id)

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
        "block_blueprints": [
            {
                "beat": "HOOK",
                "block_type": "HOOK",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [3, 6],
                "framing": "MEDIUM",
                "purpose": "Provocative direct-to-lens hook challenging a common belief or sharing an urgent realization.",
            },
            {
                "beat": "STORY",
                "block_type": "STORY",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [8, 15],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Intimate personal backstory or struggle that builds emotional connection with unbroken eye contact.",
            },
            {
                "beat": "PRODUCT",
                "block_type": "PRODUCT",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [8, 16],
                "framing": "CLOSE",
                "purpose": "Direct, authentic recommendation explaining how the product solved the core problem.",
            },
            {
                "beat": "CTA",
                "block_type": "CTA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [4, 7],
                "framing": "MEDIUM",
                "purpose": "Urgent, confident call to action directly addressing the viewer to try it now.",
            },
        ],
        "video_generation_prompt": (
            "TALKING HEAD HOOK FORMAT (STRICT 100% DIRECT-TO-CAMERA PRESENTATION):\n"
            "- The entire video is a single continuous take of the avatar speaking directly to the camera with unbroken eye contact.\n"
            "- 100% of total screen time MUST be avatar_speaking. EVERY single block MUST be category=avatar_speaking with render_mode=avatar_full.\n"
            "- Absolutely ZERO b-roll cutaways, ZERO stock video overlays, and ZERO voiceover-only blocks.\n"
            "- Framing starts at MEDIUM for the hook, shifts subtly to MEDIUM_CLOSE/CLOSE for emotional emphasis, and ends at MEDIUM for the CTA."
        ),
        "visual_rules": [
            "Maintain category=avatar_speaking and render_mode=avatar_full for 100% of all blocks (zero b-roll cutaways).",
            "Keep the avatar visible, centered, and speaking straight to camera from the first frame to the final second.",
            "Use direct-to-camera eye contact, animated facial micro-expressions, and natural head movements throughout.",
        ],
        "script_direction": (
            "Write as an unbroken, passionate direct-to-camera monologue.\n"
            "- HOOK FORMULA: Open immediately in the first 3 seconds with a bold confession, contrarian truth, or direct warning "
            "(e.g., 'Stop doing X if you actually want Y...' or 'I used to think X was impossible until...').\n"
            "- NARRATIVE FLOW: Speak conversationally like a trusted friend sharing a private secret. Connect every thought seamlessly.\n"
            "- CTA FORMULA: Close with a direct, single-action command (e.g., 'Grab yours before they sell out — link in bio.').\n"
            "- BANNED: Never say 'Hey guys', 'Welcome back', 'Are you tired of...', or 'Check out this product'."
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
        "block_blueprints": [
            {
                "beat": "HOOK",
                "block_type": "HOOK",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [3, 5],
                "framing": "MEDIUM",
                "purpose": "Host sets up the problem or demo challenge in the first 3-4 seconds.",
            },
            {
                "beat": "PRODUCT_DEMO",
                "block_type": "PRODUCT_DEMO",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [6, 12],
                "framing": "CLOSE",
                "purpose": "Hands-on demonstration cutaway showing the product actively solving the problem in real-time.",
            },
            {
                "beat": "FEATURE_SHOWCASE",
                "block_type": "FEATURE_SHOWCASE",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [5, 10],
                "framing": "MACRO",
                "purpose": "Macro visual focus highlighting key textures, build quality, and specific engineering/design features.",
            },
            {
                "beat": "PRODUCT_DEMO",
                "block_type": "PRODUCT_DEMO",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [6, 12],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Visual proof of the final result, ease of use, or clean finish with voiceover commentary.",
            },
            {
                "beat": "CTA",
                "block_type": "CTA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [4, 6],
                "framing": "MEDIUM",
                "purpose": "Avatar returns on camera to deliver the final verdict and clear purchasing call to action.",
            },
        ],
        "video_generation_prompt": (
            "DEMO HEAVY FORMAT (STRICT PRODUCT SHOWCASE & B-ROLL FOCUS):\n"
            "- 70%+ of total screen time is dedicated to high-definition hands-on product demonstration and macro b-roll cutaways.\n"
            "- The host avatar appears on camera ONLY for the initial hook (3-4s) and the final CTA block (4-5s).\n"
            "- All intermediate core blocks MUST be category=avatar_voiceover with real product media cutaways.\n"
            "- Visuals prioritize crisp product detail, active handling, and tangible problem-solving proof over host face time."
        ),
        "visual_rules": [
            "Avatar appears on camera ONLY for the initial hook and final CTA blocks.",
            "All middle blocks MUST be avatar_voiceover with product footage/images as the primary visual background.",
            "Visual cues must illustrate exact physical actions matching the voiceover narration.",
        ],
        "script_direction": (
            "Write descriptive, synchronized voiceover narration timed to visual product cues.\n"
            "- HOOK FORMULA: Open with visual curiosity (e.g., 'Watch what happens when I put X to the test...' or 'Here is why this tool is replacing everything else.').\n"
            "- BODY DEMO: Walk through specific features and sensory details ('Notice how smooth...', 'In just 30 seconds...').\n"
            "- CTA FORMULA: Final summary and direct link instruction ('Click below to grab yours today.')."
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
        "block_blueprints": [
            {
                "beat": "HOOK",
                "block_type": "HOOK",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [3, 5],
                "framing": "CLOSE",
                "purpose": "Intense close-up hook jumping straight into the story conflict.",
            },
            {
                "beat": "STORY",
                "block_type": "STORY",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [6, 12],
                "framing": "WIDE",
                "purpose": "Wide angle shot contextualizing the situation and escalating the narrative conflict.",
            },
            {
                "beat": "PRODUCT",
                "block_type": "PRODUCT",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [6, 12],
                "framing": "ANGLE_LEFT_3Q",
                "purpose": "Dynamic b-roll punch showing the product breakthrough in action.",
            },
            {
                "beat": "STORY",
                "block_type": "STORY",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [6, 10],
                "framing": "ANGLE_RIGHT_3Q",
                "purpose": "Three-quarter angle take sharing the turning point realization with comedic or dramatic punch.",
            },
            {
                "beat": "CTA",
                "block_type": "CTA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [4, 6],
                "framing": "MEDIUM",
                "purpose": "Centred medium shot delivering the fast, punchy closing call to action.",
            },
        ],
        "video_generation_prompt": (
            "MULTI-ANGLE STORY FORMAT (DYNAMIC JUMP-CUT & ANGLE SWITCHING):\n"
            "- High-energy, fast-paced creator storytelling with dynamic camera framing variations on every cut.\n"
            "- Alternates camera framing across blocks: CLOSE -> WIDE -> ANGLE_LEFT_3Q -> ANGLE_RIGHT_3Q -> MEDIUM every 3-6 seconds.\n"
            "- Seamlessly weaves punchy product b-roll cutaways between energetic talking-head takes with jump-cut rhythm.\n"
            "- Keeps viewers glued to the screen with constant visual momentum and perspective shifts."
        ),
        "visual_rules": [
            "Vary camera framing on every consecutive block (never use the same framing twice in a row).",
            "Cycle dynamically between CLOSE, WIDE, ANGLE_LEFT_3Q, and MEDIUM.",
            "Fast-paced jump-cut energy with punchy visual cuts on transition beats.",
        ],
        "script_direction": (
            "Write a fast-paced, episodic story narrative with rhythmic sentence progression.\n"
            "- HOOK FORMULA: Plunge into the middle of the drama (e.g., 'So my entire weekend was almost ruined until this happened...').\n"
            "- PACING: Use quick sentence punchlines with comedic or dramatic pauses [pause].\n"
            "- CTA FORMULA: Fast, decisive wrap-up ('Trust me on this one — link is right down below.')."
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
        "block_blueprints": [
            {
                "beat": "SOCIAL_PROOF",
                "block_type": "SOCIAL_PROOF",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [3, 6],
                "framing": "MEDIUM",
                "purpose": "Opens immediately on statistical proof, review count, or rating shock point.",
            },
            {
                "beat": "TESTIMONIAL",
                "block_type": "TESTIMONIAL",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [6, 12],
                "framing": "CLOSE",
                "purpose": "Customer review quotes and real-world testimonial evidence breakdown with product visuals.",
            },
            {
                "beat": "PRODUCT",
                "block_type": "PRODUCT",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [6, 12],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Product capability showcase backing up the customer satisfaction claims.",
            },
            {
                "beat": "SOCIAL_PROOF",
                "block_type": "SOCIAL_PROOF",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [5, 10],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Avatar on camera breaking down why the community consensus agrees this is the top pick.",
            },
            {
                "beat": "CTA",
                "block_type": "CTA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [4, 6],
                "framing": "MEDIUM",
                "purpose": "Trust-rich closing call to action leveraging social proof momentum.",
            },
        ],
        "video_generation_prompt": (
            "SOCIAL PROOF STACK FORMAT (DATA, REVIEWS & TESTIMONIAL DRIVEN):\n"
            "- Opens immediately on shocking statistics, verified rating numbers, or customer testimonial proof points.\n"
            "- Alternates testimonial-style avatar narration with on-screen review graphics, stats, and real-life product cutaways.\n"
            "- 60%+ of screen time is dedicated to proof elements, ratings, testimonials, and tangible evidence.\n"
            "- Establishes undeniable credibility and peer consensus that drives viewer trust and conversion."
        ),
        "visual_rules": [
            "Open on bold social proof (numbers, 5-star ratings, customer review quotes).",
            "Intercut testimonial narration with proof-point product cutaways and satisfaction visuals.",
            "Use avatar_voiceover for review quotes and statistical breakdowns.",
        ],
        "script_direction": (
            "Write an authoritative, evidence-backed script centered on real-world proof.\n"
            "- HOOK FORMULA: Open with compelling numbers ('Over 15,000 verified 5-star reviews can't all be wrong...' or 'This has a 4.9 rating for a reason.').\n"
            "- PROOF STACK: Quote specific customer feedback, address common skepticism, and validate quality.\n"
            "- CTA FORMULA: Leverage momentum ('Join over 50,000 happy customers — get yours while stock lasts.')."
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
        "block_blueprints": [
            {
                "beat": "HOOK",
                "block_type": "HOOK",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [3, 5],
                "framing": "MEDIUM",
                "purpose": "Plunges directly into the visceral pain point and frustration of the 'Before' state.",
            },
            {
                "beat": "COMPARISON",
                "block_type": "COMPARISON",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [5, 10],
                "framing": "WIDE",
                "purpose": "Visual breakdown of the old, slow, or frustrating way of doing things.",
            },
            {
                "beat": "PRODUCT_DEMO",
                "block_type": "PRODUCT_DEMO",
                "category": "avatar_voiceover",
                "render_mode": "background",
                "target_duration_range": [6, 12],
                "framing": "CLOSE",
                "purpose": "The dramatic transformation moment — introducing the product solution in action.",
            },
            {
                "beat": "COMPARISON",
                "block_type": "COMPARISON",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [5, 10],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Avatar on camera showcasing the glowing, effortless 'After' results with relief.",
            },
            {
                "beat": "CTA",
                "block_type": "CTA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [4, 6],
                "framing": "MEDIUM",
                "purpose": "Final transformational call to action urging viewers to make the switch.",
            },
        ],
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
            "Balance avatar reaction with vivid transformation product footage.",
        ],
        "script_direction": (
            "Create emotional contrast between frustration and relief.\n"
            "- HOOK FORMULA: Start with visceral frustration about the old way ('I used to spend hours struggling with X every single day...').\n"
            "- REVEAL MOMENT: Build dramatic tension to the breakthrough ('Until I made this one simple switch...').\n"
            "- AFTER REACTION: Express genuine relief and satisfaction at the results.\n"
            "- CTA FORMULA: Invite the viewer to transform their routine ('Ditch the old way — tap below to upgrade today.')."
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
        "block_blueprints": [
            {
                "beat": "HOOK",
                "block_type": "HOOK",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [3, 6],
                "framing": "MEDIUM",
                "purpose": "Spontaneous behind-the-scenes creator hook bringing the viewer into the moment.",
            },
            {
                "beat": "STORY",
                "block_type": "STORY",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [8, 14],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Authentic personal walkthrough or daily vlog routine incorporating the product naturally.",
            },
            {
                "beat": "PRODUCT",
                "block_type": "PRODUCT",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [6, 12],
                "framing": "CLOSE",
                "purpose": "Close-up creator reaction and honest personal impressions while using the product.",
            },
            {
                "beat": "QA",
                "block_type": "QA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [6, 12],
                "framing": "MEDIUM_CLOSE",
                "purpose": "Answering common follower questions or addressing community curiosity directly.",
            },
            {
                "beat": "CTA",
                "block_type": "CTA",
                "category": "avatar_speaking",
                "render_mode": "avatar_full",
                "target_duration_range": [4, 6],
                "framing": "MEDIUM",
                "purpose": "Casual, friendly sign-off with clear directions on where to find the product.",
            },
        ],
        "video_generation_prompt": (
            "MIC-ON CREATOR VLOG FORMAT (AUTHENTIC, CASUAL & CONVERSATIONAL):\n"
            "- Authentic, raw, unscripted-feel creator vlog aesthetic with visible clip-on mic throughout.\n"
            "- 80%+ screen time on avatar with natural, casual body language and intimate creator-to-community delivery.\n"
            "- Conversational flow addressing common audience questions (QA beats) like an organic vlog update.\n"
            "- Captions and natural micro-expressions carry the energy with minimal corporate polish."
        ),
        "visual_rules": [
            "Avatar on-camera for 80%+ of video with natural, casual framing.",
            "Relaxed body language, expressive hand gestures, and organic creator vibe.",
            "Minimal stock b-roll — keep the focus on authentic personal sharing and direct interaction.",
        ],
        "script_direction": (
            "Write conversational, spontaneous-sounding creator dialogue.\n"
            "- HOOK FORMULA: Open like a mid-sentence conversation with friends ('Okay so everyone in my DMs has been asking about this...').\n"
            "- VLOG STYLE: Share honest impressions, natural reactions, and informal transitions ('I honestly didn't expect it to work this well...').\n"
            "- CTA FORMULA: Friendly, informal recommendation ('I linked the exact one I use below — let me know what you think in the comments!')."
        ),
    },
}


def list_templates() -> list[dict]:
    """Return the launch-set templates as a list, ordered as defined.

    Each item carries the summary fields the Stage-1 picker needs:
    id, name, description, preview_image_key, block_count,
    est_duration_range, default_bias, default_mic_on, default_caption_preset,
    video_generation_prompt, visual_rules, script_direction, block_blueprints.
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
                "block_blueprints": tpl.get("block_blueprints", []),
            }
        )
    return out


def get_template(template_id: Optional[str]) -> Optional[dict]:
    """Look up a full template config by id. Returns None for unknown/empty ids."""
    if not template_id:
        return None
    return TEMPLATES.get(template_id)


from models.variant import Variant, VariantStatus
"""
Cast Generation Pipeline.
Orchestrates the full generation of a Cast: scripts → TTS → video clips.

Cast generation uses webhooks: TTS is synchronous, then InfiniteTalk jobs
are submitted to RunPod with a webhook callback and the Celery task returns
immediately. RunPod POSTs results to /api/webhooks/runpod/infinitetalk.
"""
import asyncio
import logging
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

DEFAULT_MOTION_BY_ROLE = {
    "intro": "young person waving warmly at camera, bright friendly smile, hands at chest height, upbeat welcoming energy",
    "hook": "person leaning forward toward camera, curious excited expression, one hand gesturing for emphasis, engaging storyteller energy",
    "product_reveal": "person holding product at chest height with both hands, showcasing enthusiastically, bright smile, animated face",
    "product": "person holding product at chest height with both hands, showcasing enthusiastically, bright smile, animated face",
    "demo": "person demonstrating with animated hand gestures, pointing to features, engaged teaching expression, hands active",
    "social_proof": "person nodding affirmatively, warm confident expression, open-palm gestures, trustworthy calm posture",
    "cta": "person leaning forward, pointing toward camera, urgent excited energy, raised hand for emphasis",
    "flash_sale": "person leaning forward, pointing toward camera, urgent excited energy, raised hand for emphasis",
    "closing": "person waving goodbye warmly, bright friendly closing smile, relaxed posture",
}

def _default_motion_for_block_role(role: str) -> str:
    return DEFAULT_MOTION_BY_ROLE.get(
        role.lower(),
        "person talking enthusiastically to camera, animated expression, natural upper body gestures",
    )

# Categories the old `generate_outline` is allowed to emit. Subset of
# VALID_BLOCK_CATEGORIES (defined later for the Smart Cast pipeline) — we
# accept the same names so downstream Block.category handling is uniform
# regardless of which outline path produced the scenes.
_OUTLINE_ALLOWED_CATEGORIES = {
    "avatar_speaking",
    "avatar_voiceover",
    "avatar_action",
    "live_pip",
    "stock_video",
    "stock_photo",
}

# Legacy categories that should be auto-mapped to the new merged category.
# Any outline containing these is silently rewritten so old caches / fine-tuned
# prompts that still emit `avatar_motion` or `avatar_acting` keep working.
_LEGACY_CATEGORY_ALIASES = {
    "avatar_motion": "avatar_action",
    "avatar_acting": "avatar_action",
}

# Round-6 Bug B: valid camera framings the outline may assign per block. An
# unknown / missing value is coerced to MEDIUM so the pipeline never crashes.
_VALID_FRAMINGS = {
    "CLOSE",
    "MEDIUM",
    "MEDIUM_WIDE",
    "WIDE",
    "ANGLE_LEFT_3Q",
    "ANGLE_RIGHT_3Q",
}
_DEFAULT_FRAMING = "MEDIUM"


def _sanitize_outline_framing(scene: dict) -> None:
    """Coerce ``scene['framing']`` to a valid ShotFraming (default MEDIUM).

    Mutates the scene in place. Unknown / missing values fall back to MEDIUM so
    a malformed LLM response can't break look generation.
    """
    raw = (scene.get("framing") or "").strip().upper()
    scene["framing"] = raw if raw in _VALID_FRAMINGS else _DEFAULT_FRAMING


# Round-6 Bug B follow-up: deterministic rotation used to break up runs of
# identical framings on consecutive avatar blocks. LLMs ignore the "don't reuse
# the same framing twice in a row" instruction, so we enforce variety after the
# fact. The order is intentionally varied (medium → close → wide → angles) so a
# rotated block lands on a visibly different shot than its predecessor.
_FRAMING_ROTATION = [
    "MEDIUM",
    "CLOSE",
    "MEDIUM_WIDE",
    "ANGLE_LEFT_3Q",
    "MEDIUM",
    "ANGLE_RIGHT_3Q",
    "WIDE",
]

# Categories that put the avatar on camera — only these carry a meaningful
# camera framing. b-roll / stock / voiceover blocks don't show the avatar so
# their framing is irrelevant and excluded from the consecutive-duplicate check.
_AVATAR_ONSCREEN_CATEGORIES = {
    "avatar_speaking",
    "avatar_action",
    "live_pip",
    "pip_talking_head",
}


def _scene_is_avatar_onscreen(scene: dict) -> bool:
    cat = (scene.get("category") or "").strip().lower()
    if cat in _LEGACY_CATEGORY_ALIASES:
        cat = _LEGACY_CATEGORY_ALIASES[cat]
    return cat in _AVATAR_ONSCREEN_CATEGORIES


def _enforce_framing_variety(scenes: list[dict]) -> list[dict]:
    """Guarantee consecutive avatar-on-camera blocks don't share a framing.

    Belt-and-suspenders for the LLM, which is unreliable about the "do NOT reuse
    the same framing for two consecutive avatar blocks" instruction. Walks the
    scenes in order tracking each avatar block's ORIGINAL (LLM-emitted) framing;
    whenever a run of avatar blocks repeats the same original framing, every
    block after the first in that run is deterministically rotated through
    ``_FRAMING_ROTATION`` (advancing the cursor each time) so a run of N
    identical framings becomes N distinct shots. The assigned framing is also
    kept distinct from the immediately-preceding assigned framing. Mutates and
    returns the same list. Non-avatar blocks are ignored (their framing doesn't
    matter — the avatar isn't on screen).

    Returns the (possibly mutated) list; the caller logs ``[framing-rotation]``
    with the rotated count.
    """
    prev_original: str | None = None
    prev_assigned: str | None = None
    rotation_idx = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        # Normalise first so the comparison is against a valid value.
        _sanitize_outline_framing(scene)
        if not _scene_is_avatar_onscreen(scene):
            # Non-avatar block — doesn't affect the consecutive-avatar streak.
            continue
        original = scene["framing"]
        assigned = original
        # Rotate when the LLM repeated the previous avatar block's framing, OR
        # when our own assignment would collide with the previous assigned shot.
        needs_rotation = (
            prev_original is not None and original == prev_original
        ) or (
            prev_assigned is not None and assigned == prev_assigned
        )
        if needs_rotation:
            for _ in range(len(_FRAMING_ROTATION)):
                candidate = _FRAMING_ROTATION[rotation_idx % len(_FRAMING_ROTATION)]
                rotation_idx += 1
                if candidate != prev_assigned and candidate != original:
                    assigned = candidate
                    break
            else:
                # Degenerate fallback: pick anything that differs from prev.
                for candidate in _FRAMING_ROTATION:
                    if candidate != prev_assigned:
                        assigned = candidate
                        break
            scene["framing"] = assigned
            scene["_framing_rotated"] = True
        prev_original = original
        prev_assigned = assigned
    return scenes


def _count_framing_rotations(scenes: list[dict]) -> int:
    """Count + clear the transient ``_framing_rotated`` flags left by
    ``_enforce_framing_variety`` so they don't leak into the persisted scene."""
    n = 0
    for scene in scenes:
        if isinstance(scene, dict) and scene.pop("_framing_rotated", False):
            n += 1
    return n


def _sanitize_outline_categories(scenes: list[dict], cast_id: str) -> list[dict]:
    """Soft guard: ensure every scene from generate_outline carries a valid
    `category`. The prompt asks for it, but legacy LLMs / cached responses
    may omit it. We log + Sentry-capture the miss and default to
    avatar_speaking so the pipeline never crashes on a missing field.
    """
    import sentry_sdk
    cleaned: list[dict] = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            cleaned.append(scene)
            continue
        cat = (scene.get("category") or "").strip().lower()
        if cat in _LEGACY_CATEGORY_ALIASES:
            cat = _LEGACY_CATEGORY_ALIASES[cat]
        if not cat:
            _log(
                "warning",
                f"Outline scene {i} missing category, defaulting to avatar_speaking",
                cast_id=cast_id,
                block_index=i,
            )
            try:
                raise ValueError(
                    f"generate_outline scene {i} missing category — LLM did not follow schema"
                )
            except ValueError as exc:
                sentry_sdk.capture_exception(exc)
            cat = "avatar_speaking"
        elif cat not in _OUTLINE_ALLOWED_CATEGORIES:
            _log(
                "warning",
                f"Outline scene {i} returned unknown category {cat!r}, defaulting to avatar_speaking",
                cast_id=cast_id,
                block_index=i,
            )
            try:
                raise ValueError(
                    f"generate_outline scene {i} returned invalid category {cat!r}"
                )
            except ValueError as exc:
                sentry_sdk.capture_exception(exc)
            cat = "avatar_speaking"
        scene["category"] = cat
        # Round-6 Bug B: normalise the camera framing (default MEDIUM).
        _sanitize_outline_framing(scene)
        # PR #83 default pip_layout: if the LLM missed it for a speaking
        # block, pick a sensible default by block_type / hook_type. Keep
        # whatever the LLM emitted when it's a valid enum value.
        _apply_default_pip_layout(scene, cast_id=cast_id, block_index=i)
        cleaned.append(scene)
    return cleaned


def _apply_default_pip_layout(scene: dict, *, cast_id: str, block_index: int) -> None:
    """Fill ``scene['pip_layout']`` with a research-backed default when the
    LLM didn't emit one.

    Only applies to talking-head categories where the field actually matters
    (avatar_speaking, pip_talking_head). For other categories the field is
    irrelevant — we leave the scene alone.

    Emits one of the four canonical layout primitives (regression-5):
    ``fullscreen`` / ``split_h`` / ``pip_quarter_bl`` / ``pip_quarter_br``.
    Whatever the LLM emitted is coerced through the alias table so legacy
    values (``pip_small`` / ``top_half`` …) still resolve to a primitive.

    Defaults:
      - greeting / closing / cta blocks → "fullscreen" (direct camera, full
        canvas; the prosody-led, micro-expression-heavy moments where the
        viewer expects to look at the face).
      - everything else in the speaking set → "pip_quarter_bl" (quarter-area
        talking face, bottom-left; suppresses AI artifacts and lets product /
        b-roll breathe — the dominant social-commerce convention).
    """
    from layouts.primitives import LayoutPrimitive, coerce_to_primitive

    talking_categories = {"avatar_speaking", "pip_talking_head"}
    cat = (scene.get("category") or "").strip().lower()
    if cat not in talking_categories:
        return

    raw = scene.get("pip_layout")
    if isinstance(raw, str) and raw.strip() != "":
        # The LLM (or a legacy template) supplied a value — keep its intent by
        # coercing it onto a primitive rather than discarding it.
        scene["pip_layout"] = coerce_to_primitive(raw)
        return

    block_type = (scene.get("block_type") or "").strip().lower()
    fullscreen_block_types = {"hook", "cta", "closing", "intro", "testimonial"}
    if block_type in fullscreen_block_types or cat == "avatar_speaking" and block_type == "hook":
        scene["pip_layout"] = LayoutPrimitive.FULLSCREEN.value
    else:
        # product_demo, feature, social_proof, context, story, lesson, action…
        scene["pip_layout"] = LayoutPrimitive.PIP_QUARTER_BL.value

    try:
        _log(
            "info",
            "Default pip_layout applied",
            cast_id=cast_id,
            block_index=block_index,
            category=cat,
            block_type=block_type,
            pip_layout=scene["pip_layout"],
        )
    except Exception as exc:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)


GENERATION_RETRY_MAX = 3
GENERATION_FAILURE_THRESHOLD = 0.3  # 30%

# Speaking rate used for word-count budgeting. Conservative (lower than the
# ~150 wpm casual-speech average) so the generated script leaves headroom for
# natural pauses, prosody markers, and slightly slower TTS voices. Raising
# this number is the wrong fix for "audio too long" — TTS will still play at
# its actual cadence; we only get a cleaner cap by lowering it.
SPEAKING_WPM = 140
WORDS_PER_SECOND = SPEAKING_WPM / 60.0  # ≈ 2.33

# Post-generation slack — we allow up to 5% over the per-block target before
# we trim. Five percent is enough to absorb a stray clause without trimming
# every single block but tight enough that a 60s cast can't drift into 2:30.
SCRIPT_OVERSHOOT_SLACK = 1.05

# TTS overshoot warning threshold. Once an actual TTS clip exceeds the
# block's target by this multiplier we capture a Sentry message so we can
# spot voices/prompts that systematically blow the budget.
TTS_OVERSHOOT_WARN_RATIO = 1.10


def _word_budget_for_seconds(seconds: float) -> int:
    """Conservative word budget for a target duration at SPEAKING_WPM."""
    return max(1, int(round(float(seconds or 0) * WORDS_PER_SECOND)))


def _count_words(text: str) -> int:
    """Words for budgeting — strip prosody/SFX markers so they don't count."""
    if not text:
        return 0
    from utils.script_cleaning import strip_script_markers
    return len([w for w in strip_script_markers(text).split() if w.strip()])

# Model used for ALL script-generation LLM calls in this module. Centralised
# in services.creative_models so a single edit (or env override) flips every
# creative call site across the codebase — no risk of leaving one on the
# cheap default. Override per-deploy via CAST_GENERATOR_MODEL=... in the VPS
# .env if you want to A/B without code.
from services.creative_models import (
    CAST_GENERATOR_MODEL,
    get_outline_max_tokens,
    get_outline_self_correction_max_tokens,
    log_creative_model_use,
)


def _log(level: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps({
            "service": "cast_generator",
            "level": level,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **kwargs
        })
    )


async def _record_llm_usage(
    *,
    user_id: Optional[str],
    cast_id: Optional[str],
    model: str,
    usage: dict,
    event_type: str = "script_generation",
):
    """Best-effort log_usage for an LLM call.

    Opens a fresh DB session (we're typically inside a Celery task), and
    swallows any exception via Sentry — failing to log usage must never
    fail the user's render.
    """
    if not user_id or not usage:
        return
    try:
        import sentry_sdk
        from database import async_session_factory
        from services.usage_tracker import calculate_llm_cost, log_usage

        cost = calculate_llm_cost(
            model,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )
        async with async_session_factory() as session:
            await log_usage(
                session,
                user_id=user_id,
                event_type=event_type,
                provider="openrouter",
                provider_cost_usd=cost,
                quantity=int(usage.get("total_tokens") or 0),
                quantity_unit="tokens",
                resource_type="cast" if cast_id else None,
                resource_id=cast_id,
                provider_model=model,
            )
            await session.commit()
    except Exception as exc:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass


async def attach_layout_template_to_cast(cast, content_type_id: Optional[str], db) -> None:
    """Step 3 — record which layout-template preset a cast resolves to.

    Resolves the Step-2 selector for ``content_type_id`` and stamps the chosen
    template id onto ``cast.layout_template_id`` IN PLACE. The caller owns the
    transaction (the same one that persists the cast/blocks) and commits.

    Read-only field for now: nothing downstream consumes it yet (Step 4). It
    must NEVER block cast generation — any failure leaves
    ``cast.layout_template_id = None`` and is Sentry-captured.
    """
    import sentry_sdk

    try:
        from services.layout_selector import select_layout_template

        template = await select_layout_template(content_type_id, db)
        cast.layout_template_id = template.id
        _log(
            "info",
            f"cast {cast.id} -> template {template.id} (content_type={content_type_id})",
            cast_id=cast.id,
            layout_template_id=template.id,
            content_type=content_type_id,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        cast.layout_template_id = None
        _log(
            "warning",
            "layout template attach failed — leaving cast.layout_template_id unset",
            cast_id=getattr(cast, "id", None),
            content_type=content_type_id,
            error=str(e),
        )


# Step 5 — feature flag. Default ON; flip TEMPLATE_DEFAULTS_ENABLED=false to
# disable the template→block default stamping entirely (env-overridable, no
# hardcoded behaviour locked in).
def _template_defaults_enabled() -> bool:
    import os
    return os.environ.get("TEMPLATE_DEFAULTS_ENABLED", "true").strip().lower() == "true"


def _caption_preset_is_unset(value) -> bool:
    """A cast's caption_preset (JSON dict / None) counts as unset when it is
    falsey or carries no preset id. Only then does the template default win."""
    if not value:
        return True
    if isinstance(value, dict):
        return not (value.get("id") or "").strip()
    return False


async def _resolve_ready_mic_on_look_id(avatar_id, db) -> Optional[str]:
    """Return the id of a ready ``mic_on_*`` look for ``avatar_id``, or None.

    Prefers an original/default mic-on look, then falls back to the most
    recently created ready mic-on look. Returns None (caller leaves the block's
    ``avatar_look_id`` NULL → renderer falls back to the base look) when the
    avatar has no ready mic-on look yet. Never raises.
    """
    if not avatar_id:
        return None
    try:
        from sqlalchemy import select as _sa_select

        from models.avatar_look import AvatarLook
        from services.mic_on_look import MIC_ON_LOOK_TYPE_PREFIX

        result = await db.execute(
            _sa_select(AvatarLook)
            .where(AvatarLook.avatar_id == avatar_id)
            .where(AvatarLook.look_type.like(f"{MIC_ON_LOOK_TYPE_PREFIX}%"))
            .where(AvatarLook.status == "ready")
            .order_by(
                AvatarLook.is_original.desc(),
                AvatarLook.is_default.desc(),
                AvatarLook.created_at.desc(),
            )
            .limit(1)
        )
        look = result.scalars().first()
        return look.id if look is not None else None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None


async def bind_mic_on_look_to_blocks(cast, blocks, db) -> None:
    """regr-wiring: bind the avatar's ``mic_on_*`` look to mic-on blocks.

    For every block with ``mic_on == True`` whose ``avatar_look_id`` is unset,
    resolve the avatar's ready ``mic_on_*`` look and assign it so the renderer
    bakes the clip-on lavalier variant instead of the clean base look.

    If no ready mic-on look exists the block's ``avatar_look_id`` is left as-is
    (NULL → renderer falls back to the base look; the resolver in cast_render
    can still lazy-generate at render time). When the avatar has NO mic-on look
    at all, a best-effort async generation is triggered so the variant exists by
    the time the cast is rendered. Caller owns the transaction/commit.

    Never blocks generation: any failure is Sentry-captured and swallowed.
    """
    import sentry_sdk

    try:
        from services.mic_on_look import mic_on_look_enabled

        if not mic_on_look_enabled():
            return

        avatar_id = getattr(cast, "avatar_id", None)
        if not avatar_id:
            return

        mic_on_blocks = [b for b in blocks if getattr(b, "mic_on", None) is True]
        if not mic_on_blocks:
            return

        look_id = await _resolve_ready_mic_on_look_id(avatar_id, db)

        if look_id is not None:
            bound = 0
            for block in mic_on_blocks:
                if not getattr(block, "avatar_look_id", None):
                    block.avatar_look_id = look_id
                    bound += 1
            _log(
                "info",
                f"mic-on look bound to blocks: avatar={avatar_id} "
                f"look={look_id} bound={bound}/{len(mic_on_blocks)}",
                cast_id=getattr(cast, "id", None),
                avatar_id=avatar_id,
                mic_on_look_id=look_id,
            )
            return

        # No ready mic-on look yet. Leave avatar_look_id NULL so the render
        # never blocks: cast_render's resolve_mic_on_face_key lazy-generates
        # the variant on first use (and falls back to the clean base look if
        # generation isn't ready). We deliberately do NOT run the FLUX bake
        # inline here — that network call would stall the outline-persist
        # request. Generation stays on the render path's best-effort lazy hook.
        _log(
            "info",
            f"mic-on look not ready for avatar={avatar_id} — leaving "
            f"avatar_look_id NULL; render-time resolver will lazy-generate",
            cast_id=getattr(cast, "id", None),
            avatar_id=avatar_id,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log(
            "warning",
            "mic-on look binding failed — leaving blocks unchanged",
            cast_id=getattr(cast, "id", None),
            error=str(e),
        )


async def stamp_template_defaults_on_blocks(cast, blocks, db) -> None:
    """Step 5 — feed the cast's layout-template defaults into the blocks.

    Defaults only: a block/cast value that is already set is NEVER overwritten —
    explicit user/block overrides always win. Stamps, when unset:
      * caption_preset (cast-level JSON in this schema) ← template caption_preset id
      * Block.mic_on                                    ← template voice.mic == "on"

    A template's ``scene`` field used to also auto-stamp ``Block.background_id``
    by matching it (case-insensitive, by NAME) against an AvatarBackground row —
    removed. That match was invisible and undiscoverable (nothing in the UI ever
    showed which scene name a template expected, or let a user set
    background_id directly at all — it only ever happened automatically) and,
    worse, silently outranked whatever a user explicitly picked in the Script
    tab's Effects panel (Cast.effects_config.background), which IS a real,
    visible, user-controlled way to set a cast's background. Existing blocks
    that already have a background_id from before this change keep rendering
    with it (the compositor's own read path is unchanged) — this only stops
    NEW blocks from silently getting one stamped on.

    Must run AFTER ``attach_layout_template_to_cast`` so ``cast.layout_template_id``
    is known. If the selector failed (id is NULL) we skip entirely and preserve
    current behaviour. Caller owns the transaction/commit.

    Never blocks generation: any failure is Sentry-captured and swallowed.
    """
    import sentry_sdk

    if not _template_defaults_enabled():
        _log("info", "template defaults disabled via flag — skipping stamping",
             cast_id=getattr(cast, "id", None))
        return

    template_id = getattr(cast, "layout_template_id", None)
    if not template_id:
        # Selector failed / no template — preserve current behaviour.
        return

    try:
        from sqlalchemy import select

        from models.layout_template import LayoutTemplate

        tpl = (
            await db.execute(
                select(LayoutTemplate).where(LayoutTemplate.id == template_id)
            )
        ).scalars().first()
        if tpl is None:
            return
        config = tpl.config or {}

        caption_preset_id = config.get("caption_preset")
        mic_default = (config.get("voice") or {}).get("mic") == "on"

        # Caption preset is cast-level in this schema (Cast.caption_preset JSON).
        # Stamp the template's preset id only when the cast has none — the
        # frontend resolves the full style object from this id.
        if caption_preset_id and _caption_preset_is_unset(getattr(cast, "caption_preset", None)):
            cast.caption_preset = {"id": caption_preset_id}

        for block in blocks:
            if getattr(block, "mic_on", None) is None:
                block.mic_on = mic_default

        _log(
            "info",
            (
                f"template defaults stamped: blocks={len(blocks)} "
                f"caption_preset={caption_preset_id} mic={mic_default}"
            ),
            cast_id=getattr(cast, "id", None),
            layout_template_id=template_id,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log(
            "warning",
            "template defaults stamping failed — leaving blocks unchanged",
            cast_id=getattr(cast, "id", None),
            layout_template_id=template_id,
            error=str(e),
        )


def _build_template_constraint(template: Optional[dict]) -> str:
    """Render a Stage-1 creative template into a prompt constraint block.

    Returns "" when no template is selected (Auto mode) so the prompt is
    unchanged from the legacy free-choice behavior. When a template is set,
    the LLM is told to follow the template's block sequence and to weight
    screen time per the bias ratios. The mapping from bias lanes to scene
    categories: avatar_speaking -> avatar_speaking, broll -> stock_video /
    stock_photo / avatar_voiceover, uploaded_video -> stock_video.
    """
    if not template:
        return ""
    seq = template.get("block_sequence") or []
    bias = template.get("bias") or {}
    seq_str = " -> ".join(seq) if seq else "your choice"
    avatar_pct = int(round(float(bias.get("avatar_speaking", 0)) * 100))
    broll_pct = int(round(float(bias.get("broll", 0)) * 100))
    uploaded_pct = int(round(float(bias.get("uploaded_video", 0)) * 100))
    return f"""
TEMPLATE CONSTRAINT — the user picked the "{template.get('name', 'custom')}" format. Follow it:
- Produce blocks that follow this structural sequence (repeat/extend beats only as duration requires): {seq_str}
- Weight screen time roughly: {avatar_pct}% avatar speaking to camera, {broll_pct}% b-roll / product cutaways, {uploaded_pct}% uploaded footage.
- When b-roll is favored, prefer categories avatar_voiceover / stock_video / stock_photo for those beats instead of a talking head.
- Keep the opening beat a strong hook and the final beat a clear CTA regardless of the sequence above.
"""


def _build_live_defaults_section(live_mode_defaults: Optional[dict]) -> str:
    """Render the Stage-1 LIVE-mode preset object into a prompt constraint.

    ``live_mode_defaults`` is the object the SetupPhase LIVE/Recorded toggle
    POSTs (see PR #162). Returns "" when unset so Recorded casts and casts
    created before the field existed keep the legacy prompt. Only the keys we
    know how to honor are surfaced; unknown keys are ignored rather than
    dumped raw into the prompt.
    """
    if not live_mode_defaults:
        return ""
    lines: list[str] = []
    voiceover = live_mode_defaults.get("voiceover")
    if voiceover is not None:
        lines.append(
            "- Voiceover is ON: lean on avatar_voiceover blocks (narration over b-roll)."
            if voiceover
            else "- Voiceover is OFF: keep the avatar on camera (avatar_speaking); avoid voiceover-only beats."
        )
    broll = live_mode_defaults.get("broll")
    if broll is not None:
        lines.append(
            "- B-roll is ON: intercut stock_video / stock_photo cutaways between talking-head beats."
            if broll
            else "- B-roll is OFF: do not insert stock cutaways; stay on the avatar."
        )
    max_duration = live_mode_defaults.get("max_duration_seconds") or live_mode_defaults.get("max_duration")
    if max_duration:
        lines.append(f"- Target total runtime around {max_duration} seconds; size the block count to fit.")
    broll_cadence = live_mode_defaults.get("broll_cadence_seconds") or live_mode_defaults.get("broll_cadence")
    if broll_cadence:
        lines.append(f"- Aim for a b-roll cutaway roughly every {broll_cadence} seconds of narration.")
    if not lines:
        return ""
    return "LIVE-MODE DEFAULTS — honor these creator presets:\n" + "\n".join(lines) + "\n"


async def generate_outline(
    cast_id: str, products: list[dict], persona: dict, template_name: str,
    script_direction: str = None, cast_type: str = "recorded",
    description: str = None, target_audience: dict = None,
    duration_target_seconds: int = None, platform_target: str = "tiktok",
    user_id: Optional[str] = None, template: Optional[dict] = None,
    live_assessment: Optional[dict] = None, live_reference_id: Optional[str] = None,
    live_mode_defaults: Optional[dict] = None, production_level: str = "standard",
) -> list[dict]:
    """
    Generate script outline for a Cast using LLM.
    Returns list of scene outlines with block_type, category, mood, key_points,
    etc. The `category` field drives the visual layout (avatar_speaking,
    avatar_voiceover, avatar_action, live_pip, stock_video, stock_photo) — when
    the LLM omits it or returns an unknown value, we log + Sentry-capture and
    fall back to avatar_speaking so the pipeline keeps working.

    When description is provided, uses intent-driven generation that considers
    the user's goal, avatar target audience, and duration target.

    When `template` (a Stage-1 creative template dict from
    services.cast_templates) is provided, the prompt is constrained to that
    template's block sequence + bias ratios. Null = Auto (free choice).
    """
    from services.openrouter import get_openrouter_service

    # Round-6 Bug B follow-up: confirm WHICH generator ran. The validation hit
    # generate-outline but the framing audit never fired — this WARN-level entry
    # line lets us see in the worker logs which outline path actually executed.
    logger.warning("[prompt-audit-entry] cast=%s generator=generate_outline", cast_id)

    template_constraint = _build_template_constraint(template)
    production_level = _normalize_production_level_for_generation(production_level)
    effective_duration_target = _effective_duration_target_seconds(
        duration_target_seconds, template, production_level,
    )
    live_defaults_section = _build_live_defaults_section(live_mode_defaults)
    if live_defaults_section:
        logger.info("[live-defaults] cast=%s applying live_mode_defaults=%s", cast_id, live_mode_defaults)

    # Live Style Reference — distilled cadence/structure from a past live (Step 5).
    # Built unconditionally so the reconciliation rules are always present, even
    # when no assessment is found (build_live_style_section emits the rules alone).
    from engine.live_style import select_exemplars, build_live_style_section
    _brief_text = description or script_direction or ""
    _product_desc = " ".join((p.get("description") or "") for p in (products or []))
    _selected_exemplars = select_exemplars(live_assessment, _product_desc, _brief_text)
    live_style_section = build_live_style_section(live_assessment, _selected_exemplars)

    live_constraint = ""
    if cast_type == "live":
        live_constraint = """
IMPORTANT: This is a LIVE SESSION cast. Each block must start and end with a smooth transition phrase that allows blocks to be reshuffled in any order without sounding jarring. Avoid 'first', 'next', 'finally' — use neutral connectives. Each block should be self-contained but flow into any other block."""

    # Intent-driven generation when description is provided
    if description:
        ta_section = ""
        if target_audience:
            ta_age = target_audience.get("age_range", "")
            ta_interests = target_audience.get("interests", "")
            ta_desc = target_audience.get("description", "")
            ta_section = f"""
TARGET AUDIENCE (derived from the avatar):
Age range: {ta_age}
Interests: {ta_interests}
{ta_desc}
"""

        products_section = ""
        if products:
            for p in products:
                products_section += f"""
PRODUCT: {p.get('name', 'Unknown')}
{p.get('description', '')}
Key benefits: {', '.join(p.get('key_benefits', [])) if p.get('key_benefits') else 'N/A'}
Price: {p.get('price', 'N/A')}
"""

        avatar_visual_section = ""
        av_desc = (persona.get("visual_description") or persona.get("description") or "").strip()
        av_name = (persona.get("name") or "").strip()
        if av_desc or av_name:
            avatar_visual_section = f"""
AVATAR (the on-camera host):
Name: {av_name or 'Host'}
Description: {av_desc or 'Professional creator'}
"""

        duration_directive = (
            f"Exactly {effective_duration_target} seconds"
            if effective_duration_target
            else "Between 30 and 90 seconds, pick what fits the goal"
        )

        style_dna_section = _build_style_dna_section(persona)
        prompt = f"""You are writing a {platform_target.replace('_', ' ')}-style video script.

{style_dna_section}USER GOAL:
{description}

{ta_section}
{avatar_visual_section}
{live_style_section}
{products_section}

DURATION TARGET:
{duration_directive}

{live_constraint}
{template_constraint}
{live_defaults_section}
Produce a cast script as N blocks where:
- N is derived from the duration (roughly one block per 10-15 seconds of speech)
- Each block has a specific purpose that serves the user goal
- Each block's script is tuned to the target audience's voice and interests
- First block hooks immediately (no "Hi everyone, welcome to my live")
- Last block has a clear CTA tied to the product
- MIX block categories — never produce all avatar_speaking. If the brief describes
  motion / action / a setting (running, jungle, office, dancing, walking),
  use avatar_action or stock_video for those moments instead of a talking head.

Each block MUST include a `category` field — one of:
  avatar_speaking | avatar_voiceover | avatar_action | live_pip | stock_video | stock_photo
Pick the category that matches the scene. See the system prompt for definitions.

For avatar on-camera blocks, vary `framing` across the cast. Default starting frame is MEDIUM; use CLOSE for emotional emphasis, MEDIUM_WIDE for product hand-off moments, ANGLE_LEFT_3Q / ANGLE_RIGHT_3Q for transitions. Do NOT reuse the same framing for two consecutive avatar blocks. Allowed values: CLOSE | MEDIUM | MEDIUM_WIDE | WIDE | ANGLE_LEFT_3Q | ANGLE_RIGHT_3Q.

Return JSON array: [{{"block_type": "...", "category": "...", "framing": "MEDIUM", "mood": "...", "purpose": "...", "key_points": [...], "style_directives": [...], "estimated_duration_seconds": N, "motion_prompt": "<vivid 15-30 word action/setting description, REQUIRED for avatar_action — describe the SCENE and the action; do NOT repeat the avatar's appearance, the system prepends it>", "action_start_prompt": "<1-2 sentences for the FIRST scene frame (camera angle, pose, expression, scene/wardrobe) — REQUIRED for avatar_action>", "action_end_prompt": "<1-2 sentences for the FINAL scene frame using the same vocabulary — REQUIRED for avatar_action>", "product_name": "..." or null}}]"""
    else:
        prompt = f"""Create a live selling script outline for a TikTok live stream.
Template: {template_name}
Creator persona: {json.dumps(persona)}

{live_style_section}
Products: {json.dumps(products)}

Creator direction: {script_direction or 'No specific direction given'}
{live_constraint}
{template_constraint}
{live_defaults_section}
For each scene/block, provide:
- block_type (intro, product, flash_sale, social_proof, cta, filler, closing)
- category (one of: avatar_speaking | avatar_voiceover | avatar_action | live_pip | stock_video | stock_photo) — pick the visual layout that fits the scene; do NOT default everything to avatar_speaking. Mix categories.
- framing (one of: CLOSE | MEDIUM | MEDIUM_WIDE | WIDE | ANGLE_LEFT_3Q | ANGLE_RIGHT_3Q) — for avatar on-camera blocks, vary the camera framing across the cast. Default starting frame is MEDIUM; use CLOSE for emotional emphasis, MEDIUM_WIDE for product hand-off moments, ANGLE_LEFT_3Q / ANGLE_RIGHT_3Q for transitions. Do NOT reuse the same framing for two consecutive avatar blocks.
- mood (energetic, intimate, urgent, etc.)
- key_points (list of selling points to cover)
- style_directives (camera/presentation instructions)
- estimated_duration_seconds
- motion_prompt (REQUIRED when category is avatar_action — vivid 15-30 word description of the action + setting + lighting + camera angle. Do NOT repeat the avatar's appearance — the system auto-prepends it.)
- action_start_prompt (REQUIRED when category is avatar_action — 1-2 sentences describing the FIRST scene frame: camera angle, pose, expression, scene/wardrobe)
- action_end_prompt (REQUIRED when category is avatar_action — 1-2 sentences describing the FINAL scene frame, where the motion lands)
- product_name (if applicable)

Return valid JSON array of scenes."""

    from services.ai_prompts import get_prompt
    from services.gesture_thesaurus import GESTURE_THESAURUS
    from services.content_type import detect_content_type, fill_dynamic_placeholders
    system_key = "cast_script_generation" if description else "cast_outline_generator"
    outline_prompt = get_prompt(system_key)
    # Universal content engine: detect what the user actually wants and
    # adapt the script-writer persona instead of forcing live-selling mode.
    content_type = await detect_content_type(description or "", products)
    system_text = fill_dynamic_placeholders(outline_prompt["system"], content_type)
    system_text = system_text.replace(
        "{gesture_keys}", ", ".join(GESTURE_THESAURUS.keys())
    )
    _oai = get_openrouter_service()
    log_creative_model_use(
        "cast_script_generation" if description else "cast_outline_generation",
        CAST_GENERATOR_MODEL,
    )
    logger.info(
        "[prompt-audit] cast=%s kind=outline live_ref_id=%s exemplars_used=%d system=%r user=%r",
        cast_id, live_reference_id, len(_selected_exemplars), system_text, prompt,
    )
    response = await _oai.generate_text(
        prompt=prompt,
        system_prompt=system_text,
        model=CAST_GENERATOR_MODEL,
        max_tokens=get_outline_max_tokens(),
        temperature=0.8,
    )
    await _record_llm_usage(
        user_id=user_id, cast_id=cast_id,
        model=CAST_GENERATOR_MODEL,
        usage=getattr(_oai, "last_usage", {}),
    )

    # Strip markdown code fences if present (LLMs often wrap JSON in ```json ... ```)
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Remove first line (```json) and last line (```)
        if lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
        cleaned = cleaned.strip()

    # Also handle case where response has leading/trailing text around the JSON
    if not cleaned.startswith("["):
        # Try to find the JSON array in the response
        start_idx = cleaned.find("[")
        end_idx = cleaned.rfind("]")
        if start_idx != -1 and end_idx != -1:
            cleaned = cleaned[start_idx:end_idx + 1]

    try:
        scenes = json.loads(cleaned)
        if isinstance(scenes, list):
            # Round-6 Bug B follow-up: confirm scenes parsed + show the shape so
            # we can tell whether the framing fields survived to this point.
            _first_keys = sorted(scenes[0].keys()) if scenes and isinstance(scenes[0], dict) else []
            logger.warning(
                "[prompt-audit-parsed] cast=%s generator=generate_outline n_scenes=%d first_scene_keys=%s",
                cast_id, len(scenes), _first_keys,
            )
            scenes = _sanitize_outline_categories(scenes, cast_id=cast_id)
            # Collapse duplicate-position siblings and exact-duplicate beats so
            # the persist loop can never emit two rows for one (position, type,
            # category) tuple (cst_0f43a80b624d). Mirrors generate_smart_outline.
            scenes = _dedupe_outline_by_position(scenes, cast_id=cast_id)
            scenes = dedupe_outline_scenes(scenes, cast_id=cast_id)
            # Round-6 Bug B follow-up: GUARANTEE framing variety regardless of
            # LLM compliance. Rotate any consecutive avatar block that repeats
            # its predecessor's framing.
            scenes = _enforce_framing_variety(scenes)
            _rotated = _count_framing_rotations(scenes)
            if _rotated:
                logger.info("[framing-rotation] cast=%s rotated=%d blocks", cast_id, _rotated)
            _log("info", "Outline generated successfully", cast_id=cast_id, scene_count=len(scenes))
            # Round-6 Bug B: emit the per-block framing so we can verify variety
            # in the logs (extends the PR #163 [prompt-audit] line).
            _framings = [s.get("framing") for s in scenes if isinstance(s, dict)]
            logger.info(
                "[prompt-audit] cast=%s kind=outline framing=%s distinct=%d",
                cast_id, _framings, len(set(f for f in _framings if f)),
            )

            # Duration sanity check: if effective_duration_target is set, verify total ±20%
            if effective_duration_target and scenes:
                total_est = sum(s.get("estimated_duration_seconds", 10) for s in scenes)
                deviation = abs(total_est - effective_duration_target) / effective_duration_target
                if deviation > 0.2:
                    _log("warning", f"Outline total duration {total_est}s deviates {deviation*100:.0f}% from target {effective_duration_target}s, re-prompting once",
                         cast_id=cast_id, total_est=total_est, target=effective_duration_target)
                    correction = f"""The blocks you produced total {total_est}s but the user requested {effective_duration_target}s.
Adjust the number of blocks and per-block durations to hit {effective_duration_target}s (±20%). Return the corrected JSON array only."""
                    _oai2 = get_openrouter_service()
                    response2 = await _oai2.generate_text(
                        prompt=correction,
                        system_prompt=system_text,
                        model=CAST_GENERATOR_MODEL,
                        max_tokens=get_outline_max_tokens(),
                        temperature=0.7,
                    )
                    await _record_llm_usage(
                        user_id=user_id, cast_id=cast_id,
                        model=CAST_GENERATOR_MODEL,
                        usage=getattr(_oai2, "last_usage", {}),
                    )
                    cleaned2 = response2.strip()
                    if cleaned2.startswith("```"):
                        lines2 = cleaned2.splitlines()
                        if lines2[-1].strip() == "```":
                            cleaned2 = "\n".join(lines2[1:-1])
                        else:
                            cleaned2 = "\n".join(lines2[1:])
                        cleaned2 = cleaned2.strip()
                    if not cleaned2.startswith("["):
                        s2 = cleaned2.find("[")
                        e2 = cleaned2.rfind("]")
                        if s2 != -1 and e2 != -1:
                            cleaned2 = cleaned2[s2:e2 + 1]
                    try:
                        scenes2 = json.loads(cleaned2)
                        if isinstance(scenes2, list):
                            scenes2 = _sanitize_outline_categories(scenes2, cast_id=cast_id)
                            scenes2 = _dedupe_outline_by_position(scenes2, cast_id=cast_id)
                            scenes2 = dedupe_outline_scenes(scenes2, cast_id=cast_id)
                            scenes2 = _enforce_framing_variety(scenes2)
                            _rotated2 = _count_framing_rotations(scenes2)
                            if _rotated2:
                                logger.info("[framing-rotation] cast=%s rotated=%d blocks", cast_id, _rotated2)
                            total_est2 = sum(s.get("estimated_duration_seconds", 10) for s in scenes2)
                            _log("info", f"Re-prompted outline: {total_est2}s (was {total_est}s, target {effective_duration_target}s)",
                                 cast_id=cast_id)
                            block_cap2 = _production_level_block_cap(template, production_level)
                            scenes2 = _enforce_block_count_cap(scenes2, block_cap2, cast_id=cast_id)
                            scenes2 = _clamp_outline_duration_strict(
                                _enforce_outline_duration(scenes2, effective_duration_target, cast_id=cast_id),
                                effective_duration_target, cast_id=cast_id,
                            )
                            return _enforce_template_broll_ratio(
                                scenes2, template, production_level, cast_id=cast_id,
                            )
                    except (json.JSONDecodeError, TypeError):
                        _log("warning", "Re-prompt correction parse failed, using original", cast_id=cast_id)

            block_cap = _production_level_block_cap(template, production_level)
            scenes = _enforce_block_count_cap(scenes, block_cap, cast_id=cast_id)
            scenes = _clamp_outline_duration_strict(
                _enforce_outline_duration(scenes, effective_duration_target, cast_id=cast_id),
                effective_duration_target, cast_id=cast_id,
            )
            return _enforce_template_broll_ratio(scenes, template, production_level, cast_id=cast_id)
    except (json.JSONDecodeError, TypeError) as e:
        _log("error", "Failed to parse outline JSON", cast_id=cast_id, error=str(e), response_preview=cleaned[:500])
        import sentry_sdk
        sentry_sdk.capture_exception(e)

    _log("warning", "Failed to parse outline JSON, returning default", cast_id=cast_id)
    return []


# ============================================================================
# Smart Cast: enhanced outline + auto stock-media population
# ----------------------------------------------------------------------------
# `generate_smart_outline` produces blocks with category, hook_type, stock
# media query, background type, transition, and energy level. Combined with
# `auto_populate_stock_media`, the LLM effectively designs the entire video
# (block sequence + visual layout) and Pexels supplies the stock footage.
#
# See:
#   - docs/Content_Creation_Knowledge_Base_v1.md (hooks/formats catalog)
#   - services/ai_prompts.py → "smart_cast_outline_generator" (system prompt)
# ============================================================================

# Allowed categories — must match frontend lib/blockCategories.ts so the
# editor can render the right visual layout per block.
VALID_BLOCK_CATEGORIES = {
    "avatar_speaking",
    "avatar_voiceover",
    # Merged category — replaces avatar_motion (T2V, no face) and avatar_acting
    # (I2V from generic body shots). avatar_action ALWAYS uses the avatar's
    # face via I2V with FLUX-Kontext-generated scene-specific first/last frames.
    "avatar_action",
    "pip_talking_head",
    "stock_video",
    "stock_photo",
    "generated_photo",
    "generated_video",
}


# regression-6 — product/review casts must SHOW the product and cut to at
# least one b-roll/demo beat. The smart-outline prompt only *encourages* this;
# the LLM is free to return an all-talking-head plan (it did for the reference
# cast cst_89f282ba2af5). These helpers guarantee the beats exist by INJECTING
# them post-sanitization when the model omitted them. Non-product casts are
# untouched.
#
# A "product display" beat is any block that puts the product on screen: a
# split_h half (avatar in one region, product in the other) or a talking-head
# block whose pip_layout is split_h. A "b-roll/demo" beat is a non-speaking
# descriptive cut — stock footage / demo / generated visual.
_PRODUCT_DISPLAY_CATEGORIES = {"pip_talking_head"}
_PRODUCT_DISPLAY_BLOCK_TYPES = {"product", "product_demo", "feature_showcase"}
_BROLL_CATEGORIES = {
    "stock_video",
    "stock_photo",
    "avatar_voiceover",
    "generated_video",
    "generated_photo",
}
# content_type ids (from services.content_type) that imply a product cast.
# "review" is folded into product_showcase by detect_content_type, but we
# match it explicitly too in case an upstream caller passes the raw label.
_PRODUCT_CONTENT_TYPES = {"product_showcase", "review", "live_selling"}


def _scene_is_product_display(scene: dict) -> bool:
    """True when a scene already puts the product on screen via a PIP corner
    bubble (product/b-roll filling the full canvas behind a small avatar
    overlay), a legacy split_h half, or an explicit product role."""
    if not isinstance(scene, dict):
        return False
    from layouts.primitives import LayoutPrimitive, coerce_to_primitive

    category = (scene.get("category") or "").strip().lower()
    block_type = (scene.get("block_type") or "").strip().lower()
    content_role = (scene.get("content_role") or "").strip().lower()
    layout = coerce_to_primitive(scene.get("pip_layout"))
    is_product_layout = layout in (
        LayoutPrimitive.PIP_QUARTER_BR.value,
        LayoutPrimitive.SPLIT_H.value,
    )

    if content_role == "product" and is_product_layout:
        return True
    if category in _PRODUCT_DISPLAY_CATEGORIES and is_product_layout:
        return True
    if block_type in _PRODUCT_DISPLAY_BLOCK_TYPES and is_product_layout:
        return True
    return False


def _scene_is_broll(scene: dict) -> bool:
    """True when a scene is a non-speaking descriptive / demo beat that resolves
    to its own clip (stock footage, voiceover-over-broll, generated visual)."""
    if not isinstance(scene, dict):
        return False
    category = (scene.get("category") or "").strip().lower()
    return category in _BROLL_CATEGORIES


def _is_product_cast(content_type: dict | None, products: list[dict] | None) -> bool:
    """A cast is a product/review cast when its detected content_type is a
    product/review/live-selling type OR it has at least one linked product."""
    if products:
        return True
    ct = (content_type or {}).get("type") if isinstance(content_type, dict) else None
    return bool(ct) and str(ct).strip().lower() in _PRODUCT_CONTENT_TYPES


def _build_injected_product_scene(products: list[dict] | None) -> dict:
    """A product-display beat: the product clip fills the full canvas with the
    avatar riding as a small bottom-right PIP bubble, instead of a 50/50
    split — a split crops tightly enough that neither the avatar's mic nor
    much of their expression stays in frame. The stock_media_query lets
    auto_populate resolve a placeholder clip when no product asset is
    attached yet (vision-rerank is a later step).
    """
    from layouts.primitives import LayoutPrimitive

    product_name = ""
    if products:
        product_name = (products[0].get("name") or "").strip()
    query = f"{product_name} product".strip() if product_name else "product on display"
    scene: dict = {
        "block_type": "product",
        "category": "pip_talking_head",
        "content_role": "product",
        "pip_layout": LayoutPrimitive.PIP_QUARTER_BR.value,
        "mood": "energetic",
        "energy_level": "medium",
        "transition_in": "cut",
        "estimated_duration_seconds": 6,
        "stock_media_query": query[:60],
        "background_type": "stock_video",
        "injected": "product_display",
    }
    if product_name:
        scene["product_name"] = product_name
        scene["key_points"] = [f"Show the {product_name} on screen"]
    return scene


def _build_injected_broll_scene(products: list[dict] | None) -> dict:
    """A non-speaking b-roll/demo beat cutting to descriptive footage. The
    footage fills the full canvas with the avatar riding as a small
    bottom-right PIP bubble (instead of a 50/50 split) so the demo clip gets
    full-frame attention; the resolver fills the content from
    stock_media_url (placeholder OK)."""
    from layouts.primitives import LayoutPrimitive

    product_name = ""
    if products:
        product_name = (products[0].get("name") or "").strip()
    query = f"{product_name} demo".strip() if product_name else "lifestyle b-roll"
    return {
        "block_type": "product_demo",
        "category": "stock_video",
        "pip_layout": LayoutPrimitive.PIP_QUARTER_BR.value,
        "mood": "informative",
        "energy_level": "medium",
        "transition_in": "cut",
        "estimated_duration_seconds": 5,
        "stock_media_query": query[:60],
        "background_type": "stock_video",
        "injected": "broll",
    }


# ── action/b-roll product-id propagation ─────────────────────────────────────
# Bug (cst_d7424cfa4f36): blocks whose prompts explicitly name the product were
# rendered without product_id set, so action-frame regen / b-roll selection had
# no product reference and FLUX hallucinated a generic prop or unrelated stock.
# PR #163 fixed this for PRODUCT block_type only; these helpers extend the
# guarantee to every block category/type that visually involves the product.

# Categories that visually involve the product and should inherit the primary
# product when the cast has one.
_PRODUCT_PROPAGATE_CATEGORIES = {
    "avatar_action",      # action shots — almost always involve the product
    "pip_talking_head",   # often holds / wears the product
    "stock_video",        # b-roll showing product use
    "avatar_voiceover",   # frequently shows product b-roll behind voiceover
}

# Block types that should carry the primary product when the cast has one.
_PRODUCT_PROPAGATE_BLOCK_TYPES = {
    "product",
    "product_demo",
    "cta",
    "social_proof",
    "transition",
}

# Block (category, type) pairs that are pure narrative and must NOT auto-inherit
# the product unless their prompts mention it: a plain HOOK/STORY talking head.
_PURE_NARRATIVE_BLOCK_TYPES = {"hook", "story"}

# Block prompt fields scanned for the product name (the bulletproof branch).
_PRODUCT_NAME_PROMPT_FIELDS = (
    "body_motion_prompt",
    "action_start_prompt",
    "action_end_prompt",
    "motion_prompt",
)


def _primary_product_id(cast) -> str | None:
    """Return the cast's primary product id (first CastProduct), or None."""
    products = getattr(cast, "products", None) or []
    for cp in products:
        pid = getattr(cp, "product_id", None) or getattr(cp, "id", None)
        if pid:
            return pid
    return None


def _primary_product_name(cast) -> str:
    """Return the cast's primary product name (lower-cased), or ''."""
    products = getattr(cast, "products", None) or []
    for cp in products:
        product = getattr(cp, "product", None)
        name = getattr(product, "name", None) if product is not None else None
        if name:
            return str(name).strip().lower()
    return ""


def _product_name_tokens(product_name: str) -> list[str]:
    """Significant (≥4 char, non-brand/filler) tokens from a product name.

    The full name rarely appears verbatim in a prompt ("MASGRE massager" vs
    "MASGRE Cordless Neck and Shoulder Massager"), so we match on individual
    meaningful tokens instead of the whole string."""
    import re

    tokens: list[str] = []
    for tok in re.split(r"[^a-z0-9]+", (product_name or "").lower()):
        if len(tok) >= 4 and tok not in _GENERIC_BRAND_TOKENS and tok not in tokens:
            tokens.append(tok)
    return tokens


def _block_prompt_mentions_product(block, product_name: str) -> bool:
    """True when any action/motion prompt or key_points text on the block names
    the product (case-insensitive, token-based). This is the bulletproof
    branch: if the avatar is told to interact with the named product, the block
    must reference that product regardless of category/type.

    Also fires on the user-edit sentinel "the product" so a scene description
    like "the product is on the neck" links the block even when the model
    didn't echo the product name.
    """
    tokens = _product_name_tokens(product_name)

    def _hit(text: str) -> bool:
        low = text.lower()
        if "the product" in low:
            return True
        return any(tok in low for tok in tokens)

    for field in _PRODUCT_NAME_PROMPT_FIELDS:
        val = getattr(block, field, None)
        if val and _hit(str(val)):
            return True
    key_points = getattr(block, "key_points", None)
    if isinstance(key_points, (list, tuple)):
        for kp in key_points:
            if kp and _hit(str(kp)):
                return True
    elif key_points and _hit(str(key_points)):
        return True
    return False


def _block_str_attr(block, attr: str) -> str:
    """Read a Block attr that may be an Enum or plain string, lower-cased."""
    raw = getattr(block, attr, None)
    value = getattr(raw, "value", raw)
    return str(value or "").strip().lower()


def assign_product_id_to_blocks(cast) -> int:
    """Set ``block.product_id`` on every block of ``cast`` that visually
    references the primary product but is missing the link.

    Rules (any match → assign the primary product), applied only when the
    block has no product_id yet and the cast has a primary product:

    1. category in {avatar_action, pip_talking_head, stock_video, avatar_voiceover}
    2. block_type in {PRODUCT, PRODUCT_DEMO, CTA, SOCIAL_PROOF, TRANSITION}
    3. conservative: the block is not a pure HOOK/STORY avatar_speaking beat
    4. bulletproof: any action/motion prompt or key_points text names the product

    Pure HOOK/STORY talking heads (block_type HOOK or STORY with category
    avatar_speaking) are left untouched unless rule 4 fires. Returns the number
    of blocks newly assigned. Idempotent — blocks that already carry a
    product_id are skipped.
    """
    primary_id = _primary_product_id(cast)
    if not primary_id:
        return 0
    product_name = _primary_product_name(cast)
    blocks = getattr(cast, "blocks", None) or []

    assigned = 0
    for block in blocks:
        if getattr(block, "product_id", None):
            continue

        category = _block_str_attr(block, "category")
        block_type = _block_str_attr(block, "type")

        # Rule 4 (bulletproof) — final guarantee, wins even for HOOK/STORY.
        mentions_product = _block_prompt_mentions_product(block, product_name)

        # Rules 1 & 2 — categorical.
        categorical = (
            category in _PRODUCT_PROPAGATE_CATEGORIES
            or block_type in _PRODUCT_PROPAGATE_BLOCK_TYPES
        )

        # Rule 3 — conservative: anything that is not a pure HOOK/STORY
        # avatar_speaking talking head inherits the product.
        is_pure_narrative = (
            block_type in _PURE_NARRATIVE_BLOCK_TYPES
            and category == "avatar_speaking"
        )
        conservative = not is_pure_narrative

        if mentions_product or categorical or conservative:
            block.product_id = primary_id
            assigned += 1
            _log(
                "info",
                "Propagated primary product to block",
                block_id=getattr(block, "id", None),
                product_id=primary_id,
                category=category,
                block_type=block_type,
                via="prompt" if mentions_product else "category",
            )
    return assigned


# ── Pexels query shortening ──────────────────────────────────────────────────
# Bug (block[5]): stock_media_query was the FULL product name
# ("MASGRE Cordless Neck and Shoulder Massager with Heat...") which Pexels can
# not match, so it returned unrelated footage (woman at laptop). Shorten the
# query to a few product-category keywords, stripping brand and SKU tokens.


def shorten_stock_query(product_name: str, max_words: int = 4) -> str:
    """Collapse a long product name into a short Pexels-friendly query.

    Strips the leading brand token, SKU / model-number tokens, capacity / unit
    tokens, and generic filler, then keeps the first ``max_words`` meaningful
    words. Aims for a query under ~30 characters that names the product
    category, e.g.::

        "MASGRE Cordless Neck and Shoulder Massager with Heat" -> "cordless neck shoulder massager"
        "Sony WH-1000XM5 Wireless Headphones" -> "wireless headphones"
    """
    import re

    if not product_name:
        return ""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", product_name) if w]
    if not words:
        return ""

    # Drop a leading ALL-CAPS brand token (MASGRE, ANKER, …) when the name has
    # more than one word, so the query leads with the product category.
    if len(words) > 1 and words[0].isupper():
        words = words[1:]

    meaningful: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _GENERIC_BRAND_TOKENS or len(lw) <= 2 or lw in meaningful:
            continue
        if _looks_like_sku(w):
            continue
        meaningful.append(lw)
        if len(meaningful) >= max_words:
            break

    return " ".join(meaningful)


def _looks_like_sku(token: str) -> bool:
    """True for model-number / capacity / unit tokens (WH-1000XM5, 5000mAh,
    20W, v2) that pollute a Pexels search."""
    t = token.lower()
    if any(ch.isdigit() for ch in t) and any(ch.isalpha() for ch in t):
        return True  # mixed alnum → model number / capacity (5000mah, wh1000xm5)
    return t.isdigit()


def _ensure_product_and_broll_beats(
    scenes: list[dict],
    content_type: dict | None,
    products: list[dict] | None,
    cast_id: str | None = None,
) -> list[dict]:
    """regression-6 injection guarantee.

    For product/review casts (content_type in product/review/live-selling OR a
    linked product), ensure the plan carries ≥1 product-display beat AND ≥1
    b-roll/demo beat. Missing beats are injected mid-cast using the Step-5
    split_h primitive. Idempotent: a plan that already satisfies both
    conditions is returned unchanged. Non-product casts are returned untouched.

    Wrapped end-to-end so an injection bug can never abort outline generation.
    """
    import sentry_sdk

    try:
        if not isinstance(scenes, list) or not scenes:
            return scenes
        if not _is_product_cast(content_type, products):
            return scenes

        has_product = any(_scene_is_product_display(s) for s in scenes)
        has_broll = any(_scene_is_broll(s) for s in scenes)
        if has_product and has_broll:
            return scenes

        # Insert toward the middle so injected beats land between the hook and
        # the closing rather than disrupting either bookend.
        mid = max(1, len(scenes) // 2)
        injected: list[str] = []

        if not has_product:
            scenes.insert(mid, _build_injected_product_scene(products))
            injected.append("product_display")
            mid += 1
        if not has_broll:
            scenes.insert(mid, _build_injected_broll_scene(products))
            injected.append("broll")

        _log(
            "info",
            "regression-6 injected missing product/b-roll beats",
            cast_id=cast_id,
            injected=injected,
            block_count=len(scenes),
        )
        return scenes
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


def _build_style_dna_section(persona: dict | None) -> str:
    """Render Avatar Style DNA as a directorial guidance block.

    Returns an empty string when no usable Style DNA is attached so older
    avatars (and avatars whose analysis failed) keep producing the same
    output as before. We gate on `voice_duration_s > 0` because that
    field is only set when the analyze-style pipeline reached the LLM
    step — i.e. partial / aborted analyses don't poison the prompt.
    """
    if not isinstance(persona, dict):
        return ""
    dna = persona.get("style_dna")
    if not isinstance(dna, dict):
        return ""
    try:
        if float(dna.get("voice_duration_s", 0) or 0) <= 0:
            return ""
    except (TypeError, ValueError):
        return ""

    transitions = dna.get("preferred_transitions") or ["jump_cut"]
    if isinstance(transitions, str):
        transitions = [transitions]
    broll_pct = int(round(float(dna.get("broll_ratio", 0.4) or 0.4) * 100))
    return (
        "AVATAR STYLE DNA (cloned from creator content):\n"
        f"- Cut frequency: every {dna.get('cut_frequency_seconds', 3.5)}s\n"
        f"- B-roll ratio: {broll_pct}%\n"
        f"- Caption preset: {dna.get('caption_preset', 'hormozi_bold')}\n"
        f"- Preferred transitions: {', '.join(transitions)}\n"
        f"- Music energy: {dna.get('music_energy', 'medium')}\n"
        f"- Hook pattern: {dna.get('hook_pattern', 'pattern_interrupt')}\n"
        f"- Speaking tone: {dna.get('tone', 'casual')}\n\n"
        "MATCH THIS STYLE in your block design — cut frequency, b-roll ratio, "
        "energy pacing, and transitions should align with the creator's DNA.\n\n"
    )


def _enforce_outline_duration(
    scenes: list[dict],
    duration_target_seconds: int | None,
    cast_id: str | None = None,
) -> list[dict]:
    """HARD enforcement: if scenes total > target * 1.3, scale durations
    down proportionally. If still over target * 1.2 after scaling, drop
    blocks from the middle (keeping the first hook block and the final
    CTA) until under the cap.

    The Pass 1 word-count cap in `_generate_full_script` does most of the
    enforcement at the script level — speech is ~2 words/second so a
    120-word cap mechanically prevents 2:30 of content for a 60s target.
    This outline trim is a backstop for the case where the outline LLM
    over-allocates duration and downstream layout would expect long blocks.
    """
    if not duration_target_seconds or not scenes:
        return scenes

    total = sum(int(s.get("estimated_duration_seconds", 10) or 10) for s in scenes)
    if total <= duration_target_seconds * 1.3:
        return scenes

    _log(
        "warning",
        f"Outline total {total}s exceeds target {duration_target_seconds}s by >30% — proportionally trimming",
        cast_id=cast_id,
        total=total,
        target=duration_target_seconds,
    )

    scale = duration_target_seconds / total
    for s in scenes:
        cur = int(s.get("estimated_duration_seconds", 10) or 10)
        s["estimated_duration_seconds"] = max(3, int(cur * scale))

    # If still over target by 20%+, drop blocks from the middle (keep the
    # first block — hook — and the last block — CTA — intact).
    while (
        sum(int(s.get("estimated_duration_seconds", 10) or 10) for s in scenes)
        > duration_target_seconds * 1.2
    ):
        if len(scenes) > 2:
            scenes.pop(-2)
        else:
            break

    new_total = sum(int(s.get("estimated_duration_seconds", 10) or 10) for s in scenes)
    _log(
        "info",
        f"Outline trimmed to {new_total}s (was {total}s, target {duration_target_seconds}s)",
        cast_id=cast_id,
    )
    return scenes


# PR #66 Fix 3 — keep the cast within ±10% of the user's requested
# duration. The existing _enforce_outline_duration only kicks in
# beyond ±30%, which let a 45s request land at 65s (44% over). This
# second pass scales scenes proportionally so the total sits inside
# [target * 0.9, target * 1.1]. Run AFTER _enforce_outline_duration
# so the LLM-driven block trimming has already run.
def _clamp_outline_duration_strict(
    scenes: list[dict],
    duration_target_seconds: int | None,
    cast_id: str | None = None,
) -> list[dict]:
    """Tight ±10% clamp on outline scene durations.

    - Total > target * 1.10: scale every block proportionally, re-snap
      to integer seconds, ensure floor of 3s per block.
    - Total < target * 0.90: extend the LAST block by the deficit.

    Wrapped end-to-end in try/except so a clamp bug never aborts
    outline generation.
    """
    import sentry_sdk
    try:
        if not duration_target_seconds or not scenes:
            return scenes
        target = float(duration_target_seconds)
        total = float(sum(
            float(s.get("estimated_duration_seconds", 10) or 10)
            for s in scenes
        ))
        if target <= 0 or total <= 0:
            return scenes
        upper = target * 1.10
        lower = target * 0.90
        if lower <= total <= upper:
            return scenes

        before_total = total
        blocks_scaled = 0

        if total > upper:
            scale = target / total
            for s in scenes:
                cur = float(s.get("estimated_duration_seconds", 10) or 10)
                snapped = max(3.0, round(cur * scale))
                s["estimated_duration_seconds"] = snapped
                blocks_scaled += 1
            after = sum(
                float(s.get("estimated_duration_seconds", 10) or 10)
                for s in scenes
            )
            # If the floor of 3s pushed us back over the cap, drop blocks
            # from the middle (preserving hook + CTA) until we fit.
            while after > upper and len(scenes) > 2:
                scenes.pop(-2)
                after = sum(
                    float(s.get("estimated_duration_seconds", 10) or 10)
                    for s in scenes
                )
        elif total < lower:
            deficit = target - total
            # Pad the last block so the cast lands at target. Speaking
            # blocks with TTS shorter than their duration silence-pad
            # naturally; silent action blocks just extend.
            last = scenes[-1]
            cur = float(last.get("estimated_duration_seconds", 10) or 10)
            last["estimated_duration_seconds"] = round(cur + deficit)
            blocks_scaled = 1

        after_total = sum(
            float(s.get("estimated_duration_seconds", 10) or 10)
            for s in scenes
        )
        _log(
            "info",
            "[duration_clamp] strict ±10% applied",
            cast_id=cast_id,
            target=int(target),
            sum_before=round(before_total, 2),
            sum_after=round(after_total, 2),
            blocks_scaled=blocks_scaled,
        )
        return scenes
    except Exception as e:
        sentry_sdk.capture_exception(e)
        _log(
            "warning",
            "[duration_clamp] strict clamp failed; continuing without clamp",
            cast_id=cast_id,
            error=str(e)[:200],
        )
        return scenes


def _strip_json_fences(raw: str) -> str:
    """Strip markdown fences and surrounding chatter from an LLM JSON reply."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
        cleaned = cleaned.strip()
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
    return cleaned


# ── PR E: dedupe duplicate-position blocks ───────────────────────────────────
#
# A live cast (cst_db7b2b7ef5ac) came back from the outline LLM with 25 blocks
# carrying DUPLICATE `position` values (0,0,1,1,2,2…). Each position held 2-3
# sibling blocks where only one carried a product reference and the others were
# product-less placeholders that never received a script — so after TTS, 11
# variants failed with "No script text". This collapses the plan to exactly ONE
# block per position BEFORE persistence: when the LLM emitted duplicates we keep
# the product-bearing sibling (product_id / product_name set), else the first.
# Blocks without an explicit `position` are treated as already-distinct (they
# keep their order). Pure + DB-free so the dedupe is unit-testable.


def _scene_has_product_ref(scene: dict) -> bool:
    """True when an outline scene references a product (id or name)."""
    if not isinstance(scene, dict):
        return False
    if scene.get("product_id"):
        return True
    name = scene.get("product_name")
    return bool(isinstance(name, str) and name.strip())


def _dedupe_outline_by_position(
    scenes: list[dict],
    cast_id: str | None = None,
) -> list[dict]:
    """Collapse outline scenes that share a `position` to one block each.

    The LLM occasionally emits several sibling blocks for the same `position`
    (one real, the rest product-less placeholders). Persisting all of them
    produces script-less variants that fail TTS. Keep exactly one scene per
    explicit position — preferring the product-bearing sibling, else the first
    seen — while preserving the relative order positions first appeared.

    Scenes with no usable integer `position` are passed through untouched and
    keep their order (the caller re-indexes with enumerate at persist time).
    Wrapped so a dedupe bug can never abort outline generation.
    """
    import sentry_sdk

    try:
        if not isinstance(scenes, list) or not scenes:
            return scenes

        # First pass: detect whether any duplicate positions exist at all. If
        # not, return the list unchanged so non-buggy plans are a no-op.
        seen_positions: set[int] = set()
        ordered_positions: list[int] = []
        passthrough: list[dict] = []  # scenes with no explicit position
        has_duplicate = False
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            pos = scene.get("position")
            try:
                pos_int = int(pos) if pos is not None else None
            except (TypeError, ValueError):
                pos_int = None
            if pos_int is None:
                continue
            if pos_int in seen_positions:
                has_duplicate = True
                break
            seen_positions.add(pos_int)

        if not has_duplicate:
            return scenes

        chosen: dict[int, dict] = {}
        for scene in scenes:
            if not isinstance(scene, dict):
                passthrough.append(scene)
                continue
            pos = scene.get("position")
            try:
                pos_int = int(pos) if pos is not None else None
            except (TypeError, ValueError):
                pos_int = None
            if pos_int is None:
                passthrough.append(scene)
                continue
            if pos_int not in chosen:
                chosen[pos_int] = scene
                ordered_positions.append(pos_int)
            else:
                # Prefer the product-bearing sibling over a placeholder.
                existing = chosen[pos_int]
                if _scene_has_product_ref(scene) and not _scene_has_product_ref(existing):
                    chosen[pos_int] = scene

        deduped = [chosen[p] for p in ordered_positions] + passthrough
        dropped = len(scenes) - len(deduped)
        if dropped:
            _log(
                "warning",
                "Smart outline had duplicate-position blocks — deduped",
                cast_id=cast_id,
                blocks_in=len(scenes),
                blocks_out=len(deduped),
                dropped=dropped,
                positions=len(ordered_positions),
            )
        return deduped
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


def dedupe_outline_scenes(
    scenes: list[dict],
    cast_id: str | None = None,
) -> list[dict]:
    """Drop scenes that are exact duplicate beats — same block_type, category,
    purpose and key_points.

    Defence-in-depth: the outline path for cst_0f43a80b624d persisted every beat
    twice, so each (position, type, category) tuple appeared on two block rows.
    Distinct beats that merely share a type/category (legitimate — a cast can
    have several PRODUCT/avatar_speaking blocks with different scripts) are
    preserved. Order is kept. Wrapped so a dedupe bug can never abort outline
    generation.
    """
    import sentry_sdk

    try:
        if not isinstance(scenes, list):
            return scenes
        seen: set[tuple] = set()
        out: list[dict] = []
        dropped = 0
        for s in scenes:
            if not isinstance(s, dict):
                out.append(s)
                continue
            kp = s.get("key_points")
            kp_sig = tuple(kp) if isinstance(kp, list) else (kp,)
            key = (
                (s.get("block_type") or "").strip().lower(),
                (s.get("category") or "").strip().lower(),
                (s.get("purpose") or "").strip().lower(),
                kp_sig,
            )
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            out.append(s)
        if dropped:
            _log(
                "warning",
                "Outline had duplicate beats — deduped",
                cast_id=cast_id,
                blocks_in=len(scenes),
                blocks_out=len(out),
                dropped=dropped,
            )
        return out
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


# ── PR E: live-cast category-ratio bias ──────────────────────────────────────
#
# Directive: a live/standard short-form cast must lean on voiceover + b-roll +
# uploaded video and use FEWER full avatar-speaking blocks. The outline LLM is
# *asked* to do this in the system prompt, but the model still over-produces
# talking-head blocks. This post-LLM pass nudges the actual mix toward the
# target ratios by REASSIGNING the category of surplus avatar-speaking blocks
# (not adding/removing blocks, so durations/dedupe are untouched).
#
# The three ratios are env-tunable and renormalized to sum to 1.0:
#   LIVE_RATIO_AVATAR   — talking-head (avatar_speaking / avatar_action)
#   LIVE_RATIO_BROLL    — VO over stock (avatar_voiceover / stock_video / stock_photo)
#   LIVE_RATIO_UPLOADED — uploaded product footage / product-asset overlay

_LIVE_RATIO_DEFAULTS = (0.35, 0.45, 0.20)  # avatar, broll, uploaded
_AVATAR_RATIO_CATEGORIES = {"avatar_speaking", "avatar_action"}
_BROLL_RATIO_CATEGORIES = {"avatar_voiceover", "stock_video", "stock_photo"}

# Production-level b-roll adjustment, applied on top of a *template's own*
# bias["broll"] (services.cast_templates.TEMPLATES) rather than the global
# env-tunable ratios above — see _production_level_avatar_ratio/
# _enforce_template_broll_ratio below. Quick pulls b-roll down toward a pure
# talking-head cut; Premium pushes it up toward a fuller-production cut.
_QUICK_BROLL_ADJUST = 0.15
_PREMIUM_BROLL_ADJUST = 0.15
_BROLL_RATIO_FLOOR = 0.0
_BROLL_RATIO_CEILING = 0.85


def _live_ratio_short_form_max_seconds() -> int:
    import os

    try:
        return max(1, int(os.environ.get("LIVE_RATIO_MAX_SECONDS", "60")))
    except (ValueError, TypeError) as exc:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
        return 60


def get_live_category_ratios() -> tuple[float, float, float]:
    """Return ``(avatar, broll, uploaded)`` ratios, env-tunable + renormalized.

    Reads ``LIVE_RATIO_AVATAR`` / ``LIVE_RATIO_BROLL`` / ``LIVE_RATIO_UPLOADED``.
    Any missing/invalid var falls back to its default. The three are renormalized
    so they always sum to 1.0 (so a user can set e.g. 30/40/10 and still get a
    sane split). Falls back to the defaults if the sum is non-positive.
    """
    import os

    def _read(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            val = float(raw)
            return val if val >= 0 else default
        except (ValueError, TypeError) as exc:
            import sentry_sdk

            sentry_sdk.capture_exception(exc)
            return default

    avatar = _read("LIVE_RATIO_AVATAR", _LIVE_RATIO_DEFAULTS[0])
    broll = _read("LIVE_RATIO_BROLL", _LIVE_RATIO_DEFAULTS[1])
    uploaded = _read("LIVE_RATIO_UPLOADED", _LIVE_RATIO_DEFAULTS[2])
    total = avatar + broll + uploaded
    if total <= 0:
        return _LIVE_RATIO_DEFAULTS
    return (avatar / total, broll / total, uploaded / total)


def _demote_surplus_avatar_blocks(
    scenes: list[dict],
    avatar_ratio: float,
    cast_id: str | None = None,
    log_label: str = "Live-ratio bias",
) -> list[dict]:
    """Shared core: cap talking-head blocks at ``avatar_ratio`` of the plan.

    If more than ``avatar_ratio`` of the blocks are talking-head, the surplus
    avatar_speaking blocks (never the first — the hook — nor the last — the
    CTA) are re-categorised to avatar_voiceover so the same script now
    narrates over b-roll instead of a talking head. Block count, order,
    durations and product refs are untouched; only the visual treatment
    changes. No-op for plans with fewer than 3 blocks (nothing to rebalance
    without touching the bookends). Used by both :func:`_enforce_live_ratios`
    (global env-tunable ratio, Auto/no-template mode) and
    :func:`_enforce_template_broll_ratio` (per-template + production-level
    ratio) — same mechanism, different source for the target ratio.
    """
    import sentry_sdk

    try:
        if not isinstance(scenes, list) or len(scenes) < 3:
            return scenes

        n = len(scenes)
        max_avatar = max(1, int(round(avatar_ratio * n)))

        # Indices of talking-head blocks, excluding the hook (first) and CTA
        # (last) which must stay avatar-on-camera for the open and conversion.
        avatar_idx = [
            i
            for i, s in enumerate(scenes)
            if isinstance(s, dict)
            and (s.get("category") or "").strip().lower() in _AVATAR_RATIO_CATEGORIES
        ]
        protected = {0, n - 1}
        demotable = [i for i in avatar_idx if i not in protected]

        surplus = len(avatar_idx) - max_avatar
        if surplus <= 0 or not demotable:
            return scenes

        # Demote talking-head body blocks to voiceover: the script survives,
        # now read as narration over the block's stock b-roll (auto_populate
        # already fetches a clip for every block). The hook (first) and CTA
        # (last) are excluded above, so the bookends always stay on-camera.
        demoted = 0
        for i in demotable:
            if demoted >= surplus:
                break
            scene = scenes[i]
            scene["category"] = "avatar_voiceover"
            if not scene.get("background_type"):
                scene["background_type"] = "stock_video"
            scene["ratio_demoted"] = True
            demoted += 1

        if demoted:
            _log(
                "info",
                f"{log_label}: demoted surplus avatar blocks to voiceover",
                cast_id=cast_id,
                demoted=demoted,
                block_count=n,
                avatar_cap=max_avatar,
                avatar_ratio=round(avatar_ratio, 3),
            )
        return scenes
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


def _enforce_live_ratios(
    scenes: list[dict],
    duration_target_seconds: int | None,
    cast_id: str | None = None,
) -> list[dict]:
    """Bias a short-form live/standard cast toward VO + b-roll, away from
    full avatar-speaking blocks, using the global env-tunable ratios.

    No-op for long-form casts (> ``LIVE_RATIO_MAX_SECONDS``) — this global
    ratio is a short-form-only default, unlike the per-template ratio in
    :func:`_enforce_template_broll_ratio`, which applies at any duration.
    Used only when no template is selected (Auto mode); see the call sites
    in generate_outline/generate_smart_outline. Wrapped so a rebalance bug
    can never abort outline generation.
    """
    import sentry_sdk

    try:
        if not isinstance(scenes, list) or len(scenes) < 3:
            return scenes
        if duration_target_seconds and duration_target_seconds > _live_ratio_short_form_max_seconds():
            return scenes

        avatar_ratio, _broll_ratio, _uploaded_ratio = get_live_category_ratios()
        return _demote_surplus_avatar_blocks(scenes, avatar_ratio, cast_id=cast_id, log_label="Live-ratio bias")
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


def _production_level_avatar_ratio(template: Optional[dict], production_level: str) -> Optional[float]:
    """Avatar-side ratio target for :func:`_enforce_template_broll_ratio`.

    Derived from the *template's own* bias["broll"] (services.cast_templates)
    rather than the global env-tunable ratios above — Standard uses the
    template's bias as-is (now numerically enforced, not just soft-prompted
    via _build_template_constraint); Quick pulls b-roll down toward a purer
    talking-head cut; Premium pushes it up toward a fuller-production cut.
    Returns None when there's no template to derive a ratio from.
    """
    if not template:
        return None
    base_broll = float((template.get("bias") or {}).get("broll", 0.0))
    if production_level == "quick":
        broll = max(_BROLL_RATIO_FLOOR, base_broll - _QUICK_BROLL_ADJUST)
    elif production_level == "premium":
        broll = min(_BROLL_RATIO_CEILING, base_broll + _PREMIUM_BROLL_ADJUST)
    else:
        broll = base_broll
    return max(0.0, min(1.0, 1.0 - broll))


def _enforce_template_broll_ratio(
    scenes: list[dict],
    template: Optional[dict],
    production_level: str,
    cast_id: str | None = None,
) -> list[dict]:
    """Template + production-level-aware sibling of :func:`_enforce_live_ratios`.

    Used (instead of the global env-ratio version) whenever a template is
    selected, for BOTH generate_outline and generate_smart_outline — unlike
    _enforce_live_ratios, this has no short-form gate, since a template's
    intended shot mix should hold regardless of the cast's duration.
    """
    avatar_ratio = _production_level_avatar_ratio(template, production_level)
    if avatar_ratio is None:
        return scenes
    return _demote_surplus_avatar_blocks(
        scenes, avatar_ratio, cast_id=cast_id, log_label="Template b-roll ratio",
    )


def _normalize_production_level_for_generation(value: Optional[str]) -> str:
    """Normalize production_level for GENERATION purposes only.

    Deliberately separate from services.billing_config.normalize_production_level,
    which collapses the legacy "quick" value to "standard" for BILLING rate
    purposes — reusing that here would silently erase the quick tier this
    module needs to keep distinct. Anything unrecognized (None, "", or a
    legacy quality value like "simple"/"hd"/"hd_plus") defaults to "standard".
    """
    level = (value or "").strip().lower()
    return level if level in ("quick", "standard", "premium") else "standard"


def _production_level_block_cap(template: Optional[dict], production_level: str) -> Optional[int]:
    """Total block-count cap for the chosen production level.

    Quick collapses the template's block_sequence down to its unique block
    types (repeats removed) — the leanest structurally-valid cut of the
    format. Premium allows one extra beat beyond the template's normal
    length. Standard is unchanged (the template's natural length — same as
    today). None when no template is selected (Auto mode stays a no-op,
    matching _build_template_constraint's own behavior).
    """
    if not template:
        return None
    seq = template.get("block_sequence") or []
    if not seq:
        return None
    if production_level == "quick":
        return max(2, len(set(seq)))
    if production_level == "premium":
        return len(seq) + 1
    return len(seq)


def _enforce_block_count_cap(
    scenes: list[dict],
    cap: Optional[int],
    cast_id: str | None = None,
) -> list[dict]:
    """Trim ``scenes`` down to ``cap`` blocks, mirroring the middle-drop
    pattern already used by _enforce_outline_duration for duration caps.

    Protects index 0 (hook), index -1 (CTA), and any scene already marked
    ``injected`` (product/broll/video beats added by
    _ensure_product_and_broll_beats / inject_product_asset_video) — this is
    a soft structural ceiling, never a hard guarantee, so if every remaining
    scene is protected it stops early rather than violating one of them.
    """
    import sentry_sdk

    try:
        if cap is None or not isinstance(scenes, list) or len(scenes) <= cap:
            return scenes
        scenes = list(scenes)
        removed = 0
        while len(scenes) > cap:
            n = len(scenes)
            protected = {0, n - 1}
            candidates = [
                i for i in range(n)
                if i not in protected and not (isinstance(scenes[i], dict) and scenes[i].get("injected"))
            ]
            if not candidates:
                break
            # Drop from the middle, same spot _enforce_outline_duration pops from.
            drop_idx = candidates[len(candidates) // 2]
            scenes.pop(drop_idx)
            removed += 1
        if removed:
            _log(
                "info",
                "Production-level block cap: trimmed outline",
                cast_id=cast_id,
                removed=removed,
                cap=cap,
                remaining=len(scenes),
            )
        return scenes
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


def _effective_duration_target_seconds(
    duration_target_seconds: Optional[int],
    template: Optional[dict],
    production_level: str,
) -> Optional[int]:
    """Combine the user's manual duration slider with the template's
    production-level duration ceiling.

    An EXPLICITLY user-set duration_target_seconds always wins outright —
    never clamped by the tier ceiling, even if it falls outside the
    template's normal range. The manual duration slider is the only length
    control that exists in Auto mode (no template) and the only way to hit
    a hard external constraint (e.g. an exact ad-slot length); silently
    clamping a number the user deliberately typed in — with no indication
    anywhere that it happened — produced a slider showing one value while
    a shorter video actually got generated. Same "never silently override
    what the user explicitly chose" principle applied elsewhere in this
    module (e.g. _enforce_block_count_cap protecting injected beats).

    The tier ceiling is used ONLY as the DEFAULT when the user hasn't set a
    duration at all — derived from the template's own est_duration_range
    ([lo, hi] seconds): Quick defaults to lo, Premium to hi. Standard, or no
    template, or no est_duration_range: no default to compute, returns None.
    """
    if duration_target_seconds:
        return duration_target_seconds
    if not template or production_level == "standard":
        return duration_target_seconds
    dur_range = template.get("est_duration_range") or []
    if len(dur_range) != 2:
        return duration_target_seconds
    lo, hi = dur_range
    ceiling = lo if production_level == "quick" else hi
    return int(ceiling)


# ── PR E: prefer the product's own uploaded video footage ────────────────────


def inject_product_asset_video(
    scenes: list[dict],
    product_video_assets: list[dict] | None,
    cast_id: str | None = None,
) -> list[dict]:
    """Insert an uploaded-video block pointing at a real ProductAsset video.

    When the bound product has ≥1 video ProductAsset, the cast should SHOW the
    actual product footage rather than generic Pexels b-roll. This inserts one
    ``stock_video`` block (the existing no-GPU clip-overlay kind) whose
    ``stock_media_url`` already points at the asset's R2 URL and is flagged
    ``stock_media_source="product_asset"`` so :func:`auto_populate_stock_media`
    leaves it alone instead of overwriting it with Pexels.

    ``product_video_assets`` is a list of dicts shaped from the ProductAsset
    rows the caller looked up::

        {"id", "product_id", "product_name", "url", "thumbnail",
         "duration_seconds", "width", "height"}

    Idempotent: a plan that already carries a product-asset video block is
    returned unchanged. No-op when no video assets exist (caller falls back to
    Pexels). Pure + DB-free so it is unit-testable. Wrapped so an injection bug
    can never abort outline generation.
    """
    import sentry_sdk

    try:
        if not isinstance(scenes, list) or not scenes:
            return scenes
        assets = [a for a in (product_video_assets or []) if isinstance(a, dict) and a.get("url")]
        if not assets:
            return scenes

        # Idempotent: don't stack a second product-asset block on re-run.
        if any(
            isinstance(s, dict) and s.get("stock_media_source") == "product_asset"
            for s in scenes
        ):
            return scenes

        asset = assets[0]
        duration = 5
        try:
            d = float(asset.get("duration_seconds") or 0)
            if d > 0:
                duration = max(3, min(15, int(round(d))))
        except (TypeError, ValueError):
            duration = 5

        block: dict = {
            "block_type": "product_demo",
            "category": "stock_video",
            "background_type": "stock_video",
            "mood": "informative",
            "energy_level": "medium",
            "transition_in": "cut",
            "estimated_duration_seconds": duration,
            "stock_media_url": asset["url"],
            "stock_media_thumbnail": asset.get("thumbnail"),
            "stock_media_kind": "video",
            "stock_media_source": "product_asset",
            "stock_media_width": asset.get("width"),
            "stock_media_height": asset.get("height"),
            "product_asset_id": asset.get("id"),
            "injected": "product_video",
            "key_points": ["Show the real product footage on screen"],
        }
        if asset.get("product_name"):
            block["product_name"] = asset["product_name"]

        # Insert toward the middle so the real footage lands in the body, not
        # over the hook or the CTA.
        mid = max(1, len(scenes) // 2)
        scenes.insert(mid, block)
        _log(
            "info",
            "Injected product-asset video block",
            cast_id=cast_id,
            product_asset_id=asset.get("id"),
            block_count=len(scenes),
        )
        return scenes
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        return scenes


async def generate_smart_outline(
    cast_id: str,
    products: list[dict],
    persona: dict,
    description: str,
    target_audience: dict | None = None,
    duration_target_seconds: int = 60,
    platform_target: str = "tiktok",
    quality_tier: str = "hd",
    aspect_ratio: str = "9:16",
    user_id: Optional[str] = None,
    template: Optional[dict] = None,
    product_video_assets: list[dict] | None = None,
    live_assessment: Optional[dict] = None,
    live_reference_id: Optional[str] = None,
    live_mode_defaults: Optional[dict] = None,
    production_level: str = "standard",
) -> tuple[list[dict], dict | None]:
    """Smart Cast outline: LLM designs the whole video.

    Returns ``(blocks, content_type)`` where ``blocks`` is a list of block
    dicts with all the new fields (block_type, category, mood, key_points,
    estimated_duration_seconds, hook_type, style_directives, stock_media_query,
    background_type, transition_in, energy_level, product_name) and
    ``content_type`` is the dict from ``detect_content_type`` (or ``None`` if
    detection failed). Step 3 uses ``content_type["type"]`` to attach the
    cast's layout template in the caller's persisting transaction.

    Falls back to ``([], content_type)`` on parse failure (caller can drop back
    to `generate_outline` if needed).
    """
    from services.ai_prompts import get_prompt
    from services.openrouter import get_openrouter_service

    # Round-6 Bug B follow-up: confirm WHICH generator ran (see generate_outline).
    logger.warning("[prompt-audit-entry] cast=%s generator=generate_smart_outline", cast_id)

    ta_section = ""
    if target_audience:
        ta_age = target_audience.get("age_range", "")
        ta_interests = target_audience.get("interests", "")
        if isinstance(ta_interests, list):
            ta_interests = ", ".join(ta_interests)
        ta_desc = target_audience.get("description", "")
        ta_section = (
            f"\nTARGET AUDIENCE (from the avatar):\n"
            f"Age range: {ta_age}\nInterests: {ta_interests}\n{ta_desc}\n"
        )

    products_section = ""
    if products:
        for p in products:
            benefits = p.get("key_benefits") or []
            if isinstance(benefits, list):
                benefits = ", ".join(benefits) or "N/A"
            products_section += (
                f"\nPRODUCT: {p.get('name', 'Unknown')}\n"
                f"{p.get('description', '')}\n"
                f"Key benefits: {benefits}\n"
                f"Price: {p.get('price', 'N/A')}\n"
            )

    avatar_visual_section = ""
    av_desc = (persona.get("visual_description") or persona.get("description") or "").strip()
    av_name = (persona.get("name") or "").strip()
    if av_desc or av_name:
        avatar_visual_section = (
            f"\nAVATAR (the on-camera host):\n"
            f"Name: {av_name or 'Host'}\n"
            f"Description: {av_desc or 'Professional creator'}\n"
        )

    style_dna_section = _build_style_dna_section(persona)
    template_constraint = _build_template_constraint(template)
    production_level = _normalize_production_level_for_generation(production_level)
    effective_duration_target = _effective_duration_target_seconds(
        duration_target_seconds, template, production_level,
    )
    live_defaults_section = _build_live_defaults_section(live_mode_defaults)
    if live_defaults_section:
        logger.info("[live-defaults] cast=%s applying live_mode_defaults=%s", cast_id, live_mode_defaults)
    from engine.live_style import select_exemplars, build_live_style_section
    _smart_product_desc = " ".join((p.get("description") or "") for p in (products or []))
    _smart_exemplars = select_exemplars(live_assessment, _smart_product_desc, description or "")
    live_style_section = build_live_style_section(live_assessment, _smart_exemplars)
    user_prompt = f"""{style_dna_section}USER GOAL:
{description}
{ta_section}{avatar_visual_section}
{live_style_section}
{products_section}
PLATFORM: {platform_target}
ASPECT RATIO: {aspect_ratio}
QUALITY TIER: {quality_tier}
DURATION TARGET: {effective_duration_target} seconds (±10%).
{template_constraint}
{live_defaults_section}
Design the complete video. Match the user's intent EXACTLY — do not default to selling
unless the brief is a product/showcase pitch. If the user asks for the avatar to perform
an action (run, walk, dance, demonstrate, fashion walk), USE the avatar_action category
and write a vivid motion_prompt plus action_start_prompt and action_end_prompt for that
block. The system will auto-generate scene-specific first/last frames of the avatar
(face preserved) and animate them — DO NOT repeat the avatar's appearance in the prompts;
just describe the scene, action, and camera.

LIVE-STYLE COVERAGE BIAS (short-form, ≤60s): Default to b-roll + voiceover blocks;
reserve avatar-on-camera blocks for the hook, the snap-on demo moment, and the CTA. The
body of the cast should be over Pexels/stock or uploaded product footage with avatar
narration. Concretely, aim for roughly: 30-40% avatar_speaking / avatar_action (talking
head, including mic-on demo), 40-50% avatar_voiceover / stock_video / stock_photo (voice
over stock footage, NO avatar on screen), 10-20% uploaded product-asset footage when the
product has its own video. Fewer talking-head blocks, more narrated b-roll.

CAMERA FRAMING: every avatar-on-camera block MUST include a `framing` field — one of
CLOSE | MEDIUM | MEDIUM_WIDE | WIDE | ANGLE_LEFT_3Q | ANGLE_RIGHT_3Q. Default starting
frame is MEDIUM; use CLOSE for emotional emphasis, MEDIUM_WIDE for product hand-off
moments, ANGLE_LEFT_3Q / ANGLE_RIGHT_3Q for transitions. Do NOT reuse the same framing
for two consecutive avatar blocks.

Return ONLY a valid JSON array of blocks following the schema in the system prompt."""

    # Detect content type so the strategist persona adapts to the brief
    # (tutorial, fashion, motion, lecture, cartoon, brand awareness, etc.)
    # instead of being hardcoded as a live-selling host.
    from services.content_type import detect_content_type, fill_dynamic_placeholders
    content_type = await detect_content_type(description or "", products)
    system_prompt = fill_dynamic_placeholders(
        get_prompt("smart_cast_outline_generator")["system"], content_type
    )

    _oai_smart = get_openrouter_service()
    log_creative_model_use("cast_smart_outline", CAST_GENERATOR_MODEL)
    logger.info(
        "[prompt-audit] cast=%s kind=script live_ref_id=%s exemplars_used=%d system=%r user=%r",
        cast_id, live_reference_id, len(_smart_exemplars), system_prompt, user_prompt,
    )
    raw = await _oai_smart.generate_text(
        prompt=user_prompt,
        system_prompt=system_prompt,
        model=CAST_GENERATOR_MODEL,
        max_tokens=get_outline_max_tokens(),
        temperature=0.85,
    )
    await _record_llm_usage(
        user_id=user_id, cast_id=cast_id,
        model=CAST_GENERATOR_MODEL,
        usage=getattr(_oai_smart, "last_usage", {}),
    )
    cleaned = _strip_json_fences(raw)

    blocks = None
    try:
        blocks = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError) as exc:
        # First attempt failed JSON parse — common when Opus 4 gets
        # creative with property quoting. Retry once asking the model to
        # FIX its own malformed output. Lower temperature + Sonnet for
        # stricter compliance. If this also fails, give up.
        _log(
            "warning",
            "Smart outline JSON parse failed — attempting self-correction",
            cast_id=cast_id,
            error=str(exc),
            response_preview=cleaned[:200],
        )
        correction_prompt = (
            "The JSON below failed to parse with the error: "
            f"{str(exc)[:300]}\n\n"
            "Return ONLY a valid JSON array of block objects. No markdown, "
            "no commentary, no trailing commas. Every property name must "
            "be wrapped in double quotes. Preserve all the original "
            "content but fix the syntax errors.\n\n"
            f"---ORIGINAL OUTPUT---\n{cleaned}"
        )
        try:
            _oai_corr = get_openrouter_service()
            log_creative_model_use("cast_outline_self_correction", CAST_GENERATOR_MODEL)
            corrected = await _oai_corr.generate_text(
                prompt=correction_prompt,
                system_prompt="You are a strict JSON formatter. Return only valid JSON arrays.",
                model=CAST_GENERATOR_MODEL,
                max_tokens=get_outline_self_correction_max_tokens(),
                temperature=0.1,
            )
            await _record_llm_usage(
                user_id=user_id, cast_id=cast_id,
                model=CAST_GENERATOR_MODEL,
                usage=getattr(_oai_corr, "last_usage", {}),
            )
            blocks = json.loads(_strip_json_fences(corrected))
            _log(
                "info",
                "Smart outline self-correction succeeded",
                cast_id=cast_id,
                block_count=len(blocks) if isinstance(blocks, list) else 0,
            )
        except Exception as exc2:
            import sentry_sdk
            sentry_sdk.capture_exception(exc2)
            _log(
                "error",
                "Smart outline JSON parse failed (incl. self-correction)",
                cast_id=cast_id,
                error=str(exc),
                correction_error=str(exc2),
                response_preview=cleaned[:400],
            )
            return [], content_type

    if not isinstance(blocks, list):
        _log("warning", "Smart outline did not return a list", cast_id=cast_id)
        return [], content_type

    # Round-6 Bug B follow-up: confirm scenes parsed + show the shape so we can
    # tell whether the framing fields survived to this point.
    _first_keys = sorted(blocks[0].keys()) if blocks and isinstance(blocks[0], dict) else []
    logger.warning(
        "[prompt-audit-parsed] cast=%s generator=generate_smart_outline n_scenes=%d first_scene_keys=%s",
        cast_id, len(blocks), _first_keys,
    )

    # Sanity-check + sanitize each block. Unknown categories fall back to
    # avatar_speaking so the rest of the pipeline keeps working.
    sanitized: list[dict] = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        # Round-6 Bug B: normalise the camera framing (default MEDIUM) — the
        # smart outline never did this, so every block landed framing=NULL and
        # the renderer reused one shared MEDIUM look.
        _sanitize_outline_framing(b)
        category = b.get("category") or "avatar_speaking"
        if category in _LEGACY_CATEGORY_ALIASES:
            category = _LEGACY_CATEGORY_ALIASES[category]
        if category not in VALID_BLOCK_CATEGORIES:
            _log(
                "warning",
                f"Smart outline block {i} returned unknown category {category!r}, defaulting to avatar_speaking",
                cast_id=cast_id,
            )
            category = "avatar_speaking"
        b["category"] = category
        # Keep the LLM's stock_media_query for EVERY category, including
        # avatar_speaking. The auto-populate step below uses it as overlay
        # b-roll on speaking/PIP blocks (background visual that runs over
        # the avatar's voice) and as the actual block visual on
        # stock_video / stock_photo. Cost is one Pexels call per block,
        # which is cheap and dramatically improves the user's first
        # impression of the storyboard.
        if category == "avatar_speaking":
            b.setdefault("background_type", "avatar_full")
        else:
            # Make sure background_type is set sensibly when the LLM forgets.
            if not b.get("background_type"):
                if category in ("stock_video", "avatar_voiceover", "pip_talking_head"):
                    b["background_type"] = "stock_video"
                elif category == "stock_photo":
                    b["background_type"] = "stock_photo"
                else:
                    b["background_type"] = "avatar_full"
        b.setdefault("transition_in", "cut")
        b.setdefault("energy_level", "medium")
        b.setdefault("estimated_duration_seconds", 8)
        sanitized.append(b)

    sanitized = _enforce_outline_duration(
        sanitized, effective_duration_target, cast_id=cast_id,
    )
    # PR #66 Fix 3: tight ±10% clamp on top of the existing ±30% trim.
    sanitized = _clamp_outline_duration_strict(
        sanitized, effective_duration_target, cast_id=cast_id,
    )

    # regression-6: product/review casts MUST show the product and cut to at
    # least one b-roll/demo beat. Inject them if the model omitted them. Runs
    # after the duration clamps so injected beats aren't trimmed away.
    sanitized = _ensure_product_and_broll_beats(
        sanitized, content_type, products, cast_id=cast_id,
    )

    # Production-level block-count cap — runs right after mandatory beat
    # injection so those beats exist (and are protected via their "injected"
    # marker) before any structural trim. No-op in Auto mode (no template).
    block_cap = _production_level_block_cap(template, production_level)
    sanitized = _enforce_block_count_cap(sanitized, block_cap, cast_id=cast_id)

    # PR E — collapse any duplicate-position siblings the LLM emitted (the
    # cst_db7b2b7ef5ac bug) to exactly one block per position. Runs before the
    # ratio bias / product-asset injection so those operate on the clean plan.
    sanitized = _dedupe_outline_by_position(sanitized, cast_id=cast_id)
    sanitized = dedupe_outline_scenes(sanitized, cast_id=cast_id)

    # Bias screen time toward voiceover + b-roll and away from full
    # avatar-speaking blocks. When a template is selected, use ITS bias
    # (adjusted for production_level) instead of the global env-tunable
    # short-form-only ratio, so the enforced mix matches what the user
    # actually picked rather than a template-agnostic default.
    if template:
        sanitized = _enforce_template_broll_ratio(
            sanitized, template, production_level, cast_id=cast_id,
        )
    else:
        sanitized = _enforce_live_ratios(
            sanitized, effective_duration_target, cast_id=cast_id,
        )

    # PR E — when the bound product has its own uploaded video footage, insert
    # a block that puts that real footage on screen instead of generic stock.
    sanitized = inject_product_asset_video(
        sanitized, product_video_assets, cast_id=cast_id,
    )

    # Round-6 Bug B follow-up: GUARANTEE framing variety after ALL block
    # injection / reordering so consecutive avatar blocks never share a shot,
    # regardless of LLM compliance. Runs last so injected beats are covered.
    sanitized = _enforce_framing_variety(sanitized)
    _rotated = _count_framing_rotations(sanitized)
    if _rotated:
        logger.info("[framing-rotation] cast=%s rotated=%d blocks", cast_id, _rotated)
    _framings = [b.get("framing") for b in sanitized if isinstance(b, dict)]
    logger.info(
        "[prompt-audit] cast=%s kind=script framing=%s distinct=%d",
        cast_id, _framings, len(set(f for f in _framings if f)),
    )

    _log(
        "info",
        "Smart outline generated",
        cast_id=cast_id,
        block_count=len(sanitized),
        target_duration=duration_target_seconds,
        total_duration=sum(b.get("estimated_duration_seconds", 0) for b in sanitized),
    )
    return sanitized, content_type


# ── B-roll vision re-rank (Step 10) ──────────────────────────────────────
#
# Picking videos[0] blindly often attaches an off-topic clip (Pexels ranks by
# popularity, not by how well a result matches the specific beat). The re-rank
# pulls a wider candidate set, drops the obviously-wrong ones with cheap
# signals, then asks the creative vision model to pick the finalist whose
# preview frame best matches the block's script text. Every knob is
# env-overridable and any failure falls straight back to the first result so a
# render is never blocked.


def _broll_rerank_enabled() -> bool:
    import os
    return os.environ.get("BROLL_RERANK_ENABLED", "true").strip().lower() == "true"


def _broll_candidate_count() -> int:
    import os
    try:
        return max(1, int(os.environ.get("BROLL_CANDIDATE_COUNT", "6")))
    except (ValueError, TypeError) as exc:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        return 6


def _broll_finalist_count() -> int:
    import os
    try:
        return max(1, int(os.environ.get("BROLL_RERANK_FINALISTS", "3")))
    except (ValueError, TypeError) as exc:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        return 3


# ── Product-relevant Pexels queries (PR C) ───────────────────────────────
#
# A render of a Sony cast pulled b-roll of "a Sony shop storefront" and "a
# Stockholm train" — the query was the brand/topic, so Pexels returned generic
# brand/location footage instead of the PRODUCT. `build_pexels_query` rewrites
# the search around the product name + feature/use-case words pulled from the
# block, returning a specific→generic ladder of candidates that the caller
# tries in order until one passes the vision re-rank.

# Brand-only tokens that, on their own, send Pexels toward storefront / logo /
# city footage instead of the product. We keep them when paired with the
# product/category words but never let them stand as the whole query.
_GENERIC_BRAND_TOKENS = frozenset({
    "sony", "apple", "samsung", "lg", "bose", "nike", "adidas", "google",
    "microsoft", "amazon", "dell", "hp", "lenovo", "asus", "canon", "nikon",
    "the", "a", "an", "of", "and", "for", "with", "new", "best", "review",
})


def _extract_feature_words(script_block: dict | None, limit: int = 4) -> list[str]:
    """Pull feature / use-case words from a block's key_points + purpose.

    These describe what the product *does* ("noise cancellation", "wireless",
    "battery life") and are far better Pexels signal than the brand name. Brand
    and filler tokens are dropped so the words steer toward the product in use.
    """
    import re

    if not isinstance(script_block, dict):
        return []
    parts: list[str] = []
    key_points = script_block.get("key_points")
    if isinstance(key_points, list):
        parts.extend(str(k) for k in key_points if k)
    elif key_points:
        parts.append(str(key_points))
    for field in ("purpose", "stock_media_query"):
        val = script_block.get(field)
        if val:
            parts.append(str(val))

    seen: list[str] = []
    for tok in re.split(r"[^a-z0-9]+", " ".join(parts).lower()):
        if len(tok) <= 2 or tok in _GENERIC_BRAND_TOKENS or tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def build_pexels_query(product: dict | None, script_block: dict | None) -> list[str]:
    """Return 2–3 Pexels search queries for a block, ordered specific → generic.

    The caller tries each in order until one returns clips that pass the vision
    re-rank, so the most product-specific phrasing wins when footage exists and
    a lifestyle fallback still fills the block otherwise.

    The queries blend the normalized product name with feature/use-case words
    extracted from the block (so the search targets the PRODUCT, not the brand
    or a city) and end with a category-level lifestyle fallback.

    Example:
        >>> product = {"name": "Sony WH-1000XM5", "category": "headphones"}
        >>> block = {"key_points": ["noise cancellation", "wireless music"]}
        >>> build_pexels_query(product, block)
        ['Sony WH-1000XM5 noise cancellation wireless',
         'headphones noise cancellation wireless',
         'headphones lifestyle close-up']
    """
    product = product or {}
    name = (product.get("name") or "").strip()
    category = (product.get("category") or product.get("product_type") or "").strip()
    features = _extract_feature_words(script_block)
    feature_phrase = " ".join(features[:3]).strip()

    # Infer a category from the product name when none is supplied, so the
    # generic fallback is still product-shaped ("headphones") and not "product".
    if not category and name:
        name_words = [w for w in name.lower().split() if w not in _GENERIC_BRAND_TOKENS]
        category = name_words[-1] if name_words else ""

    candidates: list[str] = []

    # 1. Most specific that Pexels can actually match: a SHORT product-category
    #    phrase (brand + SKU stripped) + what it does. The full product name is
    #    too long for Pexels (cst_d7424cfa4f36 block[5] matched nothing and fell
    #    back to unrelated footage), so lead with the shortened query.
    short_name = shorten_stock_query(name) if name else ""
    if short_name:
        candidates.append(f"{short_name} {feature_phrase}".strip())

    # 1b. Full product name + features — kept as a lower-priority candidate in
    #     case the short form is too generic and real branded footage exists.
    if name:
        specific = f"{name} {feature_phrase}".strip()
        candidates.append(specific)

    # 2. Mid: product category + features (drops the brand so Pexels stops
    #    returning storefronts, but keeps the product type + use-case).
    if category:
        mid = f"{category} {feature_phrase}".strip()
        candidates.append(mid)
    elif feature_phrase:
        candidates.append(feature_phrase)

    # 3. Generic lifestyle fallback so the block is never left empty.
    fallback_subject = category or (name.split()[0] if name else "product")
    if fallback_subject.lower() in _GENERIC_BRAND_TOKENS:
        fallback_subject = "product"
    candidates.append(f"{fallback_subject} lifestyle close-up")

    # De-dupe (case-insensitive) while preserving order, cap each at Pexels'
    # practical query length, drop any candidate that is *only* brand/filler
    # tokens (a bare "Sony" sends Pexels to storefronts), and keep at most 3.
    import re

    out: list[str] = []
    lowered: set[str] = set()
    for q in candidates:
        q = q.strip()[:80]
        key = q.lower()
        if not q or key in lowered:
            continue
        tokens = [t for t in re.split(r"[^a-z0-9]+", key) if t]
        if tokens and all(t in _GENERIC_BRAND_TOKENS for t in tokens):
            continue
        lowered.add(key)
        out.append(q)
        if len(out) >= 3:
            break
    return out


def _multi_angle_enabled() -> bool:
    import os
    return os.environ.get("MULTI_ANGLE_TEMPLATE_ENABLED", "1").strip().lower() in ("1", "true", "yes")


def _multi_angle_min_block_sec() -> float:
    import os
    try:
        return max(0.0, float(os.environ.get("MULTI_ANGLE_MIN_BLOCK_SEC", "4")))
    except (ValueError, TypeError) as exc:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        return 4.0


def _keyword_overlap(query: str, *texts: str) -> int:
    """Count query tokens that appear in any of the candidate's text signals.

    Pexels video objects carry no explicit tag/title field, so the strongest
    text signal is the result's page-slug URL (e.g. ``woman-running-beach``)
    plus the uploader name; photos additionally expose ``alt`` caption text.
    """
    import re
    q_tokens = {t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if len(t) > 2}
    if not q_tokens:
        return 0
    blob = " ".join(t for t in texts if t).lower()
    blob_tokens = set(re.split(r"[^a-z0-9]+", blob))
    return len(q_tokens & blob_tokens)


def _prefilter_video_candidates(
    candidates: list[dict], query: str, want_orientation: str, finalists: int,
) -> list[dict]:
    """Reduce a wide candidate list to the top `finalists` by cheap signals.

    Scores each candidate on keyword overlap (query vs. URL slug + uploader),
    orientation match, and a duration sanity window (3–30s is ideal b-roll).
    Stable-sorts so ties preserve Pexels' original popularity ranking.
    """
    def _score(idx_cand: tuple[int, dict]) -> tuple:
        idx, cand = idx_cand
        overlap = _keyword_overlap(
            query, cand.get("url") or "", (cand.get("user") or {}).get("name") or "",
        )
        width = cand.get("width") or 0
        height = cand.get("height") or 0
        orient = "portrait" if height >= width else "landscape"
        orient_match = 1 if orient == want_orientation else 0
        duration = cand.get("duration") or 0
        duration_ok = 1 if 3 <= duration <= 30 else 0
        # Higher is better; -idx keeps Pexels order as the final tiebreak.
        return (overlap, orient_match, duration_ok, -idx)

    ranked = sorted(enumerate(candidates), key=_score, reverse=True)
    return [cand for _idx, cand in ranked[:finalists]]


async def _vision_pick_video(
    finalists: list[dict], block_text: str, query: str, cast_id: str, block_index: int,
) -> int:
    """Ask the creative vision model which finalist preview frame best matches
    the script beat. Returns the chosen index into `finalists` (0 on any
    failure so the caller keeps the pre-filter's top result).
    """
    thumbs = [(c.get("image") or "") for c in finalists]
    if not all(thumbs):
        return 0

    from services.openrouter import get_openrouter_service
    from services.creative_models import (
        CREATIVE_DESCRIPTION_MODEL,
        log_creative_model_use,
    )

    numbered = "\n".join(f"Image {i}: candidate #{i}" for i in range(len(thumbs)))
    system_prompt = (
        "You are a video editor choosing b-roll. You are shown several preview "
        "frames of candidate stock clips, in order. Pick the ONE whose visible "
        "content best matches the narration beat. Respond with ONLY a JSON "
        'object: {"index": <integer>} where index is the 0-based position of '
        "the best candidate. No prose."
    )
    user_text = (
        f"NARRATION BEAT:\n{block_text}\n\n"
        f"SEARCH INTENT: {query}\n\n"
        f"CANDIDATES (in image order):\n{numbered}\n\n"
        f'Return only {{"index": N}} with N between 0 and {len(thumbs) - 1}.'
    )

    log_creative_model_use(site="broll_rerank", model=CREATIVE_DESCRIPTION_MODEL)
    svc = get_openrouter_service()
    raw = await svc.rank_images(
        image_urls=thumbs,
        system_prompt=system_prompt,
        user_text=user_text,
        model=CREATIVE_DESCRIPTION_MODEL,
    )

    cleaned = _strip_json_fences(raw)
    try:
        parsed = json.loads(cleaned)
        idx = int(parsed["index"])
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        _log(
            "warning",
            "B-roll vision pick parse failed — using pre-filter top result",
            cast_id=cast_id,
            block_index=block_index,
            response_preview=cleaned[:120],
        )
        return 0
    if idx < 0 or idx >= len(thumbs):
        return 0
    return idx


def _block_beat_text(block: dict, query: str) -> str:
    """Build a short beat description from the outline block for the vision
    re-rank. The final script text isn't written yet at this stage, so we
    combine the block's key points / mood with the search query.
    """
    parts: list[str] = []
    key_points = block.get("key_points")
    if isinstance(key_points, list):
        parts.extend(str(k) for k in key_points if k)
    elif key_points:
        parts.append(str(key_points))
    for field in ("mood", "hook_type"):
        val = block.get(field)
        if val:
            parts.append(str(val))
    parts.append(query)
    return ". ".join(p.strip() for p in parts if p and p.strip())


async def _search_and_rank_one(
    client, query: str, beat_text: str, want_orientation: str,
    cast_id: str, block_index: int,
) -> dict | None:
    """Search Pexels for a single `query` and return the vision-reranked pick.

    Returns None when the query yields no results. Two-tier: pull a wide
    candidate set, cheap pre-filter to finalists, then a vision pick across the
    finalists' preview frames. Any failure degrades to the first result.
    """
    per_page = _broll_candidate_count() if _broll_rerank_enabled() else 3
    results = await client.safe_search_videos(
        query, per_page=per_page, orientation=want_orientation,
    )
    videos = (results or {}).get("videos") or []
    if not videos:
        return None
    if not _broll_rerank_enabled() or len(videos) <= 1:
        return videos[0]

    try:
        finalists = _prefilter_video_candidates(
            videos, query, want_orientation, _broll_finalist_count(),
        )
        if not finalists:
            return videos[0]
        chosen = await _vision_pick_video(
            finalists, beat_text, query, cast_id, block_index,
        )
        return finalists[chosen]
    except Exception as exc:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
        _log(
            "warning",
            "B-roll re-rank failed — falling back to first result",
            cast_id=cast_id,
            block_index=block_index,
            query=query,
            error=str(exc),
        )
        return videos[0]


async def _select_video_candidate(
    client, query, beat_text: str, want_orientation: str,
    cast_id: str, block_index: int,
) -> dict | None:
    """Return the best-matching Pexels video, or None if no query yields results.

    `query` may be a single string or a specific→generic list of candidate
    queries (see `build_pexels_query`). Each query is tried in order until one
    returns clips; the first non-empty, vision-reranked result wins so the most
    product-specific phrasing is preferred and a lifestyle fallback still fills
    the block when specific footage doesn't exist.
    """
    queries = [query] if isinstance(query, str) else list(query or [])
    for q in queries:
        q = (q or "").strip()
        if not q:
            continue
        best = await _search_and_rank_one(
            client, q, beat_text, want_orientation, cast_id, block_index,
        )
        if best:
            if q != queries[0]:
                _log(
                    "info",
                    "B-roll query fell back to a more generic candidate",
                    cast_id=cast_id,
                    block_index=block_index,
                    query=q,
                )
            return best
    return None


def _product_for_block(block: dict, products: list[dict] | None) -> dict | None:
    """Match a block to its product: by the block's product_name when set,
    otherwise the cast's first product. Returns None for non-product casts."""
    if not products:
        return None
    name = (block.get("product_name") or "").strip().lower()
    if name:
        for p in products:
            if (p.get("name") or "").strip().lower() == name:
                return p
    return products[0]


def _parallel_clip_entry(
    best: dict, file: dict, *, start_offset_s: float = 0, duration_s: float | None = None,
) -> dict:
    """Shape a chosen Pexels video into a parallel_media overlay entry.

    ``start_offset_s``/``duration_s`` default to 0/None, correct for the
    single-clip case (one entry in the array, no staggering needed). Callers
    placing MULTIPLE clips in the same block's parallel_media array (see
    ``_attach_multi_angle``) MUST pass explicit, distinct offsets — the
    frontend (editorStarterMapping.ts) only auto-staggers sequential clips
    when start_offset_s is absent; an explicit 0 on every clip (the previous
    bug here) defeats that fallback and stacks every clip at t=0, so only
    the last-composited one is ever visible instead of a sequential montage.
    """
    return {
        "kind": "video",
        "url": file["link"],
        "thumbnail": best.get("image"),
        "pexels_id": str(best.get("id")) if best.get("id") else None,
        "source": "pexels",
        "start_offset_s": start_offset_s,
        "duration_s": duration_s,
        "ai_suggested": True,
    }


def _apply_preferred_broll(
    outline: list[dict], cast_id: str, preferred_broll_urls: list[str],
) -> set[int]:
    """Assign the user's uploaded videos (PR #162 `user_video_ids`, resolved to
    R2 URLs by the caller) to b-roll-capable blocks before Pexels runs.

    URLs are handed out round-robin to blocks that take an overlay/cutaway
    (avatar narration + voiceover beats) and to pure stock_video blocks. Each
    such block gets its `stock_media_url` + `parallel_media` set from the user
    asset and is flagged `stock_media_source="user_video"` so the later Pexels
    pass skips it. Returns the set of block indices that were claimed.
    """
    claimed: set[int] = set()
    if not preferred_broll_urls:
        return claimed
    cursor = 0
    for i, block in enumerate(outline):
        category = block.get("category") or "avatar_speaking"
        if category not in (
            "avatar_speaking", "avatar_voiceover", "pip_talking_head", "stock_video",
        ):
            continue
        url = preferred_broll_urls[cursor % len(preferred_broll_urls)]
        cursor += 1
        block["stock_media_url"] = url
        block["stock_media_kind"] = "video"
        block["stock_media_source"] = "user_video"
        if category in ("avatar_speaking", "avatar_voiceover", "pip_talking_head"):
            block["parallel_media"] = [
                {
                    "kind": "video",
                    "url": url,
                    "thumbnail": None,
                    "pexels_id": None,
                    "source": "user_video",
                    "start_offset_s": 0,
                    "duration_s": None,
                    "ai_suggested": True,
                }
            ]
        claimed.add(i)
    _log(
        "info", "Applied preferred user-video b-roll",
        cast_id=cast_id, asset_count=len(preferred_broll_urls), blocks_claimed=len(claimed),
    )
    return claimed


async def auto_populate_stock_media(
    outline: list[dict], cast_id: str, products: list[dict] | None = None,
    preferred_broll_urls: list[str] | None = None,
) -> list[dict]:
    """For each outline block with a stock_media_query, search Pexels and
    attach the top result to the block in place.

    When `products` is supplied, the search query is rebuilt around the product
    name + feature words (see `build_pexels_query`) so the b-roll matches the
    PRODUCT instead of the brand or an incidental location. Long blocks may also
    receive a multi-angle pair (close-up + hands-using) jump-cut together via
    parallel_media — see `_multi_angle_enabled`.

    When `preferred_broll_urls` is supplied (PR #162 — the user's `user_video_ids`
    resolved to R2 URLs), those assets are assigned to b-roll-capable blocks
    first and those blocks are skipped by the Pexels pass.

    On failure (no Pexels key, network error, no results) the block is left
    untouched and the editor will fall back to a solid background. Returns
    the (mutated) outline for caller convenience.
    """
    from services.pexels import get_pexels_client_optional, pick_best_video_file

    preferred_claimed = _apply_preferred_broll(
        outline, cast_id, preferred_broll_urls or [],
    )

    client = get_pexels_client_optional()
    if client is None:
        _log(
            "info",
            "Pexels not configured, skipping stock auto-population",
            cast_id=cast_id,
        )
        return outline

    # Run all the per-block Pexels lookups concurrently. Each call is
    # ~150-400ms; serialising them was costing 3-6s on a 10-block cast,
    # which compounded with the script-gen wall time to bump the user
    # close to the Cloudflare edge timeout. asyncio.gather drops it to
    # roughly the slowest single call regardless of block count.
    import asyncio

    async def _fetch_video_clip(queries, beat_text: str, i: int) -> tuple[dict, dict] | None:
        """Resolve `queries` (specific→generic) to a (video, file) pair, or None."""
        best = await _select_video_candidate(
            client, queries, beat_text, "portrait", cast_id, i,
        )
        if not best:
            return None
        file = pick_best_video_file(best) or {}
        if not file.get("link"):
            return None
        return best, file

    async def _fetch_for_block(i: int, block: dict) -> None:
        # PR E — a block already pointing at real product-asset footage must
        # NOT be overwritten with generic Pexels b-roll. Leave it untouched.
        if block.get("stock_media_source") == "product_asset" and block.get("stock_media_url"):
            return
        # PR #162 — blocks claimed by the user's preferred b-roll (user_video_ids)
        # already carry the chosen asset; do not overwrite with Pexels.
        if i in preferred_claimed:
            return
        base_query = (block.get("stock_media_query") or "").strip()
        category = block.get("category") or "avatar_speaking"
        bg_type = block.get("background_type") or ""

        # Decide whether the block wants a video or a photo. stock_photo
        # is the only photo-bound category; everything else gets video.
        wants_photo = category == "stock_photo" or bg_type == "stock_photo"

        # Build product-relevant query candidates (specific→generic). For
        # product casts this swaps the brand/topic query for product name +
        # feature words; otherwise we fall back to the LLM's own query.
        product = _product_for_block(block, products)
        if product:
            queries = build_pexels_query(product, block)
            # The LLM's stock_media_query is often the FULL product name, which
            # Pexels can't match (cst_d7424cfa4f36). Shorten it before using it
            # as an extra fallback candidate.
            if base_query:
                short_base = shorten_stock_query(base_query) or base_query
                for cand in (short_base, base_query):
                    if cand and cand.lower() not in {q.lower() for q in queries}:
                        queries.append(cand)
        elif base_query:
            queries = [base_query]
        else:
            return
        if not queries:
            return
        query = queries[0]

        try:
            if wants_photo:
                results = await client.safe_search_photos(query, per_page=3)
                photos = (results or {}).get("photos") or []
                if not photos:
                    return
                best = photos[0]
                src = best.get("src") or {}
                if not src.get("large2x"):
                    return
                block["stock_media_url"] = src["large2x"]
                block["stock_media_thumbnail"] = src.get("medium")
                block["stock_media_pexels_id"] = best.get("id")
                block["stock_media_kind"] = "photo"
                block["stock_media_width"] = best.get("width")
                block["stock_media_height"] = best.get("height")
            else:
                beat_text = _block_beat_text(block, query)
                resolved = await _fetch_video_clip(queries, beat_text, i)
                if not resolved:
                    return
                best, file = resolved
                block["stock_media_url"] = file["link"]
                block["stock_media_thumbnail"] = best.get("image")
                block["stock_media_pexels_id"] = best.get("id")
                block["stock_media_kind"] = "video"
                block["stock_media_width"] = file.get("width")
                block["stock_media_height"] = file.get("height")
        except Exception as exc:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
            _log("warning", "Pexels search failed", cast_id=cast_id, block_index=i, query=query, error=str(exc))
            return

        # Mirror the chosen asset into parallel_media for avatar/PIP
        # blocks. parallel_media is what the editor reads to overlay
        # b-roll while the avatar's voice plays underneath. For pure
        # stock blocks we leave parallel_media alone — the asset IS the
        # block, not an overlay.
        if category in ("avatar_speaking", "avatar_voiceover", "pip_talking_head"):
            block["parallel_media"] = [
                {
                    "kind": block["stock_media_kind"],
                    "url": block["stock_media_url"],
                    "thumbnail": block.get("stock_media_thumbnail"),
                    "pexels_id": str(block["stock_media_pexels_id"]) if block.get("stock_media_pexels_id") else None,
                    "source": "pexels",
                    "start_offset_s": 0,
                    "duration_s": None,
                    "ai_suggested": True,
                }
            ]

        # Multi-angle template: for a long video block, fetch a second
        # angle/zoom variant of the same subject and jump-cut the two clips
        # together via parallel_media so the block has visual rhythm instead
        # of a single static stock loop. The base clip becomes the close-up;
        # the second clip shows the product in use. Best-effort — any failure
        # leaves the single-clip result untouched.
        try:
            duration = float(block.get("estimated_duration_seconds") or 0)
        except (TypeError, ValueError) as exc:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
            duration = 0.0
        if (
            not wants_photo
            and _multi_angle_enabled()
            and duration > _multi_angle_min_block_sec()
            and block.get("stock_media_kind") == "video"
        ):
            try:
                await _attach_multi_angle(block, queries, i, _fetch_video_clip)
            except Exception as exc:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
                _log(
                    "warning",
                    "Multi-angle b-roll variant failed — keeping single clip",
                    cast_id=cast_id,
                    block_index=i,
                    query=query,
                    error=str(exc),
                )

        _log(
            "info",
            "Stock media attached",
            cast_id=cast_id,
            block_index=i,
            kind=block.get("stock_media_kind"),
            pexels_id=block.get("stock_media_pexels_id"),
            query=query,
        )

    async def _attach_multi_angle(block: dict, queries, i: int, fetcher) -> None:
        """Replace the block's single overlay with a close-up + hands-using
        jump-cut pair. The two clips fill the block duration via the existing
        parallel_media block-extension logic (sequential entries, each filling
        its share)."""
        base = queries[0]
        angle_queries = [
            ([f"{base} close-up"] + queries),
            ([f"{base} hands using"] + queries),
        ]
        beat_text = _block_beat_text(block, base)
        try:
            duration = float(block.get("estimated_duration_seconds") or 0)
        except (TypeError, ValueError) as exc:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
            duration = 0.0
        slot = duration / len(angle_queries) if duration > 0 else None
        clips: list[dict] = []
        for idx, aq in enumerate(angle_queries):
            resolved = await fetcher(aq, beat_text, i)
            if resolved:
                best, file = resolved
                clips.append(_parallel_clip_entry(
                    best, file,
                    start_offset_s=(idx * slot) if slot else 0,
                    duration_s=slot,
                ))
        # Need at least two distinct clips to be worth a jump cut; otherwise
        # leave the single-clip overlay already attached above.
        unique = {c["url"] for c in clips}
        if len(clips) < 2 or len(unique) < 2:
            return
        block["parallel_media"] = clips
        block["multi_angle"] = True

    await asyncio.gather(
        *[_fetch_for_block(i, b) for i, b in enumerate(outline)],
        return_exceptions=True,
    )
    return outline


# Per-category script-writing rules. The Smart Cast outline can mark blocks
# as avatar_speaking, avatar_voiceover, pip_talking_head, stock_video, or stock_photo.
# Each demands a different writing style; we inject the matching block of
# guidance into the per-scene prompt so the LLM stops writing "hey guys!"
# over a stock-photo block.
CATEGORY_SCRIPT_RULES: dict[str, str] = {
    "avatar_speaking": (
        "This block is the avatar TALKING DIRECTLY TO CAMERA. Write natural "
        "conversational speech with contractions and filler words. "
        "Include [gesture:KEY] markers for emphasis. The avatar's face "
        "fills the frame."
    ),
    "avatar_voiceover": (
        "This block is a VOICEOVER over a background video/photo — the "
        "avatar's face is NOT visible. Write CLEAN NARRATION: no 'hey guys', "
        "no direct camera address. Describe what the viewer is seeing on "
        "the background. Pacing should match the on-screen content. "
        "No gesture markers (the avatar is invisible)."
    ),
    "pip_talking_head": (
        "This block has the avatar in a SMALL PICTURE-IN-PICTURE WINDOW "
        "(~30% of screen) over background product/demo content. Write "
        "CONVERSATIONAL COMMENTARY that references what's on screen "
        "behind the PiP — e.g. 'look at this texture…', 'see how it "
        "absorbs?'. Light gestures only."
    ),
    "avatar_action": (
        "This block shows the AVATAR PERFORMING AN ACTION in a scene — not "
        "talking to camera. The system auto-generates SCENE-SPECIFIC first "
        "and last frames of the avatar (face preserved) and animates between "
        "them. Fill THREE prompt fields: "
        "(a) motion_prompt — vivid 15-30 word description of the action, "
        "setting, lighting, camera (e.g. 'running through a sunlit jungle "
        "trail, confident stride, hair flowing, golden hour lighting, "
        "slow-motion cinematic'). DO NOT repeat the avatar's age/skin/hair/"
        "outfit — those are auto-prepended. "
        "(b) action_start_prompt — 1-2 sentences for the FIRST frame: "
        "camera angle, initial pose, expression, scene/wardrobe. "
        "(c) action_end_prompt — 1-2 sentences for the FINAL frame using "
        "the same vocabulary, showing where the motion lands. "
        "The user can later edit either frame prompt in the editor's frame "
        "carousel and click Regenerate. "
        "VOICING MODE (required field `voicing_mode`): pick one of three modes: "
        "(1) tts_dialogue — voiceover narrates the action; script is non-empty, ≤18s; "
        "(2) prosody_only — a single prosody beat like '[laugh]', '[gasp]', '[sigh]' and nothing else; "
        "(3) motion_sfx_only — script MUST be \"\" (empty) and the block carries a `motion_sfx[]` array "
        "(cues like footstep_soft, fabric_rustle, object_pickup, breath_out, hand_clap…) — "
        "the *movement* makes the sound, no TTS is rendered. Use motion_sfx_only for "
        "fashion walks, transitions, product handling, or any visual-first beat. "
        "VOICEOVER FLAG (required field `voiceover_enabled`, PR #76): action blocks are "
        "NEVER lip-synced — the lipsync engines distort moving subjects. If the block "
        "has dialogue, the renderer plays it as a paired voiceover audio track over the "
        "motion clip. Emit `voiceover_enabled`: true when the dialogue should play over "
        "the action (DEFAULT for any tts_dialogue/prosody_only block), false for pure "
        "silent visual beats where the line should be dropped. The user can override "
        "this in the Script step."
    ),
    "stock_video": (
        "This block is PURE STOCK FOOTAGE — no avatar, no narration. "
        "Instead of dialogue, write a BRIEF TEXT OVERLAY (max 8 words) "
        "that supports the visual. Return that text as `script_text`. "
        "motion_prompt is empty."
    ),
    "stock_photo": (
        "This block is a STATIC PHOTO with optional ken-burns zoom. No "
        "avatar, no narration. Instead of dialogue, write a BRIEF TEXT "
        "OVERLAY (max 8 words) that supports the photo — a stat, a quote, "
        "or a CTA. Return it as `script_text`. motion_prompt is empty."
    ),
    # Fallbacks for the optional generated-media categories.
    "generated_photo": (
        "This block is a generated photo backdrop with optional text "
        "overlay. Write a brief text overlay (max 10 words). No avatar."
    ),
    "generated_video": (
        "This block is a generated video backdrop. Write a brief text "
        "overlay (max 10 words) OR a short voiceover line if the scene "
        "benefits from narration. "
        "VOICING MODE (required field `voicing_mode`): pick "
        "tts_dialogue (script non-empty, ≤18s narration over the generated visual), "
        "prosody_only (a single prosody marker like '[gasp]'), or "
        "motion_sfx_only (script MUST be \"\" and `motion_sfx[]` carries ≥1 cue — "
        "use this when the generated scene depicts a discrete physical action whose "
        "foley should drive the audio rather than dialogue)."
    ),
}


async def _generate_full_script(
    outline: list[dict],
    persona: dict,
    system_prompt: str,
    description: str,
    duration_target: int,
    style_dna_section: str = "",
    cast_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """Pass 1 — Write the entire cast script as ONE continuous narrative.

    This replaces the previous per-block independent LLM calls where each
    block's call was unaware of the others — leading to every block
    starting with "Comrades" or "Ok you guys" because each thought it was
    the opening. Pass 2 (`_split_into_blocks`) takes this flowing text and
    splits it into per-block scripts following the outline.
    """
    from services.openrouter import get_openrouter_service
    import sentry_sdk

    # Per-block word budgets at SPEAKING_WPM (140) — show the model the
    # exact ceiling per block, not just the total. The LLM consistently
    # respected per-block limits in testing where it ignored a single
    # global "stay under X words" rule and overshot by ~50%.
    block_summary_lines = []
    for i, b in enumerate(outline):
        secs = float(b.get("estimated_duration_seconds", 10) or 10)
        block_word_cap = _word_budget_for_seconds(secs)
        block_summary_lines.append(
            f"- Block {i + 1}: {b.get('block_type', 'scene')} "
            f"({b.get('category', 'avatar_speaking')}, "
            f"~{int(secs)}s, MAX {block_word_cap} words) — {b.get('purpose', '')}"
        )
    block_summary = "\n".join(block_summary_lines)

    word_target = _word_budget_for_seconds(duration_target)
    word_hard_cap = int(round(word_target * SCRIPT_OVERSHOOT_SLACK))

    user_brief = (description or "").strip() or "(no specific brief)"
    prompt = f"""{style_dna_section}USER'S BRIEF: {user_brief}

Write a COMPLETE video script as ONE continuous flowing narrative.
Do NOT write separate blocks. Write it as if ONE person is talking non-stop.

STRUCTURE (follow this order, but write as continuous speech):
{block_summary}

TOTAL TARGET DURATION: {duration_target} seconds at {SPEAKING_WPM} words/min — MAX {word_hard_cap} words.
The narration MUST fit within the time limit. If unsure, prefer FEWER words.

RULES:
1. First 5 words are the hook — NO filler. BANNED openers: "hey guys", "ok so", "comrades", "okay so", "alright so"
2. EVERY sentence starts DIFFERENTLY — no repeated openers
3. The text must FLOW — each sentence connects naturally to the next
4. Energy varies: high for hooks/CTAs, medium for demos, low for intimate moments
5. Include prosody: (excited), (casual), (whispering), [pause]
6. Include [sfx:NAME] markers at key moments
7. HARD LIMIT: total word count MUST be ≤ {word_hard_cap} words. Going over WILL truncate the cast. When in doubt, end early.
8. Each block's narration must fit ITS per-block word cap above — do not let any block run long.

Return ONLY the script text. No JSON. No block markers. No headers."""

    oai = get_openrouter_service()
    log_creative_model_use("cast_full_script", CAST_GENERATOR_MODEL)
    try:
        raw = await oai.generate_text(
            prompt=prompt,
            system_prompt=system_prompt,
            model=CAST_GENERATOR_MODEL,
            temperature=0.9,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise

    await _record_llm_usage(
        user_id=user_id,
        cast_id=cast_id,
        model=CAST_GENERATOR_MODEL,
        usage=getattr(oai, "last_usage", {}),
        event_type="script_generation",
    )

    full_text = (raw or "").strip()

    # Single-line trace — duration budgeting visibility per the bug report.
    actual_words = _count_words(full_text)
    predicted_seconds = round(actual_words / WORDS_PER_SECOND, 1) if actual_words else 0
    _log(
        "info",
        f"script generated: target={duration_target}s, predicted={predicted_seconds}s "
        f"({actual_words} words at {SPEAKING_WPM} wpm)",
        cast_id=cast_id,
        target_seconds=duration_target,
        predicted_seconds=predicted_seconds,
        actual_words=actual_words,
        word_cap=word_hard_cap,
    )

    return full_text


async def _split_into_blocks(
    full_script: str,
    outline: list[dict],
    persona: dict,
    cast_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> list[dict]:
    """Pass 2 — Split the flowing script into production blocks.

    Returns list of {"block_index", "script_text", "motion_prompt"} dicts,
    one per outline block. For visual-only blocks (avatar_action,
    stock_video, stock_photo) the script_text may be empty and
    motion_prompt carries the visual brief.

    On unexpected output (wrong length, malformed JSON), the caller is
    expected to fall back to a degraded single-block layout — we don't
    want a Pass 2 hiccup to lose the Pass 1 narrative.
    """
    from services.openrouter import get_openrouter_service
    import sentry_sdk
    import re as _re

    block_plan = json.dumps(
        [
            {
                "index": i,
                "type": b.get("block_type"),
                "category": b.get("category"),
                "duration": b.get("estimated_duration_seconds"),
                "purpose": b.get("purpose", ""),
            }
            for i, b in enumerate(outline)
        ],
        indent=2,
    )

    prompt = f"""Split this script into {len(outline)} blocks following the plan.

FULL SCRIPT:
{full_script}

BLOCK PLAN:
{block_plan}

For each block, extract the EXACT portion of script (do not rewrite). For
avatar_action / stock_video / stock_photo blocks, script_text can be the
empty string (visual carries meaning) — set motion_prompt for avatar_action
blocks describing the visual action.

Return ONLY a JSON array, one object per block in order:
[{{"block_index": 0, "script_text": "exact text from the script", "motion_prompt": ""}}]"""

    oai = get_openrouter_service()
    log_creative_model_use("cast_split_into_blocks", CAST_GENERATOR_MODEL)
    try:
        raw = await oai.generate_text(
            prompt=prompt,
            system_prompt="You are a video editor splitting a script into blocks.",
            model=CAST_GENERATOR_MODEL,
            max_tokens=get_outline_max_tokens(),
            temperature=0.3,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise

    await _record_llm_usage(
        user_id=user_id,
        cast_id=cast_id,
        model=CAST_GENERATOR_MODEL,
        usage=getattr(oai, "last_usage", {}),
        event_type="script_split",
    )

    cleaned = (raw or "").strip()
    cleaned = _re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = _re.sub(r"\s*```$", "", cleaned)
    cleaned = _strip_json_fences(cleaned)

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError) as exc:
        sentry_sdk.capture_exception(exc)
        raise

    if not isinstance(parsed, list):
        exc = ValueError(
            f"Pass 2 returned non-list: {type(parsed).__name__}"
        )
        sentry_sdk.capture_exception(exc)
        raise exc

    # Pad or trim if the LLM split into a different count than expected.
    while len(parsed) < len(outline):
        parsed.append({
            "block_index": len(parsed),
            "script_text": "",
            "motion_prompt": "",
        })
    parsed = parsed[: len(outline)]

    # Normalise each entry — guarantee the keys downstream callers read.
    normalised: list[dict] = []
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            entry = {}
        normalised.append({
            "block_index": int(entry.get("block_index", i) or i),
            "script_text": (entry.get("script_text") or "").strip(),
            "motion_prompt": (entry.get("motion_prompt") or "").strip(),
        })
    return normalised


async def generate_scripts(
    cast_id: str,
    outline: list[dict],
    persona: dict,
    num_variants: int = 1,
    voice_profile: dict = None,
    description: str = "",
    products: list[dict] | None = None,
    user_id: Optional[str] = None,
) -> list[dict]:
    """Generate per-block scripts using a two-pass approach.

    Pass 1: Write the entire cast as one continuous narrative (energy varies,
    no repeated openers, hooked properly).
    Pass 2: Split that narrative into blocks following the outline.

    The two-pass approach replaces the previous per-block independent
    generation where each block's LLM call was unaware of the others —
    leading to every block starting with "Comrades" or "Ok you guys"
    because each thought it was opening.

    Returns list of {block_index, variant_index, script_text, motion_prompt}.
    Smart Cast aware: per-category writing rules are not needed at the
    block level any more because Pass 1 sees the full block plan.

    Universal content engine: the system prompt is built dynamically from
    the user's description (tutorial, motion, fashion, lecture, …) so a
    "Zara running in trousers" brief no longer comes out as a live-selling
    pitch.

    `num_variants` is accepted for backwards compatibility with callers
    but the two-pass approach returns one variant per block (variant_index=0).
    Re-roll for an alternative is a separate Regenerate action on a single
    block, not a parallel batch.
    """
    from services.openrouter import get_openrouter_service

    from services.ai_prompts import get_prompt
    from services.content_type import detect_content_type, fill_dynamic_placeholders, build_system_prompt
    script_prompt_entry = get_prompt("cast_script_generator")
    content_type = await detect_content_type(description or "", products)
    system_prompt = fill_dynamic_placeholders(script_prompt_entry["system"], content_type)

    if voice_profile and voice_profile.get("tone") and voice_profile["tone"] != "unknown":
        # Layer the voice profile on top of the dynamic content-type persona.
        voice_block = build_system_prompt(content_type, voice_profile)
        system_prompt = (
            f"{voice_block}\n\n"
            f"Speaking style: {voice_profile.get('tone', 'enthusiastic')}.\n"
            f"Average sentence: {voice_profile.get('avg_sentence_length', 10)} words.\n"
            f"Common phrases: {', '.join(voice_profile.get('common_phrases', [])[:15])}.\n"
            f"Sentence starters: {', '.join(voice_profile.get('sentence_starters', [])[:8])}.\n"
            f"{int(voice_profile.get('question_frequency', 0) * 100)}% of sentences are questions.\n"
            f"Energy: {voice_profile.get('avg_energy', 'medium')}.\n"
            f"Sign-offs: {', '.join(voice_profile.get('sign_offs', [])[:5])}.\n\n"
            f"Examples of how they actually talk:\n"
            f"{chr(10).join('- ' + chr(34) + p + chr(34) for p in voice_profile.get('sample_phrases', [])[:8])}\n\n"
            "MATCH THIS STYLE EXACTLY. Use their phrases naturally. Sound like THEM."
        )

    import re as _re
    import sentry_sdk

    if not outline:
        return []

    style_dna_section = _build_style_dna_section(persona)
    duration_target = sum(
        int(b.get("estimated_duration_seconds", 10) or 10) for b in outline
    )

    # PASS 1 — write the entire cast as one continuous narrative. Failure
    # here has no useful fallback (no script means no cast), so re-raise.
    try:
        full_script = await _generate_full_script(
            outline=outline,
            persona=persona,
            system_prompt=system_prompt,
            description=description,
            duration_target=duration_target,
            style_dna_section=style_dna_section,
            cast_id=cast_id,
            user_id=user_id,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise

    # PASS 2 — split into per-block scripts. If this fails (or returns
    # malformed JSON), degrade gracefully: place the full Pass 1 narrative
    # on block 0 and leave the remaining blocks empty so the user still
    # sees their script and can re-roll individual blocks.
    try:
        block_scripts = await _split_into_blocks(
            full_script=full_script,
            outline=outline,
            persona=persona,
            cast_id=cast_id,
            user_id=user_id,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning(
            "Script split failed; degrading to single-block output. err=%s",
            exc,
        )
        block_scripts = [
            {"block_index": 0, "script_text": full_script, "motion_prompt": ""}
        ]
        for i in range(1, len(outline)):
            block_scripts.append({
                "block_index": i,
                "script_text": "",
                "motion_prompt": "",
            })

    # Map back to the existing output shape (one variant for now). Apply
    # the same product_name placeholder substitution + "Block N:" prefix
    # cleanup the previous per-block path used so downstream rendering
    # behaves identically.
    scripts: list[dict] = []
    for bs in block_scripts:
        i = int(bs.get("block_index", 0) or 0)
        scene = outline[i] if 0 <= i < len(outline) else {}
        script_text = (bs.get("script_text") or "").strip()
        motion_prompt = (bs.get("motion_prompt") or "").strip()

        # Strip stray "Block 1:" prefixes the splitter sometimes leaves in.
        script_text = _re.sub(r"^Block\s*\d+\s*[:.]\s*", "", script_text)

        # Substitute [product_name] placeholder if it leaks through.
        product_name = (
            scene.get("product_name")
            or scene.get("title")
            or persona.get("product_name")
            or "this product"
        )
        script_text = script_text.replace("[product_name]", product_name)
        script_text = script_text.replace("[Product Name]", product_name)

        # Post-generation HARD enforcement: if this block's script exceeds
        # the per-block word budget at SPEAKING_WPM by >SCRIPT_OVERSHOOT_SLACK,
        # trim from the END (sentence-by-sentence) until it fits. This is the
        # safety net that prevents the user-reported "60s → 2:30" bug — Pass 1
        # is asked to stay under cap but the LLM occasionally overshoots; we
        # enforce mechanically here regardless of what the model returned.
        block_secs = float(scene.get("estimated_duration_seconds", 10) or 10)
        block_word_cap = _word_budget_for_seconds(block_secs)
        block_word_hard_cap = int(round(block_word_cap * SCRIPT_OVERSHOOT_SLACK))
        actual_words = _count_words(script_text)
        if actual_words > block_word_hard_cap:
            trimmed = _trim_to_word_cap(script_text, block_word_hard_cap)
            _log(
                "warning",
                f"Block {i} script trimmed: {actual_words} → {_count_words(trimmed)} "
                f"words (cap {block_word_hard_cap} for {block_secs:.0f}s)",
                cast_id=cast_id,
                block_index=i,
                original_words=actual_words,
                trimmed_words=_count_words(trimmed),
                cap_words=block_word_hard_cap,
                block_seconds=block_secs,
            )
            try:
                import sentry_sdk as _sentry
                _sentry.capture_message(
                    f"Script overshoot trimmed: block {i} {actual_words}→"
                    f"{_count_words(trimmed)} words (cap {block_word_hard_cap})",
                    level="warning",
                )
            except Exception:
                pass
            script_text = trimmed

        scripts.append({
            "block_index": i,
            "variant_index": 0,
            "script_text": script_text,
            "motion_prompt": motion_prompt,
        })

    scripts.sort(key=lambda s: (s["block_index"], s["variant_index"]))

    # Final tally for visibility — what the user will actually hear.
    total_words = sum(_count_words(s.get("script_text", "")) for s in scripts)
    predicted_seconds = round(total_words / WORDS_PER_SECOND, 1) if total_words else 0
    _log(
        "info",
        f"scripts ready: target={duration_target}s, predicted={predicted_seconds}s "
        f"({total_words} words at {SPEAKING_WPM} wpm, {len(scripts)} blocks)",
        cast_id=cast_id,
        target_seconds=duration_target,
        predicted_seconds=predicted_seconds,
        total_words=total_words,
        block_count=len(scripts),
    )

    return scripts


def _trim_to_word_cap(text: str, max_words: int) -> str:
    """Trim text to ≤max_words by dropping trailing sentences. Falls back to a
    hard word-slice if even one sentence exceeds the cap (rare). Preserves
    prosody markers — they don't count toward the budget."""
    import re as _re
    if max_words <= 0 or _count_words(text) <= max_words:
        return text
    sentences = _re.split(r"(?<=[.!?])\s+", text.strip())
    out: list[str] = []
    used = 0
    for s in sentences:
        w = _count_words(s)
        if used + w > max_words:
            break
        out.append(s)
        used += w
    if not out:
        # Single overly-long sentence — emergency word-slice. Strip prosody
        # before counting so we don't waste budget on markers.
        words = text.split()
        clean_words = [
            w for w in words
            if not (w.startswith("[") or w.startswith("("))
        ]
        if len(clean_words) <= max_words:
            return text
        # Take the first max_words actual words, preserving prosody markers
        # that fall before them.
        kept = []
        kept_count = 0
        for w in words:
            kept.append(w)
            if not (w.startswith("[") or w.startswith("(")):
                kept_count += 1
            if kept_count >= max_words:
                break
        result = " ".join(kept).rstrip(",;:")
        if not result.endswith((".", "!", "?")):
            result += "."
        return result
    return " ".join(out)


async def suggest_clips(
    blocks: list[dict],
    description: str,
    *,
    cast_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> list[dict]:
    """Identify 2-4 self-contained segments that work as standalone shorts.

    Runs after generate_scripts has filled in scripts on the parent's blocks.
    Returns a list of clip dicts in the shape::

        {
          "name": "Hook & Reveal",
          "block_ids": ["blk_abc", "blk_def"],
          "duration_seconds": 22,
          "best_platforms": ["tiktok", "instagram_reels"],
          "why": "...",
        }

    Failure is non-fatal — any exception is captured to Sentry and we
    return []. Cast generation must never fail because clip suggestion did.

    NB: per the implementation gotchas we use stable block IDs (not
    positional indices). Reordering / deleting blocks would otherwise
    silently re-point an approved clip at the wrong segment.
    """
    import sentry_sdk
    if not blocks:
        return []
    try:
        from services.openrouter import get_openrouter_service
        block_summary = json.dumps(
            [
                {
                    "block_id": b.get("id") or b.get("block_id"),
                    "category": b.get("category", ""),
                    "duration": b.get("estimated_duration_seconds") or b.get("duration_seconds") or 10,
                    "summary": b.get("summary") or b.get("mood", ""),
                    "script": (b.get("script_text") or "")[:120],
                }
                for b in blocks
                if (b.get("id") or b.get("block_id"))
            ],
            indent=2,
        )
        user_brief = (description or "").strip() or "(no specific brief)"
        prompt = f"""From this video script ({len(blocks)} blocks), identify 2-4 segments that work as STANDALONE short clips (8-30 seconds each).

Each clip must:
- Start with a hook that works WITHOUT context (viewer hasn't seen the full video)
- Deliver value on its own (not a fragment)
- End at a natural stopping point (CTA, punchline, reveal, or cliffhanger)

USER'S ORIGINAL BRIEF: {user_brief}

BLOCKS:
{block_summary}

Return ONLY a JSON array, no preamble, no fences:
[{{
  "name": "short catchy name (2-4 words)",
  "block_ids": ["blk_..."],
  "duration_seconds": 15,
  "best_platforms": ["tiktok", "instagram_reels"],
  "why": "1 sentence explaining why this works as a standalone"
}}]"""
        oai = get_openrouter_service()
        log_creative_model_use("cast_suggest_clips", CAST_GENERATOR_MODEL)
        raw = await oai.generate_text(
            prompt=prompt,
            system_prompt="You are a social media content strategist. Identify clip-worthy segments. Always return valid JSON.",
            model=CAST_GENERATOR_MODEL,
            temperature=0.5,
        )
        await _record_llm_usage(
            user_id=user_id,
            cast_id=cast_id,
            model=CAST_GENERATOR_MODEL,
            usage=getattr(oai, "last_usage", {}) or {},
            event_type="clip_suggestion",
        )
        import re as _re
        cleaned = (raw or "").strip()
        cleaned = _re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = _re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("suggest_clips: LLM returned invalid JSON: %s", exc)
            return []
        if not isinstance(parsed, list):
            return []
        valid_block_ids = {b.get("id") or b.get("block_id") for b in blocks}
        cleaned_clips: list[dict] = []
        for clip in parsed:
            if not isinstance(clip, dict):
                continue
            ids = clip.get("block_ids") or []
            if not isinstance(ids, list):
                continue
            ids = [bid for bid in ids if bid in valid_block_ids]
            if not ids:
                continue
            cleaned_clips.append({
                "name": str(clip.get("name") or "Clip")[:80],
                "block_ids": ids,
                "duration_seconds": int(clip.get("duration_seconds") or 0) or None,
                "best_platforms": [str(p) for p in (clip.get("best_platforms") or []) if isinstance(p, str)],
                "why": str(clip.get("why") or "")[:300],
            })
        return cleaned_clips
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("suggest_clips failed: %s", exc)
        return []


async def generate_cast_clips(
    cast_id: str,
    blocks: list,
    avatar_voice_id: str,
    progress_callback=None,
    quality: str = "simple",
    avatar_face_ref_key: str = "",
    user_id: str = "",
    effects_config: dict = None,
    avatar_clip_mic_enabled: bool = False,
) -> dict:
    """
    Cast generation pipeline — webhook-based, non-blocking.

    For each variant:
    1. Generate TTS audio (synchronous — fast, ~5 seconds)
    2. Upload TTS audio to R2
    3. Submit InfiniteTalk job to RunPod WITH webhook callback
    4. Store runpod_job_id on the variant record
    5. Return immediately — webhook handles completion

    The Celery task completes in ~2 minutes (just TTS for all variants),
    NOT ~42 minutes × N variants (the old polling approach).

    Returns:
        {"success": bool, "failed_count": int, "total_variants": int, "submitted_jobs": int}
    """
    from services.fish_audio import get_fish_audio_service
    from services.runpod import get_runpod_service
    from services.r2_storage import get_r2_storage_service
    from services import sentry
    from config import settings

    runpod = get_runpod_service()
    r2 = get_r2_storage_service()

    total_variants = sum(len(getattr(b, 'variants', [])) for b in blocks)
    if total_variants == 0:
        return {"success": True, "failed_count": 0, "total_variants": 0, "submitted_jobs": 0}

    tts_completed = 0
    failed_count = 0
    submitted_jobs = 0

    from services.mic_presets import resolve_scene_voice_settings

    for block in blocks:
        # Voice chain precedence: per-block mic_on override > the scene
        # (AvatarLook) this block uses > avatar-wide default. Requires the
        # caller to have eager-loaded Block.avatar_look (this function gets
        # plain in-memory `blocks`, no session of its own to lazy-load with).
        _look = getattr(block, "avatar_look", None)
        block_clip_mic_enabled, scene_chain_id = resolve_scene_voice_settings(
            block_mic_on=getattr(block, "mic_on", None),
            avatar_clip_mic_enabled=bool(avatar_clip_mic_enabled),
            look_environment=getattr(_look, "environment", None),
            look_mic_visible=getattr(_look, "mic_visible", None),
        )
        logger.info(
            "voice mode=%s scene_chain=%s block=%s",
            "clip_mic" if block_clip_mic_enabled else "phone_mic",
            scene_chain_id, getattr(block, "id", None),
        )

        for variant in getattr(block, 'variants', []):
            if variant.status in (VariantStatus.READY, "READY"):
                # Skip already-ready variants (retry scenario)
                tts_completed += 1
                continue

            if not variant.script_text:
                variant.status = VariantStatus.FAILED
                variant.generation_error = "No script text"
                failed_count += 1
                tts_completed += 1
                continue

            try:
                # Step 1: TTS (synchronous — 5-15 seconds)
                variant.status = VariantStatus.GENERATING
                step = f"Generating audio {tts_completed + 1}/{total_variants}"
                if progress_callback:
                    await progress_callback(tts_completed / (total_variants * 2), step)

                tts_result = await get_fish_audio_service().generate_tts(
                    text=variant.script_text,
                    voice_id=avatar_voice_id,
                    clip_mic_enabled=block_clip_mic_enabled,
                    scene_chain_id=scene_chain_id,
                    block_id=getattr(block, "id", None),
                )

                audio_key = tts_result.get("audio_key", "")
                audio_url = tts_result.get("audio_url", "")
                duration = tts_result.get("duration_seconds", 0)

                # Ensure TTS audio is on R2
                tmp_path = tts_result.get("tmp_path", "")
                if audio_key and tmp_path:
                    import os
                    if os.path.exists(tmp_path) and not await r2.key_exists(audio_key):
                        await r2.upload_file(tmp_path, audio_key, content_type="audio/mpeg")

                if not audio_key:
                    # Generate a key and upload
                    if tts_result.get("audio_path") or tmp_path:
                        audio_key = f"creators/{user_id or cast_id}/casts/{cast_id}/tts/{variant.id}.mp3"
                        src_path = tts_result.get("audio_path") or tmp_path
                        await r2.upload_file(src_path, audio_key, content_type="audio/mpeg")

                # Build the public audio URL for RunPod
                if not audio_url and audio_key:
                    audio_url = r2.get_public_url(audio_key)

                variant.audio_key = audio_key
                variant.tts_duration_seconds = duration
                variant.duration_seconds = duration

                # Duration guard — block anything over 18s from reaching InfiniteTalk
                MAX_TTS_SECONDS = 18.0  # 15s target + 3s buffer for LLM variance
                if variant.tts_duration_seconds and variant.tts_duration_seconds > MAX_TTS_SECONDS:
                    logger.warning(
                        "Block %s variant %s exceeded duration cap: %.1fs > %.1fs. "
                        "LLM ignored length constraint. Script: %r",
                        variant.block_id, variant.id,
                        variant.tts_duration_seconds, MAX_TTS_SECONDS,
                        (variant.script_text or "")[:120],
                    )
                    variant.status = VariantStatus.FAILED
                    variant.generation_error = (
                        f"Script generated {variant.tts_duration_seconds:.1f}s of audio but 15s cap is enforced. "
                        f"Rewrite manually or regenerate."
                    )
                    failed_count += 1
                    continue

                # Step 2: Submit InfiniteTalk with webhook (non-blocking)
                scene_key = getattr(block, 'scene_image_key', None) or ""
                if not scene_key and avatar_face_ref_key:
                    scene_key = avatar_face_ref_key

                # PATH A: Pre-InfiniteTalk background compositing (static bg)
                # Priority: block.background_id (per-block) > cast effects_config.background (cast-level)
                try:
                    from services.background_compositor import composite_face_on_background
                    from models.avatar_background import AvatarBackground as _AvatarBg
                    import tempfile

                    bg_image_url_for_compositor = ""
                    bg_type = "original"
                    bg_color = "#1a1a2e"
                    bg_gradient = None

                    # Check per-block background first
                    if hasattr(block, 'background_id') and block.background_id:
                        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
                        from config import settings as _cfg
                        _eng = create_async_engine(_cfg.database_url, pool_size=1, max_overflow=0)
                        _factory = async_sessionmaker(_eng, class_=AsyncSession, expire_on_commit=False)
                        async with _factory() as _tmp_sess:
                            avatar_bg = await _tmp_sess.get(_AvatarBg, block.background_id)
                            if avatar_bg and not avatar_bg.deleted_at:
                                bg_image_url_for_compositor = r2.get_public_url(avatar_bg.r2_key)
                                bg_type = "image"
                            else:
                                _log("warning", f"Block {block.id} references deleted/missing background_id={block.background_id}, falling back to cast-level",
                                     cast_id=cast_id)
                        await _eng.dispose()

                    # Fall back to cast-level config if no per-block background
                    if bg_type == "original":
                        bg_config = (effects_config or {}).get("background", {})
                        bg_type = bg_config.get("type", "original")
                        bg_color = bg_config.get("color", "#1a1a2e")
                        bg_gradient = bg_config.get("gradient")
                        if bg_type == "image" and bg_config.get("image_key"):
                            bg_image_url_for_compositor = r2.get_public_url(bg_config["image_key"])

                    if bg_type in ("color", "image", "gradient") and scene_key:
                        face_url = r2.get_public_url(scene_key)
                        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                            composited_path = tmp.name

                        await composite_face_on_background(
                            face_image_url=face_url,
                            bg_type=bg_type,
                            bg_color=bg_color,
                            bg_image_url=bg_image_url_for_compositor,
                            gradient=bg_gradient,
                            output_path=composited_path,
                        )

                        composited_key = f"creators/{user_id}/casts/{cast_id}/input_face_bg_{variant.id}.jpg"
                        await r2.upload_file(composited_path, composited_key, content_type="image/jpeg")
                        scene_key = composited_key
                        import os
                        os.unlink(composited_path)

                        _log("info", f"Composited {bg_type} background for variant {variant.id} (source: {'block' if hasattr(block, 'background_id') and block.background_id else 'cast'})",
                             cast_id=cast_id, variant_id=variant.id)
                except Exception as bg_err:
                    _log("warning", f"Pre-InfiniteTalk background compositing failed, using original: {bg_err}",
                         cast_id=cast_id, variant_id=variant.id)

                if scene_key and audio_url:
                    image_url = r2.get_public_url(scene_key)

                    motion = getattr(variant, 'motion_prompt', '') or _default_motion_for_block_role(
                        block.type.value if hasattr(block.type, 'value') else str(block.type)
                    )
                    job_id = await runpod.submit_video_job_webhook(
                        image_url=image_url,
                        audio_url=audio_url,
                        variant_id=variant.id,
                        prompt=motion,
                    )

                    variant.runpod_job_id = job_id
                    variant.status = VariantStatus.GENERATING  # Waiting for webhook callback
                    submitted_jobs += 1

                    _log("info", f"Variant {variant.id}: TTS done, InfiniteTalk submitted",
                         cast_id=cast_id, variant_id=variant.id,
                         job_id=job_id, audio_key=audio_key)
                else:
                    variant.status = VariantStatus.FAILED
                    variant.generation_error = "Missing face image or audio URL"
                    failed_count += 1

                tts_completed += 1

                if progress_callback:
                    await progress_callback(
                        tts_completed / (total_variants * 2),
                        f"Audio {tts_completed}/{total_variants} done, submitting video jobs..."
                    )

            except Exception as e:
                variant.status = VariantStatus.FAILED
                variant.generation_error = str(e)[:500]
                failed_count += 1
                tts_completed += 1
                sentry.capture_exception(e)
                _log("error", f"Variant {variant.id} TTS/submit failed: {e}",
                     cast_id=cast_id, variant_id=variant.id)

    # All TTS done, all InfiniteTalk jobs submitted
    # Report 50% progress — webhook handles the rest
    if progress_callback:
        await progress_callback(
            0.5,
            f"All audio generated. {submitted_jobs} video jobs submitted to GPU. Rendering..."
        )

    _log("info", f"Cast {cast_id}: {submitted_jobs} InfiniteTalk jobs submitted via webhook. "
         f"{failed_count} failed during TTS. Task complete.",
         cast_id=cast_id, total=total_variants, submitted=submitted_jobs, failed=failed_count)

    # Success means we submitted at least some jobs (or all were already ready)
    # The webhook will handle final cast status when all jobs complete
    return {
        "success": failed_count < total_variants,  # At least one variant succeeded
        "failed_count": failed_count,
        "total_variants": total_variants,
        "submitted_jobs": submitted_jobs,
    }

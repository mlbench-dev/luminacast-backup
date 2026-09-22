"""Production-level (quick/standard/premium) generation thresholds.

Previously production_level did nothing to actual cast generation — it was
purely a billing multiplier (services.billing_config) despite UI copy
claiming it shaped content. These helpers make it genuinely constrain
generation via three template-derived thresholds:

  * ``_production_level_block_cap`` / ``_enforce_block_count_cap`` — caps
    total block count (Quick collapses repeated beats to unique types,
    Premium allows one extra beat, Standard unchanged).
  * ``_effective_duration_target_seconds`` — derives a duration ceiling from
    the template's own ``est_duration_range``, tightening (never expanding)
    the user's manual duration_target_seconds.
  * ``_production_level_avatar_ratio`` / ``_enforce_template_broll_ratio`` —
    template-bias-driven sibling of the existing (global-ratio,
    short-form-only) ``_enforce_live_ratios``, reusing its shared
    ``_demote_surplus_avatar_blocks`` core.

DB-free by design (mirrors tests/unit/test_live_outline_bias.py): every
helper operates on plain outline scene dicts, no DB/network involved.
"""
import pytest

from engine.cast_generator import (
    _normalize_production_level_for_generation,
    _production_level_block_cap,
    _enforce_block_count_cap,
    _effective_duration_target_seconds,
    _production_level_avatar_ratio,
    _enforce_template_broll_ratio,
    _AVATAR_RATIO_CATEGORIES,
)
from services.cast_templates import TEMPLATES

DEMO_HEAVY = TEMPLATES["demo_heavy"]  # 5 blocks, 1 repeated (PRODUCT_DEMO x2), bias 30/70/0, range [25, 50]
TALKING_HEAD_HOOK = TEMPLATES["talking_head_hook"]  # 4 blocks, no repeats, bias 85/15/0, range [20, 45]


def _scene(category="avatar_speaking", injected=None, duration=6):
    s = {"category": category, "estimated_duration_seconds": duration}
    if injected:
        s["injected"] = injected
    return s


# ── _normalize_production_level_for_generation ───────────────────────────────


def test_normalize_keeps_the_three_valid_values():
    assert _normalize_production_level_for_generation("quick") == "quick"
    assert _normalize_production_level_for_generation("standard") == "standard"
    assert _normalize_production_level_for_generation("premium") == "premium"


def test_normalize_defaults_unknown_values_to_standard():
    # Legacy quality values and empty/None must NOT collapse quick's meaning —
    # they should just default to standard, distinct from billing's normalizer
    # which explicitly maps "quick" -> "standard" (that would erase this tier).
    assert _normalize_production_level_for_generation(None) == "standard"
    assert _normalize_production_level_for_generation("") == "standard"
    assert _normalize_production_level_for_generation("simple") == "standard"
    assert _normalize_production_level_for_generation("hd_plus") == "standard"


# ── _production_level_block_cap ──────────────────────────────────────────────


def test_block_cap_without_template():
    # Auto mode: Quick still gets a fixed lean cap so a bloated outline is
    # trimmed; Standard / Premium stay uncapped.
    assert _production_level_block_cap(None, "quick") == 5
    assert _production_level_block_cap(None, "standard") is None
    assert _production_level_block_cap(None, "premium") is None


def test_block_cap_quick_collapses_repeats():
    # Demo Heavy: ["HOOK", "PRODUCT_DEMO", "FEATURE_SHOWCASE", "PRODUCT_DEMO", "CTA"]
    # 5 blocks total, but only 4 unique types (PRODUCT_DEMO repeats).
    assert _production_level_block_cap(DEMO_HEAVY, "quick") == 4


def test_block_cap_quick_no_repeats_to_collapse():
    # Talking Head Hook has no repeated block type — quick cap equals its
    # natural length, same as standard (nothing to trim).
    assert _production_level_block_cap(TALKING_HEAD_HOOK, "quick") == 4
    assert _production_level_block_cap(TALKING_HEAD_HOOK, "standard") == 4


def test_block_cap_standard_matches_template_length():
    assert _production_level_block_cap(DEMO_HEAVY, "standard") == 5


def test_block_cap_premium_adds_one_beat():
    assert _production_level_block_cap(DEMO_HEAVY, "premium") == 6


# ── _enforce_block_count_cap ──────────────────────────────────────────────────


def test_enforce_block_cap_noop_when_cap_is_none():
    scenes = [_scene() for _ in range(5)]
    out = _enforce_block_count_cap(scenes, None, cast_id="cst_x")
    assert out is scenes


def test_enforce_block_cap_noop_when_already_under_cap():
    scenes = [_scene() for _ in range(3)]
    out = _enforce_block_count_cap(scenes, 5, cast_id="cst_x")
    assert len(out) == 3


def test_enforce_block_cap_trims_to_exact_count():
    scenes = [_scene() for _ in range(7)]
    out = _enforce_block_count_cap(scenes, 4, cast_id="cst_x")
    assert len(out) == 4


def test_enforce_block_cap_protects_hook_and_cta():
    scenes = [
        {"category": "avatar_speaking", "block_type": "hook"},
        _scene(), _scene(), _scene(), _scene(),
        {"category": "avatar_speaking", "block_type": "cta"},
    ]
    out = _enforce_block_count_cap(scenes, 3, cast_id="cst_x")
    assert out[0]["block_type"] == "hook"
    assert out[-1]["block_type"] == "cta"
    assert len(out) == 3


def test_enforce_block_cap_protects_injected_beats():
    scenes = [
        _scene(),
        _scene(injected="product_display"),
        _scene(),
        _scene(injected="broll"),
        _scene(),
    ]
    # Cap at 2 — only the two injected beats can survive since everything
    # else is fair game; the cap is a soft ceiling, so it stops once only
    # protected scenes remain rather than violating one of them.
    out = _enforce_block_count_cap(scenes, 2, cast_id="cst_x")
    injected_kept = [s for s in out if s.get("injected")]
    assert len(injected_kept) == 2


def test_enforce_block_cap_stops_early_if_everything_left_is_protected():
    # 2 scenes, both bookends (hook/CTA implicitly via index 0/-1) — cap of 1
    # can't be honored without violating a protected scene, so it's a no-op
    # beyond what's structurally possible.
    scenes = [_scene(), _scene()]
    out = _enforce_block_count_cap(scenes, 1, cast_id="cst_x")
    assert len(out) == 2


# ── _effective_duration_target_seconds ───────────────────────────────────────


def test_effective_duration_manual_value_wins_without_template():
    # An explicitly-set manual duration always wins, template or not.
    assert _effective_duration_target_seconds(60, None, "quick") == 60
    assert _effective_duration_target_seconds(120, None, "premium") == 120


def test_effective_duration_auto_quick_default_when_nothing_set():
    # Auto mode + Quick + no manual duration -> the fixed short default,
    # so "Quick" actually produces a shorter cast without a template.
    assert _effective_duration_target_seconds(None, None, "quick") == 35


def test_effective_duration_auto_standard_and_premium_have_no_default():
    assert _effective_duration_target_seconds(None, None, "standard") is None
    assert _effective_duration_target_seconds(None, None, "premium") is None


def test_effective_duration_unchanged_for_standard():
    assert _effective_duration_target_seconds(60, DEMO_HEAVY, "standard") == 60
    assert _effective_duration_target_seconds(None, DEMO_HEAVY, "standard") is None


def test_effective_duration_explicit_user_value_always_wins_quick():
    # Demo Heavy range [25, 50]. An explicit user value ALWAYS wins outright,
    # even though 40s exceeds quick's [25] default ceiling — no more silent
    # clamping a number the user deliberately typed in.
    assert _effective_duration_target_seconds(40, DEMO_HEAVY, "quick") == 40


def test_effective_duration_explicit_user_value_always_wins_premium():
    # 200s is way outside Demo Heavy's [25, 50] range entirely, but an
    # explicit user choice is never overridden by the tier ceiling.
    assert _effective_duration_target_seconds(30, DEMO_HEAVY, "premium") == 30
    assert _effective_duration_target_seconds(200, DEMO_HEAVY, "premium") == 200


def test_effective_duration_becomes_the_ceiling_only_when_user_set_nothing():
    # The tier ceiling is purely a DEFAULT for when nothing was set manually.
    assert _effective_duration_target_seconds(None, DEMO_HEAVY, "quick") == 25
    assert _effective_duration_target_seconds(None, DEMO_HEAVY, "premium") == 50


# ── _production_level_avatar_ratio / _enforce_template_broll_ratio ──────────


def test_avatar_ratio_none_without_template():
    assert _production_level_avatar_ratio(None, "standard") is None


def test_avatar_ratio_standard_matches_template_bias():
    # Demo Heavy broll=0.5 -> avatar side = 0.5, unchanged from the template.
    assert abs(_production_level_avatar_ratio(DEMO_HEAVY, "standard") - 0.5) < 1e-9


def test_avatar_ratio_quick_raises_avatar_share():
    # Quick pulls broll DOWN by 0.15 -> avatar share goes UP.
    ratio = _production_level_avatar_ratio(DEMO_HEAVY, "quick")
    assert abs(ratio - 0.65) < 1e-9  # 1 - (0.5 - 0.15)


def test_avatar_ratio_premium_lowers_avatar_share():
    # Premium pushes broll UP by 0.15 -> avatar share goes DOWN.
    ratio = _production_level_avatar_ratio(DEMO_HEAVY, "premium")
    assert abs(ratio - 0.35) < 1e-9  # 1 - (0.5 + 0.15)


def test_enforce_template_broll_ratio_noop_without_template():
    scenes = [_scene() for _ in range(5)]
    out = _enforce_template_broll_ratio(scenes, None, "quick", cast_id="cst_x")
    assert out is scenes


def test_enforce_template_broll_ratio_demotes_surplus_avatar_blocks():
    # Multi-Angle Story bias avatar=0.65 broll=0.35 -> avatar ratio 0.65.
    # Force premium so broll ratio pushes UP (avatar ratio down), making the
    # demotion deterministic.
    multi_angle = TEMPLATES["multi_angle_story"]
    scenes = [_scene("avatar_speaking") for _ in range(10)]
    out = _enforce_template_broll_ratio(scenes, multi_angle, "premium", cast_id="cst_x")
    avatar_blocks = [s for s in out if s["category"] in _AVATAR_RATIO_CATEGORIES]
    assert len(avatar_blocks) < 10
    demoted = [s for s in out if s.get("ratio_demoted")]
    assert len(demoted) > 0
    for s in demoted:
        # Demoted to talking-head PIP (not avatar_voiceover) — the script
        # survives as active narration while the avatar keeps speaking in a
        # corner PIP window over the block's b-roll, per
        # _enforce_template_broll_ratio's demotion comment.
        assert s["category"] == "pip_talking_head"


def test_enforce_template_broll_ratio_protects_hook_and_cta():
    scenes = [{"category": "avatar_speaking", "estimated_duration_seconds": 6} for _ in range(5)]
    out = _enforce_template_broll_ratio(scenes, DEMO_HEAVY, "premium", cast_id="cst_x")
    assert out[0]["category"] == "avatar_speaking"
    assert out[-1]["category"] == "avatar_speaking"


def test_enforce_template_broll_ratio_applies_regardless_of_duration():
    # Unlike _enforce_live_ratios (short-form-only, gated on
    # duration_target_seconds), the template-driven version has no such
    # gate — it takes no duration argument at all and should still act on a
    # long-form plan.
    scenes = [_scene("avatar_speaking") for _ in range(10)]
    out = _enforce_template_broll_ratio(scenes, DEMO_HEAVY, "standard", cast_id="cst_x")
    avatar_blocks = [s for s in out if s["category"] in _AVATAR_RATIO_CATEGORIES]
    assert len(avatar_blocks) == 5  # round(0.5 * 10), same math regardless of "duration"

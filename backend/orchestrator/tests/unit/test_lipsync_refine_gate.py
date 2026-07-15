"""Unit tests for the post-bake lipsync-refine gating logic in
``services.provider_chain``.

Covers which block types / durations get refined under which env flags,
for both the speaking gate (``evaluate_speaking_lipsync_refine``, Change
B) and the motion gate (``should_run_motion_lipsync_refine``, Change A).

``sentry_sdk`` is a real installed dependency — do NOT stub it.
"""
from __future__ import annotations

import pytest

from services.provider_chain import (
    RefineDecision,
    evaluate_speaking_lipsync_refine,
    should_run_motion_lipsync_refine,
    should_run_sync_lipsync_refine,
)

# Env keys the gates read. Cleared before every case so a leaked flag
# from the host environment never changes a verdict.
_FLAGS = (
    "SPEAKING_LIPSYNC_REFINE_FOR_ALL",
    "PRODUCTION_LIPSYNC",
    "MOTION_LIPSYNC_REFINE_ENABLED",
    "MOTION_LIPSYNC_REFINE_BLOCK_TYPES",
    "MOTION_LIPSYNC_REFINE_MAX_DURATION_S",
)


@pytest.fixture(autouse=True)
def _clear_flags(monkeypatch):
    for key in _FLAGS:
        monkeypatch.delenv(key, raising=False)
    yield


# ──────────────────────────── speaking gate ────────────────────────────


def test_speaking_default_long_block_refines(monkeypatch):
    # No flags: the legacy >=3s gate applies. 11.3s (blk_0bd1cc27c4d0)
    # is the one block that refined in the field renders.
    d = evaluate_speaking_lipsync_refine(11.3)
    assert d == RefineDecision(True, "duration_ge_min", "speak")


def test_speaking_default_short_block_skipped(monkeypatch):
    # 5s PRODUCT speak block (@0:19/@0:47) — refined under the OLD gate
    # too (>=3s). The blocks that drifted were really skipped because
    # the FOR_ALL path was off AND they sat below 3s in some renders;
    # here we assert the sub-3s boundary is what the old gate excluded.
    d = evaluate_speaking_lipsync_refine(2.0)
    assert d.run is False
    assert d.reason == "below_min_duration"


def test_speaking_for_all_refines_short_block(monkeypatch):
    monkeypatch.setenv("SPEAKING_LIPSYNC_REFINE_FOR_ALL", "1")
    d = evaluate_speaking_lipsync_refine(2.0)
    assert d == RefineDecision(True, "speak_all_above_floor", "speak")


def test_speaking_for_all_still_skips_below_floor(monkeypatch):
    monkeypatch.setenv("SPEAKING_LIPSYNC_REFINE_FOR_ALL", "1")
    d = evaluate_speaking_lipsync_refine(0.3)
    assert d == RefineDecision(False, "below_floor", "speak")


def test_speaking_for_all_at_floor_boundary(monkeypatch):
    monkeypatch.setenv("SPEAKING_LIPSYNC_REFINE_FOR_ALL", "1")
    d = evaluate_speaking_lipsync_refine(0.5)
    assert d.run is True


def test_speaking_production_lipsync_forces_refine(monkeypatch):
    monkeypatch.setenv("PRODUCTION_LIPSYNC", "1")
    d = evaluate_speaking_lipsync_refine(0.1)
    assert d == RefineDecision(True, "production_forced", "speak")


def test_speaking_for_all_takes_precedence_over_production(monkeypatch):
    # FOR_ALL is checked first; below its floor it skips even when
    # PRODUCTION_LIPSYNC would otherwise force a refine.
    monkeypatch.setenv("SPEAKING_LIPSYNC_REFINE_FOR_ALL", "1")
    monkeypatch.setenv("PRODUCTION_LIPSYNC", "1")
    d = evaluate_speaking_lipsync_refine(0.1)
    assert d == RefineDecision(False, "below_floor", "speak")


def test_speaking_bad_input_is_safe(monkeypatch):
    d = evaluate_speaking_lipsync_refine(None)  # type: ignore[arg-type]
    assert d.run is False


def test_speaking_bool_facade_matches_decision(monkeypatch):
    monkeypatch.setenv("SPEAKING_LIPSYNC_REFINE_FOR_ALL", "1")
    assert should_run_sync_lipsync_refine(2.0) is True
    assert should_run_sync_lipsync_refine(0.1) is False


# ───────────────────────────── motion gate ─────────────────────────────


@pytest.mark.parametrize(
    "btype", ["HOOK", "PRODUCT", "PRODUCT_DEMO", "SOCIAL_PROOF", "PIP"]
)
def test_motion_default_eligible_types_refine(monkeypatch, btype):
    # Default ENABLED=true, default allow-list. 5s clip is under the 20s
    # ceiling. These are the @0:07/@0:13/@0:24/@0:52 motion complaints.
    d = should_run_motion_lipsync_refine(btype, 5.0)
    assert d == RefineDecision(True, "eligible", "motion")


def test_motion_case_insensitive_type(monkeypatch):
    d = should_run_motion_lipsync_refine("hook", 5.0)
    assert d.run is True


def test_motion_unlisted_type_skipped(monkeypatch):
    d = should_run_motion_lipsync_refine("CTA", 5.0)
    assert d == RefineDecision(False, "type_not_eligible", "motion")


def test_motion_master_switch_off(monkeypatch):
    monkeypatch.setenv("MOTION_LIPSYNC_REFINE_ENABLED", "0")
    d = should_run_motion_lipsync_refine("HOOK", 5.0)
    assert d == RefineDecision(False, "motion_disabled", "motion")


def test_motion_over_max_duration_skipped(monkeypatch):
    # Default ceiling is 20s.
    d = should_run_motion_lipsync_refine("HOOK", 25.0)
    assert d == RefineDecision(False, "over_max_duration", "motion")


def test_motion_at_max_duration_boundary(monkeypatch):
    # Exactly at the ceiling is allowed (only strictly-greater is skipped).
    d = should_run_motion_lipsync_refine("HOOK", 20.0)
    assert d.run is True


def test_motion_custom_block_types_override(monkeypatch):
    monkeypatch.setenv("MOTION_LIPSYNC_REFINE_BLOCK_TYPES", "HOOK")
    assert should_run_motion_lipsync_refine("HOOK", 5.0).run is True
    # PRODUCT no longer in the allow-list once overridden.
    assert (
        should_run_motion_lipsync_refine("PRODUCT", 5.0).reason
        == "type_not_eligible"
    )


def test_motion_custom_max_duration_override(monkeypatch):
    monkeypatch.setenv("MOTION_LIPSYNC_REFINE_MAX_DURATION_S", "8")
    assert should_run_motion_lipsync_refine("HOOK", 10.0).reason == (
        "over_max_duration"
    )
    assert should_run_motion_lipsync_refine("HOOK", 6.0).run is True


def test_motion_zero_ceiling_disables_cost_guard(monkeypatch):
    monkeypatch.setenv("MOTION_LIPSYNC_REFINE_MAX_DURATION_S", "0")
    d = should_run_motion_lipsync_refine("HOOK", 999.0)
    assert d.run is True


def test_motion_bad_ceiling_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MOTION_LIPSYNC_REFINE_MAX_DURATION_S", "not-a-number")
    # Falls back to the 20s default, so 25s is over.
    d = should_run_motion_lipsync_refine("HOOK", 25.0)
    assert d.reason == "over_max_duration"


def test_motion_empty_block_type_skipped(monkeypatch):
    d = should_run_motion_lipsync_refine("", 5.0)
    assert d.reason == "type_not_eligible"
    d2 = should_run_motion_lipsync_refine(None, 5.0)
    assert d2.reason == "type_not_eligible"


def test_motion_bad_duration_is_safe(monkeypatch):
    # Non-numeric duration coerces to 0.0 → under ceiling → eligible.
    d = should_run_motion_lipsync_refine("HOOK", None)  # type: ignore[arg-type]
    assert d.run is True

"""Unit tests for engine.cast_generator._clamp_outline_duration_strict.

PR #66 Fix 3 — tight ±10% clamp on top of the existing ±30% trim. A
45 s target that the LLM oversizes to 65 s must be scaled back into
[40.5 s, 49.5 s], and no resulting block may be < 3 s after scaling.
"""
from __future__ import annotations

import sys
import types

# Stub sentry_sdk so the production module can be imported without the
# real dep installed in the test sandbox. The clamp function uses
# `import sentry_sdk` locally, so this stub is enough.
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
    )


def _load_clamp():
    """Import the clamp helper lazily so this test file can run even
    when the wider engine.cast_generator module's heavy imports (LLM
    clients etc.) aren't available — we only need this one function.

    When sqlalchemy / config aren't installed in the test sandbox we
    stub them out enough for the top-of-module imports to resolve.
    """
    if "sqlalchemy" not in sys.modules:
        sqlalchemy_stub = types.ModuleType("sqlalchemy")
        sys.modules["sqlalchemy"] = sqlalchemy_stub
    if "config" not in sys.modules:
        config_stub = types.ModuleType("config")
        config_stub.settings = types.SimpleNamespace()
        sys.modules["config"] = config_stub
    # models.variant pulls SQLAlchemy types; stub the whole module.
    if "models.variant" not in sys.modules:
        models_pkg = types.ModuleType("models")
        sys.modules["models"] = models_pkg
        variant_stub = types.ModuleType("models.variant")
        class _Variant: ...
        class _VariantStatus:
            PENDING = "PENDING"
            READY = "READY"
            FAILED = "FAILED"
        variant_stub.Variant = _Variant
        variant_stub.VariantStatus = _VariantStatus
        sys.modules["models.variant"] = variant_stub
    from engine.cast_generator import _clamp_outline_duration_strict
    return _clamp_outline_duration_strict


def test_45s_target_65s_scenes_clamped_within_10pct():
    """LLM produces 5 blocks summing to 65 s for a 45 s request →
    every block scales by 45/65 ≈ 0.692, and the new total lands
    inside [40.5, 49.5] s.
    """
    clamp = _load_clamp()
    scenes = [
        {"estimated_duration_seconds": 13},
        {"estimated_duration_seconds": 13},
        {"estimated_duration_seconds": 13},
        {"estimated_duration_seconds": 13},
        {"estimated_duration_seconds": 13},
    ]
    out = clamp(scenes, 45, cast_id="cst_test")
    total = sum(s["estimated_duration_seconds"] for s in out)
    assert 40.5 <= total <= 49.5, (
        f"clamped total {total}s outside ±10% of 45s"
    )
    assert all(s["estimated_duration_seconds"] >= 3 for s in out), (
        "no block should be < 3s after scaling"
    )


def test_total_within_10pct_is_noop():
    """A 47 s outline for a 45 s target is already inside ±10%; the
    clamp must leave durations untouched.
    """
    clamp = _load_clamp()
    scenes = [
        {"estimated_duration_seconds": 15},
        {"estimated_duration_seconds": 16},
        {"estimated_duration_seconds": 16},
    ]
    out = clamp(scenes, 45, cast_id="cst_test")
    total = sum(s["estimated_duration_seconds"] for s in out)
    assert total == 47, f"expected unchanged 47s, got {total}"


def test_undershoot_pads_last_block():
    """A 30 s outline for a 45 s target is below the lower bound; the
    clamp pads the last block to bring the total back to target.
    """
    clamp = _load_clamp()
    scenes = [
        {"estimated_duration_seconds": 10},
        {"estimated_duration_seconds": 10},
        {"estimated_duration_seconds": 10},
    ]
    out = clamp(scenes, 45, cast_id="cst_test")
    total = sum(s["estimated_duration_seconds"] for s in out)
    assert 40.5 <= total <= 49.5, (
        f"padded total {total}s outside ±10% of 45s"
    )
    # Last block should have been the one extended.
    assert out[-1]["estimated_duration_seconds"] > 10


def test_empty_scenes_returns_empty():
    """Empty input is a no-op."""
    clamp = _load_clamp()
    assert clamp([], 45) == []


def test_zero_target_returns_unchanged():
    """No target means no clamping (the user never specified one)."""
    clamp = _load_clamp()
    scenes = [{"estimated_duration_seconds": 13}, {"estimated_duration_seconds": 13}]
    out = clamp(list(scenes), 0)
    assert out == scenes

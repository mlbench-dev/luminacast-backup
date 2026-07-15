"""PR-I unit tests — video-second backends record OUTPUT seconds + real cost.

Before PR-I, `_log_render_usage` only computed a correct video-second quantity
and a non-zero cost for fal_t2v / fal_i2v. fal_hallo, fal_sync_lipsync_v2_pro,
fal_sync_lipsync_v3, product_elements_bake and wavespeed all map to
quantity_unit="video_seconds" but fell into the GPU branch: they recorded
wall-clock `elapsed_seconds` (400–1100s in prod) at $0.00 cost because
`calculate_gpu_render_cost(provider="fal_ai"/"wavespeed")` has no rate entry.

These tests pin:
  * video-second backends bill the OUTPUT clip seconds (slot duration), not
    wall-clock, with a non-zero provider cost;
  * gpu-second backends (hostkey/modal/runpod) are unchanged;
  * `calculate_video_second_cost` returns the right per-backend rate.

`sentry_sdk` is a real installed dependency — do NOT stub it.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

import tasks.cast_render as cr
from services.cost_rates import COST_RATES
from services.usage_tracker import calculate_video_second_cost


# ──────────────────────────────────────────────────────────────────────────
# Harness: drive _log_render_usage with a stubbed DB session + a log_usage
# that captures kwargs, but keep the REAL cost calculators so we can assert
# provider_cost_usd > 0 and quantity correctness.
# ──────────────────────────────────────────────────────────────────────────


def _run_log_render_usage(monkeypatch, backend, **extra):
    captured: dict = {}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            return None

    fake_db = types.ModuleType("database")
    fake_db.async_session_factory = lambda: _FakeSession()
    monkeypatch.setitem(sys.modules, "database", fake_db)

    # Real calculators, captured log_usage. We re-export the genuine functions
    # from services.usage_tracker so cost math is exercised end-to-end.
    import services.usage_tracker as real_tracker

    fake_tracker = types.ModuleType("services.usage_tracker")
    fake_tracker.calculate_gpu_render_cost = real_tracker.calculate_gpu_render_cost
    fake_tracker.calculate_video_second_cost = real_tracker.calculate_video_second_cost

    async def _log_usage(session, **kwargs):
        captured.update(kwargs)

    fake_tracker.log_usage = _log_usage
    monkeypatch.setitem(sys.modules, "services.usage_tracker", fake_tracker)

    asyncio.run(cr._log_render_usage(
        user_id="u1", cast_id="c1", block_id="blk_1", render_id="rnd_1",
        backend=backend, event_type="avatar_render", elapsed_seconds=120.0,
        **extra,
    ))
    return captured


# ── 1. video-second backends bill OUTPUT seconds, not wall-clock ───────────


@pytest.mark.parametrize(
    "backend",
    ["fal_hallo", "fal_sync_lipsync_v3", "product_elements_bake", "wavespeed"],
)
def test_log_render_usage_video_second_backends_use_slot_seconds(monkeypatch, backend):
    # §5.4 requested-tier billing OFF so the slot output seconds are billed
    # verbatim (otherwise a requested tier could override the 5.0s slot).
    monkeypatch.setattr(cr, "_USAGE_LOG_STRICT_BACKEND", True)
    monkeypatch.setattr(cr, "_USAGE_BILL_REQUESTED_TIER", False)

    captured = _run_log_render_usage(
        monkeypatch, backend, output_video_seconds=5.0,
    )

    assert captured["quantity"] == 5.0, backend
    assert captured["quantity_unit"] == "video_seconds", backend
    # The whole point of PR-I: cost is no longer silently $0.00.
    assert captured["provider_cost_usd"] > 0, backend
    # And it is NOT the 120s wall-clock value.
    assert captured["quantity"] != 120.0, backend


def test_log_render_usage_video_second_backend_via_deprecated_alias(monkeypatch):
    # Older call sites still pass the deprecated `fal_video_seconds` kwarg.
    monkeypatch.setattr(cr, "_USAGE_BILL_REQUESTED_TIER", False)
    captured = _run_log_render_usage(
        monkeypatch, "fal_hallo", fal_video_seconds=5.0,
    )
    assert captured["quantity"] == 5.0
    assert captured["quantity_unit"] == "video_seconds"
    assert captured["provider_cost_usd"] > 0


# ── 2. gpu-second backends are unchanged ───────────────────────────────────


@pytest.mark.parametrize("backend", ["hostkey", "modal", "runpod"])
def test_log_render_usage_gpu_backends_unchanged(monkeypatch, backend):
    monkeypatch.setattr(cr, "_USAGE_LOG_STRICT_BACKEND", True)
    captured = _run_log_render_usage(
        monkeypatch, backend, output_video_seconds=5.0,
    )
    # GPU backends ignore output_video_seconds and bill wall-clock elapsed.
    assert captured["quantity"] == 120.0, backend
    assert captured["quantity_unit"] == "gpu_seconds", backend


# ── 3. per-backend rate correctness ────────────────────────────────────────


@pytest.mark.parametrize(
    "backend,rate_key",
    [
        ("fal_hallo",               "fal/fal_hallo"),
        ("fal_sync_lipsync_v2_pro", "fal/sync_lipsync_v2_pro"),
        ("fal_sync_lipsync_v3",     "fal/sync_lipsync_v3"),
        ("product_elements_bake",   "fal/kling_elements_v3_pro"),
        ("wavespeed",               "wavespeed/infinitalk"),
    ],
)
def test_calculate_video_second_cost_per_backend(backend, rate_key):
    seconds = 5.0
    expected = round(COST_RATES[rate_key] * seconds, 4)
    assert calculate_video_second_cost(backend, None, seconds) == expected
    assert expected > 0


def test_calculate_video_second_cost_fal_t2v_delegates_to_model_rate():
    # fal_t2v / fal_i2v delegate to calculate_fal_video_cost (model-aware).
    seconds = 5.0
    kling = round(COST_RATES["fal/kling_2.5_turbo_pro"] * seconds, 4)
    assert calculate_video_second_cost("fal_t2v", "kling-2.5", seconds) == kling
    # Unknown model falls back to the older Wan rate.
    wan = round(COST_RATES["fal/wan_2.2_t2v"] * seconds, 4)
    assert calculate_video_second_cost("fal_i2v", None, seconds) == wan


def test_calculate_video_second_cost_unmapped_returns_zero():
    assert calculate_video_second_cost("bogus_backend", None, 5.0) == 0.0


def test_calculate_video_second_cost_nonpositive_seconds_zero():
    assert calculate_video_second_cost("fal_hallo", None, 0.0) == 0.0
    assert calculate_video_second_cost("fal_hallo", None, -3.0) == 0.0

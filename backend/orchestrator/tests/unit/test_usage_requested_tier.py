"""Unit tests for Render_Quality_Duration_Validation.md §5.4 — UsageEvent
fal video bakes are billed at the REQUESTED provider tier (overshot, snapped)
rather than the timeline slot.

Covers the pure decision helper ``tasks.cast_render._resolve_billed_video_seconds``.
``sentry_sdk`` is a real installed dependency — do NOT stub it.
"""
from __future__ import annotations

import pytest  # noqa: F401

import tasks.cast_render as cr


def test_bills_requested_tier_when_enabled(monkeypatch):
    # rnd_7923dde6c01f: slot=9.3s billed instead of the 10s requested tier.
    monkeypatch.setattr(cr, "_USAGE_BILL_REQUESTED_TIER", True)
    billed = cr._resolve_billed_video_seconds(
        fal_video_seconds=9.3, requested_tier_s=10.0
    )
    assert billed == 10.0


def test_falls_back_to_slot_when_disabled(monkeypatch):
    monkeypatch.setattr(cr, "_USAGE_BILL_REQUESTED_TIER", False)
    billed = cr._resolve_billed_video_seconds(
        fal_video_seconds=9.3, requested_tier_s=10.0
    )
    assert billed == 9.3


def test_falls_back_to_slot_when_tier_unknown(monkeypatch):
    monkeypatch.setattr(cr, "_USAGE_BILL_REQUESTED_TIER", True)
    assert cr._resolve_billed_video_seconds(9.3, None) == 9.3
    assert cr._resolve_billed_video_seconds(9.3, 0.0) == 9.3


def test_handles_none_slot(monkeypatch):
    monkeypatch.setattr(cr, "_USAGE_BILL_REQUESTED_TIER", True)
    # No slot, but a known tier → bill the tier.
    assert cr._resolve_billed_video_seconds(None, 5.0) == 5.0
    # Neither known → zero.
    assert cr._resolve_billed_video_seconds(None, None) == 0.0


def test_bill_requested_tier_flag_default_true():
    assert cr._USAGE_BILL_REQUESTED_TIER is True


# ──────────────────────────────────────────────────────────────────────────
# §5.4 — UsageEvent backend → provider / quantity_unit attribution.
# Regression guard for rnd_e8304a5dd7bf where live WaveSpeed bakes were billed
# as provider="hostkey" quantity_unit="gpu_seconds" via the old silent default.
# ──────────────────────────────────────────────────────────────────────────


def test_backend_maps_are_in_lockstep():
    # Every backend must define BOTH a provider and a unit so neither lookup
    # silently falls through.
    assert set(cr._BACKEND_TO_PROVIDER) == set(cr._BACKEND_TO_UNIT)


@pytest.mark.parametrize(
    "backend,provider",
    [
        ("hostkey", "hostkey"),
        ("musetalk", "hostkey"),
        ("modal", "modal"),
        ("runpod", "runpod"),
        ("hostkey_t2v", "hostkey"),
        ("fal_t2v", "fal_ai"),
        ("fal_i2v", "fal_ai"),
        ("fal_hallo", "fal_ai"),
        ("wavespeed", "wavespeed"),
        ("product_elements_bake", "fal_ai"),
        ("fal_sync_lipsync_v2_pro", "fal_ai"),
        ("fal_sync_lipsync_v3", "fal_ai"),
    ],
)
def test_known_backends_map_to_provider(backend, provider):
    assert cr._BACKEND_TO_PROVIDER[backend] == provider


def test_wavespeed_unit_is_video_seconds_not_gpu():
    # The exact rnd_e8304a5dd7bf misattribution: WaveSpeed must bill in
    # video_seconds, never gpu_seconds.
    assert cr._BACKEND_TO_UNIT["wavespeed"] == "video_seconds"


def test_hostkey_unit_is_gpu_seconds():
    assert cr._BACKEND_TO_UNIT["hostkey"] == "gpu_seconds"


def test_fal_and_kling_units_are_video_seconds():
    for backend in ("fal_t2v", "fal_i2v", "fal_hallo", "product_elements_bake",
                    "fal_sync_lipsync_v2_pro", "fal_sync_lipsync_v3"):
        assert cr._BACKEND_TO_UNIT[backend] == "video_seconds", backend


def test_strict_backend_flag_default_true():
    assert cr._USAGE_LOG_STRICT_BACKEND is True


def _run_log_render_usage(monkeypatch, backend, **extra):
    """Drive ``_log_render_usage`` with stubbed DB + usage_tracker, capturing
    the kwargs handed to ``log_usage``. Returns (captured_kwargs, sentry_msgs)."""
    import asyncio
    import sys
    import types

    captured: dict = {}
    sentry_msgs: list = []

    # Stub the lazily-imported ``database`` and ``services.usage_tracker``.
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

    fake_tracker = types.ModuleType("services.usage_tracker")
    fake_tracker.calculate_fal_video_cost = lambda model, secs: 0.0
    fake_tracker.calculate_gpu_render_cost = lambda provider, secs: 0.0
    # PR-I: _log_render_usage now imports calculate_video_second_cost for the
    # video-second branch. Stub it so this harness keeps capturing log_usage
    # kwargs instead of the import failing and swallowing to Sentry.
    fake_tracker.calculate_video_second_cost = lambda backend, model, secs: 0.0

    async def _log_usage(session, **kwargs):
        captured.update(kwargs)

    fake_tracker.log_usage = _log_usage
    monkeypatch.setitem(sys.modules, "services.usage_tracker", fake_tracker)

    monkeypatch.setattr(
        cr.sentry_sdk, "capture_message",
        lambda msg, level=None: sentry_msgs.append((msg, level)),
    )

    asyncio.run(cr._log_render_usage(
        user_id="u1", cast_id="c1", block_id="blk_1", render_id="rnd_1",
        backend=backend, event_type="motion_render", elapsed_seconds=10.0,
        **extra,
    ))
    return captured, sentry_msgs


def test_wavespeed_logged_as_video_seconds(monkeypatch):
    monkeypatch.setattr(cr, "_USAGE_LOG_STRICT_BACKEND", True)
    captured, sentry_msgs = _run_log_render_usage(monkeypatch, "wavespeed")
    assert captured["provider"] == "wavespeed"
    assert captured["quantity_unit"] == "video_seconds"
    assert sentry_msgs == []


def test_unknown_backend_fails_loud(monkeypatch):
    monkeypatch.setattr(cr, "_USAGE_LOG_STRICT_BACKEND", True)
    captured, sentry_msgs = _run_log_render_usage(monkeypatch, "bogus_backend")
    # Never silently "hostkey".
    assert captured["provider"] == "unknown"
    assert any("bogus_backend" in m for m, _ in sentry_msgs)


def test_unknown_backend_rollback_silent_hostkey(monkeypatch):
    # USAGE_LOG_STRICT_BACKEND=false restores the legacy silent default.
    monkeypatch.setattr(cr, "_USAGE_LOG_STRICT_BACKEND", False)
    captured, sentry_msgs = _run_log_render_usage(monkeypatch, "bogus_backend")
    assert captured["provider"] == "hostkey"
    assert sentry_msgs == []


def test_hostkey_logged_as_gpu_seconds(monkeypatch):
    monkeypatch.setattr(cr, "_USAGE_LOG_STRICT_BACKEND", True)
    captured, _ = _run_log_render_usage(monkeypatch, "hostkey")
    assert captured["provider"] == "hostkey"
    assert captured["quantity_unit"] == "gpu_seconds"

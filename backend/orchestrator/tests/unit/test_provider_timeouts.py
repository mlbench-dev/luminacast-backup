"""Unit tests for duration-derived provider timeouts.

Verifies the project standard "no hardcoded render/audio timeouts — derive
per-block from duration" for the render/bake/lipsync provider paths.

Background: production validation render rnd_cc4f2d8da674 block 3 (slot 5.00s,
TTS 9.67s) failed because every speaking provider (product_elements_bake,
wavespeed_infinitetalk, fal_hallo) timed out at exactly 180s — the old
``_compute_cloud_timeout`` floor. ``derive_provider_timeout`` replaces that
floor with 300s and scales at 60x realtime up to a 1800s ceiling.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Stub sentry_sdk so the provider module imports cleanly in the test sandbox
# without the real dependency (mirrors test_render_providers.py).
if "sentry_sdk" not in sys.modules:
    class _NoScope:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def set_tag(self, *_a, **_k):
            pass

        def set_extra(self, *_a, **_k):
            pass

    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
        push_scope=lambda: _NoScope(),
    )


def _import_providers():
    from services import render_providers
    return render_providers


# ─────────────────────────────────────────────────────────────────────────────
# derive_provider_timeout: the matrix from the task spec.
# ─────────────────────────────────────────────────────────────────────────────

def test_derive_provider_timeout_zero_is_floor():
    rp = _import_providers()
    assert rp.derive_provider_timeout(0) == 300


def test_derive_provider_timeout_short_clip_hits_floor():
    rp = _import_providers()
    # 5s * 60 = 300, which equals the floor — a tiny block no longer races
    # the old 180s cap that broke rnd_cc4f2d8da674 block 3.
    assert rp.derive_provider_timeout(5) == 300


def test_derive_provider_timeout_ten_seconds():
    rp = _import_providers()
    assert rp.derive_provider_timeout(10) == 600


def test_derive_provider_timeout_thirty_seconds_hits_ceiling():
    rp = _import_providers()
    # 30s * 60 = 1800 = ceiling.
    assert rp.derive_provider_timeout(30) == 1800


def test_derive_provider_timeout_sixty_seconds_capped_at_ceiling():
    rp = _import_providers()
    # 60s * 60 = 3600 → capped at 1800.
    assert rp.derive_provider_timeout(60) == 1800


def test_derive_provider_timeout_none_is_floor():
    rp = _import_providers()
    assert rp.derive_provider_timeout(None) == 300


def test_derive_provider_timeout_bad_input_is_floor():
    rp = _import_providers()
    assert rp.derive_provider_timeout("not-a-number") == 300


def test_derive_provider_timeout_never_180_for_failing_block():
    """The exact block that failed in production: TTS 9.67s. The old floor
    produced 180s; the new helper must produce well above that."""
    rp = _import_providers()
    t = rp.derive_provider_timeout(9.67)
    assert t != 180
    # 9.67 * 60 = 580 (int), above the 300 floor and below the 1800 ceiling.
    assert t == 580
    assert t > 180


def test_derive_provider_timeout_custom_bounds():
    rp = _import_providers()
    assert rp.derive_provider_timeout(
        10, floor_s=100, ratio=10, ceiling_s=200
    ) == 100  # 10*10=100 == floor
    assert rp.derive_provider_timeout(
        100, floor_s=100, ratio=10, ceiling_s=200
    ) == 200  # 100*10=1000 → ceiling


# ─────────────────────────────────────────────────────────────────────────────
# Mock-based: a provider applies the DERIVED timeout (not 180) to its poll
# deadline. WaveSpeed's poll loop uses ``time.monotonic() + timeout`` and
# raises a TimeoutError whose message embeds the deadline budget, so we drive
# the loop straight to its deadline and assert the budget is the derived value.
# ─────────────────────────────────────────────────────────────────────────────

def test_wavespeed_applies_derived_timeout_not_180(monkeypatch):
    rp = _import_providers()
    provider = rp.WavespeedInfinitetalkProvider()

    # Submit returns a pending prediction so the provider enters its poll
    # loop; poll always returns "processing" so it can only exit via the
    # duration-derived deadline.
    submit_response = MagicMock()
    submit_response.status_code = 200
    submit_response.json = MagicMock(return_value={
        "data": {"id": "pred_timeout", "status": "created"},
    })
    poll_response = MagicMock()
    poll_response.status_code = 200
    poll_response.json = MagicMock(return_value={
        "data": {"status": "processing", "outputs": []},
    })

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=submit_response)
    fake_client.get = AsyncMock(return_value=poll_response)

    # Spy on the derived-timeout helper to confirm it is what the provider
    # uses (and that it is fed the block's effective duration, not a const).
    real_derive = rp.derive_provider_timeout
    seen = {}

    def _spy(duration_s, **kw):
        val = real_derive(duration_s, **kw)
        seen["duration_s"] = duration_s
        seen["value"] = val
        return val

    monkeypatch.setattr(rp, "derive_provider_timeout", _spy)

    # Make the poll deadline already in the past so the loop raises on its
    # first iteration without real sleeping: freeze monotonic so
    # ``time.monotonic() > deadline`` is immediately true.
    clock = {"t": 0.0}
    monkeypatch.setattr(rp.time, "monotonic", lambda: clock["t"])

    async def _advance_clock(*_a, **_k):
        # First sleep call jumps the clock past the derived deadline.
        clock["t"] += seen["value"] + 1.0

    monkeypatch.setattr(rp.asyncio, "sleep", _advance_clock)

    with patch.dict("os.environ", {"WAVESPEED_API_KEY": "test-key"}):
        with patch.object(rp.httpx, "AsyncClient", return_value=fake_client):
            with pytest.raises(TimeoutError) as exc_info:
                import asyncio
                asyncio.run(provider.generate(
                    image_url="https://x", audio_url="https://y", prompt="p",
                    width=480, height=848, audio_duration_s=10.0,
                ))

    # 10s block → 600s derived budget, never the hardcoded 180.
    assert seen["duration_s"] == 10.0
    assert seen["value"] == 600
    assert "600s" in str(exc_info.value)
    assert "180s" not in str(exc_info.value)

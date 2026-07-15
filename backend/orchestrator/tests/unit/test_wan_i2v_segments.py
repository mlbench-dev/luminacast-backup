"""Unit tests for the Wan 2.7 I2V duration tier set, overshoot tier-snap,
and multi-segment chaining added for Render_Quality_Duration_Validation.md
§1.1 / §1.1.3.

Pure planning math — no ffmpeg / network. ``sentry_sdk`` is a real installed
dependency; do NOT stub it (a SimpleNamespace stub shadows the real package
and breaks downstream integration imports). ``capture_exception`` is a harmless
no-op without a configured DSN.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest  # noqa: F401

from services.render_providers import (  # noqa: E402
    WAN_27_I2V_DURATIONS,
    WAN_27_I2V_MIN_S,
    WAN_27_I2V_MAX_S,
    _overshoot_target_s,
    _plan_wan_i2v_segments,
    _snap_up_tier,
)

import services.wan_body_motion as wbm  # noqa: E402


def test_tier_set_is_full_integer_range():
    # Wan 2.7 I2V serves every whole second in [min, max], unlike the
    # discrete (5, 10) T2V tier set.
    assert WAN_27_I2V_DURATIONS[0] == WAN_27_I2V_MIN_S
    assert WAN_27_I2V_DURATIONS[-1] == WAN_27_I2V_MAX_S
    assert list(WAN_27_I2V_DURATIONS) == list(
        range(WAN_27_I2V_MIN_S, WAN_27_I2V_MAX_S + 1)
    )


def test_short_slot_single_segment():
    # The rnd_7923dde6c01f shortfall: a 3.63s slot must produce a single
    # segment that, after overshoot, is >= slot (was baking 1.07s).
    segs = _plan_wan_i2v_segments(3.63)
    assert segs == [4]  # ceil(3.63) snapped into range
    overshot = _snap_up_tier(_overshoot_target_s(float(segs[-1])), WAN_27_I2V_DURATIONS)
    # 4 * 1.15 = 4.6 -> snap up to 5; >= the 3.63 slot.
    assert overshot >= 3.63
    assert overshot == 5


def test_slot_at_max_tier_single_segment():
    assert _plan_wan_i2v_segments(float(WAN_27_I2V_MAX_S)) == [WAN_27_I2V_MAX_S]


def test_slot_over_max_tier_chains():
    # 16.5s > 15s max -> two segments, each within range, summing to >= ceil.
    segs = _plan_wan_i2v_segments(16.5)
    assert all(WAN_27_I2V_MIN_S <= s <= WAN_27_I2V_MAX_S for s in segs)
    assert sum(segs) >= 17
    assert len(segs) >= 2


def test_chain_final_segment_never_below_min():
    # A total that would leave a sub-min remainder must steal from the
    # previous chunk so the last segment stays >= min.
    segs = _plan_wan_i2v_segments(float(WAN_27_I2V_MAX_S + 1))  # 16
    assert segs[-1] >= WAN_27_I2V_MIN_S
    assert all(WAN_27_I2V_MIN_S <= s <= WAN_27_I2V_MAX_S for s in segs)


def test_zero_or_negative_slot_is_empty():
    assert _plan_wan_i2v_segments(0.0) == []
    assert _plan_wan_i2v_segments(-3.0) == []


def test_overshoot_applies_only_to_final_segment():
    # The §1.1.3 invariant: interior segments stay exact, only the final
    # tier is overshot.
    segs = _plan_wan_i2v_segments(22.0)
    interior = list(segs[:-1])
    segs[-1] = _snap_up_tier(
        _overshoot_target_s(float(segs[-1])), WAN_27_I2V_DURATIONS
    )
    # Interior untouched.
    assert segs[:-1] == interior
    # Total >= original slot.
    assert sum(segs) >= 22


# ──────────────────────────────────────────────────────────────────────────
# §1.1 — per-endpoint ``duration`` param type (regression guard for PR #103's
# blanket str() that 422'd live Wan 2.7 I2V on cst_d2dd91985028).
# ──────────────────────────────────────────────────────────────────────────


def _capture_wan_subscribe(monkeypatch):
    """Install a fake ``fal_client`` whose ``subscribe`` records its args and
    returns a minimal valid Wan result. Returns the capture dict."""
    captured: dict = {}

    def fake_subscribe(model_id, arguments=None, **kwargs):
        captured["model_id"] = model_id
        captured["arguments"] = arguments or {}
        return {"video": {"url": "https://example.com/out.mp4", "duration": 5.0}}

    fake_mod = types.ModuleType("fal_client")
    fake_mod.subscribe = fake_subscribe
    monkeypatch.setitem(sys.modules, "fal_client", fake_mod)
    return captured


def test_wan_i2v_duration_is_int_not_str(monkeypatch):
    # The bug: PR #103 sent duration="12" (str) → fal Wan 2.7 I2V 422
    # literal_error expecting an integer enum member.
    monkeypatch.setattr(wbm, "WAN_I2V_DURATION_AS_INT", True)
    captured = _capture_wan_subscribe(monkeypatch)

    result = asyncio.run(
        wbm._submit_wan_segment(
            start_image_url="https://example.com/start.jpg",
            end_image_url=None,
            full_prompt="A person walks.",
            duration_int=12,
            resolution="720p",
        )
    )

    assert result["video_url"] == "https://example.com/out.mp4"
    assert captured["model_id"] == wbm.FAL_WAN_27_I2V_ENDPOINT
    body = captured["arguments"]
    assert type(body["duration"]) is int
    assert body["duration"] == 12


def test_wan_i2v_override_false_sends_string(monkeypatch):
    # Emergency rollback: WAN_I2V_DURATION_AS_INT=false reverts to the old
    # string behaviour without a code change.
    monkeypatch.setattr(wbm, "WAN_I2V_DURATION_AS_INT", False)
    captured = _capture_wan_subscribe(monkeypatch)

    asyncio.run(
        wbm._submit_wan_segment(
            start_image_url="https://example.com/start.jpg",
            end_image_url=None,
            full_prompt="A person walks.",
            duration_int=12,
            resolution="720p",
        )
    )
    body = captured["arguments"]
    assert type(body["duration"]) is str
    assert body["duration"] == "12"


def test_wan_t2v_endpoint_duration_is_str():
    # The non-I2V (T2V) endpoint keeps the string form regardless of the
    # I2V override — only the image-to-video endpoint flips to int.
    t2v_endpoint = "fal-ai/wan/v2.2-a14b/text-to-video"
    param = wbm._wan_duration_param(12, t2v_endpoint)
    assert type(param) is str
    assert param == "12"


def test_wan_endpoint_detection():
    assert wbm._endpoint_is_wan_i2v(wbm.FAL_WAN_27_I2V_ENDPOINT) is True
    assert wbm._endpoint_is_wan_i2v("fal-ai/wan/v2.2-a14b/text-to-video") is False


def test_wan_i2v_default_flag_true():
    import os
    import importlib
    saved = os.environ.pop("WAN_I2V_DURATION_AS_INT", None)
    try:
        reloaded = importlib.reload(wbm)
        assert reloaded.WAN_I2V_DURATION_AS_INT is True
    finally:
        if saved is not None:
            os.environ["WAN_I2V_DURATION_AS_INT"] = saved
        importlib.reload(wbm)

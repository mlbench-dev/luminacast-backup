"""Unit tests for the PIP (social-proof talking-head) route fix.

Context: PIP blocks used to route through ``submit_and_wait(is_pip=True)``,
whose cloud tier looped the face still into a static video and ran fal
sync-lipsync v2/pro off of it (``fal_sync_lipsync_v2_pro``). That produced a
100%-frozen bake the validator rejected as ``clip_mostly_frozen``
(render rnd_f9a7f008ab85, block blk_c59218e232f4).

PIP now routes through the SAME audio-driven speaking cascade as full-frame
speaking blocks — WaveSpeed InfiniteTalk → fal Hallo — which animates the
mouth from the audio and produces a real talking clip with motion. The PIP
inset (small bottom-corner placement) is applied as a post-bake step in the
FFmpeg compose pass via ``timeline_builder.pip_geometry``, operating over the
full-frame talking clip baked by the cascade.

These tests assert:
  1. The chain PIP now uses (``build_speaking_chain``) leads with WaveSpeed
     InfiniteTalk and never contains the frozen-face
     ``FalSyncLipsyncV2ProProvider``.
  2. The inset-overlay post-step (``pip_geometry``) still produces a small
     inset placement for pip_small / pip_medium — i.e. the special visual
     treatment is preserved after the upstream generator change.
"""
from __future__ import annotations

import sys
import types


# ─── Stub sentry_sdk so importing the modules doesn't pull in the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
        capture_message=lambda *_a, **_k: None,
        add_breadcrumb=lambda *_a, **_k: None,
    )


def test_pip_speaking_chain_leads_with_wavespeed_not_sync_lipsync():
    """The cascade PIP now routes through must lead with WaveSpeed
    InfiniteTalk (audio-driven, real motion) and must NOT contain the
    frozen-face fal sync-lipsync v2/pro provider."""
    from services.provider_chain import build_speaking_chain
    from services.render_providers import (
        WavespeedInfinitetalkProvider,
        FalSyncLipsyncV2ProProvider,
    )

    # hostkey_acquired=False mirrors production (HOSTKEY decommissioned), so
    # the first audio-driven generator is WaveSpeed.
    providers = build_speaking_chain(
        block_duration_s=4.57,
        hostkey_acquired=False,
    )

    assert providers, "speaking chain must not be empty"
    assert isinstance(providers[0], WavespeedInfinitetalkProvider), (
        f"PIP must lead with WaveSpeed InfiniteTalk, got "
        f"{type(providers[0]).__name__}"
    )
    # The frozen-face generator must be absent from the first-pass cascade.
    assert not any(
        isinstance(p, FalSyncLipsyncV2ProProvider) for p in providers
    ), "fal_sync_lipsync_v2_pro must not be in the PIP first-pass cascade"
    assert not any(
        getattr(p, "name", "") == "fal_sync_lipsync_v2_pro" for p in providers
    ), "no provider named fal_sync_lipsync_v2_pro may serve PIP blocks"


def test_pip_inset_post_step_preserved():
    """The PIP inset (corner placement) post-step must still produce a
    sub-canvas inset for the quarter-PIP primitives — the special visual
    treatment is preserved; only the upstream lipsync generator changed.

    Regression-5: the legacy ``pip_small`` / ``pip_medium`` vocabulary collapsed
    into ``pip_quarter_bl`` / ``pip_quarter_br`` (a ~1/4-area face anchored to a
    bottom corner). Legacy strings still resolve to those primitives.
    """
    from services.timeline_builder import pip_geometry

    canvas_w, canvas_h = 1080, 1920

    for layout in ("pip_quarter_bl", "pip_quarter_br", "pip_small", "pip_medium"):
        placement = pip_geometry(layout, canvas_w, canvas_h)
        assert placement["visible"] is True
        # An inset is strictly smaller than the full canvas …
        assert 0 < placement["w"] < canvas_w, f"{layout} width must be inset"
        assert 0 < placement["h"] < canvas_h, f"{layout} height must be inset"
        # … and offset from the canvas origin, confirming it is an overlay
        # placement rather than a full-frame replacement.
        assert placement["x"] >= 0 and placement["y"] > 0, (
            f"{layout} must sit inside the canvas as an inset overlay"
        )

    # The bottom-left and bottom-right quarter PIPs share dimensions but anchor
    # to opposite corners.
    bl = pip_geometry("pip_quarter_bl", canvas_w, canvas_h)
    br = pip_geometry("pip_quarter_br", canvas_w, canvas_h)
    assert (bl["w"], bl["h"]) == (br["w"], br["h"])
    assert br["x"] > bl["x"], "pip_quarter_br must anchor further right than bl"

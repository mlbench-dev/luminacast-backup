"""Unit tests for services.mic_presets — Step 7 selection logic only.

These tests cover the pure selector that maps a per-block ``mic_on``
flag to a named :class:`MicPreset` and exposes the existing audio-chain
identifier. No ffmpeg / TTS / post-process calls are exercised — this is
about which chain gets *selected*, not the DSP itself (the EQ strings
are user-vetted and live in services.media_processing).
"""
from __future__ import annotations

import importlib

import pytest

import services.mic_presets as mic_presets
from services.mic_presets import MicPreset, select_mic_preset


@pytest.fixture(autouse=True)
def _default_flag_on(monkeypatch):
    """Default the feature flag ON for every test unless overridden."""
    monkeypatch.setenv("VOICE_MIC_PRESETS_ENABLED", "true")
    importlib.reload(mic_presets)
    yield


def test_mic_on_returns_clip_mic_chain(monkeypatch):
    monkeypatch.setenv("VOICE_MIC_PRESETS_ENABLED", "true")
    importlib.reload(mic_presets)
    preset = mic_presets.select_mic_preset(True)
    assert preset is mic_presets.MicPreset.MIC_ON
    assert preset.chain_id == "clip_mic"
    assert preset.clip_mic_enabled is True


def test_mic_off_returns_ambient_room_chain(monkeypatch):
    monkeypatch.setenv("VOICE_MIC_PRESETS_ENABLED", "true")
    importlib.reload(mic_presets)
    preset = mic_presets.select_mic_preset(False)
    assert preset is mic_presets.MicPreset.MIC_OFF
    assert preset.chain_id == "ambient_room"
    assert preset.clip_mic_enabled is False


def test_mic_none_falls_through_to_default(monkeypatch):
    """mic_on=None means unset/inherit — selector returns None so the
    caller preserves whatever its current default behaviour is."""
    monkeypatch.setenv("VOICE_MIC_PRESETS_ENABLED", "true")
    importlib.reload(mic_presets)
    assert mic_presets.select_mic_preset(None) is None


def test_feature_flag_off_returns_none_for_all_inputs(monkeypatch):
    """Flag OFF → no preset selected for any input; caller falls through
    to its current default."""
    monkeypatch.setenv("VOICE_MIC_PRESETS_ENABLED", "false")
    importlib.reload(mic_presets)
    assert mic_presets.select_mic_preset(True) is None
    assert mic_presets.select_mic_preset(False) is None
    assert mic_presets.select_mic_preset(None) is None
    assert mic_presets.presets_enabled() is False


def test_feature_flag_defaults_on_when_unset(monkeypatch):
    monkeypatch.delenv("VOICE_MIC_PRESETS_ENABLED", raising=False)
    importlib.reload(mic_presets)
    assert mic_presets.presets_enabled() is True
    assert mic_presets.select_mic_preset(True) is mic_presets.MicPreset.MIC_ON


def test_chain_id_values_are_internal_not_user_facing():
    """The chain identifiers feed the `voice mode=...` log line and must
    name no engine/provider — they are the internal chain ids only."""
    assert MicPreset.MIC_ON.value == "clip_mic"
    assert MicPreset.MIC_OFF.value == "ambient_room"


def test_clip_mic_enabled_maps_to_post_process_boolean():
    """The boolean is what gets fed to post_process_voice / apply_mic_style;
    only MIC_ON drives the clip-mic chain."""
    assert select_mic_preset(True).clip_mic_enabled is True
    assert select_mic_preset(False).clip_mic_enabled is False


# ---------------------------------------------------------------------------
# select_scene_preset — environment-aware chain selection
# ---------------------------------------------------------------------------

def test_select_scene_preset_studio_mic_on():
    assert mic_presets.select_scene_preset("studio", True) == "clip_mic"


def test_select_scene_preset_room_mic_off_uses_soft_chain():
    """Room mic-off gets its own softer chain, distinct from studio's
    ambient_room — this is the whole point of environment-awareness."""
    assert mic_presets.select_scene_preset("room", False) == "ambient_room_soft"


def test_select_scene_preset_outdoor_mic_on_uses_windscreen():
    assert mic_presets.select_scene_preset("outdoor", True) == "clip_mic_windscreen"


def test_select_scene_preset_outdoor_mic_off():
    assert mic_presets.select_scene_preset("outdoor", False) == "ambient_outdoor"


def test_select_scene_preset_unknown_environment_falls_back_to_studio():
    assert mic_presets.select_scene_preset("spaceship", True) == "clip_mic"
    assert mic_presets.select_scene_preset("spaceship", False) == "ambient_room"


def test_select_scene_preset_none_environment_defaults_to_studio():
    assert mic_presets.select_scene_preset(None, True) == "clip_mic"


def test_select_scene_preset_none_mic_visible_returns_none():
    """mic_visible=None means unresolved — caller must resolve it first."""
    assert mic_presets.select_scene_preset("studio", None) is None


def test_select_scene_preset_case_insensitive_environment():
    assert mic_presets.select_scene_preset("STUDIO", True) == "clip_mic"
    assert mic_presets.select_scene_preset("Room", False) == "ambient_room_soft"


def test_select_scene_preset_respects_feature_flag(monkeypatch):
    monkeypatch.setenv("VOICE_MIC_PRESETS_ENABLED", "false")
    importlib.reload(mic_presets)
    assert mic_presets.select_scene_preset("studio", True) is None


# ---------------------------------------------------------------------------
# resolve_scene_voice_settings — the block > scene > avatar precedence chain
# ---------------------------------------------------------------------------

def test_resolve_prefers_block_override_over_everything():
    """An explicit per-block mic_on wins even when the scene and avatar
    disagree with it."""
    mic_visible, chain_id = mic_presets.resolve_scene_voice_settings(
        block_mic_on=True,
        avatar_clip_mic_enabled=False,
        look_environment="outdoor",
        look_mic_visible=False,
    )
    assert mic_visible is True
    assert chain_id == "clip_mic_windscreen"


def test_resolve_falls_through_to_scene_default_when_no_block_override():
    """No block override → the scene's own mic_visible (set when the scene
    was created) decides, not the avatar-wide default."""
    mic_visible, chain_id = mic_presets.resolve_scene_voice_settings(
        block_mic_on=None,
        avatar_clip_mic_enabled=False,
        look_environment="room",
        look_mic_visible=True,
    )
    assert mic_visible is True
    assert chain_id == "clip_mic"


def test_resolve_falls_through_to_avatar_default_with_no_look():
    """No block override, no look at all (e.g. legacy block with no
    avatar_look_id) → avatar-wide default, studio environment."""
    mic_visible, chain_id = mic_presets.resolve_scene_voice_settings(
        block_mic_on=None,
        avatar_clip_mic_enabled=True,
        look_environment=None,
        look_mic_visible=None,
    )
    assert mic_visible is True
    assert chain_id == "clip_mic"


def test_resolve_block_override_false_beats_scene_true():
    mic_visible, chain_id = mic_presets.resolve_scene_voice_settings(
        block_mic_on=False,
        avatar_clip_mic_enabled=True,
        look_environment="studio",
        look_mic_visible=True,
    )
    assert mic_visible is False
    assert chain_id == "ambient_room"

"""Scene ambience-bed library — catalog + environment resolution.

The renderer picks a background-atmosphere bed per scene from its environment
(studio / room / outdoor); this locks that mapping, the master flag, and the
per-environment overrides. Pure unit tests — no DB, no network, no FFmpeg.
"""
import importlib
from unittest.mock import patch

import pytest

from services import ambience_library as amb


def _reload(env: dict):
    with patch.dict("os.environ", env, clear=False):
        importlib.reload(amb)
        return amb


@pytest.fixture(autouse=True)
def _clean_env():
    keys = [k for k in list(__import__("os").environ) if k.startswith("SCENE_AMBIENCE")]
    with patch.dict("os.environ", {k: "" for k in keys}, clear=False):
        importlib.reload(amb)
        yield
    importlib.reload(amb)


def test_disabled_by_default():
    assert amb.scene_ambience_enabled() is False
    assert amb.for_environment("outdoor") is None
    assert amb.for_environment("room") is None


def test_enabled_resolves_room_and_outdoor():
    m = _reload({"SCENE_AMBIENCE_ENABLED": "true"})
    assert m.scene_ambience_enabled() is True
    out = m.for_environment("outdoor")
    room = m.for_environment("room")
    assert out is not None and out.name == "wind_soft"
    assert room is not None and room.name == "room_tone"
    # a bed is quieter than a music bed (~0.15 linear)
    assert 0 < out.default_volume < 0.15
    assert 0 < room.default_volume < 0.15


@pytest.mark.parametrize("env", ["studio", "", None, "spaceship", "STUDIO"])
def test_no_bed_for_studio_or_unknown(env):
    m = _reload({"SCENE_AMBIENCE_ENABLED": "1"})
    assert m.for_environment(env) is None


def test_env_name_and_volume_overrides():
    m = _reload({
        "SCENE_AMBIENCE_ENABLED": "yes",
        "SCENE_AMBIENCE_OUTDOOR_NAME": "city_street",
        "SCENE_AMBIENCE_OUTDOOR_VOL": "0.2",
    })
    e = m.for_environment("outdoor")
    assert e.name == "city_street"
    assert e.default_volume == pytest.approx(0.2)


def test_bad_volume_override_falls_back_to_catalog_default():
    m = _reload({
        "SCENE_AMBIENCE_ENABLED": "on",
        "SCENE_AMBIENCE_ROOM_VOL": "not-a-number",
    })
    e = m.for_environment("room")
    assert e.name == "room_tone"
    assert e.default_volume == m.AMBIENCE_CATALOG["room_tone"].default_volume


def test_volume_override_clamped_0_1():
    m = _reload({"SCENE_AMBIENCE_ENABLED": "1", "SCENE_AMBIENCE_ROOM_VOL": "9"})
    assert m.for_environment("room").default_volume == 1.0
    m = _reload({"SCENE_AMBIENCE_ENABLED": "1", "SCENE_AMBIENCE_ROOM_VOL": "-3"})
    assert m.for_environment("room").default_volume == 0.0


def test_unknown_configured_name_yields_no_bed():
    m = _reload({
        "SCENE_AMBIENCE_ENABLED": "1",
        "SCENE_AMBIENCE_ROOM_NAME": "does_not_exist",
    })
    assert m.for_environment("room") is None


def test_catalog_urls_are_wav_on_media_cdn():
    for e in amb.list_entries():
        assert e.url.startswith("https://media.luminacast.com/ambience/")
        assert e.url.endswith(".wav")
        assert e.loop_s > 0


def test_lookup_is_case_insensitive_and_trims():
    assert amb.lookup("  WIND_SOFT ").name == "wind_soft"
    assert amb.lookup("nope") is None
    assert amb.lookup(None) is None

"""Round 7 — background-music volume must follow env truth, not a hard-coded default.

The user set ``MUSIC_DEFAULT_VOLUME=0.004375`` in their environment but the
rendered bed came out ~4x louder (≈0.0175 — the old hard-coded default),
because at least one ffmpeg path ignored the env var entirely.

``resolve_music_volume`` is now the single source of truth. Precedence:

    element_prop  >  cast_column  >  env_default  >  hardcoded

These tests pin that precedence and, critically, assert that NO codepath
silently falls back to the old hard-coded 0.0175 (or the legacy 0.3
compositor default) when an env / cast / element override is present.
"""
import pytest

from services import cast_ffmpeg_composer as comp

# The values the bug was about: the old hard-coded music default and the
# legacy video_compositor default. No resolution with an override should ever
# return either of these.
OLD_HARDCODED = 0.0175
LEGACY_COMPOSITOR_DEFAULT = 0.3

# The user's real env value from the brief.
USER_ENV_VOLUME = 0.004375


def _music_el(volume=None, meta_volume=None):
    el = {"id": "m1", "type": "audio", "props": {}, "metadata": {}}
    if volume is not None:
        el["props"]["volume"] = volume
    if meta_volume is not None:
        el["metadata"]["volume"] = meta_volume
    return el


# ── default fallback ────────────────────────────────────────────────────────


def test_default_is_bumped_to_0_0044():
    assert comp.DEFAULT_MUSIC_VOLUME == pytest.approx(0.0044)


def test_no_override_no_env_falls_back_to_hardcoded_default(monkeypatch):
    monkeypatch.delenv("MUSIC_DEFAULT_VOLUME", raising=False)
    vol, source = comp.resolve_music_volume([_music_el()], cast_volume_override=None)
    assert vol == pytest.approx(comp.DEFAULT_MUSIC_VOLUME)
    assert source == "hardcoded"
    # The bug value is the OLD default; the new default must not equal it.
    assert vol != pytest.approx(OLD_HARDCODED)


# ── env precedence ──────────────────────────────────────────────────────────


def test_env_default_used_when_no_element_or_cast_override(monkeypatch):
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", str(USER_ENV_VOLUME))
    vol, source = comp.resolve_music_volume([_music_el()], cast_volume_override=None)
    assert vol == pytest.approx(USER_ENV_VOLUME)
    assert source == "env_default"
    # The reported bug: env ignored, bed ~4x too loud at the hard-coded value.
    assert vol != pytest.approx(OLD_HARDCODED)
    assert vol != pytest.approx(LEGACY_COMPOSITOR_DEFAULT)


def test_env_invalid_falls_back_to_hardcoded(monkeypatch):
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", "not-a-number")
    vol, source = comp.resolve_music_volume([_music_el()], cast_volume_override=None)
    assert vol == pytest.approx(comp.DEFAULT_MUSIC_VOLUME)


# ── cast-column override ─────────────────────────────────────────────────────


def test_cast_column_override_beats_env(monkeypatch):
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", str(USER_ENV_VOLUME))
    vol, source = comp.resolve_music_volume([_music_el()], cast_volume_override=0.01)
    assert vol == pytest.approx(0.01)
    assert source == "cast_column"
    assert vol != pytest.approx(OLD_HARDCODED)


def test_cast_column_override_used_when_no_env(monkeypatch):
    monkeypatch.delenv("MUSIC_DEFAULT_VOLUME", raising=False)
    vol, source = comp.resolve_music_volume([], cast_volume_override=0.006)
    assert vol == pytest.approx(0.006)
    assert source == "cast_column"


# ── element-prop override (highest precedence) ──────────────────────────────


def test_element_prop_beats_cast_and_env(monkeypatch):
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", str(USER_ENV_VOLUME))
    vol, source = comp.resolve_music_volume(
        [_music_el(volume=0.02)], cast_volume_override=0.01
    )
    assert vol == pytest.approx(0.02)
    assert source == "element_prop"


def test_element_metadata_volume_also_honored(monkeypatch):
    monkeypatch.delenv("MUSIC_DEFAULT_VOLUME", raising=False)
    vol, source = comp.resolve_music_volume(
        [_music_el(meta_volume=0.03)], cast_volume_override=None
    )
    assert vol == pytest.approx(0.03)
    assert source == "element_prop"


def test_element_volume_clamped_to_unit_range(monkeypatch):
    vol, source = comp.resolve_music_volume([_music_el(volume=5.0)])
    assert vol == pytest.approx(1.0)
    assert source == "element_prop"
    vol, source = comp.resolve_music_volume([_music_el(volume=-2.0)])
    assert vol == pytest.approx(0.0)


# ── the core regression guarantee ───────────────────────────────────────────


def test_override_never_falls_back_to_buggy_hardcoded_value(monkeypatch):
    """With ANY override present, resolution must not yield the bug values."""
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", str(USER_ENV_VOLUME))

    # env override
    vol, _ = comp.resolve_music_volume([_music_el()])
    assert vol not in (pytest.approx(OLD_HARDCODED), pytest.approx(LEGACY_COMPOSITOR_DEFAULT))

    # cast override
    vol, _ = comp.resolve_music_volume([_music_el()], cast_volume_override=0.008)
    assert vol not in (pytest.approx(OLD_HARDCODED), pytest.approx(LEGACY_COMPOSITOR_DEFAULT))

    # element override
    vol, _ = comp.resolve_music_volume([_music_el(volume=0.009)], cast_volume_override=0.008)
    assert vol not in (pytest.approx(OLD_HARDCODED), pytest.approx(LEGACY_COMPOSITOR_DEFAULT))


def test_legacy_shim_matches_resolver(monkeypatch):
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", str(USER_ENV_VOLUME))
    vol_only = comp._resolve_music_volume([_music_el()], cast_volume_override=0.01)
    vol, _ = comp.resolve_music_volume([_music_el()], cast_volume_override=0.01)
    assert vol_only == pytest.approx(vol)


def test_music_default_volume_reads_env(monkeypatch):
    monkeypatch.setenv("MUSIC_DEFAULT_VOLUME", str(USER_ENV_VOLUME))
    assert comp.music_default_volume() == pytest.approx(USER_ENV_VOLUME)
    monkeypatch.delenv("MUSIC_DEFAULT_VOLUME", raising=False)
    assert comp.music_default_volume() == pytest.approx(comp.DEFAULT_MUSIC_VOLUME)

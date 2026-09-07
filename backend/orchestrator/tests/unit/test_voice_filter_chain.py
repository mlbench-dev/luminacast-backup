"""regr-7: voice filter-chain composition tests (no ffmpeg needed).

These assert the *string* produced by ``_voice_filter_chain`` — what
DSP nodes each mic preset selects — without running ffmpeg. The
duration-preservation behaviour is covered separately (and skips when
ffmpeg is absent) in ``test_post_process_voice_duration.py``.

Two guarantees matter here:

  * mic-ON (clip-mic / lavalier) is BYTE-IDENTICAL to the user-vetted
    production chain — regr-7 must not retune it.
  * mic-OFF selects the NEW ambient-room chain: gentle high-pass,
    presence dip, a light early-reflection (aecho), softer compression
    — clearly distinct from clip-mic — and pins duration when known.
"""
from __future__ import annotations

import importlib

import pytest

import services.media_processing as mp
from services.media_processing import _voice_filter_chain


# The exact clip-mic chain shipping in production at the time of regr-7.
# If this literal needs to change, the clip-mic EQ was retuned — which
# the regr-7 task forbids. Update only with explicit sign-off.
_PROD_CLIP_MIC_CHAIN = (
    "highpass=f=80,lowpass=f=14000,adeclick,afftdn=nr=10:nf=-25,"
    "equalizer=f=150:w=0.5:g=3,equalizer=f=6500:w=2:g=-3,"
    "equalizer=f=8000:w=1:g=-2,"
    "compand=attacks=0.01:decays=0.1:points=-80/-80|-45/-25|-20/-12|0/-5|20/-3:gain=4,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


def test_clip_mic_chain_is_byte_identical_to_production():
    """mic-ON must be untouched by regr-7 — proves no retune."""
    assert _voice_filter_chain(clip_mic_enabled=True) == _PROD_CLIP_MIC_CHAIN
    # Passing a duration must NOT alter the clip-mic chain (no trim added).
    assert (
        _voice_filter_chain(clip_mic_enabled=True, duration_s=4.0)
        == _PROD_CLIP_MIC_CHAIN
    )


def test_ambient_room_chain_has_distinct_nodes():
    """mic-OFF selects the new ambient/room chain with its hallmark nodes."""
    chain = _voice_filter_chain(clip_mic_enabled=False, duration_s=4.0)
    # Shared front-end de-noise prefix.
    assert "afftdn=nr=10:nf=-25" in chain
    # Gentle high-pass above the common 80 Hz floor.
    assert "highpass=f=90" in chain
    # Presence dip ~4 kHz (negative gain).
    assert "equalizer=f=4000:w=2:g=-2" in chain
    # Light room sense: a single short early reflection.
    assert "aecho=" in chain
    # Softer compression: slow attack, distinct from the clip-mic compand.
    assert "compand=attacks=0.05:decays=0.5" in chain
    # Same broadcast loudness target as the other chains.
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in chain


def test_ambient_room_is_not_clip_mic():
    """The two presets must produce visibly different DSP — mic-off must
    not accidentally fall back to the clip-mic chain."""
    on = _voice_filter_chain(clip_mic_enabled=True, duration_s=4.0)
    off = _voice_filter_chain(clip_mic_enabled=False, duration_s=4.0)
    assert on != off
    # Clip-mic hallmarks (proximity warmth boost + aggressive compand)
    # must NOT appear in the ambient-room chain.
    assert "equalizer=f=150:w=0.5:g=3" not in off
    assert "attacks=0.01" not in off
    # Ambient-room hallmark (early reflection) must NOT appear in clip-mic.
    assert "aecho=" not in on


def test_ambient_room_pins_duration_when_known():
    """When the input duration is known, the echo tail is trimmed back so
    the lipsync feed stays sample-aligned. The trim sits BEFORE loudnorm
    (a single-pass loudnorm drops trailing samples, so trimming after it
    would not restore the exact length)."""
    chain = _voice_filter_chain(clip_mic_enabled=False, duration_s=3.5)
    assert "atrim=end=3.5" in chain
    assert "asetpts=N/SR/TB" in chain
    # Trim must precede the loudnorm node.
    assert chain.index("atrim=end=3.5") < chain.index("loudnorm=")


def test_ambient_room_without_duration_omits_trim():
    """If duration is unknown the trim is omitted (best-effort) rather
    than guessing a wrong length."""
    chain = _voice_filter_chain(clip_mic_enabled=False, duration_s=None)
    assert "atrim=" not in chain
    assert "aecho=" in chain


def test_ambient_room_env_knobs_override(monkeypatch):
    """The MIC_OFF_* knobs must be read at call time so production can
    tune the room feel without a redeploy."""
    monkeypatch.setenv("MIC_OFF_HPF_HZ", "120")
    monkeypatch.setenv("MIC_OFF_PRESENCE_HZ", "3500")
    monkeypatch.setenv("MIC_OFF_PRESENCE_DB", "-3")
    monkeypatch.setenv("MIC_OFF_REVERB_MS", "70")
    monkeypatch.setenv("MIC_OFF_REVERB_DECAY", "0.2")
    monkeypatch.setenv("MIC_OFF_COMP_RATIO", "3")
    chain = _voice_filter_chain(clip_mic_enabled=False, duration_s=4.0)
    assert "highpass=f=120" in chain
    assert "equalizer=f=3500:w=2:g=-3" in chain
    assert "aecho=0.8:0.85:70:0.2" in chain
    # comp_ratio=3 ⇒ attack 0.025*3=0.075, decay 0.25*3=0.75.
    assert "compand=attacks=0.075:decays=0.75" in chain


def test_ambient_room_knobs_default_when_unset(monkeypatch):
    """Unset / blank knobs fall back to the module defaults."""
    for k in (
        "MIC_OFF_HPF_HZ",
        "MIC_OFF_PRESENCE_HZ",
        "MIC_OFF_PRESENCE_DB",
        "MIC_OFF_REVERB_MS",
        "MIC_OFF_REVERB_DECAY",
        "MIC_OFF_COMP_RATIO",
    ):
        monkeypatch.delenv(k, raising=False)
    chain = _voice_filter_chain(clip_mic_enabled=False, duration_s=4.0)
    assert "highpass=f=90" in chain
    assert "equalizer=f=4000:w=2:g=-2" in chain
    assert "aecho=0.8:0.85:55:0.18" in chain


# ── Scene-aware chains: studio / room / outdoor must sound different ──────────
#
# Before this, `select_scene_preset` routed room -> "ambient_room_soft" and
# outdoor -> "ambient_outdoor", but BOTH resolved to the exact studio DSP
# (outdoor + one extra high-pass), so the scene picker changed almost nothing
# about the voice. These pin that room and outdoor now carry distinct acoustics
# while studio / the generic default stays byte-identical.
from services.media_processing import _voice_filter_chain_for_scene


def _scene(chain_id, *, duration_s=4.0):
    return _voice_filter_chain_for_scene(
        chain_id, clip_mic_enabled=False, duration_s=duration_s
    )


def test_studio_scene_chain_is_unchanged_default():
    """The studio scene id resolves to the vetted default chain, untouched."""
    assert _scene("ambient_room") == _voice_filter_chain(
        clip_mic_enabled=False, duration_s=4.0
    )


def test_studio_room_outdoor_are_all_distinct():
    studio = _scene("ambient_room")
    room = _scene("ambient_room_soft")
    outdoor = _scene("ambient_outdoor")
    assert len({studio, room, outdoor}) == 3


def test_room_scene_has_untreated_room_acoustics():
    room = _scene("ambient_room_soft")
    # Low-mid "box" resonance of a boxy indoor space.
    assert "equalizer=f=250:w=1.2:g=2" in room
    # Denser early reflections than the single studio tap.
    assert "aecho=0.8:0.9:33|58:0.2|0.13" in room
    # Soft-furnishing HF absorption.
    assert "lowpass=f=7800" in room
    # Shared broadcast target + soft compand tail.
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in room
    assert "compand=attacks=0.05:decays=0.5" in room


def test_outdoor_scene_has_open_air_acoustics():
    outdoor = _scene("ambient_outdoor")
    # Room / proximity boom high-passed away.
    assert "highpass=f=130" in outdoor
    # Near-dry: a single faint, distant slap (no dense room reflections).
    assert "aecho=0.8:0.6:115:0.05" in outdoor
    # Distance / air absorption of the highs.
    assert "lowpass=f=7200" in outdoor
    # No indoor "box" resonance outdoors.
    assert "equalizer=f=250" not in outdoor


def test_scene_chains_pin_duration_before_loudnorm():
    """room/outdoor reflection taps run longer than studio's, so the trim
    that keeps the lipsync WAV sample-aligned must still sit before loudnorm."""
    for chain_id in ("ambient_room_soft", "ambient_outdoor"):
        chain = _scene(chain_id, duration_s=3.5)
        assert "atrim=end=3.5" in chain
        assert chain.index("atrim=end=3.5") < chain.index("loudnorm=")


def test_scene_chains_without_duration_omit_trim():
    for chain_id in ("ambient_room_soft", "ambient_outdoor"):
        chain = _scene(chain_id, duration_s=None)
        assert "atrim=" not in chain
        assert "aecho=" in chain


def test_scene_acoustic_env_override(monkeypatch):
    """SCENE_AC_<ENV>_<KNOB> overrides are read at call time."""
    monkeypatch.setenv("SCENE_AC_OUTDOOR_HPF_HZ", "160")
    monkeypatch.setenv("SCENE_AC_ROOM_LOWPASS_HZ", "6800")
    assert "highpass=f=160" in _scene("ambient_outdoor")
    assert "lowpass=f=6800" in _scene("ambient_room_soft")


def test_scene_chains_are_not_clip_mic():
    on = _voice_filter_chain(clip_mic_enabled=True, duration_s=4.0)
    for chain_id in ("ambient_room_soft", "ambient_outdoor"):
        off = _scene(chain_id)
        assert on != off
        assert "equalizer=f=150:w=0.5:g=3" not in off  # clip-mic proximity boost
        assert "attacks=0.01" not in off               # clip-mic aggressive compand

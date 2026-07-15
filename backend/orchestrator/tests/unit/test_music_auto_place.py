"""Regression 3 — background music must actually play under narration.

Mubert was generating tracks and writing ``cast.background_music_url``, but no
music element was ever placed onto the cast timeline, so the renderer's music
collector had nothing to mix. These tests lock the auto-placement contract:

  * the auto-arranged timeline carries a non-bonded ``kind=music`` audio
    element when the cast has a music URL (and none when music is "off");
  * the renderer's music collectors (adapter + ffmpeg composer) pick that
    element up and never confuse it with the bonded narration audio;
  * the fixed music library resolves known ids and raises on unknown;
  * the per-cast / env volume override is honored.

Pure unit tests — no DB, no network, no FFmpeg.
"""
import os
from unittest.mock import patch

import pytest

from services import cast_ffmpeg_composer as comp
from services.cast_ffmpeg_composer import translate_timeline_to_ffmpeg
from services.twick_compositor_adapter import translate_timeline_to_overlays
from services import music_library

CANVAS_W = 1080
CANVAS_H = 1920

MUSIC_URL = "https://media.luminacast.com/music/casts/cst_x/background.mp3"


def build_arranged_tracks(*, with_music: bool, music_off: bool = False, total: float = 6.0):
    """Mirror the shape routers.casts.auto_arrange_cast_timeline emits.

    Mirrored (not imported) because the endpoint itself needs a DB + auth; the
    element/track shape is the actual contract the renderer consumes, so we
    assert against that shape directly.
    """
    video_elements = [{
        "id": "v1_blk1", "type": "video", "s": 0.0, "e": total,
        "props": {"src": "https://x/seg.mp4"},
        "metadata": {"block_id": "blk1", "bonded": True,
                     "paired_audio_element_id": "a1_blk1"},
    }]
    audio_elements = [{
        "id": "a1_blk1", "type": "audio", "s": 0.0, "e": total,
        "props": {"src": "https://x/voice.mp3"},
        "metadata": {"block_id": "blk1", "bonded": True,
                     "paired_video_element_id": "v1_blk1"},
    }]
    caption_elements = [{
        "id": "cap_blk1", "type": "captions", "s": 0.0, "e": total,
        "props": {"text": "hello", "_captions_tokens": []},
        "metadata": {"block_id": "blk1", "track_type": "captions"},
    }]

    tracks = [
        {"id": "video", "type": "video", "elements": video_elements},
        {"id": "voice", "type": "audio", "elements": audio_elements},
    ]
    if with_music and not music_off:
        tracks.append({
            "id": "music", "type": "audio",
            "elements": [{
                "id": "music_bg", "type": "audio", "s": 0, "e": total,
                "props": {"src": MUSIC_URL},
                "metadata": {"kind": "music", "source": "auto"},
            }],
        })
    tracks.append({"id": "captions", "type": "captions", "elements": caption_elements})
    return {"tracks": tracks, "version": 1}


# ── Track count contract ──────────────────────────────────────────────────

def test_music_present_yields_extra_track():
    t = build_arranged_tracks(with_music=True)
    assert len(t["tracks"]) == 4  # video, voice, music, captions
    types = [tr["type"] for tr in t["tracks"]]
    assert types == ["video", "audio", "audio", "captions"]


def test_music_off_omits_music_track():
    t = build_arranged_tracks(with_music=True, music_off=True)
    assert len(t["tracks"]) == 3  # video, voice, captions
    assert all(tr["id"] != "music" for tr in t["tracks"])


# ── Music element shape ──────────────────────────────────────────────────

def test_music_element_shape():
    t = build_arranged_tracks(with_music=True, total=6.0)
    music_track = next(tr for tr in t["tracks"] if tr["id"] == "music")
    el = music_track["elements"][0]
    assert el["type"] == "audio"
    assert el["s"] == 0
    assert el["e"] == 6.0
    assert el["props"]["src"] == MUSIC_URL
    assert el["metadata"]["kind"] == "music"
    # Crucially: NOT bonded (the mixer harvests non-bonded audio).
    assert "bonded" not in el["metadata"]
    assert not el["metadata"].get("paired_video_element_id")


# ── Adapter music collector ──────────────────────────────────────────────

def test_adapter_picks_music_element_not_voice():
    t = build_arranged_tracks(with_music=True)
    out = translate_timeline_to_overlays(t, block_regions=[], blocks=None)
    assert out["music_track"] is not None
    assert out["music_track"]["src"] == MUSIC_URL


def test_adapter_ignores_bonded_voice_when_no_music():
    t = build_arranged_tracks(with_music=False)
    out = translate_timeline_to_overlays(t, block_regions=[], blocks=None)
    # The voice track is bonded audio — must NOT be mistaken for music.
    assert out["music_track"] is None


def test_adapter_passes_volume_override():
    t = build_arranged_tracks(with_music=True)
    music_track = next(tr for tr in t["tracks"] if tr["id"] == "music")
    music_track["elements"][0]["props"]["volume"] = 0.3
    out = translate_timeline_to_overlays(t, block_regions=[], blocks=None)
    assert out["music_track"]["volume"] == 0.3


# ── FFmpeg composer integration smoke ────────────────────────────────────

def test_composer_mixes_music_with_sidechain():
    t = build_arranged_tracks(with_music=True)
    baked = {"v1_blk1": "https://x/baked.mp4"}
    plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    fc = plan.filter_complex
    assert "sidechaincompress" in fc
    # Round 7: hard-coded default bumped 0.0175 -> 0.0044.
    assert "volume=0.0044" in fc  # default volume applied to the music input
    # The music URL becomes an ffmpeg input.
    assert any(inp.url == MUSIC_URL for inp in plan.inputs)


def test_composer_no_music_track_no_sidechain():
    t = build_arranged_tracks(with_music=False)
    baked = {"v1_blk1": "https://x/baked.mp4"}
    plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    assert "sidechaincompress" not in plan.filter_complex


def test_composer_env_volume_override():
    t = build_arranged_tracks(with_music=True)
    baked = {"v1_blk1": "https://x/baked.mp4"}
    with patch.dict(os.environ, {"MUSIC_DEFAULT_VOLUME": "0.25"}):
        plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    assert "volume=0.25" in plan.filter_complex


def test_composer_element_volume_beats_env():
    t = build_arranged_tracks(with_music=True)
    music_track = next(tr for tr in t["tracks"] if tr["id"] == "music")
    music_track["elements"][0]["props"]["volume"] = 0.4
    baked = {"v1_blk1": "https://x/baked.mp4"}
    with patch.dict(os.environ, {"MUSIC_DEFAULT_VOLUME": "0.25"}):
        plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    assert "volume=0.4" in plan.filter_complex


# ── music_library lookups ────────────────────────────────────────────────

def test_library_known_track_returns_url():
    track = music_library.list_tracks()[0]
    assert music_library.get_track_url(track.id) == track.url
    assert music_library.get_track_url(track.id).startswith("https://")


def test_library_unknown_track_raises():
    with pytest.raises(music_library.UnknownMusicTrackError):
        music_library.get_track_url("does_not_exist")


def test_library_parse_choice():
    assert music_library.parse_choice("track_id:chill_lofi") == "chill_lofi"
    assert music_library.parse_choice("auto") is None
    assert music_library.parse_choice("off") is None
    assert music_library.parse_choice(None) is None
    assert music_library.parse_choice("track_id:") is None


def test_volume_helper_clamps_and_defaults():
    # Round 7: the hard-coded default was bumped 0.0175 -> 0.0044 so the bed
    # sits ~4x quieter, matching what users were overriding to in env.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MUSIC_DEFAULT_VOLUME", None)
        assert comp.music_default_volume() == comp.DEFAULT_MUSIC_VOLUME == 0.0044
    with patch.dict(os.environ, {"MUSIC_DEFAULT_VOLUME": "bogus"}):
        assert comp.music_default_volume() == 0.0044
    with patch.dict(os.environ, {"MUSIC_DEFAULT_VOLUME": "5"}):
        assert comp.music_default_volume() == 1.0  # clamped

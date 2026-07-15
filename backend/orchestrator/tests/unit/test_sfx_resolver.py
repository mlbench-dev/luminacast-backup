"""Regression 4 — [sfx:NAME] markers must play real sounds at the right beats.

Markers were only ever *stripped* (from captions and TTS); they never resolved
to actual audio. These tests lock the resolver contract end to end:

  * ``extract_sfx_markers`` captures name + char offset + the spoken-word index
    each marker precedes, BEFORE stripping;
  * ``align_sfx_to_words`` anchors each marker to the nearest following word's
    start, with sane fallbacks (trailing marker, no-words even distribution);
  * the auto-arranged timeline carries an unbonded ``kind=sfx`` audio track
    when a block has resolved markers, and none when it doesn't;
  * an unknown SFX name is skipped (logged), never fatal;
  * the ffmpeg composer mixes SFX at full volume WITHOUT sidechain ducking and
    references the SFX file as an input.

Pure unit tests — no DB, no network, no FFmpeg.
"""
import os
from unittest.mock import patch

import pytest

from services import sfx_library
from services.cast_ffmpeg_composer import translate_timeline_to_ffmpeg
from utils.sfx_extraction import (
    SfxMarker,
    align_sfx_to_words,
    extract_sfx_markers,
)

CANVAS_W = 1080
CANVAS_H = 1920


# ── extract_sfx_markers ──────────────────────────────────────────────────

def test_extract_two_markers_with_char_offsets():
    text = "[sfx:record_scratch] Wait... [sfx:whoosh] Coffee shop"
    markers = extract_sfx_markers(text)
    assert [m.name for m in markers] == ["record_scratch", "whoosh"]
    # char offsets point at each marker's opening bracket.
    assert markers[0].char_offset == text.index("[sfx:record_scratch]")
    assert markers[1].char_offset == text.index("[sfx:whoosh]")
    # record_scratch leads the very first spoken word ("Wait...") -> index 0.
    assert markers[0].word_index == 0
    # whoosh follows "Wait..." -> precedes "Coffee" (index 1).
    assert markers[1].word_index == 1


def test_extract_is_case_insensitive():
    markers = extract_sfx_markers("Look [SFX:Sparkle] here")
    assert len(markers) == 1
    assert markers[0].name == "sparkle"


def test_extract_word_index_skips_other_direction_markers():
    # (excited) and [pause] are direction markers too — they must not count as
    # spoken words, so the sfx word_index aligns with the clean caption stream.
    text = "Oh (excited) wow [pause] [sfx:ding] amazing"
    markers = extract_sfx_markers(text)
    assert len(markers) == 1
    # spoken words before the marker: "Oh", "wow" -> ding precedes "amazing" = 2
    assert markers[0].word_index == 2


def test_extract_empty_and_none():
    assert extract_sfx_markers("") == []
    assert extract_sfx_markers(None) == []
    assert extract_sfx_markers("no markers here") == []


def test_extract_trailing_marker_word_index_past_end():
    text = "all done [sfx:applause]"
    markers = extract_sfx_markers(text)
    assert markers[0].word_index == 2  # two words, marker after both


# ── align_sfx_to_words ───────────────────────────────────────────────────

def _words():
    return [
        {"word": "Wait", "start": 0.0, "end": 0.5},
        {"word": "Coffee", "start": 1.0, "end": 1.4},
        {"word": "shop", "start": 1.4, "end": 1.9},
    ]


def test_align_uses_following_word_start():
    markers = [
        SfxMarker("record_scratch", 0, 0),
        SfxMarker("whoosh", 28, 1),
    ]
    out = align_sfx_to_words(markers, _words())
    assert out == [
        {"name": "record_scratch", "start_s": 0.0},
        {"name": "whoosh", "start_s": 1.0},
    ]


def test_align_trailing_marker_anchors_to_last_word_end():
    markers = [SfxMarker("applause", 0, 3)]  # index past the 3 words
    out = align_sfx_to_words(markers, _words())
    assert out == [{"name": "applause", "start_s": 1.9}]  # last word's end


def test_align_no_words_even_distribution():
    markers = [SfxMarker("whoosh", 0, 0), SfxMarker("ding", 5, 1)]
    out = align_sfx_to_words(markers, [], tts_duration_seconds=6.0)
    # even distribution across 6s for 2 markers -> step = 2.0
    assert out == [
        {"name": "whoosh", "start_s": 2.0},
        {"name": "ding", "start_s": 4.0},
    ]


def test_align_no_words_no_duration_defaults_zero():
    markers = [SfxMarker("whoosh", 0, 0)]
    out = align_sfx_to_words(markers, None)
    assert out == [{"name": "whoosh", "start_s": 0.0}]


def test_align_empty_markers():
    assert align_sfx_to_words([], _words()) == []


# ── sfx_library ──────────────────────────────────────────────────────────

def test_library_has_18_entries():
    assert len(sfx_library.SFX_CATALOG) == 18


def test_library_lookup_known():
    entry = sfx_library.lookup("record_scratch")
    assert entry is not None
    assert entry.name == "record_scratch"
    assert entry.url.startswith("https://")
    assert entry.duration_s == 0.6


def test_library_lookup_case_insensitive():
    assert sfx_library.lookup("WHOOSH") is sfx_library.lookup("whoosh")


def test_library_lookup_unknown_returns_none():
    assert sfx_library.lookup("does_not_exist") is None
    assert sfx_library.lookup(None) is None
    assert sfx_library.lookup("") is None


# ── auto_arrange timeline shape (mirrored, like the music test) ───────────

def build_arranged_tracks(*, sfx_timings: list[dict] | None, total: float = 6.0):
    """Mirror routers.casts.auto_arrange_cast_timeline's element shape.

    Mirrored (not imported) because the endpoint needs a DB + auth; the track
    shape is the actual contract the renderer consumes. Reproduces the SFX
    collection loop: each resolved timing becomes one full-volume audio element
    on a dedicated unbonded ``sfx`` track. Unknown names are skipped.
    """
    block_id = "blk1"
    start_s = 0.0
    video_elements = [{
        "id": f"v1_{block_id}", "type": "video", "s": 0.0, "e": total,
        "props": {"src": "https://x/seg.mp4"},
        "metadata": {"block_id": block_id, "bonded": True,
                     "paired_audio_element_id": f"a1_{block_id}"},
    }]
    audio_elements = [{
        "id": f"a1_{block_id}", "type": "audio", "s": 0.0, "e": total,
        "props": {"src": "https://x/voice.mp3"},
        "metadata": {"block_id": block_id, "bonded": True,
                     "paired_video_element_id": f"v1_{block_id}"},
    }]
    tracks = [
        {"id": "video", "type": "video", "elements": video_elements},
        {"id": "voice", "type": "audio", "elements": audio_elements},
    ]

    sfx_elements: list[dict] = []
    for ti, timing in enumerate(sfx_timings or []):
        entry = sfx_library.lookup(timing.get("name"))
        if entry is None:
            continue
        sfx_start = start_s + max(float(timing.get("start_s", 0) or 0), 0.0)
        sfx_elements.append({
            "id": f"sfx_{block_id}_{ti}",
            "type": "audio",
            "s": sfx_start,
            "e": sfx_start + entry.duration_s,
            "props": {"src": entry.url},
            "metadata": {"kind": "sfx", "name": entry.name, "volume": entry.default_volume},
        })
    if sfx_elements:
        tracks.append({"id": "sfx", "type": "audio", "elements": sfx_elements})

    return {"tracks": tracks, "version": 1}


def test_auto_arrange_emits_sfx_track_when_markers_resolved():
    t = build_arranged_tracks(sfx_timings=[{"name": "record_scratch", "start_s": 0.0}])
    sfx_track = next((tr for tr in t["tracks"] if tr["id"] == "sfx"), None)
    assert sfx_track is not None
    el = sfx_track["elements"][0]
    assert el["type"] == "audio"
    assert el["metadata"]["kind"] == "sfx"
    assert el["metadata"]["name"] == "record_scratch"
    assert el["s"] == 0.0
    assert el["e"] == pytest.approx(0.6)  # record_scratch duration
    assert el["props"]["src"].endswith("/sfx/record_scratch.wav")
    # Crucially NOT bonded.
    assert "bonded" not in el["metadata"]


def test_auto_arrange_no_sfx_track_when_absent():
    t = build_arranged_tracks(sfx_timings=None)
    assert all(tr["id"] != "sfx" for tr in t["tracks"])


def test_auto_arrange_skips_unknown_sfx_name():
    t = build_arranged_tracks(sfx_timings=[{"name": "totally_fake", "start_s": 1.0}])
    # the one unknown marker is skipped -> no sfx track emitted.
    assert all(tr["id"] != "sfx" for tr in t["tracks"])


# ── ffmpeg composer integration smoke ────────────────────────────────────

def _timeline_with_sfx_and_music(*, with_music: bool):
    total = 6.0
    tracks = [
        {"id": "video", "type": "video", "elements": [{
            "id": "v1_blk1", "type": "video", "s": 0.0, "e": total,
            "props": {"src": "https://x/seg.mp4"},
            "metadata": {"block_id": "blk1", "bonded": True,
                         "paired_audio_element_id": "a1_blk1"},
        }]},
        {"id": "voice", "type": "audio", "elements": [{
            "id": "a1_blk1", "type": "audio", "s": 0.0, "e": total,
            "props": {"src": "https://x/voice.mp3"},
            "metadata": {"block_id": "blk1", "bonded": True,
                         "paired_video_element_id": "v1_blk1"},
        }]},
    ]
    if with_music:
        tracks.append({"id": "music", "type": "audio", "elements": [{
            "id": "music_bg", "type": "audio", "s": 0, "e": total,
            "props": {"src": "https://media.luminacast.com/music/bg.mp3"},
            "metadata": {"kind": "music", "source": "auto"},
        }]})
    tracks.append({"id": "sfx", "type": "audio", "elements": [{
        "id": "sfx_blk1_0", "type": "audio", "s": 1.0, "e": 1.6,
        "props": {"src": "https://media.luminacast.com/sfx/record_scratch.wav"},
        "metadata": {"kind": "sfx", "name": "record_scratch", "volume": 1.0},
    }]})
    return {"tracks": tracks, "version": 1}


def test_composer_mixes_sfx_full_volume_no_ducking():
    t = _timeline_with_sfx_and_music(with_music=False)
    baked = {"v1_blk1": "https://x/baked.mp4"}
    plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    fc = plan.filter_complex
    # SFX file is an input.
    assert any(inp.url.endswith("/sfx/record_scratch.wav") for inp in plan.inputs)
    # Delayed to its 1.0s cue and mixed at full (1.0) volume.
    assert "adelay=1000|1000" in fc
    assert "volume=1.0" in fc
    # No music here -> no sidechain ducking at all.
    assert "sidechaincompress" not in fc
    # SFX mixed in without normalize (full punch).
    assert "normalize=0" in fc


def test_composer_sfx_alongside_ducked_music():
    t = _timeline_with_sfx_and_music(with_music=True)
    baked = {"v1_blk1": "https://x/baked.mp4"}
    plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    fc = plan.filter_complex
    # Music ducks; SFX does not (its own adelay + normalize=0 amix).
    assert "sidechaincompress" in fc
    assert "adelay=1000|1000" in fc
    assert "normalize=0" in fc
    # Both files present as inputs.
    assert any(inp.url.endswith("/sfx/record_scratch.wav") for inp in plan.inputs)
    assert any(inp.url.endswith("/music/bg.mp3") for inp in plan.inputs)


def test_composer_no_sfx_track_no_adelay():
    t = _timeline_with_sfx_and_music(with_music=False)
    # drop the sfx track
    t["tracks"] = [tr for tr in t["tracks"] if tr["id"] != "sfx"]
    baked = {"v1_blk1": "https://x/baked.mp4"}
    plan = translate_timeline_to_ffmpeg(t, baked, "rnd_test", CANVAS_W, CANVAS_H)
    assert "adelay=" not in plan.filter_complex

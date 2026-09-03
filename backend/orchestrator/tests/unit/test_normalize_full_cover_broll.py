"""``_normalize_full_cover_broll`` — a single auto-picked b-roll clip must not
swallow a whole ``avatar_speaking`` beat.

``auto_populate_stock_media`` attaches ONE ``parallel_media`` entry with
``start_offset_s=0, duration_s=None`` to avatar beats. The editor mapping
expands a lone null-duration clip to the FULL block (full canvas, opaque), so
the "avatar speaking" block renders with the avatar hidden for the entire beat.

  * beat shorter than a real cutaway  -> retype to ``avatar_voiceover`` + leave
    an ``auto_categorized`` marker for the editor.
  * beat long enough                  -> keep ``avatar_speaking`` but bound the
    clip to a mid-beat cutaway.
"""
from __future__ import annotations

from engine.cast_generator import _normalize_full_cover_broll


def _spk(dur, *, clip_duration=None, extra=None):
    block = {
        "category": "avatar_speaking",
        "estimated_duration_seconds": dur,
        "parallel_media": [{"url": "https://x/clip.mp4", "duration_s": clip_duration}],
    }
    if extra:
        block.update(extra)
    return block


def test_short_fully_covered_beat_is_retyped_to_voiceover():
    outline = [_spk(6)]
    _normalize_full_cover_broll(outline, "cst_test")
    blk = outline[0]
    assert blk["category"] == "avatar_voiceover"
    assert blk["render_mode"] == "voiceover"
    assert blk["auto_categorized"] == {
        "from": "avatar_speaking",
        "to": "avatar_voiceover",
        "reason": "broll_full_cover",
    }


def test_long_beat_keeps_avatar_and_bounds_the_cutaway():
    outline = [_spk(15)]
    _normalize_full_cover_broll(outline, "cst_test")
    blk = outline[0]
    assert blk["category"] == "avatar_speaking"
    assert "auto_categorized" not in blk
    entry = blk["parallel_media"][0]
    assert entry["start_offset_s"] == 1.5
    assert 2.0 <= entry["duration_s"] <= 4.0


def test_multi_angle_beat_is_left_alone():
    outline = [
        {
            "category": "avatar_speaking",
            "estimated_duration_seconds": 6,
            "multi_angle": True,
            "parallel_media": [{"url": "a"}, {"url": "b"}],
        }
    ]
    snapshot = repr(outline)
    _normalize_full_cover_broll(outline, "cst_test")
    assert repr(outline) == snapshot


def test_beat_without_estimated_duration_is_left_alone():
    """The re-fetch path (repopulate_stock_media) builds outline dicts with no
    beat length and its blocks already carry written scripts."""
    outline = [
        {
            "category": "avatar_speaking",
            "parallel_media": [{"url": "x", "duration_s": None}],
        }
    ]
    snapshot = repr(outline)
    _normalize_full_cover_broll(outline, "cst_test")
    assert repr(outline) == snapshot


def test_already_bounded_cutaway_is_left_alone():
    outline = [_spk(15, clip_duration=3.0)]
    snapshot = repr(outline)
    _normalize_full_cover_broll(outline, "cst_test")
    assert repr(outline) == snapshot


def test_non_speaking_categories_are_left_alone():
    for cat in ("avatar_voiceover", "pip_talking_head", "stock_video", "avatar_action"):
        outline = [
            {
                "category": cat,
                "estimated_duration_seconds": 6,
                "parallel_media": [{"url": "x", "duration_s": None}],
            }
        ]
        snapshot = repr(outline)
        _normalize_full_cover_broll(outline, "cst_test")
        assert repr(outline) == snapshot, cat


def test_two_unbounded_clips_covering_a_short_beat_are_retyped_to_voiceover():
    # Two null-duration clips tile the whole beat between them (the editor
    # mapping splits the beat evenly), so the avatar is hidden 100% — the
    # same full-cover failure as a single lone clip, just spread over two.
    outline = [_spk(6), _spk(6), {
        "category": "avatar_speaking",
        "estimated_duration_seconds": 6,
        "parallel_media": [{"url": "a"}, {"url": "b"}],
    }, _spk(6)]
    _normalize_full_cover_broll(outline, "cst_test")
    blk = outline[2]  # interior beat, not a bookend
    assert blk["category"] == "avatar_voiceover"
    assert blk["auto_categorized"]["reason"] == "broll_full_cover"


def test_two_unbounded_clips_on_a_long_beat_become_back_to_back_cutaways():
    outline = [_spk(6), {
        "category": "avatar_speaking",
        "estimated_duration_seconds": 20,
        "parallel_media": [{"url": "a"}, {"url": "b"}],
    }, _spk(6)]
    _normalize_full_cover_broll(outline, "cst_test")
    blk = outline[1]
    assert blk["category"] == "avatar_speaking"
    a, b = blk["parallel_media"]
    # Combined cutaway budget (min(4, max(2, 0.4*beat)) = 4) split evenly.
    assert a["start_offset_s"] == 1.5 and a["duration_s"] == 2.0
    assert b["start_offset_s"] == 3.5 and b["duration_s"] == 2.0
    # Avatar visible for the rest of the beat.
    assert a["duration_s"] + b["duration_s"] < 20


def test_two_explicit_clips_that_tile_the_whole_beat_are_normalized():
    outline = [_spk(6), {
        "category": "avatar_speaking",
        "estimated_duration_seconds": 12,
        "parallel_media": [
            {"url": "a", "duration_s": 6.0},
            {"url": "b", "duration_s": 6.0},
        ],
    }, _spk(6)]
    _normalize_full_cover_broll(outline, "cst_test")
    blk = outline[1]
    total = sum(c["duration_s"] for c in blk["parallel_media"])
    assert total <= 4.0  # bounded, no longer covering the full 12s beat


def test_two_explicit_clips_that_are_only_a_partial_cutaway_are_left_alone():
    outline = [_spk(6), {
        "category": "avatar_speaking",
        "estimated_duration_seconds": 20,
        "parallel_media": [
            {"url": "a", "duration_s": 2.0, "start_offset_s": 3.0},
            {"url": "b", "duration_s": 2.0, "start_offset_s": 8.0},
        ],
    }, _spk(6)]
    snapshot = repr(outline)
    _normalize_full_cover_broll(outline, "cst_test")
    assert repr(outline) == snapshot


def test_last_beat_cta_drops_full_cover_broll_and_stays_on_camera():
    """The CTA is always the last avatar_speaking beat — the avatar must be
    visible making the ask, so a full-cover clip is dropped outright rather
    than retyped/cutaway (both still hide the face)."""
    outline = [_spk(6), _spk(6), _spk(6, extra={"stock_media_url": "https://x/c.mp4"})]
    _normalize_full_cover_broll(outline, "cst_test")
    last = outline[-1]
    assert last["category"] == "avatar_speaking"
    assert "auto_categorized" not in last
    assert last["parallel_media"] == []
    assert last.get("stock_media_url") is None


def test_first_beat_hook_drops_full_cover_broll_and_stays_on_camera():
    outline = [_spk(6), _spk(6), _spk(6)]
    _normalize_full_cover_broll(outline, "cst_test")
    first = outline[0]
    assert first["category"] == "avatar_speaking"
    assert "auto_categorized" not in first
    assert first["parallel_media"] == []


def test_middle_beat_in_multi_block_outline_still_normalizes():
    """Bookend protection must not disable the retype/cutaway pass for the
    interior beats."""
    outline = [_spk(6), _spk(6), _spk(15)]
    _normalize_full_cover_broll(outline, "cst_test")
    mid = outline[1]
    assert mid["category"] == "avatar_voiceover"
    assert mid["auto_categorized"]["reason"] == "broll_full_cover"
    tail = outline[2]
    assert tail["category"] == "avatar_speaking"
    assert tail["parallel_media"] == []

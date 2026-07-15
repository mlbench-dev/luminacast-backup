"""Round-6 Bug B follow-up — deterministic framing-variety enforcement.

The LLM is unreliable about "do NOT reuse the same framing for two consecutive
avatar blocks", so ``_enforce_framing_variety`` rotates any consecutive avatar
block that repeats its predecessor's framing through a fixed cycle. These tests
pin that guarantee independent of LLM compliance.
"""
from engine import cast_generator as cg


def test_three_consecutive_medium_blocks_get_distinct_framings():
    scenes = [
        {"category": "avatar_speaking", "framing": "MEDIUM"},
        {"category": "avatar_speaking", "framing": "MEDIUM"},
        {"category": "avatar_speaking", "framing": "MEDIUM"},
    ]
    out = cg._enforce_framing_variety(scenes)
    framings = [s["framing"] for s in out]
    # GUARANTEE: at least 3 distinct framings across the 3 avatar blocks.
    assert len(set(framings)) >= 3, framings
    # And no two CONSECUTIVE avatar blocks share a framing.
    for a, b in zip(framings, framings[1:]):
        assert a != b, framings


def test_no_rotation_when_already_varied():
    scenes = [
        {"category": "avatar_speaking", "framing": "MEDIUM"},
        {"category": "avatar_speaking", "framing": "CLOSE"},
        {"category": "avatar_speaking", "framing": "WIDE"},
    ]
    cg._enforce_framing_variety(scenes)
    rotated = cg._count_framing_rotations(scenes)
    assert rotated == 0
    assert [s["framing"] for s in scenes] == ["MEDIUM", "CLOSE", "WIDE"]


def test_rotation_count_is_reported():
    scenes = [
        {"category": "avatar_speaking", "framing": "MEDIUM"},
        {"category": "avatar_speaking", "framing": "MEDIUM"},
        {"category": "avatar_speaking", "framing": "MEDIUM"},
    ]
    cg._enforce_framing_variety(scenes)
    # Two of the three blocks had to be rotated off MEDIUM.
    assert cg._count_framing_rotations(scenes) == 2
    # Flags are cleared so they don't leak into the persisted scene.
    assert all("_framing_rotated" not in s for s in scenes)


def test_non_avatar_blocks_do_not_break_the_streak():
    # A stock/voiceover block between two avatar blocks does NOT reset the
    # consecutive-avatar comparison — the two avatar blocks are still adjacent
    # from the viewer's perspective and must differ.
    scenes = [
        {"category": "avatar_speaking", "framing": "MEDIUM"},
        {"category": "stock_video", "framing": "MEDIUM"},  # ignored
        {"category": "avatar_speaking", "framing": "MEDIUM"},
    ]
    cg._enforce_framing_variety(scenes)
    avatar_framings = [
        s["framing"] for s in scenes if cg._scene_is_avatar_onscreen(s)
    ]
    assert avatar_framings[0] != avatar_framings[1], avatar_framings


def test_invalid_framings_are_normalised_then_varied():
    scenes = [
        {"category": "avatar_speaking", "framing": "bogus"},  # -> MEDIUM
        {"category": "avatar_speaking", "framing": "bogus"},  # -> MEDIUM, then rotated
    ]
    cg._enforce_framing_variety(scenes)
    framings = [s["framing"] for s in scenes]
    assert all(f in cg._VALID_FRAMINGS for f in framings), framings
    assert framings[0] != framings[1], framings


def test_rotated_framings_are_all_valid():
    scenes = [{"category": "avatar_speaking", "framing": "MEDIUM"} for _ in range(8)]
    cg._enforce_framing_variety(scenes)
    for s in scenes:
        assert s["framing"] in cg._VALID_FRAMINGS

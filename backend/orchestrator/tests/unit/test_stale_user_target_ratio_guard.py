"""Regression test for the stale user_target guard in tasks/cast_render.py.

Bug history: a block's per-block "user_target" duration is read from whatever
[s, e] window is saved in the Arrange-phase timeline for it — but that window
can be a leftover snapshot from before the block's TTS settled at its current
duration (e.g. saved when the tab was first opened, or before a later TTS
refresh/regeneration), never resynced afterward.

Round 1 (block blk_17f7c35fac86, render rnd_4fa8aedf2b18): saved slot 0.1s
against tts=4.7s. Caught by an absolute floor (_VALIDATE_MIN_DURATION_S,
0.5s).

Round 2 (block blk_88f2a4cd3183, render rnd_25a6a0c2b241): saved slot 1.267s
against a *refreshed* tts=4.65s (27% of it) — comfortably above the 0.5s
absolute floor, so it sailed through as a "real edit," got head-trimmed to
1.267s, came out 89% frozen, and the whole render failed Phase 3 validation
with block_statuses showing "1/9 blocks failed" and NO fallback (this path
predates the voiceover-branch's avatar-idle fallback; speaking blocks have no
equivalent safety net today).

Fix: the guard is now `max(absolute_floor, tts_duration * RATIO)`, catching
both a tiny absolute leftover AND a small-fraction-of-a-refreshed-tts leftover,
while still allowing a genuine moderate trim (kept above the ratio) through
unaffected.
"""
from __future__ import annotations

import re
from pathlib import Path

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_CAST_RENDER_PATH = _ORCH_ROOT / "tasks" / "cast_render.py"

_STALE_USER_TARGET_RATIO = 0.35
_STRUCTURAL_FLOOR_S = 0.5  # services.media_processing._VALIDATE_MIN_DURATION_S


def _is_implausible(user_target_s: float, tts_duration_s: float) -> bool:
    """Mirrors the exact decision in tasks/cast_render.py's dispatch loop."""
    threshold = max(_STRUCTURAL_FLOOR_S, tts_duration_s * _STALE_USER_TARGET_RATIO)
    return user_target_s < threshold


def test_round_1_stale_slot_caught():
    """blk_17f7c35fac86: 0.1s saved slot vs 4.7s tts — must be rejected."""
    assert _is_implausible(0.1, 4.7)


def test_round_2_stale_slot_caught_by_ratio_not_floor():
    """blk_88f2a4cd3183: 1.267s saved slot vs 4.65s tts (27%) — above the old
    0.5s absolute floor, but must still be rejected by the ratio component."""
    assert 1.267 > _STRUCTURAL_FLOOR_S, "sanity: this case is above the old floor alone"
    assert _is_implausible(1.267, 4.65)


def test_moderate_genuine_trim_not_flagged():
    """A deliberate, moderate trim (well above the ratio) must survive —
    the guard should not block legitimate pacing edits."""
    assert not _is_implausible(3.0, 4.65)  # 65% of tts — plausible trim
    assert not _is_implausible(4.0, 4.0)   # untrimmed, exact match


def test_short_tts_still_protected_by_absolute_floor():
    """For a very short line, 35% of tts could fall below the structural
    floor — the max() must keep the absolute floor as a hard minimum."""
    # tts=1.0s -> ratio component = 0.35s, floor wins at 0.5s
    assert _is_implausible(0.4, 1.0)
    assert not _is_implausible(0.6, 1.0)


def test_cast_render_source_combines_floor_and_ratio_via_max():
    """Static-code assertion: the shipped guard must be max(structural floor,
    tts * ratio), not the absolute floor alone (which is what let round 2
    through in production)."""
    src = _CAST_RENDER_PATH.read_text(encoding="utf-8")
    assert "_STALE_USER_TARGET_RATIO" in src
    assert re.search(
        r"_stale_threshold_s\s*=\s*max\(\s*_clip_min_duration_s,",
        src,
    ), "guard must be max(structural_floor, tts_duration * ratio)"

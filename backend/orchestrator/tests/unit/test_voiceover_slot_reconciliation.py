"""Regression test: a voiceover block's compose-time audio slot must
reconcile to its real audio-driven duration even when its bake FAILS.

Bug chain (render rnd_6bec42a04ba9), found from a user-added debug log
([audio_mix_debug]) that proved the audio mix itself was NOT overlapping —
each track was correctly non-overlapping per its OWN declared slot. The real
defect was upstream: blk_d1f2dd615ff3's bake correctly targeted 7.078s
(audio-driven — see the voiceover branch's `broll_s = audio_dur`), but both
its B-roll and avatar-idle-fallback attempts timed out, so it produced no
baked clip. `_measure_baked_block_durations` can only measure blocks that
DID produce a baked clip to probe, so this block's Arrange-timeline slot
(a stale 4.0s, vs. its real 6.48s narration) was never corrected. Compose
then hard-trimmed the CORRECT, freshly-fetched narration audio down to that
stale 4.0s window — chopping the sentence off mid-way just as the next
block's (correctly-placed, non-overlapping) audio began. To a listener that
reads as "two voices talking over each other," even though the mix itself
never actually overlapped two tracks.

Fix: the voiceover branch registers its real audio-driven duration
(`voiceover_real_durations[block_id] = broll_s`) unconditionally — bake
success or failure — and that dict is merged into `measured_durations`
(gap-fill only; an actual measurement still wins) before the post-bake
`_apply_real_block_durations` call, so a voiceover block's slot always
reconciles to its real length.
"""
from __future__ import annotations

from pathlib import Path

from tasks.cast_render import _apply_real_block_durations

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_CAST_RENDER_PATH = _ORCH_ROOT / "tasks" / "cast_render.py"


def _timeline_for_two_adjacent_blocks() -> dict:
    """Two voiceover blocks back-to-back: block A's Arrange slot (4.0s) is
    stale — its real narration is 6.48s. Block B starts right where A's
    stale slot ends (10.367+4.0=14.367), matching the real production bug."""
    return {
        "tracks": [{
            "type": "video",
            "elements": [
                {
                    "id": "v1_blkA", "type": "image", "s": 10.367, "e": 14.367,
                    "metadata": {"bonded": True, "block_id": "blkA", "track_type": "video_face"},
                },
                {
                    "id": "a1_blkA", "type": "audio", "s": 10.367, "e": 14.367,
                    "metadata": {"bonded": True, "block_id": "blkA", "track_type": "audio_voice"},
                },
                {
                    "id": "v1_blkB", "type": "image", "s": 14.367, "e": 20.600,
                    "metadata": {"bonded": True, "block_id": "blkB", "track_type": "video_face"},
                },
                {
                    "id": "a1_blkB", "type": "audio", "s": 14.367, "e": 20.600,
                    "metadata": {"bonded": True, "block_id": "blkB", "track_type": "audio_voice"},
                },
            ],
        }],
    }


def test_voiceover_real_duration_expands_stale_slot_without_bake_measurement():
    """Simulates the exact bug: blkA has no bake measurement (its bake
    failed) but DOES have a registered real audio-driven duration (6.48s).
    Merging it in — the fix's whole point — must expand blkA's window and
    push blkB later, eliminating the overlap-causing truncation."""
    timeline = _timeline_for_two_adjacent_blocks()

    # No bake measurement for blkA (bake failed) — this is what
    # _measure_baked_block_durations alone would produce.
    measured_durations: dict[str, float] = {}
    # What the fix adds: the real audio-driven duration, registered
    # regardless of bake success.
    voiceover_real_durations = {"blkA": 6.48}

    combined = {**voiceover_real_durations, **measured_durations}
    new_timeline, rewritten, _ = _apply_real_block_durations(
        timeline, block_durations=combined, render_id="rnd_test", fps=30,
    )

    assert rewritten, "blkA's stale 4.0s slot must be corrected to its real 6.48s"

    windows = {}
    for el in new_timeline["tracks"][0]["elements"]:
        bid = el["metadata"]["block_id"]
        if el["metadata"].get("track_type") == "audio_voice":
            windows[bid] = (el["s"], el["e"])

    a_s, a_e = windows["blkA"]
    b_s, b_e = windows["blkB"]
    assert abs((a_e - a_s) - 6.48) < 0.05, f"blkA window should be ~6.48s, got {a_e - a_s:.3f}"
    assert b_s >= a_e - 1e-6, (
        f"blkB must start at/after blkA's new (longer) end — got blkA end="
        f"{a_e:.3f}, blkB start={b_s:.3f} — an overlap here is exactly the "
        f"reported bug"
    )


def test_measured_duration_still_wins_over_voiceover_estimate():
    """If a bake DID succeed and get measured, that measurement (more
    precise — it reflects the actual encoded clip) must win over the
    pre-bake broll_s estimate, not the other way around."""
    timeline = _timeline_for_two_adjacent_blocks()
    voiceover_real_durations = {"blkA": 6.48}
    measured_durations = {"blkA": 6.60}  # actual encoded clip came out slightly longer

    combined = {**voiceover_real_durations, **measured_durations}
    assert combined["blkA"] == 6.60


def test_voiceover_branch_registers_real_duration_unconditionally():
    """Static-code check: the registration must happen regardless of bake
    success/failure — i.e. before the resolve/render/fallback attempts, not
    gated behind a successful video_bytes result."""
    src = _CAST_RENDER_PATH.read_text(encoding="utf-8")
    start = src.find('if block_render_mode == "voiceover":')
    assert start != -1
    end = src.find("# ── avatar_motion (T2V)", start)
    assert end != -1
    branch = src[start:end]

    assert "voiceover_real_durations[block_id] = broll_s" in branch
    # It must be registered before the FIRST bake attempt — originally a
    # single "if src is not None:" resolve/render step; the branch has
    # since grown a multi-shot sequence attempt (_bake_voiceover_broll_sequence)
    # tried before the priority-ordered candidates loop, so that's now the
    # earliest bake attempt to check against.
    reg_pos = branch.find("voiceover_real_durations[block_id] = broll_s")
    resolve_pos = branch.find("_bake_voiceover_broll_sequence(")
    assert 0 <= reg_pos < resolve_pos, (
        "duration must be registered before the bake attempt, so it's "
        "recorded even if baking subsequently fails"
    )


def test_post_bake_reconciliation_merges_voiceover_durations():
    """Static-code check: the post-bake _apply_real_block_durations call
    must be fed the merge of voiceover_real_durations and
    measured_durations, not measured_durations alone."""
    src = _CAST_RENDER_PATH.read_text(encoding="utf-8")
    assert "combined_durations = {**voiceover_real_durations, **measured_durations}" in src
    assert "block_durations=combined_durations" in src

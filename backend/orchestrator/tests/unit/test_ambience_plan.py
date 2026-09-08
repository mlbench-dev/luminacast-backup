"""Scene ambience plan — timeline span extraction + same-env run coalescing.

``_build_ambience_plan`` turns a timeline snapshot + per-block scene environment
into looped atmosphere-bed segments for the audio remux. The DB lookup in the
middle needs a session, but the two pure halves — pulling block spans off the
timeline and merging consecutive same-environment blocks into runs — carry the
timing logic and are locked down here. Pure unit tests: no DB, no FFmpeg.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from tasks.cast_render import (
    _timeline_block_spans,
    _coalesce_env_runs,
    _build_ambience_plan,
)


def _tl(*blocks):
    """blocks: (block_id, s, e[, track_type]) -> a minimal timeline snapshot."""
    els = []
    for b in blocks:
        bid, s, e = b[0], b[1], b[2]
        els.append({
            "id": f"v1_{bid}", "type": "video", "s": s, "e": e,
            "metadata": {"block_id": bid, "bonded": True},
        })
    return {"tracks": [{"type": "video", "elements": els}]}


def test_block_spans_merge_multiple_elements_per_block():
    tl = {
        "tracks": [
            {"type": "video", "elements": [
                {"id": "v1_a", "type": "video", "s": 0.0, "e": 4.0,
                 "metadata": {"block_id": "a"}},
            ]},
            {"type": "audio", "elements": [
                {"id": "a1_a", "type": "audio", "s": 0.2, "e": 5.5,
                 "metadata": {"block_id": "a"}},
                {"id": "sfx", "type": "audio", "s": 1.0, "e": 1.5,
                 "metadata": {}},  # no block_id -> ignored
            ]},
        ]
    }
    spans = _timeline_block_spans(tl)
    assert spans == {"a": (0.0, 5.5)}


def test_block_spans_skip_zero_and_negative_slots():
    tl = _tl(("a", 3.0, 3.0), ("b", 5.0, 2.0), ("c", 0.0, 4.0))
    assert set(_timeline_block_spans(tl)) == {"c"}


def test_coalesce_merges_consecutive_same_env_across_small_gap():
    spans = {"a": (0.0, 4.0), "b": (4.3, 9.0), "c": (9.0, 12.0)}
    env = {"a": "outdoor", "b": "outdoor", "c": "outdoor"}
    runs = _coalesce_env_runs(spans, env)
    assert runs == [("outdoor", 0.0, 12.0)]


def test_coalesce_breaks_on_env_change_and_on_large_gap():
    spans = {"a": (0.0, 4.0), "b": (4.2, 8.0), "c": (20.0, 25.0)}
    env = {"a": "outdoor", "b": "room", "c": "room"}
    runs = _coalesce_env_runs(spans, env)
    # a: outdoor run; b: room run; c: separate room run (gap 12s > bridge)
    assert runs == [
        ("outdoor", 0.0, 4.0),
        ("room", 4.2, 8.0),
        ("room", 20.0, 25.0),
    ]


def test_coalesce_drops_runs_below_min_length():
    spans = {"a": (0.0, 1.0), "b": (1.2, 1.8)}
    env = {"a": "outdoor", "b": "outdoor"}
    # merged run is 0.0-1.8 = 1.8s < 2.0s floor
    assert _coalesce_env_runs(spans, env) == []


def test_coalesce_orders_by_start_time():
    spans = {"late": (10.0, 15.0), "early": (0.0, 5.0)}
    env = {"late": "room", "early": "room"}
    # gap 5s > 1.5s bridge -> two runs, early first
    assert _coalesce_env_runs(spans, env) == [
        ("room", 0.0, 5.0),
        ("room", 10.0, 15.0),
    ]


def test_missing_env_becomes_empty_string_not_a_bed():
    spans = {"a": (0.0, 6.0)}
    runs = _coalesce_env_runs(spans, {})
    assert runs == [("", 0.0, 6.0)]


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_build_plan_returns_empty_when_flag_disabled():
    with patch("services.ambience_library.scene_ambience_enabled", return_value=False):
        out = _run(_build_ambience_plan(
            timeline=_tl(("a", 0.0, 8.0)), cast_id="cst_x",
            factory=None, render_id="rnd_x",
        ))
    assert out == []


def test_build_plan_empty_timeline_short_circuits_before_db():
    # factory=None would blow up if the DB path were reached.
    with patch("services.ambience_library.scene_ambience_enabled", return_value=True):
        out = _run(_build_ambience_plan(
            timeline={"tracks": []}, cast_id="cst_x",
            factory=None, render_id="rnd_x",
        ))
    assert out == []

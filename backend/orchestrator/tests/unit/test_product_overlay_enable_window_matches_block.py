"""Round-6 Bug C — the product overlay enable window must track the block's
own start/end, never the whole cast duration.

The overlay's ``enable=between(t,start,end)`` is built from each overlay
element's per-block ``start_s`` / ``end_s`` (sourced from the timeline
element's ``s`` / ``e`` in cast_render.py), so the product card disappears when
its block ends instead of lingering for the entire video.

The multi-overlay compositor needs a real ffmpeg + downloaded PNGs to run, so
we assert against the source of the exact ``enable`` clause (mirroring the
established source-assertion pattern used elsewhere in the suite).
"""
from __future__ import annotations

from pathlib import Path

ORCH_ROOT = Path(__file__).resolve().parents[2]
COMPOSITOR_PATH = ORCH_ROOT / "services" / "video_compositor.py"
CAST_RENDER_PATH = ORCH_ROOT / "tasks" / "cast_render.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_enable_window_uses_per_overlay_start_end():
    src = _read(COMPOSITOR_PATH)
    # The enable clause must be derived from the overlay op's own bounds.
    assert "between(t,{op['start_s']},{op['end_s']})" in src
    # And it must NOT be derived from a cast-wide duration.
    assert "between(t,0,{duration" not in src
    assert "between(t,0,{cast" not in src


def test_overlay_end_sourced_from_block_element_not_cast_duration():
    src = _read(CAST_RENDER_PATH)
    # start_s / end_s for the product overlay come from the block element's
    # s / e fields, not the cast duration.
    assert 'end_s = float(el.get("e") or 0)' in src
    assert 'start_s = float(el.get("s") or 0)' in src

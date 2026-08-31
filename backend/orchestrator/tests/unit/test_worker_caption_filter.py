"""Regression coverage for burnt-in caption escaping in worker_ffmpeg_compose.

Bug: the in-process composer (``worker_ffmpeg_compose._run_ffmpeg_compose``,
Path 2 in tasks/cast_render.py) inlined each caption as ``text='<words>'`` in
the ``-filter_complex`` string, with shell-style ``'\\''`` apostrophe escaping
and wrapped lines joined by a real newline. On the production FFmpeg build a
wrapped caption containing an apostrophe ("shouldn't", "it's") desynced the
filtergraph quote parser, so the ``text=`` value swallowed the filter's own
trailing options and drawtext burned
``:fontsize=42:...:enable=between(t,4.7333333333333333,10.8)`` into the frame
as literal text — confirmed live on cst_26b60791fd7c.

Fix: the caption body now goes to a ``tmpdir/caption_<n>.txt`` sidecar and is
referenced with ``textfile=`` + ``expansion=none``. No caption character ever
reaches the filtergraph string, so nothing can desync the parser.

These tests inspect the constructed filtergraph directly via the extracted
``_build_caption_filter_parts`` helper — no real FFmpeg invocation.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

_ORCH_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORCH_ROOT))

import worker_ffmpeg_compose as wfc  # noqa: E402

# The offending caption from cst_26b60791fd7c, block 2 — apostrophe + commas +
# long enough to wrap onto multiple lines.
OFFENDING = (
    "The Hanes EcoSmart Fleece is made with recycled fibers so it's soft, "
    "warm, and actually built to last."
)


def _caption(text=OFFENDING, start=4.75, end=10.8, **extra):
    ov = {"type": "caption", "text": text, "start_s": start, "end_s": end}
    ov.update(extra)
    return ov


def test_caption_uses_textfile_not_inlined_text(tmp_path):
    parts, label, drawn = wfc._build_caption_filter_parts(
        [_caption()], "[0:v]", canvas_w=480, tmpdir=str(tmp_path)
    )
    assert drawn == 1
    assert label == "[cap0]"
    graph = ";".join(parts)

    # The caption body is referenced by file, never inlined.
    assert "textfile='" in graph
    assert "expansion=none" in graph
    assert "text='" not in graph

    # No fragment of the caption prose leaks into the filtergraph string —
    # this is what used to get swallowed and burned as literal text.
    for fragment in ("built to last", "recycled fibers", "it's soft", "shouldn"):
        assert fragment not in graph

    # The sidecar file exists and holds the wrapped caption (newline-joined),
    # with the apostrophe and commas intact and unescaped.
    txt = (tmp_path / "caption_0.txt").read_text(encoding="utf-8")
    assert "it's soft" in txt
    assert "'\\''" not in txt  # no shell-style escaping in the file
    assert "\\," not in txt  # no filtergraph comma-escaping in the file
    # Word-wrapped onto >1 line, but every word preserved and in order.
    assert "\n" in txt
    assert txt.split() == OFFENDING.split()


def test_caption_filter_options_survive_after_textfile(tmp_path):
    parts, _label, _drawn = wfc._build_caption_filter_parts(
        [_caption()], "[0:v]", canvas_w=480, tmpdir=str(tmp_path)
    )
    part = parts[0]
    # Style / timing options still present and outside any quoted text value.
    assert ":fontsize=" in part
    assert ":fix_bounds=1:" in part
    assert "x=(w-text_w)/2:y=h-text_h-40:" in part
    assert "enable='between(t,4.75,10.8)'" in part


def test_multiple_captions_chain_in_start_order(tmp_path):
    caps = [
        _caption(text="Second one, later.", start=5.0, end=9.0),
        _caption(text="First up — it's early!", start=0.0, end=4.0),
    ]
    parts, label, drawn = wfc._build_caption_filter_parts(
        caps, "[0:v]", canvas_w=480, tmpdir=str(tmp_path)
    )
    assert drawn == 2
    assert label == "[cap1]"
    # Sorted by start_s: cap0 chains off [0:v], cap1 chains off [cap0].
    assert parts[0].startswith("[0:v]drawtext=")
    assert parts[1].startswith("[cap0]drawtext=")
    assert (tmp_path / "caption_0.txt").read_text(encoding="utf-8").startswith("First up")
    assert (tmp_path / "caption_1.txt").read_text(encoding="utf-8").startswith("Second one")


def test_blank_and_zero_length_captions_are_skipped(tmp_path):
    caps = [
        _caption(text="   ", start=0.0, end=3.0),
        _caption(text="valid", start=3.0, end=3.0),  # end <= start
        _caption(text="kept", start=4.0, end=6.0),
    ]
    parts, _label, drawn = wfc._build_caption_filter_parts(
        caps, "[0:v]", canvas_w=480, tmpdir=str(tmp_path)
    )
    assert drawn == 1
    assert len(parts) == 1


def test_uppercase_transform_applied_to_sidecar(tmp_path):
    parts, _label, _drawn = wfc._build_caption_filter_parts(
        [_caption(text="stay cozy", start=0.0, end=2.0, textTransform="uppercase")],
        "[0:v]",
        canvas_w=480,
        tmpdir=str(tmp_path),
    )
    assert parts
    assert (tmp_path / "caption_0.txt").read_text(encoding="utf-8") == "STAY COZY"


def test_no_captions_returns_passthrough(tmp_path):
    parts, label, drawn = wfc._build_caption_filter_parts(
        [], "[0:v]", canvas_w=480, tmpdir=str(tmp_path)
    )
    assert parts == []
    assert label == "[0:v]"
    assert drawn == 0

"""Regression coverage for burnt-in captions in worker_ffmpeg_compose.

Two bugs on the in-process compose path
(``worker_ffmpeg_compose._run_ffmpeg_compose``, Path 2 in tasks/cast_render.py):

1. Caption text was inlined into ``-filter_complex`` as ``text='<words>'`` with
   shell-style ``'\\''`` apostrophe escaping and real-newline line joins. On the
   production FFmpeg build a wrapped caption containing an apostrophe
   ("shouldn't", "it's") desynced the filtergraph quote parser, so the ``text=``
   value swallowed the filter's own trailing options and drawtext burned
   ``:fontsize=42:...:enable=between(t,4.7333333333333333,10.8)`` into the frame
   as literal text (confirmed live on cst_26b60791fd7c). Fix: the caption body
   goes to a ``tmpdir/caption_<n>.txt`` sidecar referenced with ``textfile=`` +
   ``expansion=none``.

2. This path burned the whole block's caption as one static line — no
   ~6-7-word paging and no karaoke word highlight, unlike the editor preview.
   Fix: when the overlay carries ``_captions_tokens`` (per-word timing), page it
   like cast_ffmpeg_composer / the preview and stack a per-word highlight layer.

These tests inspect the constructed filtergraph via the extracted
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


def _tokens(words, *, start_ms, step_ms=250, dur_ms=240):
    return [
        {
            "text": w,
            "startMs": start_ms + i * step_ms,
            "endMs": start_ms + i * step_ms + dur_ms,
        }
        for i, w in enumerate(words)
    ]


# ── Bug 1: no filter syntax burned as text ──────────────────────────────────

def test_caption_uses_textfile_not_inlined_text(tmp_path):
    parts, label, drawn = wfc._build_caption_filter_parts(
        [_caption()], "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    assert drawn == 1
    assert label == "[cap0]"
    graph = ";".join(parts)

    assert "textfile='" in graph
    assert "expansion=none" in graph
    assert "text='" not in graph

    # No fragment of the caption prose leaks into the filtergraph string.
    for fragment in ("built to last", "recycled fibers", "it's soft", "shouldn"):
        assert fragment not in graph

    txt = (tmp_path / "caption_0.txt").read_text(encoding="utf-8")
    assert "it's soft" in txt
    assert "'\\''" not in txt  # no shell-style escaping in the file
    assert "\\," not in txt  # no filtergraph comma-escaping in the file
    assert "\n" in txt  # wrapped onto >1 line
    assert txt.split() == OFFENDING.split()  # every word preserved, in order


def test_caption_filter_options_survive_after_textfile(tmp_path):
    parts, _label, _drawn = wfc._build_caption_filter_parts(
        [_caption()], "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    part = parts[0]
    assert ":fontsize=" in part
    assert ":fix_bounds=1:" in part
    # No preset positionY → bottom-margin fallback. Margin is now a fraction
    # of canvas height (CAPTION_BOTTOM_MARGIN_FRAC, default 0.14) so it clears
    # the video player's control bar, instead of a fixed 40 px.
    assert "x=(w-text_w)/2:y=h-text_h-(h*0." in part
    assert "enable='between(t,4.750,10.800)'" in part


def test_multiple_captions_chain_in_start_order(tmp_path):
    caps = [
        _caption(text="Second one, later.", start=5.0, end=9.0),
        _caption(text="First up — it's early!", start=0.0, end=4.0),
    ]
    parts, label, drawn = wfc._build_caption_filter_parts(
        caps, "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    assert drawn == 2
    assert label == "[cap1]"
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
        caps, "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    assert drawn == 1
    assert len(parts) == 1


def test_uppercase_transform_applied_to_sidecar(tmp_path):
    parts, _label, _drawn = wfc._build_caption_filter_parts(
        [_caption(text="stay cozy", start=0.0, end=2.0, textTransform="uppercase")],
        "[0:v]",
        canvas_w=480,
        canvas_h=848,
        tmpdir=str(tmp_path),
    )
    assert parts
    assert (tmp_path / "caption_0.txt").read_text(encoding="utf-8") == "STAY COZY"


def test_no_captions_returns_passthrough(tmp_path):
    parts, label, drawn = wfc._build_caption_filter_parts(
        [], "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    assert parts == []
    assert label == "[0:v]"
    assert drawn == 0


# ── Bug 2: word-windowed paging + karaoke highlight in the render ────────────

WORDS_14 = (
    "your hoodie should not fall apart after ten washes but this one is "
    "different"
).split()


def test_tokens_paged_into_word_windows(tmp_path):
    cap = _caption(
        text=" ".join(WORDS_14),
        start=4.0,
        end=8.0,
        _captions_tokens=_tokens(WORDS_14, start_ms=4000),
    )
    parts, label, drawn = wfc._build_caption_filter_parts(
        [cap], "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    # One caption element, but split into >=2 timed pages.
    assert drawn == 1
    assert len(parts) >= 2
    assert label == f"[cap{len(parts) - 1}]"

    # Each page's sidecar holds at most _MAX_WORDS_PER_PAGE words.
    page_files = sorted(
        tmp_path.glob("caption_*.txt"),
        key=lambda p: int(p.stem.split("_")[1]),
    )
    assert len(page_files) == len(parts)
    for pf in page_files:
        assert len(pf.read_text(encoding="utf-8").split()) <= wfc._MAX_WORDS_PER_PAGE

    # Concatenated pages reproduce the caption, in order, nothing lost.
    rejoined = " ".join(
        pf.read_text(encoding="utf-8").replace("\n", " ") for pf in page_files
    ).split()
    assert rejoined == WORDS_14

    # Pages advance in time — page 2 enables strictly after page 1.
    import re

    starts = [
        float(re.search(r"between\(t,([0-9.]+),", p).group(1)) for p in parts
    ]
    assert starts == sorted(starts)
    assert starts[1] > starts[0]

    # No highlight layer when highlightColor is absent (defaults to base
    # color) — every filter part is a base page, one per sidecar file.
    assert len(parts) == len(page_files)


def test_distinct_highlight_color_adds_per_word_layer(tmp_path):
    cap = _caption(
        text=" ".join(WORDS_14),
        start=4.0,
        end=8.0,
        fontColor="#FFFFFF",
        highlightColor="#FFD400",
        _captions_tokens=_tokens(WORDS_14, start_ms=4000),
    )
    parts, _label, drawn = wfc._build_caption_filter_parts(
        [cap], "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    assert drawn == 1

    import re

    def y_of(p):
        return int(re.search(r":y=([0-9]+):", p).group(1))

    base_parts = [p for p in parts if "fontcolor=0xFFFFFF" in p]
    hl_parts = [p for p in parts if "fontcolor=0xFFD400" in p]
    # One base-colour drawtext for the whole line PER PAGE, plus one
    # highlight-colour drawtext per spoken word.
    n_pages = len(base_parts)
    assert 1 <= n_pages < len(WORDS_14)
    assert len(hl_parts) == len(WORDS_14)

    # The base line is centred (`x=(w-<line_w>)/2`, no per-word offset); each
    # highlight word carries the measured offset to its slot in that line.
    for p in base_parts:
        assert re.search(r"x=\(w-[0-9.]+\)/2:", p), p
    for p in hl_parts:
        assert re.search(r"x=\(w-[0-9.]+\)/2\+[0-9.]+:", p), p
        assert "box=1" not in p

    # y is a plain integer everywhere — never the per-word `h-text_h-40` that
    # let ascender-less words ("warm,") float above the line.
    for p in base_parts + hl_parts:
        assert re.search(r":y=[0-9]+:", p), p
        assert "text_h" not in p.split(":y=")[1].split(":")[0]

    # A highlight word is only ever nudged DOWN onto the baseline, never up:
    # every highlight y >= the page baseline y it belongs to.
    base_ys = sorted(y_of(p) for p in base_parts)
    for p in hl_parts:
        assert min(base_ys) <= y_of(p) <= max(base_ys) + 60

    # Each highlight word's window sits inside the caption window.
    for p in hl_parts:
        m = re.search(r"between\(t,([0-9.]+),([0-9.]+)\)", p)
        s, e = float(m.group(1)), float(m.group(2))
        assert 4.0 <= s < e <= 8.0

    # First word of each page sits at the line's left edge (offset 0.0).
    assert any(re.search(r"x=\(w-[0-9.]+\)/2\+0\.0:", p) for p in hl_parts)


def test_identical_highlight_color_no_extra_layer(tmp_path):
    cap = _caption(
        text=" ".join(WORDS_14),
        start=4.0,
        end=8.0,
        fontColor="#FFFFFF",
        highlightColor="#FFFFFF",
        _captions_tokens=_tokens(WORDS_14, start_ms=4000),
    )
    parts, _label, _drawn = wfc._build_caption_filter_parts(
        [cap], "[0:v]", canvas_w=480, canvas_h=848, tmpdir=str(tmp_path)
    )
    # Only the base pages, no per-word layer.
    assert all("fontcolor=0xFFFFFF" in p for p in parts)
    assert len(parts) < len(WORDS_14)

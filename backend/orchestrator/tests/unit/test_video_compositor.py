"""Unit tests for ``services.video_compositor``.

Regression guard for the product-overlay ``filter_complex`` bug: the
``enable=between(t,start,end)`` clause MUST be wrapped in single quotes,
otherwise ffmpeg's filter-graph parser splits the value on the commas inside
``between(t,1.325,3.97)`` and aborts with ``No such filter: '1.325'``.

The fix (``composite_product_overlays_multi``) mirrors the already-correct
``:enable='between(t,...)'`` usage in the drawtext helpers in the same module.
"""
from __future__ import annotations

import sys
import types

import pytest
from PIL import Image

# ── Stub heavy / external module-level deps so importing the service is cheap ──
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

if "services.r2_storage" not in sys.modules:
    _r2_stub = types.ModuleType("services.r2_storage")
    _r2_stub.get_r2_storage_service = lambda *_a, **_k: None
    sys.modules["services.r2_storage"] = _r2_stub

from services import video_compositor  # noqa: E402


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess (ffmpeg never runs)."""

    returncode = 0

    async def communicate(self):
        return (b"", b"")


def _capture_ffmpeg_cmd(monkeypatch):
    """Patch create_subprocess_exec to record argv and skip running ffmpeg.

    The compositor deletes its scratch tmpdir (and the overlay PNGs) in a
    ``finally`` block, so we read each overlay PNG's dimensions *here* —
    while the files still exist — and stash them on the captured dict.
    """
    captured = {}

    async def _fake_exec(*args, **kwargs):
        argv = list(args)
        captured["argv"] = argv
        sizes = []
        for i, tok in enumerate(argv):
            if tok == "-i" and i + 1 < len(argv) and argv[i + 1].endswith(".png"):
                with Image.open(argv[i + 1]) as img:
                    sizes.append(img.size)
        captured["png_sizes"] = sizes
        return _FakeProc()

    monkeypatch.setattr(
        video_compositor.asyncio, "create_subprocess_exec", _fake_exec
    )
    return captured


def _filter_complex_from(argv: list[str]) -> str:
    assert "-filter_complex" in argv, f"no -filter_complex in argv: {argv}"
    return argv[argv.index("-filter_complex") + 1]


async def test_product_overlay_enable_is_single_quoted(tmp_path, monkeypatch):
    """The generated filter_complex must quote the between() expression."""
    captured = _capture_ffmpeg_cmd(monkeypatch)

    output = str(tmp_path / "out.mp4")
    overlays = [
        {"title": "Widget", "price": "$29.99", "start_s": 1.325, "end_s": 3.97},
    ]

    await video_compositor.composite_product_overlays_multi(
        input_video=str(tmp_path / "in.mp4"),
        product_overlays=overlays,
        output=output,
    )

    filter_complex = _filter_complex_from(captured["argv"])

    # The exact regression: the value following enable= must open with a quote.
    assert "enable='between(t," in filter_complex, filter_complex
    # And the literal unquoted form must NOT appear.
    assert "enable=between(t," not in filter_complex, filter_complex


async def test_product_overlay_enable_quoted_for_every_overlay(tmp_path, monkeypatch):
    """Each chained overlay carries its own quoted enable expression."""
    captured = _capture_ffmpeg_cmd(monkeypatch)

    overlays = [
        {"title": "A", "price": "$1", "start_s": 0.0, "end_s": 2.5},
        {"title": "B", "price": "$2", "start_s": 2.5, "end_s": 4.125},
        {"title": "C", "price": "$3", "start_s": 4.125, "end_s": 6.0},
    ]

    await video_compositor.composite_product_overlays_multi(
        input_video=str(tmp_path / "in.mp4"),
        product_overlays=overlays,
        output=str(tmp_path / "out.mp4"),
    )

    filter_complex = _filter_complex_from(captured["argv"])

    # One quoted enable clause per overlay; none unquoted.
    assert filter_complex.count("enable='between(t,") == len(overlays), filter_complex
    assert "enable=between(t," not in filter_complex, filter_complex


# ── Hero-mode dimensional + positioning tests ──────────────────────────────


def _overlay_coords(filter_complex: str) -> list[tuple[int, int]]:
    """Parse each ``overlay=X:Y:enable=...`` clause into (x, y) int pairs."""
    coords = []
    for part in filter_complex.split(";"):
        marker = "overlay="
        if marker not in part:
            continue
        rest = part.split(marker, 1)[1]
        x_str, rest2 = rest.split(":", 1)
        y_str = rest2.split(":", 1)[0]
        coords.append((int(x_str), int(y_str)))
    return coords


async def test_hero_mode_when_width_zero(tmp_path, monkeypatch):
    """width=0 selects hero mode (Round-6 Bug C): the product image is sized to
    78% of the canvas and wrapped in a rounded card + drop shadow, so the
    emitted PNG (card chrome included) never exceeds the canvas width and the
    inner image tracks the 78% target."""
    captured = _capture_ffmpeg_cmd(monkeypatch)

    canvas_width = 720
    overlays = [
        {"title": "Widget", "price": "$29.99", "start_s": 0.0, "end_s": 3.0, "width": 0},
    ]

    await video_compositor.composite_product_overlays_multi(
        input_video=str(tmp_path / "in.mp4"),
        product_overlays=overlays,
        output=str(tmp_path / "out.mp4"),
        canvas_width=canvas_width,
        canvas_height=1280,
    )

    sizes = captured["png_sizes"]
    assert len(sizes) == 1, sizes

    target_img_w = video_compositor._hero_target_width(canvas_width)
    png_w, png_h = sizes[0]
    # The full overlay PNG (image + padding + shadow margins) must still fit
    # inside the canvas — no half-frame overflow.
    assert png_w <= canvas_width, (png_w, canvas_width)
    # The inner product image is the 78% target; the PNG is that plus a fixed
    # amount of card chrome (2*pad + 2*shadow_margin).
    chrome = 2 * 16 + 2 * (12 * 2)
    assert png_w == target_img_w + chrome, (png_w, target_img_w, chrome)


async def test_legacy_mode_when_width_positive(tmp_path, monkeypatch):
    """width>0 preserves legacy 2:1 thumbnail card dimensions."""
    captured = _capture_ffmpeg_cmd(monkeypatch)

    overlays = [
        {"title": "Widget", "price": "$29.99", "start_s": 0.0, "end_s": 3.0, "width": 400},
    ]

    await video_compositor.composite_product_overlays_multi(
        input_video=str(tmp_path / "in.mp4"),
        product_overlays=overlays,
        output=str(tmp_path / "out.mp4"),
    )

    sizes = captured["png_sizes"]
    assert len(sizes) == 1, sizes
    assert sizes[0] == (400, 200), sizes[0]


async def test_centre_sentinel_x_minus_one(tmp_path, monkeypatch):
    """x=-1 centres the card horizontally on the canvas."""
    captured = _capture_ffmpeg_cmd(monkeypatch)

    canvas_width = 720
    overlays = [
        {"title": "W", "price": "$1", "start_s": 0.0, "end_s": 3.0, "width": 0, "x": -1},
    ]

    await video_compositor.composite_product_overlays_multi(
        input_video=str(tmp_path / "in.mp4"),
        product_overlays=overlays,
        output=str(tmp_path / "out.mp4"),
        canvas_width=canvas_width,
        canvas_height=1280,
    )

    filter_complex = _filter_complex_from(captured["argv"])
    coords = _overlay_coords(filter_complex)
    assert len(coords) == 1, coords

    # x=-1 centres the overlay PNG (card + shadow) horizontally, clamped so the
    # visible card never crosses the safe inset.
    png_w = captured["png_sizes"][0][0]
    shadow_margin = 12 * 2
    safe_inset = video_compositor._hero_safe_inset(canvas_width)
    expected_x = (canvas_width - png_w) // 2
    min_x = safe_inset - shadow_margin
    max_x = canvas_width - png_w - (safe_inset - shadow_margin)
    expected_x = max(min_x, min(expected_x, max_x))
    assert coords[0][0] == expected_x, coords


async def test_lower_landing_sentinel_y_minus_one(tmp_path, monkeypatch):
    """y=-1 lands the card in the lower portion, never overflowing the canvas."""
    captured = _capture_ffmpeg_cmd(monkeypatch)

    canvas_width = 720
    canvas_height = 1280
    overlays = [
        {"title": "W", "price": "$1", "start_s": 0.0, "end_s": 3.0, "width": 0, "y": -1},
    ]

    await video_compositor.composite_product_overlays_multi(
        input_video=str(tmp_path / "in.mp4"),
        product_overlays=overlays,
        output=str(tmp_path / "out.mp4"),
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )

    filter_complex = _filter_complex_from(captured["argv"])
    coords = _overlay_coords(filter_complex)
    assert len(coords) == 1, coords

    png_h = captured["png_sizes"][0][1]
    y = coords[0][1]

    # Default landing is ~34% down the canvas, pulled up if it would overflow.
    assert y >= 20, y
    # Never overflows the bottom (20px margin).
    assert y + png_h <= canvas_height - 20, (y, png_h, canvas_height)

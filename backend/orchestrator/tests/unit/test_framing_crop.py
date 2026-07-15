"""Round-6 Bug B round-4 — deterministic camera-framing crops.

``services.framing_crop.apply_framing`` replaces the image-to-image "framing"
step with a pure crop/pad/perspective transform on the avatar's canonical
portrait. These tests assert the contract the render pipeline depends on:

  * every framing preserves the exact source canvas dimensions,
  * different framings produce different bitmaps (the whole point of the fix),
  * MEDIUM is a (re-encoded) pass-through of the canonical portrait,
  * the transform is deterministic (same input -> same bytes),
  * it works whether or not OpenCV face detection is available.

No DB, no network — pure PIL in-memory, mirroring test_talking_head_framing_lookup.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from services.framing_crop import (
    ANGLE_LEFT_3Q,
    ANGLE_RIGHT_3Q,
    CLOSE,
    MEDIUM,
    MEDIUM_WIDE,
    WIDE,
    apply_framing,
)

ALL_FRAMINGS = [MEDIUM, CLOSE, MEDIUM_WIDE, WIDE, ANGLE_LEFT_3Q, ANGLE_RIGHT_3Q]
CANVAS = (832, 1488)  # 9:16 portrait, matches the production source size


def _portrait_bytes(size=CANVAS) -> bytes:
    """A synthetic 9:16 portrait with a face-like patch in the top third."""
    w, h = size
    img = Image.new("RGB", (w, h), (40, 80, 160))
    fx0, fy0 = int(w * 0.43), int(h * 0.28)
    fx1, fy1 = int(w * 0.57), int(h * 0.40)
    for x in range(fx0, fx1):
        for y in range(fy0, fy1):
            img.putpixel((x, y), (220, 180, 150))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _decode(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b))


@pytest.mark.parametrize("framing", ALL_FRAMINGS)
def test_preserves_canvas_dimensions(framing):
    out = apply_framing(_portrait_bytes(), framing)
    assert _decode(out).size == CANVAS


@pytest.mark.parametrize("framing", ALL_FRAMINGS)
def test_returns_decodable_jpeg(framing):
    out = apply_framing(_portrait_bytes(), framing)
    img = _decode(out)
    assert img.format == "JPEG"


def test_all_framings_are_distinct():
    src = _portrait_bytes()
    outputs = {f: apply_framing(src, f) for f in ALL_FRAMINGS}
    seen: dict[bytes, str] = {}
    for framing, data in outputs.items():
        assert data not in seen, f"{framing} produced identical bytes to {seen.get(data)}"
        seen[data] = framing


def test_close_differs_from_medium():
    src = _portrait_bytes()
    assert apply_framing(src, CLOSE) != apply_framing(src, MEDIUM)


def test_angle_left_differs_from_angle_right():
    src = _portrait_bytes()
    assert apply_framing(src, ANGLE_LEFT_3Q) != apply_framing(src, ANGLE_RIGHT_3Q)


def test_medium_is_passthrough_dimensions():
    # MEDIUM re-encodes the canonical portrait; allow JPEG delta but require the
    # canvas to be untouched and the dominant top-third face patch to survive.
    src = _portrait_bytes()
    out = _decode(apply_framing(src, MEDIUM)).convert("RGB")
    assert out.size == CANVAS
    cx, cy = int(CANVAS[0] * 0.5), int(CANVAS[1] * 0.33)
    r, g, b = out.getpixel((cx, cy))
    # The face patch is warm-toned (R high, B low); ensure it stayed near top.
    assert r > b


def test_deterministic_same_input_same_output():
    src = _portrait_bytes()
    assert apply_framing(src, CLOSE) == apply_framing(src, CLOSE)
    assert apply_framing(src, ANGLE_RIGHT_3Q) == apply_framing(src, ANGLE_RIGHT_3Q)


def test_unknown_framing_falls_back_to_medium():
    src = _portrait_bytes()
    bogus = apply_framing(src, "NOT_A_FRAMING")
    medium = apply_framing(src, MEDIUM)
    assert bogus == medium


def test_lowercase_framing_normalized():
    src = _portrait_bytes()
    assert apply_framing(src, "close") == apply_framing(src, CLOSE)


def test_none_framing_defaults_to_medium():
    src = _portrait_bytes()
    assert apply_framing(src, None) == apply_framing(src, MEDIUM)


def test_works_without_opencv(monkeypatch):
    """Force the cv2 import inside _find_face_center to fail and confirm the
    top-third-center heuristic still produces valid, distinct output."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cv2":
            raise ImportError("cv2 disabled for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    src = _portrait_bytes()
    close = apply_framing(src, CLOSE)
    medium = apply_framing(src, MEDIUM)
    assert _decode(close).size == CANVAS
    assert close != medium


def test_non_default_canvas_size_preserved():
    other = (720, 1280)
    out = apply_framing(_portrait_bytes(other), WIDE)
    assert _decode(out).size == other

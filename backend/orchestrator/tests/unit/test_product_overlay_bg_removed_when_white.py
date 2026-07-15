"""Round-6 Bug C — white product-photo backgrounds are detected and removed.

The product hero card was compositing a raw JPG with a white box onto the dark
canvas. ``has_white_background`` detects that case (so we don't waste a pass on
already-transparent art) and ``remove_background`` keys the white out, always
emitting a transparent PNG.
"""
import os

from PIL import Image

from services import bg_remove


def _make_white_box_with_product(path: str) -> None:
    """White canvas with a small dark square in the middle (a 'product')."""
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    for y in range(80, 120):
        for x in range(80, 120):
            img.putpixel((x, y), (10, 10, 10))
    img.save(path, "JPEG")


def _make_dark_image(path: str) -> None:
    img = Image.new("RGB", (200, 200), (12, 12, 14))
    img.save(path, "PNG")


def test_white_box_is_detected(tmp_path):
    p = str(tmp_path / "white.jpg")
    _make_white_box_with_product(p)
    assert bg_remove.has_white_background(p) is True


def test_dark_image_is_not_flagged(tmp_path):
    p = str(tmp_path / "dark.png")
    _make_dark_image(p)
    assert bg_remove.has_white_background(p) is False


def test_remove_background_emits_transparent_png(tmp_path):
    src = str(tmp_path / "white.jpg")
    out = str(tmp_path / "out.png")
    _make_white_box_with_product(src)

    result = bg_remove.remove_background(src, out)
    assert result == out
    assert os.path.exists(out) and os.path.getsize(out) > 0

    result_img = Image.open(out).convert("RGBA")
    # A corner pixel (was pure white) should now be fully transparent, while
    # the dark product centre stays opaque. (rembg, if installed, also yields a
    # transparent border on this synthetic image.)
    assert result_img.getpixel((0, 0))[3] == 0
    assert result_img.getpixel((100, 100))[3] == 255


def test_cached_nobg_key_shape():
    key = bg_remove.cached_nobg_key("prod_1", "asset_9")
    assert key == "products/prod_1/assets/asset_9_nobg.png"

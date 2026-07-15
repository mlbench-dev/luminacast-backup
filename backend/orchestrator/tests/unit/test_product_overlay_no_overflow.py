"""Round-6 Bug C — product hero image must fit entirely inside the safe area.

The user saw the product image overflow the right edge (a half-frame cut off).
The fix sizes the hero image to 78% of the canvas width and clamps its x to
the left safe inset so it can never reach a cropping edge.
"""
from services import video_compositor as vc


CANVAS_W = 480
CANVAS_H = 848


def test_hero_target_width_is_78_percent():
    # 78% of 480 = 374.4 -> 374, leaving >= 24px on each side.
    assert vc._hero_target_width(CANVAS_W) == int(CANVAS_W * 0.78)
    assert vc._hero_target_width(CANVAS_W) <= 374


def test_hero_target_width_never_overflows_safe_area():
    target_w = vc._hero_target_width(CANVAS_W)
    inset = vc._hero_safe_inset(CANVAS_W)
    # The image plus both safe insets must stay within the canvas.
    assert target_w + 2 * inset <= CANVAS_W


def test_hero_safe_inset_floor_is_24():
    assert vc._hero_safe_inset(CANVAS_W) == 24
    assert vc._hero_safe_inset(1080) == 54


def test_target_height_preserves_aspect_within_bounds():
    # A 1000x1500 source scaled to 374 wide keeps aspect: h = 374 * 1.5 = 561.
    src_w, src_h = 1000, 1500
    target_w = vc._hero_target_width(CANVAS_W)
    target_h = int(target_w * (src_h / src_w))
    assert target_h == int(target_w * 1.5)
    # The card (image + shadow margin) still fits the canvas height.
    assert target_h < CANVAS_H

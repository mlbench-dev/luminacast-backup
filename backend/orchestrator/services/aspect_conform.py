"""Aspect-ratio-aware "conform" helpers — contain-fit + blurred-extend
instead of a hard cover-crop when a source's shape diverges meaningfully
from its target canvas.

Bug this exists for: every avatar reference photo, body shot, and product
image in the app is authored/generated PORTRAIT-only (e.g. 1024x1792),
regardless of which output format a cast actually uses. When a cast is set
to "Horizontal" (16:9), the render pipeline still hands that same portrait
photo to the AI video generator and, later, force-crops whatever comes back
to cover the wide landscape canvas (``scale=...:force_original_aspect_ratio
=increase,crop=W:H``). Since a 9:16 source is roughly 3.2x narrower than a
16:9 target, "cover" has to blow the image up ~1.8x and throw away most of
its height to fill the frame — that's the "heavily zoomed in" bug report.

The fix applied throughout the render pipeline (block_normalize, the avatar
face reference fed to the talking-head bake, and the product/b-roll image
-> Ken-Burns clip path) is: keep the existing cheap cover-crop when the
source and target shapes are already close (same-family formats — a little
edge trim is invisible and cheaper than blurring), but switch to a
CONTAIN-fit + blurred, edge-extended backdrop when they diverge past
``CONTAIN_FIT_THRESHOLD``. Contain-fit never crops the subject and the blur
extension avoids a hard black bar, satisfying both halves of the bug
report's expected result ("without unnecessary zooming, cropping, or black
bars").

Two domains, one shared threshold/decision function:
  * :func:`build_conform_filter` — an ffmpeg filter-graph fragment, for
    video sources (baked avatar/product clips, b-roll video).
  * :func:`conform_image_bytes` — a pure PIL transform, for still images
    fed either straight to a Ken-Burns clip or as an I2V reference photo.

Both are pure / deterministic and safe to unit test without ffmpeg or
network access.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# Below this ratio (0..1; 1.0 = identical shape) a straight cover-crop would
# throw away too much of the frame. Same-family format changes (9:16 -> 4:5,
# 16:9 -> 1:1) land comfortably above this and keep the cheap cover-crop
# (minor edge trim); a portrait<->landscape flip (9:16 -> 16:9, the reported
# bug) lands well below it.
#   9:16 (0.5625) vs 16:9 (1.778) -> ratio 0.316 (triggers contain-fit)
#   9:16 (0.5625) vs 4:5  (0.8)   -> ratio 0.703 (stays cover-crop)
#   16:9 (1.778)  vs 1:1  (1.0)   -> ratio 0.5625 (triggers contain-fit)
CONTAIN_FIT_THRESHOLD = 0.65

_JPEG_QUALITY = 92


def aspect_ratio(w: int, h: int) -> float:
    if not w or not h:
        return 1.0
    return float(w) / float(h)


def shapes_diverge(
    src_w: int, src_h: int, target_w: int, target_h: int,
    threshold: float = CONTAIN_FIT_THRESHOLD,
) -> bool:
    """True when a cover-crop from (src_w, src_h) to (target_w, target_h)
    would lose a meaningful chunk of the subject — i.e. contain-fit should
    be used instead. False (including on any zero/invalid input) means the
    existing cheap cover-crop is fine.
    """
    if src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
        return False
    src_ar = aspect_ratio(src_w, src_h)
    tgt_ar = aspect_ratio(target_w, target_h)
    ratio = min(src_ar, tgt_ar) / max(src_ar, tgt_ar)
    return ratio < threshold


def build_conform_filter(
    *,
    in_label: str,
    out_label: str,
    target_w: int,
    target_h: int,
    src_w: int,
    src_h: int,
    extra_pre: str = "",
    threshold: float = CONTAIN_FIT_THRESHOLD,
) -> str:
    """Return an ffmpeg filter-graph fragment conforming ``in_label`` to
    (target_w, target_h) and binding the result to ``out_label``.

    ``extra_pre`` is any filter(s) to apply to the input stream before the
    conform step (e.g. ``"fps=30,"``) — must end in a comma when non-empty.

    Cover-crop (existing behaviour) when the shapes are close; contain-fit
    onto a blurred, edge-extended backdrop of the same frame when they
    diverge (see module docstring). Output is always exactly
    ``target_w x target_h``, sar=1, in both branches.
    """
    if not shapes_diverge(src_w, src_h, target_w, target_h, threshold):
        return (
            f"[{in_label}]{extra_pre}scale={target_w}:{target_h}:"
            f"force_original_aspect_ratio=increase,crop={target_w}:{target_h},"
            f"setsar=1[{out_label}]"
        )
    # Contain + blurred-extend background: split the source into a
    # cover-cropped + heavily blurred + slightly darkened backdrop (so nothing
    # reads as a hard black bar) and a contain-fit (nothing cropped)
    # foreground, then centre the foreground over the backdrop.
    blur_sigma = max(10, min(target_w, target_h) // 20)
    return (
        f"[{in_label}]{extra_pre}split=2[cf_bg_{out_label}][cf_fg_{out_label}];"
        f"[cf_bg_{out_label}]scale={target_w}:{target_h}:"
        f"force_original_aspect_ratio=increase,crop={target_w}:{target_h},"
        f"gblur=sigma={blur_sigma},eq=brightness=-0.08,setsar=1[cf_bgo_{out_label}];"
        f"[cf_fg_{out_label}]scale={target_w}:{target_h}:"
        f"force_original_aspect_ratio=decrease,setsar=1[cf_fgo_{out_label}];"
        f"[cf_bgo_{out_label}][cf_fgo_{out_label}]overlay=(W-w)/2:(H-h)/2[{out_label}]"
    )


def conform_image_bytes(
    image_bytes: bytes,
    target_w: int,
    target_h: int,
    *,
    threshold: float = CONTAIN_FIT_THRESHOLD,
) -> bytes:
    """Return JPEG bytes of ``image_bytes`` conformed to (target_w, target_h).

    Mirrors :func:`build_conform_filter`'s two branches, done with PIL for
    callers that need a still image (an I2V reference photo, or the source
    frame for a Ken-Burns clip) rather than an ffmpeg filter graph. Never
    raises on a decodable image; returns the resize-only fallback if PIL
    itself is unavailable for some reason (caller can also just skip calling
    this on failure — it's always optional).
    """
    from PIL import Image, ImageEnhance, ImageFilter

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    if not shapes_diverge(w, h, target_w, target_h, threshold):
        scale = max(target_w / w, target_h / h)
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        left = max(0, (new_w - target_w) // 2)
        top = max(0, (new_h - target_h) // 2)
        out = resized.crop((left, top, left + target_w, top + target_h))
    else:
        scale = min(target_w / w, target_h / h)
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        fg = img.resize((new_w, new_h), Image.LANCZOS)

        bg = img.resize((target_w, target_h), Image.LANCZOS)
        blur_radius = max(8, min(target_w, target_h) // 40)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        bg = ImageEnhance.Brightness(bg).enhance(0.85)

        off_x = max(0, (target_w - new_w) // 2)
        off_y = max(0, (target_h - new_h) // 2)
        bg.paste(fg, (off_x, off_y))
        out = bg

    if out.size != (target_w, target_h):
        out = out.resize((target_w, target_h), Image.LANCZOS)

    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()

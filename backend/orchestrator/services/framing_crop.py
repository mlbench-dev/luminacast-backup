"""Deterministic camera-framing transforms for avatar talking-head looks.

Round-6 Bug B round-4. Instead of asking an image-to-image model to recompose a
portrait per camera framing (conservative, visually indistinct after lip-sync),
we apply a pure-function crop / pad / perspective transform to the avatar's
single canonical portrait. The output is a different bitmap per framing —
guaranteed distinct, no model roulette, no extra inference cost.

Every transform:
  * preserves the source canvas dimensions exactly (downstream lip-sync expects a
    fixed resolution),
  * keeps the face anchored inside the frame (shifts are clamped),
  * is idempotent + pure: same input + framing -> same output bytes, so the
    result is safely cacheable in R2 keyed by the AvatarLook id.

Face anchoring uses OpenCV's bundled Haar cascade when ``cv2`` is importable;
otherwise it falls back to a "top third, horizontally centered" heuristic. We
deliberately avoid adding heavy detection deps — speed of variation matters more
than photorealism here.
"""
from __future__ import annotations

import io
import logging
from typing import Tuple

from PIL import Image

import sentry_sdk

logger = logging.getLogger(__name__)

# Canonical framing keys — mirror models.avatar_look.ShotFraming. Kept as bare
# strings so this module has no DB/model import dependency and stays pure.
CLOSE = "CLOSE"
MEDIUM = "MEDIUM"
MEDIUM_WIDE = "MEDIUM_WIDE"
WIDE = "WIDE"
ANGLE_LEFT_3Q = "ANGLE_LEFT_3Q"
ANGLE_RIGHT_3Q = "ANGLE_RIGHT_3Q"

DEFAULT_FRAMING = MEDIUM

_JPEG_QUALITY = 92

# Fraction of canvas height where an undetected face is assumed to sit (top third
# center). Used by the fallback anchor and as a vertical recenter target.
_HEURISTIC_FACE_Y = 1.0 / 3.0


def _find_face_center(img: Image.Image) -> Tuple[float, float]:
    """Return the face center as (fx, fy) fractions of (width, height).

    Tries OpenCV's bundled frontal-face Haar cascade; on any failure (cv2 not
    installed, no face found, decode error) falls back to the top-third-center
    heuristic. Never raises.
    """
    w, h = img.size
    fallback = (0.5, _HEURISTIC_FACE_Y)
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            return fallback
        gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if faces is None or len(faces) == 0:
            return fallback
        # Largest detected face wins.
        fx0, fy0, fw, fh = max(faces, key=lambda r: int(r[2]) * int(r[3]))
        cx = (float(fx0) + float(fw) / 2.0) / float(w)
        cy = (float(fy0) + float(fh) / 2.0) / float(h)
        return (cx, cy)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.info("Face detect unavailable/failed — using top-third heuristic: %s", e)
        return fallback


def _zoom_about(
    img: Image.Image,
    scale: float,
    face_xy: Tuple[float, float],
    target_xy: Tuple[float, float],
) -> Image.Image:
    """Resize the subject by ``scale`` and recompose onto a same-size canvas so
    the face lands at ``target_xy`` (fractions of the output canvas).

    ``scale > 1`` zooms in (tighter shot); ``scale < 1`` shrinks the subject and
    pads the surrounding canvas via edge replication so there is no hard border.
    The face position is clamped to keep it fully inside the frame.
    """
    w, h = img.size
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    # Face center in the resized image, in pixels.
    face_px = face_xy[0] * new_w
    face_py = face_xy[1] * new_h
    # Desired face position on the output canvas, in pixels.
    tgt_px = target_xy[0] * w
    tgt_py = target_xy[1] * h
    # Top-left offset at which to paste the resized image onto the canvas.
    off_x = int(round(tgt_px - face_px))
    off_y = int(round(tgt_py - face_py))

    if scale >= 1.0:
        # Zoom-in: crop the resized image to the canvas window. No padding needed.
        crop_left = -off_x
        crop_top = -off_y
        # Clamp so the crop window stays inside the resized image.
        crop_left = max(0, min(crop_left, new_w - w))
        crop_top = max(0, min(crop_top, new_h - h))
        return resized.crop((crop_left, crop_top, crop_left + w, crop_top + h))

    # Shrink: build a padded background by edge-replicating the resized subject,
    # then paste the subject at the clamped offset.
    canvas = _edge_pad_background(resized, w, h)
    # Clamp the paste so the subject stays fully on-canvas.
    off_x = max(min(off_x, w - new_w), 0)
    off_y = max(min(off_y, h - new_h), 0)
    canvas.paste(resized, (off_x, off_y))
    return canvas


def _edge_pad_background(subject: Image.Image, w: int, h: int) -> Image.Image:
    """Return a (w, h) RGB canvas filled by stretching ``subject`` to cover it.

    A blurred, upscaled copy of the subject reads as a soft extended backdrop —
    cheaper and more natural than a flat fill, and fully deterministic.
    """
    from PIL import ImageFilter

    bg = subject.convert("RGB").resize((w, h), Image.LANCZOS)
    return bg.filter(ImageFilter.GaussianBlur(radius=max(2, w // 64)))


def _perspective_skew(img: Image.Image, pull_left: bool, amount: float = 0.06) -> Image.Image:
    """Apply a horizontal perspective skew so the subject reads as a 3/4 turn.

    ``pull_left=True`` pulls the left edge forward (subject appears turned to the
    viewer's right). ``amount`` is the fraction of width the near edge is widened
    by. Output keeps the source canvas size; transformed-out regions are filled
    by edge replication so no hard black border appears.
    """
    w, h = img.size
    dx = amount * w

    # PIL PERSPECTIVE maps OUTPUT (x,y) -> INPUT via 8 coeffs. We solve for the
    # coeffs from a forward quad-to-quad mapping of the four corners.
    if pull_left:
        # Left edge taller/forward: sample inner pixels at top/bottom-left.
        src = [(0, 0), (w, 0), (w, h), (0, h)]
        dst = [(0, 0 + dx), (w, 0), (w, h), (0, h - dx)]
    else:
        src = [(0, 0), (w, 0), (w, h), (0, h)]
        dst = [(0, 0), (w, 0 + dx), (w, h - dx), (0, h)]

    coeffs = _perspective_coeffs(dst, src)

    # Pre-pad with a blurred background so areas pulled in from outside the
    # original frame show backdrop, not black.
    bg = _edge_pad_background(img, w, h)
    skewed = img.transform(
        (w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC, fillcolor=None
    )
    bg.paste(skewed, (0, 0))
    return bg


def _perspective_coeffs(src_quad, dst_quad):
    """Solve the 8 PIL perspective coefficients mapping ``src_quad``->``dst_quad``.

    PIL's ``Image.transform(PERSPECTIVE)`` expects coeffs that map output coords
    back to input coords, so callers pass (output_quad, input_quad).
    """
    import numpy as np

    matrix = []
    for (sx, sy), (dx, dy) in zip(src_quad, dst_quad):
        matrix.append([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy])
        matrix.append([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy])
    a = np.array(matrix, dtype=np.float64)
    b = np.array([c for pt in dst_quad for c in pt], dtype=np.float64)
    res = np.linalg.solve(a, b)
    return res.reshape(8).tolist()


def apply_framing(source_image_bytes: bytes, framing: str) -> bytes:
    """Apply the deterministic crop/pad/skew recipe for ``framing``.

    Returns JPEG bytes at q=92 with the exact source canvas dimensions. MEDIUM
    re-encodes the canonical portrait unchanged (modulo JPEG delta). Unknown
    framings fall back to MEDIUM.
    """
    key = (framing or DEFAULT_FRAMING).strip().upper() or DEFAULT_FRAMING

    img = Image.open(io.BytesIO(source_image_bytes)).convert("RGB")
    w, h = img.size
    face_xy = _find_face_center(img)

    if key == MEDIUM:
        out = img
    elif key == CLOSE:
        # Previously a tight head shot (zoom 1.4x) — disabled per user report:
        # the in-browser editor preview (editorStarterMapping.ts) never reads
        # block.framing at all, so it always shows the full unframed avatar;
        # a CLOSE-framed block's actual render (torso/jeans cropped out) then
        # looked "zoomed"/"cut off" compared to what the editor had shown.
        # Rather than teach the editor to replicate every framing's crop,
        # CLOSE now matches MEDIUM (no crop) so render always matches
        # preview. Revisit alongside an editor-side framing preview if CLOSE
        # is wanted back as a distinct look.
        out = img
    elif key == MEDIUM_WIDE:
        # More torso + headroom: shrink to 0.72x, keep face high-center.
        out = _zoom_about(img, 0.72, face_xy, (0.5, 0.42))
    elif key == WIDE:
        # Full upper body, lots of space: shrink to 0.55x.
        out = _zoom_about(img, 0.55, face_xy, (0.5, 0.45))
    elif key == ANGLE_LEFT_3Q:
        # Subject appears turned to viewer's right; face shifted toward 0.55.
        skewed = _perspective_skew(img, pull_left=True)
        out = _zoom_about(skewed, 1.0, face_xy, (0.55, face_xy[1]))
    elif key == ANGLE_RIGHT_3Q:
        skewed = _perspective_skew(img, pull_left=False)
        out = _zoom_about(skewed, 1.0, face_xy, (0.45, face_xy[1]))
    else:
        out = img

    # Guarantee exact source dimensions.
    if out.size != (w, h):
        out = out.resize((w, h), Image.LANCZOS)

    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()

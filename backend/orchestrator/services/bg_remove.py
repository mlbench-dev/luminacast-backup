"""Product-image background removal (Round-6 Bug C).

The product hero card was compositing the raw stock product photo — a JPG with a
white background — straight onto the dark canvas, so the white box was clearly
visible. This helper removes a white / near-white background before compositing
and caches the transparent PNG to R2.

Resolution order for the removal pass:

  1. ``rembg.remove`` when the package is importable (best quality, alpha matte).
  2. A lightweight PIL white-key fallback (any pixel within a tolerance of pure
     white becomes transparent) so we still strip the box even without rembg.

``has_white_background`` lets callers skip the pass entirely for images that are
already transparent / dark, so we don't waste a removal pass or accidentally
key out a legitimately light product.
"""
import logging
import os

import sentry_sdk

logger = logging.getLogger(__name__)

# A pixel counts as "white" when each RGB channel is at/above this value. 235
# keeps off-white studio sweeps in scope without eating bright product faces.
_WHITE_THRESHOLD = 235

# Fraction of border pixels that must be white before we treat the whole image
# as having a white background worth removing.
_WHITE_BORDER_FRACTION = 0.55


def has_white_background(image_path: str) -> bool:
    """True when the image's border is predominantly white / near-white.

    We sample the 1px frame around the edge rather than the whole image so a
    product that merely contains white isn't mistaken for a white-box photo.
    """
    try:
        from PIL import Image
    except Exception as e:  # pragma: no cover — PIL ships in the worker image
        sentry_sdk.capture_exception(e)
        return False
    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return False

    w, h = img.size
    if w == 0 or h == 0:
        return False
    px = img.load()

    border_coords = []
    for x in range(w):
        border_coords.append((x, 0))
        border_coords.append((x, h - 1))
    for y in range(h):
        border_coords.append((0, y))
        border_coords.append((w - 1, y))

    white = 0
    for (x, y) in border_coords:
        r, g, b, a = px[x, y]
        if a >= 250 and r >= _WHITE_THRESHOLD and g >= _WHITE_THRESHOLD and b >= _WHITE_THRESHOLD:
            white += 1

    return (white / max(1, len(border_coords))) >= _WHITE_BORDER_FRACTION


def remove_background(image_path: str, output_path: str) -> str:
    """Write a transparent-background PNG of ``image_path`` to ``output_path``.

    Uses rembg when available, otherwise a PIL white-key fallback. Always emits
    a PNG (alpha) at ``output_path`` and returns that path. On any failure the
    original image is copied through unchanged so the caller still has a usable
    file.
    """
    try:
        with open(image_path, "rb") as f:
            raw = f.read()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return image_path

    # 1) rembg — best quality.
    try:
        from rembg import remove as rembg_remove
        out = rembg_remove(raw)
        with open(output_path, "wb") as f:
            f.write(out)
        logger.info("bg_remove: rembg removed background -> %s", output_path)
        return output_path
    except ImportError as e:
        sentry_sdk.capture_exception(e)
        logger.info("bg_remove: rembg not installed; using PIL white-key fallback")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("bg_remove: rembg failed (%s); using PIL white-key fallback", e)

    # 2) PIL white-key fallback.
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGBA")
        px = img.load()
        w, h = img.size
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if r >= _WHITE_THRESHOLD and g >= _WHITE_THRESHOLD and b >= _WHITE_THRESHOLD:
                    px[x, y] = (r, g, b, 0)
        img.save(output_path, "PNG")
        logger.info("bg_remove: PIL white-key removed background -> %s", output_path)
        return output_path
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("bg_remove: PIL fallback failed (%s); using original image", e)
        return image_path


def cached_nobg_key(product_id: str, asset_id: str) -> str:
    """R2 key for a product's background-removed PNG."""
    return f"products/{product_id}/assets/{asset_id}_nobg.png"


async def ensure_nobg_image(
    *,
    r2,
    product_id: str,
    asset_id: str,
    source_path: str,
    work_dir: str,
) -> tuple[str, bool]:
    """Return a local path to a background-removed PNG, caching it to R2.

    Skips the removal pass when the source has no white background. Returns
    ``(local_path, bg_removed)`` — ``bg_removed`` is True only when a removal
    pass actually ran. Failures fall back to the original ``source_path``.
    """
    if not has_white_background(source_path):
        return source_path, False

    key = cached_nobg_key(product_id, asset_id)
    local_nobg = os.path.join(work_dir, f"{asset_id}_nobg.png")

    # Reuse the cached PNG if it already exists in R2.
    try:
        if await r2.key_exists(key):
            await r2.download_file(key, local_nobg)
            if os.path.exists(local_nobg) and os.path.getsize(local_nobg) > 0:
                logger.info("bg_remove: reused cached nobg PNG %s", key)
                return local_nobg, True
    except Exception as e:
        sentry_sdk.capture_exception(e)

    result_path = remove_background(source_path, local_nobg)
    if result_path == local_nobg and os.path.exists(local_nobg):
        try:
            await r2.upload_file(local_nobg, key, content_type="image/png")
            logger.info("bg_remove: cached nobg PNG to %s", key)
        except Exception as e:
            sentry_sdk.capture_exception(e)
        return local_nobg, True

    return source_path, False

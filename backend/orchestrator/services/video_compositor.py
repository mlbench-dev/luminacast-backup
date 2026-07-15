"""FFmpeg-based product overlay compositor.

Downloads avatar video + product image, runs FFmpeg overlay filter,
uploads composited result to R2.
"""

import asyncio
import logging
import os
import tempfile
from typing import Optional

import httpx
import sentry_sdk

from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)

CDN_BASE = "https://media.luminacast.com"

# Position presets: (x_expr, y_expr) using FFmpeg overlay filter expressions
# W/H = main video dimensions, w/h = overlay dimensions
POSITION_MAP = {
    "bottom_right": ("W-w-20", "H-h-20"),
    "bottom_left": ("20", "H-h-20"),
    "top_right": ("W-w-20", "20"),
    "top_left": ("20", "20"),
    "center": ("(W-w)/2", "(H-h)/2"),
    "bottom_center": ("(W-w)/2", "H-h-20"),
    "top_center": ("(W-w)/2", "20"),
}


async def _download_file(url: str, dest: str) -> None:
    """Download a URL to a local file."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)


async def composite_product_overlay(
    avatar_video_url: str,
    product_image_url: str,
    output_key: str,
    layout: str = "bottom_right",
    product_size: float = 0.25,
    fade_in_seconds: float = 0.5,
    fade_out_seconds: float = 0.5,
    border_radius: int = 12,
    shadow: bool = True,
    avatar_fit_mode: str = "cover",
) -> dict:
    """Composite a product image onto an avatar video.

    Args:
        avatar_video_url: Full URL to the base video.
        product_image_url: Full URL to the product image (PNG/JPG).
        output_key: R2 key for the composited output.
        layout: Position preset (bottom_right, bottom_left, etc.).
        product_size: Product overlay width as fraction of video width (0.1-0.5).
        fade_in_seconds: Seconds for fade-in effect.
        fade_out_seconds: Seconds for fade-out effect.
        border_radius: Corner radius for product image.
        shadow: Add drop shadow behind product image.

    Returns:
        dict with output_key and metadata.
    """
    product_size = max(0.1, min(0.5, product_size))
    pos = POSITION_MAP.get(layout, POSITION_MAP["bottom_right"])

    with tempfile.TemporaryDirectory(prefix="compositor_") as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        image_path = os.path.join(tmpdir, "product.png")
        output_path = os.path.join(tmpdir, "composited.mp4")

        # Download inputs in parallel
        await asyncio.gather(
            _download_file(avatar_video_url, video_path),
            _download_file(product_image_url, image_path),
        )

        # Get video duration for fade-out timing
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        probe_proc = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_out, _ = await probe_proc.communicate()
        try:
            duration = float(probe_out.decode().strip())
        except (ValueError, AttributeError):
            duration = 10.0  # fallback

        fade_out_start = max(0, duration - fade_out_seconds)

        # Build FFmpeg filter complex
        # Use fixed 720px base width (standard 9:16 output)
        scaled_w = int(720 * product_size)
        # Ensure even dimensions
        scaled_w = max(scaled_w // 2 * 2, 2)

        x_pos, y_pos = pos

        # Pre-scale avatar video to target canvas with correct fit mode
        if avatar_fit_mode == "contain":
            pre_scale = (
                f"[0:v]scale=720:1280:force_original_aspect_ratio=decrease,"
                f"pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black[base]"
            )
        else:
            pre_scale = (
                f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
                f"crop=720:1280[base]"
            )

        scale_expr = f"[1:v]scale={scaled_w}:-2[scaled]"
        fade_filter = (
            f"[scaled]format=rgba,"
            f"fade=t=in:st=0:d={fade_in_seconds}:alpha=1,"
            f"fade=t=out:st={fade_out_start}:d={fade_out_seconds}:alpha=1[faded]"
        )
        overlay_filter = f"[base][faded]overlay={x_pos}:{y_pos}:format=auto"

        filter_complex = f"{pre_scale};{scale_expr};{fade_filter};{overlay_filter}"

        # Run FFmpeg
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", image_path,
            "-filter_complex", filter_complex,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level", "4.0",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(f"Running FFmpeg compositor: layout={layout}, size={product_size}")
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
            logger.error(f"FFmpeg compositor failed: {error_msg}")
            raise RuntimeError(f"FFmpeg overlay failed (rc={proc.returncode}): {error_msg}")

        # Upload composited video to R2
        r2 = get_r2_storage_service()
        with open(output_path, "rb") as f:
            output_bytes = f.read()

        await r2.upload_bytes(output_bytes, output_key, content_type="video/mp4")

        output_size = len(output_bytes)
        logger.info(f"Composited video uploaded: {output_key} ({output_size} bytes)")

        return {
            "output_key": output_key,
            "output_url": f"{CDN_BASE}/{output_key}",
            "size_bytes": output_size,
            "duration_seconds": duration,
            "layout": layout,
            "product_size": product_size,
        }


async def composite_text_overlays(
    video_url: str,
    output_key: str,
    overlays: list[dict],
    video_duration: float = 0,
) -> dict:
    """Burn text/sticker overlays onto a video using FFmpeg drawtext/overlay.

    Each overlay dict should have:
        kind: "text" | "sticker"
        text / sticker_emoji: content
        x, y: normalized 0-1 position
        start_ms, duration_ms: timing
        font_size_pct: size relative to frame height
        color, bg, stroke, font, animation: styling
    """
    if not overlays:
        return {"skipped": True}

    # Filter to only text/sticker (sfx handled separately)
    visual = [o for o in overlays if o.get("kind") in ("text", "sticker")]
    if not visual:
        return {"skipped": True}

    with tempfile.TemporaryDirectory(prefix="overlay_") as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        output_path = os.path.join(tmpdir, "overlayed.mp4")

        await _download_file(video_url, video_path)

        # Probe duration if not provided
        if not video_duration:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            probe_proc = await asyncio.create_subprocess_exec(
                *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            probe_out, _ = await probe_proc.communicate()
            try:
                video_duration = float(probe_out.decode().strip())
            except (ValueError, AttributeError):
                video_duration = 10.0

        # Probe video dimensions
        dim_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        dim_proc = await asyncio.create_subprocess_exec(
            *dim_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        dim_out, _ = await dim_proc.communicate()
        try:
            dim_lines = dim_out.decode().strip().split("\n")
            video_width = int(dim_lines[0])
            video_height = int(dim_lines[1])
        except (ValueError, IndexError):
            video_width, video_height = 720, 1280

        # Build drawtext filters for each overlay
        drawtext_parts = []
        for ov in visual:
            kind = ov.get("kind", "text")
            x_norm = max(0, min(1, ov.get("x", 0.5)))
            y_norm = max(0, min(1, ov.get("y", 0.5)))
            start_s = ov.get("start_ms", 0) / 1000.0
            dur_s = ov.get("duration_ms", 3000) / 1000.0
            end_s = start_s + dur_s

            # Clamp to video duration
            if end_s > video_duration:
                end_s = video_duration

            content = ov.get("text", "") if kind == "text" else ov.get("sticker_emoji", "⭐")
            if not content:
                continue

            # Escape special chars for FFmpeg drawtext
            safe_text = content.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:").replace("%", "%%")

            font_size_pct = ov.get("font_size_pct", 0.05)
            font_size = max(12, int(video_height * font_size_pct))
            color = ov.get("color", "white")
            bg_color = ov.get("bg", "")
            stroke_color = ov.get("stroke", "")

            # X/Y in pixels (using FFmpeg expressions)
            x_expr = f"w*{x_norm}"
            y_expr = f"h*{y_norm}"

            font_family = "Sans"
            font_map = {"impact": "Impact", "handwritten": "Permanent Marker", "serif": "Serif", "pixel": "Monospace"}
            if ov.get("font") in font_map:
                font_family = font_map[ov["font"]]

            dt = f"drawtext=text='{safe_text}':fontsize={font_size}:fontcolor={color}"
            dt += f":x={x_expr}:y={y_expr}"
            dt += f":font='{font_family}'"

            # Enable/disable timing
            dt += f":enable='between(t,{start_s},{end_s})'"

            # Background box
            if bg_color:
                dt += f":box=1:boxcolor={bg_color}@0.8:boxborderw=6"

            # Stroke (border text)
            if stroke_color:
                dt += f":borderw=2:bordercolor={stroke_color}"

            drawtext_parts.append(dt)

        if not drawtext_parts:
            return {"skipped": True}

        # Chain all drawtext filters
        vf = ",".join(drawtext_parts)

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level", "4.0",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(f"Running FFmpeg text overlay compositor: {len(visual)} overlays")
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
            logger.error(f"FFmpeg text overlay failed: {error_msg}")
            raise RuntimeError(f"FFmpeg text overlay failed (rc={proc.returncode}): {error_msg}")

        r2 = get_r2_storage_service()
        with open(output_path, "rb") as f:
            output_bytes = f.read()

        await r2.upload_bytes(output_bytes, output_key, content_type="video/mp4")
        logger.info(f"Text overlay video uploaded: {output_key} ({len(output_bytes)} bytes)")

        return {
            "output_key": output_key,
            "output_url": f"{CDN_BASE}/{output_key}",
            "size_bytes": len(output_bytes),
            "overlay_count": len(visual),
        }


async def composite_sfx_mix(
    video_url: str,
    output_key: str,
    sfx_overlays: list[dict],
    video_duration: float = 0,
) -> dict:
    """Mix SFX audio clips into a video's audio track at specified timestamps.

    Each sfx_overlay dict should have:
        kind: "sfx"
        sfx_preset: "ping" | "swoosh" | "bell" | "cash_register" | "drum_roll" | "chime"
        start_ms: when to trigger (ms from clip start)
        sfx_volume: 0-1 (default 0.8)
    """
    if not sfx_overlays:
        return {"skipped": True}

    sfx_list = [o for o in sfx_overlays if o.get("kind") == "sfx" and o.get("sfx_preset")]
    if not sfx_list:
        return {"skipped": True}

    with tempfile.TemporaryDirectory(prefix="sfx_") as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        output_path = os.path.join(tmpdir, "mixed.mp4")
        await _download_file(video_url, video_path)

        # Download all needed SFX files from R2
        r2 = get_r2_storage_service()
        sfx_files = {}
        for ov in sfx_list:
            preset = ov["sfx_preset"]
            if preset in sfx_files:
                continue
            sfx_key = f"sfx/{preset}.wav"
            sfx_path = os.path.join(tmpdir, f"{preset}.wav")
            try:
                sfx_url = f"{CDN_BASE}/{sfx_key}"
                await _download_file(sfx_url, sfx_path)
                sfx_files[preset] = sfx_path
            except Exception as e:
                logger.warning(f"SFX download failed for {preset}: {e}")
                continue

        if not sfx_files:
            return {"skipped": True, "reason": "no SFX files available"}

        inputs = ["-i", video_path]
        filter_chains = []
        mix_inputs = ["[0:a]"]

        sfx_idx = 1
        for ov in sfx_list:
            preset = ov["sfx_preset"]
            if preset not in sfx_files:
                continue
            inputs.extend(["-i", sfx_files[preset]])
            delay_ms = max(0, int(ov.get("start_ms", 0)))
            volume = max(0.0, min(1.0, float(ov.get("sfx_volume", 0.8))))
            filter_chains.append(
                f"[{sfx_idx}:a]adelay={delay_ms}|{delay_ms},volume={volume}[sfx{sfx_idx}]"
            )
            mix_inputs.append(f"[sfx{sfx_idx}]")
            sfx_idx += 1

        if sfx_idx == 1:
            return {"skipped": True, "reason": "no prepared SFX tracks"}

        mix_count = sfx_idx
        filter_chains.append(
            f"{''.join(mix_inputs)}amix=inputs={mix_count}:duration=first:dropout_transition=0,volume=1.5[aout]"
        )
        filter_complex = ";".join(filter_chains)

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(f"Running FFmpeg SFX mix: {sfx_idx - 1} SFX tracks")
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "unknown"
            logger.error(f"FFmpeg SFX mix failed: {error_msg}")
            raise RuntimeError(f"SFX mix failed (rc={proc.returncode}): {error_msg}")

        with open(output_path, "rb") as f:
            output_bytes = f.read()
        await r2.upload_bytes(output_bytes, output_key, "video/mp4")

        return {
            "output_key": output_key,
            "output_url": f"{CDN_BASE}/{output_key}",
            "sfx_count": sfx_idx - 1,
        }


async def ensure_product_cover_on_r2(
    product,
    db=None,
    *,
    block_id: str = "",
    cast_linked_product_ids=None,
) -> Optional[str]:
    """Resolve the product's OWN cover image to an R2 key — nothing else.

    Resolution order (Phase 4 — defect #5 fix):

      1. ``product.cover_image_key`` if already cached on R2.
      2. The product's first image ``ProductAsset`` ordered by
         ``position.asc(), created_at.asc()`` — position 0 is the
         user-chosen cover. The asset's ``r2_key`` is used directly.
      3. Otherwise return ``None`` (``product_asset_unresolved``).

    Deleted in this phase:

      * The TrendingProduct proxy. Looking the product up in the shared
        trending-products table by ``tiktok_product_id`` and proxying *that*
        image to R2 surfaced a DIFFERENT seller's product photo on the
        overlay (the "wrong product image" defect) — a trending row is not
        the user's own asset.
      * The ``media_keys`` JSON scan. It grabbed the first string ending in
        an image extension regardless of provenance / order, so it could
        pick a non-cover or stale key.

    Belongs-to-cast guard: when ``cast_linked_product_ids`` is supplied we
    refuse to resolve a cover for a product that is NOT linked to the cast
    (defence-in-depth against a mismatched ``block.product_id``), and we
    assert the resolved asset's ``product_id`` equals ``product.id`` so we
    can never return another product's asset.

    Args:
        product: Product ORM instance.
        db: Optional AsyncSession for the ProductAsset lookup + persisting
            ``cover_image_key`` back onto the product row.
        block_id: Block being composited — for the resolve DEBUG log.
        cast_linked_product_ids: Iterable of product ids linked to the cast.
            When provided, the product must be in this set.

    Returns:
        R2 key for the cover image, or None when unresolved.
    """
    def _debug(asset_id: str, resolved_url: str) -> None:
        logger.debug(
            "overlay resolve: block_id=%s product_id=%s asset_id=%s resolved_url=%s",
            block_id, getattr(product, "id", None), asset_id, resolved_url,
        )

    # Belongs-to-cast guard: never resolve a cover for a product the cast
    # isn't actually selling.
    if cast_linked_product_ids is not None:
        linked = set(cast_linked_product_ids)
        if product.id not in linked:
            logger.error(
                "overlay resolve: product %s is NOT linked to the cast "
                "(linked=%s); refusing to resolve cover (block_id=%s)",
                product.id, sorted(linked), block_id,
            )
            _debug(asset_id="", resolved_url="")
            return None

    # Step 1: already have a cover key on R2.
    if product.cover_image_key:
        _debug(asset_id="cached", resolved_url=product.cover_image_key)
        return product.cover_image_key

    # Step 2: the product's OWN first image asset (position-ordered).
    if db is not None:
        try:
            from models.product_asset import ProductAsset
            from sqlalchemy import select as _select
            asset = await db.scalar(
                _select(ProductAsset).where(
                    ProductAsset.product_id == product.id,
                    ProductAsset.media_type == "image",
                ).order_by(
                    ProductAsset.position.asc(),
                    ProductAsset.created_at.asc(),
                ).limit(1)
            )
            if asset and asset.r2_key:
                # Hard guard: the asset must belong to THIS product. The
                # query already filters on product_id, so a mismatch here
                # would be a serious ORM/data bug — fail closed.
                assert asset.product_id == product.id, (
                    f"resolved asset {asset.id} belongs to product "
                    f"{asset.product_id}, not {product.id}"
                )
                product.cover_image_key = asset.r2_key
                logger.info(
                    "Using ProductAsset as cover for %s: %s (pos=%s)",
                    product.id, asset.r2_key, asset.position,
                )
                _debug(asset_id=asset.id, resolved_url=asset.r2_key)
                return asset.r2_key
        except AssertionError:
            raise
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("ProductAsset cover lookup failed for %s: %s", product.id, e)

    logger.warning(
        "product_asset_unresolved: no image asset for product %s "
        "(tiktok_id=%s, block_id=%s). Overlay will be skipped.",
        product.id, product.tiktok_product_id, block_id,
    )
    _debug(asset_id="", resolved_url="")
    return None


async def composite_text_overlays_multi(
    input_video: str,
    text_overlays: list[dict],
    output: str,
) -> None:
    """Burn multiple timed text overlays onto a video using FFmpeg drawtext filters.

    Each overlay dict should have:
        text: str - the text content
        start_s: float - start time in seconds
        end_s: float - end time in seconds
        x: int - x position in pixels (720-wide canvas)
        y: int - y position in pixels (1280-tall canvas)
        color: str - fill color (default 'white')
        font_size: int - font size in pixels (default 48)
        font_family: str - font family (default 'Sans')
        font_weight: str - 'normal' or 'bold'
        font_style: str - 'normal' or 'italic'
        stroke_color: str - border color (optional)
        stroke_width: int - border width (optional)
        text_align: str - 'left', 'center', 'right'

    Args:
        input_video: Path to local input video file.
        text_overlays: List of overlay dicts.
        output: Path for the output video file.
    """
    if not text_overlays:
        # Nothing to do — just copy input to output
        import shutil
        shutil.copy2(input_video, output)
        return

    drawtext_parts = []
    for ov in text_overlays:
        text = ov.get("text", "")
        if not text:
            continue

        # Escape special chars for FFmpeg drawtext
        safe_text = (
            text.replace("\\", "\\\\")
            .replace("'", "'\\''")
            .replace(":", "\\:")
            .replace("%", "%%")
        )

        start_s = ov.get("start_s", 0)
        end_s = ov.get("end_s", start_s + 3)
        x = ov.get("x", 360)
        y = ov.get("y", 100)
        color = ov.get("color", "white")
        font_size = ov.get("font_size", 48)
        font_family = ov.get("font_family", "Sans")
        stroke_color = ov.get("stroke_color", "")
        stroke_width = ov.get("stroke_width", 0)

        dt = (
            f"drawtext=text='{safe_text}'"
            f":fontsize={font_size}"
            f":fontcolor={color}"
            f":x={x}"
            f":y={y}"
            f":font='{font_family}'"
            f":enable='between(t,{start_s},{end_s})'"
        )

        if stroke_color and stroke_width:
            dt += f":borderw={stroke_width}:bordercolor={stroke_color}"

        drawtext_parts.append(dt)

    if not drawtext_parts:
        import shutil
        shutil.copy2(input_video, output)
        return

    vf = ",".join(drawtext_parts)

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.0",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output,
    ]

    logger.info("Running multi-text overlay compositor: %d overlays", len(drawtext_parts))
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
        logger.error("FFmpeg multi-text overlay failed: %s", error_msg)
        raise RuntimeError(f"FFmpeg multi-text overlay failed (rc={proc.returncode}): {error_msg}")

    logger.info("Multi-text overlay complete: %s", output)


async def mux_music_track(
    video_path: str,
    music_url: str,
    output: str,
    volume: float | None = None,
) -> None:
    """Download music and mix it under the video's existing audio at reduced volume.

    Args:
        video_path: Path to local video file (with existing audio).
        music_url: URL of the music file to download and mix.
        output: Path for the output video file.
        volume: Music volume relative to original audio (0.0-1.0). When None
            the env-aware default (``MUSIC_DEFAULT_VOLUME`` → hard-coded
            ``DEFAULT_MUSIC_VOLUME``) is used. The old hard default of 0.3 was
            a landmine: any caller that forgot to pass the resolved value
            shipped music ~70× louder than the intended bed.
    """
    if volume is None:
        # Resolve from env so this path can never silently fall back to a
        # loud hard-coded default. Falls back to the composer's sane default.
        try:
            from services.cast_ffmpeg_composer import music_default_volume
            volume = music_default_volume()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            volume = 0.0044
    volume = max(0.0, min(1.0, float(volume)))
    with tempfile.TemporaryDirectory(prefix="mux_music_") as tmpdir:
        music_path = os.path.join(tmpdir, "music.mp3")
        await _download_file(music_url, music_path)

        # Use amix to blend original audio with music at reduced volume.
        # normalize=0 so the original (voice) audio keeps its level instead of
        # being attenuated 1/N by amix's default normalisation.
        filter_complex = (
            f"[1:a]volume={volume}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2:"
            f"normalize=0[aout]"
        )

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output,
        ]

        logger.info("Muxing music track at volume=%.2f", volume)
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
            logger.error("FFmpeg music mux failed: %s", error_msg)
            raise RuntimeError(f"FFmpeg music mux failed (rc={proc.returncode}): {error_msg}")

        logger.info("Music mux complete: %s", output)


def _hero_target_width(canvas_width: int) -> int:
    """Product hero image width — 78% of canvas (Round-6 Bug C).

    The product image must fit ENTIRELY inside the inner safe area so it never
    overflows the left/right edges (the user saw a half-frame cut off on the
    right). 78% of a 480px canvas is ~374px, leaving ≥24px on each side.
    """
    return int(canvas_width * 0.78)


def _hero_safe_inset(canvas_width: int) -> int:
    """Left/right safe inset for the hero card (matches caption safe area)."""
    return max(24, int(round(canvas_width * 0.05)))


def _render_hero_card(
    ov: dict,
    idx: int,
    tmpdir: str,
    canvas_width: int,
    canvas_height: int,
    font_path: str,
    font_path_regular: str,
) -> dict:
    """Render one rounded "hero" product card for vertical video.

    Round-6 Bug C: the product image is sized to 78% of the canvas so it fits
    entirely within the safe area (no edge overflow / half-frames), wrapped in a
    rounded-corner card (radius 24px, 1px white @ 10% stroke) with a soft drop
    shadow — never a bare image on the canvas. The image is expected to already
    have a transparent background (see services.bg_remove).
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    target_img_w = _hero_target_width(canvas_width)
    safe_inset = _hero_safe_inset(canvas_width)

    radius = 24
    pad = 16  # inner padding between card edge and product image
    shadow_blur = 12
    shadow_margin = shadow_blur * 2

    # Measure the product image so the card wraps it without overflow.
    prod_img = None
    prod_img_path = ov.get("product_image_path")
    if prod_img_path:
        try:
            src = Image.open(prod_img_path).convert("RGBA")
            src_w, src_h = src.size
            # target_h preserves the source aspect ratio.
            target_img_h = max(1, int(target_img_w * (src_h / max(1, src_w))))
            src.thumbnail((target_img_w, target_img_h), Image.LANCZOS)
            prod_img = src
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning("Failed to load product image %s: %s", prod_img_path, e)

    img_w, img_h = (prod_img.size if prod_img is not None else (target_img_w, int(target_img_w * 4 / 3)))

    title = ov.get("title", "")
    if len(title) > 24:
        title = title[:21] + "..."
    price = ov.get("price", "")

    title_size = max(28, int(target_img_w * 0.07))
    price_size = max(34, int(target_img_w * 0.10))
    try:
        title_font = ImageFont.truetype(font_path, title_size)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        title_font = ImageFont.load_default()
    try:
        price_font = ImageFont.truetype(font_path, price_size)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        price_font = ImageFont.load_default()

    text_block_h = (title_size + price_size + 28) if (title or price) else 0

    card_w = img_w + 2 * pad
    card_h = img_h + 2 * pad + text_block_h

    # Full canvas-aligned overlay PNG (card + drop shadow). Transparent so only
    # the rounded card + shadow are burnt onto the video.
    overlay_w = card_w + shadow_margin * 2
    overlay_h = card_h + shadow_margin * 2
    layer = Image.new("RGBA", (overlay_w, overlay_h), (0, 0, 0, 0))

    card_x0, card_y0 = shadow_margin, shadow_margin
    card_x1, card_y1 = card_x0 + card_w, card_y0 + card_h

    # Soft drop shadow.
    shadow = Image.new("RGBA", (overlay_w, overlay_h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    sdraw.rounded_rectangle(
        [card_x0, card_y0 + 6, card_x1, card_y1 + 6],
        radius=radius, fill=(0, 0, 0, 140),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
    layer.alpha_composite(shadow)

    # Rounded card body with a 1px white @ 10% opacity stroke.
    cdraw = ImageDraw.Draw(layer)
    cdraw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=radius, fill=(0, 0, 0, 180),
        outline=(255, 255, 255, 26), width=1,
    )

    if prod_img is not None:
        paste_x = card_x0 + pad
        paste_y = card_y0 + pad
        layer.paste(prod_img, (paste_x, paste_y), prod_img)

    text_x = card_x0 + pad
    title_y = card_y0 + pad + img_h + 8
    if title:
        cdraw.text((text_x, title_y), title, fill="white", font=title_font)
    if price:
        price_y = title_y + title_size + 6
        cdraw.text((text_x, price_y), price, fill="#4ade80", font=price_font)

    png_path = os.path.join(tmpdir, f"overlay_{idx}.png")
    layer.save(png_path)

    # Position: centre horizontally within the safe area by default.
    x = ov.get("x", -1)
    if x == -1:
        x = (canvas_width - overlay_w) // 2
    # Clamp so the visible card never crosses the safe inset on either side.
    min_x = safe_inset - shadow_margin
    max_x = canvas_width - overlay_w - (safe_inset - shadow_margin)
    if max_x >= min_x:
        x = max(min_x, min(x, max_x))

    y = ov.get("y", -1)
    if y == -1:
        y = max(20, int(canvas_height * 0.34))
        if y + overlay_h > canvas_height - 20:
            y = max(20, canvas_height - overlay_h - 20)

    return {
        "png_path": png_path,
        "start_s": ov.get("start_s", 0),
        "end_s": ov.get("end_s", 5),
        "x": x,
        "y": y,
    }


async def composite_product_overlays_multi(
    input_video: str,
    product_overlays: list[dict],
    output: str,
    canvas_width: int = 720,
    canvas_height: int = 1280,
) -> None:
    """Render product overlay PNGs via Pillow, then burn all onto video in a single ffmpeg pass.

    Each overlay dict should have:
        product_image_path: str - local path to the product image
        title: str - product title
        price: str - formatted price (e.g. "$29.99")
        start_s: float - start time in seconds
        end_s: float - end time in seconds
        x: int - x position on canvas (default 20; -1 = centre horizontally)
        y: int - y position on canvas (default bottom area; -1 = lower portion)
        width: int - overlay card width. 0 or unset selects "hero mode"
            (a large 3:4 portrait product card sized to the canvas, suitable
            for vertical mobile video); a positive value selects legacy mode
            (the small 2:1 thumbnail card, unchanged for back-compat).
    """
    import shutil
    from PIL import Image, ImageDraw, ImageFont

    if not product_overlays:
        shutil.copy2(input_video, output)
        return

    FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    FONT_PATH_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    import tempfile
    overlay_pngs = []
    tmpdir = tempfile.mkdtemp(prefix="prodoverlay_")

    try:
        for idx, ov in enumerate(product_overlays):
            requested_w = ov.get("width", 0)
            hero_mode = not requested_w or requested_w <= 0

            if hero_mode:
                op = _render_hero_card(
                    ov, idx, tmpdir, canvas_width, canvas_height,
                    FONT_PATH, FONT_PATH_REGULAR,
                )
                overlay_pngs.append(op)
                continue

            card_w = requested_w
            card_h = int(card_w * 0.5)  # 2:1 aspect ratio card
            card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 180))
            draw = ImageDraw.Draw(card)

            # Load product image if available
            prod_img_path = ov.get("product_image_path")
            img_area_w = int(card_w * 0.35)
            if prod_img_path:
                try:
                    prod_img = Image.open(prod_img_path).convert("RGBA")
                    prod_img.thumbnail((img_area_w, card_h - 16), Image.LANCZOS)
                    card.paste(prod_img, (8, (card_h - prod_img.size[1]) // 2), prod_img)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logger.warning("Failed to load product image %s: %s", prod_img_path, e)

            # Text area
            text_x = img_area_w + 16
            text_w = card_w - text_x - 8

            try:
                title_font = ImageFont.truetype(FONT_PATH, 20)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                title_font = ImageFont.load_default()
            try:
                price_font = ImageFont.truetype(FONT_PATH, 24)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                price_font = ImageFont.load_default()

            # Draw title (truncate if too long)
            title = ov.get("title", "")
            if len(title) > 30:
                title = title[:27] + "..."
            draw.text((text_x, 12), title, fill="white", font=title_font)

            # Draw price
            price = ov.get("price", "")
            draw.text((text_x, card_h - 36), price, fill="#4ade80", font=price_font)

            # Round corners
            png_path = os.path.join(tmpdir, f"overlay_{idx}.png")
            card.save(png_path)
            x = ov.get("x", 20)
            if x == -1:
                x = (canvas_width - card_w) // 2
            y = ov.get("y", canvas_height - card_h - 40)
            if y == -1:
                y = max(20, int(canvas_height * 0.40))
                if y + card_h > canvas_height - 20:
                    y = max(20, canvas_height - card_h - 20)
            overlay_pngs.append({
                "png_path": png_path,
                "start_s": ov.get("start_s", 0),
                "end_s": ov.get("end_s", 5),
                "x": x,
                "y": y,
            })

        if not overlay_pngs:
            shutil.copy2(input_video, output)
            return

        # Build single ffmpeg command with all overlay inputs
        inputs = ["-i", input_video]
        for op in overlay_pngs:
            inputs.extend(["-i", op["png_path"]])

        # Build filter chain: overlay each PNG with enable timing
        filter_parts = []
        prev_label = "0:v"
        for i, op in enumerate(overlay_pngs):
            inp_idx = i + 1
            out_label = f"v{i}" if i < len(overlay_pngs) - 1 else "vout"
            x = op["x"]
            y = op["y"]
            enable = f"'between(t,{op['start_s']},{op['end_s']})'"
            filter_parts.append(
                f"[{prev_label}][{inp_idx}:v]overlay={x}:{y}:enable={enable}[{out_label}]"
            )
            prev_label = out_label

        filter_complex = ";".join(filter_parts)

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[vout]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
            "-level", "4.0",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output,
        ]

        logger.info("Running product overlay compositor: %d overlays", len(overlay_pngs))
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
            logger.error("FFmpeg product overlay failed: %s", error_msg)
            raise RuntimeError(f"FFmpeg product overlay failed (rc={proc.returncode}): {error_msg}")

        logger.info("Product overlay composite complete: %s", output)
    finally:
        import shutil as _shutil
        _shutil.rmtree(tmpdir, ignore_errors=True)


async def composite_pip_avatar(
    base: str,
    pip_clip: str,
    x: int,
    y: int,
    width: int,
    height: int,
    output: str,
    shape: str = "rect",
) -> None:
    """Scale pip_clip to width×height and overlay at (x, y) on base video.

    Args:
        base: Path to local base video file (user/background video).
        pip_clip: Path to local PIP avatar video clip.
        x, y: Top-left position of PIP on the canvas.
        width, height: Target PIP dimensions (pixels).
        output: Path for the output video file.
        shape: "rect" for rectangular PIP, "circle" for circular mask.
    """
    # Ensure even dimensions
    width = max(width // 2 * 2, 2)
    height = max(height // 2 * 2, 2)

    if shape == "circle":
        # Circular PIP using geq alpha mask
        # Scale PIP, then apply circular alpha, then overlay
        filter_complex = (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
            f"format=rgba,"
            f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)'"
            f":a='if(lte(hypot(X-{width}/2,Y-{height}/2),{min(width,height)}/2),255,0)'[pip];"
            f"[0:v][pip]overlay={x}:{y}:format=auto"
        )
    else:
        # Rectangular PIP
        filter_complex = (
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[pip];"
            f"[0:v][pip]overlay={x}:{y}:format=auto"
        )

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", base,
        "-i", pip_clip,
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.0",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output,
    ]

    logger.info("Running PIP avatar compositor: shape=%s, pos=(%d,%d), size=%dx%d", shape, x, y, width, height)
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
        logger.error("FFmpeg PIP avatar failed: %s", error_msg)
        raise RuntimeError(f"FFmpeg PIP avatar failed (rc={proc.returncode}): {error_msg}")

    logger.info("PIP avatar composite complete: %s", output)


async def trim_user_video_segment(
    src_path: str,
    media_offset: float,
    duration: float,
    output: str,
) -> None:
    """Trim a user video segment using FFmpeg seek + duration.

    Args:
        src_path: Path to the source video file.
        media_offset: Start offset in seconds (maps to ffmpeg -ss).
        duration: Duration in seconds to extract.
        output: Path for the trimmed output file.
    """
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-ss", str(media_offset),
        "-i", src_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.0",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output,
    ]

    logger.info("Trimming user video: offset=%.2fs, duration=%.2fs", media_offset, duration)
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "Unknown FFmpeg error"
        logger.error("FFmpeg trim failed: %s", error_msg)
        raise RuntimeError(f"FFmpeg trim failed (rc={proc.returncode}): {error_msg}")

    logger.info("User video trim complete: %s", output)

"""Background compositing for avatar videos.

Two paths:
- PATH A (pre-InfiniteTalk): Static backgrounds (color, image, gradient).
  Composites face onto background as a single image → feeds to InfiniteTalk.
  Uses rembg once on a single image (~2-3 sec).

- PATH B (post-InfiniteTalk): Video/animated backgrounds (video, blur).
  Extracts person per-frame via rembg → composites onto video background.
  Slower (~50-80 sec for 10-sec clip) but supports dynamic backgrounds.
"""

import asyncio
import io
import logging
import os
import subprocess
import tempfile
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CDN_BASE = "https://media.luminacast.com"


# ── PATH A: Pre-InfiniteTalk (static backgrounds) ──


async def composite_face_on_background(
    face_image_url: str,
    bg_type: str,
    bg_color: str = "#1a1a2e",
    bg_image_url: str = "",
    gradient: dict = None,
    output_path: str = "",
) -> str:
    """Remove face image background and place person on new static background.

    Returns path to composited image file.
    This runs ONCE before InfiniteTalk submission — not per frame.
    """
    from PIL import Image, ImageDraw, ImageColor

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(face_image_url)
        resp.raise_for_status()
        face_bytes = resp.content

    # Remove background from face image (single image, fast ~2-3s)
    try:
        from rembg import remove as rembg_remove
        face_no_bg = rembg_remove(face_bytes)
        face_img = Image.open(io.BytesIO(face_no_bg)).convert("RGBA")
    except ImportError:
        logger.warning("rembg not installed, using original face image without bg removal")
        face_img = Image.open(io.BytesIO(face_bytes)).convert("RGBA")

    w, h = face_img.size

    if bg_type == "color":
        bg = Image.new("RGBA", (w, h), bg_color)

    elif bg_type == "image":
        if not bg_image_url:
            bg = Image.new("RGBA", (w, h), "#1a1a2e")
        else:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(bg_image_url)
                resp.raise_for_status()
                bg = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                bg = bg.resize((w, h), Image.LANCZOS)

    elif bg_type == "gradient":
        g = gradient or {"from": "#667eea", "to": "#764ba2", "direction": "vertical"}
        bg = _create_gradient_pil(w, h, g.get("from", "#667eea"), g.get("to", "#764ba2"), g.get("direction", "vertical"))

    else:
        # Default: return original face image unchanged
        with open(output_path, "wb") as f:
            f.write(face_bytes)
        return output_path

    # Composite: background + person with alpha
    bg.paste(face_img, (0, 0), face_img)

    # Save as JPEG (InfiniteTalk input)
    result = bg.convert("RGB")
    result.save(output_path, "JPEG", quality=95)

    logger.info(f"Pre-composited face on {bg_type} background: {output_path}")
    return output_path


def _create_gradient_pil(w: int, h: int, color_from: str, color_to: str, direction: str):
    """Create a gradient image using PIL with line drawing (fast)."""
    from PIL import Image, ImageDraw, ImageColor

    img = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(img)

    c1 = ImageColor.getrgb(color_from)
    c2 = ImageColor.getrgb(color_to)

    for y in range(h):
        if direction == "horizontal":
            ratio = 0  # Will compute per-pixel below
        elif direction == "diagonal":
            ratio = y / h
        else:  # vertical (default)
            ratio = y / h

        if direction == "horizontal":
            for x in range(w):
                ratio = x / w
                r = int(c1[0] + (c2[0] - c1[0]) * ratio)
                g = int(c1[1] + (c2[1] - c1[1]) * ratio)
                b = int(c1[2] + (c2[2] - c1[2]) * ratio)
                draw.point((x, y), fill=(r, g, b, 255))
        else:
            r = int(c1[0] + (c2[0] - c1[0]) * ratio)
            g = int(c1[1] + (c2[1] - c1[1]) * ratio)
            b = int(c1[2] + (c2[2] - c1[2]) * ratio)
            draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    return img


# ── PATH B: Post-InfiniteTalk (video/animated backgrounds) ──


async def replace_background(
    avatar_video_url: str,
    output_key: str,
    bg_type: str = "original",
    bg_color: str = "#1a1a2e",
    bg_image_url: str = "",
    bg_video_url: str = "",
    blur_strength: int = 20,
    gradient: dict = None,
    animation: str = "none",
    ken_burns_speed: float = 0.03,
) -> dict:
    """Remove avatar background and composite onto new background.

    Pipeline:
    1. Extract person mask video using rembg (per-frame)
    2. Use FFmpeg alphamerge to composite person onto new background
    """
    if bg_type == "original":
        return {"video_key": "", "skipped": True}

    with tempfile.TemporaryDirectory(prefix="bg_replace_") as tmpdir:
        video_path = os.path.join(tmpdir, "avatar.mp4")
        output_path = os.path.join(tmpdir, "bg_replaced.mp4")

        # Download avatar video
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(avatar_video_url)
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(resp.content)

        # Get video dimensions and duration
        probe = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_out, _ = await probe.communicate()
        lines = probe_out.decode().strip().split("\n")
        # First line: width,height  Second line: duration
        vid_w, vid_h = 480, 854
        duration = 10.0
        try:
            wh = lines[0].split(",")
            vid_w, vid_h = int(wh[0]), int(wh[1])
            if len(lines) > 1:
                duration = float(lines[1])
        except (ValueError, IndexError):
            pass

        # Extract person mask
        mask_path = os.path.join(tmpdir, "mask.mp4")
        _extract_person_mask_video(video_path, mask_path, tmpdir)

        cmd = None

        if bg_type == "blur":
            filter_complex = (
                f"[0:v]boxblur={blur_strength}:1[blurred];"
                f"[0:v][2:v]alphamerge[person];"
                f"[blurred][person]overlay=0:0"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", video_path,
                "-i", mask_path,
                "-filter_complex", filter_complex,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy", "-movflags", "+faststart",
                output_path,
            ]

        elif bg_type == "color":
            filter_complex = (
                f"color=c='{bg_color}':s={vid_w}x{vid_h}:d={duration}:r=25[bg];"
                f"[1:v][2:v]alphamerge[person];"
                f"[bg][person]overlay=0:0:shortest=1"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", video_path,
                "-i", mask_path,
                "-filter_complex", filter_complex,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy", "-movflags", "+faststart",
                output_path,
            ]

        elif bg_type == "image":
            bg_img_path = os.path.join(tmpdir, "bg.jpg")
            if bg_image_url:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    resp = await client.get(bg_image_url)
                    resp.raise_for_status()
                    with open(bg_img_path, "wb") as f:
                        f.write(resp.content)
            else:
                return {"video_key": "", "skipped": True}

            # Ken Burns or static
            if animation == "ken_burns":
                bg_filter = (
                    f"[0:v]scale={vid_w * 2}:{vid_h * 2},"
                    f"zoompan=z='min(zoom+{ken_burns_speed},1.5)':d={int(duration * 25)}:"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"s={vid_w}x{vid_h}:fps=25[bg]"
                )
            elif animation == "slow_zoom":
                bg_filter = (
                    f"[0:v]scale={vid_w * 2}:{vid_h * 2},"
                    f"zoompan=z='min(zoom+0.001,1.2)':d={int(duration * 25)}:"
                    f"s={vid_w}x{vid_h}:fps=25[bg]"
                )
            else:
                bg_filter = f"[0:v]scale={vid_w}:{vid_h}:force_original_aspect_ratio=increase,crop={vid_w}:{vid_h}[bg]"

            filter_complex = (
                f"{bg_filter};"
                f"[2:v][3:v]alphamerge[person];"
                f"[bg][person]overlay=0:0:shortest=1"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", bg_img_path,
                "-i", video_path,
                "-i", video_path,
                "-i", mask_path,
                "-filter_complex", filter_complex,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy", "-movflags", "+faststart",
                "-t", str(duration),
                output_path,
            ]

        elif bg_type == "video":
            bg_vid_path = os.path.join(tmpdir, "bg_video.mp4")
            if bg_video_url:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(bg_video_url)
                    resp.raise_for_status()
                    with open(bg_vid_path, "wb") as f:
                        f.write(resp.content)
            else:
                return {"video_key": "", "skipped": True}

            filter_complex = (
                f"[0:v]scale={vid_w}:{vid_h}:force_original_aspect_ratio=increase,crop={vid_w}:{vid_h}[bg];"
                f"[2:v][3:v]alphamerge[person];"
                f"[bg][person]overlay=0:0:shortest=1"
            )
            cmd = [
                "ffmpeg", "-y",
                "-stream_loop", "-1",
                "-i", bg_vid_path,
                "-i", video_path,
                "-i", video_path,
                "-i", mask_path,
                "-filter_complex", filter_complex,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy", "-movflags", "+faststart",
                "-t", str(duration),
                output_path,
            ]

        elif bg_type == "gradient":
            g = gradient or {"from": "#667eea", "to": "#764ba2", "direction": "vertical"}
            gradient_path = os.path.join(tmpdir, "gradient.png")
            _create_gradient_image_file(gradient_path, vid_w, vid_h, g.get("from", "#667eea"), g.get("to", "#764ba2"), g.get("direction", "vertical"))

            filter_complex = (
                f"[0:v]scale={vid_w}:{vid_h}[bg];"
                f"[2:v][3:v]alphamerge[person];"
                f"[bg][person]overlay=0:0:shortest=1"
            )
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", gradient_path,
                "-i", video_path,
                "-i", video_path,
                "-i", mask_path,
                "-filter_complex", filter_complex,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy", "-movflags", "+faststart",
                "-t", str(duration),
                output_path,
            ]

        else:
            return {"video_key": "", "skipped": True}

        logger.info(f"Background replacement ({bg_type}): running FFmpeg...")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "Unknown"
            logger.error(f"Background replacement failed: {error_msg}")
            raise RuntimeError(f"Background replacement failed: {error_msg}")

        # Upload to R2
        from services.r2_storage import get_r2_storage_service, IMMUTABLE_CACHE_CONTROL
        r2 = get_r2_storage_service()
        with open(output_path, "rb") as f:
            output_bytes = f.read()
        await r2.upload_bytes(output_bytes, output_key, content_type="video/mp4", cache_control=IMMUTABLE_CACHE_CONTROL)

        logger.info(f"Background replaced ({bg_type}): {output_key}")
        return {
            "video_key": output_key,
            "video_url": f"{CDN_BASE}/{output_key}",
        }


def _extract_person_mask_video(input_video: str, output_mask: str, tmpdir: str):
    """Extract alpha mask video from avatar video using rembg per-frame."""
    frames_dir = os.path.join(tmpdir, "frames")
    masks_dir = os.path.join(tmpdir, "masks")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(masks_dir, exist_ok=True)

    # Extract frames
    subprocess.run([
        "ffmpeg", "-y", "-i", input_video,
        "-vf", "fps=25",
        f"{frames_dir}/frame_%05d.png"
    ], capture_output=True, timeout=60)

    # Process each frame with rembg
    try:
        from rembg import remove, new_session
        session = new_session("u2net")
    except ImportError:
        logger.error("rembg not installed — cannot extract person mask")
        # Create a white mask (no background removal) as fallback
        subprocess.run([
            "ffmpeg", "-y", "-i", input_video,
            "-vf", "fps=25,format=gray,geq=lum=255",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            output_mask,
        ], capture_output=True, timeout=60)
        return

    frame_files = sorted(os.listdir(frames_dir))
    for fname in frame_files:
        frame_path = os.path.join(frames_dir, fname)
        mask_path = os.path.join(masks_dir, fname)

        with open(frame_path, "rb") as f:
            input_data = f.read()

        output_data = remove(
            input_data,
            session=session,
            only_mask=True,
        )

        with open(mask_path, "wb") as f:
            f.write(output_data)

    # Reassemble mask frames into video
    subprocess.run([
        "ffmpeg", "-y",
        "-framerate", "25",
        "-i", f"{masks_dir}/frame_%05d.png",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        output_mask,
    ], capture_output=True, timeout=120)


def _create_gradient_image_file(output_path: str, width: int, height: int, color_from: str, color_to: str, direction: str):
    """Create a gradient image file using PIL."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)

    r1, g1, b1 = int(color_from[1:3], 16), int(color_from[3:5], 16), int(color_from[5:7], 16)
    r2, g2, b2 = int(color_to[1:3], 16), int(color_to[3:5], 16), int(color_to[5:7], 16)

    for y in range(height):
        ratio = y / height
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    img.save(output_path)

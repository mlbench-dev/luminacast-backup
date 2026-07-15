"""Generate a poster thumbnail from a completed render video.

Picks the frame at ~10% into the duration (avoids black fade-in) and uploads a 480x854 JPEG.
Non-blocking: returns None on failure so rendering is never blocked by thumbnail errors.
"""
import asyncio
import logging
import subprocess
import tempfile
import uuid
from functools import partial
from pathlib import Path

import sentry_sdk

from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)


async def generate_and_upload_thumbnail(
    video_r2_key: str,
    render_id: str,
    duration_seconds: float | None = None,
) -> str | None:
    """Extract a thumbnail from the rendered video, upload to R2, return the key.

    Returns None on failure (non-blocking).
    """
    r2 = get_r2_storage_service()
    try:
        with tempfile.TemporaryDirectory() as td:
            video_path = Path(td) / "in.mp4"
            thumb_path = Path(td) / "thumb.jpg"

            # Download video from R2 to local temp
            await r2.download_file(video_r2_key, str(video_path))

            # Pick time: 10% into duration, clamped to 1.0s min
            seek = max(1.0, (duration_seconds or 10.0) * 0.1)

            # Extract one frame, scale to 480x854 (9:16 poster size) preserving aspect
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, partial(
                subprocess.run,
                [
                    "ffmpeg", "-y",
                    "-ss", str(seek),
                    "-i", str(video_path),
                    "-vframes", "1",
                    "-vf", "scale='if(gt(a,9/16),480,-2)':'if(gt(a,9/16),-2,854)',crop=480:854",
                    "-q:v", "4",  # JPEG quality — low enough to stay small
                    str(thumb_path),
                ],
                **{"check": True, "capture_output": True, "timeout": 30},
            ))

            if not thumb_path.exists():
                logger.warning("Thumbnail extraction produced no output for render %s", render_id)
                return None

            thumb_key = f"casts/renders/{render_id}/thumb_{uuid.uuid4().hex[:8]}.jpg"
            thumb_bytes = thumb_path.read_bytes()
            await r2.upload_bytes(
                thumb_bytes,
                thumb_key,
                content_type="image/jpeg",
                cache_control="public, max-age=31536000",
            )
            logger.info("Thumbnail uploaded for render %s: %s (%d bytes)", render_id, thumb_key, len(thumb_bytes))
            return thumb_key
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("Thumbnail generation failed for render %s: %s", render_id, e)
        return None

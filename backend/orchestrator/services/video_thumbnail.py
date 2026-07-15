"""Extract a JPEG thumbnail from a video using ffmpeg.

Used by the user-video upload flow (and the backfill script) to create a
small library tile image so consumers don't need to render the first frame
of an MP4 client-side.
"""
import asyncio
import logging
import os
import tempfile

import sentry_sdk

logger = logging.getLogger(__name__)

_FFMPEG_TIMEOUT_S = 30


async def _run_ffmpeg_to_jpeg(
    input_path: str, timestamp_s: float, quality: int
) -> bytes:
    """Run ffmpeg against a path and return JPEG bytes from stdout."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{timestamp_s:.3f}",
        "-i",
        input_path,
        "-vframes",
        "1",
        "-q:v",
        str(quality),
        "-f",
        "mjpeg",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_FFMPEG_TIMEOUT_S
        )
    except asyncio.TimeoutError as e:
        try:
            proc.kill()
        except Exception as kill_err:
            sentry_sdk.capture_exception(kill_err)
        sentry_sdk.capture_exception(e)
        raise
    if proc.returncode != 0:
        err_tail = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise RuntimeError(
            f"ffmpeg exited {proc.returncode}: {err_tail}"
        )
    return stdout or b""


async def extract_video_thumbnail_jpeg(
    video_bytes: bytes,
    *,
    timestamp_s: float = 0.5,
    quality: int = 2,
    suffix: str = ".mp4",
) -> bytes:
    """Return a JPEG thumbnail extracted from the given video bytes.

    Writes the input to a temp file (more reliable than stdin for mov/m4v
    containers), runs ffmpeg, returns the JPEG payload. On any failure the
    exception is captured via Sentry and an empty bytes object is returned
    so the caller can decide whether to fall back.

    The ffmpeg subprocess is capped at 30s.
    """
    if not video_bytes:
        return b""

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        try:
            return await _run_ffmpeg_to_jpeg(tmp_path, timestamp_s, quality)
        except RuntimeError as e:
            # Likely too-short input for the requested seek — retry at 0.
            if timestamp_s > 0:
                try:
                    return await _run_ffmpeg_to_jpeg(tmp_path, 0.0, quality)
                except Exception as retry_err:
                    sentry_sdk.capture_exception(retry_err)
                    logger.warning(
                        "thumbnail extraction retry failed: %s", retry_err
                    )
                    return b""
            sentry_sdk.capture_exception(e)
            logger.warning("thumbnail extraction failed: %s", e)
            return b""
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("thumbnail extraction error: %s", e)
        return b""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception as e:
                sentry_sdk.capture_exception(e)

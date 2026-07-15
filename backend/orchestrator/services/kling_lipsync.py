"""Kling LipSync — add mouth sync to any video via fal.ai."""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)


async def apply_lipsync(
    video_url: str,
    audio_url: str,
) -> dict:
    """
    Apply Kling LipSync to a video, syncing mouth movements to audio.

    Args:
        video_url: Public URL of the input video (e.g. Wan output)
        audio_url: Public URL of the TTS audio

    Returns:
        {"video_url": str, "duration_seconds": float}
    """
    from config import settings
    import fal_client

    if not os.environ.get("FAL_KEY") and settings.FAL_API_KEY:
        os.environ["FAL_KEY"] = settings.FAL_API_KEY

    def call_kling_lipsync():
        return fal_client.subscribe(
            "fal-ai/kling-video/lipsync/audio-to-video",
            arguments={
                "video_url": video_url,
                "audio_url": audio_url,
            },
        )

    logger.info("Kling LipSync: video=%s, audio=%s", video_url[:80], audio_url[:80])
    result = await asyncio.to_thread(call_kling_lipsync)

    video_out = None
    if isinstance(result, dict):
        video = result.get("video")
        if isinstance(video, dict):
            video_out = video.get("url")
        elif isinstance(video, str):
            video_out = video

    if not video_out:
        raise RuntimeError(f"Kling LipSync returned no video: {str(result)[:300]}")

    duration = 0.0
    if isinstance(result, dict):
        video_data = result.get("video")
        if isinstance(video_data, dict) and "duration" in video_data:
            duration = float(video_data["duration"])

    logger.info("Kling LipSync complete: %s (%.1fs)", video_out[:80], duration)
    return {"video_url": video_out, "duration_seconds": duration}

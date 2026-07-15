"""Wan 2.2 text-to-video (A14B) and image-to-video (5B) client
for the Generated Videos feature.

Scoped to the My Videos | Photos Generated sub-folder feature only.

Endpoint mapping (confirmed via Phase A smoke tests):
  - T2V: fal-ai/wan/v2.2-a14b/text-to-video  (A14B model)
  - I2V: fal-ai/wan/v2.2-5b/image-to-video    (5B model)

Wan has NO camera control parameters — all camera motion is prompt-driven.
Wan uses num_frames instead of seconds: 81 frames ≈ 5s, 161 frames ≈ 10s.
Wan supports seed for reproducibility.
"""
import asyncio
import logging
import os
from typing import Literal

import fal_client
import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)

AspectRatio = Literal["16:9", "9:16", "1:1"]
Duration = Literal[5, 10]

# Duration-to-frames mapping for Wan (at default fps)
DURATION_TO_FRAMES = {5: 81, 10: 161}


class WanTTI2VClient:
    def __init__(self):
        if not os.environ.get("FAL_KEY") and getattr(settings, "FAL_API_KEY", None):
            os.environ["FAL_KEY"] = settings.FAL_API_KEY

    async def text_to_video(
        self,
        prompt: str,
        duration: Duration = 5,
        aspect_ratio: AspectRatio = "16:9",
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> bytes:
        """Generate one video from a text prompt. Returns MP4 bytes."""
        arguments: dict = {
            "prompt": prompt,
            "num_frames": DURATION_TO_FRAMES.get(duration, 81),
            "resolution": "720p",
            "aspect_ratio": aspect_ratio,
            "enable_safety_checker": False,
        }
        if negative_prompt:
            arguments["negative_prompt"] = negative_prompt
        if seed is not None:
            arguments["seed"] = seed

        logger.info(
            "Wan T2V call: prompt_len=%d duration=%s frames=%d aspect=%s seed=%s",
            len(prompt), duration, arguments["num_frames"], aspect_ratio, seed,
        )

        try:
            result = await asyncio.to_thread(
                fal_client.subscribe,
                "fal-ai/wan/v2.2-a14b/text-to-video",
                arguments=arguments,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

        return await self._download(result)

    async def image_to_video(
        self,
        reference_image_url: str,
        prompt: str,
        duration: Duration = 5,
        aspect_ratio: AspectRatio = "16:9",
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> bytes:
        """Generate one video from a reference image + motion prompt. Returns MP4 bytes."""
        arguments: dict = {
            "image_url": reference_image_url,
            "prompt": prompt,
            "num_frames": DURATION_TO_FRAMES.get(duration, 81),
            "resolution": "720p",
            "aspect_ratio": aspect_ratio,
            "enable_safety_checker": False,
        }
        if negative_prompt:
            arguments["negative_prompt"] = negative_prompt
        if seed is not None:
            arguments["seed"] = seed

        logger.info(
            "Wan I2V call: image_url=%s duration=%s frames=%d aspect=%s seed=%s",
            reference_image_url[:80], duration, arguments["num_frames"], aspect_ratio, seed,
        )

        try:
            result = await asyncio.to_thread(
                fal_client.subscribe,
                "fal-ai/wan/v2.2-5b/image-to-video",
                arguments=arguments,
            )
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

        return await self._download(result)

    async def _download(self, fal_result: dict) -> bytes:
        """Download the video from the fal.ai output URL."""
        video_url = fal_result.get("video", {}).get("url")
        if not video_url:
            raise RuntimeError(f"Wan returned no video URL: {str(fal_result)[:300]}")

        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.get(video_url)
            r.raise_for_status()
            return r.content

"""Kling v2.1 text-to-video (Master) and image-to-video (Pro) client
for the Generated Videos feature.

Scoped to the My Videos | Photos Generated sub-folder feature only.
Not reused by the avatar acting video pipeline — that uses a separate client.

Endpoint mapping (confirmed via Phase A smoke tests):
  - T2V: fal-ai/kling-video/v2.1/master/text-to-video (Master tier)
  - I2V: fal-ai/kling-video/v2.1/pro/image-to-video   (Pro tier)

Camera control is available on T2V only, as a string enum.
Kling does not expose a seed parameter.
Duration is a string: "5" or "10".
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

# Camera control presets — confirmed from Phase A.5 smoke test.
# Kling v2.1 Master T2V accepts camera_control as a string enum.
KLING_CAMERA_PRESETS = {
    "none": None,
    "down_back": "down_back",
    "forward_up": "forward_up",
    "right_turn_forward": "right_turn_forward",
    "left_turn_forward": "left_turn_forward",
}


class KlingTTI2VClient:
    def __init__(self):
        if not os.environ.get("FAL_KEY") and getattr(settings, "FAL_API_KEY", None):
            os.environ["FAL_KEY"] = settings.FAL_API_KEY

    async def text_to_video(
        self,
        prompt: str,
        duration: Duration = 5,
        aspect_ratio: AspectRatio = "16:9",
        negative_prompt: str | None = None,
        camera_preset: str | None = None,
        seed: int | None = None,
    ) -> bytes:
        """Generate one video from a text prompt. Returns MP4 bytes.

        Note: Kling does not support seed. The parameter is accepted
        for interface compatibility but silently ignored.
        """
        arguments: dict = {
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
        }
        if negative_prompt:
            arguments["negative_prompt"] = negative_prompt
        if camera_preset and camera_preset != "none":
            camera_value = KLING_CAMERA_PRESETS.get(camera_preset)
            if camera_value:
                arguments["camera_control"] = camera_value

        logger.info(
            "Kling T2V call: prompt_len=%d duration=%s aspect=%s camera=%s",
            len(prompt), duration, aspect_ratio, camera_preset,
        )

        try:
            result = await asyncio.to_thread(
                fal_client.subscribe,
                "fal-ai/kling-video/v2.1/master/text-to-video",
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
        camera_preset: str | None = None,
        seed: int | None = None,
    ) -> bytes:
        """Generate one video from a reference image + motion prompt. Returns MP4 bytes.

        Note: Kling I2V does not support camera_control — camera motion
        is prompt-driven. camera_preset is silently ignored for I2V.
        Seed is also not supported by Kling.
        """
        arguments: dict = {
            "image_url": reference_image_url,
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
        }
        if negative_prompt:
            arguments["negative_prompt"] = negative_prompt

        logger.info(
            "Kling I2V call: image_url=%s duration=%s aspect=%s",
            reference_image_url[:80], duration, aspect_ratio,
        )

        try:
            result = await asyncio.to_thread(
                fal_client.subscribe,
                "fal-ai/kling-video/v2.1/pro/image-to-video",
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
            raise RuntimeError(f"Kling returned no video URL: {str(fal_result)[:300]}")

        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.get(video_url)
            r.raise_for_status()
            return r.content

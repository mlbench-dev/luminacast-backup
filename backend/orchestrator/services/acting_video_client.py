"""Client for generating acting-block image-to-video clips via Kling 2.5 Turbo Pro on fal.ai."""
import logging
import os

import fal_client
import sentry_sdk

logger = logging.getLogger(__name__)

# Kling 2.5 Turbo Pro — best quality/cost balance for avatar acting clips
# Supports first+last frame via `tail_image_url`
KLING_ENDPOINT = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"

# Cost: ~$0.35 per 5s clip, ~$0.70 per 10s
COST_PER_SECOND_USD = 0.07


class ActingVideoClient:
    def __init__(self, fal_api_key: str):
        if not os.environ.get("FAL_KEY"):
            os.environ["FAL_KEY"] = fal_api_key

    async def generate(
        self,
        first_frame_url: str,
        last_frame_url: str | None,
        prompt: str,
        duration_seconds: int = 5,
        aspect_ratio: str = "9:16",
    ) -> dict:
        """
        Generate an acting video clip using Kling 2.5 Turbo Pro.

        Returns: {"video_url": str, "duration_seconds": float, "engine": str, "cost_usd": float}
        """
        if duration_seconds > 10:
            raise ValueError("Acting video clips are capped at 10 seconds")

        arguments = {
            "prompt": prompt,
            "image_url": first_frame_url,
            "duration": str(duration_seconds),
            "aspect_ratio": aspect_ratio,
        }
        if last_frame_url:
            arguments["tail_image_url"] = last_frame_url

        try:
            result = await fal_client.run_async(KLING_ENDPOINT, arguments=arguments)
            video_url = result["video"]["url"]
            cost_usd = round(COST_PER_SECOND_USD * duration_seconds, 4)

            logger.info(
                "Kling acting video generated: duration=%ds, cost=$%.4f",
                duration_seconds,
                cost_usd,
            )

            return {
                "video_url": video_url,
                "duration_seconds": float(duration_seconds),
                "engine": "kling_2.5_turbo_pro",
                "cost_usd": cost_usd,
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error("Kling acting video generation failed: %s", e)
            raise

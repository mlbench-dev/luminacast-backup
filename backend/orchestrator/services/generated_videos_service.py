"""Generated videos service — Kling v2.1 + Wan 2.2 via fal.ai.

Scoped to the My Videos | Photos Generated sub-folder feature only.
Called by exactly one endpoint: POST /api/videos/generate.
"""
import asyncio
import logging
import uuid
from typing import Literal

import sentry_sdk

from services.kling_tti2v_client import KlingTTI2VClient
from services.wan_tti2v_client import WanTTI2VClient
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)

Engine = Literal["kling", "wan"]
Mode = Literal["text_to_video", "image_to_video"]


class GeneratedVideosService:
    def __init__(self):
        self.kling = KlingTTI2VClient()
        self.wan = WanTTI2VClient()

    async def generate_and_store(
        self,
        user_id: str,
        engine: Engine,
        mode: Mode,
        prompt: str,
        reference_image_url: str | None = None,
        duration: int = 5,
        aspect_ratio: str = "16:9",
        negative_prompt: str | None = None,
        seed: int | None = None,
        camera_preset: str | None = None,
        num_videos: int = 1,
    ) -> dict:
        """Generate N videos with the selected engine + mode.

        Returns {
            'r2_keys': list[str],
            'r2_urls': list[str],
            'engine_used': str,
            'mode': str,
            'cost_usd': float,
            'duration_seconds': int,
        }
        """
        if mode == "image_to_video" and not reference_image_url:
            raise ValueError("image_to_video mode requires reference_image_url")
        if engine == "wan" and camera_preset and camera_preset != "none":
            logger.warning("camera_preset=%s ignored for Wan engine", camera_preset)
            camera_preset = None

        tasks = []
        for i in range(num_videos):
            call_seed = seed + i if seed is not None else None
            if engine == "kling":
                if mode == "text_to_video":
                    tasks.append(self.kling.text_to_video(
                        prompt=prompt,
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        negative_prompt=negative_prompt,
                        camera_preset=camera_preset,
                        seed=call_seed,
                    ))
                else:
                    tasks.append(self.kling.image_to_video(
                        reference_image_url=reference_image_url,
                        prompt=prompt,
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        negative_prompt=negative_prompt,
                        camera_preset=camera_preset,
                        seed=call_seed,
                    ))
            else:
                if mode == "text_to_video":
                    tasks.append(self.wan.text_to_video(
                        prompt=prompt,
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        negative_prompt=negative_prompt,
                        seed=call_seed,
                    ))
                else:
                    tasks.append(self.wan.image_to_video(
                        reference_image_url=reference_image_url,
                        prompt=prompt,
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        negative_prompt=negative_prompt,
                        seed=call_seed,
                    ))

        videos_bytes = await asyncio.gather(*tasks, return_exceptions=True)

        successful = []
        for i, result in enumerate(videos_bytes):
            if isinstance(result, Exception):
                logger.error("Video %d failed: %s", i, result)
                sentry_sdk.capture_exception(result)
                continue
            successful.append(result)

        if not successful:
            raise RuntimeError("All video generation calls failed")

        r2 = get_r2_storage_service()
        r2_keys, r2_urls = [], []
        for video_bytes in successful:
            key = f"users/{user_id}/generated_videos/{uuid.uuid4().hex}.mp4"
            await r2.upload_bytes(video_bytes, key, "video/mp4")
            r2_keys.append(key)
            r2_urls.append(r2.get_public_url(key))

        per_video_cost = self._cost_per_video(engine, duration)
        total_cost = per_video_cost * len(successful)

        return {
            "r2_keys": r2_keys,
            "r2_urls": r2_urls,
            "engine_used": f"fal_kling_v21_pro" if engine == "kling" else "fal_wan_v22",
            "mode": mode,
            "cost_usd": total_cost,
            "duration_seconds": duration,
        }

    def _cost_per_video(self, engine: Engine, duration: int) -> float:
        """Cost estimates from Phase A audit."""
        if engine == "kling":
            return {5: 0.35, 10: 0.70}.get(duration, 0.35)
        else:
            return {5: 0.25, 10: 0.50}.get(duration, 0.25)

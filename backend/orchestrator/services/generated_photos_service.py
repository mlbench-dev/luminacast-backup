"""Generated photos service — three-tier image generation for the
My Videos | Photos Generated sub-folder feature.

Scoped to exactly one caller: POST /api/photos/generate.
"""
import logging
import os
import uuid

import httpx
import sentry_sdk

from services.sdxl_client import SDXLClient
from config import settings
from services.r2_storage import get_r2_storage_service

logger = logging.getLogger(__name__)


class GeneratedPhotosService:
    def __init__(self):
        self.sdxl_client = SDXLClient(settings.HOSTKEY_GPU_URL)
        if not os.environ.get("FAL_KEY") and getattr(settings, "FAL_API_KEY", None):
            os.environ["FAL_KEY"] = settings.FAL_API_KEY

    async def generate_and_store(
        self,
        user_id: str,
        prompt: str,
        negative_prompt: str | None = None,
        width: int = 1024,
        height: int = 1024,
        num_images: int = 1,
        seed: int | None = None,
    ) -> dict:
        """Three-tier fallback:
            Tier 1: self-hosted SDXL (RealVisXL 5.0)
            Tier 2: fal.ai fast-sdxl
            Tier 3: fal.ai flux-schnell
        """
        # Tier 1
        from services.hostkey_flags import hostkey_disabled, log_hostkey_skip

        tier1_ok = False
        if hostkey_disabled():
            log_hostkey_skip("fal.ai sdxl (generated photos)")
        else:
            try:
                tier1_ok = await self.sdxl_client.health()
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning(f"SDXL Tier 1 health check failed: {e}")
        if tier1_ok:
            try:
                logger.info(f"SDXL Tier 1: self-hosted, {num_images} images")
                images = await self.sdxl_client.generate(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    model_variant="realvisxl_5",
                    width=width,
                    height=height,
                    num_images=num_images,
                    seed=seed,
                )
                r2_keys, r2_urls = await self._upload(user_id, images)
                return {
                    "r2_keys": r2_keys,
                    "r2_urls": r2_urls,
                    "engine_used": "sdxl_self_hosted",
                    "tier": 1,
                    "cost_usd": self._estimate_local_cost(num_images, width, height),
                    "model_variant": "realvisxl_5",
                }
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning(f"SDXL Tier 1 failed: {e}")

        # Tier 2
        try:
            import fal_client

            logger.info("SDXL Tier 2: fal.ai fast-sdxl")
            result = await fal_client.run_async(
                "fal-ai/fast-sdxl",
                arguments={
                    "prompt": prompt,
                    "negative_prompt": negative_prompt or "",
                    "image_size": {"width": width, "height": height},
                    "num_images": num_images,
                    "num_inference_steps": 25,
                    "guidance_scale": 7.5,
                    "seed": seed,
                    "enable_safety_checker": False,
                },
            )
            images = await self._download_fal(result.get("images", []))
            r2_keys, r2_urls = await self._upload(user_id, images)
            return {
                "r2_keys": r2_keys,
                "r2_urls": r2_urls,
                "engine_used": "fal_fast_sdxl",
                "tier": 2,
                "cost_usd": 0.01 * num_images,
                "model_variant": "fal_fast_sdxl",
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.warning(f"SDXL Tier 2 failed: {e}")

        # Tier 3
        try:
            import fal_client

            logger.info("SDXL Tier 3: fal.ai flux-schnell")
            result = await fal_client.run_async(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": prompt,
                    "image_size": {"width": width, "height": height},
                    "num_images": num_images,
                    "num_inference_steps": 4,
                    "seed": seed,
                    "enable_safety_checker": False,
                },
            )
            images = await self._download_fal(result.get("images", []))
            r2_keys, r2_urls = await self._upload(user_id, images)
            return {
                "r2_keys": r2_keys,
                "r2_urls": r2_urls,
                "engine_used": "fal_flux_schnell",
                "tier": 3,
                "cost_usd": 0.003 * num_images,
                "model_variant": "fal_flux_schnell",
            }
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"All SDXL tiers failed: {e}")
            raise

    async def _upload(
        self, user_id: str, images: list[bytes]
    ) -> tuple[list[str], list[str]]:
        r2 = get_r2_storage_service()
        keys, urls = [], []
        for img in images:
            key = f"users/{user_id}/generated_photos/{uuid.uuid4().hex}.jpg"
            await r2.upload_bytes(img, key, "image/jpeg")
            keys.append(key)
            urls.append(r2.get_public_url(key))
        return keys, urls

    async def _download_fal(self, fal_images: list) -> list[bytes]:
        results = []
        async with httpx.AsyncClient(timeout=60) as client:
            for img in fal_images:
                try:
                    r = await client.get(img["url"])
                    r.raise_for_status()
                    results.append(r.content)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    raise
        return results

    def _estimate_local_cost(
        self, num_images: int, width: int, height: int
    ) -> float:
        seconds_per_image = (width * height / (1024 * 1024)) * 5.0
        total_seconds = seconds_per_image * num_images
        return round((total_seconds / 3600) * 0.50, 4)

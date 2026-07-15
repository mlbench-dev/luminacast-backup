"""Client for the HOSTKEY GPU worker's SDXL endpoint.

Scoped to the My Videos | Photos Generated sub-folder feature only.
"""
import base64
import logging
from typing import Literal

import httpx
import sentry_sdk

logger = logging.getLogger(__name__)
ModelVariant = Literal["sdxl_base", "realvisxl_5"]


class SDXLClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def health(self) -> bool:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{self.base_url}/api/health", timeout=5)
                return r.status_code == 200
            except Exception:
                return False

    async def generate(
        self,
        prompt: str,
        negative_prompt: str | None = None,
        model_variant: ModelVariant = "realvisxl_5",
        width: int = 1024,
        height: int = 1024,
        num_inference_steps: int = 25,
        guidance_scale: float | None = None,
        seed: int | None = None,
        num_images: int = 1,
    ) -> list[bytes]:
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "model_variant": model_variant,
            "width": width,
            "height": height,
            "num_inference_steps": num_inference_steps,
            "seed": seed,
            "num_images": num_images,
        }
        if guidance_scale is not None:
            payload["guidance_scale"] = guidance_scale

        timeout = 120 + num_images * 30
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/sdxl-generate", json=payload
                )
                r.raise_for_status()
                data = r.json()
                return [base64.b64decode(b) for b in data["images_base64"]]
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise

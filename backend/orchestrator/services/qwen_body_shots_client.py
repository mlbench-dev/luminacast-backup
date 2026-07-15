"""Client for the HOSTKEY GPU worker's Qwen Image Edit body shot endpoint."""
import logging
from typing import Literal

import httpx
import sentry_sdk

logger = logging.getLogger(__name__)

Angle = Literal[
    "front",
    "three_quarter_left",
    "three_quarter_right",
    "profile_left",
    "profile_right",
    "back",
]


class QwenBodyShotsClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def health(self) -> bool:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{self.base_url}/api/router/status", timeout=5)
                return r.status_code == 200
            except Exception:
                return False

    async def generate(
        self,
        reference_image_url: str,
        angle: Angle,
        output_width: int = 1024,
        output_height: int = 1792,
        seed: int = 42,
    ) -> bytes:
        """Generate a body shot at the given angle. Returns JPEG bytes."""
        payload = {
            "reference_image_url": reference_image_url,
            "angle": angle,
            "output_width": output_width,
            "output_height": output_height,
            "seed": seed,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            try:
                r = await client.post(
                    f"{self.base_url}/api/qwen-body-shot", json=payload
                )
                r.raise_for_status()
                return r.content
            except Exception as e:
                sentry_sdk.capture_exception(e)
                raise

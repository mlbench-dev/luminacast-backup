"""MuseTalk lip-sync client for the HOSTKEY GPU server."""
import logging
import asyncio
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class MuseTalkClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def is_healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/health")
                data = resp.json()
                return resp.status_code == 200 and data.get("status") == "ok"
        except Exception as e:
            logger.warning("MuseTalk health check failed: %s", e)
            return False

    async def submit_lipsync(
        self,
        face_image_url: str,
        audio_url: str,
        render_size: str = "240p",
    ) -> dict:
        payload = {
            "face_image_url": face_image_url,
            "audio_url": audio_url,
            "render_size": render_size,
            "fps": 25,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/api/musetalk-lipsync",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 409:
                    # Contention — retry once after 5s
                    logger.info("MuseTalk contention, retrying in 5s...")
                    await asyncio.sleep(5)
                    resp = await client.post(
                        f"{self.base_url}/api/musetalk-lipsync",
                        json=payload,
                    )
                    resp.raise_for_status()
                    return resp.json()
                raise


_client: Optional[MuseTalkClient] = None


def get_musetalk_client() -> MuseTalkClient:
    global _client
    if _client is None:
        from config import settings
        _client = MuseTalkClient(settings.GPU_SERVER_URL)
    return _client

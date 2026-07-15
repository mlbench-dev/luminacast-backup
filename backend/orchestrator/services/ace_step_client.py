"""Client for the HOSTKEY GPU worker's ACE-Step endpoints."""

import logging

import httpx
from config import settings

logger = logging.getLogger(__name__)


class ACEStepClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def generate_music(
        self,
        prompt: str,
        lyrics: str,
        duration_seconds: float,
        output_r2_key: str,
        lora_r2_key: str | None = None,
        seed: int = -1,
        guidance_scale: float = 15.0,
        inference_steps: int = 60,
        scheduler_type: str = "euler",
    ) -> dict:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                f"{self.base_url}/api/music-generate",
                json={
                    "prompt": prompt,
                    "lyrics": lyrics,
                    "duration_seconds": duration_seconds,
                    "output_r2_key": output_r2_key,
                    "lora_r2_key": lora_r2_key or "",
                    "seed": seed,
                    "guidance_scale": guidance_scale,
                    "num_inference_steps": inference_steps,
                    "scheduler_type": scheduler_type,
                },
            )
            if resp.status_code >= 400:
                body = resp.text[:2000]
                raise RuntimeError(f"GPU worker returned {resp.status_code}: {body}")
            return resp.json()

    async def train_lora(
        self,
        sound_cast_id: str,
        training_audio_r2_keys: list[str],
        training_prompts: list[str],
        output_r2_key: str,
        steps: int = 2400,
        learning_rate: float = 1e-4,
        rank: int = 128,
    ) -> dict:
        async with httpx.AsyncClient(timeout=7200) as client:
            resp = await client.post(
                f"{self.base_url}/api/music-train-lora",
                json={
                    "sound_cast_id": sound_cast_id,
                    "training_audio_r2_keys": training_audio_r2_keys,
                    "training_prompts": training_prompts,
                    "output_r2_key": output_r2_key,
                    "steps": steps,
                    "learning_rate": learning_rate,
                    "rank": rank,
                },
            )
            if resp.status_code >= 400:
                body = resp.text[:2000]
                raise RuntimeError(f"GPU worker returned {resp.status_code}: {body}")
            return resp.json()


_instance = None


def get_ace_step_client() -> ACEStepClient:
    global _instance
    if _instance is None:
        url = settings.GPU_SERVER_URL
        if not url:
            raise RuntimeError("GPU_SERVER_URL not configured")
        _instance = ACEStepClient(url)
    return _instance

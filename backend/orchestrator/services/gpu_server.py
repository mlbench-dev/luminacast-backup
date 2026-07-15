"""Dedicated GPU server client for BS-RoFormer and Fish Speech TTS.

Smart contention handling:
  - Checks /api/health before each job
  - If GPU is locked by a DIFFERENT model, returns immediately (3s skip)
    instead of waiting 300s for a timeout
  - Dynamic timeout: input_duration * render_ratio + model_load + safety_buffer

No InfiniteTalk — that runs on RunPod only.
"""

import logging

import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)

MODEL_LOAD_TIME = 30   # seconds for model swap
SAFETY_BUFFER = 60     # extra margin


class GPUServerClient:
    """HTTP client for the dedicated GPU server."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def is_healthy(self) -> bool:
        """Check if the GPU server is reachable (3 second timeout)."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/api/health")
                data = resp.json()
                return data.get("status") == "ok"
        except Exception:
            return False

    async def _check_contention(self, needed_model: str) -> None:
        """Check GPU health and contention. Raises if server is busy with a different model.

        This prevents waiting 5+ minutes for a timeout when the GPU is running
        InfiniteTalk or another heavy model — we skip to RunPod in ~3s instead.
        """
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/api/health")
                data = resp.json()

            if data.get("status") != "ok":
                raise ConnectionError("GPU server unhealthy")

            if data.get("gpu_locked"):
                loaded = data.get("loaded_model", "")
                if loaded and loaded != needed_model:
                    raise ConnectionError(
                        f"GPU locked by {loaded}, need {needed_model} — skip to fallback"
                    )
        except httpx.ConnectError:
            raise ConnectionError("GPU server unreachable, skip to fallback")

    async def bs_roformer(
        self, audio_url: str, output_key: str, max_duration: int = 90,
        input_duration_seconds: float = 60.0,
    ) -> dict:
        """Run BS-RoFormer vocal isolation on the GPU server.

        Returns dict with 'vocals_url' and optionally 'transcript'.
        """
        with sentry_sdk.start_span(op='gpu_server', description='BS-RoFormer') as span:
            span.set_data('input_duration', input_duration_seconds)
            span.set_data('server', self.base_url)
            await self._check_contention("bs_roformer")

            timeout = (
                input_duration_seconds * settings.BS_ROFORMER_RENDER_RATIO
                + MODEL_LOAD_TIME
                + SAFETY_BUFFER
            )
            timeout = max(timeout, 120.0)  # minimum 2 minutes
            span.set_data('timeout', timeout)
            logger.info(
                f"GPU bs_roformer: input={input_duration_seconds:.0f}s, "
                f"timeout={timeout:.0f}s (ratio={settings.BS_ROFORMER_RENDER_RATIO})"
            )

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/bs-roformer",
                    json={
                        "audio_url": audio_url,
                        "output_key": output_key,
                        "max_duration": max_duration,
                    },
                )
                resp.raise_for_status()
                span.set_data('status', 'success')
                return resp.json()

    async def fish_speech_tts(
        self, text: str, reference_audio_b64: str, format: str = "mp3",
        input_duration_seconds: float = 10.0,
    ) -> dict:
        """Run Fish Speech TTS on the GPU server.

        Returns dict with 'audio_base64'.
        """
        with sentry_sdk.start_span(op='gpu_server', description='Fish Speech TTS') as span:
            span.set_data('text_length', len(text))
            span.set_data('server', self.base_url)
            await self._check_contention("fish_speech")

            timeout = (
                input_duration_seconds * settings.FISH_SPEECH_RENDER_RATIO
                + MODEL_LOAD_TIME
                + SAFETY_BUFFER
            )
            timeout = max(timeout, 120.0)
            span.set_data('timeout', timeout)
            logger.info(
                f"GPU fish_speech: input={input_duration_seconds:.0f}s, "
                f"timeout={timeout:.0f}s (ratio={settings.FISH_SPEECH_RENDER_RATIO})"
            )

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/fish-speech",
                    json={
                        "text": text,
                        "reference_audio": [reference_audio_b64],
                        "format": format,
                    },
                )
                resp.raise_for_status()
                span.set_data('status', 'success')
                return resp.json()

    async def upscale_video(
        self, video_url: str, output_key: str, target_height: int = 720,
        enhance_faces: bool = False,
    ) -> dict:
        """Upscale a video via the GPU server. Returns dict with 'video_url'."""
        with sentry_sdk.start_span(op='gpu_server', description='Upscale Video') as span:
            span.set_data('target_height', target_height)
            await self._check_contention("upscale")
            timeout = 600.0  # 10 minutes max
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/upscale-video",
                    json={
                        "video_url": video_url,
                        "output_key": output_key,
                        "target_height": target_height,
                        "enhance_faces": enhance_faces,
                    },
                )
                resp.raise_for_status()
                return resp.json()

    async def whisper_transcribe(
        self, audio_url: str, language: str = "en", word_timestamps: bool = False,
    ) -> dict:
        """Run Whisper transcription on the GPU server.
        Returns dict with 'transcript', 'duration_seconds', 'segments', and optionally 'words'.
        """
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {"audio_url": audio_url, "language": language}
            if word_timestamps:
                payload["word_timestamps"] = True
            resp = await client.post(
                f"{self.base_url}/api/whisper-transcribe",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            # Flatten word-level timing from segments if returned
            if word_timestamps and "words" not in data:
                words = []
                for seg in data.get("segments", []):
                    for w in seg.get("words", []):
                        words.append({
                            "word": w.get("word", "").strip(),
                            "start": w.get("start", 0),
                            "end": w.get("end", 0),
                            "probability": w.get("probability", 0),
                        })
                data["words"] = words

            return data


_instance: GPUServerClient | None = None


def get_gpu_server_client() -> GPUServerClient | None:
    """Return the singleton GPU server client, or None if disabled."""
    global _instance
    if not settings.GPU_SERVER_ENABLED or not settings.GPU_SERVER_URL:
        return None
    if _instance is None:
        _instance = GPUServerClient(settings.GPU_SERVER_URL)
    return _instance

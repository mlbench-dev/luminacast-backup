"""Fish Audio service adapter for voice cloning and TTS.

Supports two backends:
1. Self-hosted Fish Speech on RunPod Serverless via pre-built template (preferred, 10-25x cheaper)
2. Fish Audio hosted API (fallback)

The backend is selected automatically: if FISH_SPEECH_ENDPOINT_ID is set,
the self-hosted worker is tried first. On failure, falls back to Fish Audio API.

Self-hosted clone runs locally on the VPS (ffmpeg trim/validate + R2 upload).
Self-hosted TTS sends reference audio + text to the pre-built RunPod Fish Speech
template which returns base64-encoded audio.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)

import re as _re_tts

def _sanitize_for_tts(text: str) -> str:
    """Strip stage directions, brackets, excessive whitespace from TTS input.

    Also enforces a terminal punctuation mark. Without one, autoregressive
    voice models (Fish Speech in particular) routinely keep generating after
    the user's script ends — producing a spurious trailing phrase like an
    outro/sign-off the user never typed. A clear sentence terminator gives
    the model an unambiguous stop signal.
    """
    text = _re_tts.sub(r'\*[^*]*\*', '', text)
    text = _re_tts.sub(r'\[[^\]]*\]', '', text)
    # Parenthetical prosody directions — (excited), (casual), (whispering),
    # etc. — are internal LLM markers (see services/ai_prompts.py), NOT Fish
    # control syntax. Fish receives plain text and has no (...) grammar, so it
    # is safe to strip all parenthesised spans; left in, TTS speaks the
    # direction word aloud.
    text = _re_tts.sub(r'\([^\)]*\)', '', text)
    text = _re_tts.sub(r'\s+', ' ', text).strip()
    if text and text[-1] not in '.!?。!?':
        text = text + '.'
    return text


def _max_new_tokens_for(text: str) -> int:
    """Cap RunPod Fish Speech generation length to roughly the input.

    Fish Speech defaults to 1024 max_new_tokens regardless of input length,
    which lets the model continue producing speech well past the script.
    Heuristic: ~3 tokens per character + a small buffer, clamped to a
    conservative ceiling so very short scripts don't hallucinate trailing
    words.
    """
    base = max(64, len(text) * 3 + 64)
    return min(base, 1024)


RUNPOD_API_BASE = "https://api.runpod.ai/v2"
# Max seconds to poll a RunPod job before giving up
RUNPOD_POLL_TIMEOUT = 60  # Fast fail — Fish Audio API fallback handles TTS if RunPod is slow
RUNPOD_POLL_INTERVAL = 3


def _log(level: str, service: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": service,
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


async def _retry_async(func, *args, max_retries=3, base_delay=2.0, **kwargs):
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            _log(
                "warning",
                func.__module__ or "service",
                f"Retry {attempt + 1}/{max_retries}: {e}",
                delay=delay,
            )
            await asyncio.sleep(delay)


def _is_r2_voice_id(voice_id: str) -> bool:
    """Check if a voice_id is an R2 key (self-hosted) vs a Fish Audio UUID."""
    return "/" in voice_id or voice_id.endswith(".wav")


def _voice_id_to_r2_url(voice_id: str) -> str:
    """Convert an R2 key voice_id to its public URL."""
    return f"{settings.R2_PUBLIC_URL}/{voice_id}"


class FishAudioService:
    BASE_URL = "https://api.fish.audio"

    def __init__(self):
        self.api_key = settings.FISH_AUDIO_API_KEY
        self.fish_speech_endpoint_id = settings.FISH_SPEECH_ENDPOINT_ID
        self.runpod_api_key = settings.RUNPOD_API_KEY

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
        }

    def _use_self_hosted(self) -> bool:
        """Whether self-hosted Fish Speech worker is configured."""
        return bool(self.fish_speech_endpoint_id and self.runpod_api_key)

    # ── RunPod helpers ──

    async def _runpod_submit(self, input_data: dict) -> str:
        """Submit a job to the Fish Speech RunPod worker. Returns job_id."""
        url = f"{RUNPOD_API_BASE}/{self.fish_speech_endpoint_id}/run"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.runpod_api_key}"},
                json={"input": input_data},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["id"]

    async def _runpod_poll(self, job_id: str) -> dict:
        """Poll a RunPod job until completion. Returns the output dict."""
        url = f"{RUNPOD_API_BASE}/{self.fish_speech_endpoint_id}/status/{job_id}"
        start = time.monotonic()

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                elapsed = time.monotonic() - start
                if elapsed > RUNPOD_POLL_TIMEOUT:
                    raise TimeoutError(
                        f"RunPod job {job_id} timed out after {RUNPOD_POLL_TIMEOUT}s"
                    )

                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {self.runpod_api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")

                if status == "COMPLETED":
                    output = data.get("output", {})
                    if isinstance(output, dict) and output.get("error"):
                        raise RuntimeError(
                            f"RunPod job {job_id} failed: {output['error']}"
                        )
                    return output
                elif status == "FAILED":
                    error = data.get("error", "Unknown error")
                    raise RuntimeError(f"RunPod job {job_id} failed: {error}")
                elif status in ("IN_QUEUE", "IN_PROGRESS"):
                    await asyncio.sleep(RUNPOD_POLL_INTERVAL)
                else:
                    raise RuntimeError(
                        f"RunPod job {job_id} unexpected status: {status}"
                    )

    async def _runpod_run(self, input_data: dict) -> dict:
        """Submit and poll a RunPod job. Returns the output dict."""
        job_id = await self._runpod_submit(input_data)
        _log("info", "fish_speech", "RunPod job submitted",
             job_id=job_id, mode=input_data.get("mode"))
        return await self._runpod_poll(job_id)

    # ── Self-hosted clone (local ffmpeg processing + R2 upload) ──

    @staticmethod
    def _probe_duration(path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())

    @staticmethod
    def _trim_to_wav(input_path: str, output_path: str, max_seconds: float = 30.0):
        """Trim audio to max_seconds, convert to mono 44.1kHz 16-bit WAV."""
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-t", str(max_seconds),
             "-ac", "1", "-ar", "44100", "-acodec", "pcm_s16le",
             output_path],
            capture_output=True, timeout=60, check=True,
        )

    async def _self_hosted_clone_from_url(self, audio_url: str, name: str) -> str:
        """Clone voice from URL: download, trim with ffmpeg, upload WAV to R2.

        Returns voice_id (R2 key).
        """
        from services.r2_storage import get_r2_storage_service

        url_hash = hashlib.sha256(audio_url.encode()).hexdigest()[:12]
        output_key = f"voices/refs/{url_hash}/reference.wav"

        # Download audio to temp file
        tmp_dl = tempfile.mktemp(suffix=".audio")
        tmp_wav = tempfile.mktemp(suffix=".wav")
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                resp = await client.get(audio_url)
                resp.raise_for_status()
                with open(tmp_dl, "wb") as f:
                    f.write(resp.content)

            if os.path.getsize(tmp_dl) < 1000:
                raise ValueError(f"Downloaded audio too small ({os.path.getsize(tmp_dl)} bytes)")

            # Probe duration and trim to 30s max as mono WAV
            duration = self._probe_duration(tmp_dl)
            _log("info", "fish_speech", "Clone audio downloaded",
                 duration=duration, size=os.path.getsize(tmp_dl))

            self._trim_to_wav(tmp_dl, tmp_wav)

            # Upload trimmed WAV to R2
            r2 = get_r2_storage_service()
            await r2.upload_file(tmp_wav, output_key, content_type="audio/wav")

            _log("info", "fish_speech", "Self-hosted clone complete (local)",
                 voice_id=output_key)
            return output_key
        finally:
            for p in (tmp_dl, tmp_wav):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def _self_hosted_clone_from_file(
        self, audio_path: str, name: str, transcript: str = "",
    ) -> str:
        """Clone voice from local file: trim with ffmpeg, upload WAV to R2.

        Returns voice_id (R2 key).
        """
        from services.r2_storage import get_r2_storage_service

        with open(audio_path, "rb") as f:
            file_hash = hashlib.sha256(f.read(8192)).hexdigest()[:12]
        output_key = f"voices/refs/{file_hash}/reference.wav"

        tmp_wav = tempfile.mktemp(suffix=".wav")
        try:
            # Trim to 30s max as mono WAV
            self._trim_to_wav(audio_path, tmp_wav)

            # Upload trimmed WAV to R2
            r2 = get_r2_storage_service()
            await r2.upload_file(tmp_wav, output_key, content_type="audio/wav")

            _log("info", "fish_speech", "Self-hosted clone from file complete (local)",
                 voice_id=output_key)
            return output_key
        finally:
            try:
                os.unlink(tmp_wav)
            except OSError:
                pass

    # ── Self-hosted TTS (pre-built RunPod Fish Speech template) ──

    async def _self_hosted_tts(self, text: str, voice_id: str, *, user_id: str | None = None) -> dict:
        """Generate TTS via pre-built RunPod Fish Speech template.

        1. Download reference audio from R2 (voice_id is an R2 key)
        2. Base64-encode it and send to RunPod with the template's API format
        3. Decode the base64 audio response
        4. Upload to R2 and save to temp file
        """
        from services.r2_storage import get_r2_storage_service

        r2 = get_r2_storage_service()
        output_key = f"tts/{voice_id.replace('/', '_')}/{int(time.time())}.mp3"
        tts_start = time.monotonic()

        # Download reference audio from R2
        tmp_ref = tempfile.mktemp(suffix=".wav")
        try:
            await r2.download_file(voice_id, tmp_ref)
            with open(tmp_ref, "rb") as f:
                ref_audio_b64 = base64.b64encode(f.read()).decode("ascii")
        finally:
            try:
                os.unlink(tmp_ref)
            except OSError:
                pass

        # ── Try 1: Dedicated GPU server (fastest, no cold start) ──
        # Skip if fish_speech model not loaded on GPU server (it's a stub endpoint)
        from services.gpu_server import get_gpu_server_client
        gpu_client = get_gpu_server_client()
        # Quick check: skip GPU server if fish_speech is not ready
        try:
            health = await gpu_client.health()
            _gpu_fish_ready = health.get("models_ready", {}).get("fish_speech", False)
        except Exception:
            _gpu_fish_ready = False
        if not _gpu_fish_ready:
            raise ValueError("GPU server fish_speech not ready — skip to RunPod")
        if gpu_client:
            try:
                _log("info", "fish_speech", "TTS: trying dedicated GPU server")
                gpu_result = await gpu_client.fish_speech_tts(
                    text=text, reference_audio_b64=ref_audio_b64, format="mp3",
                )
                audio_b64_gpu = gpu_result.get("audio_base64", "")
                if audio_b64_gpu:
                    audio_bytes_gpu = base64.b64decode(audio_b64_gpu)
                    tmp_fd_gpu, tmp_path_gpu = tempfile.mkstemp(suffix=".mp3")
                    try:
                        with os.fdopen(tmp_fd_gpu, "wb") as f:
                            f.write(audio_bytes_gpu)
                        try:
                            duration_seconds_gpu = self._probe_duration(tmp_path_gpu)
                        except Exception:
                            duration_seconds_gpu = round(len(audio_bytes_gpu) / 16000.0, 2)
                        await r2.upload_bytes(audio_bytes_gpu, output_key, content_type="audio/mpeg")
                        from services.usage_logger import log_api_usage
                        await log_api_usage(
                            user_id=user_id, service="fish_speech", operation="tts_generate_gpu_server",
                            success=True, duration_seconds=round(time.monotonic() - tts_start, 1),
                        )
                        _log("info", "fish_speech", "TTS via GPU server complete",
                             audio_key=output_key, duration_seconds=round(duration_seconds_gpu, 2))
                        return {
                            "audio_key": output_key,
                            "duration_seconds": round(duration_seconds_gpu, 2),
                            "tmp_path": tmp_path_gpu,
                        }
                    except Exception:
                        try:
                            os.unlink(tmp_path_gpu)
                        except OSError:
                            pass
                        raise
            except Exception as e:
                _log("warning", "fish_speech",
                     f"GPU server TTS failed, falling back to RunPod: {e}")

        # ── Try 2: RunPod (existing code below, unchanged) ──
        # Call pre-built RunPod Fish Speech template
        result = await self._runpod_run({
            "text": text,
            "format": "mp3",
            "reference_audio": [ref_audio_b64],
            "reference_text": [],
            "temperature": 0.8,
            "top_p": 0.8,
            "repetition_penalty": 1.1,
            "max_new_tokens": _max_new_tokens_for(text),
            "chunk_length": 300,
            "seed": None,
            "use_memory_cache": "off",
        })

        # Decode base64 audio from response
        audio_b64 = result.get("audio_base64", "")
        if not audio_b64:
            raise RuntimeError("RunPod Fish Speech template returned no audio_base64")
        audio_bytes = base64.b64decode(audio_b64)

        # Save to temp file (callers expect tmp_path)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(audio_bytes)

            # Get duration via ffprobe on the temp file
            try:
                duration_seconds = self._probe_duration(tmp_path)
            except Exception:
                # Fallback: rough estimate for MP3 (~128kbps)
                duration_seconds = round(len(audio_bytes) / 16000.0, 2)

            # Upload to R2
            await r2.upload_bytes(audio_bytes, output_key, content_type="audio/mpeg")

            from services.usage_logger import log_api_usage
            await log_api_usage(
                user_id=user_id, service="fish_speech", operation="tts_generate",
                success=True, duration_seconds=round(time.monotonic() - tts_start, 1),
            )

            return {
                "audio_key": output_key,
                "duration_seconds": round(duration_seconds, 2),
                "tmp_path": tmp_path,
            }
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── Fish Audio API (original implementation) ──

    async def _post_clone_voice(
        self, audio_bytes: bytes, name: str, ext: str = "wav",
        transcript: str = "", enhance: bool = False,
    ) -> dict:
        """Create a voice model via Fish Audio API using multipart/form-data.

        Args:
            audio_bytes: Raw audio file bytes
            name: Model title
            ext: File extension (wav or mp3)
            transcript: Whisper transcript — helps Fish Audio understand prosody
            enhance: Whether Fish Audio should enhance audio (False if we pre-processed)
        """
        mime = "audio/wav" if ext == "wav" else "audio/mpeg"
        filename = f"voice_sample.{ext}"

        _log("info", "fish_audio", "Posting voice clone to Fish Audio",
             audio_size=len(audio_bytes), filename=filename,
             has_transcript=bool(transcript), enhance=enhance)

        data = {
            "type": "tts",
            "title": name,
            "train_mode": "fast",
            "visibility": "private",
            "enhance_audio_quality": str(enhance).lower(),
        }
        # Include transcript so Fish Audio maps text to prosody
        if transcript:
            data["texts"] = transcript[:2000]  # Fish Audio limit

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/model",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=[
                    ("voices", (filename, audio_bytes, mime)),
                ],
                data=data,
            )

            # HARD LOGGING — surface the exact error, do NOT swallow it
            if response.status_code >= 400:
                error_body = response.text[:500]
                _log("error", "fish_audio",
                     f"Fish Audio clone FAILED: HTTP {response.status_code}",
                     status_code=response.status_code,
                     response_body=error_body)
                raise ValueError(
                    f"Fish Audio API returned {response.status_code}: {error_body}"
                )

            result = response.json()
            _log("info", "fish_audio", "Fish Audio clone response",
                 response_id=result.get("_id"), state=result.get("state"))
            return result

    async def _fish_audio_clone_from_url(self, audio_url: str, name: str) -> str:
        """Clone via Fish Audio hosted API from a URL."""
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(audio_url)
            resp.raise_for_status()
            audio_bytes = resp.content

        if len(audio_bytes) < 1000:
            raise ValueError(f"Downloaded audio too small ({len(audio_bytes)} bytes)")

        content_type = resp.headers.get("content-type", "")
        ext = "wav" if "wav" in content_type else "mp3"
        result = await self._post_clone_voice(audio_bytes, name, ext)
        return result["_id"]

    async def _fish_audio_clone_from_file(
        self, audio_path: str, name: str, transcript: str = "",
    ) -> str:
        """Clone via Fish Audio hosted API from a local file."""
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        ext = "wav" if audio_path.endswith(".wav") else "mp3"
        result = await self._post_clone_voice(
            audio_bytes, name, ext, transcript=transcript, enhance=False)
        return result["_id"]

    async def _post_tts(self, text: str, voice_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/v1/tts",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "reference_id": voice_id,
                    "format": "mp3",
                    "mp3_bitrate": 128,
                },
            )
            response.raise_for_status()
            return response.content

    async def _fish_audio_tts(self, text: str, voice_id: str) -> dict:
        """Generate TTS via Fish Audio hosted API."""
        from services.r2_storage import get_r2_storage_service

        audio_bytes = await _retry_async(self._post_tts, text, voice_id)

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(audio_bytes)

            duration_seconds = round(len(audio_bytes) / 16000.0, 2)
            audio_key = f"tts/{voice_id}/{int(time.time())}.mp3"

            # Upload to R2 so downstream consumers (RunPod) can access it
            r2 = get_r2_storage_service()
            await r2.upload_bytes(audio_bytes, audio_key, content_type="audio/mpeg")

            return {
                "audio_key": audio_key,
                "duration_seconds": duration_seconds,
                "tmp_path": tmp_path,
            }
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def _fish_audio_tts_with_reference(self, text: str, voice_id: str, *, user_id: str | None = None) -> dict:
        """Generate TTS via Fish Audio hosted API using inline reference audio.

        Downloads the voice reference from R2 and sends it to Fish Audio's
        /v1/tts endpoint using the `references` parameter (msgpack format)
        for zero-shot cloning. This is the fallback when self-hosted RunPod
        worker is unavailable.
        """
        import msgpack

        tts_start = time.monotonic()
        voice_url = _voice_id_to_r2_url(voice_id)
        _log("info", "fish_audio", "Downloading reference audio for API fallback",
             voice_url=voice_url[:80])

        # Download reference audio from R2
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(voice_url)
            resp.raise_for_status()
            ref_audio_bytes = resp.content

        tmp_ref_in = tempfile.mktemp(suffix=".wav")
        tmp_ref_out = tempfile.mktemp(suffix=".wav")
        try:
            with open(tmp_ref_in, "wb") as f:
                f.write(ref_audio_bytes)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_ref_in,
                     "-t", "30",
                     "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
                     tmp_ref_out],
                    capture_output=True, timeout=60, check=True,
                )
                with open(tmp_ref_out, "rb") as f:
                    resampled_ref_audio_bytes = f.read()
                if len(resampled_ref_audio_bytes) > 1000:
                    _log(
                        "info",
                        "fish_audio",
                        "Prepared reference audio for inline TTS",
                        original_size=len(ref_audio_bytes),
                        prepared_size=len(resampled_ref_audio_bytes),
                    )
                    ref_audio_bytes = resampled_ref_audio_bytes
            except Exception as e:
                _log(
                    "warning",
                    "fish_audio",
                    f"Reference audio prep failed; using original bytes: {type(e).__name__}",
                    original_size=len(ref_audio_bytes),
                )
        finally:
            for p in (tmp_ref_in, tmp_ref_out):
                try:
                    os.unlink(p)
                except OSError:
                    pass

        _log("info", "fish_audio", "Sending TTS with inline reference (msgpack)",
             ref_size=len(ref_audio_bytes), text_length=len(text))

        # Build request payload — Fish Audio API uses msgpack with raw bytes
        payload = {
            "text": text,
            "references": [{
                "audio": ref_audio_bytes,  # Raw bytes, not base64
                "text": "",
            }],
            "format": "mp3",
            "mp3_bitrate": 128,
            "chunk_length": 200,
            "latency": "normal",
            "normalize": True,
        }
        body = msgpack.packb(payload, use_bin_type=True)

        request_timeout = httpx.Timeout(connect=30.0, read=300.0, write=180.0, pool=30.0)

        async def _send_reference_tts() -> bytes:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                t0 = time.monotonic()
                _log(
                    "info",
                    "fish_audio",
                    "Inline-reference TTS request starting",
                    voice_id=voice_id,
                    text_length=len(text),
                    ref_size=len(ref_audio_bytes),
                    body_size=len(body),
                    timeout_read_s=request_timeout.read,
                )
                try:
                    async with client.stream(
                        "POST",
                        f"{self.BASE_URL}/v1/tts",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/msgpack",
                            "model": "s2-pro",
                        },
                        content=body,
                    ) as response:
                        if response.status_code >= 400:
                            error_bytes = await response.aread()
                            _log(
                                "error",
                                "fish_audio",
                                f"Inline-reference TTS failed: HTTP {response.status_code}",
                                status_code=response.status_code,
                                response_body=error_bytes[:500].decode("utf-8", errors="replace"),
                            )
                            response.raise_for_status()

                        audio_chunks = []
                        async for chunk in response.aiter_bytes():
                            audio_chunks.append(chunk)
                        audio_bytes = b"".join(audio_chunks)

                        _log(
                            "info",
                            "fish_audio",
                            "Inline-reference TTS response received",
                            status_code=response.status_code,
                            audio_size=len(audio_bytes),
                            duration_seconds=round(time.monotonic() - t0, 2),
                        )
                        return audio_bytes
                except Exception as exc:
                    _log(
                        "error",
                        "fish_audio",
                        f"Inline-reference TTS request raised: {type(exc).__name__}",
                        voice_id=voice_id,
                        text_length=len(text),
                        ref_size=len(ref_audio_bytes),
                        body_size=len(body),
                    )
                    raise

        audio_bytes = await _retry_async(_send_reference_tts)

        # Save to temp file
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(audio_bytes)

            duration_seconds = round(len(audio_bytes) / 16000.0, 2)
            audio_key = f"tts/{voice_id.replace('/', '_')}/{int(time.time())}.mp3"

            # Upload to R2 so downstream consumers (RunPod) can access it
            from services.r2_storage import get_r2_storage_service
            r2 = get_r2_storage_service()
            await r2.upload_bytes(audio_bytes, audio_key, content_type="audio/mpeg")

            from services.usage_logger import log_api_usage
            await log_api_usage(
                user_id=user_id, service="fish_audio", operation="tts_generate",
                success=True, duration_seconds=round(time.monotonic() - tts_start, 1),
            )

            return {
                "audio_key": audio_key,
                "duration_seconds": duration_seconds,
                "tmp_path": tmp_path,
            }
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── Public methods (try self-hosted → fallback to Fish Audio API) ──

    async def clone_voice(self, audio_url: str, name: str = "creator_voice") -> str:
        """Clone a voice from an audio URL. Returns voice_id."""
        with sentry_sdk.start_span(op='fish_audio', description='Voice clone from URL') as span:
            span.set_data('name', name)
            _log("info", "fish_audio", "Cloning voice from URL", name=name, audio_url=audio_url[:100])

            if self._use_self_hosted():
                try:
                    voice_id = await self._self_hosted_clone_from_url(audio_url, name)
                    span.set_data('voice_id', voice_id)
                    span.set_data('tier', 'self_hosted')
                    _log("info", "fish_speech", "Voice cloned via self-hosted worker",
                         voice_id=voice_id)
                    return voice_id
                except Exception as e:  # noqa: deliberate fallback — self-hosted clone → Fish Audio API
                    _log("warning", "fish_speech",
                         f"Self-hosted clone failed, falling back to Fish Audio API: {e}")

            voice_id = await self._fish_audio_clone_from_url(audio_url, name)
            span.set_data('voice_id', voice_id)
            span.set_data('tier', 'fish_audio_api')
            _log("info", "fish_audio", "Voice cloning complete", voice_id=voice_id)
            return voice_id

    async def clone_voice_from_file(
        self, audio_path: str, name: str = "creator_voice",
        transcript: str = "",
    ) -> str:
        """Clone a voice from a local audio file. Returns voice_id."""
        with sentry_sdk.start_span(op='fish_audio', description='Voice clone from file') as span:
            span.set_data('audio_size', os.path.getsize(audio_path))
            _log("info", "fish_audio", "Cloning voice from local file",
                 path=audio_path, has_transcript=bool(transcript))

            if self._use_self_hosted():
                try:
                    voice_id = await self._self_hosted_clone_from_file(
                        audio_path, name, transcript)
                    span.set_data('voice_id', voice_id)
                    span.set_data('tier', 'self_hosted')
                    _log("info", "fish_speech", "Voice cloned from file via self-hosted worker",
                         voice_id=voice_id)
                    return voice_id
                except Exception as e:  # noqa: deliberate fallback — self-hosted file clone → Fish Audio API
                    _log("warning", "fish_speech",
                         f"Self-hosted clone from file failed, falling back to Fish Audio API: {e}")

            voice_id = await self._fish_audio_clone_from_file(audio_path, name, transcript)
            span.set_data('voice_id', voice_id)
            span.set_data('tier', 'fish_audio_api')
            _log("info", "fish_audio", "Voice cloning complete", voice_id=voice_id)
            if not voice_id:
                logger.warning("clone_voice_from_file returned None")
                return None
            return voice_id

    async def generate_tts(
        self,
        text: str,
        voice_id: str,
        *,
        clip_mic_enabled: bool = False,
        scene_chain_id: str | None = None,
        block_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """Generate TTS audio. Returns ``{audio_key, duration_seconds, tmp_path, lipsync_audio_key}``.

        Adds broadcast-quality post-processing (de-ess / EQ / compand /
        loudnorm) and produces two outputs per call:
        - ``audio_key``: 44.1 kHz mono MP3 192k — the high-fidelity
          master fed to the final compose audio remux.
        - ``lipsync_audio_key``: 16 kHz mono WAV — fed to the lipsync
          engine. Persist this on the variant as ``tts_lipsync_r2_key``.

        ``clip_mic_enabled`` toggles the EQ profile between lavalier
        (warm proximity, tighter compand) and phone-mic (natural,
        lighter compand). The default is phone-mic.

        ``scene_chain_id`` optionally selects a scene-aware filter chain
        (see ``services.mic_presets.SCENE_FILTER_LIBRARY``) instead of the
        flat clip_mic/phone_mic split — e.g. an outdoor scene gets a
        windscreen-shaped high-pass even with the mic visible. ``None``
        preserves the plain ``clip_mic_enabled`` behavior.
        """
        original_len = len(text)
        text = _sanitize_for_tts(text)
        # Regression guard: if a future code path concatenates extra text onto
        # the user's script before reaching synthesis, the preview will make
        # the divergence obvious in production logs.
        logger.info(
            "tts.text_to_synthesize len=%d (raw=%d) preview=%r",
            len(text), original_len, text[:80],
        )
        with sentry_sdk.start_span(op='fish_audio', description='TTS generate') as span:
            span.set_data('voice_id', voice_id)
            span.set_data('text_length', len(text))
            _log(
                "info",
                "fish_audio",
                "Generating TTS",
                voice_id=voice_id,
                text_length=len(text),
            )

            # If voice_id is an R2 key (self-hosted clone), use self-hosted TTS
            if _is_r2_voice_id(voice_id) and self._use_self_hosted():
                try:
                    result = await self._self_hosted_tts(text, voice_id, user_id=user_id)
                    span.set_data('duration_seconds', result.get('duration_seconds'))
                    span.set_data('tier', 'self_hosted')
                    _log("info", "fish_speech", "TTS generated via self-hosted worker",
                         audio_key=result["audio_key"],
                         duration_seconds=result["duration_seconds"])
                    return await self._attach_post_process(
                        result,
                        clip_mic_enabled=clip_mic_enabled,
                        scene_chain_id=scene_chain_id,
                        block_id=block_id,
                    )
                except Exception as e:  # noqa: deliberate fallback — self-hosted TTS → Fish Audio API (zero-shot reference)
                    _log("warning", "fish_speech",
                         f"Self-hosted TTS failed, falling back to Fish Audio API with reference audio: {e}")
                    # Fall back to Fish Audio API using inline reference audio (zero-shot)
                    result = await self._fish_audio_tts_with_reference(text, voice_id, user_id=user_id)
                    span.set_data('duration_seconds', result.get('duration_seconds'))
                    span.set_data('tier', 'fish_audio_api_fallback')
                    _log("info", "fish_audio", "TTS generated via Fish Audio API fallback",
                         audio_key=result["audio_key"],
                         duration_seconds=result["duration_seconds"])
                    return await self._attach_post_process(
                        result,
                        clip_mic_enabled=clip_mic_enabled,
                        scene_chain_id=scene_chain_id,
                        block_id=block_id,
                    )

            # Fish Audio UUID voice_id — try self-hosted if configured (won't work for
            # Fish Audio UUIDs since there's no reference audio on R2), so go direct
            result = await self._fish_audio_tts(text, voice_id)
            span.set_data('duration_seconds', result.get('duration_seconds'))
            span.set_data('tier', 'fish_audio_api')
            _log("info", "fish_audio", "TTS generation complete",
                 audio_key=result["audio_key"],
                 duration_seconds=result["duration_seconds"])
            return await self._attach_post_process(
                result,
                clip_mic_enabled=clip_mic_enabled,
                scene_chain_id=scene_chain_id,
                block_id=block_id,
            )

    async def _attach_post_process(
        self,
        result: dict,
        *,
        clip_mic_enabled: bool = False,
        scene_chain_id: str | None = None,
        block_id: str | None = None,
    ) -> dict:
        """Post-process the freshly-generated TTS (de-ess / EQ / compand /
        loudnorm), overwrite ``audio_key`` with the 44.1 kHz MP3 master,
        and add a sibling ``lipsync_audio_key`` (16 kHz WAV).

        On any failure the original ``result`` dict is returned unchanged
        so renders never abort on a post-process hiccup.
        """
        try:
            audio_key = result.get("audio_key") or ""
            tmp_path = result.get("tmp_path") or ""
            if not audio_key or not tmp_path:
                return result
            try:
                import os as _os
                if not _os.path.exists(tmp_path):
                    return result
            except Exception as path_exc:
                sentry_sdk.capture_exception(path_exc)
                return result
            from services.r2_storage import get_r2_storage_service
            from services.voice_postprocess_upload import post_process_and_upload
            r2 = get_r2_storage_service()
            mix_key, lipsync_key = await post_process_and_upload(
                r2=r2,
                raw_tmp_path=tmp_path,
                mix_r2_key=audio_key,
                clip_mic_enabled=clip_mic_enabled,
                scene_chain_id=scene_chain_id,
                block_id=block_id,
            )
            result["audio_key"] = mix_key
            result["lipsync_audio_key"] = lipsync_key
            return result
        except Exception as e:
            sentry_sdk.capture_exception(e)
            _log(
                "warning",
                "fish_audio",
                f"Voice post-process failed; using raw bytes: {e}",
                block_id=block_id,
            )
            return result


_instance: FishAudioService | None = None


def get_fish_audio_service() -> FishAudioService:
    global _instance
    if _instance is None:
        _instance = FishAudioService()
    return _instance

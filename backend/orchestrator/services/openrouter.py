"""OpenRouter service adapter for LLM text gen, image gen, and persona analysis."""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
import sentry_sdk

from config import settings

logger = logging.getLogger(__name__)


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


from services.ai_prompts import get_prompt
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)

# Build persona extraction prompt dynamically using the centralized registry
def _build_persona_extraction_prompt(transcripts_text: str) -> str:
    prompt = get_prompt("persona_analyzer")
    return f"""{prompt["system"]}

Transcripts:
{transcripts_text}"""


class OpenRouterService:
    """OpenRouter LLM/image adapter.

    After calling generate_text() / generate_image() / analyze_persona(),
    read `service.last_usage` to get the token counts from the most recent
    call. Shape: ``{"prompt_tokens": int, "completion_tokens": int,
    "total_tokens": int}`` — empty dict if the API didn't return usage or
    the call failed. Used by the cost tracker to bill LLM calls per token.
    """

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.last_usage: dict = {}

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://luminacast.ai",
            "X-Title": "Luminacast Omni",
            "Content-Type": "application/json",
        }

    async def _post_chat_completions(
        self,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
    ) -> dict:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            response.raise_for_status()
            return response.json()

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        model: str = "anthropic/claude-3-haiku",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """Generate text via OpenRouter LLM. Uses retry logic."""
        _log("info", "openrouter", "Generating text", model=model, max_tokens=max_tokens)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        self.last_usage = {}
        result = await _retry_async(
            self._post_chat_completions,
            model,
            messages,
            max_tokens,
            temperature,
        )

        if "error" in result:
            error_msg = result["error"].get("message", str(result["error"]))
            raise RuntimeError(f"LLM API error: {error_msg}")
        try:
            self.last_usage = result.get("usage", {}) or {}
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            self.last_usage = {}
        content = result["choices"][0]["message"]["content"]
        _log("info", "openrouter", "Text generation complete", chars=len(content))
        return content

    async def generate_image(self, prompt: str, output_r2_key: str) -> str:
        """Generate image via Nano Banana Pro on OpenRouter. Returns R2 key."""
        _log("info", "openrouter", "Generating image", output_r2_key=output_r2_key)

        messages = [{"role": "user", "content": prompt}]

        self.last_usage = {}
        result = await _retry_async(
            self._post_chat_completions,
            "bananadev/nano-banana-pro",
            messages,
            1024,
            0.7,
        )
        try:
            self.last_usage = (result or {}).get("usage", {}) or {}
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            self.last_usage = {}

        _log("info", "openrouter", "Image generation complete", output_r2_key=output_r2_key)
        return output_r2_key

    async def describe_image(
        self,
        image_url: str,
        system_prompt: str,
        user_text: str,
        model: str = CREATIVE_DESCRIPTION_MODEL,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """Send a single image plus instructions to a vision LLM and return the raw text response.

        Used to extract wardrobe / accessory attributes from a selected face
        image so downstream body-shot generation can mirror what the user
        actually picked instead of an earlier setup-time description.
        """
        log_creative_model_use("openrouter_describe_image", model)
        _log("info", "openrouter", "Describing single image", model=model)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]

        self.last_usage = {}
        result = await _retry_async(
            self._post_chat_completions,
            model,
            messages,
            max_tokens,
            temperature,
        )

        if "error" in result:
            error_msg = result["error"].get("message", str(result["error"]))
            raise RuntimeError(f"Vision API error: {error_msg}")
        try:
            self.last_usage = result.get("usage", {}) or {}
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            self.last_usage = {}
        content = result["choices"][0]["message"]["content"]
        _log("info", "openrouter", "Image description complete", chars=len(content))
        return content

    async def compare_two_images(
        self,
        image_url_a: str,
        image_url_b: str,
        system_prompt: str,
        user_text: str,
        model: str = "anthropic/claude-sonnet-4",
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        """Send two images plus instructions to a vision LLM and return the raw text response.

        Both images are passed via URL (OpenRouter fetches them server-side).
        Used by the body-shot clothing-consistency check.
        """
        _log("info", "openrouter", "Comparing two images", model=model)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": image_url_a}},
                    {"type": "image_url", "image_url": {"url": image_url_b}},
                ],
            },
        ]

        self.last_usage = {}
        result = await _retry_async(
            self._post_chat_completions,
            model,
            messages,
            max_tokens,
            temperature,
        )

        if "error" in result:
            error_msg = result["error"].get("message", str(result["error"]))
            raise RuntimeError(f"Vision API error: {error_msg}")
        try:
            self.last_usage = result.get("usage", {}) or {}
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            self.last_usage = {}
        content = result["choices"][0]["message"]["content"]
        _log("info", "openrouter", "Image comparison complete", chars=len(content))
        return content

    async def rank_images(
        self,
        image_urls: list[str],
        system_prompt: str,
        user_text: str,
        model: str = CREATIVE_DESCRIPTION_MODEL,
        max_tokens: int = 256,
        temperature: float = 0.0,
    ) -> str:
        """Send several candidate images plus instructions to a vision LLM and
        return the raw text response.

        Each image is passed via URL (OpenRouter fetches them server-side) in
        the same order as ``image_urls`` so the caller can reference them by
        index. Used by the b-roll re-rank to pick the candidate that best
        matches a script beat.
        """
        _log("info", "openrouter", "Ranking images", model=model, count=len(image_urls))

        content: list[dict] = [{"type": "text", "text": user_text}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        self.last_usage = {}
        result = await _retry_async(
            self._post_chat_completions,
            model,
            messages,
            max_tokens,
            temperature,
        )

        if "error" in result:
            error_msg = result["error"].get("message", str(result["error"]))
            raise RuntimeError(f"Vision API error: {error_msg}")
        try:
            self.last_usage = result.get("usage", {}) or {}
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            self.last_usage = {}
        content_out = result["choices"][0]["message"]["content"]
        _log("info", "openrouter", "Image ranking complete", chars=len(content_out))
        return content_out

    async def analyze_persona(self, transcripts: list[str]) -> dict:
        """Analyze creator persona from video transcripts. Returns persona profile dict."""
        _log(
            "info",
            "openrouter",
            "Analyzing creator persona",
            transcript_count=len(transcripts),
        )

        combined = "\n\n---\n\n".join(transcripts)
        prompt = _build_persona_extraction_prompt(combined)

        raw = await self.generate_text(
            prompt=prompt,
            system_prompt="You are a JSON-only response AI. Always respond with valid JSON.",
            model="anthropic/claude-3-haiku",
            max_tokens=2048,
            temperature=0.3,
        )

        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        persona = json.loads(cleaned)
        _log("info", "openrouter", "Persona analysis complete")
        return persona


_instance: OpenRouterService | None = None


def get_openrouter_service() -> OpenRouterService:
    global _instance
    if _instance is None:
        _instance = OpenRouterService()
    return _instance

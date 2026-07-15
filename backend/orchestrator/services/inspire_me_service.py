"""Engine-aware Inspire Me prompt enhancement for generated videos.

Takes the user's raw idea (or empty string) + current modal settings,
returns an optimized video prompt using the hand-curated Kling/Wan guide.
"""
import logging
from pathlib import Path

import sentry_sdk

from services.openrouter import get_openrouter_service
from services.ai_prompts import get_prompt
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)

logger = logging.getLogger(__name__)

PROMPTING_GUIDE_PATH = Path(__file__).parent.parent / "kling_wan_prompting_guide.md"


class InspireMeService:
    def __init__(self):
        self._guide_content = None

    def _load_guide(self) -> str:
        if self._guide_content is None:
            self._guide_content = PROMPTING_GUIDE_PATH.read_text()
        return self._guide_content

    async def enhance(
        self,
        user_idea: str,
        engine: str,
        mode: str,
        duration: int,
        aspect_ratio: str,
        camera_preset: str | None = None,
        reference_image_description: str | None = None,
    ) -> str:
        """Return an enhanced prompt optimized for the current settings."""
        guide = self._load_guide()
        prompt_template = get_prompt("generated_video_inspire_me")
        system_prompt = prompt_template["system"].replace(
            "{prompting_guide_content}", guide
        )

        settings_context = (
            f"Current video generation settings:\n"
            f"- Engine: {engine}\n"
            f"- Mode: {mode}\n"
            f"- Duration: {duration} seconds\n"
            f"- Aspect ratio: {aspect_ratio}\n"
            f"- Camera motion preset: {camera_preset or 'none'}"
        )

        if reference_image_description:
            settings_context += f"\n- Reference image shows: {reference_image_description}"

        if user_idea.strip():
            user_msg = (
                f"{settings_context}\n\n"
                f"User's rough idea: {user_idea.strip()}\n\n"
                f"Enhance this into a cinematic prompt optimized for {engine}."
            )
        else:
            user_msg = (
                f"{settings_context}\n\n"
                f"Generate a fresh compelling video idea optimized for {engine}."
            )

        try:
            oai = get_openrouter_service()
            log_creative_model_use("inspire_me_enhance", CREATIVE_DESCRIPTION_MODEL)
            enhanced = await oai.generate_text(
                prompt=user_msg,
                system_prompt=system_prompt,
                model=CREATIVE_DESCRIPTION_MODEL,
                max_tokens=300,
            )
            return enhanced.strip()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

    async def describe_reference_image(self, image_url: str) -> str:
        """Use vision LLM to describe a reference image for image-to-video mode."""
        try:
            oai = get_openrouter_service()
            log_creative_model_use("inspire_me_describe_reference", CREATIVE_DESCRIPTION_MODEL)
            description = await oai.generate_text(
                prompt=(
                    f"Describe this image in 1-2 sentences for a video generation prompt. "
                    f"Focus on: what the subject is, its position, the setting, and any "
                    f"existing motion cues. No preamble.\n\nImage URL: {image_url}"
                ),
                system_prompt="You are a concise image describer for video generation.",
                model=CREATIVE_DESCRIPTION_MODEL,
                max_tokens=150,
            )
            return description.strip()
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise

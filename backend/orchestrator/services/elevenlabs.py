"""ElevenLabs Voice Design — generate unique voices from text descriptions.

Flow:
1. Send voice description → ElevenLabs generates a unique voice
2. Generate a 45-second sample reading a phonetically rich passage
3. User previews and approves
4. The approved sample is sent to Fish Audio for voice cloning
5. Fish Audio voice_id becomes the avatar's permanent voice

ElevenLabs is used ONLY for voice design (one-time generation).
Fish Audio handles all production TTS.
"""

import httpx
import logging
import base64
from config import settings

logger = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"

# Phonetically rich passage that covers all English sounds —
# designed to give Fish Audio the best possible training material.
# ~45 seconds when spoken at normal pace.
VOICE_TRAINING_TEXT = (
    "Welcome everyone to today's live stream! I'm so excited to show you "
    "these incredible products. First up, we have this gorgeous moisturizer "
    "that over two hundred thousand people absolutely love. The texture is "
    "smooth, lightweight, and it absorbs in just thirty seconds. Can you "
    "believe the reviews? Four point eight stars from twelve thousand buyers! "
    "Now let me show you the before and after — this is where it gets really "
    "interesting. The original price was forty-six dollars, but right now, "
    "just for you watching live, it's only twenty-three dollars. That's a "
    "fifty percent discount! Quick question for the chat — have any of you "
    "tried this brand before? Drop a yes or no below! Okay, moving on to "
    "our next product. This one is a total game changer for your morning "
    "routine. I've been using it for three weeks and honestly, I'm obsessed. "
    "Tap the basket icon right now if you want to grab it before we sell out!"
)


class ElevenLabsService:

    def __init__(self):
        self.api_key = settings.ELEVENLABS_API_KEY

    async def generate_voice_previews(
        self,
        description: str,
        text: str = "",
        avatar_name: str = "",
        count: int = 4,
    ) -> list[dict]:
        """Generate voice previews from a text description.

        Makes two parallel calls to ElevenLabs create-previews endpoint
        (each returns 3 voice variations) and returns the first `count`
        unique previews to guarantee at least 4 options.

        Args:
            description: e.g. "Young woman, warm and friendly, slight British accent, energetic"
            text: Preview text (~10 seconds). If empty, uses a default.
            count: Number of previews to return (default 4).

        Returns: list of {
            "preview_id": str,       # ElevenLabs generated_voice_id
            "audio_base_64": str,    # Base64 encoded MP3 preview (note: underscores)
            "description": str,
            "index": int,
        }
        """
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY not configured")

        avatar_display = avatar_name or "your host"
        preview_text = text or (
            f"Hi everyone! It's {avatar_display} here! Welcome to my stream! I have got some truly amazing "
            "products to show you today. You are going to absolutely love these incredible deals "
            "that I have picked out just for you. Let us get started right now!"
        )
        # ElevenLabs requires minimum 100 characters
        while len(preview_text) < 100:
            preview_text += " This is going to be amazing, trust me on this one!"

        payload = {
            "voice_description": description,
            "model_id": "eleven_ttv_v3",
            "auto_enhance_description": True,
            "output_format": "mp3_44100_128",
            "text": preview_text,
        }

        import asyncio

        async def _single_call() -> list[dict]:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{ELEVENLABS_BASE}/text-to-voice/design",
                    headers={
                        "xi-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json().get("previews", [])

        # Two parallel calls → up to 6 previews, take first `count`
        results = await asyncio.gather(_single_call(), _single_call(), return_exceptions=True)

        previews: list[dict] = []
        seen_ids: set[str] = set()
        global_idx = 0
        for batch in results:
            if isinstance(batch, Exception):
                logger.warning(f"ElevenLabs batch call failed: {batch}")
                continue
            for item in batch:
                audio_b64 = item.get("audio_base_64", "")
                gen_id = item.get("generated_voice_id", f"preview_{global_idx}")
                if audio_b64 and gen_id not in seen_ids:
                    seen_ids.add(gen_id)
                    previews.append({
                        "preview_id": gen_id,
                        "audio_base_64": audio_b64,
                        "description": description,
                        "index": global_idx,
                    })
                    global_idx += 1
                    if len(previews) >= count:
                        break
            if len(previews) >= count:
                break

        logger.info(f"ElevenLabs generated {len(previews)} voice previews (requested {count})")
        return previews

    async def delete_voice(self, voice_id: str) -> None:
        """Delete a permanent ElevenLabs voice. Best-effort — log and swallow
        errors so a delete failure never blocks the avatar pipeline."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{ELEVENLABS_BASE}/voices/{voice_id}",
                headers={"xi-api-key": self.api_key},
            )
            resp.raise_for_status()

    async def create_voice_from_preview(self, generated_voice_id: str, voice_name: str, voice_description: str) -> str:
        """Convert a preview voice to a permanent ElevenLabs voice.
        Must be called before using the voice for TTS.
        Returns the permanent voice_id (same as generated_voice_id).
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{ELEVENLABS_BASE}/text-to-voice/create-voice-from-preview",
                headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
                json={
                    "voice_name": voice_name,
                    "voice_description": voice_description[:200] if len(voice_description) >= 20 else voice_description + " - AI generated voice for live streaming",
                    "generated_voice_id": generated_voice_id,
                    "labels": {"language": "en"},
                },
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                # raise_for_status()'s default message drops the response
                # body, which is where ElevenLabs actually explains the
                # rejection (expired/consumed preview_id, bad description,
                # etc). Surface it so the caller/logs show the real reason.
                try:
                    body = e.response.json()
                except Exception:
                    body = e.response.text
                logger.error(
                    "ElevenLabs create-voice-from-preview 400/4xx body: %s",
                    body,
                )
                raise RuntimeError(f"ElevenLabs create-voice-from-preview failed: {body}") from e
            data = resp.json()
            return data.get("voice_id", generated_voice_id)
    
    async def _tts_with_voice(self, client: httpx.AsyncClient, voice_id: str, text: str) -> bytes:
        """Generate TTS audio with a specific ElevenLabs voice."""
        resp = await client.post(
            f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                    "style": 0.5,
                },
            },
        )
        resp.raise_for_status()
        return resp.content

    async def generate_training_sample(
        self,
        generated_voice_id: str,
    ) -> bytes:
        """Generate a long (~45 second) sample for Fish Audio training.

        Uses the approved voice from previews to speak the phonetically rich
        VOICE_TRAINING_TEXT. This audio becomes Fish Audio's training input.

        Args:
            generated_voice_id: The voice ID from the approved preview

        Returns: MP3 audio bytes (~45 seconds)
        """
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{ELEVENLABS_BASE}/text-to-speech/{generated_voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": VOICE_TRAINING_TEXT,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                        "style": 0.5,
                    },
                },
            )
            resp.raise_for_status()

            audio_bytes = resp.content
            logger.info(f"ElevenLabs training sample: {len(audio_bytes)} bytes (~45s)")
            return audio_bytes

    @staticmethod
    def _extract_gender(description: str) -> str:
        desc_lower = description.lower()
        if any(w in desc_lower for w in ("woman", "female", "girl", "she")):
            return "female"
        if any(w in desc_lower for w in ("man", "male", "boy", "he")):
            return "male"
        return "female"

    @staticmethod
    def _extract_age(description: str) -> str:
        desc_lower = description.lower()
        if any(w in desc_lower for w in ("young", "20s", "teen")):
            return "young"
        if any(w in desc_lower for w in ("middle", "40s", "50s")):
            return "middle_aged"
        if any(w in desc_lower for w in ("old", "elder", "60s", "70s", "grandpa", "grandma")):
            return "old"
        return "young"


_instance = None



def get_elevenlabs_service() -> ElevenLabsService:
    global _instance
    if _instance is None:
        _instance = ElevenLabsService()
    return _instance


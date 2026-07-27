"""Shared helper: take a raw TTS tempfile, post-process it, and upload
both outputs (lipsync WAV + master MP3) to R2 under derived keys.

Used by every TTS provider — services.fish_audio.FishAudioService and
the three render_providers TTS classes — so the broadcast-quality
post-process chain (de-ess / EQ / compand / loudnorm) and the dual
output layout are identical everywhere.

If post-processing fails, the helper uploads the raw input bytes to
BOTH keys (so renders still work, just unprocessed). Every except
captures to Sentry.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

import sentry_sdk

logger = logging.getLogger(__name__)


def _derive_lipsync_key(mix_key: str) -> str:
    """Convert an mp3 output key into the sibling 16k-WAV lipsync key.

    ``tts/<voice>/<ts>.mp3`` → ``tts/<voice>/<ts>.lipsync.wav``
    Falls back to ``<mix_key>.lipsync.wav`` if the input doesn't end
    in ``.mp3``.
    """
    if mix_key.lower().endswith(".mp3"):
        return mix_key[: -len(".mp3")] + ".lipsync.wav"
    return mix_key + ".lipsync.wav"


async def post_process_and_upload(
    *,
    r2,
    raw_tmp_path: str,
    mix_r2_key: str,
    clip_mic_enabled: bool = False,
    scene_chain_id: Optional[str] = None,
    block_id: Optional[str] = None,
) -> tuple[str, str]:
    """Run post_process_voice on ``raw_tmp_path`` and upload both outputs
    to R2. Returns ``(mix_r2_key, lipsync_r2_key)``.

    - ``mix_r2_key``: the canonical TTS audio key (now a 44.1 kHz mono
      MP3 instead of the raw TTS output). Existing call sites that
      already computed this key keep using it; the underlying object
      is just higher-quality now.
    - ``lipsync_r2_key`` (derived): the 16 kHz mono WAV that the lipsync
      engine consumes. Caller should persist this on the variant as
      ``tts_lipsync_r2_key``.

    On any failure, the raw bytes are uploaded to BOTH keys so the
    render path still succeeds (no engine names in user-facing
    errors — internal logs may name engines).
    """
    from services.media_processing import post_process_voice

    lipsync_r2_key = _derive_lipsync_key(mix_r2_key)

    tmpdir = tempfile.mkdtemp(prefix=f"voice_pp_{(block_id or 'tts')[:12]}_")
    out_lipsync = os.path.join(tmpdir, "out.lipsync.wav")
    out_mix = os.path.join(tmpdir, "out.mix.mp3")
    try:
        lip_path, mix_path = await post_process_voice(
            raw_tmp_path,
            out_lipsync,
            out_mix,
            clip_mic_enabled=clip_mic_enabled,
            scene_chain_id=scene_chain_id,
            block_id=block_id,
        )

        # post_process_voice falls back to (input, input) on any
        # internal failure. In that case the same raw file gets
        # uploaded to both keys, which is fine — renders still work,
        # just with unprocessed audio.
        if lip_path == raw_tmp_path and mix_path == raw_tmp_path:
            logger.warning(
                "post_process_and_upload: post_process_voice fell back "
                "to raw input for block=%s; uploading raw to both keys",
                block_id,
            )
            await r2.upload_file(raw_tmp_path, lipsync_r2_key, content_type="audio/wav")
            await r2.upload_file(raw_tmp_path, mix_r2_key, content_type="audio/mpeg")
            return mix_r2_key, lipsync_r2_key

        # Upload the high-fidelity MP3 master under the canonical key
        # (mix_r2_key) so consumers reading tts_r2_key get the broadcast
        # version. The 16k WAV goes under the derived lipsync key.
        await r2.upload_file(mix_path, mix_r2_key, content_type="audio/mpeg")
        await r2.upload_file(lip_path, lipsync_r2_key, content_type="audio/wav")
        return mix_r2_key, lipsync_r2_key
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "post_process_and_upload failed for block=%s: %s — "
            "falling back to raw upload",
            block_id, e,
        )
        try:
            await r2.upload_file(raw_tmp_path, lipsync_r2_key, content_type="audio/wav")
            await r2.upload_file(raw_tmp_path, mix_r2_key, content_type="audio/mpeg")
        except Exception as upload_exc:
            sentry_sdk.capture_exception(upload_exc)
            raise
        return mix_r2_key, lipsync_r2_key
    finally:
        # Clean up tempdir best-effort. The two outputs are short-lived.
        try:
            for p in (out_lipsync, out_mix):
                if os.path.exists(p):
                    os.unlink(p)
            os.rmdir(tmpdir)
        except OSError:
            pass


def apply_mic_style(voice_description: str, clip_mic_enabled: bool) -> str:
    """Append a mic-style block to ``voice_description`` so the TTS
    model generates audio that already SOUNDS like the chosen mic.

    Default OFF = phone mic. ON = lavalier clip mic. The text below is
    the user-vetted spec — DO NOT edit casually; the model's output
    is sensitive to specific phrases like "clip-on lavalier" vs
    "smartphone microphone".
    """
    base = (voice_description or "").rstrip()
    if clip_mic_enabled:
        suffix = (
            ". Recorded through a clip-on lavalier microphone. "
            "Close intimate pickup, slight proximity effect with warm low-end boost, "
            "minimal room reverb, natural breath sounds present, "
            "slightly compressed dynamic range. NOT a studio condenser mic, "
            "NOT a phone mic — specifically a small lapel clip mic."
        )
    else:
        suffix = (
            ". Recorded on a smartphone microphone. "
            "Natural room acoustics, slight distance from speaker, "
            "ambient background present but not distracting, "
            "casual authentic feel like a real TikTok or Instagram creator "
            "filming in their room."
        )
    if not base:
        return suffix.lstrip(". ").lstrip()
    return base + suffix

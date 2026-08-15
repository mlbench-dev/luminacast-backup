"""Avatar style_dna endpoints — split from the former routers/avatar.py."""

from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy import update as sa_update
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User, TeamRole
from models.avatar import Avatar, AvatarType, AvatarStatus, BodyShotSet
from models.avatar_look import AvatarLook
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.fish_audio import get_fish_audio_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)
import re

logger = logging.getLogger(__name__)

from ._shared import normalize_tiktok_input, _r2_key_to_url, AvatarResponse, _avatar_to_response, _BODY_MOTION_POSES, _BODY_MOTION_POSE_LABELS, _seed_body_motion_looks_from_body_shot_set

router = APIRouter()

class StyleDNARequest(BaseModel):
    urls: list[str]

def _strip_style_dna_json_fences(raw: str) -> str:
    """Pull a JSON object out of a possibly-fenced LLM reply."""
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
        cleaned = cleaned.strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start : end + 1]
    return cleaned

async def _download_video_for_style(url: str, user_id: str) -> tuple[str, float]:
    """Download a single creator video to a temp mp4. Returns (path, duration_s)."""
    import hashlib
    import os
    import subprocess
    import tempfile

    video_hash = hashlib.md5(url.encode()).hexdigest()[:10]
    tmp_video = os.path.join(tempfile.gettempdir(), f"styledna_{user_id}_{video_hash}.mp4")
    result = subprocess.run(
        [
            "yt-dlp", "-f", "mp4/best[ext=mp4]/best",
            "--no-playlist", "--max-filesize", "60M",
            "-o", tmp_video, "--no-warnings", "--quiet", url,
        ],
        capture_output=True, timeout=90,
    )
    if result.returncode != 0 or not os.path.exists(tmp_video):
        raise RuntimeError(f"yt-dlp failed for {url}")

    probe = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", tmp_video,
        ],
        capture_output=True, timeout=10,
    )
    duration = 0.0
    if probe.returncode == 0:
        try:
            duration = float(probe.stdout.decode().strip())
        except (ValueError, AttributeError):
            duration = 0.0
    return tmp_video, duration

async def _extract_audio_wav(video_path: str) -> str:
    """ffmpeg → mono 16k WAV. Returns local path."""
    import os
    import subprocess
    import tempfile

    audio_path = os.path.join(
        tempfile.gettempdir(), f"styledna_audio_{os.path.basename(video_path)}.wav",
    )
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            audio_path,
        ],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0 or not os.path.exists(audio_path):
        raise RuntimeError(f"ffmpeg audio extract failed for {video_path}")
    return audio_path

async def _concat_audio_files(paths: list[str]) -> tuple[str, float]:
    """Concatenate WAV files via ffmpeg concat demuxer. Returns (path, duration_s)."""
    import os
    import subprocess
    import tempfile

    if len(paths) == 1:
        # Still probe duration for consistency
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", paths[0],
            ],
            capture_output=True, timeout=10,
        )
        try:
            return paths[0], float(probe.stdout.decode().strip())
        except (ValueError, AttributeError):
            return paths[0], 0.0

    list_path = os.path.join(tempfile.gettempdir(), f"concat_{uuid.uuid4().hex[:8]}.txt")
    with open(list_path, "w") as fh:
        for p in paths:
            fh.write(f"file '{p}'\n")
    out_path = os.path.join(tempfile.gettempdir(), f"styledna_combined_{uuid.uuid4().hex[:8]}.wav")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", out_path],
        capture_output=True, timeout=120,
    )
    try:
        os.unlink(list_path)
    except OSError:
        pass
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("ffmpeg concat failed")

    probe = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", out_path,
        ],
        capture_output=True, timeout=10,
    )
    duration = 0.0
    try:
        duration = float(probe.stdout.decode().strip())
    except (ValueError, AttributeError):
        duration = 0.0
    return out_path, duration

@router.post("/{avatar_id}/analyze-style")
async def analyze_style_dna(
    avatar_id: str,
    req: StyleDNARequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Analyze 1-3 creator videos to extract Style DNA for an avatar.

    Returns the persisted Style DNA dict. Voice clone id is also written to
    `avatar.voice_id` so the existing TTS path picks it up automatically.
    """
    import json
    import os

    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    urls = [u.strip() for u in (req.urls or []) if u and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="At least one video URL is required")
    urls = urls[:3]

    from services.r2_storage import get_r2_storage_service
    from services.gpu_server import get_gpu_server_client
    from services.fish_audio import get_fish_audio_service
    from services.openrouter import get_openrouter_service
    from services.usage_tracker import log_usage, calculate_llm_cost

    r2 = get_r2_storage_service()
    gpu_client = get_gpu_server_client()

    # ── Steps 1-4 per video, independent. Drop failures, keep survivors. ──
    survivors: list[dict] = []  # {url, audio_path, transcript, duration_s}
    tmp_files: list[str] = []

    for url in urls:
        try:
            # 1. download
            video_path, _video_dur = await _download_video_for_style(url, ctx.workspace_owner_id)
            tmp_files.append(video_path)
            await log_usage(
                db, user_id=ctx.workspace_owner_id, event_type="video_scrape",
                provider="apify", provider_cost_usd=0.05,
                quantity=1, quantity_unit="videos",
                resource_type="avatar", resource_id=avatar.id,
            )

            # 2. extract audio
            raw_audio = await _extract_audio_wav(video_path)
            tmp_files.append(raw_audio)

            # 3. (optional) BS-RoFormer voice separation. If the GPU server
            #    is unreachable or returns an error we degrade to the raw
            #    audio — voice cloning still works, just on noisier input.
            #    TODO: tighten the fallback once BS-RoFormer is more stable
            #    across the deployed fleet.
            clean_audio = raw_audio
            if gpu_client is not None:
                try:
                    audio_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/style_dna_input_{uuid.uuid4().hex[:8]}.wav"
                    await r2.upload_file(raw_audio, audio_key, content_type="audio/wav")
                    audio_url = r2.get_public_url(audio_key)
                    output_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/style_dna_vocals_{uuid.uuid4().hex[:8]}.wav"
                    audio_size_bytes = os.path.getsize(raw_audio)
                    audio_duration_s = audio_size_bytes / (16000 * 2)  # mono 16-bit
                    gpu_result = await gpu_client.bs_roformer(
                        audio_url, output_key, max_duration=90,
                        input_duration_seconds=audio_duration_s,
                    )
                    vocals_url = gpu_result.get("vocals_url") if gpu_result else None
                    if vocals_url:
                        import httpx
                        local_clean = os.path.join(
                            "/tmp", f"styledna_vocals_{uuid.uuid4().hex[:8]}.wav",
                        )
                        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as dl_client:
                            resp = await dl_client.get(vocals_url)
                            resp.raise_for_status()
                            with open(local_clean, "wb") as f:
                                f.write(resp.content)
                        clean_audio = local_clean
                        tmp_files.append(local_clean)
                        await log_usage(
                            db, user_id=ctx.workspace_owner_id, event_type="voice_separation",
                            provider="hostkey", provider_cost_usd=0.0,
                            quantity=audio_duration_s, quantity_unit="audio_seconds",
                            resource_type="avatar", resource_id=avatar.id,
                        )
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    logger.warning(
                        "style_dna: voice separation failed for %s, using raw audio: %s",
                        url, exc,
                    )

            # 4. transcribe. Try the dedicated GPU server first; if it's
            #    unreachable (box down, network issue) fall back to fal.ai
            #    Whisper — same degrade-gracefully pattern as the
            #    BS-RoFormer step above, using the tiered provider that
            #    already backs the live-reference pipeline.
            transcript_text = ""
            transcript_duration = 0.0
            transcribe_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar.id}/style_dna_transcribe_{uuid.uuid4().hex[:8]}.wav"
            await r2.upload_file(clean_audio, transcribe_key, content_type="audio/wav")
            transcribe_url = r2.get_public_url(transcribe_key)

            whisper_result = None
            transcription_provider = "hostkey"
            if gpu_client is not None:
                try:
                    whisper_result = await gpu_client.whisper_transcribe(transcribe_url)
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    logger.warning(
                        "style_dna: GPU whisper transcribe failed for %s, "
                        "falling back to fal Whisper: %s", url, exc,
                    )
            if whisper_result is None:
                from services.render_providers import FalWhisperProvider
                transcription_provider = "fal"
                whisper_result = await FalWhisperProvider().generate(
                    audio_url=transcribe_url, word_timestamps=False,
                )

            transcript_text = (whisper_result or {}).get("transcript", "") or ""
            transcript_duration = float((whisper_result or {}).get("duration_seconds", 0) or 0)
            await log_usage(
                db, user_id=ctx.workspace_owner_id, event_type="transcription",
                provider=transcription_provider, provider_cost_usd=0.0,
                quantity=transcript_duration, quantity_unit="audio_seconds",
                resource_type="avatar", resource_id=avatar.id,
            )
            if not transcript_text:
                raise RuntimeError("transcription returned empty")

            survivors.append({
                "url": url,
                "audio_path": clean_audio,
                "transcript": transcript_text,
                "duration_s": transcript_duration,
            })
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            logger.warning("style_dna: video %s failed: %s", url, exc)
            continue

    if not survivors:
        # Best-effort cleanup
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise HTTPException(
            status_code=500,
            detail="Could not analyze any of the provided videos — try different URLs.",
        )

    # ── Step 5: combined voice → Fish Speech clone ──
    voice_id_new = None
    try:
        combined_audio, voice_duration_s = await _concat_audio_files(
            [s["audio_path"] for s in survivors]
        )
        tmp_files.append(combined_audio)

        fish = get_fish_audio_service()
        clone_name = f"styledna_{avatar.id}"
        joined_transcript = " ".join(s["transcript"] for s in survivors)
        voice_id_new = await fish.clone_voice_from_file(
            combined_audio, name=clone_name, transcript=joined_transcript[:1500],
        )
        if voice_id_new:
            await log_usage(
                db, user_id=ctx.workspace_owner_id, event_type="voice_clone",
                provider="hostkey", provider_cost_usd=0.0,
                quantity=voice_duration_s, quantity_unit="audio_seconds",
                resource_type="avatar", resource_id=avatar.id,
            )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning("style_dna: voice clone failed (continuing without): %s", exc)
        voice_duration_s = sum(s.get("duration_s", 0.0) for s in survivors)

    # ── Step 6: Claude Sonnet style analysis ──
    full_transcript = " ".join(s["transcript"] for s in survivors)
    style_prompt = f"""Analyze this creator's content style from their transcript.
TRANSCRIPT (from {len(survivors)} video(s), {voice_duration_s:.0f}s of speech):
{full_transcript[:3000]}

Analyze and return JSON:
{{
  "tone": "2-4 word description (e.g. 'energetic, casual, lots of questions')",
  "avg_energy": 7,
  "avg_sentence_length": 8,
  "common_phrases": ["top 5 phrases they repeat"],
  "sentence_starters": ["how they typically start sentences"],
  "sign_offs": ["how they end videos"],
  "question_frequency": 0.3,
  "hook_pattern": "pattern_interrupt | question | claim | story | shock",
  "cut_frequency_seconds": 3.5,
  "broll_ratio": 0.45,
  "caption_preset": "hormozi_bold | karaoke_pop | minimal_lower | etc",
  "preferred_transitions": ["whip_pan", "jump_cut"],
  "music_energy": "low | medium | high",
  "music_genre": "pop | lofi | electronic | acoustic | cinematic"
}}"""

    try:
        oai = get_openrouter_service()
        raw = await oai.generate_text(
            prompt=style_prompt,
            system_prompt="You are a video content analyst. Return valid JSON only.",
            model="anthropic/claude-sonnet-4",
            temperature=0.5,
        )
        usage = getattr(oai, "last_usage", {}) or {}
        if usage:
            try:
                input_tokens = int(usage.get("prompt_tokens", 0) or 0)
                output_tokens = int(usage.get("completion_tokens", 0) or 0)
                total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
                cost = calculate_llm_cost("anthropic/claude-sonnet-4", input_tokens, output_tokens)
                await log_usage(
                    db, user_id=ctx.workspace_owner_id, event_type="style_dna_analysis",
                    provider="openrouter", provider_cost_usd=cost,
                    quantity=total_tokens, quantity_unit="tokens",
                    resource_type="avatar", resource_id=avatar.id,
                    provider_model="anthropic/claude-sonnet-4",
                )
            except Exception as inner_exc:
                sentry_sdk.capture_exception(inner_exc)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        # Tear down temp files before bailing
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="AI analysis temporarily unavailable")

    try:
        style_dna = json.loads(_strip_style_dna_json_fences(raw))
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="AI returned an unreadable analysis")

    for required_key in ("tone", "avg_energy", "common_phrases"):
        if required_key not in style_dna:
            sentry_sdk.capture_exception(
                ValueError(f"style_dna missing required key {required_key}")
            )
            for p in tmp_files:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail="AI returned an incomplete analysis")

    style_dna["voice_model_id"] = voice_id_new
    style_dna["voice_duration_s"] = round(voice_duration_s, 1)
    style_dna["source_urls"] = [s["url"] for s in survivors]
    style_dna["analyzed_at"] = datetime.utcnow().isoformat() + "Z"

    avatar.style_dna = style_dna
    if voice_id_new:
        # Style DNA owns the voice clone for this avatar — replaces any
        # earlier clone. Frontend warns the user before this point.
        avatar.voice_id = voice_id_new
    await db.commit()

    # Cleanup temp files (R2 uploads stay — they're cheap and aid debug).
    for p in tmp_files:
        try:
            os.unlink(p)
        except OSError:
            pass

    return {
        "style_dna": style_dna,
        "voice_duration_s": round(voice_duration_s, 1),
        "successful_videos": len(survivors),
        "requested_videos": len(urls),
    }

@router.delete("/{avatar_id}/style-dna")
async def reset_style_dna(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Clear Style DNA so the user can re-analyze with different videos.

    The avatar's voice clone (`voice_id`) is intentionally preserved — that
    column belongs to the avatar's identity, not to a particular Style DNA
    snapshot. Re-running analyze-style will overwrite it again.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")
    avatar.style_dna = None
    await db.commit()
    return {"ok": True}

"""Celery tasks for the live-reference pipeline.

    transcribe  — download source, (extract audio), chunk long audio on silence,
                  fal-Whisper each chunk, stitch with offset timestamps, enforce
                  the rolling monthly transcription budget, then chain to assess.
    assess      — strip PII, ask Opus 4.8 for a distilled Live Style Assessment
                  (JSON), persist it + per-beat exemplars, set status=assessed.

Both tasks set status=failed + capture to Sentry + re-raise on any exception so
Celery records the failure.
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta

import sentry_sdk

from tasks import celery_app

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Config (all env-overridable)
# ----------------------------------------------------------------------------
def _transcribe_engine() -> str:
    return os.environ.get("LIVE_TRANSCRIBE_ENGINE", "fal_whisper")


def _whisper_max_seconds() -> float:
    return float(os.environ.get("FAL_WHISPER_MAX_SECONDS", "1800"))


def _chunk_target_seconds() -> float:
    return float(os.environ.get("LIVE_REF_CHUNK_SECONDS", "600"))


def _monthly_cap_cents() -> int:
    return int(os.environ.get("LIVE_REF_MONTHLY_CAP_CENTS", "2000"))


def _cost_cents_per_minute() -> float:
    # fal Whisper ~ $0.006/min; kept as an env knob so billing can be tuned
    # without a code change. Used to attribute spend per record.
    return float(os.environ.get("LIVE_REF_COST_CENTS_PER_MIN", "0.6"))


def _make_session_factory():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from config import settings
    eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


# ----------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ----------------------------------------------------------------------------
def _probe_duration_seconds(path: str) -> float:
    """Return media duration in seconds via ffprobe (0.0 if unknown)."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(out.stdout or "{}")
    return float(info.get("format", {}).get("duration", 0.0) or 0.0)


def _extract_audio(video_path: str, out_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
         "-ar", "16000", "-ac", "1", out_path, "-y"],
        check=True, capture_output=True,
    )


def _detect_silence_breakpoints(audio_path: str) -> list[float]:
    """Run ffmpeg silencedetect and return silence-midpoint timestamps (s)."""
    proc = subprocess.run(
        ["ffmpeg", "-i", audio_path, "-af", "silencedetect=noise=-30dB:d=0.5",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    stderr = proc.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]
    points: list[float] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else s
        points.append((s + e) / 2.0)
    return sorted(points)


def plan_chunk_offsets(duration_s: float, silence_points: list[float],
                       target_s: float) -> list[tuple[float, float]]:
    """Plan (start, length) chunk cuts.

    Prefer cutting on a silence breakpoint nearest each ~target boundary; if
    silencedetect found nothing usable, fall back to fixed target_s cuts.
    Returns a list of (start_offset, length) tuples covering [0, duration_s].
    """
    if duration_s <= 0:
        return [(0.0, target_s)]
    cuts: list[float] = [0.0]
    if silence_points:
        next_boundary = target_s
        while next_boundary < duration_s:
            # nearest silence point to the ideal boundary
            candidate = min(silence_points, key=lambda p: abs(p - next_boundary))
            if candidate > cuts[-1] + 1.0 and candidate < duration_s:
                cuts.append(candidate)
                next_boundary = candidate + target_s
            else:
                next_boundary += target_s
    if len(cuts) <= 1:
        # fixed cuts fallback
        cuts = []
        t = 0.0
        while t < duration_s:
            cuts.append(t)
            t += target_s
    chunks: list[tuple[float, float]] = []
    for i, start in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else duration_s
        chunks.append((start, max(end - start, 0.0)))
    return chunks


def _cut_chunk(audio_path: str, start: float, length: float, out_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-ss", str(start), "-t", str(length), "-i", audio_path,
         "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path, "-y"],
        check=True, capture_output=True,
    )


def stitch_segments(chunk_results: list[dict], offsets: list[float]) -> tuple[str, list[dict]]:
    """Stitch per-chunk transcripts, shifting each chunk's segment timestamps
    by its start offset. Returns (full_text, merged_segments)."""
    texts: list[str] = []
    segments: list[dict] = []
    for result, offset in zip(chunk_results, offsets):
        txt = (result.get("text") or "").strip()
        if txt:
            texts.append(txt)
        for seg in result.get("segments") or []:
            segments.append({
                "text": seg.get("text", ""),
                "start": float(seg.get("start", 0.0)) + offset,
                "end": float(seg.get("end", 0.0)) + offset,
            })
    return " ".join(texts).strip(), segments


# ----------------------------------------------------------------------------
# transcribe task
# ----------------------------------------------------------------------------
@celery_app.task(name="tasks.live_reference.transcribe", bind=True, max_retries=1)
def transcribe(self, live_reference_id: str):
    asyncio.run(_transcribe_async(live_reference_id))


async def _transcribe_async(live_reference_id: str):
    from models.live_reference import LiveReference
    from services.r2_storage import get_r2_storage_service
    from services.render_providers import transcribe_audio
    from sqlalchemy import select

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    async with factory() as session:
        ref = await session.get(LiveReference, live_reference_id)
        if not ref:
            logger.error("LiveReference %s not found", live_reference_id)
            return
        try:
            engine = _transcribe_engine()
            if engine != "fal_whisper":
                raise NotImplementedError(f"transcription engine not supported: {engine}")

            # Rolling 30-day budget check for this user.
            cutoff = datetime.utcnow() - timedelta(days=30)
            spend_rows = await session.execute(
                select(LiveReference.monthly_spend_cents)
                .where(LiveReference.user_id == ref.user_id)
                .where(LiveReference.created_at >= cutoff)
            )
            prior_spend = sum(int(c or 0) for (c,) in spend_rows.all())
            if prior_spend >= _monthly_cap_cents():
                ref.status = "failed"
                ref.error_message = "monthly transcription budget reached"
                await session.commit()
                return

            ref.status = "transcribing"
            await session.commit()

            with tempfile.TemporaryDirectory() as tmp:
                src_path = os.path.join(tmp, "source")
                await r2.download_file(ref.source_r2_key, src_path)

                audio_path = os.path.join(tmp, "audio.wav")
                if ref.media_kind == "video":
                    _extract_audio(src_path, audio_path)
                else:
                    # normalise audio to a known format for ffprobe + cutting
                    _extract_audio(src_path, audio_path)

                duration = _probe_duration_seconds(audio_path)
                ref.duration_seconds = duration

                if duration > _whisper_max_seconds():
                    silence = _detect_silence_breakpoints(audio_path)
                    plan = plan_chunk_offsets(duration, silence, _chunk_target_seconds())
                else:
                    plan = [(0.0, duration or _chunk_target_seconds())]

                chunk_results: list[dict] = []
                offsets: list[float] = []
                for idx, (start, length) in enumerate(plan):
                    chunk_path = os.path.join(tmp, f"chunk_{idx}.wav")
                    if len(plan) == 1 and start == 0.0:
                        chunk_path = audio_path
                    else:
                        _cut_chunk(audio_path, start, length, chunk_path)
                    chunk_key = f"tmp/live_references/{live_reference_id}/chunk_{idx}_{uuid.uuid4().hex[:8]}.wav"
                    await r2.upload_file(chunk_path, chunk_key, content_type="audio/wav")
                    chunk_url = r2.get_public_url(chunk_key)
                    chunk_results.append(await transcribe_audio(chunk_url, language="en"))
                    offsets.append(start)

                full_text, segments = stitch_segments(chunk_results, offsets)

            ref.transcript_text = full_text
            ref.transcript_segments = segments
            ref.monthly_spend_cents = int(round((duration / 60.0) * _cost_cents_per_minute()))
            ref.status = "transcribed"
            await session.commit()

            from tasks.live_reference import assess
            assess.delay(live_reference_id)

        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Live reference transcribe failed for %s: %s", live_reference_id, e)
            ref.status = "failed"
            ref.error_message = str(e)[:500]
            await session.commit()
            raise


# ----------------------------------------------------------------------------
# assess task (Opus 4.8)
# ----------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s().]{7,}\d)(?!\d)")
_HANDLE_RE = re.compile(r"(?<![\w/])@[A-Za-z0-9_]{2,}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")


def strip_pii(text: str) -> str:
    """Replace emails, phone numbers, @handles and URLs with [redacted].

    URLs and emails are redacted before phone numbers so a phone-like run
    inside a URL isn't half-redacted.
    """
    if not text:
        return text
    text = _URL_RE.sub("[redacted]", text)
    text = _EMAIL_RE.sub("[redacted]", text)
    text = _HANDLE_RE.sub("[redacted]", text)
    text = _PHONE_RE.sub("[redacted]", text)
    return text


_ASSESS_SYSTEM = """You analyze a creator's PAST LIVE SELLING SESSION transcript and distil ONLY reusable
patterns — cadence, structure, disfluency texture, and selling moves. You never copy long
verbatim slices; exemplars must be SHORT (a phrase or one sentence). Output STRICT JSON
matching exactly this schema (no markdown, no commentary):

{
  "register": "energy/pace/formality summary (1-2 lines)",
  "disfluency_profile": {"fillers": ["..."], "self_correction": "low|med|high", "repetition": "..."},
  "openers": ["..."], "transitions": ["..."], "urgency_scarcity": ["..."],
  "price_framing": ["..."], "objection_handling": ["..."], "cross_sell": ["..."],
  "cta": {"cadence_min": [5,8], "phrasings": ["..."]},
  "exemplar_bank": [{"beat": "hook|demo|objection|cta", "text": "short snippet"}],
  "provenance": {"source": "own_live", "duration_min": 0}
}"""


def _parse_json_strict(raw: str) -> dict | None:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if lines and lines[-1].strip() == "```" else "\n".join(lines[1:])
        cleaned = cleaned.strip()
    if not cleaned.startswith("{"):
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e != -1:
            cleaned = cleaned[s:e + 1]
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


@celery_app.task(name="tasks.live_reference.assess", bind=True, max_retries=1)
def assess(self, live_reference_id: str):
    asyncio.run(_assess_async(live_reference_id))


async def _assess_async(live_reference_id: str):
    from sqlalchemy import update
    from models.live_reference import LiveReference, LiveReferenceExemplar
    from services.openrouter import get_openrouter_service
    from services.creative_models import CAST_GENERATOR_MODEL, log_creative_model_use

    factory = _make_session_factory()
    async with factory() as session:
        ref = await session.get(LiveReference, live_reference_id)
        if not ref:
            logger.error("LiveReference %s not found for assess", live_reference_id)
            return
        try:
            transcript = strip_pii(ref.transcript_text or "")
            if len(transcript) > 30000:
                transcript = transcript[:30000]

            duration_min = int(round((ref.duration_seconds or 0) / 60.0))
            user_prompt = (
                f"Duration: ~{duration_min} minutes.\n"
                f"Redacted transcript follows. Extract distilled patterns ONLY.\n\n"
                f"{transcript}"
            )

            oai = get_openrouter_service()
            log_creative_model_use("live_reference_assess", CAST_GENERATOR_MODEL)
            raw = await oai.generate_text(
                prompt=user_prompt,
                system_prompt=_ASSESS_SYSTEM,
                model=CAST_GENERATOR_MODEL,
                max_tokens=2048,
                temperature=0.3,
            )
            assessment = _parse_json_strict(raw)
            if assessment is None:
                raw2 = await oai.generate_text(
                    prompt=user_prompt + "\n\nReturn ONLY valid JSON.",
                    system_prompt=_ASSESS_SYSTEM,
                    model=CAST_GENERATOR_MODEL,
                    max_tokens=2048,
                    temperature=0.2,
                )
                assessment = _parse_json_strict(raw2)
            if assessment is None:
                raise ValueError("assessment did not return valid JSON")

            assessment.setdefault("provenance", {})
            assessment["provenance"]["source"] = "own_live"
            assessment["provenance"]["duration_min"] = duration_min

            ref.assessment = assessment

            # Persist exemplars as text rows (no embedding helper on this deploy
            # — Step 5 ranks by token overlap). embedding stays null.
            for ex in (assessment.get("exemplar_bank") or []):
                txt = (ex.get("text") or "").strip()
                if not txt:
                    continue
                session.add(LiveReferenceExemplar(
                    id=f"lrex_{uuid.uuid4().hex[:16]}",
                    live_reference_id=ref.id,
                    beat=(ex.get("beat") or "hook")[:20],
                    text=txt,
                    embedding=None,
                ))

            # Auto-activate: a freshly-assessed upload becomes the one used
            # for future scripts by default, matching the old "most recent
            # wins" behavior — the user can switch back to an earlier one
            # afterward via POST /live-references/{id}/activate instead of
            # this always winning by upload time alone.
            scope_field = LiveReference.avatar_id if ref.avatar_id else LiveReference.cast_id
            scope_value = ref.avatar_id or ref.cast_id
            await session.execute(
                update(LiveReference)
                .where(scope_field == scope_value, LiveReference.id != ref.id)
                .values(is_active=False)
            )
            ref.is_active = True
            ref.status = "assessed"
            await session.commit()

        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.exception("Live reference assess failed for %s: %s", live_reference_id, e)
            ref.status = "failed"
            ref.error_message = str(e)[:500]
            await session.commit()
            raise

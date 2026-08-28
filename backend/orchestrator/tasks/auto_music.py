"""Auto-generate AI background music for a cast (Mubert v3).

Runs after a cast's outline is created. Fires Mubert track generation,
polls until done, downloads the MP3, uploads to R2, and writes
background_music_url / mood / tags onto the cast row. Editor reads these
to place the music on the lowest audio track at 15% volume; the FFmpeg
render ducks it under voice.

Why Celery: Mubert generation is 5-30s of polling; we don't want to
block the user's outline-review screen. The endpoint returns immediately
and this task upgrades the cast in the background. The editor polls the
cast status and picks up the music URL when it appears.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter

import httpx
import sentry_sdk

from tasks import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.auto_music.generate_for_cast",
    queue="default",
    # 10 minutes is generous \u2014 a 60s Mubert track typically takes ~10s,
    # and we only ever block on one polling loop.
    task_time_limit=600,
)
def generate_music_for_cast_task(cast_id: str) -> dict:
    return asyncio.run(_generate_async(cast_id))


async def _generate_async(cast_id: str) -> dict:
    from database import async_session_factory
    from models.cast import Cast
    from models.block import Block
    from models.user import User
    from services.mubert import (
        get_mubert_service_optional,
        mood_to_prompt,
        MubertConfigurationError,
        MubertGenerationError,
    )
    from services.r2_storage import get_r2_storage_service

    mubert = get_mubert_service_optional()
    if mubert is None:
        logger.info("Mubert not configured \u2014 skipping auto-music for cast %s", cast_id)
        return {"skipped": True, "reason": "not_configured"}

    async with async_session_factory() as db:
        cast = await db.get(Cast, cast_id)
        if cast is None:
            return {"error": "cast_not_found"}
        if cast.background_music_url:
            # Already has music \u2014 don't regenerate (user might have picked one).
            return {"skipped": True, "reason": "already_set", "url": cast.background_music_url}

        # Respect the user's music choice. "off" means no music at all;
        # "track_id:<id>" pins a fixed library track instead of generating one.
        choice = getattr(cast, "music_track_choice", "auto") or "auto"
        if choice == "off":
            return {"skipped": True, "reason": "music_off"}
        if choice.startswith("track_id:"):
            from services.music_library import get_track_url, parse_choice, UnknownMusicTrackError
            track_id = parse_choice(choice)
            try:
                if track_id:
                    cast.background_music_url = get_track_url(track_id)
                    await db.commit()
                    return {"skipped": True, "reason": "library_track", "url": cast.background_music_url}
            except UnknownMusicTrackError as e:
                sentry_sdk.capture_exception(e)
                # Fall through to auto-generation on a bad id.

        user = await db.get(User, cast.user_id)
        if user is None:
            return {"error": "user_not_found"}

        # Determine dominant mood from blocks. Eager-load variants so the
        # duration calculation below sees actual TTS lengths (lazy-loading
        # in async greenlet-less context will raise).
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        rows = await db.execute(
            select(Block)
            .options(selectinload(Block.variants))
            .where(Block.cast_id == cast_id, Block.deleted_at.is_(None))
        )
        blocks = rows.scalars().all()
        if not blocks:
            return {"skipped": True, "reason": "no_blocks"}

        moods = [b.mood for b in blocks if b.mood]
        dominant_mood = Counter(moods).most_common(1)[0][0] if moods else "enthusiastic"

        # Sum estimated duration from the actual variants — the prior
        # heuristic (8s × N blocks) ignored the user's chosen length, so a
        # 60s cast got 8 × 7 = 56s of music, then a long music tail extended
        # the timeline past the script. Scale to actual TTS length when we
        # have it, plus a tiny tail (≤2s) to avoid an abrupt music cutoff.
        # For very short casts (≤90s total) we DROP the tail entirely.
        total_duration = 0.0
        for b in blocks:
            block_secs = 0.0
            for v in getattr(b, "variants", []) or []:
                tts = float(getattr(v, "tts_duration_seconds", 0) or 0)
                est = float(getattr(v, "estimated_duration_seconds", 0) or 0)
                block_secs = max(block_secs, tts, est)
            if block_secs <= 0:
                # No variant info yet — fall back to 8s, but this is rare
                # because auto_music is dispatched after TTS is known.
                block_secs = 8.0
            total_duration += block_secs
        # Mubert minimum is 15s, max 1500s.
        if total_duration <= 90:
            tail = 0  # short cast — no tail, music ends with last block
        else:
            tail = 2  # long cast — small tail so music doesn't snap off
        total_duration = max(30, min(int(total_duration + tail), 600))

        # Intensity: high if there's a CTA/hook, else medium.
        intensities = {b.energy_level for b in blocks if getattr(b, "energy_level", None)}
        intensity = "high" if "high" in intensities else ("low" if intensities == {"low"} else "medium")

        prompt = mood_to_prompt(dominant_mood)
        logger.info(
            "Auto-music starting cast=%s mood=%s prompt=%r duration=%ds intensity=%s",
            cast_id, dominant_mood, prompt, total_duration, intensity,
        )

        track_url: str | None = None
        source = "generated"
        try:
            result = await mubert.generate_for_user(
                db=db, user=user, prompt=prompt,
                duration_seconds=total_duration, intensity=intensity,
            )
            track_url = result.get("url")
        except Exception as e:
            # On-demand generation failed. The common case in production is the
            # Mubert monthly track-generation cap (HTTP 403
            # LicenseLimitTracksCount) — but a config/network error lands here
            # too. Fall back to Mubert's curated 12K-track library (no
            # generation quota), then to the fixed self-hosted set, so "Auto"
            # still lands music instead of silently doing nothing.
            sentry_sdk.capture_exception(e)
            logger.warning(
                "Auto-music generation failed for cast %s (%s) — falling back to curated library",
                cast_id, e,
            )
            track_url = await _pick_library_track(mubert, db, user, dominant_mood)
            source = "library"

        if not track_url:
            logger.warning("Auto-music: no track available for cast %s (generation + library both failed)", cast_id)
            return {"skipped": True, "reason": "no_track_available"}

        # Download and re-host on R2 so the URL doesn't expire.
        music_key = f"music/casts/{cast_id}/background.mp3"
        public_url = f"https://media.luminacast.com/{music_key}"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(track_url)
                resp.raise_for_status()
                mp3_bytes = resp.content
            r2 = get_r2_storage_service()
            await r2.upload_bytes(mp3_bytes, music_key, content_type="audio/mpeg")
        except Exception as e:
            sentry_sdk.capture_exception(e)
            # Fall back to the Mubert URL if R2 upload fails. It's valid
            # for a few hours which is enough for the user to render.
            public_url = track_url

        cast.background_music_url = public_url
        cast.background_music_mood = dominant_mood
        cast.background_music_tags = prompt.split() if prompt else None
        await db.commit()
        logger.info(
            "Auto-music attached cast=%s mood=%s source=%s url=%s",
            cast_id, dominant_mood, source, public_url[:80],
        )
        return {
            "cast_id": cast_id,
            "url": public_url,
            "mood": dominant_mood,
            "duration": total_duration,
            "source": source,
        }


def _extract_track_url(t: dict) -> str | None:
    """Pull a playable URL out of a raw Mubert curated-library track row."""
    if not isinstance(t, dict):
        return None
    gens = t.get("generations") or []
    if gens and isinstance(gens[0], dict) and gens[0].get("url"):
        return gens[0]["url"]
    return t.get("url") or None


# Fixed-library moods are coarse buckets — map the block-level mood vocabulary
# onto them for the last-resort self-hosted set.
_FIXED_LIBRARY_MOOD_MAP = {
    "energetic": "energetic", "playful": "energetic",
    "excited": "high-energy", "urgent": "high-energy", "hype": "high-energy", "triumphant": "high-energy",
    "calm": "calm", "intimate": "calm", "emotional": "calm", "dreamy": "calm", "mysterious": "calm",
    "informative": "neutral", "trustworthy": "neutral", "confident": "neutral", "corporate": "neutral",
    "cinematic": "dramatic", "dramatic": "dramatic",
}


async def _pick_library_track(mubert, db, user, mood: str) -> str | None:
    """Best-effort music URL when on-demand generation is unavailable.

    Order: Mubert's curated library filtered by mood → curated library
    unfiltered → the fixed self-hosted set (loosely mood-matched) → None.
    Every step is wrapped so one failure just moves to the next.
    """
    # 1 + 2: Mubert curated library (no generation quota).
    try:
        customer_id, access_token = await mubert.ensure_user_customer(db, user)
        for moods_filter in ([mood] if mood else None, None):
            try:
                data = await mubert.search_library(
                    customer_id, access_token, moods=moods_filter, limit=30,
                )
            except Exception as e:  # noqa: BLE001 — try the next variant
                sentry_sdk.capture_exception(e)
                continue
            for row in (data.get("tracks") or []):
                url = _extract_track_url(row)
                if url:
                    logger.info("Auto-music fallback: using curated library track (mood=%s)", mood)
                    return url
    except Exception as e:  # noqa: BLE001
        sentry_sdk.capture_exception(e)

    # 3: fixed self-hosted library.
    try:
        from services.music_library import list_tracks
        tracks = list_tracks()
        if tracks:
            want = _FIXED_LIBRARY_MOOD_MAP.get((mood or "").lower(), "")
            match = next((t for t in tracks if t.mood == want), tracks[0])
            logger.info("Auto-music fallback: using fixed library track %s", match.id)
            return match.url
    except Exception as e:  # noqa: BLE001
        sentry_sdk.capture_exception(e)

    return None

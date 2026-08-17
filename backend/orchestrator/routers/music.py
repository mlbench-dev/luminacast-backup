"""MUSIC tab endpoints — sound casts + track generation."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.sound_cast import SoundCast, SoundCastStatus, MusicTrack, MusicTrackStatus
from models.user import User
from routers.auth import get_current_user
from services import audit_log
from services.r2_storage import get_r2_storage_service
from tasks.music import generate_track_task, train_sound_cast_task
import sentry_sdk

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/music", tags=["music"])


CDN_BASE = "https://media.luminacast.com"


# ── Pydantic schemas ──

class SoundCastCreate(BaseModel):
    name: str
    description: str = ""


class GenerateTrackRequest(BaseModel):
    name: str
    prompt: str
    lyrics: str = ""
    duration_seconds: float = 60.0
    seed: int = -1
    guidance_scale: float = 15.0
    inference_steps: int = 150
    scheduler_type: str = "euler"


class StartTrainingRequest(BaseModel):
    training_prompts: list[str] = []
    steps: int = 5000


# ── Helpers ──

def _sc_to_dict(sc: SoundCast) -> dict:
    return {
        "id": sc.id,
        "name": sc.name,
        "description": sc.description,
        "status": sc.status.value,
        "training_audio_count": len(sc.training_audio_keys or []),
        "training_audio_keys": sc.training_audio_keys or [],
        "training_prompts": sc.training_prompts or [],
        "training_steps": sc.training_steps,
        "training_loss": sc.training_loss,
        "training_error": sc.training_error,
        "lora_r2_key": sc.lora_r2_key or "",
        "created_at": sc.created_at.isoformat() if sc.created_at else None,
    }


def _track_to_dict(t: MusicTrack) -> dict:
    return {
        "id": t.id,
        "sound_cast_id": t.sound_cast_id,
        "name": t.name,
        "prompt": t.prompt,
        "lyrics": t.lyrics,
        "status": t.status.value,
        "audio_url": f"{CDN_BASE}/{t.audio_r2_key}" if t.audio_r2_key else "",
        "duration_seconds": t.actual_duration_seconds,
        "generation_time_seconds": t.generation_time_seconds,
        "generation_error": t.generation_error,
        "seed": t.seed,
        "guidance_scale": t.guidance_scale,
        "inference_steps": t.inference_steps,
        "scheduler_type": t.scheduler_type,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


# ── Sound Cast CRUD ──

@router.get("/sound-casts")
async def list_sound_casts(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(SoundCast).where(
            SoundCast.user_id == user.id,
            SoundCast.deleted_at.is_(None),
        ).order_by(SoundCast.created_at.desc())
    )
    return {"sound_casts": [_sc_to_dict(sc) for sc in rows.scalars().all()]}


@router.get("/sound-casts/{sc_id}")
async def get_sound_cast(sc_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id or sc.deleted_at is not None:
        raise HTTPException(404, "SoundCast not found")
    return _sc_to_dict(sc)


@router.post("/sound-casts")
async def create_sound_cast(req: SoundCastCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not req.name or len(req.name) > 80:
        raise HTTPException(400, "Name required, 1-80 chars")
    sc = SoundCast(
        id=f"sc_{uuid.uuid4().hex[:12]}",
        user_id=user.id,
        name=req.name,
        description=req.description,
        training_audio_keys=[],
        training_prompts=[],
    )
    db.add(sc)
    await db.commit()
    await db.refresh(sc)
    try:
        await audit_log.record(
            db, user_id=user.id, action="music.soundcast_create", entity_type="music",
            entity_id=sc.id, after={"name": sc.name},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _sc_to_dict(sc)


@router.post("/sound-casts/{sc_id}/training-audio")
async def upload_training_audio(
    sc_id: str,
    file: UploadFile = File(...),
    prompt: str = Form(""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    if sc.status not in (SoundCastStatus.DRAFT, SoundCastStatus.TRAINING_PENDING):
        raise HTTPException(400, "Cannot add training audio after training started")

    allowed_types = ("audio/wav", "audio/mpeg", "audio/mp3", "audio/x-wav", "audio/flac", "audio/x-flac", "audio/x-m4a", "audio/aac", "audio/ogg", "audio/webm", "audio/x-aiff", "application/octet-stream")
    allowed_exts = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".webm", ".aiff")
    fname = (file.filename or "").lower()
    ext_ok = any(fname.endswith(ext) for ext in allowed_exts)
    type_ok = not file.content_type or file.content_type in allowed_types
    if not ext_ok and not type_ok:
        raise HTTPException(400, "Only WAV, MP3, FLAC, M4A, AAC, OGG files accepted")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 50MB)")

    idx = len(sc.training_audio_keys or [])
    r2_key = f"music-training/{user.id}/{sc_id}/audio_{idx:03d}_{file.filename or 'track'}"
    r2 = get_r2_storage_service()
    await r2.upload_bytes(content, r2_key, content_type=file.content_type or "audio/wav")

    sc.training_audio_keys = (sc.training_audio_keys or []) + [r2_key]
    sc.training_prompts = (sc.training_prompts or []) + [prompt]
    if sc.status == SoundCastStatus.DRAFT:
        sc.status = SoundCastStatus.TRAINING_PENDING
    await db.commit()
    await db.refresh(sc)
    return _sc_to_dict(sc)


@router.delete("/sound-casts/{sc_id}/training-audio/{audio_idx}")
async def delete_training_audio(
    sc_id: str,
    audio_idx: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    if sc.status not in (SoundCastStatus.DRAFT, SoundCastStatus.TRAINING_PENDING):
        raise HTTPException(400, "Cannot modify training data after training started")

    keys = list(sc.training_audio_keys or [])
    prompts = list(sc.training_prompts or [])
    if audio_idx < 0 or audio_idx >= len(keys):
        raise HTTPException(400, "Invalid audio index")

    keys.pop(audio_idx)
    prompts.pop(audio_idx)
    sc.training_audio_keys = keys
    sc.training_prompts = prompts
    if len(keys) == 0:
        sc.status = SoundCastStatus.DRAFT
    await db.commit()
    return _sc_to_dict(sc)


@router.post("/sound-casts/{sc_id}/train")
async def start_training(sc_id: str, req: StartTrainingRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    if len(sc.training_audio_keys or []) < 5:
        raise HTTPException(400, "Need at least 5 training audio files (recommended: 8-20)")
    if sc.status not in (SoundCastStatus.DRAFT, SoundCastStatus.TRAINING_PENDING, SoundCastStatus.FAILED, SoundCastStatus.TRAINED):
        raise HTTPException(400, f"Cannot train from status={sc.status.value}")

    sc.training_steps = req.steps
    sc.training_error = ""
    await db.commit()

    train_sound_cast_task.delay(sc_id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="music.soundcast_train", entity_type="music",
            entity_id=sc_id, after={"steps": req.steps},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _sc_to_dict(sc)


@router.delete("/sound-casts/{sc_id}")
async def delete_sound_cast(sc_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    sc.deleted_at = datetime.utcnow()
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="music.soundcast_delete", entity_type="music",
            entity_id=sc_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"deleted": True}


# ── Track generation ──


@router.get("/tracks")
async def list_all_user_tracks(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    sound_cast_id: str = None,
    limit: int = 50,
):
    """List all music tracks across all sound-casts for the current user."""
    from sqlalchemy import select
    q = (
        select(MusicTrack)
        .join(SoundCast, MusicTrack.sound_cast_id == SoundCast.id)
        .where(SoundCast.user_id == user.id)
        .where(SoundCast.deleted_at.is_(None))
    )
    if sound_cast_id:
        q = q.where(MusicTrack.sound_cast_id == sound_cast_id)
    q = q.order_by(MusicTrack.created_at.desc()).limit(limit)
    result = await db.execute(q)
    tracks = result.scalars().all()
    return {"tracks": [_track_to_dict(t) for t in tracks]}

@router.get("/sound-casts/{sc_id}/tracks")
async def list_tracks(sc_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    rows = await db.execute(
        select(MusicTrack).where(
            MusicTrack.sound_cast_id == sc_id,
            MusicTrack.deleted_at.is_(None),
        ).order_by(MusicTrack.created_at.desc())
    )
    return {"tracks": [_track_to_dict(t) for t in rows.scalars().all()]}


@router.post("/sound-casts/{sc_id}/tracks")
async def generate_track(sc_id: str, req: GenerateTrackRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    if req.duration_seconds < 10 or req.duration_seconds > 600:
        raise HTTPException(400, "Duration must be 10-600 seconds")

    track = MusicTrack(
        id=f"mt_{uuid.uuid4().hex[:12]}",
        sound_cast_id=sc_id,
        user_id=user.id,
        name=req.name,
        prompt=req.prompt,
        lyrics=req.lyrics,
        duration_seconds=req.duration_seconds,
        seed=req.seed,
        guidance_scale=req.guidance_scale,
        inference_steps=req.inference_steps,
        scheduler_type=req.scheduler_type,
        status=MusicTrackStatus.PENDING,
    )
    db.add(track)
    await db.commit()
    await db.refresh(track)

    generate_track_task.delay(track.id)
    try:
        await audit_log.record(
            db, user_id=user.id, action="music.track_generate", entity_type="music",
            entity_id=track.id, after={"name": track.name, "duration_seconds": track.duration_seconds},
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return _track_to_dict(track)


@router.delete("/tracks/{track_id}")
async def delete_track(track_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from datetime import datetime
    t = await db.get(MusicTrack, track_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "Track not found")
    t.deleted_at = datetime.utcnow()
    await db.commit()
    try:
        await audit_log.record(
            db, user_id=user.id, action="music.track_delete", entity_type="music",
            entity_id=track_id,
        )
        await db.commit()
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return {"deleted": True}


@router.post("/sound-casts/{sc_id}/cancel-training")
async def cancel_training(sc_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sc = await db.get(SoundCast, sc_id)
    if not sc or sc.user_id != user.id:
        raise HTTPException(404, "SoundCast not found")
    if sc.status not in (SoundCastStatus.TRAINING_PENDING, SoundCastStatus.TRAINING_IN_PROGRESS):
        raise HTTPException(400, "Not currently training")
    sc.status = SoundCastStatus.DRAFT
    sc.training_error = None
    await db.commit()
    return _sc_to_dict(sc)


# =========================================================================
# AI MUSIC (Mubert v3) + SFX + Catalog + Uploaded
#
# These routes power the new Music page (Browse / AI Generate / SFX /
# Uploaded). The legacy ACE-Step "sound-casts" routes above are kept
# behind a frontend feature flag for future re-enablement, but new code
# uses this section.
# =========================================================================

import sentry_sdk
import httpx
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import text as _sa_text

from services.mubert import (
    get_mubert_service_optional,
    mood_to_prompt,
    MubertConfigurationError,
    MubertGenerationError,
    get_cached_library_params,
    pick_track_name,
)
from config import settings as app_settings


def _normalise_library_track(t: dict, *, theme_hint: Optional[str] = None) -> dict:
    """Map a single curated-library track to the frontend's expected shape.

    The upstream payload looks like:
      {
        "id": "...uuid...",
        "duration": 300,
        "intensity": "high",
        "mode": "track",
        "bpm": 120,
        "key": "D",
        "generations": [{"status": "done", "url": "https://...mp3", ...}],
      }
    There is no top-level name/title or genre/mood on the track itself.
    The title always comes from the per-track name pool (see
    services.mubert.pick_track_name) derived from bpm/key/intensity, so
    every row gets a distinct, evocative, stable name — including under a
    genre/mood filter, where naively using the filter's own name as the
    title made every result in the list show the identical title (e.g.
    filtering by "Atmosphere" showed a list of tracks all titled
    "Atmosphere"). The applied filter is surfaced in the description
    instead, since it's still useful context, just not a good title.
    """
    if not isinstance(t, dict):
        return {}
    gens = t.get("generations") or []
    primary_gen = gens[0] if gens else {}
    if not isinstance(primary_gen, dict):
        primary_gen = {}
    url = primary_gen.get("url") or t.get("url") or ""
    track_id = t.get("id") or primary_gen.get("session_id") or ""
    bpm = t.get("bpm")
    intensity = t.get("intensity") or ""
    mode = t.get("mode") or ""
    key = t.get("key") or ""
    duration_v = t.get("duration") or 0

    name, vibe_label = pick_track_name(str(track_id), key, intensity)

    # Description — every signal we have, in a compact one-liner. The
    # frontend renders this directly under the title. Lead with the
    # applied filter (theme/genre/mood) when present, else the derived
    # vibe label (e.g. "Dreamy") — either way the card reads as having a
    # genre/mood even though Mubert's track object doesn't carry one.
    desc_parts: list[str] = []
    if theme_hint:
        desc_parts.append(theme_hint)
    elif vibe_label:
        desc_parts.append(vibe_label)
    if bpm:
        desc_parts.append(f"{int(bpm)} BPM")
    if key:
        desc_parts.append(str(key))
    if intensity:
        desc_parts.append(intensity)
    if mode and mode != "track":  # "track" is the default; only show jingle/mix
        desc_parts.append(mode)
    description = " · ".join(desc_parts)

    display_mood = theme_hint or vibe_label
    return {
        "id": str(track_id),
        "name": name,
        "description": description,
        "mood": display_mood,
        "genre": theme_hint or "",
        "moods": [display_mood] if display_mood else [],
        "genres": [theme_hint] if theme_hint else [],
        "intensity": intensity,
        "mode": mode,
        "tempo": str(bpm) if bpm else "",
        "bpm": bpm,
        "key": key,
        "duration": int(duration_v) if duration_v else 0,
        "prompt": ", ".join(p for p in [theme_hint, intensity] if p),
        "url": url,
        "created_at": primary_gen.get("created_at") or primary_gen.get("generated_at"),
    }


def _name_options(items) -> list[dict]:
    """Coerce upstream `{value, tracks_count}` rows to `{label, value}`."""
    out: list[dict] = []
    for it in items or []:
        if isinstance(it, str):
            out.append({"label": it, "value": it})
        elif isinstance(it, dict):
            label = it.get("name") or it.get("title") or it.get("label") or it.get("value") or ""
            value = it.get("value") or it.get("id") or label
            if label and label != "":
                out.append({"label": str(label), "value": str(value)})
    return out


@router.get("/library/params")
async def list_library_params(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the available filter dimensions for the curated library.

    Cached for 12 hours per process; the upstream values change rarely.
    Falls back to a small static set when upstream is unavailable so the
    dropdowns always populate.
    """
    fallback = {
        "genres": [],
        "moods": [],
        "activities": [],
        "bpms": [
            {"label": "Slow (60–90)", "value": "90"},
            {"label": "Medium (90–120)", "value": "120"},
            {"label": "Fast (120–140)", "value": "140"},
        ],
        "durations": [
            {"label": "30 seconds", "value": "30"},
            {"label": "60 seconds", "value": "60"},
            {"label": "90 seconds", "value": "90"},
            {"label": "2 minutes", "value": "120"},
            {"label": "3 minutes", "value": "180"},
        ],
    }
    mubert = get_mubert_service_optional()
    if mubert is None:
        return fallback
    try:
        customer_id, access_token = await mubert.ensure_user_customer(db, user)
        data = await get_cached_library_params(mubert, customer_id, access_token)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return fallback

    if not isinstance(data, dict):
        return fallback

    genres = _name_options(data.get("genres"))
    # Upstream `themes` are the most useful "category" dimension for end
    # users (Cinematic, Corporate, Action, Documentary, etc.). Surface
    # them as "moods" since that's what the user thinks of them as.
    themes = _name_options(data.get("themes"))
    moods_native = _name_options(data.get("moods"))
    # Themes usually have far more tracks per entry, so they go first.
    moods = themes + moods_native
    activities = _name_options(data.get("activities"))

    # BPMs come back as ~50 individual numeric strings ("120", "132"…).
    # Bucket into broad ranges so the dropdown doesn't have 50 rows.
    bpm_values = sorted(
        {int(v.get("value")) for v in (data.get("bpm") or []) if str(v.get("value", "")).isdigit()}
    )
    if bpm_values:
        bpms = []
        for label, lo, hi in [
            ("Very slow (≤80)", 0, 80),
            ("Slow (80–100)", 80, 100),
            ("Medium (100–120)", 100, 120),
            ("Upbeat (120–140)", 120, 140),
            ("Fast (140–160)", 140, 160),
            ("Very fast (160+)", 160, 1000),
        ]:
            # Pick the closest representative value in this bucket so the
            # filter actually matches one of Mubert's discrete BPMs.
            in_bucket = [b for b in bpm_values if lo < b <= hi]
            if in_bucket:
                bpms.append({"label": label, "value": str(in_bucket[len(in_bucket) // 2])})
    else:
        bpms = fallback["bpms"]

    # Durations: upstream sends ~120 individual values. Surface a small
    # curated set the user actually cares about, restricted to values
    # the API knows about.
    dur_values = {
        int(v.get("value")) for v in (data.get("duration") or []) if str(v.get("value", "")).isdigit()
    }
    chosen = []
    for d in (15, 30, 60, 90, 120, 180, 240, 300):
        if not dur_values or d in dur_values:
            chosen.append({"label": (f"{d}s" if d < 60 else f"{d // 60} min" if d % 60 == 0 else f"{d}s"), "value": str(d)})
    durations = chosen or fallback["durations"]

    return {
        "genres": genres,
        "moods": moods,
        "activities": activities,
        "bpms": bpms,
        "durations": durations,
    }


@router.get("/library/tracks")
async def list_library_tracks(
    genre: Optional[str] = None,
    mood: Optional[str] = None,
    activity: Optional[str] = None,
    bpm: Optional[str] = None,
    duration: Optional[int] = None,
    offset: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search the curated 12K-track library.

    Returns the same shape as /api/music/catalog (tracks + total) so the
    frontend renders both via the same path. Falls back to the local
    `music_catalog` rows on upstream failure.

    The `mood` arg may be either a Mubert "mood" (Epic, Romantic…) or a
    "theme" (Cinematic, Corporate…). Themes are tried first since that's
    where the high-volume curated content lives. We pass *one* of these
    through as the upstream filter — whichever the value matches.
    """
    mubert = get_mubert_service_optional()
    if mubert is None:
        return await _fallback_catalog(db, mood=mood, duration=duration, limit=limit)

    try:
        customer_id, access_token = await mubert.ensure_user_customer(db, user)
        # Decide whether `mood` is actually a theme (more common case) by
        # consulting the cached params. Falls through if either lookup
        # fails — upstream will just return 0 results we can fall back from.
        themes_filter: Optional[list[str]] = None
        moods_filter: Optional[list[str]] = None
        if mood:
            try:
                params_cache = await get_cached_library_params(mubert, customer_id, access_token)
                theme_values = {
                    str(v.get("value")) for v in (params_cache.get("themes") or [])
                }
                mood_values = {
                    str(v.get("value")) for v in (params_cache.get("moods") or [])
                }
                if mood in theme_values:
                    themes_filter = [mood]
                elif mood in mood_values:
                    moods_filter = [mood]
                else:
                    themes_filter = [mood]  # best-effort
            except Exception as e:
                sentry_sdk.capture_exception(e)
                themes_filter = [mood]

        # services.mubert.search_library doesn't take "themes" as a kwarg
        # so pass it via the underlying call. Easiest: extend the kwargs.
        data = await mubert.search_library(
            customer_id,
            access_token,
            genres=[genre] if genre else None,
            moods=moods_filter,
            activities=[activity] if activity else None,
            bpm=bpm,
            duration=duration,
            offset=offset,
            limit=limit,
        )
        # If we resolved a theme rather than a mood, redo the call adding
        # the themes param. (Avoiding service-level signature churn.)
        if themes_filter:
            import httpx as _httpx
            url = "https://music-api.mubert.com/api/v3/public/music-library/tracks"
            qparams: list[tuple[str, object]] = [("themes", themes_filter[0])]
            if genre:
                qparams.append(("genres", genre))
            if activity:
                qparams.append(("activities", activity))
            if bpm:
                qparams.append(("bpm", bpm))
            if duration:
                qparams.append(("duration", int(duration)))
            qparams.append(("offset", offset))
            qparams.append(("limit", limit))
            async with _httpx.AsyncClient(timeout=30) as c:
                resp = await c.get(
                    url,
                    headers={
                        "customer-id": customer_id,
                        "access-token": access_token,
                    },
                    params=qparams,
                )
                resp.raise_for_status()
                payload = resp.json()
            tracks_raw = payload.get("data") or []
            meta = payload.get("meta") or {}
            data = {
                "tracks": tracks_raw,
                "total": int(meta.get("total", len(tracks_raw))),
                "offset": int(meta.get("offset", offset)),
                "limit": int(meta.get("limit", limit)),
            }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return await _fallback_catalog(db, mood=mood, duration=duration, limit=limit)

    raw_tracks = data.get("tracks") or []
    theme_hint = mood or genre or activity or None
    tracks = [_normalise_library_track(t, theme_hint=theme_hint) for t in raw_tracks if t]
    tracks = [t for t in tracks if t.get("url")]
    total = int(data.get("total", len(tracks)))
    has_more = (offset + len(tracks)) < total and len(raw_tracks) > 0
    return {
        "tracks": tracks,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


async def _fallback_catalog(
    db: AsyncSession,
    *,
    mood: Optional[str],
    duration: Optional[int],
    limit: int,
) -> dict:
    """Read from the local `music_catalog` table as a last-resort fallback."""
    where = []
    params: dict = {"limit": max(1, min(int(limit), 200))}
    if mood and mood != "all":
        where.append("mood = :mood")
        params["mood"] = mood
    if duration:
        where.append("duration_seconds = :duration")
        params["duration"] = int(duration)
    sql = (
        "SELECT id, name, mood, intensity, tempo, duration_seconds, prompt, public_url, created_at "
        "FROM music_catalog "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY created_at DESC LIMIT :limit"
    )
    try:
        result = await db.execute(_sa_text(sql), params)
        rows = result.fetchall()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        rows = []
    tracks = [
        {
            "id": r.id,
            "name": r.name,
            "mood": r.mood,
            "genre": "",
            "moods": [r.mood] if r.mood else [],
            "genres": [],
            "intensity": r.intensity,
            "tempo": r.tempo,
            "bpm": None,
            "duration": r.duration_seconds,
            "prompt": r.prompt,
            "url": r.public_url,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {
        "tracks": tracks,
        "total": len(tracks),
        "offset": 0,
        "limit": limit,
        "has_more": False,
    }


class GenerateAIMusicRequest(BaseModel):
    """Request body for /api/music/ai/generate.

    Either pass a free-text `prompt` directly, or a `mood` we'll resolve
    to tags via the knowledge base. Both are fine; prompt wins if both
    are set.
    """
    prompt: Optional[str] = None
    mood: Optional[str] = None
    duration_seconds: int = 60
    intensity: str = "medium"  # low | medium | high
    cast_id: Optional[str] = None  # if set, attach result to this cast


@router.post("/ai/generate")
async def generate_ai_music(
    req: GenerateAIMusicRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a single royalty-free AI music track via Mubert v3.

    Flow: ensure customer → POST /tracks → poll until done → download →
    upload to R2 → attach to cast (if requested) → return public URL.
    """
    mubert = get_mubert_service_optional()
    if mubert is None:
        raise HTTPException(503, "AI music is not configured")

    prompt = (req.prompt or "").strip() or mood_to_prompt(req.mood or "enthusiastic")
    duration = max(15, min(int(req.duration_seconds or 60), 600))

    try:
        result = await mubert.generate_for_user(
            db=db,
            user=user,
            prompt=prompt,
            duration_seconds=duration,
            intensity=req.intensity,
        )
    except MubertConfigurationError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(503, "AI music is not configured")
    except MubertGenerationError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(502, f"AI music generation failed: {e}")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(502, "AI music generation failed")

    # Download the track from Mubert and re-host on our own CDN. Mubert
    # URLs expire (usually within 24h) and are slower than R2 in our edge.
    track_url = result["url"]
    music_key = f"music/users/{user.id}/{result['track_id']}.mp3"
    public_url = f"{CDN_BASE}/{music_key}"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(track_url)
            resp.raise_for_status()
            mp3_bytes = resp.content
        r2 = get_r2_storage_service()
        await r2.upload_bytes(mp3_bytes, music_key, content_type="audio/mpeg")
    except Exception as e:
        # Re-hosting failed but the Mubert URL is still valid for hours —
        # return that so the user gets immediate audio. Best-effort R2
        # upload happens in the background separately if we want it.
        sentry_sdk.capture_exception(e)
        public_url = track_url

    # Optional: attach to a specific cast.
    if req.cast_id:
        try:
            from models.cast import Cast
            cast = await db.get(Cast, req.cast_id)
            if cast and cast.user_id == user.id:
                cast.background_music_url = public_url
                cast.background_music_mood = req.mood
                cast.background_music_tags = (prompt or "").split() if prompt else None
                await db.commit()
        except Exception as e:
            sentry_sdk.capture_exception(e)

    return {
        "id": result["track_id"],
        "url": public_url,
        "duration": result.get("duration", duration),
        "prompt": prompt,
        "mood": req.mood,
        "intensity": req.intensity,
        "bpm": result.get("bpm"),
        "key": result.get("key"),
    }


@router.get("/catalog")
async def list_music_catalog(
    mood: Optional[str] = None,
    duration: Optional[int] = None,
    limit: int = 60,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List pre-generated catalog tracks browsable from the Browse tab.

    Tracks are produced by a weekly Celery beat job; until that's wired
    up the catalog can be empty (Browse falls back to a 'no presets yet'
    state and the user can generate on-demand via the AI Generate tab).
    """
    where = []
    params: dict = {"limit": max(1, min(int(limit), 200))}
    if mood and mood != "all":
        where.append("mood = :mood")
        params["mood"] = mood
    if duration:
        where.append("duration_seconds = :duration")
        params["duration"] = int(duration)
    sql = (
        "SELECT id, name, mood, intensity, tempo, duration_seconds, prompt, public_url, created_at "
        "FROM music_catalog "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY created_at DESC LIMIT :limit"
    )
    try:
        result = await db.execute(_sa_text(sql), params)
        rows = result.fetchall()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        rows = []
    return {
        "tracks": [
            {
                "id": r.id,
                "name": r.name,
                "mood": r.mood,
                "intensity": r.intensity,
                "tempo": r.tempo,
                "duration": r.duration_seconds,
                "prompt": r.prompt,
                "url": r.public_url,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/uploaded/upload")
async def upload_user_music(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a custom music file (mp3/wav). Limited to 25 MB."""
    if file.content_type not in ("audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav"):
        raise HTTPException(400, "Only MP3 or WAV files are accepted")
    contents = await file.read()
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 25 MB)")
    music_id = f"mus_{uuid.uuid4().hex[:12]}"
    safe_name = (file.filename or "upload").replace("/", "_").replace("\\", "_")[:200]
    ext = "mp3" if file.content_type in ("audio/mpeg", "audio/mp3") else "wav"
    r2_key = f"music/uploaded/{user.id}/{music_id}.{ext}"
    try:
        r2 = get_r2_storage_service()
        await r2.upload_bytes(contents, r2_key, content_type=file.content_type)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(502, "Storage upload failed")
    public_url = f"{CDN_BASE}/{r2_key}"
    await db.execute(
        _sa_text(
            "INSERT INTO uploaded_music (id, user_id, name, r2_key, public_url, file_size_bytes, mime_type) "
            "VALUES (:id, :user_id, :name, :r2_key, :public_url, :size, :mime)"
        ),
        {
            "id": music_id,
            "user_id": user.id,
            "name": safe_name,
            "r2_key": r2_key,
            "public_url": public_url,
            "size": len(contents),
            "mime": file.content_type,
        },
    )
    await db.commit()
    return {
        "id": music_id,
        "name": safe_name,
        "url": public_url,
        "file_size_bytes": len(contents),
    }


@router.get("/uploaded")
async def list_uploaded_music(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        _sa_text(
            "SELECT id, name, public_url, duration_seconds, file_size_bytes, created_at "
            "FROM uploaded_music WHERE user_id = :uid ORDER BY created_at DESC"
        ),
        {"uid": user.id},
    )
    rows = res.fetchall()
    return {
        "tracks": [
            {
                "id": r.id,
                "name": r.name,
                "url": r.public_url,
                "duration": r.duration_seconds,
                "file_size_bytes": r.file_size_bytes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.delete("/uploaded/{music_id}")
async def delete_uploaded_music(
    music_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        _sa_text(
            "DELETE FROM uploaded_music WHERE id = :id AND user_id = :uid RETURNING id"
        ),
        {"id": music_id, "uid": user.id},
    )
    deleted = res.fetchone()
    await db.commit()
    if not deleted:
        raise HTTPException(404, "Track not found")
    return {"deleted": True}


# =========================================================================
# SFX library
# =========================================================================

# Canonical SFX list — mirrors Music_SFX_Knowledge_Base_v1.md Section 2.
# Each entry has a key, label, icon, and source URL on our CDN. The
# physical files are seeded by scripts/seed_sfx_library.py (synthetic
# tones for now; replace with CC0 clips before user-facing launch).
SFX_LIBRARY: list[dict] = [
    {"key": "whoosh",          "icon": "\ud83d\udca8", "label": "Whoosh",          "duration": 0.5, "description": "Swipe / transition"},
    {"key": "pop",             "icon": "\ud83e\udee7", "label": "Pop",             "duration": 0.3, "description": "Item appearing / pop-up"},
    {"key": "ding",            "icon": "\ud83d\udd14", "label": "Ding",            "duration": 0.5, "description": "Notification / achievement"},
    {"key": "cash_register",   "icon": "\ud83d\udcb0", "label": "Cash Register",   "duration": 0.8, "description": "Price reveal / purchase"},
    {"key": "sparkle",         "icon": "\u2728",       "label": "Sparkle",         "duration": 0.7, "description": "Premium reveal"},
    {"key": "record_scratch",  "icon": "\ud83d\udd34", "label": "Record Scratch",  "duration": 0.6, "description": "Pattern interrupt"},
    {"key": "swoosh_up",       "icon": "\ud83d\udcc8", "label": "Swoosh Up",       "duration": 0.5, "description": "Energy rising"},
    {"key": "swoosh_down",     "icon": "\ud83d\udcc9", "label": "Swoosh Down",     "duration": 0.5, "description": "Energy falling"},
    {"key": "notification",    "icon": "\ud83d\udcf3", "label": "Notification",    "duration": 0.4, "description": "Phone notification"},
    {"key": "timer_tick",      "icon": "\u23f1",       "label": "Timer Tick",      "duration": 0.3, "description": "Countdown / urgency"},
    {"key": "click",           "icon": "\ud83d\uddb1", "label": "Click",           "duration": 0.2, "description": "CTA / tap"},
    {"key": "drumroll",        "icon": "\ud83e\udd41", "label": "Drumroll",        "duration": 1.2, "description": "Reveal anticipation"},
    {"key": "applause",        "icon": "\ud83d\udc4f", "label": "Applause",        "duration": 1.5, "description": "Celebration"},
    {"key": "camera_shutter",  "icon": "\ud83d\udcf8", "label": "Shutter",         "duration": 0.3, "description": "Photo moment"},
    {"key": "bass_drop",       "icon": "\ud83d\udd0a", "label": "Bass Drop",       "duration": 0.5, "description": "Major reveal"},
    {"key": "typing",          "icon": "\u2328",       "label": "Typing",          "duration": 0.8, "description": "Comment reading"},
    {"key": "coin",            "icon": "\ud83e\ude99", "label": "Coin",            "duration": 0.4, "description": "Savings / discount"},
    {"key": "success",         "icon": "\u2705",       "label": "Success",         "duration": 0.5, "description": "Task complete"},
]


@router.get("/sfx/library")
async def list_sfx_library(user: User = Depends(get_current_user)):
    """Browseable SFX library for the SFX tab + drag-to-timeline.

    Each entry includes a public preview URL on our CDN. Files are
    seeded synthetically by scripts/seed_sfx_library.py until the CC0
    clips arrive.
    """
    return {
        "items": [
            {
                **sfx,
                "url": f"{CDN_BASE}/sfx/{sfx['key']}.mp3",
            }
            for sfx in SFX_LIBRARY
        ]
    }

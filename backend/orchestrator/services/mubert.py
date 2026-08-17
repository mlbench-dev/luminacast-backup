"""
Mubert API v3 wrapper.

Generates royalty-free AI background music tailored to cast mood and duration.
This service is the source of legally-safe music for both recorded videos
(no Content ID strikes) and live streams (TikTok banned copyrighted music
on LIVE in July 2025 — Mubert is one of the only safe options).

Auth model (v3):
  - Service-level credentials: company-id + license-token (in env)
  - Per-user credentials: customer-id + access-token (returned when we
    create a customer the first time, cached on User.mubert_*).
  - Track generation uses customer-level credentials, NOT service-level.

Generation flow:
  1. ensure_customer(user) -> creates a Mubert customer if user has none yet
  2. generate_track(prompt, duration, ...) -> POST /api/v3/public/tracks
     returns track_id, status=processing
  3. wait_for_track(track_id) -> poll GET /api/v3/public/tracks/{id}
     until generations[0].status == "done", returns the MP3 URL

Mood-to-prompt mapping is NOT hardcoded here \u2014 it lives in the
ai_prompts knowledge base entry so admins can edit via /preadmin.
We fall back to a small default dict if the registry is empty.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

logger = logging.getLogger(__name__)

MUBERT_API_BASE = "https://music-api.mubert.com/api/v3"


def _log(level: str, message: str, **kwargs):
    logger.log(
        getattr(logging, level.upper()),
        json.dumps(
            {
                "service": "mubert",
                "level": level,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }
        ),
    )


# Fallback mood-to-prompt mapping. The authoritative source is the
# `music_mood_tags` entry in services/ai_prompts.py registry, edited
# via /preadmin. We mirror it here so a missing/corrupt registry never
# breaks track generation.
# Natural-language sentences, not bare space-joined keyword lists — verified
# live against Mubert's generation endpoint that the keyword-list style
# (e.g. "pop upbeat cheerful positive") has a real, reproducible chance of
# coming back as a structurally valid but completely silent mp3 (confirmed
# 3/3 on that exact string), while natural sentences generated reliably in
# every test run. Mubert's own validation requires *some* non-empty prompt
# (a literal empty string is hard-rejected with a 422), but a merely
# present-but-terse prompt can still silently fail content-wise — this is
# about prompt quality, not presence.
FALLBACK_MOOD_PROMPTS: dict[str, str] = {
    # high energy
    "excited": "An energetic, upbeat, and bright pop track",
    "urgent": "An intense, driving, fast-paced electronic track",
    "hype": "A powerful, energetic trap track with heavy bass",
    "triumphant": "An epic, cinematic, and uplifting orchestral track",
    # medium
    "enthusiastic": "An upbeat, cheerful pop track with a positive feel",
    "confident": "A modern, motivational corporate track",
    "informative": "A calm, light ambient electronic track",
    "trustworthy": "A warm, gentle corporate acoustic track",
    "playful": "A fun, quirky, and bouncy light track",
    # low
    "calm": "A relaxing, soft ambient chill track",
    "intimate": "A warm, intimate lofi acoustic track",
    "mysterious": "A dark, atmospheric, and mysterious cinematic track",
    "emotional": "A slow, emotional piano-led cinematic track",
    "dreamy": "An airy, gentle, ethereal ambient track",
}


def mood_to_prompt(mood: str) -> str:
    """Resolve a mood string to a Mubert prompt.

    Reads the admin-editable knowledge base first, falls back to the dict above.
    """
    try:
        from services.ai_prompts import AI_PROMPTS
        kb = (AI_PROMPTS or {}).get("music_mood_tags") or {}
        mapping = kb.get("mapping") or {}
        if mood in mapping and mapping[mood]:
            tags = mapping[mood]
            if isinstance(tags, list):
                return " ".join(str(t) for t in tags if t)
            if isinstance(tags, str):
                return tags
    except Exception as e:  # pragma: no cover - defensive
        sentry_sdk.capture_exception(e)
    return FALLBACK_MOOD_PROMPTS.get(mood, "pop upbeat modern")


class MubertConfigurationError(RuntimeError):
    """Raised when Mubert is not configured (env keys missing)."""


class MubertGenerationError(RuntimeError):
    """Raised when track generation fails or times out."""


class MubertService:
    """Thin wrapper over the Mubert v3 REST API."""

    def __init__(self) -> None:
        self.company_id = settings.MUBERT_COMPANY_ID
        self.license_token = settings.MUBERT_LICENSE_TOKEN
        if not (self.company_id and self.license_token):
            raise MubertConfigurationError(
                "MUBERT_COMPANY_ID and MUBERT_LICENSE_TOKEN must be set"
            )

    # ── Service-level (company) calls ─────────────────────────────────────

    @property
    def _service_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "company-id": self.company_id,
            "license-token": self.license_token,
        }

    async def create_customer(self, custom_id: str) -> dict:
        """Create a Mubert customer for the given internal user id.

        Returns the `data.access` block, which contains both `customer_id`
        and `token` (= access-token). The caller should persist these on
        the User row so subsequent calls reuse them.
        """
        url = f"{MUBERT_API_BASE}/service/customers"
        payload = {"custom_id": custom_id}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=self._service_headers, json=payload)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                _log("error", "Mubert create_customer failed", status=resp.status_code, body=resp.text[:400])
                sentry_sdk.capture_exception(e)
                raise
            data = resp.json().get("data") or {}
        access = (data or {}).get("access") or {}
        if not access.get("customer_id") or not access.get("token"):
            raise MubertGenerationError(f"Mubert returned no customer access: {data}")
        _log("info", "Mubert customer created", customer_id=access["customer_id"])
        return access

    # ── Per-customer (public) calls ───────────────────────────────────────

    @staticmethod
    def _customer_headers(customer_id: str, access_token: str) -> dict:
        return {
            "Content-Type": "application/json",
            "customer-id": customer_id,
            "access-token": access_token,
        }

    async def generate_track(
        self,
        customer_id: str,
        access_token: str,
        prompt: str,
        duration_seconds: int,
        intensity: str = "medium",
        bitrate: int = 128,
        fmt: str = "mp3",
    ) -> dict:
        """Kick off track generation. Returns the raw `data` block.

        Mubert v3 is async \u2014 the response is { id, generations: [{status: "processing", url: null}] }.
        Caller should follow up with `wait_for_track(id, ...)`.
        """
        url = f"{MUBERT_API_BASE}/public/tracks"
        # Clamp duration to Mubert's supported range. v3 supports 15s\u201325min.
        duration_seconds = max(15, min(int(duration_seconds), 1500))
        payload = {
            "prompt": prompt or "pop upbeat modern",
            "duration": duration_seconds,
            "bitrate": bitrate,
            "format": fmt,
            "intensity": intensity if intensity in ("low", "medium", "high") else "medium",
            "mode": "track",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url, headers=self._customer_headers(customer_id, access_token), json=payload
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                _log("error", "Mubert generate_track failed", status=resp.status_code, body=resp.text[:400])
                sentry_sdk.capture_exception(e)
                raise
            data = resp.json().get("data") or {}
        if not data.get("id"):
            raise MubertGenerationError(f"Mubert returned no track id: {data}")
        _log("info", "Mubert track requested", track_id=data["id"], duration=duration_seconds, intensity=intensity)
        return data

    async def get_track(
        self, customer_id: str, access_token: str, track_id: str
    ) -> dict:
        url = f"{MUBERT_API_BASE}/public/tracks/{track_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url, headers=self._customer_headers(customer_id, access_token)
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                sentry_sdk.capture_exception(e)
                _log("error", "Mubert get_track failed", track_id=track_id, status=resp.status_code, body=resp.text[:400])
                raise
            return resp.json().get("data") or {}

    async def wait_for_track(
        self,
        customer_id: str,
        access_token: str,
        track_id: str,
        max_attempts: int = 90,
        poll_interval_seconds: float = 2.0,
    ) -> dict:
        """Poll until the first generation is done. Returns the generation dict.

        max_attempts \u00d7 poll_interval = total wait. Defaults give 3 minutes
        which is plenty for typical 15\u2013120s tracks (Mubert claims ~10s for
        a 60s clip).
        """
        for attempt in range(max_attempts):
            await asyncio.sleep(poll_interval_seconds)
            data = await self.get_track(customer_id, access_token, track_id)
            gens = data.get("generations") or []
            if not gens:
                continue
            gen = gens[0]
            status = gen.get("status")
            if status == "done" and gen.get("url"):
                _log("info", "Mubert track ready", track_id=track_id, attempt=attempt)
                return gen
            if status == "failed" or gen.get("error"):
                err = gen.get("error") or "unknown"
                raise MubertGenerationError(f"Mubert track {track_id} failed: {err}")
        raise MubertGenerationError(
            f"Mubert track {track_id} did not complete in {max_attempts * poll_interval_seconds:.0f}s"
        )

    # ── High-level helpers ────────────────────────────────────────────────

    async def ensure_user_customer(
        self,
        db: AsyncSession,
        user,
    ) -> tuple[str, str]:
        """Get-or-create the Mubert customer for this user.

        Caches `mubert_customer_id` and `mubert_access_token` on the User
        row so subsequent track generations don't pay the customer-create
        round-trip. Returns (customer_id, access_token).
        """
        existing_id = getattr(user, "mubert_customer_id", None)
        existing_tok = getattr(user, "mubert_access_token", None)
        if existing_id and existing_tok:
            return existing_id, existing_tok

        access = await self.create_customer(custom_id=user.id)
        user.mubert_customer_id = access["customer_id"]
        user.mubert_access_token = access["token"]
        await db.commit()
        return access["customer_id"], access["token"]

    # ── Curated music library (12K tracks, searchable) ───────────────────
    #
    # Mubert v3 ships a curated catalogue in addition to on-demand generation.
    # Endpoints:
    #   GET /api/v3/public/music-library/params  -> filter dimensions
    #   GET /api/v3/public/music-library/tracks  -> filtered track list
    # Both use customer-id + access-token auth.

    async def list_library_params(
        self,
        customer_id: str,
        access_token: str,
    ) -> dict:
        """Fetch the available filter dimensions (genres, moods, BPMs, etc.).

        Upstream returns a list of `{param, values: [{value, tracks_count}]}`
        records. We normalise to a dict keyed by param name so callers can
        do `params["genres"]` directly.
        """
        url = f"{MUBERT_API_BASE}/public/music-library/params"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url, headers=self._customer_headers(customer_id, access_token)
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                _log("error", "Mubert library params failed", status=resp.status_code, body=resp.text[:400])
                sentry_sdk.capture_exception(e)
                raise
            payload = resp.json()
        # Upstream may wrap in {"data": [...]} or return the list directly.
        if isinstance(payload, dict):
            payload = payload.get("data") or []
        out: dict[str, list[dict]] = {}
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                key = entry.get("param")
                values = entry.get("values") or []
                if isinstance(key, str):
                    out[key] = [v for v in values if isinstance(v, dict)]
        return out

    async def search_library(
        self,
        customer_id: str,
        access_token: str,
        *,
        genres: Optional[list[str]] = None,
        moods: Optional[list[str]] = None,
        activities: Optional[list[str]] = None,
        bpm: Optional[str] = None,
        duration: Optional[int] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """Query the curated library. Returns the raw `data` block.

        Filters are passed as repeated query params where lists are supported.
        Pagination is offset/limit; Mubert returns a `total` count we surface.
        """
        url = f"{MUBERT_API_BASE}/public/music-library/tracks"
        params: list[tuple[str, Any]] = []
        for g in genres or []:
            if g:
                params.append(("genres", g))
        for m in moods or []:
            if m:
                params.append(("moods", m))
        for a in activities or []:
            if a:
                params.append(("activities", a))
        if bpm:
            params.append(("bpm", bpm))
        if duration:
            params.append(("duration", int(duration)))
        params.append(("offset", max(0, int(offset))))
        params.append(("limit", max(1, min(int(limit), 60))))

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url,
                headers=self._customer_headers(customer_id, access_token),
                params=params,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                _log(
                    "error",
                    "Mubert library search failed",
                    status=resp.status_code,
                    body=resp.text[:400],
                    query=params,
                )
                sentry_sdk.capture_exception(e)
                raise
            payload = resp.json()
        if not isinstance(payload, dict):
            return {"tracks": payload or [], "total": 0, "offset": offset, "limit": limit}
        tracks = payload.get("data") or payload.get("tracks") or []
        meta = payload.get("meta") or {}
        total = meta.get("total")
        if total is None:
            total = len(tracks)
        return {
            "tracks": tracks,
            "total": int(total),
            "offset": int(meta.get("offset", offset)),
            "limit": int(meta.get("limit", limit)),
        }

    async def generate_for_user(
        self,
        db: AsyncSession,
        user,
        prompt: str,
        duration_seconds: int,
        intensity: str = "medium",
    ) -> dict:
        """End-to-end: ensure customer, kick generation, wait, return generation dict."""
        customer_id, access_token = await self.ensure_user_customer(db, user)
        track = await self.generate_track(
            customer_id=customer_id,
            access_token=access_token,
            prompt=prompt,
            duration_seconds=duration_seconds,
            intensity=intensity,
        )
        gen = await self.wait_for_track(customer_id, access_token, track["id"])

        try:
            import sentry_sdk as _sentry
            from services.cost_rates import COST_RATES
            from services.usage_tracker import log_usage
            await log_usage(
                db,
                user_id=getattr(user, "id", None),
                event_type="music_generation",
                provider="mubert",
                provider_cost_usd=float(COST_RATES.get("mubert/per_track", 0.04)),
                quantity=1,
                quantity_unit="tracks",
                resource_type="track",
                resource_id=str(track.get("id") or ""),
                duration_seconds=float(track.get("duration") or duration_seconds or 0),
                provider_job_id=str(track.get("id") or ""),
            )
            await db.commit()
        except Exception as _exc:
            try:
                _sentry.capture_exception(_exc)
            except Exception:
                pass

        return {
            "track_id": track["id"],
            "url": gen["url"],
            "duration": track.get("duration", duration_seconds),
            "bpm": track.get("bpm"),
            "key": track.get("key"),
            "prompt": prompt,
            "intensity": intensity,
        }


# Process-wide cache for the library params endpoint. The enums change
# rarely and the response is ~tens of KB, so a 12h TTL is plenty.
_LIBRARY_PARAMS_TTL_SECONDS = 12 * 60 * 60
_library_params_cache: dict = {"value": None, "fetched_at": 0.0}


async def get_cached_library_params(service: "MubertService", customer_id: str, access_token: str) -> dict:
    """Return cached library params, refreshing on TTL expiry."""
    now = time.time()
    cached = _library_params_cache
    if cached["value"] is not None and (now - cached["fetched_at"]) < _LIBRARY_PARAMS_TTL_SECONDS:
        return cached["value"]
    fresh = await service.list_library_params(customer_id, access_token)
    _library_params_cache["value"] = fresh
    _library_params_cache["fetched_at"] = now
    return fresh


# ── Evocative track naming ───────────────────────────────────────────────
#
# Raw library track objects carry no name/genre/mood of their own — only
# bpm, musical key, intensity, and mode (see _normalise_library_track in
# routers/music.py). We bucket tracks into 6 "vibes" from those signals
# and assign each bucket a fixed pool of evocative names (e.g. "Crystal
# Lagoon"), so the same track always gets the same readable title instead
# of a raw "130 BPM · C#m".
#
# This is a static, hand-written pool rather than an LLM call made at
# request time: the backend runs multiple worker processes, and an
# in-memory per-process cache meant each worker generated its own
# independently-random pool, so the same track could show a different
# name depending which worker served the request. A fixed list sidesteps
# that entirely — same name everywhere, no runtime API calls, no
# OpenRouter usage/rate-limit exposure for what is purely display flavor.

_VIBE_LABELS: dict[str, str] = {
    "low_major": "Peaceful",
    "low_minor": "Dreamy",
    "medium_major": "Warm",
    "medium_minor": "Atmospheric",
    "high_major": "Uplifting",
    "high_minor": "Intense",
}

_TRACK_NAME_POOL: dict[str, list[str]] = {
    "low_major": [
        "Morning Glow", "Soft Horizon", "Gentle Bloom", "Quiet Meadow", "Pale Sunrise",
        "Calm Waters", "Light Breeze", "Open Sky", "Still Harbor", "Warm Daylight",
        "Faded Postcard", "Slow Bloom", "Windowlight", "Paper Boats", "Easy Morning",
    ],
    "low_minor": [
        "Crystal Lagoon", "Velvet Haze", "Moonlit Drift", "Silver Mist", "Hollow Echo",
        "Fading Light", "Quiet Ruins", "Blue Hour", "Empty Platform", "Grey Feather",
        "Distant Shore", "Winter Glass", "Pale Moonrise", "Soft Static", "Fogbound",
    ],
    "medium_major": [
        "Golden Hour", "Amber Fields", "Warm Current", "Honey Glow", "Sunlit Path",
        "Open Road", "Bright Meadow", "Copper Sky", "Sundial", "Wildflower Drive",
        "Harvest Light", "Sunday Drive", "Coastal Run", "Afternoon Gold", "Backroad",
    ],
    "medium_minor": [
        "Slate Horizon", "Distant Signal", "Grey Tide", "Low Static", "Shadow Walk",
        "Ember Trail", "Concrete Bloom", "Night Freight", "Iron Sky", "Half Light",
        "Rust Belt", "Broken Compass", "Undertow", "Cinder Path", "Stormfront",
    ],
    "high_major": [
        "Neon Pulse", "Bright Surge", "Electric Bloom", "Solar Flare", "Wild Current",
        "Sky Ignition", "Skyline Rush", "Gold Rush", "Sunburst", "Turbo Glow",
        "Livewire", "High Voltage", "Daybreak Sprint", "Overdrive", "Firelight",
    ],
    "high_minor": [
        "Midnight Surge", "Iron Storm", "Dark Pulse", "Black Ice", "Static Storm",
        "Red Alert", "Blackout", "Riot Signal", "Steel Vortex", "Ashfall",
        "Night Sirens", "Fault Line", "Warzone", "Nightcrawler", "Voltage Drop",
    ],
}


def vibe_bucket(key: Optional[str], intensity: Optional[str]) -> str:
    """Map Mubert's raw key + intensity to one of 6 vibe buckets."""
    key = (key or "").strip()
    key_mode = "minor" if key.endswith("m") and key.upper() != "ALL" else "major"
    intensity_bucket = intensity if intensity in ("low", "medium", "high") else "medium"
    return f"{intensity_bucket}_{key_mode}"


def pick_track_name(track_id: str, key: Optional[str], intensity: Optional[str]) -> tuple[str, str]:
    """Deterministically pick a name for a track from its vibe bucket.

    The same track_id always yields the same name (stable across
    reloads/users/workers) since the index comes from a hash of the id,
    not randomness. Returns (name, vibe_label) — vibe_label is the
    human-readable bucket name (e.g. "Dreamy") for display alongside
    bpm/key/intensity.
    """
    bucket = vibe_bucket(key, intensity)
    names = _TRACK_NAME_POOL[bucket]
    idx = int(hashlib.sha256((track_id or "").encode()).hexdigest(), 16) % len(names)
    return names[idx], _VIBE_LABELS[bucket]


_singleton: Optional[MubertService] = None


def get_mubert_service() -> MubertService:
    """Return a process-wide MubertService. Raises MubertConfigurationError if not configured."""
    global _singleton
    if _singleton is None:
        _singleton = MubertService()
    return _singleton


def get_mubert_service_optional() -> Optional[MubertService]:
    """Same as get_mubert_service() but returns None when unconfigured."""
    try:
        return get_mubert_service()
    except MubertConfigurationError:
        return None

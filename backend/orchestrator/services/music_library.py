"""Fixed background-music library.

A small curated set of pre-generated tracks the user can pin to a cast
instead of the default mood-driven auto-generation. Each entry maps a stable
track id to a public URL the renderer can stream and a little metadata for
the picker UI.

The cast stores the choice as ``music_track_choice = "track_id:<id>"``; the
auto-music dispatch (tasks/auto_music) resolves the id to a URL here and writes
it onto ``cast.background_music_url`` so the existing timeline/mixer path picks
it up unchanged.

URLs point at the public media CDN (R2-backed). Replace the placeholder keys
with real uploaded objects as the library grows; the ids themselves are the
stable contract the frontend persists, so keep them fixed.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import sentry_sdk

logger = logging.getLogger(__name__)

# Public media CDN base — same host tasks/auto_music re-hosts Mubert tracks on.
_MEDIA_BASE = "https://media.luminacast.com"

# Hosts the render server can always fetch. A background_music_url on one of
# these is used as-is; anything else (a Mubert generation URL, an arbitrary
# external mp3) is downloaded and re-hosted on R2 — those URLs expire (Mubert's
# within ~24h) and the render's audio remux then silently drops the track,
# which is why "auto" music renders fine but a manually-picked library track
# never makes it into the final video.
_STABLE_MUSIC_HOSTS = ("media.luminacast.com",)

# music_track_choice values. "track_id:<id>" is a prefix, the rest are exact.
CHOICE_OFF = "off"
CHOICE_AUTO = "auto"
CHOICE_TRACK_PREFIX = "track_id:"


@dataclass(frozen=True)
class MusicTrack:
    id: str
    name: str
    mood: str
    url: str


def _key(filename: str) -> str:
    return f"{_MEDIA_BASE}/music/library/{filename}"


# Ordered so the picker can render them as-is. Keep ids stable.
MUSIC_LIBRARY: dict[str, MusicTrack] = {
    t.id: t
    for t in (
        MusicTrack("upbeat_pop", "Upbeat Pop", "energetic", _key("upbeat_pop.mp3")),
        MusicTrack("chill_lofi", "Chill Lo-Fi", "calm", _key("chill_lofi.mp3")),
        MusicTrack("cinematic_swell", "Cinematic Swell", "dramatic", _key("cinematic_swell.mp3")),
        MusicTrack("corporate_clean", "Corporate Clean", "neutral", _key("corporate_clean.mp3")),
        MusicTrack("hype_trap", "Hype Trap", "high-energy", _key("hype_trap.mp3")),
    )
}


class UnknownMusicTrackError(KeyError):
    """Raised when a track id is not present in the library."""


def list_tracks() -> list[MusicTrack]:
    """Return the library tracks in display order."""
    return list(MUSIC_LIBRARY.values())


def get_track(track_id: str) -> MusicTrack:
    """Resolve a track id to its entry, raising on unknown ids."""
    track = MUSIC_LIBRARY.get(track_id)
    if track is None:
        raise UnknownMusicTrackError(track_id)
    return track


def get_track_url(track_id: str) -> str:
    """Resolve a track id to its public URL, raising on unknown ids."""
    return get_track(track_id).url


def parse_choice(choice: str | None) -> str | None:
    """Extract the track id from a ``music_track_choice`` value.

    Returns the bare track id for ``"track_id:<id>"`` choices, else ``None``
    (for ``"off"``, ``"auto"``, empty, or malformed values).
    """
    if not choice:
        return None
    if choice.startswith(CHOICE_TRACK_PREFIX):
        track_id = choice[len(CHOICE_TRACK_PREFIX):].strip()
        return track_id or None
    return None


def resolve_choice_url(choice: str | None) -> str | None:
    """Resolve a ``music_track_choice`` to a background-music URL, or None.

    "off"/"auto" return None (the caller decides: skip, or generate). A
    "track_id:<id>" choice returns the library URL, raising
    ``UnknownMusicTrackError`` on an unknown id.
    """
    track_id = parse_choice(choice)
    if track_id is None:
        return None
    return get_track_url(track_id)


def _is_stable_music_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _STABLE_MUSIC_HOSTS)


async def rehost_external_music_url(url: str | None, *, owner_id: str) -> str | None:
    """Return a stable, render-fetchable URL for a picked background track.

    A ``background_music_url`` already on our media CDN (or empty) is returned
    unchanged. Anything else is downloaded and uploaded to R2 under a
    content-addressed key, and that R2 URL is returned — external picker URLs
    (Mubert generations especially) expire, and the render's audio remux then
    silently skips the unreachable track, leaving the video with no music.

    Best effort: on any download / upload failure the original URL is returned
    so the editor still has audio; only the render would miss it.
    """
    if not url or not url.strip():
        return url
    u = url.strip()
    if _is_stable_music_host(u):
        return u
    try:
        digest = hashlib.sha1(u.encode("utf-8")).hexdigest()[:20]
        key = f"music/rehosted/{owner_id}/{digest}.mp3"
        public_url = f"{_MEDIA_BASE}/{key}"

        from services.r2_storage import get_r2_storage_service

        r2 = get_r2_storage_service()
        if await r2.key_exists(key):
            return public_url

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(u)
            resp.raise_for_status()
            data = resp.content
        if not data:
            return u

        await r2.upload_bytes(data, key, content_type="audio/mpeg")
        logger.info(
            "music_library.rehosted external track key=%s bytes=%d src=%s",
            key, len(data), u[:120],
        )
        return public_url
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning(
            "music_library.rehost_failed src=%s err=%s — keeping original URL",
            u[:120], exc,
        )
        return u

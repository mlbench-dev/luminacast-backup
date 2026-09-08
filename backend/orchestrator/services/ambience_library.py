"""Fixed ambience-bed library — seamless background-atmosphere loops.

Where SFX (``services/sfx_library``) are deliberate one-shot accents mixed at
full level, an *ambience bed* is a low, continuous atmosphere that plays UNDER
the narration for the length of a scene and is chosen automatically from the
scene's environment (``studio`` / ``room`` / ``outdoor`` — see
``models.avatar_look.SceneEnvironment``). It is mixed like background music —
summed, EQ'd, and sidechain-ducked under the voice — but at a much lower level
(a bed you feel, not hear).

This is the "real ambience BED (street / traffic / wind under an outdoor line)"
follow-up flagged in ``services/media_processing`` next to the scene-acoustic
voice EQ: that module shapes the *voice* for the room, this one lays the
*atmosphere* under it. The two are independent and both key off the same
``AvatarLook.environment`` value.

Loops live on the public media CDN at ``media.luminacast.com/ambience/<name>.wav``
(same host the SFX + music libraries stream from) and are uploaded by
``scripts/seed_ambience_library.py``. Keep the names stable — env overrides and
any stored per-cast choice reference them by name.

Everything here is inert unless ``SCENE_AMBIENCE_ENABLED`` is truthy, mirroring
the ``NANO_BANANA_PRO_ENABLED`` rollout pattern — the feature ships dark and is
turned on per environment once real loop assets are in place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Public media CDN base — same host services/sfx_library + services/music_library use.
_MEDIA_BASE = "https://media.luminacast.com"

_TRUTHY = {"1", "true", "yes", "on"}


def scene_ambience_enabled() -> bool:
    """Master switch. Read at call time so it can be flipped without a redeploy."""
    return os.getenv("SCENE_AMBIENCE_ENABLED", "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class AmbienceEntry:
    name: str
    url: str
    loop_s: float          # length of the seamless source loop (seconds)
    default_volume: float   # linear gain — a BED, deliberately well under music


def _url(name: str) -> str:
    return f"{_MEDIA_BASE}/ambience/{name}.wav"


# name -> (loop length seconds, default linear volume). Volumes are quiet on
# purpose: music beds sit around 0.15 linear, ambience sits below that so the
# voice and any music still dominate. Tune per-env at call time with
# SCENE_AMBIENCE_<ENV>_VOL without touching this table.
_AMBIENCE_SPECS: tuple[tuple[str, float, float], ...] = (
    ("room_tone",    12.0, 0.06),
    ("office_hum",   15.0, 0.05),
    ("cafe_murmur",  18.0, 0.07),
    ("city_street",  20.0, 0.08),
    ("wind_soft",    16.0, 0.09),
    ("nature_birds", 20.0, 0.07),
    ("ocean_waves",  18.0, 0.08),
)

AMBIENCE_CATALOG: dict[str, AmbienceEntry] = {
    name: AmbienceEntry(name=name, url=_url(name), loop_s=loop_s, default_volume=vol)
    for name, loop_s, vol in _AMBIENCE_SPECS
}

# Scene environment -> default ambience bed. ``studio`` is intentionally absent:
# a treated studio has no atmosphere, so a studio scene gets no bed. Override
# the choice for an environment with SCENE_AMBIENCE_<ENV>_NAME.
_ENV_DEFAULT: dict[str, str] = {
    "room": "room_tone",
    "outdoor": "wind_soft",
}


def _vol_override(env_key: str, fallback: float) -> float:
    raw = os.getenv(f"SCENE_AMBIENCE_{env_key.upper()}_VOL", "").strip()
    if not raw:
        return fallback
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return fallback


def for_environment(environment: str | None) -> AmbienceEntry | None:
    """Resolve a scene environment to its ambience bed, or ``None``.

    ``None`` when ambience is disabled, the environment is ``studio``/unknown,
    or the configured loop name is not in the catalog. Volume can be nudged per
    environment via ``SCENE_AMBIENCE_<ENV>_VOL``; the loop itself via
    ``SCENE_AMBIENCE_<ENV>_NAME``.
    """
    if not scene_ambience_enabled():
        return None
    key = (environment or "").strip().lower()
    if not key:
        return None
    name = os.getenv(f"SCENE_AMBIENCE_{key.upper()}_NAME", "").strip() or _ENV_DEFAULT.get(key)
    if not name:
        return None
    entry = AMBIENCE_CATALOG.get(name)
    if entry is None:
        return None
    vol = _vol_override(key, entry.default_volume)
    if vol == entry.default_volume:
        return entry
    return AmbienceEntry(name=entry.name, url=entry.url, loop_s=entry.loop_s, default_volume=vol)


def lookup(name: str | None) -> AmbienceEntry | None:
    """Resolve an ambience name to its catalog entry, or ``None``. Case-insensitive.

    Used to honour an explicit choice (a planner ``ambient_bed`` tag or a stored
    per-cast override) rather than the environment default.
    """
    if not name:
        return None
    return AMBIENCE_CATALOG.get(name.strip().lower())


def list_entries() -> list[AmbienceEntry]:
    """Catalog in declaration order — for the Music page's library view."""
    return list(AMBIENCE_CATALOG.values())

"""Fixed sound-effect library.

The creative LLM may insert ``[sfx:NAME]`` markers into a script (grammar
defined in ``_specs/Music_SFX_Knowledge_Base_v1.md`` Section 2). Each marker
names a short audio clip that must be overlaid on top of the narration at the
moment it occurs — *not* spoken aloud. ``utils/script_cleaning`` strips the
marker text so it never reaches captions or TTS; this module is the other
half: it resolves a marker name to the actual sound file the renderer mixes
into the final audio.

The 18 names below are the contract the LLM is told to emit, mirrored from the
knowledge base. ``url`` points at the public media CDN (R2-backed), same host
the music library streams from; ``scripts/seed_sfx_library.py`` uploads the
clips. ``duration_s`` is the clip length (used to bound the timeline element)
and ``default_volume`` is its mix level (SFX hit at full level, unlike ducked
background music).
"""
from __future__ import annotations

from dataclasses import dataclass

# Public media CDN base — same host services/music_library uses.
_MEDIA_BASE = "https://media.luminacast.com"


@dataclass(frozen=True)
class SfxEntry:
    name: str
    url: str
    duration_s: float
    default_volume: float


def _url(name: str) -> str:
    return f"{_MEDIA_BASE}/sfx/{name}.wav"


# Mirrored from Music_SFX_Knowledge_Base_v1.md Section 2 (18 SFX). Keep the
# names stable — they are the marker grammar the LLM emits.
_SFX_SPECS: tuple[tuple[str, float], ...] = (
    ("whoosh", 0.5),
    ("pop", 0.3),
    ("ding", 0.5),
    ("cash_register", 0.8),
    ("sparkle", 0.7),
    ("record_scratch", 0.6),
    ("swoosh_up", 0.5),
    ("swoosh_down", 0.5),
    ("notification", 0.4),
    ("timer_tick", 0.3),
    ("click", 0.2),
    ("drumroll", 1.2),
    ("applause", 1.5),
    ("camera_shutter", 0.3),
    ("bass_drop", 0.5),
    ("typing", 0.8),
    ("coin", 0.4),
    ("success", 0.5),
)

# Full-level mix for SFX — they are deliberate accents, not a bed, so they are
# not ducked against narration (see cast_ffmpeg_composer SFX mixing).
_DEFAULT_SFX_VOLUME = 1.0

SFX_CATALOG: dict[str, SfxEntry] = {
    name: SfxEntry(
        name=name,
        url=_url(name),
        duration_s=duration_s,
        default_volume=_DEFAULT_SFX_VOLUME,
    )
    for name, duration_s in _SFX_SPECS
}


def lookup(name: str | None) -> SfxEntry | None:
    """Resolve an SFX marker name to its catalog entry, or ``None``.

    Case-insensitive. Unknown names return ``None`` — callers log and skip
    rather than failing the render.
    """
    if not name:
        return None
    return SFX_CATALOG.get(name.strip().lower())

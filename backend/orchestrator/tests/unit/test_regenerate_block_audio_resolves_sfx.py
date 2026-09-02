"""Per-block audio regen must resolve [sfx:NAME] markers.

Bug: adding ``[sfx:record_scratch]`` to a block's script and hitting the
per-block "Regenerate audio" button produced audio with no sound effect. The
full-cast TTS task (``_generate_tts_only``) extracts markers and aligns them
after its WhisperX pass, but ``regenerate_block_audio`` created the new variant
straight from ``fish.generate_tts`` and never populated ``sfx_markers`` /
``sfx_timings`` — so the timeline builders (backend timeline.py and the
frontend editorStarterMapping) had nothing to turn into an audio element.

The sibling cascade path (``_cascade_audio_to_siblings``) had the same gap.

DB-free: asserts on source, matching test_tts_regen_applies_mic_style.py.
"""
from __future__ import annotations

from pathlib import Path

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_VARIANTS = _ORCH_ROOT / "routers" / "casts" / "variants.py"
_SFX_EXTRACTION = _ORCH_ROOT / "utils" / "sfx_extraction.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_helper_exists():
    src = _read(_SFX_EXTRACTION)
    assert "def resolve_sfx_for_script(" in src
    # combines both stages so callers can't do one without the other
    assert "extract_sfx_markers(" in src
    assert "align_sfx_to_words(" in src


def test_regenerate_block_audio_populates_sfx_fields():
    src = _read(_VARIANTS)
    start = src.find("async def regenerate_block_audio(")
    assert start != -1
    end = src.find("\n@router", start)
    body = src[start:end if end != -1 else None]

    assert "resolve_sfx_for_script(" in body, (
        "per-block regen must extract + align [sfx:*] markers from the script"
    )
    assert "new_variant.sfx_markers = " in body
    assert "new_variant.sfx_timings = " in body
    # must land in the same commit that persists the audio, i.e. before the
    # success-path db.commit().
    assert body.index("resolve_sfx_for_script(") < body.index("await db.commit()")


def test_sibling_cascade_carries_sfx_onto_new_variant():
    src = _read(_VARIANTS)
    start = src.find("async def _cascade_audio_to_siblings(")
    assert start != -1
    end = src.find("\ndef _build_script_system_prompt", start)
    body = src[start:end if end != -1 else None]

    assert "resolve_sfx_for_script(" in body
    assert "sfx_markers=cascade_sfx_markers or None" in body
    assert "sfx_timings=cascade_sfx_timings or None" in body

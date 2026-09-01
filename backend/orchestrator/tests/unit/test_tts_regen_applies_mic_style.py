"""Regen paths must honour the mic-style / scene EQ setting.

Two gaps this covers:

  1. ``generate-tts`` skipped every block that already had audio, so toggling
     the avatar's mic style (a sound-only change — no script edit) never took
     effect. A ``force`` flag now threads endpoint -> task -> the per-variant
     skip so a full rebuild is possible.

  2. ``regenerate_block_audio`` (the per-block "Regenerate" button) called
     ``fish.generate_tts`` with no ``clip_mic_enabled`` / ``scene_chain_id``,
     producing raw TTS with zero mic-style post-processing.

DB-free: these assert on source, matching tests/unit/test_action_block_voiceover.py.
"""
from __future__ import annotations

from pathlib import Path

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_GENERATE_CAST = _ORCH_ROOT / "tasks" / "generate_cast.py"
_TTS_CAPTIONS = _ORCH_ROOT / "routers" / "casts" / "tts_captions.py"
_VARIANTS = _ORCH_ROOT / "routers" / "casts" / "variants.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── 1. force flag threads endpoint -> task -> skip guard ─────────────────────

def test_generate_tts_task_accepts_force():
    src = _read(_GENERATE_CAST)
    assert "def generate_cast_tts_task(self, cast_id: str, user_id: str, force: bool = False)" in src
    assert "_generate_tts_only(cast_id, user_id, force=force)" in src
    assert "async def _generate_tts_only(cast_id: str, user_id: str, force: bool = False)" in src


def test_per_variant_skip_is_bypassed_when_forced():
    src = _read(_GENERATE_CAST)
    # The "already has audio, skip" fast-path must be gated on `not force`.
    assert "if not force and variant.status == VariantStatus.READY and variant.audio_key:" in src


def test_generate_tts_endpoint_forwards_force():
    src = _read(_TTS_CAPTIONS)
    assert "force: bool = Body(False" in src
    assert "generate_cast_tts_task.delay(cast_id, ctx.workspace_owner_id, bool(force))" in src


# ── 2. per-block regenerate applies the mic-style / scene chain ─────────────

def test_regenerate_block_audio_resolves_and_passes_mic_style():
    src = _read(_VARIANTS)
    start = src.find("async def regenerate_block_audio(")
    assert start != -1
    end = src.find("\n@router", start)
    body = src[start:end if end != -1 else None]

    assert "resolve_scene_voice_settings(" in body, (
        "per-block regen must resolve the block>scene>avatar mic precedence"
    )
    # ...and hand the result to the TTS call (previously it passed neither).
    assert "clip_mic_enabled=_clip_mic" in body
    assert "scene_chain_id=_scene_chain" in body

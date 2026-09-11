"""SFX must never fire at a stale timestamp after the audio it was aligned
to is replaced (script edit, render-time TTS refresh, or a captions realign).

Bug (client report): "SFX appears misaligned / inappropriate for the
scene/action" on completed renders. Root cause: ``variant.sfx_timings`` is a
list of ABSOLUTE-SECOND offsets resolved against one specific take's word
timestamps (utils.sfx_extraction.align_sfx_to_words). Three call paths
replace the audio (and therefore its word timing) without recomputing or
dropping the old ``sfx_timings``:

  1. ``_mark_variant_audio_stale`` (script text edit) — nulled every audio
     field except sfx_timings.
  2. ``_ensure_fresh_tts_for_block`` (render-time last-resort TTS refresh in
     cast_render.py) — same gap, and this path never reaches a caption
     realignment step afterward, so the stale value was permanent.
  3. ``generate_captions`` (routers/casts/tts_captions.py, the "realign
     captions" endpoint used whenever TTS was regenerated elsewhere) —
     recomputed caption_words but never touched sfx_timings at all.

A concrete repro: same script, two takes with different pacing (e.g. after
"Regenerate voice") shifts a late marker by 2+ seconds — see the worked
example in test_stale_timing_drifts_multiple_seconds below, using the real
alignment function.

DB-free: (a) asserts on source for the three invalidation/realign sites, (b)
exercises the real align_sfx_to_words function to demonstrate the drift a
stale value would cause vs. the corrected one.
"""
from __future__ import annotations

from pathlib import Path

from utils.sfx_extraction import extract_sfx_markers, align_sfx_to_words

_ORCH_ROOT = Path(__file__).resolve().parents[2]
_VARIANTS = _ORCH_ROOT / "routers" / "casts" / "variants.py"
_CAST_RENDER = _ORCH_ROOT / "tasks" / "cast_render.py"
_TTS_CAPTIONS = _ORCH_ROOT / "routers" / "casts" / "tts_captions.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_script_edit_invalidation_clears_sfx_timings():
    src = _read(_VARIANTS)
    start = src.find("def _mark_variant_audio_stale(")
    assert start != -1
    end = src.find("\ndef ", start + 1)
    body = src[start:end if end != -1 else None]
    assert "variant.caption_words = None" in body
    assert "variant.sfx_timings = None" in body, (
        "script-edit invalidation must also drop sfx_timings — otherwise a "
        "stale [sfx:NAME] timestamp from the old take survives onto the new one"
    )


def test_render_time_tts_refresh_clears_sfx_timings():
    src = _read(_CAST_RENDER)
    start = src.find("async def _ensure_fresh_tts_for_block(")
    assert start != -1
    end = src.find("\nasync def ", start + 1)
    body = src[start:end if end != -1 else None]
    assert "variant.caption_words = None" in body
    assert "variant.sfx_timings = None" in body, (
        "the render-time defensive TTS refresh must drop sfx_timings too — "
        "this path never re-runs alignment afterward, so a stale value here "
        "is permanent, not just a transient window"
    )


def test_generate_captions_recomputes_sfx_timings():
    src = _read(_TTS_CAPTIONS)
    assert "def _realign_sfx(" in src, (
        "generate-captions (the realign endpoint hit whenever TTS is "
        "regenerated elsewhere) must re-resolve sfx_timings against the "
        "freshly recomputed caption_words, not leave the old value in place"
    )
    start = src.find("async def generate_captions(")
    assert start != -1
    end = src.find("\n@router", start + 1)
    body = src[start:end if end != -1 else None]
    # called on both the Whisper-success and the fallback-alignment branch
    assert body.count("_realign_sfx(variant)") >= 2


def test_stale_timing_drifts_multiple_seconds():
    """Worked example: a late marker, two takes of the same script with
    different pacing (exactly what "Regenerate voice" produces). The stale
    (unrecomputed) timestamp lands 2+ seconds away from the corrected one —
    concretely reproducing the reported "SFX doesn't match the scene" bug.
    """
    text = (
        "This cream is basically magic in a jar and honestly changed my "
        "skin in one week [sfx:cash_register] all that for only $24.99"
    )
    markers = extract_sfx_markers(text)
    assert len(markers) == 1 and markers[0].name == "cash_register"

    words = (
        "This cream is basically magic in a jar and honestly changed my "
        "skin in one week all that for only 24 99"
    ).split()

    slow_take = [
        {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
        for i, w in enumerate(words)
    ]
    slow_timing = align_sfx_to_words(
        markers, slow_take, tts_duration_seconds=slow_take[-1]["end"]
    )

    # A regenerated take: same words, materially tighter pacing.
    fast_take = [
        {"word": w, "start": i * 0.24, "end": i * 0.24 + 0.2}
        for i, w in enumerate(words)
    ]
    fast_timing_correct = align_sfx_to_words(
        markers, fast_take, tts_duration_seconds=fast_take[-1]["end"]
    )

    drift_s = slow_timing[0]["start_s"] - fast_timing_correct[0]["start_s"]
    assert drift_s > 2.0, (
        "expected a multi-second drift between the stale and the correctly "
        "re-resolved timing for this example"
    )
    # And the stale value would fire past the (shorter) new clip's own end —
    # i.e. never even audible in the render, or audible over the NEXT block.
    assert slow_timing[0]["start_s"] > fast_take[-1]["end"]

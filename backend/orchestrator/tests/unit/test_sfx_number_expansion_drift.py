"""Client report: "the sounds it attaches with the script don't match the
context or the video." Root cause (independent of the audio content itself,
which is confirmed correct on the server): a marker's ``word_index`` is
counted against the WRITTEN script text, but is used to index into
``caption_words`` — WhisperX's transcription of what was actually SPOKEN.
Those two word counts silently diverge whenever the script has a number,
price, percentage, or abbreviation near a marker, because TTS speaks (and
WhisperX transcribes) e.g. "$24.99" as five words ("twenty four dollars
ninety nine") while the written script has it as one token. Every marker
after the divergence point lands on the wrong spoken word — indistinguishable
from "the AI chose a bad sound effect" even though the marker NAME was
correct. The SFX prompt's own worked example (services/ai_prompts.py) uses a
price reveal right next to [sfx:cash_register] — exactly the failure shape.

Fix: utils.sfx_extraction._map_text_words_to_spoken re-aligns written-text
word indices onto the transcribed word list via difflib, only when the counts
actually diverge (so the common case is untouched), and every real caller of
align_sfx_to_words now passes ``script_words`` so the correction applies.

Pure unit tests — no DB, no network.
"""
from __future__ import annotations

from utils.sfx_extraction import (
    extract_sfx_markers,
    align_sfx_to_words,
    resolve_sfx_for_script,
    _map_text_words_to_spoken,
)
from utils.script_cleaning import clean_script_tokens


def _caption_words(spoken: list[str], step: float = 0.32, dur: float = 0.28) -> list[dict]:
    out, t = [], 0.0
    for w in spoken:
        out.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += step
    return out


# The exact shape from the SFX prompt's own good example: a price mentioned
# earlier in the block, then the SFX-marked price reveal later.
_TEXT = (
    "Okay wait. This $7 cream just beat a $200 dupe. "
    "[sfx:cash_register] And right now it's only $24.99."
)
_SPOKEN = (
    "Okay wait this seven dollar cream just beat a two hundred dollar dupe "
    "and right now it's only twenty four dollars and ninety nine cents"
).split()


def test_currency_expansion_reproduces_the_reported_drift_without_the_fix():
    """Sanity check that the bug is real: WITHOUT script_words, the marker
    lands on an unrelated word ("hundred", from the earlier $200 mention),
    not anywhere near the $24.99 price it was written to punctuate."""
    markers = extract_sfx_markers(_TEXT)
    cw = _caption_words(_SPOKEN)
    old = align_sfx_to_words(markers, cw, tts_duration_seconds=cw[-1]["end"])
    landed_word = cw[markers[0].word_index]["word"]
    assert landed_word != "and"  # the intended anchor word
    assert landed_word == "hundred"  # what it actually lands on today


def test_script_words_corrects_the_drift():
    markers = extract_sfx_markers(_TEXT)
    script_words = clean_script_tokens(_TEXT)
    cw = _caption_words(_SPOKEN)
    fixed = align_sfx_to_words(
        markers, cw, tts_duration_seconds=cw[-1]["end"], script_words=script_words,
    )
    idx = next(i for i, w in enumerate(cw) if abs(w["start"] - fixed[0]["start_s"]) < 1e-6)
    assert cw[idx]["word"] == "and"


def test_resolve_sfx_for_script_applies_the_fix_automatically():
    """The two already-fixed callers (variants.py) go through this helper —
    confirm it derives script_words itself, no caller change needed."""
    cw = _caption_words(_SPOKEN)
    _markers_json, timings = resolve_sfx_for_script(
        _TEXT, cw, tts_duration_seconds=cw[-1]["end"],
    )
    idx = next(i for i, w in enumerate(cw) if abs(w["start"] - timings[0]["start_s"]) < 1e-6)
    assert cw[idx]["word"] == "and"


def test_matching_word_counts_are_completely_unaffected():
    """No numbers/abbreviations -> counts already match -> byte-identical
    output with or without script_words. The fix must never change behavior
    for the common case."""
    text = "Wait, [sfx:sparkle] this changes everything."
    markers = extract_sfx_markers(text)
    script_words = clean_script_tokens(text)
    cw = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(script_words)]
    without = align_sfx_to_words(markers, cw, tts_duration_seconds=cw[-1]["end"])
    with_fix = align_sfx_to_words(
        markers, cw, tts_duration_seconds=cw[-1]["end"], script_words=script_words,
    )
    assert without == with_fix


def test_map_text_words_to_spoken_handles_insertions_and_deletions():
    text_words = ["the", "$5", "special"]
    spoken_words = ["the", "five", "dollar", "special"]
    mapping = _map_text_words_to_spoken(text_words, spoken_words)
    assert mapping[0] == 0            # "the" -> "the"
    assert mapping[2] == 3            # "special" -> "special" (after the 2-word expansion)


def test_map_text_words_to_spoken_empty_inputs():
    assert _map_text_words_to_spoken([], ["a", "b"]) == []
    assert _map_text_words_to_spoken(["a"], []) == [0]


def test_trailing_marker_still_anchors_to_speech_end_with_script_words():
    text = "That's the whole story. [sfx:success]"
    markers = extract_sfx_markers(text)
    script_words = clean_script_tokens(text)
    cw = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(script_words)]
    out = align_sfx_to_words(
        markers, cw, tts_duration_seconds=cw[-1]["end"], script_words=script_words,
    )
    assert out[0]["start_s"] == cw[-1]["end"]

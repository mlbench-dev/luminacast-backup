"""Unit tests for utils.script_cleaning and the caption-fallback contract.

Regression-1: captions and TTS must never show/speak internal script
direction markers like [sfx:record_scratch], (excited), [pause].
"""
from __future__ import annotations

import pytest

from utils.script_cleaning import strip_script_markers, clean_script_tokens


@pytest.mark.parametrize(
    "raw,expected",
    [
        # bracketed SFX/pause markers
        ("[sfx:record_scratch] Okay wait.", "Okay wait."),
        ("Hold on [pause] now look.", "Hold on now look."),
        # parenthetical prosody
        ("(excited) This is amazing!", "This is amazing!"),
        ("Honestly (whispering) you need this", "Honestly you need this"),
        # mixed brackets + parens
        ("[sfx:sparkle] (casual) Just $7 [pause] today (excited)!", "Just $7 today !"),
        # no markers — passthrough (whitespace collapsed/trimmed)
        ("Plain script with no markers.", "Plain script with no markers."),
        ("  leading and trailing  ", "leading and trailing"),
        # empty / falsy
        ("", ""),
    ],
)
def test_strip_script_markers(raw, expected):
    assert strip_script_markers(raw) == expected


def test_strip_script_markers_none_safe():
    assert strip_script_markers(None) == ""  # type: ignore[arg-type]


def test_strip_script_markers_marker_only_becomes_empty():
    # A token that is entirely a marker collapses to empty.
    assert strip_script_markers("[sfx:boom]") == ""
    assert strip_script_markers("(excited)") == ""


def test_clean_script_tokens_drops_empty():
    tokens = clean_script_tokens("[sfx:boom] (excited) hello (casual) world [pause]")
    assert tokens == ["hello", "world"]


def test_clean_script_tokens_no_markers():
    assert clean_script_tokens("one two three") == ["one", "two", "three"]


def _build_fallback_caption_words(script_text: str, duration: float) -> list[dict]:
    """Mirror of the WhisperX-fail fallback in tasks/generate_cast.py and
    routers/casts.py — builds caption_words from cleaned script tokens."""
    words = clean_script_tokens(script_text)
    tpw = duration / max(len(words), 1)
    return [
        {"word": w, "start": round(i * tpw, 3), "end": round((i + 1) * tpw, 3), "probability": 0.5}
        for i, w in enumerate(words)
    ]


def test_whisper_fail_fallback_caption_words_have_no_markers():
    script = (
        "[sfx:record_scratch] Okay wait. (excited) This $7 cream [sfx:sparkle] "
        "just changed everything. (whispering) You need this. [pause] (casual) Trust me."
    )
    caption_words = _build_fallback_caption_words(script, duration=12.0)

    assert caption_words, "fallback should produce caption words"
    banned_substrings = ["[", "]", "(", ")", "sfx:", "pause", "excited", "whispering", "casual"]
    for w in caption_words:
        word = w["word"]
        for bad in banned_substrings:
            assert bad not in word.lower(), f"marker {bad!r} leaked into caption word {word!r}"

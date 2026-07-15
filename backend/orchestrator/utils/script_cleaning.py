"""Shared helpers for cleaning internal script direction markers.

Scripts produced by the creative LLM carry two kinds of internal direction
markers that must never reach the viewer:

  * Bracketed markers — ``[sfx:record_scratch]``, ``[pause]``
  * Parenthetical prosody — ``(excited)``, ``(casual)``, ``(whispering)``

These are instructions for downstream stages, not speech. They must be
stripped before the text is used to build captions or sent to TTS. (See
``services/ai_prompts.py`` for the marker grammar the LLM is told to emit.)
"""

import re

# Bracketed markers like [pause], [sfx:NAME]; then parenthetical prosody like
# (excited), (whispering). Same patterns previously inlined in
# engine/cast_generator._count_words.
_BRACKET_MARKER = re.compile(r"\[[^\]]*\]")
_PAREN_MARKER = re.compile(r"\([^\)]*\)")


def strip_script_markers(text: str) -> str:
    """Remove ``[...]`` and ``(...)`` direction markers from script text.

    Collapses any resulting double spaces and trims. Returns an empty string
    for falsy input.
    """
    if not text:
        return ""
    cleaned = _BRACKET_MARKER.sub(" ", text)
    cleaned = _PAREN_MARKER.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def clean_script_tokens(text: str) -> list[str]:
    """Strip markers then split into tokens, dropping any empty after stripping."""
    return [w for w in strip_script_markers(text).split() if w]

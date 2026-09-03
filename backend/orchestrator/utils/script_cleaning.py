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


# Prosody / pause / non-verbal-beat direction tags the script generator is no
# longer told to emit — the whole prosody family (inline mood tags, [pause],
# and the [laugh]/[gasp]/... "prosody_only" beats) is deferred to a later
# phase. They were stripped before TTS anyway, so left in a stored script they
# only confused users in the editor. Narrow, literal matches only: `[sfx:NAME]`
# is kept, and a genuine parenthetical aside in dialogue ("(and yes, really)")
# is untouched.
_DEFERRED_PROSODY_MARKER = re.compile(
    r"\(\s*(?:excited|casual|whispering|laughing|sighing|super\s+happy)\s*\)"
    r"|\[\s*pause(?:\s*:\s*[0-9.]+)?\s*\]"
    r"|\[\s*(?:laugh|laughing|gasp|sigh|sighing|breath|breathe|hum|chuckle|scoff)\s*\]",
    re.IGNORECASE,
)


def strip_prosody_pause_markers(text: str) -> str:
    """Remove deferred prosody direction tags from a script.

    Covers the inline ``(excited)`` family, ``[pause]`` / ``[pause:1.2]``, and
    the bare non-verbal beats (``[laugh]``, ``[gasp]``, ``[sigh]``, …).
    Preserves ``[sfx:NAME]`` markers and genuine parenthetical asides.
    Collapses resulting double spaces and tidies spacing before punctuation.
    Returns an empty string for falsy input.
    """
    if not text:
        return ""
    cleaned = _DEFERRED_PROSODY_MARKER.sub(" ", text)
    cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()

"""Sanitize a user-written avatar_action "Motion description" before it is
handed to an image-to-video model (Kling / Wan / Kling-Elements).

Video models don't parse a sentence into a plan — the prompt is a weak global
signal, so they keep ~1 concrete action and silently drop sequences,
conditionals, physics, and outcomes. Users type things like
"walk and throw the product on the floor so it bounces and hits the avatar on
the head" and are surprised when the render shows only a throw.

This runs the raw text through one cheap LLM pass that rewrites it into a
single concrete, physically-plausible ~5-second camera action. The user's
original text is left untouched on the block — only what the video model
receives is cleaned. Best-effort: any failure (no API key, timeout, empty
result) returns the original string unchanged so a render never breaks here.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MOTION_PROMPT_MAX_WORDS = 30

_SYSTEM_PROMPT = (
    "You rewrite a creator's rough motion note into ONE concrete, "
    "physically-plausible camera action for a ~5 second AI-generated video "
    "clip of a single person.\n"
    "Rules:\n"
    "- Output exactly one sentence, present tense, describing only what is "
    "visibly moving plus the camera. No preamble, no quotes.\n"
    "- ONE action. Drop multi-step sequences (no 'then', no 'and then', no "
    "'after that').\n"
    "- Drop outcomes, claims, and purposes: anything like 'to show', "
    "'proves', 'undamaged', 'without a dent', 'demonstrating durability'.\n"
    "- Drop impossible or unreliable physics: an object ricocheting to hit a "
    "specific body part, bouncing back to the hand, landing perfectly "
    "upright, etc. Keep only the plausible core (e.g. 'throws it down and it "
    "bounces').\n"
    "- Keep the real product / object and the setting if given.\n"
    f"- Under {MOTION_PROMPT_MAX_WORDS} words.\n"
    "Return only the rewritten sentence."
)

# Process-local cache: re-renders and sibling blocks with identical text reuse
# the same rewrite instead of paying for another LLM call.
_cache: dict[str, str] = {}


def _trim_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",;:") + "."


def _clean_llm_line(raw: str) -> str:
    line = (raw or "").strip()
    # Models sometimes wrap the answer in quotes or prefix "Rewritten: ".
    for prefix in ("rewritten:", "rewrite:", "output:", "action:"):
        if line.lower().startswith(prefix):
            line = line[len(prefix):].strip()
    line = line.strip().strip('"').strip("'").strip()
    # Keep only the first line/sentence-ish chunk.
    line = line.splitlines()[0].strip() if line else line
    return line


async def sanitize_motion_prompt(raw: str, *, cast_id: str | None = None) -> str:
    """Return a single-action, model-friendly rewrite of ``raw``.

    Returns ``raw`` unchanged when it is empty, already terse, or when the LLM
    pass fails for any reason.
    """
    text = (raw or "").strip()
    if not text:
        return raw
    # Already short and simple — a single clause, no chaining, no obvious
    # outcome language. Skip the round-trip.
    lowered = text.lower()
    _chain_markers = (" then ", "; then", ", then", " and then ", " after that")
    _outcome_markers = (
        "to show", "prove", "proving", "without a dent", "no dent", "undamaged",
        "durab", "before and after", "unharmed", "unbroken",
    )
    if (
        len(text.split()) <= 8
        and not any(m in lowered for m in _chain_markers)
        and not any(m in lowered for m in _outcome_markers)
    ):
        return text

    if text in _cache:
        return _cache[text]

    try:
        from services.openrouter import get_openrouter_service

        svc = get_openrouter_service()
        out = await svc.generate_text(
            prompt=text,
            system_prompt=_SYSTEM_PROMPT,
            model="anthropic/claude-3-haiku",
            max_tokens=120,
            temperature=0.2,
        )
        cleaned = _trim_to_words(_clean_llm_line(out), MOTION_PROMPT_MAX_WORDS)
        if not cleaned:
            return text
        if cleaned != text:
            logger.info(
                "motion_prompt.sanitized cast=%s in=%r out=%r",
                cast_id, text[:160], cleaned[:160],
            )
        _cache[text] = cleaned
        return cleaned
    except Exception as exc:  # never break a render over prompt cleanup
        import sentry_sdk

        sentry_sdk.capture_exception(exc)
        logger.warning(
            "motion_prompt.sanitize failed cast=%s (%s) — using raw text",
            cast_id, exc,
        )
        return text

"""``services.motion_prompt.sanitize_motion_prompt`` — clean a user's
avatar_action Motion description before it reaches an image-to-video model.

The LLM call is mocked; these cover the routing (skip vs. call), the response
cleaning, and the never-break-a-render fallbacks.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.motion_prompt import (
    sanitize_motion_prompt,
    _clean_llm_line,
    _trim_to_words,
    _cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.clear()
    yield
    _cache.clear()


def _svc(reply: str) -> AsyncMock:
    svc = AsyncMock()
    svc.generate_text = AsyncMock(return_value=reply)
    return svc


# ── pure helpers ────────────────────────────────────────────────────────────

def test_clean_llm_line_strips_quotes_and_prefixes():
    assert _clean_llm_line('"she throws it down."') == "she throws it down."
    assert _clean_llm_line("Rewritten: she throws it down.") == "she throws it down."
    assert _clean_llm_line("she throws it\nextra commentary") == "she throws it"


def test_trim_to_words_caps_length():
    long = " ".join(["word"] * 50)
    out = _trim_to_words(long, 30)
    assert len(out.split()) == 30
    assert out.endswith(".")


# ── routing: skip the LLM for already-terse, simple notes ───────────────────

@pytest.mark.asyncio
async def test_short_simple_note_is_returned_untouched_without_calling_llm():
    svc = _svc("SHOULD NOT BE USED")
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        out = await sanitize_motion_prompt("walks toward the camera")
    assert out == "walks toward the camera"
    svc.generate_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_is_passed_through():
    out = await sanitize_motion_prompt("   ")
    assert out == "   "


# ── routing: call the LLM for chained / outcome-laden / long notes ──────────

@pytest.mark.asyncio
async def test_chained_note_is_rewritten():
    svc = _svc("she throws the rice cooker down and it bounces off the ground")
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        out = await sanitize_motion_prompt(
            "walk and throw the product on the floor, then it bounces and hits "
            "the avatar on the head"
        )
    assert out == "she throws the rice cooker down and it bounces off the ground"
    svc.generate_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_outcome_language_triggers_rewrite_even_when_short():
    svc = _svc("she drops the box and it hits the floor")
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        out = await sanitize_motion_prompt("drop it to prove it's undamaged")
    assert out == "she drops the box and it hits the floor"
    svc.generate_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_result_is_cached_across_calls():
    svc = _svc("she hurls it at the wall")
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        a = await sanitize_motion_prompt("throw it then it comes back and bonks her head")
        b = await sanitize_motion_prompt("throw it then it comes back and bonks her head")
    assert a == b == "she hurls it at the wall"
    svc.generate_text.assert_awaited_once()  # second call served from cache


# ── never break a render ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_failure_returns_original_text():
    svc = AsyncMock()
    svc.generate_text = AsyncMock(side_effect=RuntimeError("openrouter down"))
    raw = "walk and throw it then catch it then bow to the crowd"
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        out = await sanitize_motion_prompt(raw)
    assert out == raw


@pytest.mark.asyncio
async def test_empty_llm_reply_returns_original_text():
    svc = _svc("   ")
    raw = "throw it, then it bounces, then it lands upright to show it's fine"
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        out = await sanitize_motion_prompt(raw)
    assert out == raw

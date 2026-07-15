"""Bug 3a regression — ``generate_outline`` must surface the avatar's
visual description into the user prompt so the b-roll / stock-media
queries are anchored to the actual on-camera host.
"""
from __future__ import annotations

import pytest

from engine import cast_generator as cg


class _CaptureOAI:
    """Stands in for the OpenRouter service; records the prompts it was
    called with and returns an empty JSON array."""

    last_usage: dict = {}

    def __init__(self, sink):
        self._sink = sink

    async def generate_text(self, *, prompt, system_prompt, model, max_tokens, temperature):
        self._sink["user"] = prompt
        self._sink["system"] = system_prompt
        return "[]"


def _patch_outline_deps(monkeypatch, sink):
    import services.openrouter as openrouter_mod
    import services.content_type as content_type_mod

    monkeypatch.setattr(openrouter_mod, "get_openrouter_service", lambda: _CaptureOAI(sink))

    async def _fake_detect(_desc, _products=None):
        return {"type": "product_showcase", "role": "host", "style": "energetic"}

    monkeypatch.setattr(content_type_mod, "detect_content_type", _fake_detect)
    monkeypatch.setattr(content_type_mod, "fill_dynamic_placeholders", lambda tmpl, _ct: tmpl)
    monkeypatch.setattr(cg, "log_creative_model_use", lambda *_a, **_k: None, raising=False)


@pytest.mark.asyncio
async def test_outline_prompt_includes_avatar_visual_description(monkeypatch):
    sink: dict = {}
    _patch_outline_deps(monkeypatch, sink)

    persona = {"name": "Lina", "visual_description": "Mediterranean woman, 30s"}

    await cg.generate_outline(
        cast_id="cst_test",
        products=[{"name": "PowerBank", "description": "slim charger"}],
        persona=persona,
        template_name="custom",
        description="product showcase video for my power bank",
        target_audience={"age_range": "25-34", "interests": "tech"},
        duration_target_seconds=45,
        user_id=None,
    )

    user_prompt = sink.get("user", "")
    assert "AVATAR (the on-camera host)" in user_prompt, (
        "the outline user prompt must include the avatar section header"
    )
    assert "Mediterranean woman" in user_prompt
    assert "Lina" in user_prompt


@pytest.mark.asyncio
async def test_outline_prompt_falls_back_to_description_key(monkeypatch):
    """When persona has no ``visual_description`` it falls back to
    ``description``."""
    sink: dict = {}
    _patch_outline_deps(monkeypatch, sink)

    persona = {"name": "Lina", "description": "Mediterranean woman, 30s"}

    await cg.generate_outline(
        cast_id="cst_test",
        products=[],
        persona=persona,
        template_name="custom",
        description="product showcase video",
        user_id=None,
    )

    assert "Mediterranean woman" in sink.get("user", "")

"""Bug 3c regression — ``generate_outline`` must emit a ``[prompt-audit]``
log line carrying the full system + user prompt sent to the LLM, so ops
can grep worker logs to see exactly what the model saw.
"""
from __future__ import annotations

import logging

import pytest

from engine import cast_generator as cg


class _StubOAI:
    last_usage: dict = {}

    async def generate_text(self, *, prompt, system_prompt, model, max_tokens, temperature):
        return "[]"


def _patch_outline_deps(monkeypatch):
    import services.openrouter as openrouter_mod
    import services.content_type as content_type_mod

    monkeypatch.setattr(openrouter_mod, "get_openrouter_service", lambda: _StubOAI())

    async def _fake_detect(_desc, _products=None):
        return {"type": "product_showcase", "role": "host", "style": "energetic"}

    monkeypatch.setattr(content_type_mod, "detect_content_type", _fake_detect)
    monkeypatch.setattr(content_type_mod, "fill_dynamic_placeholders", lambda tmpl, _ct: tmpl)
    monkeypatch.setattr(cg, "log_creative_model_use", lambda *_a, **_k: None, raising=False)


@pytest.mark.asyncio
async def test_outline_prompt_audit_log_emitted(monkeypatch, caplog):
    _patch_outline_deps(monkeypatch)

    persona = {"name": "Lina", "visual_description": "Mediterranean woman, 30s"}

    with caplog.at_level(logging.INFO, logger="engine.cast_generator"):
        await cg.generate_outline(
            cast_id="cst_audit_1",
            products=[{"name": "PowerBank", "description": "slim charger"}],
            persona=persona,
            template_name="custom",
            description="product showcase video for my power bank",
            duration_target_seconds=45,
            user_id=None,
        )

    audit_records = [r for r in caplog.records if "[prompt-audit]" in r.getMessage()]
    assert audit_records, "expected a [prompt-audit] log record from generate_outline"

    msg = audit_records[0].getMessage()
    assert "[prompt-audit]" in msg
    assert "cast=cst_audit_1" in msg
    assert "system=" in msg
    assert "user=" in msg
    # The avatar context must be visible in the audited user prompt.
    assert "Mediterranean woman" in msg

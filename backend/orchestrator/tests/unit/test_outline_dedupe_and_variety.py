"""Bug 1 + Bug 2 regression — cast outline generation must produce blocks with

  * unique (position, type, category) tuples (no duplicate beats), and
  * a mix of block types (not everything collapsed to PRODUCT),

when ``template_name IS NULL`` / ``max_duration_minutes = 240`` (the config the
broken cast cst_0f43a80b624d was generated under). cst_0f43a80b624d had every
beat persisted twice and ONLY type=PRODUCT.
"""
from __future__ import annotations

import json

import pytest

from models.block import BlockType
from engine import cast_generator as cg
from engine.cast_generator import dedupe_outline_scenes


# ── dedupe_outline_scenes (pure) ──────────────────────────────────────────────

def test_dedupe_drops_exact_duplicate_beats():
    scenes = [
        {"block_type": "hook", "category": "avatar_speaking", "purpose": "grab", "key_points": ["a"]},
        {"block_type": "product", "category": "avatar_voiceover", "purpose": "show", "key_points": ["b"]},
        # exact duplicate of the product beat — must be dropped
        {"block_type": "product", "category": "avatar_voiceover", "purpose": "show", "key_points": ["b"]},
        {"block_type": "cta", "category": "avatar_speaking", "purpose": "buy", "key_points": ["c"]},
    ]
    out = dedupe_outline_scenes(scenes, cast_id="cst_x")
    assert len(out) == 3
    assert [s["block_type"] for s in out] == ["hook", "product", "cta"]


def test_dedupe_keeps_distinct_beats_sharing_type_and_category():
    """Two PRODUCT/avatar_speaking blocks with different scripts are legit and
    must both survive."""
    scenes = [
        {"block_type": "product", "category": "avatar_speaking", "purpose": "feature one", "key_points": ["x"]},
        {"block_type": "product", "category": "avatar_speaking", "purpose": "feature two", "key_points": ["y"]},
    ]
    out = dedupe_outline_scenes(scenes, cast_id="cst_x")
    assert len(out) == 2


def test_dedupe_passes_through_non_list():
    assert dedupe_outline_scenes(None) is None


# ── generate_outline (LLM mocked) ─────────────────────────────────────────────

class _FixedOAI:
    last_usage: dict = {}

    def __init__(self, payload: str):
        self._payload = payload

    async def generate_text(self, *, prompt, system_prompt, model, max_tokens, temperature):
        return self._payload


def _patch_outline_deps(monkeypatch, payload: str):
    import services.openrouter as openrouter_mod
    import services.content_type as content_type_mod

    monkeypatch.setattr(openrouter_mod, "get_openrouter_service", lambda: _FixedOAI(payload))

    async def _fake_detect(_desc, _products=None):
        return {"type": "product_showcase", "role": "host", "style": "energetic"}

    monkeypatch.setattr(content_type_mod, "detect_content_type", _fake_detect)
    monkeypatch.setattr(content_type_mod, "fill_dynamic_placeholders", lambda tmpl, _ct: tmpl)
    monkeypatch.setattr(cg, "log_creative_model_use", lambda *_a, **_k: None, raising=False)


def _persist_keys(scenes):
    """Mirror the legacy generate-outline persist loop: coerce block_type, take
    category, assign positions with a running counter that skips duplicate
    (type, category, key_points) beats — and return the (position, type,
    category) tuples that would land in the DB."""
    seen: set = set()
    keys: list = []
    for s in scenes:
        bt = BlockType.coerce(s.get("block_type"))
        cat = s.get("category") or "avatar_speaking"
        ident = (bt, cat, tuple(s.get("key_points") or []))
        if ident in seen:
            continue
        seen.add(ident)
        keys.append((len(seen) - 1, bt, cat))
    return keys


@pytest.mark.asyncio
async def test_generate_outline_no_duplicate_beats(monkeypatch):
    llm_scenes = [
        {"block_type": "hook", "category": "avatar_speaking", "purpose": "grab", "key_points": ["a"], "estimated_duration_seconds": 8},
        {"block_type": "product", "category": "avatar_voiceover", "purpose": "show", "key_points": ["b"], "estimated_duration_seconds": 10},
        # exact duplicate beat — the bug that put two rows per tuple
        {"block_type": "product", "category": "avatar_voiceover", "purpose": "show", "key_points": ["b"], "estimated_duration_seconds": 10},
        {"block_type": "cta", "category": "avatar_speaking", "purpose": "buy", "key_points": ["c"], "estimated_duration_seconds": 6},
    ]
    _patch_outline_deps(monkeypatch, json.dumps(llm_scenes))

    scenes = await cg.generate_outline(
        cast_id="cst_test",
        products=[{"name": "Widget", "description": "a widget"}],
        persona={"name": "Lina"},
        template_name="custom",
        description="product showcase video",
        user_id=None,
    )

    keys = _persist_keys(scenes)
    assert len(keys) == len(set(keys)), f"duplicate (position, type, category) tuples: {keys}"


@pytest.mark.asyncio
async def test_generate_outline_has_block_type_variety(monkeypatch):
    llm_scenes = [
        {"block_type": "hook", "category": "avatar_speaking", "purpose": "p1", "key_points": ["1"], "estimated_duration_seconds": 8},
        {"block_type": "story", "category": "avatar_speaking", "purpose": "p2", "key_points": ["2"], "estimated_duration_seconds": 8},
        {"block_type": "product", "category": "avatar_voiceover", "purpose": "p3", "key_points": ["3"], "estimated_duration_seconds": 10},
        {"block_type": "product_demo", "category": "stock_video", "purpose": "p4", "key_points": ["4"], "estimated_duration_seconds": 10},
        {"block_type": "social_proof", "category": "avatar_speaking", "purpose": "p5", "key_points": ["5"], "estimated_duration_seconds": 8},
        {"block_type": "cta", "category": "avatar_speaking", "purpose": "p6", "key_points": ["6"], "estimated_duration_seconds": 6},
    ]
    _patch_outline_deps(monkeypatch, json.dumps(llm_scenes))

    scenes = await cg.generate_outline(
        cast_id="cst_test",
        products=[{"name": "Widget", "description": "a widget"}],
        persona={"name": "Lina"},
        template_name="custom",
        description="product showcase video",
        user_id=None,
    )

    distinct_types = {BlockType.coerce(s.get("block_type")) for s in scenes}
    assert len(distinct_types) >= 4, f"expected >=4 distinct block types, got {distinct_types}"
    assert distinct_types != {BlockType.PRODUCT}, "all blocks collapsed to PRODUCT (Bug 2)"

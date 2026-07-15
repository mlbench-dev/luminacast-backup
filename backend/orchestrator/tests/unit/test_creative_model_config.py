"""Step 9 — all creative generation runs on Claude Opus 4.8.

Locks the model-slug wiring without hitting the network or a DB:

* the central ``services.creative_models`` constants default to the verified
  Opus 4.8 OpenRouter slug and are env-overridable;
* every CREATIVE call site references those constants (not a hardcoded slug);
* NON-creative call sites (content-type classifier, persona analyzer,
  clothing-consistency *judge*, camera-angle classifier, review generator)
  are deliberately NOT switched and keep their own cheaper models;
* the cost tracker prices the new slug at Opus rates (so ``/admin/costs``
  bills correctly instead of silently falling back to Sonnet).

DB-free by design: the CI "Backend Tests" unit job runs ``tests/unit/``
without a Postgres service. Source-text assertions are used for the call
sites that live inside DB/network code paths so we don't have to import or
execute them.
"""
import importlib
import inspect
import os
from pathlib import Path

import pytest

import services.creative_models as creative_models

EXPECTED_SLUG = "anthropic/claude-opus-4.8"
ORCH_ROOT = Path(__file__).resolve().parents[2]


def _read(rel_path: str) -> str:
    return (ORCH_ROOT / rel_path).read_text()


# ── 1. Central constants default to Opus 4.8 and are env-overridable ──────


def test_defaults_to_opus_when_env_unset(monkeypatch):
    monkeypatch.delenv("CAST_GENERATOR_MODEL", raising=False)
    monkeypatch.delenv("CREATIVE_DESCRIPTION_MODEL", raising=False)
    reloaded = importlib.reload(creative_models)
    try:
        assert reloaded.CAST_GENERATOR_MODEL == EXPECTED_SLUG
        assert reloaded.CREATIVE_DESCRIPTION_MODEL == EXPECTED_SLUG
    finally:
        importlib.reload(creative_models)


def test_env_overrides_constants(monkeypatch):
    monkeypatch.setenv("CAST_GENERATOR_MODEL", "anthropic/some-other-model")
    monkeypatch.setenv("CREATIVE_DESCRIPTION_MODEL", "anthropic/another-model")
    reloaded = importlib.reload(creative_models)
    try:
        assert reloaded.CAST_GENERATOR_MODEL == "anthropic/some-other-model"
        assert reloaded.CREATIVE_DESCRIPTION_MODEL == "anthropic/another-model"
    finally:
        # Restore the unset-env default for the rest of the suite.
        monkeypatch.delenv("CAST_GENERATOR_MODEL", raising=False)
        monkeypatch.delenv("CREATIVE_DESCRIPTION_MODEL", raising=False)
        importlib.reload(creative_models)


# ── 1b. Outline max_tokens knobs: default 8192, env-overridable ───────────
#
# Step 9 hotfix: Opus 4.8 is more verbose than the prior Sonnet default, so
# its outline JSON was truncated at the old hardcoded max_tokens=2048 and
# json.loads failed (the self-correction retry hit the same wall). The new
# helpers read os.environ at call time, so an override applies without a
# module reload.


def test_outline_max_tokens_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("OUTLINE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("OUTLINE_SELF_CORRECTION_MAX_TOKENS", raising=False)
    assert creative_models.get_outline_max_tokens() == 8192
    assert creative_models.get_outline_self_correction_max_tokens() == 8192


def test_outline_max_tokens_env_overrides(monkeypatch):
    monkeypatch.setenv("OUTLINE_MAX_TOKENS", "16000")
    monkeypatch.setenv("OUTLINE_SELF_CORRECTION_MAX_TOKENS", "12000")
    assert creative_models.get_outline_max_tokens() == 16000
    assert creative_models.get_outline_self_correction_max_tokens() == 12000


def test_outline_call_sites_use_helpers_not_hardcoded_2048():
    # The outline / self-correction / block-split creative call sites must
    # route max_tokens through the env-overridable helpers, and the old
    # hardcoded 2048 ceiling must be gone from this module.
    src = _read("engine/cast_generator.py")
    assert "get_outline_max_tokens" in src
    assert "get_outline_self_correction_max_tokens" in src
    assert "max_tokens=get_outline_max_tokens()" in src
    assert "max_tokens=get_outline_self_correction_max_tokens()" in src
    assert "max_tokens=2048" not in src


# ── 2. Creative call sites reference the central constants ────────────────


def test_cast_generator_uses_central_constant():
    src = _read("engine/cast_generator.py")
    # Imported from the single source of truth, not redefined locally.
    assert "from services.creative_models import" in src
    assert "CAST_GENERATOR_MODEL," in src
    assert 'CAST_GENERATOR_MODEL = ' not in src
    # The previously-hardcoded self-correction slug is gone.
    assert "anthropic/claude-sonnet-4" not in src
    # All generate_text model= calls route through the constant.
    assert 'model="anthropic/' not in src


def test_openrouter_describe_image_default_is_creative_constant():
    import services.openrouter as orouter

    sig = inspect.signature(orouter.OpenRouterService.describe_image)
    assert sig.parameters["model"].default == creative_models.CREATIVE_DESCRIPTION_MODEL
    assert sig.parameters["model"].default == EXPECTED_SLUG


def test_creative_router_and_service_sites_switched():
    # avatar.py: description rewrite, body description, audience + identity
    # generation, and the two wardrobe-extraction describe_image callers all
    # pass CREATIVE_DESCRIPTION_MODEL now (no hardcoded creative slug).
    avatar_src = _read("routers/avatar.py")
    assert avatar_src.count("CREATIVE_DESCRIPTION_MODEL") >= 6

    clone_src = _read("routers/clone_pipeline.py")
    assert "vision_model = CREATIVE_DESCRIPTION_MODEL" in clone_src
    assert '"anthropic/claude-sonnet-4.6"' not in clone_src

    inspire_src = _read("services/inspire_me_service.py")
    assert "anthropic/claude-sonnet-4" not in inspire_src
    assert inspire_src.count("CREATIVE_DESCRIPTION_MODEL") >= 2


# ── 3. Non-creative call sites are NOT switched ───────────────────────────


def test_content_type_classifier_not_switched():
    # The cheap content-type classifier lives in services/content_type.py and
    # must never read the creative constants.
    ct_src = _read("services/content_type.py")
    assert "CREATIVE_DESCRIPTION_MODEL" not in ct_src
    assert "CAST_GENERATOR_MODEL" not in ct_src


def test_persona_analyzer_stays_on_haiku():
    import services.openrouter as orouter

    src = inspect.getsource(orouter.OpenRouterService.analyze_persona)
    assert "anthropic/claude-3-haiku" in src
    assert "CREATIVE_DESCRIPTION_MODEL" not in src
    # generate_text default also stays on the cheap classifier model.
    gen_sig = inspect.signature(orouter.OpenRouterService.generate_text)
    assert gen_sig.parameters["model"].default == "anthropic/claude-3-haiku"


def test_consistency_judge_not_switched():
    # compare_two_images is a clothing-consistency JUDGE — must stay on its
    # own model, never the creative constant (global Step-9 rule).
    import services.openrouter as orouter

    sig = inspect.signature(orouter.OpenRouterService.compare_two_images)
    assert sig.parameters["model"].default == "anthropic/claude-sonnet-4"

    avatar_src = _read("routers/avatar.py")
    # The camera-angle classifier + consistency-check sites keep sonnet-4.
    assert "Classify the camera angle" in avatar_src
    assert "anthropic/claude-sonnet-4" in avatar_src


def test_review_generator_stays_on_haiku():
    products_src = _read("routers/products.py")
    assert '_REVIEW_MODEL = "anthropic/claude-3-haiku"' in products_src
    assert "CREATIVE_DESCRIPTION_MODEL" not in products_src


# ── 4. Cost tracker prices Opus 4.8 correctly (no silent Sonnet fallback) ─


def test_cost_tracker_prices_opus_48():
    from services.cost_rates import COST_RATES
    from services.usage_tracker import calculate_llm_cost

    assert COST_RATES["openrouter/claude-opus-4.8"] == {
        "input_per_1m": 5.00,
        "output_per_1m": 25.00,
    }
    # 1M in + 1M out → $5 + $25 = $30, resolved by exact slug (not fallback).
    assert calculate_llm_cost(EXPECTED_SLUG, 1_000_000, 1_000_000) == 30.0
    # Alias variants resolve to the same Opus rate, not Sonnet's $18.
    assert calculate_llm_cost("anthropic/claude-opus-4-8", 1_000_000, 1_000_000) == 30.0

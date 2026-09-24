"""AI b-roll model registry — id encoding in broll_media_source, endpoint
resolution + fallback, and the String(20) length constraint.
"""
import pytest

from services.ai_broll_models import (
    AI_BROLL_MODELS,
    DEFAULT_MODEL_ID,
    parse_broll_source,
    endpoint_for,
    cost_for,
    public_list,
)


def test_stored_values_fit_the_column():
    # casts.broll_media_source is String(20).
    for m in AI_BROLL_MODELS:
        stored = f"ai_generated:{m['id']}"
        assert len(stored) <= 20, (stored, len(stored))


@pytest.mark.parametrize("value,expected_ai,expected_id", [
    ("stock", False, DEFAULT_MODEL_ID),
    (None, False, DEFAULT_MODEL_ID),
    ("", False, DEFAULT_MODEL_ID),
    ("ai_generated", True, DEFAULT_MODEL_ID),
    ("ai_generated:veo3", True, "veo3"),
    ("ai_generated:veo3f", True, "veo3f"),
    ("ai_generated:k15", True, "k15"),
    ("ai_generated:garbage", True, DEFAULT_MODEL_ID),   # unknown → default
    ("ai_generated:", True, DEFAULT_MODEL_ID),
])
def test_parse_broll_source(value, expected_ai, expected_id):
    is_ai, mid = parse_broll_source(value)
    assert is_ai is expected_ai
    assert mid == expected_id


def test_every_model_resolves_both_modes():
    for m in AI_BROLL_MODELS:
        for mode in ("t2v", "i2v"):
            ep = endpoint_for(m["id"], mode)
            assert ep and ep.startswith("fal-ai/")


def test_missing_t2v_falls_back_to_default():
    # k15 has no text-to-video endpoint.
    assert endpoint_for("k15", "t2v") == endpoint_for(DEFAULT_MODEL_ID, "t2v")
    # but its own i2v is used
    assert "v1.5/pro/image-to-video" in endpoint_for("k15", "i2v")


def test_unknown_id_uses_default_endpoints():
    assert endpoint_for("nope", "t2v") == endpoint_for(DEFAULT_MODEL_ID, "t2v")


def test_cost_for():
    assert cost_for("veo3") == 1.0
    assert cost_for("k16") == 0.20
    assert cost_for("unknown") == cost_for(DEFAULT_MODEL_ID)


def test_public_list_has_no_endpoints():
    for row in public_list():
        assert set(row) == {"id", "label", "rank", "cost_note", "blurb"}


def test_models_ordered_best_to_worst():
    ranks = [m["rank"] for m in AI_BROLL_MODELS]
    assert ranks == sorted(ranks)

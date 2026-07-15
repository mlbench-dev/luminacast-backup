"""regression-6 — product/review casts must emit a product display + a b-roll beat.

Covers ``engine.cast_generator._ensure_product_and_broll_beats``: the post-LLM
injection guarantee that a product/review cast (or any cast with a linked
product) carries at least one product-display beat (split_h half / product
card) AND at least one b-roll/demo beat, injecting them when the model omitted
them.

DB-free by design (the CI "Backend Tests" unit job runs ``tests/unit/`` WITHOUT
a Postgres service): the helper operates on plain outline scene dicts, so every
case is a pure call with no fixtures.
"""
from engine.cast_generator import (
    _ensure_product_and_broll_beats,
    _scene_is_broll,
    _scene_is_product_display,
)
from layouts.primitives import LayoutPrimitive


def _speaking(block_type: str) -> dict:
    return {
        "category": "avatar_speaking",
        "block_type": block_type,
        "pip_layout": "fullscreen",
        "estimated_duration_seconds": 8,
    }


# ── (a) product cast with no product/broll beats gets beats injected ─────────

def test_product_cast_missing_beats_gets_injection():
    scenes = [_speaking("hook"), _speaking("cta")]
    out = _ensure_product_and_broll_beats(
        scenes, {"type": "product_showcase"}, [{"name": "GlowSerum"}], "cst_a"
    )

    # Two beats injected → original two plus product + broll.
    assert len(out) == 4
    assert any(_scene_is_product_display(s) for s in out)
    assert any(_scene_is_broll(s) for s in out)

    # Injected beats reuse the Step-5 split_h primitive — no invented layouts.
    injected = [s for s in out if s.get("injected")]
    assert {s["injected"] for s in injected} == {"product_display", "broll"}
    for s in injected:
        assert s["pip_layout"] == LayoutPrimitive.SPLIT_H.value
        # Each injected beat carries a query so auto_populate can resolve a clip.
        assert s.get("stock_media_query")

    # Bookends preserved: hook stays first, cta stays last.
    assert out[0]["block_type"] == "hook"
    assert out[-1]["block_type"] == "cta"


# ── (b) product cast already satisfying both conditions is NOT modified ──────

def test_product_cast_with_existing_beats_is_idempotent():
    scenes = [
        _speaking("hook"),
        {
            "category": "pip_talking_head",
            "block_type": "product",
            "content_role": "product",
            "pip_layout": "split_h",
            "estimated_duration_seconds": 6,
        },
        {
            "category": "stock_video",
            "block_type": "transition",
            "pip_layout": "fullscreen",
            "estimated_duration_seconds": 5,
        },
        _speaking("cta"),
    ]
    before = [dict(s) for s in scenes]
    out = _ensure_product_and_broll_beats(
        scenes, {"type": "product_showcase"}, [{"name": "GlowSerum"}], "cst_b"
    )

    assert len(out) == len(before)
    assert not any(s.get("injected") for s in out)
    assert out == before


# ── (c) a talking_head / non-product cast is untouched ───────────────────────

def test_non_product_cast_is_untouched():
    scenes = [_speaking("hook"), _speaking("story"), _speaking("cta")]
    before = [dict(s) for s in scenes]
    out = _ensure_product_and_broll_beats(
        scenes, {"type": "storytelling"}, [], "cst_c"
    )

    assert out == before
    assert not any(s.get("injected") for s in out)


# ── (d) linked product overrides a non-product content_type ──────────────────

def test_linked_product_forces_injection_even_for_story_vlog():
    scenes = [_speaking("hook"), _speaking("cta")]
    out = _ensure_product_and_broll_beats(
        scenes, {"type": "storytelling"}, [{"name": "Widget"}], "cst_d"
    )

    # A linked product makes this a product cast regardless of content_type.
    assert len(out) == 4
    assert any(_scene_is_product_display(s) for s in out)
    assert any(_scene_is_broll(s) for s in out)


# ── guards: empty / malformed input never raises ─────────────────────────────

def test_empty_scenes_returns_unchanged():
    assert _ensure_product_and_broll_beats([], {"type": "product_showcase"}, [], "cst_e") == []


def test_none_content_type_with_no_products_is_untouched():
    scenes = [_speaking("hook")]
    out = _ensure_product_and_broll_beats(scenes, None, None, "cst_f")
    assert out == scenes
    assert not any(s.get("injected") for s in out)

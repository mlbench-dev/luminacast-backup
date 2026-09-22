"""PR E — live-cast outline bias + duplicate-position dedupe + product-asset video.

Covers three pure helpers in ``engine.cast_generator``:

  * ``_dedupe_outline_by_position`` — collapses the duplicate-position blocks the
    outline LLM emitted for cst_db7b2b7ef5ac (25 blocks / 14 positions) to one
    block per position, keeping the product-bearing sibling.
  * ``_enforce_live_ratios`` / ``get_live_category_ratios`` — biases a short-form
    live cast toward voiceover + b-roll, away from full avatar-speaking blocks.
  * ``inject_product_asset_video`` — inserts an uploaded-video block pointing at a
    real ProductAsset video when one exists.

DB-free by design (the CI "Backend Tests" unit job runs ``tests/unit/`` WITHOUT a
Postgres service): every helper operates on plain outline scene dicts.
"""
import os

import pytest

from engine.cast_generator import (
    _dedupe_outline_by_position,
    _enforce_live_ratios,
    get_live_category_ratios,
    inject_product_asset_video,
    _AVATAR_RATIO_CATEGORIES,
)


# ── Change 1: dedupe duplicate-position blocks ───────────────────────────────


def _scene(position, *, product=False, category="avatar_speaking"):
    s = {
        "position": position,
        "category": category,
        "estimated_duration_seconds": 6,
    }
    if product:
        s["product_id"] = "prod_abc"
        s["product_name"] = "GlowSerum"
    return s


def test_dedupe_reproduces_25_block_14_position_bug():
    """The cst_db7b2b7ef5ac scenario: 25 blocks across 14 positions, each
    position with 2-3 siblings where only one carries a product. After dedupe
    there must be exactly 14 blocks — one per position — and every kept block
    at a position that HAD a product sibling must be the product-bearing one."""
    scenes = []
    # 14 positions; positions 0..10 get a product sibling + a placeholder
    # (and a third placeholder on a few) to reach 25 total blocks.
    sibling_counts = [2, 2, 3, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1]
    assert sum(sibling_counts) == 23
    # Pad two positions with an extra placeholder to reach 25 blocks.
    sibling_counts[8] = 2
    sibling_counts[9] = 2
    assert sum(sibling_counts) == 25

    product_positions = set()
    for pos, count in enumerate(sibling_counts):
        for k in range(count):
            # First sibling at each of the first 11 positions is the product one.
            is_product = (k == 0 and pos <= 10)
            if is_product:
                product_positions.add(pos)
            scenes.append(_scene(pos, product=is_product))

    assert len(scenes) == 25

    out = _dedupe_outline_by_position(scenes, cast_id="cst_db7b2b7ef5ac")

    # Exactly one block per position.
    assert len(out) == 14
    positions = [s["position"] for s in out]
    assert positions == sorted(set(positions))
    assert len(set(positions)) == 14

    # At every position that had a product sibling, the kept block is the product one.
    by_pos = {s["position"]: s for s in out}
    for pos in product_positions:
        assert by_pos[pos].get("product_id") == "prod_abc", pos


def test_dedupe_prefers_product_even_when_placeholder_is_first():
    scenes = [
        _scene(0, product=False),  # placeholder first
        _scene(0, product=True),   # product sibling second
        _scene(1, product=False),
    ]
    out = _dedupe_outline_by_position(scenes, cast_id="cst_x")
    assert len(out) == 2
    assert out[0].get("product_id") == "prod_abc"


def test_dedupe_noop_when_positions_already_unique():
    scenes = [_scene(0), _scene(1), _scene(2)]
    out = _dedupe_outline_by_position(scenes, cast_id="cst_x")
    assert out is scenes  # untouched, same object


def test_dedupe_passes_through_blocks_without_position():
    scenes = [
        {"category": "avatar_speaking"},
        {"category": "stock_video"},
    ]
    out = _dedupe_outline_by_position(scenes, cast_id="cst_x")
    assert len(out) == 2


# ── Change 2: live ratio bias ────────────────────────────────────────────────


def test_get_live_category_ratios_defaults_sum_to_one():
    avatar, broll, uploaded = get_live_category_ratios()
    assert abs((avatar + broll + uploaded) - 1.0) < 1e-9
    # Default leans toward b-roll over avatar.
    assert broll > avatar


def test_get_live_category_ratios_renormalizes(monkeypatch):
    monkeypatch.setenv("LIVE_RATIO_AVATAR", "30")
    monkeypatch.setenv("LIVE_RATIO_BROLL", "40")
    monkeypatch.setenv("LIVE_RATIO_UPLOADED", "10")
    avatar, broll, uploaded = get_live_category_ratios()
    assert abs((avatar + broll + uploaded) - 1.0) < 1e-9
    assert abs(avatar - 0.375) < 1e-6   # 30 / 80
    assert abs(broll - 0.5) < 1e-6      # 40 / 80
    assert abs(uploaded - 0.125) < 1e-6  # 10 / 80


def test_enforce_live_ratios_demotes_surplus_avatar_blocks(monkeypatch):
    # Force a hard avatar cap so the math is deterministic: 20% of 10 = 2.
    monkeypatch.setenv("LIVE_RATIO_AVATAR", "0.2")
    monkeypatch.setenv("LIVE_RATIO_BROLL", "0.6")
    monkeypatch.setenv("LIVE_RATIO_UPLOADED", "0.2")

    scenes = [{"category": "avatar_speaking", "estimated_duration_seconds": 6} for _ in range(10)]
    out = _enforce_live_ratios(scenes, duration_target_seconds=60, cast_id="cst_x")

    avatar_blocks = [s for s in out if s["category"] in _AVATAR_RATIO_CATEGORIES]
    # Cap is round(0.2 * 10) = 2.
    assert len(avatar_blocks) == 2
    # Demoted blocks became talking-head PIP: the script survives as active
    # narration while the avatar keeps speaking in a corner PIP window over
    # the block's b-roll (shared demotion helper — see its comment above
    # _enforce_live_ratios in engine/cast_generator.py).
    demoted = [s for s in out if s.get("ratio_demoted")]
    assert len(demoted) == 8
    for s in demoted:
        assert s["category"] == "pip_talking_head"
        assert s["background_type"] == "stock_video"
    # Block count unchanged.
    assert len(out) == 10


def test_enforce_live_ratios_protects_hook_and_cta(monkeypatch):
    monkeypatch.setenv("LIVE_RATIO_AVATAR", "0.0")  # cap floors to 1
    monkeypatch.setenv("LIVE_RATIO_BROLL", "0.8")
    monkeypatch.setenv("LIVE_RATIO_UPLOADED", "0.2")

    scenes = [{"category": "avatar_speaking", "estimated_duration_seconds": 6} for _ in range(5)]
    out = _enforce_live_ratios(scenes, duration_target_seconds=45, cast_id="cst_x")
    # First (hook) and last (CTA) stay avatar-on-camera.
    assert out[0]["category"] == "avatar_speaking"
    assert out[-1]["category"] == "avatar_speaking"


def test_enforce_live_ratios_noop_for_long_form(monkeypatch):
    monkeypatch.setenv("LIVE_RATIO_AVATAR", "0.1")
    scenes = [{"category": "avatar_speaking", "estimated_duration_seconds": 30} for _ in range(6)]
    out = _enforce_live_ratios(scenes, duration_target_seconds=180, cast_id="cst_x")
    # Long-form (>60s default) is untouched.
    assert all(s["category"] == "avatar_speaking" for s in out)


# ── Change 3: product-asset video preference ─────────────────────────────────


def _asset(asset_id="pa_1", url="https://r2/clip.mp4", duration=8.0):
    return {
        "id": asset_id,
        "product_id": "prod_abc",
        "product_name": "GlowSerum",
        "url": url,
        "thumbnail": "thumb",
        "duration_seconds": duration,
        "width": 1080,
        "height": 1920,
    }


def test_inject_product_asset_video_adds_uploaded_video_block():
    scenes = [
        {"category": "avatar_speaking", "block_type": "hook"},
        {"category": "avatar_voiceover", "block_type": "context"},
        {"category": "avatar_speaking", "block_type": "cta"},
    ]
    out = inject_product_asset_video(scenes, [_asset()], cast_id="cst_x")

    assert len(out) == 4
    injected = [s for s in out if s.get("injected") == "product_video"]
    assert len(injected) == 1
    blk = injected[0]
    # The block points at the REAL product footage, not Pexels.
    assert blk["stock_media_url"] == "https://r2/clip.mp4"
    assert blk["stock_media_kind"] == "video"
    assert blk["stock_media_source"] == "product_asset"
    assert blk["product_asset_id"] == "pa_1"
    assert blk["category"] == "stock_video"
    # Lands in the body, not over the hook/CTA bookends.
    assert out[0]["block_type"] == "hook"
    assert out[-1]["block_type"] == "cta"


def test_inject_product_asset_video_noop_without_assets():
    scenes = [{"category": "avatar_speaking"}]
    out = inject_product_asset_video(scenes, [], cast_id="cst_x")
    assert out is scenes


def test_inject_product_asset_video_skips_assets_without_url():
    scenes = [{"category": "avatar_speaking"}, {"category": "avatar_voiceover"}]
    out = inject_product_asset_video(scenes, [_asset(url="")], cast_id="cst_x")
    assert len(out) == 2
    assert not any(s.get("injected") == "product_video" for s in out)


def test_inject_product_asset_video_is_idempotent():
    scenes = [
        {"category": "avatar_speaking"},
        {"category": "avatar_voiceover"},
    ]
    once = inject_product_asset_video(scenes, [_asset()], cast_id="cst_x")
    twice = inject_product_asset_video(once, [_asset()], cast_id="cst_x")
    # Only one product-asset block, even after a second pass.
    assert sum(1 for s in twice if s.get("stock_media_source") == "product_asset") == 1


def test_inject_clamps_duration_to_sane_range():
    scenes = [{"category": "avatar_speaking"}, {"category": "avatar_voiceover"}]
    out = inject_product_asset_video(scenes, [_asset(duration=120.0)], cast_id="cst_x")
    blk = next(s for s in out if s.get("injected") == "product_video")
    assert blk["estimated_duration_seconds"] == 15  # clamped from 120

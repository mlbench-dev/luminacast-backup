"""PR C — product-relevant Pexels queries + multi-angle template.

Covers three behaviours added so a product cast stops pulling brand/location
b-roll (a "Sony storefront" / "Stockholm train") and instead searches for the
actual PRODUCT:

  (a) ``build_pexels_query`` returns a specific→generic ladder built from the
      product name + feature words, with a lifestyle close-up fallback;
  (b) ``_select_video_candidate`` tries the candidate queries in order until
      one returns clips (so a specific phrasing wins, generic still fills);
  (c) ``auto_populate_stock_media`` attaches a multi-angle (close-up +
      hands-using) jump-cut pair to a long product block when the template is
      enabled, and a single clip otherwise.

DB-free: ``engine.cast_generator`` imports without Postgres, and the Pexels
client + OpenRouter call are mocked, so this runs in the CI unit job.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from engine.cast_generator import (
    _extract_feature_words,
    _product_for_block,
    _select_video_candidate,
    auto_populate_stock_media,
    build_pexels_query,
)


# ── (a) build_pexels_query: specific → generic ladder ─────────────────────


def test_build_query_blends_product_name_and_features():
    product = {"name": "Sony WH-1000XM5", "category": "headphones"}
    block = {"key_points": ["noise cancellation", "wireless music"]}
    queries = build_pexels_query(product, block)

    # Specific first (product name + feature words), category mid, lifestyle last.
    assert queries[0].startswith("Sony WH-1000XM5")
    assert "noise" in queries[0]
    assert queries[1].startswith("headphones")
    assert queries[-1] == "headphones lifestyle close-up"
    assert 2 <= len(queries) <= 3


def test_build_query_never_leaves_brand_token_standing_alone():
    # No category, generic features → the fallback must not be a bare brand.
    product = {"name": "Sony"}
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    assert all(q.lower() != "sony" for q in queries)
    # Fallback collapses an all-brand subject to a neutral "product".
    assert queries[-1] == "product lifestyle close-up"


def test_build_query_infers_category_from_name_tail():
    product = {"name": "Acme Wireless Earbuds"}
    block = {"key_points": ["deep bass"]}
    queries = build_pexels_query(product, block)
    # "earbuds" is the product-shaped tail token, not the brand "Acme".
    assert queries[-1] == "earbuds lifestyle close-up"


def test_build_query_dedupes_and_caps_at_three():
    product = {"name": "Widget", "category": "widget"}
    block = {"key_points": []}
    queries = build_pexels_query(product, block)
    assert len(queries) == len(set(q.lower() for q in queries))
    assert len(queries) <= 3


def test_extract_feature_words_drops_brand_and_filler():
    block = {"key_points": ["The best Sony noise cancellation"], "purpose": "wireless"}
    words = _extract_feature_words(block)
    assert "sony" not in words
    assert "the" not in words
    assert "noise" in words and "cancellation" in words


def test_product_for_block_matches_by_name_then_falls_to_first():
    products = [{"name": "Alpha"}, {"name": "Beta"}]
    assert _product_for_block({"product_name": "Beta"}, products)["name"] == "Beta"
    assert _product_for_block({}, products)["name"] == "Alpha"
    assert _product_for_block({}, []) is None


# ── (b) candidate queries tried specific → generic until one yields ───────


class _SequencedClient:
    """Returns videos only for queries whose text is in ``hits``."""

    def __init__(self, hits: set[str]):
        self.hits = hits
        self.queries: list[str] = []

    async def safe_search_videos(self, query, per_page=3, orientation="portrait", **kw):
        self.queries.append(query)
        if query in self.hits:
            return {"videos": [{"id": 1, "url": f"https://pexels.com/video/{query}/",
                                "width": 1080, "height": 1920, "duration": 8,
                                "image": "https://img/x.jpg",
                                "user": {"name": ""}}]}
        return {"videos": []}


@pytest.mark.asyncio
async def test_select_prefers_specific_query(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    client = _SequencedClient(hits={"specific", "generic"})
    best = await _select_video_candidate(
        client, ["specific", "mid", "generic"], "beat", "portrait", "c", 0,
    )
    # First query already hits → we stop there.
    assert client.queries == ["specific"]
    assert best["url"].endswith("specific/")


@pytest.mark.asyncio
async def test_select_falls_through_to_generic(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    client = _SequencedClient(hits={"generic"})
    best = await _select_video_candidate(
        client, ["specific", "mid", "generic"], "beat", "portrait", "c", 0,
    )
    assert client.queries == ["specific", "mid", "generic"]
    assert best["url"].endswith("generic/")


@pytest.mark.asyncio
async def test_select_returns_none_when_no_query_hits(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    client = _SequencedClient(hits=set())
    best = await _select_video_candidate(
        client, ["a", "b"], "beat", "portrait", "c", 0,
    )
    assert best is None


# ── (c) multi-angle template on a long product block ──────────────────────


class _MultiAngleClient:
    """Every query returns a distinct video keyed by the query string."""

    def __init__(self):
        self.queries: list[str] = []

    async def safe_search_videos(self, query, per_page=3, orientation="portrait", **kw):
        self.queries.append(query)
        vid_id = abs(hash(query)) % 100000
        return {"videos": [{
            "id": vid_id,
            "url": f"https://pexels.com/video/{vid_id}/",
            "width": 1080, "height": 1920, "duration": 8,
            "image": f"https://img/{vid_id}.jpg",
            "user": {"name": ""},
            "video_files": [{"link": f"https://player/{vid_id}.mp4",
                             "file_type": "video/mp4",
                             "width": 1080, "height": 1920}],
        }]}


def _fake_pexels_module(client):
    """Patch services.pexels used inside auto_populate_stock_media."""
    def pick_best_video_file(video):
        return (video.get("video_files") or [{}])[0]
    mod = AsyncMock()
    mod.get_pexels_client_optional = lambda: client
    mod.pick_best_video_file = pick_best_video_file
    return mod


@pytest.mark.asyncio
async def test_multi_angle_attaches_two_clips_on_long_block(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "1")
    monkeypatch.setenv("MULTI_ANGLE_MIN_BLOCK_SEC", "4")
    client = _MultiAngleClient()
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 8,
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    block = out[0]
    assert block.get("multi_angle") is True
    assert len(block["parallel_media"]) == 2
    urls = {c["url"] for c in block["parallel_media"]}
    assert len(urls) == 2
    # Both angle variants were searched.
    assert any("close-up" in q for q in client.queries)
    assert any("hands using" in q for q in client.queries)


@pytest.mark.asyncio
async def test_multi_angle_disabled_keeps_single_clip(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "0")
    client = _MultiAngleClient()
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 8,
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    block = out[0]
    assert not block.get("multi_angle")
    assert len(block["parallel_media"]) == 1


@pytest.mark.asyncio
async def test_multi_angle_skipped_on_short_block(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    monkeypatch.setenv("MULTI_ANGLE_TEMPLATE_ENABLED", "1")
    monkeypatch.setenv("MULTI_ANGLE_MIN_BLOCK_SEC", "4")
    client = _MultiAngleClient()
    outline = [{
        "category": "avatar_speaking",
        "stock_media_query": "headphones demo",
        "estimated_duration_seconds": 3,  # below the 4s threshold
    }]
    products = [{"name": "Sony WH-1000XM5", "category": "headphones"}]

    with patch.dict(
        "sys.modules",
        {"services.pexels": _fake_pexels_module(client)},
    ):
        out = await auto_populate_stock_media(outline, "cast1", products=products)

    assert not out[0].get("multi_angle")
    assert len(out[0]["parallel_media"]) == 1

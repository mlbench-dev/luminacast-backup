"""Unit tests for the Fix 1 default-background fallback logic.

The cast serializer in routers.casts._get_cast computes a per-block
``default_background_url`` with this preference order:
    1. block.stock_media_thumbnail
    2. block.stock_media_url
    3. block.parallel_media[0].thumbnail / .url
    4. avatar.face_ref_key resolved to public URL

This test mirrors that logic in a tiny standalone function so it can
run without importing the heavy router module.
"""
from __future__ import annotations


def _resolve_default_bg(block: dict, avatar_profile_url: str | None) -> str | None:
    """Pure-Python mirror of the router's fallback chain."""
    if block.get("stock_media_thumbnail"):
        return block["stock_media_thumbnail"]
    if block.get("stock_media_url"):
        return block["stock_media_url"]
    pm = block.get("parallel_media") or []
    if pm and isinstance(pm[0], dict):
        return pm[0].get("thumbnail") or pm[0].get("url") or avatar_profile_url
    return avatar_profile_url


def test_no_explicit_bg_falls_back_to_avatar_profile():
    block = {}
    avatar_url = "https://cdn.test/avatars/avt_x/face.jpg"
    assert _resolve_default_bg(block, avatar_url) == avatar_url


def test_stock_media_url_wins_over_avatar():
    block = {"stock_media_url": "https://pexels.test/x.mp4"}
    avatar_url = "https://cdn.test/face.jpg"
    assert _resolve_default_bg(block, avatar_url) == "https://pexels.test/x.mp4"


def test_stock_media_thumbnail_wins_over_url():
    block = {
        "stock_media_url": "https://pexels.test/x.mp4",
        "stock_media_thumbnail": "https://pexels.test/x_thumb.jpg",
    }
    avatar_url = "https://cdn.test/face.jpg"
    assert _resolve_default_bg(block, avatar_url) == "https://pexels.test/x_thumb.jpg"


def test_parallel_media_first_item_wins_over_avatar():
    block = {
        "parallel_media": [
            {"thumbnail": "https://pexels.test/pm.jpg", "url": "https://pexels.test/pm.mp4"},
        ],
    }
    avatar_url = "https://cdn.test/face.jpg"
    assert _resolve_default_bg(block, avatar_url) == "https://pexels.test/pm.jpg"


def test_no_bg_and_no_avatar_returns_none():
    assert _resolve_default_bg({}, None) is None

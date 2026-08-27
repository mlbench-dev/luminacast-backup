"""Regression test for ``tasks.cast_render.resolve_voiceover_visual_source``.

Bug: voiceover blocks (narration over B-roll, no face track) resolved their
visual source from block.video_asset_id / the effective product's registered
ProductAsset / block.scene_image_key only. Auto-generated, stock-footage-driven
casts have none of those populated — the script engine attaches its per-beat
pick to ``block.parallel_media`` instead — so every such block fell through to
the avatar-idle Ken-Burns fallback: the narration played over the avatar's
face instead of the picked B-roll, even though the editor preview (which DOES
read parallel_media) showed the correct clip. Confirmed against a real cast
where every voiceover block had NULL video_asset_id/image_asset_id/product_id/
scene_image_key and no cast_products row, but a populated parallel_media list.

This test asserts parallel_media is now consulted (and wins over the
avatar-idle fallback) when nothing more specific is registered.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

from tasks.cast_render import resolve_voiceover_visual_source


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeBlock:
    video_asset_id = None
    image_asset_id = None
    product_id = None
    scene_image_key = None
    stock_media_url = None
    stock_media_kind = None

    def __init__(self, parallel_media=None):
        self.parallel_media = parallel_media


def test_parallel_media_video_used_when_nothing_else_registered(monkeypatch):
    block = _FakeBlock(parallel_media=[
        {"kind": "video", "url": "https://videos.pexels.com/a.mp4",
         "start_offset_s": 0.0, "duration_s": 4.0},
        {"kind": "video", "url": "https://videos.pexels.com/b.mp4",
         "start_offset_s": 4.0, "duration_s": 4.0},
    ])
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value=None),
    )

    result = _run(resolve_voiceover_visual_source(
        "blk_test", session, r2, "cst_test",
    ))
    assert result == ("video", "https://videos.pexels.com/a.mp4"), (
        "must resolve the first parallel_media video entry, not fall through "
        "to the avatar-idle fallback (result was %r)" % (result,)
    )


def test_explicit_block_asset_still_wins_over_parallel_media(monkeypatch):
    """Priority order: an explicit block.video_asset_id must still beat
    parallel_media — parallel_media only fills in when nothing more specific
    is registered."""
    block = _FakeBlock(parallel_media=[
        {"kind": "video", "url": "https://videos.pexels.com/a.mp4"},
    ])
    block.video_asset_id = "pa_explicit"

    class _Asset:
        r2_key = "products/explicit.mp4"

    async def _fake_get(model, obj_id):
        if obj_id == "pa_explicit":
            return _Asset()
        return block

    session = types.SimpleNamespace(get=_fake_get)
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value=None),
    )

    result = _run(resolve_voiceover_visual_source(
        "blk_test", session, r2, "cst_test",
    ))
    assert result == ("video", "https://cdn/products/explicit.mp4")


def test_stock_media_url_used_for_stock_photo_video_blocks(monkeypatch):
    """stock_photo / stock_video blocks carry their pick in stock_media_url
    (not parallel_media). Confirmed against a real cast: block category
    'stock_photo' had stock_media_url populated (a Pexels photo) but
    parallel_media NULL — without this check the block fell through to the
    avatar-idle fallback exactly like the parallel_media case."""
    block = _FakeBlock()
    block.stock_media_url = "https://images.pexels.com/photos/1475396/cart.jpeg"
    block.stock_media_kind = "photo"
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value=None),
    )

    result = _run(resolve_voiceover_visual_source(
        "blk_test", session, r2, "cst_test",
    ))
    assert result == (
        "image", "https://images.pexels.com/photos/1475396/cart.jpeg",
    )


def test_stock_media_url_video_kind_returns_video(monkeypatch):
    block = _FakeBlock()
    block.stock_media_url = "https://videos.pexels.com/clip.mp4"
    block.stock_media_kind = "video"
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value=None),
    )

    result = _run(resolve_voiceover_visual_source(
        "blk_test", session, r2, "cst_test",
    ))
    assert result == ("video", "https://videos.pexels.com/clip.mp4")


def test_parallel_media_wins_over_stock_media_url(monkeypatch):
    """Priority: parallel_media (per-beat AI pick) is checked before the
    coarser stock_media_url fallback."""
    block = _FakeBlock(parallel_media=[
        {"kind": "video", "url": "https://videos.pexels.com/pm.mp4"},
    ])
    block.stock_media_url = "https://images.pexels.com/should-not-win.jpeg"
    block.stock_media_kind = "photo"
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value=None),
    )

    result = _run(resolve_voiceover_visual_source(
        "blk_test", session, r2, "cst_test",
    ))
    assert result == ("video", "https://videos.pexels.com/pm.mp4")


def test_product_own_media_outranks_generic_stock(monkeypatch):
    """When a product resolves for the block, its OWN uploaded gallery
    assets must be tried before the AI-picked stock (parallel_media) and
    the coarse stock_media_url — a "promote the Galaxy S26" cast should
    show the real S26 photos, not a random Pexels phone. Regression for the
    old order where product media sat at the bottom and was never reached."""
    from tasks.cast_render import resolve_voiceover_visual_sources

    block = _FakeBlock(parallel_media=[
        {"kind": "video", "url": "https://videos.pexels.com/random-phone.mp4"},
    ])
    block.stock_media_url = "https://images.pexels.com/photos/vivo.jpeg"
    block.stock_media_kind = "photo"
    block.product_id = "prod_s26"
    block.position = 0

    class _GalleryAsset:
        def __init__(self, asset_id, media_type, key):
            self.id = asset_id
            self.media_type = media_type
            self.r2_key = key
            self.position = 0
            self.created_at = asset_id

    gallery = [
        _GalleryAsset("pa_1", "image", "products/prod_s26/assets/pa_1.jpg"),
        _GalleryAsset("pa_2", "image", "products/prod_s26/assets/pa_2.jpg"),
    ]

    class _Prod:
        cover_image_key = "products/covers/s26.jpg"

    class _Result:
        def scalars(self):
            return types.SimpleNamespace(all=lambda: gallery)

    async def _fake_get(model, obj_id):
        if getattr(model, "__name__", "") == "Product":
            return _Prod()
        return block

    async def _fake_execute(_query):
        return _Result()

    session = types.SimpleNamespace(get=_fake_get, execute=_fake_execute)
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value="prod_s26"),
    )

    result = _run(resolve_voiceover_visual_sources(
        "blk_test", session, r2, "cst_test",
    ))
    # First two candidates are the product's own gallery photos, then the
    # cover, and only then the generic Pexels stock.
    assert result[0] == ("image", "https://cdn/products/prod_s26/assets/pa_1.jpg")
    assert result[1] == ("image", "https://cdn/products/prod_s26/assets/pa_2.jpg")
    assert result[2] == ("image", "https://cdn/products/covers/s26.jpg")
    assert ("video", "https://videos.pexels.com/random-phone.mp4") in result
    assert result.index(("video", "https://videos.pexels.com/random-phone.mp4")) > 2


def test_product_media_rotates_by_block_position(monkeypatch):
    """Consecutive product beats must not all open on the same photo — the
    resolved gallery list is rotated by block.position."""
    from tasks.cast_render import resolve_voiceover_visual_sources

    class _GalleryAsset:
        def __init__(self, asset_id, key):
            self.id = asset_id
            self.media_type = "image"
            self.r2_key = key
            self.position = 0
            self.created_at = asset_id

    gallery = [
        _GalleryAsset("pa_1", "k1.jpg"),
        _GalleryAsset("pa_2", "k2.jpg"),
        _GalleryAsset("pa_3", "k3.jpg"),
    ]

    class _Result:
        def scalars(self):
            return types.SimpleNamespace(all=lambda: gallery)

    async def _fake_execute(_query):
        return _Result()

    class _Prod:
        cover_image_key = ""

    async def _fake_get(model, obj_id):
        if getattr(model, "__name__", "") == "Product":
            return _Prod()
        return _fake_get.block

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value="prod_x"),
    )
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    b0 = _FakeBlock()
    b0.product_id = "prod_x"
    b0.position = 0
    _fake_get.block = b0
    session = types.SimpleNamespace(get=_fake_get, execute=_fake_execute)
    r0 = _run(resolve_voiceover_visual_sources("blk0", session, r2, "cst"))

    b1 = _FakeBlock()
    b1.product_id = "prod_x"
    b1.position = 1
    _fake_get.block = b1
    r1 = _run(resolve_voiceover_visual_sources("blk1", session, r2, "cst"))

    assert r0[0] == ("image", "https://cdn/k1.jpg")
    assert r1[0] == ("image", "https://cdn/k2.jpg")


def test_returns_none_when_nothing_resolves(monkeypatch):
    block = _FakeBlock(parallel_media=None)
    session = types.SimpleNamespace(get=AsyncMock(return_value=block))
    r2 = types.SimpleNamespace(get_public_url=lambda key: f"https://cdn/{key}")

    import tasks.cast_render as cr
    monkeypatch.setattr(
        cr, "resolve_effective_product_id", AsyncMock(return_value=None),
    )

    result = _run(resolve_voiceover_visual_source(
        "blk_test", session, r2, "cst_test",
    ))
    assert result is None


# ─── Static-code assertions: category -> render_mode routing ────────────────
#
# Bug: stock_photo / stock_video blocks (pure B-roll, no avatar face meant to
# appear) were missing from every category -> render_mode mapping, so they
# fell through to the "avatar_full" default and got baked as a full-frame
# TALKING AVATAR while their assigned stock_media_url only ever showed as a
# small overlay on top. Confirmed against a real cast: block category
# 'stock_photo' had render_mode='avatar_full' in the DB despite a populated
# stock_media_url. Fixed in three places: the two script-generation mapping
# blocks in routers/casts.py, and a dispatch-time override in
# tasks/cast_render.py that re-derives render_mode from the live DB category
# so ALREADY-persisted blocks (like the one above) self-heal on next render
# without needing a script regeneration or migration.

import re as _re
from pathlib import Path as _Path

_ORCH_ROOT = _Path(__file__).resolve().parents[2]
_CASTS_ROUTER_PATH = _ORCH_ROOT / "routers" / "casts.py"


def test_casts_router_maps_stock_categories_to_voiceover():
    src = _CASTS_ROUTER_PATH.read_text(encoding="utf-8")
    # Both script-generation mapping blocks must route stock_photo/stock_video
    # to the voiceover (B-roll) bake path, not the avatar_full default.
    matches = _re.findall(
        r'elif (?:scene_)?category in \("stock_photo", "stock_video"\):\s*\n'
        r'(?:[^\n]*\n)*?\s*render_mode = "voiceover"',
        src,
    )
    assert len(matches) >= 2, (
        "expected both script-generation category->render_mode blocks to "
        "map stock_photo/stock_video to voiceover"
    )


def test_dispatch_overrides_stale_avatar_full_for_stock_categories():
    src = _CASTS_ROUTER_PATH.read_text(encoding="utf-8")
    cast_render_src = (_ORCH_ROOT / "tasks" / "cast_render.py").read_text(
        encoding="utf-8",
    )
    assert '"stock_photo", "stock_video"' in cast_render_src, (
        "dispatch must re-check the block's live category and override a "
        "stale avatar_full render_mode for stock_photo/stock_video blocks"
    )
    assert 'block_render_mode = "voiceover"' in cast_render_src

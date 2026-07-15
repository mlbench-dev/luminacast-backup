"""Unit tests for generate_cast._inject_product_overlays_for_blocks (PR-G Fix 3).

The user complaint was "where is the product itself?" — PRODUCT/PRODUCT_DEMO
blocks often render with no product on screen because the timeline snapshot
carried no product element. These tests pin the guarantee that such blocks get
a product overlay injected from the product's video-asset poster or
cover_image_key, gated by PRODUCT_OVERLAY_ENABLED, and that the injected
overlay sits in the middle of the block for at least 2 seconds.

Lightweight fakes for the Product / ProductAsset ORM rows and the async DB
session — no DB / network / ffmpeg needed.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest

import tasks.generate_cast as gc


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Block:
    def __init__(self, bid, btype, product_id=None):
        self.id = bid
        self.type = btype
        self.product_id = product_id


class _Product:
    def __init__(self, pid, *, cover_image_key="", title="Widget", price=29.99, current_price=0):
        self.id = pid
        self.cover_image_key = cover_image_key
        self.title = title
        self.name = title
        self.price = price
        self.current_price = current_price


class _Asset:
    def __init__(self, thumbnail_r2_key="", position=0):
        self.thumbnail_r2_key = thumbnail_r2_key
        self.position = position


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class _FakeDB:
    """Async session stub: execute() yields the preset asset; get() the product."""

    def __init__(self, product, asset=None):
        self._product = product
        self._asset = asset

    async def execute(self, *_a, **_k):
        return _ScalarResult(self._asset)

    async def get(self, _model, _id):
        return self._product


class _FakeR2:
    def get_public_url(self, key):
        return f"https://media.luminacast.com/{key}" if key else ""


def _regions(block_ids, dur=8.0):
    out = []
    cursor = 0.0
    for i, bid in enumerate(block_ids):
        out.append({"block_id": bid, "start_s": cursor, "end_s": cursor + dur, "index": i})
        cursor += dur
    return out


def test_injects_cover_image_for_bare_product_block():
    block = _Block("blk_1", "PRODUCT", product_id="prd_1")
    product = _Product("prd_1", cover_image_key="covers/prd_1.jpg")
    db = _FakeDB(product, asset=None)
    overlays: dict = {}

    with patch.dict(os.environ, {"PRODUCT_OVERLAY_ENABLED": "1"}), patch("tasks.cast_render.resolve_effective_product_id", new=_async_return("prd_1")):
        _run(gc._inject_product_overlays_for_blocks(
            [block], _regions(["blk_1"], dur=8.0), overlays, db, _FakeR2(), "cast_1",
        ))

    assert 0 in overlays
    ov = overlays[0][0]
    assert ov["src"].endswith("covers/prd_1.jpg")
    assert ov["injected"] is True
    # >= 2s hold, centred in the 8s block.
    assert ov["end_s"] - ov["start_s"] >= 2.0
    assert ov["start_s"] > 0


def test_prefers_video_asset_poster_when_present():
    block = _Block("blk_1", "PRODUCT_DEMO", product_id="prd_1")
    product = _Product("prd_1", cover_image_key="covers/prd_1.jpg")
    asset = _Asset(thumbnail_r2_key="thumbs/video_poster.jpg")
    db = _FakeDB(product, asset=asset)
    overlays: dict = {}

    with patch.dict(os.environ, {"PRODUCT_OVERLAY_ENABLED": "1"}), patch("tasks.cast_render.resolve_effective_product_id", new=_async_return("prd_1")):
        _run(gc._inject_product_overlays_for_blocks(
            [block], _regions(["blk_1"]), overlays, db, _FakeR2(), "cast_1",
        ))

    assert overlays[0][0]["src"].endswith("thumbs/video_poster.jpg")


def test_skips_non_product_blocks():
    block = _Block("blk_1", "INTRO", product_id="prd_1")
    db = _FakeDB(_Product("prd_1", cover_image_key="covers/prd_1.jpg"))
    overlays: dict = {}

    with patch.dict(os.environ, {"PRODUCT_OVERLAY_ENABLED": "1"}), patch("tasks.cast_render.resolve_effective_product_id", new=_async_return("prd_1")):
        _run(gc._inject_product_overlays_for_blocks(
            [block], _regions(["blk_1"]), overlays, db, _FakeR2(), "cast_1",
        ))

    assert overlays == {}


def test_disabled_via_env_injects_nothing():
    block = _Block("blk_1", "PRODUCT", product_id="prd_1")
    db = _FakeDB(_Product("prd_1", cover_image_key="covers/prd_1.jpg"))
    overlays: dict = {}

    with patch.dict(os.environ, {"PRODUCT_OVERLAY_ENABLED": "0"}), patch("tasks.cast_render.resolve_effective_product_id", new=_async_return("prd_1")):
        _run(gc._inject_product_overlays_for_blocks(
            [block], _regions(["blk_1"]), overlays, db, _FakeR2(), "cast_1",
        ))

    assert overlays == {}


def test_existing_overlay_is_not_overwritten():
    block = _Block("blk_1", "PRODUCT", product_id="prd_1")
    db = _FakeDB(_Product("prd_1", cover_image_key="covers/prd_1.jpg"))
    overlays = {0: [{"product_id": "prd_1", "src": "https://x/orig.jpg"}]}

    with patch.dict(os.environ, {"PRODUCT_OVERLAY_ENABLED": "1"}), patch("tasks.cast_render.resolve_effective_product_id", new=_async_return("prd_1")):
        _run(gc._inject_product_overlays_for_blocks(
            [block], _regions(["blk_1"]), overlays, db, _FakeR2(), "cast_1",
        ))

    assert len(overlays[0]) == 1
    assert overlays[0][0]["src"] == "https://x/orig.jpg"


def _async_return(value):
    async def _inner(*_a, **_k):
        return value
    return _inner

"""Unit tests for services.video_compositor.ensure_product_cover_on_r2 (Phase 4).

These tests pin the defect #5 fix: the overlay cover must resolve to the
product's OWN first image asset (position-ordered) — never a trending-product
proxy and never a media_keys scan. They use lightweight fakes for the Product
ORM row and the async DB session, so no DB / network / ffmpeg is needed.
"""
from __future__ import annotations

import asyncio

import pytest

from services.video_compositor import ensure_product_cover_on_r2


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeProduct:
    def __init__(self, pid, *, cover_image_key="", tiktok_product_id="tt_1"):
        self.id = pid
        self.cover_image_key = cover_image_key
        self.tiktok_product_id = tiktok_product_id


class _FakeAsset:
    def __init__(self, aid, product_id, r2_key, position=0):
        self.id = aid
        self.product_id = product_id
        self.r2_key = r2_key
        self.position = position


class _FakeDB:
    """Async session stub whose ``scalar`` returns a preset asset."""

    def __init__(self, asset):
        self._asset = asset

    async def scalar(self, *_args, **_kwargs):
        return self._asset


def test_cached_cover_key_short_circuits():
    """A product already carrying a cover_image_key returns it without a DB hit."""
    product = _FakeProduct("prd_1", cover_image_key="covers/prd_1.jpg")
    key = _run(
        ensure_product_cover_on_r2(
            product,
            db=None,
            block_id="blk_1",
            cast_linked_product_ids=["prd_1"],
        )
    )
    assert key == "covers/prd_1.jpg"


def test_resolves_first_image_asset():
    """With no cached key, the product's own first image asset is used."""
    product = _FakeProduct("prd_2", cover_image_key="")
    asset = _FakeAsset("pa_1", "prd_2", "assets/pa_1.jpg", position=0)
    db = _FakeDB(asset)
    key = _run(
        ensure_product_cover_on_r2(
            product,
            db=db,
            block_id="blk_2",
            cast_linked_product_ids=["prd_2"],
        )
    )
    assert key == "assets/pa_1.jpg"
    # Resolved key is cached back onto the product row.
    assert product.cover_image_key == "assets/pa_1.jpg"


def test_product_not_linked_to_cast_returns_none():
    """The belongs-to-cast guard refuses a product the cast isn't selling."""
    product = _FakeProduct("prd_rogue", cover_image_key="covers/rogue.jpg")
    key = _run(
        ensure_product_cover_on_r2(
            product,
            db=None,
            block_id="blk_3",
            cast_linked_product_ids=["prd_a", "prd_b"],
        )
    )
    assert key is None


def test_no_asset_returns_none():
    """No image asset → product_asset_unresolved → None (no silent fallback)."""
    product = _FakeProduct("prd_4", cover_image_key="")
    db = _FakeDB(None)
    key = _run(
        ensure_product_cover_on_r2(
            product,
            db=db,
            block_id="blk_4",
            cast_linked_product_ids=["prd_4"],
        )
    )
    assert key is None


def test_resolved_asset_must_belong_to_product():
    """A mismatched asset.product_id trips the hard assert (fail closed)."""
    product = _FakeProduct("prd_5", cover_image_key="")
    # Asset belongs to a DIFFERENT product — the in-query filter would never
    # return this in prod, but if it did we must fail rather than overlay it.
    asset = _FakeAsset("pa_x", "prd_other", "assets/pa_x.jpg", position=0)
    db = _FakeDB(asset)
    with pytest.raises(AssertionError):
        _run(
            ensure_product_cover_on_r2(
                product,
                db=db,
                block_id="blk_5",
                cast_linked_product_ids=["prd_5"],
            )
        )


def test_no_cast_filter_still_resolves():
    """When cast_linked_product_ids is None the guard is skipped (back-compat)."""
    product = _FakeProduct("prd_6", cover_image_key="covers/prd_6.jpg")
    key = _run(
        ensure_product_cover_on_r2(product, db=None, block_id="blk_6")
    )
    assert key == "covers/prd_6.jpg"

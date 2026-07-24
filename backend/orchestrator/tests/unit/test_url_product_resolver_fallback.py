"""TikTok Shop URL resolver fallback chain.

TikTok Shop PDP pages sit behind a hard CAPTCHA that blocks every Apify
engine we have. The resolver tries an ordered fallback chain and, when all
engines return empty, raises TikTokBlockedError carrying the product id it
could extract from the URL so the caller can offer a manual-entry path.
"""
import re
from unittest.mock import AsyncMock

import httpx
import pytest

from services.url_product_resolver import (
    TikTokBlockedError,
    _canonical_tiktok_url,
    _extract_tiktok_product_id,
    _resolve_tiktok,
    _TIKTOK_FALLBACK_ACTORS,
)

BLOCKED_URL = (
    "https://shop.tiktok.com/gb/pdp/1729774361469163960"
    "?source=ecommerce_category&foo=bar"
)
PRODUCT_ID = "1729774361469163960"
CANONICAL_URL = f"https://www.tiktok.com/view/product/{PRODUCT_ID}"


def _empty_response() -> httpx.Response:
    return httpx.Response(200, json=[])


def _item_response(item: dict) -> httpx.Response:
    return httpx.Response(200, json=[item])


def test_extract_product_id_from_pdp_url():
    assert _extract_tiktok_product_id(BLOCKED_URL) == "1729774361469163960"


def test_extract_product_id_returns_none_without_id():
    assert _extract_tiktok_product_id("https://shop.tiktok.com/gb/pdp/") is None


@pytest.mark.asyncio
async def test_resolve_tiktok_raises_blocked_when_all_engines_empty(monkeypatch):
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _empty_response()

    with pytest.raises(TikTokBlockedError) as exc_info:
        await _resolve_tiktok(BLOCKED_URL, client)

    assert exc_info.value.source_product_id == "1729774361469163960"
    assert exc_info.value.source_url == BLOCKED_URL
    # No engine name leaks into the user-facing exception message.
    assert "actor" not in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_fallback_chain_tries_every_engine_in_order(monkeypatch):
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _empty_response()

    with pytest.raises(TikTokBlockedError):
        await _resolve_tiktok(BLOCKED_URL, client)

    # One POST per fallback engine, in declared order.
    assert client.post.call_count == len(_TIKTOK_FALLBACK_ACTORS)
    called_urls = [c.args[0] for c in client.post.call_args_list]
    for (actor_id, _), called_url in zip(_TIKTOK_FALLBACK_ACTORS, called_urls):
        assert actor_id in called_url


@pytest.mark.asyncio
async def test_fallback_stops_at_first_engine_with_items(monkeypatch):
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    # First engine empty, second returns a real product, third never tried.
    client.post.side_effect = [
        _empty_response(),
        _item_response({
            "id": "1729774361469163960",
            "title": "Test Product",
            "price": 12.5,
            "currency": "GBP",
        }),
    ]

    resolved = await _resolve_tiktok(BLOCKED_URL, client)

    assert resolved.source == "tiktok"
    assert resolved.title == "Test Product"
    assert resolved.price == 12.5
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_blocked_message_has_no_engine_names(monkeypatch):
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _empty_response()
    with pytest.raises(TikTokBlockedError) as exc_info:
        await _resolve_tiktok(BLOCKED_URL, client)
    msg = str(exc_info.value).lower()
    for banned in ("actor", "apify", "pro100chok", "pratikdani", "cunning_soil"):
        assert banned not in msg


def test_canonical_url_built_from_product_id():
    assert _canonical_tiktok_url(PRODUCT_ID) == CANONICAL_URL


@pytest.mark.asyncio
async def test_regional_url_normalized_to_canonical_before_lookup(monkeypatch):
    """A regional /gb/pdp/<id> URL must reach the engine as the canonical form."""
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _item_response({"product_name": "X", "price": "$1.00"})

    await _resolve_tiktok(BLOCKED_URL, client)

    # First engine is the verified pratikdani actor; it must be called with
    # {"url": "<canonical>"}, not the regional PDP URL. The /gb/ segment in
    # the original URL must route the proxy to a GB residential exit IP.
    first_call = client.post.call_args_list[0]
    assert first_call.kwargs["json"] == {
        "url": CANONICAL_URL,
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "GB",
        },
    }


@pytest.mark.asyncio
async def test_pratikdani_response_field_mapping(monkeypatch):
    """The primary engine's response maps onto ResolvedProduct fields."""
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _item_response({
        "product_name": "Glow Serum",
        "price": "$10.57",
        "original_price": "$14.99",
        "currency": "GBP",
        "product_desc": "A radiant serum.",
        "images": [
            "https://cdn.tiktok.com/cover.jpg",
            "https://cdn.tiktok.com/gallery1.jpg",
        ],
        "commission": "10%",
        "category": "Beauty",
        "seller": {"name": "GlowCo"},
        "sale_cnt": 4231,
        "product_id": "9999999999999999",
    })

    resolved = await _resolve_tiktok(BLOCKED_URL, client)

    assert resolved.source == "tiktok"
    assert resolved.title == "Glow Serum"
    assert resolved.price == 10.57
    assert resolved.original_price == 14.99
    assert resolved.currency == "GBP"
    assert resolved.description == "A radiant serum."
    assert resolved.cover_image_url == "https://cdn.tiktok.com/cover.jpg"
    assert "https://cdn.tiktok.com/gallery1.jpg" in resolved.media_urls
    assert resolved.commission_rate == pytest.approx(0.10)
    assert resolved.category == "Beauty"
    assert resolved.seller_name == "GlowCo"
    # Product id is taken from the URL the user pasted, not the engine echo.
    assert resolved.source_product_id == PRODUCT_ID
    # Original affiliate URL is preserved, not the canonical lookup URL.
    assert resolved.source_url == BLOCKED_URL


@pytest.mark.asyncio
async def test_seller_dict_extracts_name(monkeypatch):
    monkeypatch.setattr(
        "services.url_product_resolver.settings.APIFY_API_TOKEN", "tok", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _item_response({
        "product_name": "Y",
        "price": "$2.00",
        "seller": {"seller_name": "AcmeShop"},
    })
    resolved = await _resolve_tiktok(BLOCKED_URL, client)
    assert resolved.seller_name == "AcmeShop"

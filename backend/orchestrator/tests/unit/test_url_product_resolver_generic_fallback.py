"""Generic-site resolver: Apify fallback for bot-protected storefronts.

A plain server-side GET to sites like SHEIN gets served a generic
homepage/challenge page instead of the real product page — no og:image, no
JSON-LD — because their bot detection blocks non-browser requests. The
resolver now retries through Apify's website-content-crawler (a real
headless browser behind Apify's proxy pool) whenever the direct fetch comes
back with no product image, and reuses the same JSON-LD/OG extraction on
whatever HTML it renders.
"""
from unittest.mock import AsyncMock

import httpx
import pytest

import services.url_product_resolver as resolver
from services.url_product_resolver import _resolve_generic

PRODUCT_URL = "https://www.shein.com/some-product-p-123456.html"

BLOCKED_HTML = "<html><head><title>SHEIN</title></head><body>verify you are human</body></html>"

REAL_HTML = """
<html><head>
<meta property="og:title" content="Oval Metal Frame Glasses">
<meta property="og:image" content="https://img.shein.com/glasses.jpg">
<meta property="og:description" content="Fashion sunglasses">
<meta property="product:price:amount" content="0.70">
</head><body></body></html>
"""


def _html_response(html: str) -> httpx.Response:
    return httpx.Response(200, text=html, request=httpx.Request("GET", PRODUCT_URL))


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(resolver.settings, "APIFY_API_TOKEN", "tok", raising=False)


@pytest.mark.asyncio
async def test_direct_fetch_with_image_skips_apify():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _html_response(REAL_HTML)

    resolved = await _resolve_generic(PRODUCT_URL, client)

    assert resolved.cover_image_url == "https://img.shein.com/glasses.jpg"
    assert resolved.title == "Oval Metal Frame Glasses"
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_direct_fetch_falls_back_to_apify_rendered_html():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _html_response(BLOCKED_HTML)
    client.post.return_value = httpx.Response(200, json=[{"html": REAL_HTML}])

    resolved = await _resolve_generic(PRODUCT_URL, client)

    assert resolved.cover_image_url == "https://img.shein.com/glasses.jpg"
    assert resolved.title == "Oval Metal Frame Glasses"
    client.post.assert_called_once()
    call = client.post.call_args
    assert "website-content-crawler" in call.args[0]
    assert call.kwargs["json"]["startUrls"] == [{"url": PRODUCT_URL}]


@pytest.mark.asyncio
async def test_blocked_fetch_without_apify_token_raises_blocked_error(monkeypatch):
    # Both the direct fetch AND the Apify fallback fail to find an image —
    # the resolver must raise rather than silently create an empty product
    # (see routers/products.py's GenericSiteBlockedError -> needs_manual_entry
    # handling, which gives the caller a real reason instead of a blank result).
    monkeypatch.setattr(resolver.settings, "APIFY_API_TOKEN", "", raising=False)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _html_response(BLOCKED_HTML)

    with pytest.raises(resolver.GenericSiteBlockedError) as exc_info:
        await _resolve_generic(PRODUCT_URL, client)

    assert exc_info.value.source_url == PRODUCT_URL
    client.post.assert_not_called()


@pytest.mark.asyncio
async def test_apify_actor_not_found_falls_back_gracefully(monkeypatch):
    captured = []
    monkeypatch.setattr(resolver.sentry_sdk, "capture_exception", lambda e: captured.append(e))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _html_response(BLOCKED_HTML)
    client.post.return_value = httpx.Response(404, json={"error": "not found"})

    with pytest.raises(resolver.GenericSiteBlockedError):
        await _resolve_generic(PRODUCT_URL, client)

    assert any(isinstance(e, resolver.ApifyActorNotFoundError) for e in captured)


@pytest.mark.asyncio
async def test_apify_renders_but_still_has_no_image_raises_blocked_error():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _html_response(BLOCKED_HTML)
    client.post.return_value = httpx.Response(200, json=[{"html": BLOCKED_HTML}])

    with pytest.raises(resolver.GenericSiteBlockedError):
        await _resolve_generic(PRODUCT_URL, client)


@pytest.mark.asyncio
async def test_custom_actor_id_from_settings_is_used(monkeypatch):
    monkeypatch.setattr(resolver.settings, "GENERIC_RESOLVER_ACTOR_ID", "someowner/custom-crawler", raising=False)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _html_response(BLOCKED_HTML)
    client.post.return_value = httpx.Response(200, json=[{"html": REAL_HTML}])

    await _resolve_generic(PRODUCT_URL, client)

    call = client.post.call_args
    assert "someowner~custom-crawler" in call.args[0]

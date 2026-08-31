"""Region-matched residential proxy routing for TikTok and Amazon imports.

Root cause of repeated "importing products by URL fails again" reports: (1)
Apify scraper actors get renamed/removed out from under us, and (2) several
actors run US-only exit IPs, so region-locked storefronts (TikTok Shop
/gb/pdp/..., amazon.co.uk) get region-blocked and fall back to manual entry.

The fix has three parts, each covered here:
  * country is parsed from the product URL itself (TikTok region segment,
    Amazon marketplace TLD)
  * every Apify run is routed through a residential proxy whose exit IP
    matches that country (falls back to an unmatched exit IP when the
    country can't be determined)
  * the proxy group, country-matching toggle, and the Amazon actor id are
    all settings — not hardcoded — so the next rename is a config change
"""
from unittest.mock import AsyncMock

import httpx
import pytest

import services.url_product_resolver as resolver
from services.url_product_resolver import (
    ApifyActorNotFoundError,
    _build_proxy_configuration,
    _extract_amazon_country,
    _extract_tiktok_region,
    _load_amazon_actor_id,
    _resolve_amazon,
)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(resolver.settings, "APIFY_API_TOKEN", "tok", raising=False)


# ---------------------------------------------------------------------------
# _extract_tiktok_region
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://shop.tiktok.com/gb/pdp/1729774361469163960?x=1", "GB"),
        ("https://shop.tiktok.com/us/pdp/1729774361469163960", "US"),
        ("https://shop.tiktok.com/sg/pdp/1729774361469163960", "SG"),
        # Canonical lookup URL carries no region segment.
        ("https://www.tiktok.com/view/product/1729774361469163960", None),
        # Unrecognized two-letter segment isn't treated as a region guess.
        ("https://shop.tiktok.com/xx/pdp/1729774361469163960", None),
    ],
)
def test_extract_tiktok_region(url, expected):
    assert _extract_tiktok_region(url) == expected


# ---------------------------------------------------------------------------
# _extract_amazon_country
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.amazon.co.uk/dp/B08N5WRWNW", "GB"),
        ("https://www.amazon.com/dp/B08N5WRWNW", "US"),
        ("https://www.amazon.de/dp/B08N5WRWNW", "DE"),
        ("https://www.amazon.com.au/dp/B08N5WRWNW", "AU"),
        # Shortened / unrecognized domain defaults to the largest marketplace.
        ("https://amzn.to/abc123", "US"),
    ],
)
def test_extract_amazon_country(url, expected):
    assert _extract_amazon_country(url) == expected


# ---------------------------------------------------------------------------
# _build_proxy_configuration
# ---------------------------------------------------------------------------

def test_proxy_configuration_matches_country_by_default():
    config = _build_proxy_configuration("GB")
    assert config == {
        "useApifyProxy": True,
        "apifyProxyGroups": ["RESIDENTIAL"],
        "apifyProxyCountry": "GB",
    }


def test_proxy_configuration_omits_country_when_unresolved():
    config = _build_proxy_configuration(None)
    assert "apifyProxyCountry" not in config
    assert config["apifyProxyGroups"] == ["RESIDENTIAL"]


def test_proxy_configuration_respects_country_matching_toggle(monkeypatch):
    monkeypatch.setattr(resolver.settings, "APIFY_PROXY_COUNTRY_MATCHING", False, raising=False)
    config = _build_proxy_configuration("GB")
    assert "apifyProxyCountry" not in config


def test_proxy_configuration_uses_configured_groups(monkeypatch):
    monkeypatch.setattr(resolver.settings, "APIFY_PROXY_GROUPS", "DATACENTER,RESIDENTIAL", raising=False)
    config = _build_proxy_configuration("US")
    assert config["apifyProxyGroups"] == ["DATACENTER", "RESIDENTIAL"]


def test_proxy_configuration_omits_groups_when_unset(monkeypatch):
    monkeypatch.setattr(resolver.settings, "APIFY_PROXY_GROUPS", "", raising=False)
    config = _build_proxy_configuration("US")
    assert "apifyProxyGroups" not in config
    assert config["useApifyProxy"] is True


# ---------------------------------------------------------------------------
# _load_amazon_actor_id
# ---------------------------------------------------------------------------

def test_amazon_actor_id_defaults_when_unset(monkeypatch):
    monkeypatch.setattr(resolver.settings, "AMAZON_RESOLVER_ACTOR_ID", "", raising=False)
    assert _load_amazon_actor_id() == "junglee~amazon-crawler"


def test_amazon_actor_id_reads_env_override(monkeypatch):
    monkeypatch.setattr(resolver.settings, "AMAZON_RESOLVER_ACTOR_ID", "newowner/new-amazon-actor", raising=False)
    assert _load_amazon_actor_id() == "newowner~new-amazon-actor"


def test_amazon_actor_id_falls_back_on_malformed_value(monkeypatch, caplog):
    monkeypatch.setattr(resolver.settings, "AMAZON_RESOLVER_ACTOR_ID", "not-a-valid-id", raising=False)
    assert _load_amazon_actor_id() == "junglee~amazon-crawler"


# ---------------------------------------------------------------------------
# _resolve_amazon — proxy + actor id wired end to end
# ---------------------------------------------------------------------------

def _amazon_item_response(item: dict) -> httpx.Response:
    return httpx.Response(200, json=[item])


@pytest.mark.asyncio
async def test_resolve_amazon_routes_gb_proxy_for_couk_url():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _amazon_item_response({
        "title": "Kettle",
        "asin": "B08N5WRWNW",
        "price": {"value": 34.99, "currency": "£"},
    })

    resolved = await _resolve_amazon("https://www.amazon.co.uk/dp/B08N5WRWNW", client)

    assert resolved.currency == "GBP"
    call = client.post.call_args_list[0]
    assert call.kwargs["json"]["proxyConfiguration"] == {
        "useApifyProxy": True,
        "apifyProxyGroups": ["RESIDENTIAL"],
        "apifyProxyCountry": "GB",
    }


@pytest.mark.asyncio
async def test_resolve_amazon_uses_configured_actor_id(monkeypatch):
    monkeypatch.setattr(
        resolver.settings, "AMAZON_RESOLVER_ACTOR_ID", "newowner/new-amazon-actor", raising=False
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _amazon_item_response({"title": "X", "asin": "B000000000"})

    await _resolve_amazon("https://www.amazon.com/dp/B000000000", client)

    called_url = client.post.call_args_list[0].args[0]
    assert "newowner~new-amazon-actor" in called_url


@pytest.mark.asyncio
async def test_resolve_amazon_actor_not_found_captured(monkeypatch):
    captured = []
    monkeypatch.setattr(resolver.sentry_sdk, "capture_exception", lambda e: captured.append(e))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(404, json={"error": "not found"})

    # A 404 (actor renamed/removed) is still an ops problem — captured to
    # Sentry — but surfaced to the caller as AmazonBlockedError so the user
    # gets the manual-entry fallback instead of a raw 500.
    with pytest.raises(resolver.AmazonBlockedError) as excinfo:
        await _resolve_amazon("https://www.amazon.com/dp/B000000000", client)

    assert any(isinstance(e, ApifyActorNotFoundError) for e in captured)
    assert "apify" not in str(excinfo.value).lower()
    assert "actor" not in str(excinfo.value).lower()

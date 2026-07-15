"""TikTok resolver error-envelope rejection + transient retry.

Upstream TikTok actors sometimes return HTTP 200 with a body that is not a
product: pratikdani emits [{"error": "Issue in running the query..."}], and
the cunning_soil mobile-api emits an empty wrapper
[{"product_info": {"total_products": 0, "products": []}}]. Previously these
counted as one resolved item and produced a degenerate $0 "Imported Product".
These tests pin the rejection, the single transient retry, and the
cunning_soil unwrap.
"""
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

import services.url_product_resolver as resolver
from services.url_product_resolver import (
    TikTokBlockedError,
    _is_product_item,
    _run_tiktok_actor,
    _resolve_tiktok,
)

CANONICAL_URL = "https://www.tiktok.com/view/product/1729826285113612476"
BLOCKED_URL = "https://shop.tiktok.com/gb/pdp/1729826285113612476"
PRODUCT_ID = "1729826285113612476"

ERROR_ENVELOPE = {"error": "Issue in running the query, please try again later."}
CUNNING_EMPTY = {"product_info": {"total_products": 0, "products": []}}
CUNNING_NONEMPTY = {
    "product_info": {
        "total_products": 1,
        "products": [{"product_name": "Mobile Serum", "price": "$8.00"}],
    }
}


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Keep the 2s retry backoff from slowing the suite."""
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(resolver.asyncio, "sleep", _instant)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(
        resolver.settings, "APIFY_API_TOKEN", "tok", raising=False
    )


# ---------------------------------------------------------------------------
# _is_product_item
# ---------------------------------------------------------------------------

def test_is_product_item_rejects_error_only():
    assert _is_product_item(ERROR_ENVELOPE) is False


def test_is_product_item_allows_error_plus_product_name():
    assert _is_product_item({"error": "warn", "product_name": "X"}) is True


def test_is_product_item_allows_valid_product():
    assert _is_product_item({"product_name": "Serum", "price": "$1"}) is True


def test_is_product_item_allows_product_via_id_only():
    assert _is_product_item({"id": "123"}) is True


def test_is_product_item_rejects_cunning_empty_wrapper():
    assert _is_product_item(CUNNING_EMPTY) is False


def test_is_product_item_rejects_non_dict():
    assert _is_product_item("nope") is False
    assert _is_product_item(None) is False


# ---------------------------------------------------------------------------
# _run_tiktok_actor — error envelope triggers retry + run_error_envelope log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_envelope_triggers_retry_and_log(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    # First attempt error envelope, retry returns a real product.
    client.post.side_effect = [
        httpx.Response(200, json=[ERROR_ENVELOPE]),
        httpx.Response(200, json=[{"product_name": "Recovered", "price": "$3"}]),
    ]

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor(
            "owner~slug", {"url": CANONICAL_URL}, "tok", client
        )

    # The transient error envelope triggers exactly one retry, which recovers.
    # Only the final outcome (resolved) is logged for the call.
    assert client.post.call_count == 2
    assert len(items) == 1
    assert items[0]["product_name"] == "Recovered"
    rec = next(r for r in caplog.records if "resolved" in r.getMessage())
    assert "attempts=2" in rec.getMessage()


@pytest.mark.asyncio
async def test_persistent_error_envelope_returns_empty_with_attempts_two(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[ERROR_ENVELOPE])

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor(
            "owner~slug", {"url": CANONICAL_URL}, "tok", client
        )

    assert items == []
    assert client.post.call_count == 2
    assert "tiktok_resolver.run_error_envelope" in caplog.text
    rec = next(
        r for r in caplog.records if "run_error_envelope" in r.getMessage()
    )
    assert "attempts=2" in rec.getMessage()


@pytest.mark.asyncio
async def test_empty_list_not_retried(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[])

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor(
            "owner~slug", {"url": CANONICAL_URL}, "tok", client
        )

    assert items == []
    assert client.post.call_count == 1
    assert "tiktok_resolver.run_zero_items" in caplog.text


@pytest.mark.asyncio
async def test_cunning_empty_wrapper_not_retried_logs_error_envelope(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[CUNNING_EMPTY])

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor(
            "owner~slug", {"url": CANONICAL_URL}, "tok", client
        )

    assert items == []
    # Empty wrapper is not a transient error → no retry.
    assert client.post.call_count == 1
    assert "tiktok_resolver.run_error_envelope" in caplog.text
    assert "upstream_error=no_product_fields" in caplog.text


# ---------------------------------------------------------------------------
# _run_tiktok_actor — cunning_soil unwrap on a populated wrapper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cunning_nonempty_wrapper_unwrapped_to_product(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[CUNNING_NONEMPTY])

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor(
            "owner~slug", {"url": CANONICAL_URL}, "tok", client
        )

    assert client.post.call_count == 1
    assert len(items) == 1
    assert items[0]["product_name"] == "Mobile Serum"
    assert "tiktok_resolver.resolved" in caplog.text


# ---------------------------------------------------------------------------
# _resolve_tiktok — all actors return error envelopes / empties → blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_raises_blocked_when_all_actors_error(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    # pratikdani error envelope (x2 for the retry), pro100chok empty,
    # cunning_soil empty wrapper.
    client.post.side_effect = [
        httpx.Response(200, json=[ERROR_ENVELOPE]),  # attempt 1
        httpx.Response(200, json=[ERROR_ENVELOPE]),  # retry
        httpx.Response(200, json=[]),                # pro100chok
        httpx.Response(200, json=[CUNNING_EMPTY]),   # cunning_soil
    ]

    with pytest.raises(TikTokBlockedError) as exc_info:
        await _resolve_tiktok(BLOCKED_URL, client)

    assert exc_info.value.source_product_id == PRODUCT_ID
    assert exc_info.value.source_url == BLOCKED_URL


@pytest.mark.asyncio
async def test_resolve_recovers_via_cunning_unwrap(monkeypatch):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        httpx.Response(200, json=[ERROR_ENVELOPE]),  # pratikdani attempt 1
        httpx.Response(200, json=[ERROR_ENVELOPE]),  # pratikdani retry
        httpx.Response(200, json=[]),                # pro100chok empty
        httpx.Response(200, json=[CUNNING_NONEMPTY]),  # cunning_soil hit
    ]

    resolved = await _resolve_tiktok(BLOCKED_URL, client)

    assert resolved.source == "tiktok"
    assert resolved.title == "Mobile Serum"
    assert resolved.price == 8.0

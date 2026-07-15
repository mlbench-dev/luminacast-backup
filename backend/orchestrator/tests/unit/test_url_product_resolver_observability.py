"""TikTok resolver observability + env-driven actor catalog.

Covers the hardening added on top of the PR #174 functional fix:
  * _is_successful truth table
  * the three structured per-attempt outcomes (actor_not_found / run_unfinished
    / run_zero_items) plus the resolved success line
  * env-driven actor catalog parsing + default fallback on parse failure
  * the &memory= cost-cap query param reaching the upstream call
None of this changes user-facing behavior; it only adds logging + config.
"""
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

import services.url_product_resolver as resolver
from services.url_product_resolver import (
    ApifyActorNotFoundError,
    _is_successful,
    _parse_actor_catalog,
    _run_tiktok_actor,
    _resolve_tiktok,
)

CANONICAL_URL = "https://www.tiktok.com/view/product/1729774361469163960"
BLOCKED_URL = "https://shop.tiktok.com/gb/pdp/1729774361469163960?x=1"


# ---------------------------------------------------------------------------
# _is_successful truth table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,items,expected",
    [
        (200, [{"id": "1"}], True),    # 2xx + items
        (201, [{"id": "1"}], True),    # 2xx (created) + items
        (200, [], False),              # 2xx + empty
        (404, [{"id": "1"}], False),   # 4xx + items
        (500, [{"id": "1"}], False),   # 5xx + items
        (0, [], False),                # timeout sentinel (no http status)
    ],
)
def test_is_successful_truth_table(status, items, expected):
    assert _is_successful(status, items) is expected


# ---------------------------------------------------------------------------
# Structured per-attempt outcomes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_actor_not_found_logs_and_captures(monkeypatch, caplog):
    captured = []
    monkeypatch.setattr(resolver.sentry_sdk, "capture_exception", lambda e: captured.append(e))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(404, json={"error": "not found"})

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client)

    assert items == []
    assert "tiktok_resolver.actor_not_found" in caplog.text
    assert "engine=owner~slug" in caplog.text
    assert "status=404" in caplog.text
    assert any(isinstance(e, ApifyActorNotFoundError) for e in captured)
    rec = next(r for r in caplog.records if "actor_not_found" in r.getMessage())
    assert rec.levelno == logging.ERROR


@pytest.mark.asyncio
async def test_run_zero_items_logs_warning(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[])

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client)

    assert items == []
    assert "tiktok_resolver.run_zero_items" in caplog.text
    assert "engine=owner~slug" in caplog.text
    assert "item_count=0" in caplog.text
    rec = next(r for r in caplog.records if "run_zero_items" in r.getMessage())
    assert rec.levelno == logging.WARNING


@pytest.mark.asyncio
async def test_run_unfinished_on_5xx(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(503, json={"error": "busy"})

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client)

    assert items == []
    assert "tiktok_resolver.run_unfinished" in caplog.text
    assert "http_status=503" in caplog.text
    rec = next(r for r in caplog.records if "run_unfinished" in r.getMessage())
    assert rec.levelno == logging.WARNING


@pytest.mark.asyncio
async def test_run_unfinished_on_timeout(monkeypatch, caplog):
    captured = []
    monkeypatch.setattr(resolver.sentry_sdk, "capture_exception", lambda e: captured.append(e))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = httpx.ReadTimeout("timed out")

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client)

    assert items == []
    assert "tiktok_resolver.run_unfinished" in caplog.text
    assert "error_type=ReadTimeout" in caplog.text
    assert captured  # network error captured to Sentry


@pytest.mark.asyncio
async def test_run_unfinished_on_non_succeeded_run_object(caplog):
    # 2xx but the body is a run object whose status is not SUCCEEDED.
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(
        200, json={"data": {"id": "run123", "status": "FAILED"}}
    )

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client)

    assert items == []
    assert "tiktok_resolver.run_unfinished" in caplog.text
    assert "apify_status=FAILED" in caplog.text
    assert "run_id=run123" in caplog.text


@pytest.mark.asyncio
async def test_resolved_logs_info_with_run_id_from_header(caplog):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(
        200,
        json=[{"product_name": "X", "price": "$1.00"}],
        headers={"X-Apify-Run-Id": "runABC"},
    )

    with caplog.at_level(logging.INFO):
        items = await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client)

    assert len(items) == 1
    assert "tiktok_resolver.resolved" in caplog.text
    assert "engine=owner~slug" in caplog.text
    assert "run_id=runABC" in caplog.text
    assert "item_count=1" in caplog.text
    rec = next(r for r in caplog.records if "resolved" in r.getMessage())
    assert rec.levelno == logging.INFO


# ---------------------------------------------------------------------------
# Memory cost-cap query param
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_param_in_upstream_url():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[{"id": "1"}])

    await _run_tiktok_actor("owner~slug", {"url": CANONICAL_URL}, "tok", client, max_memory_mb=256)

    called_url = client.post.call_args.args[0]
    assert "memory=256" in called_url
    assert "timeout=120" in called_url


@pytest.mark.asyncio
async def test_memory_param_from_settings(monkeypatch):
    monkeypatch.setattr(resolver.settings, "APIFY_API_TOKEN", "tok", raising=False)
    monkeypatch.setattr(resolver.settings, "TIKTOK_RESOLVER_MAX_MEMORY_MB", 1024, raising=False)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = httpx.Response(200, json=[{"product_name": "Y", "price": "$2.00"}])

    await _resolve_tiktok(BLOCKED_URL, client)

    assert "memory=1024" in client.post.call_args_list[0].args[0]


# ---------------------------------------------------------------------------
# Env-driven catalog parsing
# ---------------------------------------------------------------------------

def test_parse_catalog_string_and_list_fields():
    chain = _parse_actor_catalog(
        "a/one:url,b/two:productUrls[],c/three:productInput"
    )
    assert [actor for actor, _ in chain] == ["a~one", "b~two", "c~three"]
    # string field
    assert chain[0][1]("U") == {"url": "U"}
    # list-wrapped field
    assert chain[1][1]("U") == {"productUrls": ["U"]}
    # plain string field
    assert chain[2][1]("U") == {"productInput": "U"}


def test_parse_catalog_rejects_malformed():
    with pytest.raises(ValueError):
        _parse_actor_catalog("noseparator")
    with pytest.raises(ValueError):
        _parse_actor_catalog("noslash:url")
    with pytest.raises(ValueError):
        _parse_actor_catalog("a/one:")


@pytest.mark.asyncio
async def test_env_catalog_changes_actor_chain(monkeypatch):
    monkeypatch.setattr(resolver.settings, "TIKTOK_RESOLVER_ACTORS", "custom/actor:myField", raising=False)
    chain = resolver._load_tiktok_actors()
    assert chain[0][0] == "custom~actor"
    assert chain[0][1]("U") == {"myField": "U"}
    assert len(chain) == 1


def test_load_catalog_falls_back_to_default_on_parse_failure(monkeypatch):
    monkeypatch.setattr(resolver.settings, "TIKTOK_RESOLVER_ACTORS", "totally-broken", raising=False)
    captured = []
    monkeypatch.setattr(resolver.sentry_sdk, "capture_exception", lambda e: captured.append(e))

    chain = resolver._load_tiktok_actors()

    assert chain == resolver._DEFAULT_TIKTOK_ACTORS
    assert len(captured) == 1


def test_load_catalog_uses_default_when_env_empty(monkeypatch):
    monkeypatch.setattr(resolver.settings, "TIKTOK_RESOLVER_ACTORS", "", raising=False)
    assert resolver._load_tiktok_actors() == resolver._DEFAULT_TIKTOK_ACTORS

"""POST /api/products/from-url — TikTok blocked graceful fallback.

When the resolver raises TikTokBlockedError the endpoint must answer 202
needs_manual_entry (not a 4xx) so the frontend can route the user into a
manual create form. Successful resolution still returns 201, and a truly
invalid (non-TikTok) URL still returns 400.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from main import app
from database import get_db
from routers.auth import get_current_user, get_workspace_context, WorkspaceContext
from services.url_product_resolver import (
    ResolvedProduct,
    TikTokBlockedError,
    AmazonBlockedError,
)

BLOCKED_URL = "https://shop.tiktok.com/gb/pdp/1729774361469163960?source=x"


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value


class _FakeSession:
    """Minimal async session: enough for the from-url 201 happy path.

    create_product / import_from_url call add/commit/refresh/execute(count).
    De-dup lookups (scalar) return None so a new product is always created.
    """

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)

    async def scalar(self, *a, **k):
        return None

    async def execute(self, *a, **k):
        # _product_to_response asset-count query -> row with .total/.videos/.images
        return _FakeResult(SimpleNamespace(total=0, videos=0, images=0))


def _override_user():
    return SimpleNamespace(id="usr_test_block", email="b@test.com", role="creator")


def _override_workspace_context():
    # /from-url is gated by require_role(CREATOR), which depends on
    # get_workspace_context — a separate dependency from get_current_user
    # that isn't satisfied by overriding get_current_user alone. Without
    # this override the real get_workspace_context runs against the fake
    # session and every request 403s before reaching the route body.
    return WorkspaceContext(
        workspace_owner_id="usr_test_block",
        actor_user_id="usr_test_block",
        actor_team_role=None,
        is_owner=True,
    )


def _make_client(session):
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_workspace_context] = _override_workspace_context
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _teardown():
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_from_url_returns_202_when_tiktok_blocked():
    session = _FakeSession()
    with patch(
        "services.url_product_resolver.resolve_product_url",
        new=AsyncMock(side_effect=TikTokBlockedError(BLOCKED_URL, "1729774361469163960")),
    ):
        async with _make_client(session) as ac:
            resp = await ac.post("/api/products/from-url", json={"url": BLOCKED_URL})
    _teardown()

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "needs_manual_entry"
    assert body["source"] == "tiktok"
    assert body["source_product_id"] == "1729774361469163960"
    assert body["source_url"] == BLOCKED_URL
    # User-facing message must not leak engine terminology.
    assert "actor" not in body["message"].lower()


@pytest.mark.asyncio
async def test_from_url_returns_202_when_amazon_blocked():
    """A failed / timed-out / region-locked Apify Amazon run must land the
    user in the same manual-entry fallback as TikTok — not a raw 400 with
    'Apify actor returned ...' in it."""
    session = _FakeSession()
    amazon_url = "https://www.amazon.com/dp/B0EXAMPLE"
    reason = (
        "Amazon didn't return this product. The listing may be region-locked, "
        "out of stock, or temporarily blocking automated lookups. Try a "
        "different link. You can still add this product by entering the "
        "details below."
    )
    with patch(
        "services.url_product_resolver.resolve_product_url",
        new=AsyncMock(side_effect=AmazonBlockedError(amazon_url, reason)),
    ):
        async with _make_client(session) as ac:
            resp = await ac.post("/api/products/from-url", json={"url": amazon_url})
    _teardown()

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "needs_manual_entry"
    assert body["source"] == "amazon"
    assert body["source_url"] == amazon_url
    assert body["message"] == reason
    # No engine terminology leaks to the user.
    assert "apify" not in body["message"].lower()
    assert "actor" not in body["message"].lower()


@pytest.mark.asyncio
async def test_from_url_returns_400_for_truly_invalid_url():
    session = _FakeSession()
    with patch(
        "services.url_product_resolver.resolve_product_url",
        new=AsyncMock(side_effect=ValueError("no product found")),
    ):
        async with _make_client(session) as ac:
            resp = await ac.post(
                "/api/products/from-url", json={"url": "https://example.com/x"}
            )
    _teardown()

    assert resp.status_code == 400
    assert "actor" not in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_from_url_returns_201_on_successful_resolution():
    session = _FakeSession()
    resolved = ResolvedProduct(
        source="tiktok",
        source_product_id="999",
        source_url="https://shop.tiktok.com/gb/pdp/999",
        title="Resolved Product",
        price=19.99,
        cover_image_url=None,
        media_urls=[],
        raw={},
    )
    with patch(
        "services.url_product_resolver.resolve_product_url",
        new=AsyncMock(return_value=resolved),
    ), patch(
        "routers.products.crud._materialize_product_assets", new=AsyncMock(return_value=0)
    ), patch("services.usage_tracker.log_usage", new=AsyncMock(return_value=None)):
        async with _make_client(session) as ac:
            resp = await ac.post(
                "/api/products/from-url",
                json={"url": "https://shop.tiktok.com/gb/pdp/999"},
            )
    _teardown()

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Resolved Product"
    assert body["already_existed"] is False

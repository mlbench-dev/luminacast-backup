"""Regression tests for _apply_zernio_post_refresh and the list-posts
opportunistic refresh.

Bug: the per-post refresh (GET /api/social/posts/{id}) read
raw.get("status") / raw.get("platformPostIds") directly off Zernio's
GET /v1/posts/{id} response — but like every other Zernio endpoint in this
app, the real payload nests everything under "post" (confirmed live: fetching
a post that Zernio itself reports as "published" returned top-level key
['post'] only). So the refresh endpoint that existed SPECIFICALLY to catch a
post up from "publishing" to "published" was silently a no-op the entire
time — status and platform post ids never actually updated.

On top of that, the Published tab's list view never called the refresh
endpoint at all (it only calls the plain list), so even a working per-post
refresh wouldn't have helped a post show up there. Fixed by unwrapping the
response correctly AND opportunistically refreshing any post still in a
non-terminal state ("publishing"/"scheduled") whenever the list is fetched.
"""
from __future__ import annotations

import asyncio
import sys
import types

if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

sys.path.insert(0, ".")
from routers.social import _apply_zernio_post_refresh


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakePost:
    def __init__(self, status="publishing", zernio_post_id="zp_1", published_at=None):
        self.status = status
        self.zernio_post_id = zernio_post_id
        self.published_at = published_at
        self.platform_post_ids = {}
        self.analytics = None


class _FakeSvc:
    async def get_post_analytics(self, post_id):
        return {"views": 42}


REAL_SHAPE_RESPONSE = {
    "post": {
        "_id": "6a70734ba4536666200263ee",
        "status": "published",
        "publishedAt": "2026-08-03T10:54:08.834Z",
        "platforms": [
            {
                "platform": "youtube",
                "status": "published",
                "platformPostId": "5RWRkFSBzDo",
                "platformPostUrl": "https://www.youtube.com/watch?v=5RWRkFSBzDo",
            },
        ],
    },
}


def test_refresh_unwraps_nested_post_and_updates_status():
    p = _FakePost(status="publishing")
    _run(_apply_zernio_post_refresh(_FakeSvc(), p, REAL_SHAPE_RESPONSE))
    assert p.status == "published"
    assert p.platform_post_ids == {"youtube": "5RWRkFSBzDo"}
    assert p.published_at is not None
    assert p.published_at.tzinfo is None, "must be naive to match the DB column"


def test_refresh_is_noop_safe_on_flat_response_shape():
    """If Zernio ever changes shape (or a caller passes something
    unexpected), this must not crash — just leave the post unchanged."""
    p = _FakePost(status="publishing")
    _run(_apply_zernio_post_refresh(_FakeSvc(), p, {"status": "published"}))
    assert p.status == "publishing", (
        "a flat/unrecognized shape must not be misread as a real update"
    )


def test_refresh_does_not_overwrite_existing_published_at():
    from datetime import datetime
    existing = datetime(2026, 1, 1, 0, 0, 0)
    p = _FakePost(status="published", published_at=existing)
    _run(_apply_zernio_post_refresh(_FakeSvc(), p, REAL_SHAPE_RESPONSE))
    assert p.published_at == existing


def test_list_posts_triggers_refresh_for_non_terminal_statuses():
    """Static guard: list_social_posts must identify publishing/scheduled
    posts with a zernio_post_id and refresh them, not just return whatever
    is cached in our own DB."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "routers" / "social.py"
    text = src.read_text(encoding="utf-8")
    start = text.find("async def list_social_posts")
    end = text.find("\n@router.", start + 1)
    body = text[start:end if end != -1 else len(text)]
    assert '"publishing"' in body and '"scheduled"' in body
    assert "_apply_zernio_post_refresh" in body

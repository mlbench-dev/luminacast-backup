"""Regression test for the /api/social/posts "Render is ready but has no
video URL" bug.

Bug: create_social_post resolved the render's video URL via
    getattr(render, "final_video_url", None)
    or getattr(render, "output_url", None)
    or getattr(render, "video_url", None)
None of these three attribute names exist on CastRender (confirmed against
models/cast_render.py — the only relevant column is output_video_r2_key, a
storage KEY, not a full URL). Every one of those getattr calls silently fell
through to its None default, so media_url was ALWAYS None and every publish
attempt unconditionally raised "Render is ready but has no video URL",
regardless of whether the render actually had output.

Fix: read output_video_r2_key and resolve it to a public URL via
r2.get_public_url(), matching the pattern used everywhere else this key is
consumed (e.g. routers/casts.py's get_cast_render / select_cast_render).
"""
from __future__ import annotations

from pathlib import Path

SOCIAL_PY = Path(__file__).resolve().parents[2] / "routers" / "social.py"


def _create_post_source() -> str:
    src = SOCIAL_PY.read_text(encoding="utf-8")
    start = src.find("async def create_social_post")
    assert start != -1, "could not locate create_social_post in routers/social.py"
    end = src.find("\n@router.", start + 1)
    if end == -1:
        end = len(src)
    return src[start:end]


def test_no_longer_references_nonexistent_render_attributes():
    body = _create_post_source()
    for bogus_attr in ("final_video_url", "output_url", "video_url"):
        assert f'"{bogus_attr}"' not in body, (
            f"create_social_post must not read CastRender.{bogus_attr} — "
            "that attribute doesn't exist on the model, it always silently "
            "resolved to None"
        )


def test_resolves_output_video_r2_key_to_a_public_url():
    body = _create_post_source()
    assert "output_video_r2_key" in body, (
        "create_social_post must read the real column, output_video_r2_key"
    )
    assert "get_public_url" in body, (
        "the r2_key must be resolved to a full URL via r2.get_public_url() "
        "— Zernio needs a fetchable URL, not a bare storage key"
    )


def test_reads_nested_post_object_not_top_level_fields():
    """Bug: Zernio's create-post response nests everything under "post"
    (PostCreateResponse in their OpenAPI spec: {message, post: {_id, status,
    platforms: [...]}}) — result.get("id") and result.get("platformPostIds")
    read fields that only exist at a level that doesn't exist, always
    silently resolving to None. Confirmed live: a genuinely successful 201
    logged "Zernio post created: None". This also meant a real "published"
    status from Zernio was discarded in favor of a hardcoded "publishing",
    which is why the Published tab (filters on status === "published")
    never showed anything — nothing else ever bulk-updates that status."""
    body = _create_post_source()
    assert 'result.get("post")' in body, (
        "must unwrap the nested post object from Zernio's response"
    )
    assert 'result.get("id")' not in body
    assert 'result.get("platformPostIds")' not in body
    assert "zernio_status" in body, (
        "must use Zernio's own reported status instead of hardcoding "
        '"publishing" for every immediate-publish post'
    )

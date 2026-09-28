"""Regression test: create_social_post must reject a horizontal cast being
published to Instagram or TikTok.

Both platforms have no way to override this on Zernio's side: Instagram
publishes any single video as a Reel (9:16 required, no "Feed" flag for
video), and TikTok has no landscape feed at all. Without this guard, a
16:9 cast reached Zernio and either got silently rejected by the platform
or misclassified, with nothing in Luminacast catching it first.
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


def test_blocks_vertical_only_platforms_for_horizontal_casts():
    body = _create_post_source()
    assert "format_family" in body, (
        "create_social_post must check the cast's format_family before "
        "publishing — neither Instagram nor TikTok can be told to publish "
        "a horizontal video as anything other than what it actually is"
    )
    assert '"instagram"' in body and '"tiktok"' in body
    assert "VERTICAL_ONLY_PLATFORMS" in body


def test_youtube_and_facebook_and_linkedin_are_not_vertical_only():
    body = _create_post_source()
    start = body.find("VERTICAL_ONLY_PLATFORMS = {")
    assert start != -1
    end = body.find("}", start)
    platform_set_src = body[start:end + 1]
    for flexible in ("youtube", "facebook", "linkedin"):
        assert f'"{flexible}"' not in platform_set_src, (
            f"{flexible} accepts both orientations and must not be "
            "blocked by the orientation guard"
        )

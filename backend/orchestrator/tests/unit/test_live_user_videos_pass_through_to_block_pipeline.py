"""PR #162 wiring — the user's picked uploaded videos (``user_video_ids``,
resolved to R2 URLs) are assigned to b-roll-capable blocks as preferred b-roll,
and those blocks are skipped by the Pexels auto-population pass.

DB-free by design (the CI "Backend Tests" unit job runs ``tests/unit/`` WITHOUT a
Postgres service): exercises the pure ``_apply_preferred_broll`` helper plus
``auto_populate_stock_media`` with Pexels disabled (no API key in CI), which is
the same no-op path production hits when Pexels is unconfigured.
"""
import pytest

from engine.cast_generator import _apply_preferred_broll, auto_populate_stock_media


URLS = ["https://r2.test/userA.mp4", "https://r2.test/userB.mp4"]


def test_apply_preferred_broll_claims_broll_capable_blocks():
    outline = [
        {"category": "avatar_speaking"},
        {"category": "avatar_voiceover"},
        {"category": "stock_video"},
        {"category": "avatar_action"},  # not b-roll-capable here
    ]
    claimed = _apply_preferred_broll(outline, cast_id="cst_x", preferred_broll_urls=URLS)

    assert claimed == {0, 1, 2}
    # Round-robin assignment of the two URLs across the three claimed blocks.
    assert outline[0]["stock_media_url"] == URLS[0]
    assert outline[1]["stock_media_url"] == URLS[1]
    assert outline[2]["stock_media_url"] == URLS[0]
    for i in (0, 1, 2):
        assert outline[i]["stock_media_source"] == "user_video"
        assert outline[i]["stock_media_kind"] == "video"
    # The avatar_action block was untouched.
    assert "stock_media_url" not in outline[3]


def test_apply_preferred_broll_sets_parallel_media_for_overlay_blocks():
    outline = [{"category": "avatar_speaking"}]
    _apply_preferred_broll(outline, cast_id="cst_x", preferred_broll_urls=URLS)
    pm = outline[0]["parallel_media"]
    assert len(pm) == 1
    assert pm[0]["url"] == URLS[0]
    assert pm[0]["source"] == "user_video"
    assert pm[0]["kind"] == "video"


def test_apply_preferred_broll_noop_without_urls():
    outline = [{"category": "avatar_speaking"}]
    claimed = _apply_preferred_broll(outline, cast_id="cst_x", preferred_broll_urls=[])
    assert claimed == set()
    assert "stock_media_url" not in outline[0]


@pytest.mark.asyncio
async def test_auto_populate_keeps_user_video_when_pexels_disabled():
    # With no Pexels client (the CI default — no PEXELS_API_KEY), the function
    # still applies the preferred user-video b-roll before short-circuiting, so
    # the picked asset survives into the block.
    outline = [
        {"category": "avatar_voiceover", "stock_media_query": "serum drops"},
    ]
    out = await auto_populate_stock_media(
        outline, cast_id="cst_x", preferred_broll_urls=URLS,
    )
    assert out[0]["stock_media_url"] == URLS[0]
    assert out[0]["stock_media_source"] == "user_video"

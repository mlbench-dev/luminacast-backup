"""Step 10 — B-roll vision re-rank unit tests.

Covers the three behaviours that keep an off-topic Pexels clip out of the
render without ever blocking it:

  (a) the cheap pre-filter reduces a wide candidate set down to the finalist
      count using keyword overlap / orientation / duration signals;
  (b) the vision pick parses the model's ``{"index": N}`` reply and falls back
      to the pre-filter's top result when the reply is not valid JSON;
  (c) a total failure inside the re-rank degrades to the original
      ``videos[0]`` behaviour.

DB-free: ``engine.cast_generator`` imports without a Postgres service, and the
OpenRouter call is mocked, so this runs in the CI "Backend Tests" unit job.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from engine.cast_generator import (
    _block_beat_text,
    _keyword_overlap,
    _prefilter_video_candidates,
    _select_video_candidate,
    _vision_pick_video,
)


def _video(idx: int, *, url: str = "", user: str = "", width: int = 1080,
           height: int = 1920, duration: int = 8, image: str = "") -> dict:
    """A minimal Pexels video candidate."""
    return {
        "id": 1000 + idx,
        "url": url or f"https://www.pexels.com/video/clip-{idx}/",
        "user": {"name": user},
        "width": width,
        "height": height,
        "duration": duration,
        "image": image or f"https://images.pexels.com/thumb-{idx}.jpg",
        "video_files": [
            {"link": f"https://player.pexels.com/{idx}.mp4",
             "file_type": "video/mp4", "width": width, "height": height},
        ],
    }


# ── (a) pre-filter reduces 6 → 3 by tag overlap / orientation / duration ──


def test_prefilter_reduces_six_to_three():
    cands = [_video(i) for i in range(6)]
    out = _prefilter_video_candidates(cands, "ocean waves", "portrait", 3)
    assert len(out) == 3


def test_prefilter_ranks_keyword_overlap_first():
    # Only candidate #4's URL slug overlaps the query; it must come first even
    # though Pexels returned it 5th.
    cands = [_video(i, url=f"https://www.pexels.com/video/random-clip-{i}/")
             for i in range(6)]
    cands[4]["url"] = "https://www.pexels.com/video/woman-running-on-the-beach-99/"
    out = _prefilter_video_candidates(cands, "woman running beach", "portrait", 3)
    assert out[0]["id"] == cands[4]["id"]


def test_prefilter_prefers_matching_orientation_and_duration():
    # Two candidates with no keyword overlap: the portrait one in the 3–30s
    # window should outrank a landscape 90s clip.
    good = _video(0, width=1080, height=1920, duration=8)
    bad = _video(1, width=1920, height=1080, duration=90)
    out = _prefilter_video_candidates([bad, good], "abstract texture", "portrait", 1)
    assert out[0]["id"] == good["id"]


def test_prefilter_preserves_pexels_order_on_full_tie():
    cands = [_video(i) for i in range(4)]  # all identical signal-wise
    out = _prefilter_video_candidates(cands, "neutral", "portrait", 4)
    assert [c["id"] for c in out] == [c["id"] for c in cands]


def test_keyword_overlap_counts_query_tokens_in_url_and_uploader():
    assert _keyword_overlap(
        "sunset mountain hike",
        "https://www.pexels.com/video/mountain-hike-at-sunset-7/",
        "Jane",
    ) == 3
    assert _keyword_overlap("nothing here", "https://pexels.com/video/clip/", "") == 0


def test_block_beat_text_combines_key_points_and_query():
    block = {"key_points": ["fresh produce", "morning market"], "mood": "warm"}
    text = _block_beat_text(block, "farmers market")
    assert "fresh produce" in text
    assert "warm" in text
    assert text.endswith("farmers market")


# ── (b) vision pick parses Opus reply and falls back on JSON error ────────


@pytest.mark.asyncio
async def test_vision_pick_parses_index():
    finalists = [_video(i) for i in range(3)]
    svc = AsyncMock()
    svc.rank_images = AsyncMock(return_value='{"index": 2}')
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        idx = await _vision_pick_video(finalists, "beat", "query", "cast1", 0)
    assert idx == 2
    svc.rank_images.assert_awaited_once()


@pytest.mark.asyncio
async def test_vision_pick_parses_index_inside_code_fence():
    finalists = [_video(i) for i in range(3)]
    svc = AsyncMock()
    svc.rank_images = AsyncMock(return_value='```json\n{"index": 1}\n```')
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        idx = await _vision_pick_video(finalists, "beat", "query", "cast1", 0)
    assert idx == 1


@pytest.mark.asyncio
async def test_vision_pick_falls_back_to_zero_on_bad_json():
    finalists = [_video(i) for i in range(3)]
    svc = AsyncMock()
    svc.rank_images = AsyncMock(return_value="the second one looks great!")
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        idx = await _vision_pick_video(finalists, "beat", "query", "cast1", 0)
    assert idx == 0


@pytest.mark.asyncio
async def test_vision_pick_falls_back_when_index_out_of_range():
    finalists = [_video(i) for i in range(3)]
    svc = AsyncMock()
    svc.rank_images = AsyncMock(return_value='{"index": 9}')
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        idx = await _vision_pick_video(finalists, "beat", "query", "cast1", 0)
    assert idx == 0


@pytest.mark.asyncio
async def test_vision_pick_skips_when_any_thumbnail_missing():
    finalists = [_video(0), _video(1, image=""), _video(2)]
    finalists[1]["image"] = None
    svc = AsyncMock()
    svc.rank_images = AsyncMock(return_value='{"index": 2}')
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        idx = await _vision_pick_video(finalists, "beat", "query", "cast1", 0)
    # No vision call when a candidate has no preview frame.
    assert idx == 0
    svc.rank_images.assert_not_awaited()


# ── (c) end-to-end candidate selection + total-failure fallback ───────────


class _FakeClient:
    def __init__(self, videos):
        self._videos = videos
        self.last_per_page = None

    async def safe_search_videos(self, query, per_page=3, orientation="portrait", **kw):
        self.last_per_page = per_page
        return {"videos": self._videos}


@pytest.mark.asyncio
async def test_select_uses_vision_winner(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "true")
    monkeypatch.setenv("BROLL_CANDIDATE_COUNT", "6")
    monkeypatch.setenv("BROLL_RERANK_FINALISTS", "3")
    videos = [_video(i) for i in range(6)]
    client = _FakeClient(videos)
    svc = AsyncMock()
    svc.rank_images = AsyncMock(return_value='{"index": 1}')
    with patch("services.openrouter.get_openrouter_service", return_value=svc):
        best = await _select_video_candidate(
            client, "ocean", "ocean beat", "portrait", "cast1", 0,
        )
    assert client.last_per_page == 6
    # Index 1 into the finalists (all tie → Pexels order preserved) = video #1.
    assert best["id"] == videos[1]["id"]


@pytest.mark.asyncio
async def test_select_returns_none_when_no_results(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "true")
    client = _FakeClient([])
    best = await _select_video_candidate(
        client, "ocean", "ocean beat", "portrait", "cast1", 0,
    )
    assert best is None


@pytest.mark.asyncio
async def test_select_falls_back_to_first_on_total_failure(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "true")
    monkeypatch.setenv("BROLL_CANDIDATE_COUNT", "6")
    videos = [_video(i) for i in range(6)]
    client = _FakeClient(videos)
    # Pre-filter blows up → except path returns videos[0].
    with patch("engine.cast_generator._prefilter_video_candidates",
               side_effect=RuntimeError("boom")):
        best = await _select_video_candidate(
            client, "ocean", "ocean beat", "portrait", "cast1", 0,
        )
    assert best["id"] == videos[0]["id"]


@pytest.mark.asyncio
async def test_select_keeps_first_result_when_rerank_disabled(monkeypatch):
    monkeypatch.setenv("BROLL_RERANK_ENABLED", "false")
    videos = [_video(i) for i in range(6)]
    client = _FakeClient(videos)
    best = await _select_video_candidate(
        client, "ocean", "ocean beat", "portrait", "cast1", 0,
    )
    # Disabled → per_page reverts to 3 and we take the first result, no vision.
    assert client.last_per_page == 3
    assert best["id"] == videos[0]["id"]

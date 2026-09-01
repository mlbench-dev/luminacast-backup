"""``_ensure_fresh_tts_for_block`` — snapshot audio backfill.

The editor timeline snapshot can pre-date a block's audio (e.g. an
avatar_action block scripted + TTS'd after the timeline was last saved). Its
timeline element then has no audio URL, so the render used to bake it silently
even though the variant carries perfectly good, fresh audio.

The guard now backfills the voiceover from the variant's own TTS key when the
snapshot URL is empty and the audio is not stale.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tasks.cast_render import _ensure_fresh_tts_for_block


class _FakeVariant:
    def __init__(self):
        self.script_text = "Woohoo fam, this rice cooker is a game changer."
        self.tts_r2_key = "casts/cst_x/blk_11/tts.wav"
        self.audio_key = "casts/cst_x/blk_11/tts.wav"
        self.tts_duration_seconds = 4.2
        # Baked AFTER the last variant edit -> not stale.
        self.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)


class _Scalars:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Result:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return _Scalars(self._row)


class _FakeSession:
    def __init__(self, variant):
        self._variant = variant

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        return _Result(self._variant)

    async def get(self, *_a, **_k):  # only hit on the stale path
        return None


class _FakeR2:
    def __init__(self):
        self.public_urls = []

    async def head_object(self, key):
        # Object is newer than variant.updated_at -> fresh, not stale.
        return {"LastModified": datetime.now(timezone.utc)}

    def get_public_url(self, key):
        self.public_urls.append(key)
        return f"https://cdn.example/{key}"


def _factory(variant):
    def _make():
        return _FakeSession(variant)
    return _make


@pytest.mark.asyncio
async def test_backfills_voiceover_url_when_snapshot_audio_missing():
    variant = _FakeVariant()
    r2 = _FakeR2()

    url, dur = await _ensure_fresh_tts_for_block(
        r2, _factory(variant),
        cast_id="cst_x", user_id="usr_x", block_id="blk_11",
        snapshot_audio_url="",  # editor timeline had no audio element
    )

    assert url == "https://cdn.example/casts/cst_x/blk_11/tts.wav"
    assert dur == pytest.approx(4.2)
    assert r2.public_urls == ["casts/cst_x/blk_11/tts.wav"]


@pytest.mark.asyncio
async def test_keeps_snapshot_url_when_present():
    variant = _FakeVariant()
    r2 = _FakeR2()

    url, _dur = await _ensure_fresh_tts_for_block(
        r2, _factory(variant),
        cast_id="cst_x", user_id="usr_x", block_id="blk_11",
        snapshot_audio_url="https://cdn.example/from-snapshot.wav",
    )

    assert url == "https://cdn.example/from-snapshot.wav"
    assert r2.public_urls == []  # no backfill needed


@pytest.mark.asyncio
async def test_no_backfill_when_variant_has_no_tts_key():
    variant = _FakeVariant()
    variant.tts_r2_key = ""
    variant.audio_key = ""
    r2 = _FakeR2()
    # Empty tts_r2_key makes it "stale"; the inline regen path then bails
    # (fake session.get returns None -> no avatar/voice) and returns the
    # snapshot URL unchanged. Point: it does NOT invent a bogus URL.
    url, _dur = await _ensure_fresh_tts_for_block(
        r2, _factory(variant),
        cast_id="cst_x", user_id="usr_x", block_id="blk_11",
        snapshot_audio_url="",
    )
    assert url == ""
    assert r2.public_urls == []

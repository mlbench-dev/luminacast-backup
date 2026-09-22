"""POST /api/avatar/ai/{avatar_id}/clone-voice-from-corpus.

Trains a voice clone from existing ready voice-corpus entries instead of a
fresh multipart upload. Validates ownership/readiness, sums duration against
the minimum, downloads the isolated audio from R2, and reuses the shared
clone path (which sets avatar.voice_id).
"""
import os
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

ffmpeg_available = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not available on this runner",
)

from main import app
from database import get_db
from routers.auth import get_current_user, get_workspace_context, WorkspaceContext

AVATAR_ID = "avt_corpus_test"
USER = SimpleNamespace(id="usr_corpus_test", email="c@test.com", role="creator")


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _FakeSession:
    """Async session stub: serves the avatar + a fixed set of corpus entries."""

    def __init__(self, avatar, entries):
        self._avatar = avatar
        self._entries = entries
        self.committed = False

    async def get(self, model, pk):
        return self._avatar

    async def execute(self, *a, **k):
        return _ScalarsResult(self._entries)

    async def commit(self):
        self.committed = True


def _make_avatar():
    return SimpleNamespace(
        id=AVATAR_ID, user_id=USER.id, name="Adaeze",
        voice_id=None, voice_sample_key=None,
    )


def _make_entry(eid, status="ready", duration=30.0, key="creators/x/iso.wav"):
    return SimpleNamespace(
        id=eid, avatar_id=AVATAR_ID, status=status,
        duration_seconds=duration, audio_r2_key=key,
    )


def _fake_r2(wav_bytes=None):
    r2 = AsyncMock()
    payload = wav_bytes if wav_bytes is not None else b"\x00" * 64000

    async def _download(key, local_path):
        with open(local_path, "wb") as f:
            f.write(payload)
        return local_path

    r2.download_file = _download
    r2.upload_bytes = AsyncMock(return_value="creators/x/voice_sample.wav")
    r2.get_public_url = lambda key: f"https://media.test/{key}"
    return r2


def _fake_fish(voice_id="voice_cloned_42"):
    fish = AsyncMock()
    fish.clone_voice = AsyncMock(return_value=voice_id)
    return fish


async def _call(session, body):
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: USER
    # clone-voice-from-corpus is gated by require_role(CREATOR), which
    # depends on get_workspace_context — a separate dependency from
    # get_current_user that isn't satisfied by overriding get_current_user
    # alone. Without this override every request 403s before reaching the
    # route body.
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        workspace_owner_id=USER.id,
        actor_user_id=USER.id,
        actor_team_role=None,
        is_owner=True,
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.post(
                f"/api/avatar/ai/{AVATAR_ID}/clone-voice-from-corpus", json=body,
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_clone_from_single_ready_entry_sets_voice_id():
    avatar = _make_avatar()
    entry = _make_entry("vc_one", duration=31.98)
    session = _FakeSession(avatar, [entry])

    with patch("services.r2_storage.get_r2_storage_service", return_value=_fake_r2()), \
         patch("services.fish_audio.get_fish_audio_service", return_value=_fake_fish()):
        resp = await _call(session, {"corpus_entry_id": "vc_one"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["voice_id"] == "voice_cloned_42"
    assert avatar.voice_id == "voice_cloned_42"
    assert session.committed is True


@ffmpeg_available
@pytest.mark.asyncio
async def test_clone_from_multiple_entries_concatenates(tmp_path):
    # Real 16kHz mono WAV so the ffmpeg concat path runs end to end.
    import subprocess
    sample = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
         "-ar", "16000", "-ac", "1", str(sample), "-y"],
        check=True, capture_output=True,
    )
    wav_bytes = sample.read_bytes()

    avatar = _make_avatar()
    entries = [_make_entry("vc_a", duration=5.0), _make_entry("vc_b", duration=5.0)]
    session = _FakeSession(avatar, entries)
    fish = _fake_fish()

    with patch("services.r2_storage.get_r2_storage_service", return_value=_fake_r2(wav_bytes)), \
         patch("services.fish_audio.get_fish_audio_service", return_value=fish):
        resp = await _call(session, {"corpus_entry_ids": ["vc_a", "vc_b"]})

    assert resp.status_code == 200, resp.text
    assert avatar.voice_id == "voice_cloned_42"
    # Merged audio was uploaded as a single wav sample before cloning.
    fish.clone_voice.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_ids_returns_400():
    session = _FakeSession(_make_avatar(), [])
    resp = await _call(session, {})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_not_ready_entry_returns_400():
    avatar = _make_avatar()
    entry = _make_entry("vc_proc", status="processing")
    session = _FakeSession(avatar, [entry])
    resp = await _call(session, {"corpus_entry_ids": ["vc_proc"]})
    assert resp.status_code == 400
    assert "not ready" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_below_minimum_duration_returns_400():
    avatar = _make_avatar()
    entry = _make_entry("vc_short", duration=4.0)
    session = _FakeSession(avatar, [entry])
    resp = await _call(session, {"corpus_entry_ids": ["vc_short"]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_entry_for_other_avatar_returns_404():
    avatar = _make_avatar()
    entry = _make_entry("vc_other")
    entry.avatar_id = "avt_someone_else"
    session = _FakeSession(avatar, [entry])
    resp = await _call(session, {"corpus_entry_ids": ["vc_other"]})
    assert resp.status_code == 404

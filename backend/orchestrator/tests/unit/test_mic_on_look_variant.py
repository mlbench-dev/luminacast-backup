"""Unit tests for services.mic_on_look — Step 8.

Covers the baked mic-on look-variant generator and the renderer-facing
resolver:

  * the FLUX prompt carries the vetted clip-on constraints,
  * idempotency (a 2nd call with an existing variant reuses the cached row and
    does NOT call FLUX),
  * the block selector behaviour for mic_on True/False/None,
  * FLUX errors fall back to the base look with no exception leaking,
  * the feature flag OFF always uses the base look.

No real DB, FLUX, R2, or HTTP is touched — a tiny fake async session stands in
for SQLAlchemy and the FLUX/R2/HTTP calls are monkeypatched.
"""
from __future__ import annotations

import importlib
import types

import pytest

import services.mic_on_look as mic_on_look
from services.mic_on_look import (
    MIC_ON_LOOK_PROMPT,
    generate_mic_on_variant,
    resolve_mic_on_face_key,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeLook:
    """Stand-in for models.avatar_look.AvatarLook."""

    def __init__(self, id, avatar_id, look_type, face_ref_key=None, status="ready", name=""):
        self.id = id
        self.avatar_id = avatar_id
        self.look_type = look_type
        self.face_ref_key = face_ref_key
        self.status = status
        self.name = name
        self.is_default = False
        self.is_original = False
        self.error_message = None


class _Result:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class FakeSession:
    """Minimal async session: serves get() from a dict, returns a configurable
    existing-variant row from execute(), and records added rows.
    """

    def __init__(self, rows=None, existing_variant=None):
        self._rows = dict(rows or {})
        self._existing_variant = existing_variant
        self.added = []
        self.commits = 0

    async def get(self, model, pk):
        return self._rows.get(pk)

    async def execute(self, _query):
        return _Result(self._existing_variant)

    def add(self, obj):
        self.added.append(obj)
        self._rows[obj.id] = obj

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        return None


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("MIC_ON_LOOK_VARIANT_ENABLED", "true")
    importlib.reload(mic_on_look)
    yield


@pytest.fixture
def patched_pipeline(monkeypatch):
    """Patch the FLUX call, R2, and HTTP download so generation succeeds
    deterministically and we can assert the prompt / call counts.
    """
    calls = {"flux": 0, "flux_prompt": None}

    async def fake_flux(face_url):
        calls["flux"] += 1
        # The prompt is a module constant; record it as the caller would send.
        calls["flux_prompt"] = mic_on_look.MIC_ON_LOOK_PROMPT
        return "https://flux.test/out.jpg"

    monkeypatch.setattr(mic_on_look, "_run_flux_clip_on", fake_flux)

    class FakeR2:
        def get_public_url(self, key, cache_bust=False):
            return f"https://media.test/{key}"

        async def upload_file(self, local_path, key, content_type=None):
            return key

    monkeypatch.setattr(mic_on_look, "get_r2_storage_service", lambda: FakeR2(), raising=False)
    # get_r2_storage_service is imported inside the function, so patch the source.
    import services.r2_storage as r2mod
    monkeypatch.setattr(r2mod, "get_r2_storage_service", lambda: FakeR2())

    # Avoid real network/file IO inside generate_mic_on_variant's download step.
    class FakeResp:
        content = b"jpegbytes"

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    return calls


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


def test_prompt_contains_vetted_constraints():
    p = MIC_ON_LOOK_PROMPT.lower()
    assert "clip-on" in p
    assert "lavalier" in p
    assert "collar" in p or "lapel" in p
    # explicit negative constraints
    assert "handheld" in p
    assert "headset" in p
    assert "boom" in p
    # identity preservation
    assert "exactly the same" in p


# ---------------------------------------------------------------------------
# Generation + idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_creates_variant_and_calls_flux(monkeypatch, patched_pipeline):
    base = FakeLook("look_base", "avt1", "office_casual", face_ref_key="creators/avt1/base.jpg")
    session = FakeSession(rows={"look_base": base}, existing_variant=None)

    monkeypatch.setattr(mic_on_look, "_existing_variant", _no_existing)

    variant = await generate_mic_on_variant("avt1", "look_base", session)
    assert variant is not None
    assert variant.status == "ready"
    assert variant.look_type == "mic_on_office_casual"
    assert variant.face_ref_key  # an R2 key was assigned
    assert patched_pipeline["flux"] == 1
    assert "clip-on" in patched_pipeline["flux_prompt"].lower()


@pytest.mark.asyncio
async def test_idempotent_cache_hit_skips_flux(monkeypatch, patched_pipeline):
    """A 2nd call with an existing ready variant returns the cached row and
    does NOT invoke FLUX."""
    cached = FakeLook(
        "look_mic", "avt1", "mic_on_office_casual",
        face_ref_key="creators/avt1/mic.jpg", status="ready",
        name="mic-on:look_base",
    )
    session = FakeSession(rows={}, existing_variant=cached)

    variant = await generate_mic_on_variant("avt1", "look_base", session)
    assert variant is cached
    assert patched_pipeline["flux"] == 0  # FLUX not called on cache hit


# ---------------------------------------------------------------------------
# Block selector behaviour (resolve_mic_on_face_key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_selector_mic_on_true_no_variant_triggers_generation(monkeypatch, patched_pipeline):
    base = FakeLook("look_base", "avt1", "office_casual", face_ref_key="creators/avt1/base.jpg")
    session = FakeSession(rows={"look_base": base}, existing_variant=None)
    monkeypatch.setattr(mic_on_look, "_existing_variant", _no_existing)

    key = await resolve_mic_on_face_key(
        True, "avt1", "look_base", "creators/avt1/base.jpg", session
    )
    assert key != "creators/avt1/base.jpg"  # swapped to the generated variant
    assert patched_pipeline["flux"] == 1


@pytest.mark.asyncio
async def test_selector_mic_on_true_existing_variant_uses_cache(monkeypatch, patched_pipeline):
    cached = FakeLook(
        "look_mic", "avt1", "mic_on_office_casual",
        face_ref_key="creators/avt1/mic.jpg", status="ready", name="mic-on:look_base",
    )
    session = FakeSession(rows={}, existing_variant=cached)

    key = await resolve_mic_on_face_key(
        True, "avt1", "look_base", "creators/avt1/base.jpg", session
    )
    assert key == "creators/avt1/mic.jpg"
    assert patched_pipeline["flux"] == 0


@pytest.mark.asyncio
async def test_selector_mic_off_uses_base(patched_pipeline):
    session = FakeSession()
    key = await resolve_mic_on_face_key(
        False, "avt1", "look_base", "creators/avt1/base.jpg", session
    )
    assert key == "creators/avt1/base.jpg"
    assert patched_pipeline["flux"] == 0


@pytest.mark.asyncio
async def test_selector_mic_none_uses_base(patched_pipeline):
    session = FakeSession()
    key = await resolve_mic_on_face_key(
        None, "avt1", "look_base", "creators/avt1/base.jpg", session
    )
    assert key == "creators/avt1/base.jpg"
    assert patched_pipeline["flux"] == 0


# ---------------------------------------------------------------------------
# Failure path + feature flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flux_error_falls_back_to_base_no_leak(monkeypatch):
    base = FakeLook("look_base", "avt1", "office_casual", face_ref_key="creators/avt1/base.jpg")
    session = FakeSession(rows={"look_base": base}, existing_variant=None)
    monkeypatch.setattr(mic_on_look, "_existing_variant", _no_existing)

    async def boom(_face_url):
        raise RuntimeError("flux exploded")

    monkeypatch.setattr(mic_on_look, "_run_flux_clip_on", boom)

    captured = {"n": 0}
    monkeypatch.setattr(mic_on_look.sentry_sdk, "capture_exception", lambda e: captured.__setitem__("n", captured["n"] + 1))

    # No exception should leak, and we fall back to the base key.
    key = await resolve_mic_on_face_key(
        True, "avt1", "look_base", "creators/avt1/base.jpg", session
    )
    assert key == "creators/avt1/base.jpg"
    assert captured["n"] >= 1  # the FLUX failure was reported to Sentry


@pytest.mark.asyncio
async def test_feature_flag_off_always_uses_base(monkeypatch, patched_pipeline):
    monkeypatch.setenv("MIC_ON_LOOK_VARIANT_ENABLED", "false")
    importlib.reload(mic_on_look)

    session = FakeSession()
    key = await mic_on_look.resolve_mic_on_face_key(
        True, "avt1", "look_base", "creators/avt1/base.jpg", session
    )
    assert key == "creators/avt1/base.jpg"
    assert patched_pipeline["flux"] == 0


@pytest.mark.asyncio
async def test_selector_no_base_look_id_keeps_base(patched_pipeline):
    """mic_on True but no concrete base look id (legacy avatar.face_ref_key
    fallback) → keep the clean key rather than guessing."""
    session = FakeSession()
    key = await resolve_mic_on_face_key(
        True, "avt1", None, "creators/avt1/legacy.jpg", session
    )
    assert key == "creators/avt1/legacy.jpg"
    assert patched_pipeline["flux"] == 0


async def _no_existing(session, avatar_id, base_look_id):
    return None

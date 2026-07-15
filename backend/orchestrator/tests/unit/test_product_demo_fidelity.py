"""Unit tests for PR-D: PRODUCT_DEMO product fidelity.

Covers the two paths that fix the user complaint @0:24 ("again, she holds
generic product"):

  Option B (always-on)
    - build_product_visual_description() distils product metadata into a
      compact visual clause.
    - build_product_aware_scene_prompt() leads the motion prompt with that
      explicit product appearance (name/color/shape/label), not just the
      brand name.
    - product_demo_overlay_scale() reads PRODUCT_DEMO_OVERLAY_SCALE (default
      1.25), clamped to [1.0, 1.5].

  Option A (flag-gated)
    - flux_kontext_scene_enabled() defaults OFF and flips truthy.
    - generate_scene_image() calls the I2I model, caches per
      (product_id, motion_intent_hash), and reuses the cache on a hit.
    - KlingV3ProElementsProvider._bake_segment uses the supplied scene start
      image as the I2V start frame while keeping the avatar face_ref as the
      Elements subject anchor.

``sentry_sdk`` is a real installed dependency; do NOT stub it.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_prompts import (
    build_product_aware_scene_prompt,
    build_product_visual_description,
)
from services.twick_compositor_adapter import (
    PRODUCT_OVERLAY_BASE_WIDTH,
    product_demo_overlay_scale,
    product_demo_overlay_width,
)
import services.scene_image as scene_image
from services.render_providers import KlingV3ProElementsProvider


# ─────────────────────────────────────────────────────────────────────────────
# Option B — prompt template
# ─────────────────────────────────────────────────────────────────────────────

def test_visual_description_combines_metadata_fields():
    desc = build_product_visual_description(
        product_name="OGX Argan Oil",
        description="A teal-blue bottle with a white pump. Hydrates dry hair.",
        category="hair care",
        tags=["argan oil", "sulfate-free"],
    )
    # Name leads, first visual sentence of the description is kept, category
    # and a couple of tags follow.
    assert desc.startswith("OGX Argan Oil")
    assert "teal-blue bottle with a white pump" in desc
    assert "hair care" in desc
    assert "argan oil" in desc


def test_visual_description_dedupes_and_handles_empty():
    assert build_product_visual_description() == ""
    # Name repeated inside description should not appear twice.
    desc = build_product_visual_description(
        product_name="Glow Serum",
        description="Glow Serum in a frosted glass dropper",
    )
    assert desc.lower().count("glow serum") == 1


def test_scene_prompt_leads_with_product_appearance():
    prompt = build_product_aware_scene_prompt(
        base_prompt="walking through a sunlit kitchen, slow cinematic pan",
        product_name="OGX Argan Oil",
        product_visual_description="OGX Argan Oil, a teal-blue bottle with a white pump",
    )
    # Leads with the explicit product appearance, not the motion text.
    assert prompt.startswith("close-up of person holding OGX Argan Oil, a teal-blue bottle")
    assert "brand label clearly visible" in prompt
    assert "product centered in frame" in prompt
    # The motion description is preserved verbatim.
    assert "walking through a sunlit kitchen, slow cinematic pan" in prompt
    # The element-token clause still locks the product across frames.
    assert "@product1" in prompt
    assert "consistent shape, color, and label" in prompt


def test_scene_prompt_falls_back_to_name_then_generic():
    # No visual description → lead with the product name.
    p1 = build_product_aware_scene_prompt(
        base_prompt="holding it up to camera",
        product_name="OGX Argan Oil",
        product_visual_description=None,
    )
    assert p1.startswith("close-up of person holding OGX Argan Oil,")
    # No visual and no name → generic subject, never an empty subject.
    p2 = build_product_aware_scene_prompt(
        base_prompt="", product_name=None, product_visual_description=None
    )
    assert "close-up of person holding the product," in p2
    assert "A person speaking naturally to the camera" in p2


def test_scene_prompt_signature_is_backward_compatible():
    # Existing callers that pass only base_prompt + product_name must still work.
    p = build_product_aware_scene_prompt("doing a thing", "Brand X")
    assert "Brand X" in p
    assert p.startswith("close-up of person holding Brand X,")


# ─────────────────────────────────────────────────────────────────────────────
# Option B — overlay scale
# ─────────────────────────────────────────────────────────────────────────────

def test_overlay_scale_default(monkeypatch):
    monkeypatch.delenv("PRODUCT_DEMO_OVERLAY_SCALE", raising=False)
    assert product_demo_overlay_scale() == 1.25
    assert product_demo_overlay_width() == int(round(PRODUCT_OVERLAY_BASE_WIDTH * 1.25))


def test_overlay_scale_configurable_within_range(monkeypatch):
    monkeypatch.setenv("PRODUCT_DEMO_OVERLAY_SCALE", "1.5")
    assert product_demo_overlay_scale() == 1.5
    assert product_demo_overlay_width() == int(round(PRODUCT_OVERLAY_BASE_WIDTH * 1.5))


def test_overlay_scale_clamps_high_and_low(monkeypatch):
    monkeypatch.setenv("PRODUCT_DEMO_OVERLAY_SCALE", "5.0")
    assert product_demo_overlay_scale() == 1.5  # clamped to max
    monkeypatch.setenv("PRODUCT_DEMO_OVERLAY_SCALE", "0.2")
    assert product_demo_overlay_scale() == 1.0  # clamped to min


def test_overlay_scale_malformed_falls_back(monkeypatch):
    monkeypatch.setenv("PRODUCT_DEMO_OVERLAY_SCALE", "not-a-number")
    assert product_demo_overlay_scale() == 1.25


# ─────────────────────────────────────────────────────────────────────────────
# Option A — flag gate
# ─────────────────────────────────────────────────────────────────────────────

def test_flux_scene_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE", raising=False)
    assert scene_image.flux_kontext_scene_enabled() is False


def test_flux_scene_flag_truthy(monkeypatch):
    for v in ("true", "1", "yes", "ON"):
        monkeypatch.setenv("PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE", v)
        assert scene_image.flux_kontext_scene_enabled() is True
    monkeypatch.setenv("PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE", "false")
    assert scene_image.flux_kontext_scene_enabled() is False


def test_cache_key_is_stable_per_product_and_intent():
    k1 = scene_image.scene_image_cache_key("prod_e70898759dd4", "Hold it up")
    k2 = scene_image.scene_image_cache_key("prod_e70898759dd4", "  hold it up  ")
    k3 = scene_image.scene_image_cache_key("prod_e70898759dd4", "different motion")
    assert k1 == k2  # normalised whitespace/case → same key
    assert k1 != k3
    assert k1.startswith("scene_images/prod_e70898759dd4/")


# ─────────────────────────────────────────────────────────────────────────────
# Option A — scene image generation + caching
# ─────────────────────────────────────────────────────────────────────────────

def _fake_r2(*, exists: bool):
    r2 = MagicMock()
    r2.key_exists = AsyncMock(return_value=exists)
    r2.upload_bytes = AsyncMock(return_value="key")
    r2.get_public_url = MagicMock(return_value="https://media.test/scene.jpg")
    return r2


def test_generate_scene_image_cache_hit_skips_fal(monkeypatch):
    r2 = _fake_r2(exists=True)
    monkeypatch.setattr(
        "services.r2_storage.get_r2_storage_service", lambda: r2
    )

    # httpx GET returns the cached bytes.
    resp = MagicMock()
    resp.content = b"cached-bytes"
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    fal_run = AsyncMock()
    with patch.object(scene_image.httpx, "AsyncClient", return_value=client), \
         patch.object(scene_image.fal_client, "run_async", fal_run):
        out = asyncio.new_event_loop().run_until_complete(
            scene_image.generate_scene_image(
                product_asset_url="https://media.test/prod.jpg",
                motion_prompt="hold it up",
                product_id="prod_e70898759dd4",
            )
        )

    assert out == b"cached-bytes"
    fal_run.assert_not_called()  # cache hit → never paid FLUX
    r2.upload_bytes.assert_not_called()


def test_generate_scene_image_miss_generates_and_caches(monkeypatch):
    r2 = _fake_r2(exists=False)
    monkeypatch.setattr(
        "services.r2_storage.get_r2_storage_service", lambda: r2
    )
    monkeypatch.setenv("FAL_KEY", "test-key")

    resp = MagicMock()
    resp.content = b"generated-bytes"
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    fal_run = AsyncMock(return_value={"images": [{"url": "https://fal.test/out.jpg"}]})

    with patch.object(scene_image.httpx, "AsyncClient", return_value=client), \
         patch.object(scene_image.fal_client, "run_async", fal_run):
        out = asyncio.new_event_loop().run_until_complete(
            scene_image.generate_scene_image(
                product_asset_url="https://media.test/prod.jpg",
                motion_prompt="hold it up",
                product_id="prod_e70898759dd4",
            )
        )

    assert out == b"generated-bytes"
    fal_run.assert_awaited_once()
    # The product image is the I2I input.
    _, kwargs = fal_run.call_args
    assert kwargs["arguments"]["image_url"] == "https://media.test/prod.jpg"
    # Result cached for next time.
    r2.upload_bytes.assert_awaited_once()
    cache_key = r2.upload_bytes.call_args.args[1]
    assert cache_key.startswith("scene_images/prod_e70898759dd4/")


def test_generate_scene_image_requires_product_url():
    with pytest.raises(ValueError):
        asyncio.new_event_loop().run_until_complete(
            scene_image.generate_scene_image(
                product_asset_url="", motion_prompt="x", product_id="p"
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# Option A — provider uses scene image as the I2V start frame
# ─────────────────────────────────────────────────────────────────────────────

def test_bake_segment_uses_scene_start_image(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    provider = KlingV3ProElementsProvider()

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "status_url": "https://fal.test/status",
                "response_url": "https://fal.test/response",
            }

    async def _post(url, headers=None, json=None):
        captured["payload"] = json
        return _Resp()

    client = MagicMock()
    client.post = _post

    # The poll loop runs against the MagicMock client after submit and raises;
    # we only assert on the submitted payload, which is captured at post time.
    async def _run():
        try:
            await provider._bake_segment(
                image_url="https://media.test/avatar_face.jpg",
                product_image_urls=["https://media.test/prod.jpg"],
                prompt="hold it",
                segment_duration_s=5,
                client=client,
                timeout_s=10,
                start_image_url="https://media.test/scene.jpg",
            )
        except Exception:
            # Polling internals are out of scope — payload is captured.
            pass

    asyncio.new_event_loop().run_until_complete(_run())

    payload = captured["payload"]
    # The scene still is the I2V start frame…
    assert payload["start_image_url"] == "https://media.test/scene.jpg"
    # …while the avatar face_ref stays the Elements subject anchor.
    assert payload["elements"][0]["frontal_image_url"] == "https://media.test/avatar_face.jpg"
    assert payload["elements"][0]["reference_image_urls"] == ["https://media.test/prod.jpg"]


def test_bake_segment_defaults_to_avatar_image_without_scene(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    provider = KlingV3ProElementsProvider()
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "status_url": "https://fal.test/status",
                "response_url": "https://fal.test/response",
            }

    async def _post(url, headers=None, json=None):
        captured["payload"] = json
        return _Resp()

    client = MagicMock()
    client.post = _post

    async def _run():
        try:
            await provider._bake_segment(
                image_url="https://media.test/avatar_face.jpg",
                product_image_urls=["https://media.test/prod.jpg"],
                prompt="hold it",
                segment_duration_s=5,
                client=client,
                timeout_s=10,
            )
        except Exception:
            pass

    asyncio.new_event_loop().run_until_complete(_run())
    # No scene image → start frame is the avatar face_ref (unchanged behavior).
    assert captured["payload"]["start_image_url"] == "https://media.test/avatar_face.jpg"

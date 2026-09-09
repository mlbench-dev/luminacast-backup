"""AI scene b-roll (services/ai_broll) — flag gating, prompt shaping, aspect
ratio resolution, and the disabled/no-key early exits.

Pure unit tests — no fal calls, no network.
"""
import importlib
from unittest.mock import patch

import pytest

from services import ai_broll as m


def _reload(env: dict):
    with patch.dict("os.environ", env, clear=False):
        importlib.reload(m)
        return m


@pytest.fixture(autouse=True)
def _clean_env():
    import os
    keys = [k for k in list(os.environ) if k.startswith("AI_BROLL")]
    with patch.dict("os.environ", {k: "" for k in keys}, clear=False):
        importlib.reload(m)
        yield
    importlib.reload(m)


def test_disabled_by_default():
    assert m.ai_broll_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_flag_parsing(val, expected):
    assert _reload({"AI_BROLL_ENABLED": val}).ai_broll_enabled() is expected


def test_default_model_is_kling_t2v():
    assert m._model() == "fal-ai/kling-video/v2.1/master/text-to-video"


def test_model_override():
    assert _reload({"AI_BROLL_MODEL": "fal-ai/other/t2v"})._model() == "fal-ai/other/t2v"


def test_aspect_ratio_defaults_to_9_16():
    assert m.broll_aspect_ratio(None) == "9:16"


def test_aspect_ratio_env_override_valid():
    assert _reload({"AI_BROLL_ASPECT_RATIO": "16:9"}).broll_aspect_ratio(None) == "16:9"
    assert _reload({"AI_BROLL_ASPECT_RATIO": "1:1"}).broll_aspect_ratio(None) == "1:1"


def test_aspect_ratio_env_override_invalid_falls_back():
    assert _reload({"AI_BROLL_ASPECT_RATIO": "banana"}).broll_aspect_ratio(None) == "9:16"


def test_clean_prompt_adds_broll_styling_and_negatives():
    p = m._clean_prompt("cozy fleece hoodie flatlay")
    assert p.startswith("cozy fleece hoodie flatlay.")
    assert "b-roll" in p.lower()
    assert "no text" in p.lower() and "watermark" in p.lower()


def test_clean_prompt_rejects_blank():
    with pytest.raises(m.AiBrollError):
        m._clean_prompt("   ")
    with pytest.raises(m.AiBrollError):
        m._clean_prompt(None)


def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_generate_raises_when_disabled():
    with pytest.raises(m.AiBrollError, match="off"):
        _run(m.generate_scene_broll_video(prompt_query="x", owner_id="u"))


def test_generate_raises_without_fal_key_even_when_enabled():
    mod = _reload({"AI_BROLL_ENABLED": "1"})
    with patch("config.settings") as s:
        s.FAL_API_KEY = ""
        with pytest.raises(m.AiBrollError, match="FAL_API_KEY"):
            _run(mod.generate_scene_broll_video(prompt_query="x", owner_id="u"))

"""Env-var loading tests for worker_ffmpeg_compose.

The orchestrator stack exports R2 credentials as R2_ACCESS_KEY_ID /
R2_SECRET_ACCESS_KEY, while the legacy HOSTKEY GPU worker used the shorter
R2_ACCESS_KEY / R2_SECRET_KEY names. The module must accept both, preferring
the orchestrator-style names.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

# ─── Stub sentry_sdk so importing the module doesn't pull the real dep ───
if "sentry_sdk" not in sys.modules:
    sys.modules["sentry_sdk"] = types.SimpleNamespace(
        capture_exception=lambda *_a, **_k: None,
        set_tag=lambda *_a, **_k: None,
        set_extra=lambda *_a, **_k: None,
    )

# The compose module lives at the orchestrator package root.
_ORCH_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ORCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORCH_ROOT))

_R2_ENV_VARS = (
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ACCESS_KEY",
    "R2_SECRET_KEY",
)


def _reload_module(monkeypatch, env: dict[str, str]):
    """Clear all R2 credential env vars, set the given ones, and reload."""
    for var in _R2_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import worker_ffmpeg_compose

    return importlib.reload(worker_ffmpeg_compose)


def test_orchestrator_style_names_picked_up(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        {
            "R2_ACCESS_KEY_ID": "orch_access_id_value",
            "R2_SECRET_ACCESS_KEY": "orch_secret_value",
        },
    )
    assert mod.R2_ACCESS_KEY == "orch_access_id_value"
    assert mod.R2_SECRET_KEY == "orch_secret_value"


def test_legacy_names_picked_up(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        {
            "R2_ACCESS_KEY": "legacy_access_value",
            "R2_SECRET_KEY": "legacy_secret_value",
        },
    )
    assert mod.R2_ACCESS_KEY == "legacy_access_value"
    assert mod.R2_SECRET_KEY == "legacy_secret_value"


def test_orchestrator_style_wins_when_both_set(monkeypatch):
    mod = _reload_module(
        monkeypatch,
        {
            "R2_ACCESS_KEY_ID": "orch_access_id_value",
            "R2_SECRET_ACCESS_KEY": "orch_secret_value",
            "R2_ACCESS_KEY": "legacy_access_value",
            "R2_SECRET_KEY": "legacy_secret_value",
        },
    )
    assert mod.R2_ACCESS_KEY == "orch_access_id_value"
    assert mod.R2_SECRET_KEY == "orch_secret_value"


def test_falls_back_to_settings_when_env_vars_unset(monkeypatch):
    """pydantic-settings loads .env into config.settings only — it never
    exports those values back into os.environ. If neither the orchestrator
    nor the legacy env var names are set directly, _env_first must still
    pick up whatever config.settings has (i.e. whatever's in .env), rather
    than silently returning empty. This is exactly what broke real renders:
    R2_ENDPOINT was correctly set in .env but never reachable via a bare
    os.environ.get(), so the final-video upload failed with boto3's
    "Invalid endpoint" only after the entire compose had already run.
    """
    from config import settings

    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "settings_access_value")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "settings_secret_value")
    mod = _reload_module(monkeypatch, {})
    assert mod.R2_ACCESS_KEY == "settings_access_value"
    assert mod.R2_SECRET_KEY == "settings_secret_value"


def test_empty_when_nothing_configured_anywhere(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "")
    mod = _reload_module(monkeypatch, {})
    assert mod.R2_ACCESS_KEY == ""
    assert mod.R2_SECRET_KEY == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

"""Central kill-switch for the decommissioned HOSTKEY GPU server.

HOSTKEY (the on-prem 194.247.183.12 box) is permanently retired. Every code
path that used to dispatch work there must now route to a cloud provider
(Fish Audio for TTS, WaveSpeed for lipsync, fal.ai for T2V/I2V/Whisper/
MuseTalk/body shots). RunPod stays in the chain as a paid fallback.

Two env flags control this, and HOSTKEY is disabled when EITHER is truthy:

  - ``CAST_RENDER_HOSTKEY_DISABLED`` — truthy means "HOSTKEY off" (default on).
  - ``HOSTKEY_RENDER_ENABLED``       — falsey means "HOSTKEY off" (default off).

Defaults are chosen so HOSTKEY is OFF unless an operator explicitly turns it
back on by setting ``CAST_RENDER_HOSTKEY_DISABLED=false`` AND
``HOSTKEY_RENDER_ENABLED=true``. To re-enable HOSTKEY, flip both.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _is_truthy(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def hostkey_disabled() -> bool:
    """True when HOSTKEY must NOT be used.

    Disabled if ``CAST_RENDER_HOSTKEY_DISABLED`` is truthy (default True —
    HOSTKEY is decommissioned) OR ``HOSTKEY_RENDER_ENABLED`` is falsey
    (default False). Either guard on its own is sufficient to keep HOSTKEY
    off; both must be set to re-enable it.
    """
    # Default True: with the box gone, the absence of any override means off.
    disabled_flag = _is_truthy(
        os.environ.get("CAST_RENDER_HOSTKEY_DISABLED"), default=True
    )
    # Default False: HOSTKEY render is opt-in only.
    render_enabled = _is_truthy(
        os.environ.get("HOSTKEY_RENDER_ENABLED"), default=False
    )
    return disabled_flag or not render_enabled


def log_hostkey_skip(cloud_provider: str) -> None:
    """Emit the canonical skip log line. ``cloud_provider`` is the cloud
    target the caller is routing to instead (internal log only — never a
    user-facing string)."""
    logger.info("hostkey disabled by env flag, routing to %s", cloud_provider)

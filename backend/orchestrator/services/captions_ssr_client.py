"""Thin client for the SSR caption-render service (Step 11 / Step 12).

The service renders the caption track to a transparent overlay (PNG sequence
or alpha video) using the SAME preset table + per-token animation logic as the
editor preview, so the burnt captions match the preview pixel-for-pixel.

The composer (`cast_ffmpeg_composer.translate_timeline_to_ffmpeg`) calls
`render_overlay` per caption block, then alpha-composites the returned overlay
over the video. Every failure mode returns ``None`` (after a Sentry capture) so
the composer can transparently fall back to its drawtext path — a caption
render must never block the video render.

All timeouts/URLs/flags are env-overridable; nothing is hardcoded:

- ``CAPTIONS_SSR_URL``      (default ``http://captions-ssr:3030``)
- ``CAPTIONS_SSR_TIMEOUT_S``(default ``180``)
- ``CAPTIONS_SSR_FORMAT``   (default ``png-sequence``)
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import sentry_sdk

logger = logging.getLogger(__name__)

DEFAULT_SSR_URL = "http://captions-ssr:3030"
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_FORMAT = "png-sequence"


def _ssr_url() -> str:
    return (os.getenv("CAPTIONS_SSR_URL") or DEFAULT_SSR_URL).rstrip("/")


def _ssr_timeout_s() -> float:
    raw = os.getenv("CAPTIONS_SSR_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _ssr_format() -> str:
    return (os.getenv("CAPTIONS_SSR_FORMAT") or DEFAULT_FORMAT).strip().lower()


@dataclass
class SSROverlay:
    """Result of a successful overlay render.

    ``output_path`` is the directory of PNGs (png-sequence) or the .webm path
    (alpha-video). It lives on the shared ``captions_ssr_out`` docker volume so
    the worker can read it directly. ``fps``/``duration_in_frames`` describe the
    rendered sequence so the composer can feed it to FFmpeg at the right rate.
    """

    output_path: Path
    format: str
    width: int
    height: int
    fps: int
    duration_in_frames: int


def render_overlay(
    tokens: list[dict[str, Any]],
    preset_id: str,
    width: int,
    height: int,
    *,
    fps: int = 30,
    block: dict[str, Any] | None = None,
    fmt: str | None = None,
) -> SSROverlay | None:
    """Render a caption overlay for one block via the SSR service.

    Args:
        tokens: word timings in the SSR shape ``[{text, startMs, endMs}, ...]``.
            Times must be RELATIVE to the block (the first token starts at ~0);
            the composer re-anchors the overlay onto the absolute timeline.
        preset_id: caption preset id (e.g. ``hormozi_bold``). Falls back to the
            preset table's default server-side when unknown.
        width, height: overlay canvas size in pixels (the block's slot size).
        fps: frame rate for the rendered sequence.
        block: optional per-block style overrides mirroring the editor's
            CaptionsItem (fontFamily, highlightColor proxy via preset, pageMs,
            maxLines, captionWidth, ...). Forwarded verbatim under ``block``.
        fmt: output format override; defaults to ``CAPTIONS_SSR_FORMAT``.

    Returns:
        ``SSROverlay`` on success, or ``None`` on ANY failure (HTTP error,
        malformed JSON, timeout, network error). All failures are reported to
        Sentry; the caller falls back to the drawtext path.
    """
    if not tokens:
        return None

    payload: dict[str, Any] = {
        "tokens": tokens,
        "presetId": preset_id or "hormozi_bold",
        "canvas": {"width": int(width), "height": int(height), "fps": int(fps)},
        "format": (fmt or _ssr_format()),
    }
    if block:
        payload["block"] = block

    url = f"{_ssr_url()}/render"
    timeout = _ssr_timeout_s()

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
        if resp.status_code >= 400:
            # Engine-agnostic user-facing wording; details to logs/Sentry only.
            body = resp.text[:500]
            err = RuntimeError(
                f"caption overlay service returned {resp.status_code}: {body}"
            )
            sentry_sdk.capture_exception(err)
            logger.warning("captions-ssr render failed: %s", err)
            return None

        data = resp.json()
    except httpx.TimeoutException as e:
        sentry_sdk.capture_exception(e)
        logger.warning("captions-ssr render timed out after %.0fs", timeout)
        return None
    except httpx.HTTPError as e:
        # Covers connect errors, read errors, etc.
        sentry_sdk.capture_exception(e)
        logger.warning("captions-ssr network error: %s", e)
        return None
    except ValueError as e:
        # resp.json() on a non-JSON body raises ValueError (JSONDecodeError).
        sentry_sdk.capture_exception(e)
        logger.warning("captions-ssr returned non-JSON response")
        return None
    except Exception as e:  # noqa: BLE001 — never let a caption break the render
        sentry_sdk.capture_exception(e)
        logger.warning("captions-ssr unexpected error: %s", e)
        return None

    try:
        output_path = data["outputPath"]
        if not output_path:
            raise KeyError("outputPath")
        return SSROverlay(
            output_path=Path(str(output_path)),
            format=str(data.get("format") or _ssr_format()),
            width=int(data.get("width") or width),
            height=int(data.get("height") or height),
            fps=int(data.get("fps") or fps),
            duration_in_frames=int(data.get("durationInFrames") or 0),
        )
    except (KeyError, TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        logger.warning("captions-ssr response missing expected fields: %s", data)
        return None

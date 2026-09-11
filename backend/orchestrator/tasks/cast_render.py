"""Celery task for the two-pass render pipeline.

Pass 1: bake each bonded block via InfiniteTalk → MP4 with audio intrinsic
        Blocks are submitted in parallel and polled concurrently via asyncio.gather.
Pass 2: FFmpeg compose the full timeline (delegated to GPU worker)

A1 voice is editor-only — it is consumed by InfiniteTalk during baking.
The baked MP4 already contains the voice audio. FFmpeg SKIPS A1 in the
filter graph. Any code that mixes A1 into the final render is a bug.
"""
import asyncio
import base64
import logging
import json
import math
import os
import shutil
import subprocess
import tempfile
import uuid
import httpx
from datetime import datetime, timezone
from tasks import celery_app
from services import sentry
import sentry_sdk

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """Raised when a required deploy-time setting is missing or misconfigured."""


# InfiniteTalk cost estimate: $0.008 per second of output
INFINITETALK_COST_PER_SECOND = 0.008


# ── Caption style defaults (TikTok-style burnt captions) ──────────────────
# Mirrors the defaults in services/cast_ffmpeg_composer.py. These are applied
# when the editor element doesn't carry its own value, so even casts authored
# before the style refresh burn with the smaller, bolder, brighter look. Each
# knob is overridable via env var. fontSize is the unscaled base for the
# 480x848 test canvas; it is multiplied by min(sx, sy) below.
_CAPTION_DEFAULT_FONT_SIZE = 22
_CAPTION_DEFAULT_FONT_FAMILY = "Montserrat Bold"
_CAPTION_DEFAULT_HIGHLIGHT_COLOR = "#FFEB3B"
_CAPTION_DEFAULT_BASE_COLOR = "#FFFFFF"
_CAPTION_DEFAULT_STROKE_COLOR = "#000000"
_CAPTION_DEFAULT_STROKE_WIDTH = 2


def _caption_default_font_size() -> int:
    raw = os.getenv("CAPTION_FONT_SIZE")
    if not raw:
        return _CAPTION_DEFAULT_FONT_SIZE
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return _CAPTION_DEFAULT_FONT_SIZE


def _caption_default_stroke_width() -> int:
    raw = os.getenv("CAPTION_STROKE_WIDTH")
    if not raw:
        return _CAPTION_DEFAULT_STROKE_WIDTH
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError) as e:
        sentry_sdk.capture_exception(e)
        return _CAPTION_DEFAULT_STROKE_WIDTH


class RenderExtensionFailed(Exception):
    """Raised (and captured) when the timeline-level re-bake extension in
    ``_post_compose_audio_remux`` fails and we fall back to a held last
    frame. Synthetic — used only to give Sentry a typed breadcrumb so the
    "rebake attempted, failed, held frame" path is greppable per render.
    """


class LipsyncMuxAudioDrift(RuntimeError):
    """Raised when the audio fed to the lipsync engine and the audio the
    final mux lays down for the same block differ in duration beyond
    ``LIPSYNC_AUDIO_DRIFT_MAX_MS``. They must be the SAME audio — any drift
    means lips will lead/lag the muxed track on every frame of that block.
    """


class MotionDownloadTruncationError(RuntimeError):
    """Raised when a motion clip arrives shorter than the provider reported.

    A fal/Kling motion bake reports the duration it delivered (e.g. 12.07s),
    but a downstream hop (download, mux, conform) can silently shorten the
    file before it reaches normalize — the bug behind the persistent
    ``duration_undershoot`` / ``motion_clip_too_short`` Phase 3 failures.
    When the locally-materialised clip diverges from the provider-reported
    duration by more than the tolerance we fail loud here (and capture to
    Sentry) instead of shipping a half-length clip.
    """


class SpeakingBlockOutOfTolerance(Exception):
    """Raised when a freshly-baked SPEAKING block's duration falls outside
    the Phase 2 tolerance band around its slot.

    Speaking blocks are lipsync bakes: their length is driven by the audio,
    so a clip materially shorter than its slot means the lipsync engine
    dropped or truncated speech (lip desync downstream) and a clip much
    longer means it over-ran. We do NOT pad / stretch / reverse to fix it
    (that is what produced the desync + backward-walk artefacts). Instead
    we raise so the existing per-block retry-once pass re-bakes the block,
    and if it is still out of tolerance the block is marked ``failed`` —
    which fails the whole cast render rather than ship a desynced clip.

    Render_Quality_Duration_Validation.md §2.1: the tolerance band is measured
    against the block's TTS AUDIO duration, not its timeline slot. Slot-vs-audio
    drift is a planning defect surfaced separately as ``SlotAudioMismatch`` and
    is never resolved by re-baking.
    """


class SlotAudioMismatch(Exception):
    """Raised when a speaking block's timeline slot disagrees with its TTS
    audio duration beyond ``SLOT_AUDIO_MISMATCH_TOLERANCE`` AND the slot is
    fixed-length (cannot be resized to the audio).

    This is a PLANNING failure, not a render failure: the lipsync provider is
    rendering correctly against the audio, so re-baking would never close the
    gap. The block is failed before it enters the render queue (reason
    ``slot_audio_mismatch``) rather than re-baked. When the slot is resizable
    the planner instead mutates it to the audio length and no exception is
    raised.
    """


# Timeline-level duration tolerance. Under the overshoot+trim strategy
# every block is trimmed to exactly its slot, so the composed timeline
# must already equal the expected duration. This is the only slack we
# allow before failing the render (a single-frame rounding margin) — the
# old micro-pad / hold-frame / re-bake healing ladder is deleted.
_TL_MICRO_PAD_MAX_S = 0.25

# Phase 2 speaking-block tolerance band (fraction of slot). A speaking
# bake may legitimately land slightly under or over its slot; outside this
# band it is a defect. Lower bound −2% (a clip shorter than this dropped
# speech), upper bound +5% (a clip longer than this over-ran). When a clip
# is long but within +5% we END-trim the surplus (tail silence / trailing
# mouth-closed frames are the safe thing to drop); we NEVER pad a short
# clip up. Env-overridable for tuning without a redeploy.
_SPEAKING_TOLERANCE_UNDER_PCT = float(
    os.environ.get("SPEAKING_TOLERANCE_UNDER_PCT", "0.02")
)
_SPEAKING_TOLERANCE_OVER_PCT = float(
    os.environ.get("SPEAKING_TOLERANCE_OVER_PCT", "0.05")
)

# Render_Quality_Duration_Validation.md §2.1: the speaking-block tolerance
# band is measured against the block's TTS AUDIO duration, not its timeline
# slot. The slot is a planning-layer value; the lipsync bake's length is
# driven by the audio it is fed, so a bake that matches the audio is correct
# even when the slot disagrees. Slot-vs-audio drift is a PLANNING failure
# (handled by the reconcile pass below), never a render/re-bake failure.
#
# When |slot - audio| / audio exceeds this fraction the planner resizes the
# slot to the audio (preferred) or — when the slot is fixed-length and cannot
# be resized — surfaces a ``slot_audio_mismatch`` failure so the block never
# enters the render queue against a wrong slot.
_SLOT_AUDIO_MISMATCH_TOLERANCE = float(
    os.environ.get("SLOT_AUDIO_MISMATCH_TOLERANCE", "0.02")
)

# §2.2: when a resizable slot disagrees with the audio beyond tolerance we
# auto-resize the slot to the audio length. Disabling this (env=false) makes
# the planner fall back to the fixed-length path — a ``slot_audio_mismatch``
# failure instead of a silent resize — for operators who want every drift
# surfaced rather than auto-corrected.
_SLOT_AUDIO_AUTORESIZE_ENABLED = (
    os.environ.get("SLOT_AUDIO_AUTORESIZE_ENABLED", "true").strip().lower()
    not in ("0", "false", "no", "off")
)

# regression-2: the lipsync engine and the final mux MUST be driven by the
# exact same audio. The lipsync feed is loudness-normalized, edge-padded and
# trailing-silence-trimmed (lipsync_audio_prep), which shifts onset by ~100ms
# and changes length; if the mux then lays down a DIFFERENT (un-shifted)
# master, lips lead the audio on every block. We rewrite the mux source to
# the same prepared audio and assert their durations match within this many
# milliseconds. Env-overridable for tuning without a redeploy.
_LIPSYNC_AUDIO_DRIFT_MAX_MS = float(
    os.environ.get("LIPSYNC_AUDIO_DRIFT_MAX_MS", "30")
)


def _lipsync_audio_drift_max_s() -> float:
    """Drift tolerance in seconds, re-read per call so an env override set
    after import (tests, hot-reload) still takes effect."""
    try:
        return float(os.environ.get("LIPSYNC_AUDIO_DRIFT_MAX_MS", "30")) / 1000.0
    except (TypeError, ValueError) as exc:
        sentry_sdk.capture_exception(exc)
        return _LIPSYNC_AUDIO_DRIFT_MAX_MS / 1000.0


def _layout_template_composer_enabled() -> bool:
    """Step 4 feature flag: let the cast's layout template drive face/overlay.

    Default ON. Set ``LAYOUT_TEMPLATE_COMPOSER_ENABLED=false`` to fall back to
    purely block-level pip_layout (the pre-Step-4 behaviour) without a deploy.
    Read at call time so the flag can be flipped without restarting workers.
    """
    return (
        os.environ.get("LAYOUT_TEMPLATE_COMPOSER_ENABLED", "true").strip().lower()
        not in ("0", "false", "no", "off")
    )


def _resolve_video_bytes(result: dict) -> bytes:
    """Materialize the mp4 bytes from a render-dispatch result dict.

    Two on-the-wire shapes are accepted:
      * ``{"output_r2_key": "..."}`` — gpu-worker MuseTalk path, mp4 already
        in R2; fetch from the public CDN.
      * ``{"video": "<b64>"}``      — legacy InfiniteTalk path that returned
        the mp4 bytes inline as base64.
    """
    if not isinstance(result, dict):
        raise RuntimeError(f"Unknown bake result shape: {type(result).__name__}")
    if "output_r2_key" in result and result["output_r2_key"]:
        url = f"https://media.luminacast.com/{result['output_r2_key']}"
        try:
            r = httpx.get(url, timeout=60.0)
            r.raise_for_status()
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise
        return r.content
    if result.get("video"):
        try:
            return base64.b64decode(result["video"])
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise
    raise RuntimeError(
        f"Unknown bake result shape: keys={list(result.keys())}"
    )


async def _download_url_bytes(url: str, *, clip_duration_s: float = 0.0) -> bytes:
    """Fetch ``url`` into memory. Timeout is derived from the clip length
    (``max(120, 8.0 * clip_dur)``) rather than hardcoded, so a long
    refined clip on a cold CDN edge doesn't race a fixed deadline."""
    timeout = max(120.0, 8.0 * float(clip_duration_s or 0))
    async with httpx.AsyncClient(timeout=timeout) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        return resp.content


def _log_lipsync_refine_decision(
    *,
    block_id: str,
    render_id: str,
    decision,
    ran: bool,
    in_dur: float,
    out_dur: float,
    fal_req: str,
) -> None:
    """Emit the §C per-block lipsync-refine diagnostic line.

    Single grep-able format covering both routes (motion / speak):
    whether refine ran, the gate decision behind it, the input audio
    duration, the output clip duration, and the FAL request id.
    ``decision`` is a ``RefineDecision`` (route + reason); kept loose so
    a malformed object never breaks the render.
    """
    try:
        route = getattr(decision, "route", "?")
        reason = getattr(decision, "reason", "?")
        logger.info(
            "lipsync_refine block %s render %s: route=%s ran=%s "
            "reason=%s in_dur=%.3f out_dur=%.3f fal_req=%s",
            block_id, render_id, route, str(bool(ran)).lower(),
            reason, float(in_dur or 0.0), float(out_dur or 0.0),
            fal_req or "-",
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)


def _is_third_person_narrator(script: str) -> bool:
    """Detect a third-person narrator script for an avatar_action block.

    avatar_action scripts are lip-synced onto the avatar's mouth, so they
    must be FIRST-PERSON dialogue spoken by the avatar itself. When Opus
    slips and emits descriptive prose ("he reaches for...", "she walks
    toward..."), running it through TTS + lipsync produces a narrator
    voice over the avatar's moving lips — the renderer must skip lipsync
    and drop the audio in that case.
    """
    if not script or not script.strip():
        return False
    try:
        import re
        cleaned = re.sub(r"\[[^\]]+\]", " ", script)
        cleaned = re.sub(r"\([^)]+\)", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
        if not cleaned:
            return False
        word_count = len([w for w in re.findall(r"[a-z']+", cleaned) if w])
        if word_count < 6:
            return False
        third_person = [
            r"\bhe\b", r"\bshe\b", r"\bhis\b", r"\bher\b", r"\bhim\b",
            r"\bthey\b", r"\btheir\b", r"\bthem\b",
            r"\bthe avatar\b", r"\bthe model\b", r"\bthe person\b",
        ]
        if any(re.search(p, cleaned) for p in third_person):
            return True
        first_person = [
            r"\bi\b", r"\bi'm\b", r"\bi've\b", r"\bi'd\b", r"\bi'll\b",
            r"\bmy\b", r"\bmine\b", r"\bme\b",
            r"\bwe\b", r"\bwe're\b", r"\bwe've\b", r"\bour\b", r"\bours\b", r"\bus\b",
        ]
        direct_address = [
            r"\byou\b", r"\byou're\b", r"\byou've\b", r"\byour\b",
            r"\blet's\b",
        ]
        has_first = any(re.search(p, cleaned) for p in first_person)
        has_address = any(re.search(p, cleaned) for p in direct_address)
        if not has_first and not has_address:
            return True
        return False
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return False


# "prosody_only" removed (prosody family deferred). Legacy/stray values clamp
# to "tts_dialogue" via _normalize_voicing_mode — the render path below already
# handled the two identically.
_VALID_VOICING_MODES = ("tts_dialogue", "motion_sfx_only")


def _script_references_product(
    script: str | None, product_names: list[str] | None
) -> bool:
    """True iff the block script substring-matches any of the cast's
    product names (case-insensitive). Used to decide whether to route a
    speaking block through the product-conditioned bake — without this
    check, every speaking block would burn the elements premium even
    when the avatar is talking about something else entirely.

    We strip the product name down to its alphanumeric tokens of length
    >= 3 and require at least one token to appear in the script. This
    handles the common case where the product name is "OGX Argan Oil
    2-pack" and the script says "this argan oil" — substring match on
    the full name would miss it, but matching on the token "argan"
    succeeds. Tokens shorter than 3 chars are dropped to avoid false
    positives on short brand names ("LG", "HP") inside common words.
    """
    if not script or not product_names:
        return False
    haystack = script.lower()
    for name in product_names:
        try:
            tokens = [
                t.lower()
                for t in re.findall(r"[A-Za-z0-9]+", str(name or ""))
                if len(t) >= 3
            ]
        except Exception as e:
            sentry_sdk.capture_exception(e)
            tokens = []
        for t in tokens:
            if t in haystack:
                return True
    return False


# Category words that strongly imply the avatar is talking about (or
# holding) the cast's product even when the literal product name is
# never spoken. LLM-authored scripts very often use demonstratives
# ("this formula", "the spray") instead of the full SKU — substring
# match on these keeps the product-conditioned bake engaged for those
# blocks so the avatar's hands hold the correct product rather than a
# hallucinated bottle.
_PRODUCT_CATEGORY_WORDS = (
    "shampoo", "conditioner", "formula", "spray", "bottle", "lotion",
    "cream", "serum", "gel", "foam", "drops", "oil", "mist", "balm",
    "scrub",
)


def _script_mentions_product_category(script: str | None) -> bool:
    """True iff the block script contains any product-category word as
    a case-insensitive substring. Used as a broader sibling of
    ``_script_references_product`` so demonstrative references
    ("this formula", "the spray") still engage the
    product-conditioned bake.
    """
    if not script:
        return False
    try:
        haystack = script.lower()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return False
    for word in _PRODUCT_CATEGORY_WORDS:
        if word in haystack:
            return True
    return False


def _timeline_has_product_overlay_for_block(
    timeline: dict | None,
    block_id: str | None,
    block_start_s: float,
    block_end_s: float,
) -> bool:
    """True iff the timeline carries a product overlay element whose
    time window overlaps the given block slot. The overlay is the
    canonical "product is on-screen" signal — when the editor places a
    product PIP chip on top of a speaking slot, the avatar's hands need
    to hold a matching real product, not a hallucinated bottle.

    A product overlay is any image-typed element whose metadata carries
    a ``product_id`` field, or whose id begins with ``prod_blk_``
    (legacy naming). Element timing is read from the standard ``s``/``e``
    fields. Either an explicit metadata.block_id match OR a time-window
    overlap qualifies — both signals mean "this product is visible
    while this block is speaking".
    """
    if not timeline:
        return False
    try:
        tracks = (timeline or {}).get("tracks") or []
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return False
    for track in tracks:
        for el in (track or {}).get("elements") or []:
            try:
                meta = el.get("metadata") or {}
                el_id = str(el.get("id") or "")
                el_type = (el.get("type") or "").lower().strip()
                is_product_overlay = (
                    bool(meta.get("product_id"))
                    or el_id.startswith("prod_blk_")
                )
                if not is_product_overlay:
                    continue
                # Bonded V1/A1 elements aren't overlays.
                if meta.get("bonded"):
                    continue
                # If the overlay explicitly names this block, accept.
                if block_id and meta.get("block_id") == block_id:
                    return True
                # Otherwise require a time-window overlap with the
                # block's slot.
                try:
                    s = float(el.get("s") or 0)
                    e = float(el.get("e") or 0)
                except (TypeError, ValueError):
                    continue
                if e <= s:
                    continue
                # Standard half-open interval overlap.
                if s < block_end_s and e > block_start_s and el_type in (
                    "image", "overlay", "sticker", "logo"
                ):
                    return True
            except Exception as _ovx:
                sentry_sdk.capture_exception(_ovx)
                continue
    return False


def _normalize_voicing_mode(value: object) -> str:
    """Clamp a voicing_mode value to the supported set; default to tts_dialogue."""
    try:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _VALID_VOICING_MODES:
                return v
    except Exception as e:
        sentry_sdk.capture_exception(e)
    return "tts_dialogue"


async def resolve_effective_product_id(
    block,
    session,
    cast_id: str,
) -> str | None:
    """Resolve the product id the renderer should condition this block on.

    Priority order:
      1. ``block.product_id`` when set — authored intent wins.
      2. Else look up ``cast_products`` rows for the cast and use the row
         with the lowest ``position``. PRODUCT_DEMO blocks are very often
         authored as "demonstrate any product on this cast" and ship with
         ``product_id = NULL``; without this fallback the dispatcher routes
         them through the non-product-conditioned cascade and the lipsync
         engine paints in a hallucinated bottle instead of the user's
         actual product. When multiple cast products exist and the block
         has no explicit choice, we log a warning and pick the primary.

    Returns the resolved product id, or ``None`` when nothing resolves.
    Any DB error is captured to Sentry and treated as an unresolved
    product (the caller falls back to non-conditioned routing).
    """
    try:
        explicit = getattr(block, "product_id", None) if block is not None else None
        if explicit:
            return str(explicit)
        if not cast_id:
            return None
        from models.cast import CastProduct as _CP
        from sqlalchemy import select as _sa_select
        res = await session.execute(
            _sa_select(_CP)
            .where(_CP.cast_id == cast_id)
            .order_by(_CP.position.asc())
        )
        rows = list(res.scalars().all())
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning(
                "product gate: cast %s has %d products and block has no "
                "explicit product_id; picking primary (position=%d, "
                "product_id=%s)",
                cast_id, len(rows),
                getattr(rows[0], "position", 0),
                getattr(rows[0], "product_id", None),
            )
        return str(rows[0].product_id) if rows[0].product_id else None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None


async def resolve_voiceover_visual_sources(
    block_id: str,
    session,
    r2,
    cast_id: str,
) -> list[tuple[str, str]]:
    """Resolve EVERY viable visual source for a voiceover block's B-roll,
    in priority order (most-preferred first).

    Same priority order as ``resolve_voiceover_visual_source`` used to
    stop at, but this returns every match instead of just the first — the
    caller now tries each candidate in turn until one both bakes AND
    passes Phase 3 validation.

    Why: a single AI-picked stock clip (block.parallel_media[0]) can be a
    perfectly valid, downloadable video file that is nonetheless
    near-motionless — e.g. a locked-off product shot — which freezedetect
    correctly flags as clip_mostly_frozen. Retrying the SAME clip forever
    always fails the same way. But the script engine frequently attaches
    MORE than one candidate clip to a beat (block.parallel_media often has
    2+ entries for a multi-shot B-roll sequence), and a later candidate is
    often fine. Confirmed on render rnd_999f49e0c8d6 / block
    blk_f75c94e0833a: parallel_media[0] (a static stocking shot) was 87.5%
    frozen and hard-failed the block every render attempt, while
    parallel_media[1] (a gift-wrapping shot, already attached to the same
    block) had zero detected freeze — but was never tried.

      1. ("video"/"image", url) — a registered explicit asset
         (block.video_asset_id / block.image_asset_id). An explicit
         per-block override picked in the Script tab still wins outright.
      2. ("video"/"image", url) — the effective product's OWN uploaded
         media: every gallery ProductAsset (videos + images), rotated by
         block position so consecutive product beats don't all open on the
         same photo, then the product cover image. Ranked ABOVE generic
         stock so a "promote the Galaxy S26" cast actually shows the S26
         instead of a random stock phone. Only applies when a product
         resolves for the block (block.product_id, else the cast's primary
         product — see resolve_effective_product_id).
      3. ("video"/"image", url) — each entry in block.parallel_media, the
         AI-picked stock B-roll the script engine attaches to this beat.
      4. ("video"/"image", url) — stock_photo/stock_video blocks' pick
         (block.stock_media_url).
      5. ("image", url) — block.scene_image_key.
      Blocks with no resolvable product keep the old order (parallel_media
      → stock_media_url → scene). Videos are looped/trimmed to the slot by
      the caller; images are animated with a Ken-Burns pan-zoom (NEVER
      shown as a still — a still trips clip_mostly_frozen).

    Returns ``[]`` when nothing resolves (the caller then renders
    avatar-idle B-roll as the last resort). Any DB / resolver error is
    captured to Sentry and treated as "nothing further resolved" so the
    render still falls through to whatever candidates were found so far.
    """
    candidates: list[tuple[str, str]] = []
    try:
        from models.block import Block as _Block
        from models.product import Product as _Product
        from models.product_asset import ProductAsset as _ProductAsset
        from sqlalchemy import select as _sa_select

        blk = await session.get(_Block, block_id)

        # 1. Explicit block video asset, then explicit block image asset.
        video_asset_id = getattr(blk, "video_asset_id", None) if blk else None
        image_asset_id = getattr(blk, "image_asset_id", None) if blk else None
        for asset_id, want in ((video_asset_id, "video"), (image_asset_id, "image")):
            if not asset_id:
                continue
            asset = await session.get(_ProductAsset, asset_id)
            key = getattr(asset, "r2_key", None) if asset else None
            if key:
                url = r2.get_public_url(key)
                if url:
                    candidates.append((want, url))

        # Build each generic-stock source group first, then decide ordering
        # against the product's own media below. Previously these were
        # appended straight onto `candidates` and the real product shots
        # (added last) were never reached because Pexels always baked fine.
        generic_stock: list[tuple[str, str]] = []

        # (3) AI-picked stock B-roll (block.parallel_media) — ALL entries,
        # not just the first, so a bad first pick has a fallback candidate
        # already attached to the same block instead of skipping straight
        # to the avatar-idle Ken-Burns clip.
        parallel_media = getattr(blk, "parallel_media", None) if blk else None
        if isinstance(parallel_media, list):
            for pm in parallel_media:
                if not isinstance(pm, dict):
                    continue
                pm_url = pm.get("url")
                pm_kind = pm.get("kind")
                if pm_url and pm_kind in ("video", "photo"):
                    generic_stock.append(("video" if pm_kind == "video" else "image", pm_url))

        # (4) stock_photo / stock_video blocks carry their pick in
        # stock_media_url (a different field than parallel_media — set by
        # the auto-populate step, not the multi-angle b-roll attacher).
        stock_media_url = getattr(blk, "stock_media_url", None) if blk else None
        if stock_media_url:
            stock_media_kind = (getattr(blk, "stock_media_kind", None) or "").lower()
            generic_stock.append(("video" if stock_media_kind == "video" else "image", stock_media_url))

        # (2) The effective product's OWN uploaded media — EVERY gallery
        # asset (videos and images), not just the first video + cover. The
        # resolved list is rotated by block position so consecutive product
        # beats don't all open on the same photo.
        product_media: list[tuple[str, str]] = []
        product_id = await resolve_effective_product_id(blk, session, cast_id)
        if product_id:
            res = await session.execute(
                _sa_select(_ProductAsset)
                .where(_ProductAsset.product_id == product_id)
                .where(_ProductAsset.media_type.in_(("video", "image")))
                .order_by(
                    _ProductAsset.position.asc(),
                    _ProductAsset.created_at.asc(),
                    _ProductAsset.id.asc(),
                )
            )
            resolved: list[tuple[str, str]] = []
            for a in res.scalars().all():
                key = getattr(a, "r2_key", None)
                if not key:
                    continue
                url = r2.get_public_url(key)
                if not url:
                    continue
                resolved.append(("video" if a.media_type == "video" else "image", url))
            if resolved:
                offset = (getattr(blk, "position", 0) or 0) % len(resolved)
                product_media = resolved[offset:] + resolved[:offset]

            # Cover image as a final product-media entry when it isn't
            # already one of the gallery asset rows above.
            prod = await session.get(_Product, product_id)
            ckey = getattr(prod, "cover_image_key", None) if prod else None
            if ckey:
                curl = r2.get_public_url(ckey)
                if curl and all(curl != u for _, u in product_media):
                    product_media.append(("image", curl))

        # (5) Scene image key on the block — last-resort still.
        scene_tail: list[tuple[str, str]] = []
        scene_key = getattr(blk, "scene_image_key", None) if blk else None
        if scene_key:
            url = r2.get_public_url(scene_key)
            if url:
                scene_tail.append(("image", url))

        # Assemble in priority order: explicit override (already appended
        # above) → the product's own media → generic stock → scene still.
        candidates.extend(product_media)
        candidates.extend(generic_stock)
        candidates.extend(scene_tail)

        return candidates
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return candidates


async def resolve_voiceover_visual_source(
    block_id: str,
    session,
    r2,
    cast_id: str,
) -> tuple[str, str] | None:
    """Best-single-match convenience wrapper around
    ``resolve_voiceover_visual_sources`` — kept for existing callers/tests
    that only want the top candidate. The render loop itself now calls the
    plural form directly so it can fall through multiple candidates.
    """
    candidates = await resolve_voiceover_visual_sources(block_id, session, r2, cast_id)
    return candidates[0] if candidates else None


async def resolve_avatar_idle_image(
    cast_id: str,
    session,
    r2,
) -> str | None:
    """Resolve the cast avatar's look image for last-resort B-roll.

    When a voiceover block has no product/scene visual, we animate the
    avatar's face/look image with Ken Burns so the slot is never black.
    Returns a public image URL or ``None``. Errors are captured to Sentry.
    """
    try:
        from models.cast import Cast as _Cast
        from models.avatar import Avatar as _Avatar

        cst = await session.get(_Cast, cast_id) if cast_id else None
        avatar = (
            await session.get(_Avatar, cst.avatar_id)
            if cst and getattr(cst, "avatar_id", None)
            else None
        )
        key = getattr(avatar, "face_ref_key", None) if avatar else None
        if key:
            return r2.get_public_url(key)
        return None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return None


# EBU R128 loudness target — broadcast podcast standard. Used at every
# point we touch an audio track so the final video plays at a consistent
# loudness regardless of which bake path produced it.
LOUDNORM_I = "-16"
LOUDNORM_TP = "-1.5"
LOUDNORM_LRA = "11"
LOUDNORM_FILTER = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"


# Re-export the composer's hard-coded music default so the [music-mix] log
# emitted from the remux path reports the same number the composer uses.
try:
    from services.cast_ffmpeg_composer import DEFAULT_MUSIC_VOLUME as _MUSIC_DEFAULT_VOLUME
except Exception as _imp_exc:  # pragma: no cover - import guard
    sentry_sdk.capture_exception(_imp_exc)
    _MUSIC_DEFAULT_VOLUME = 0.0044


def _resolve_music_volume_with_source(
    elements: list[dict],
    cast_volume_override: float | None,
) -> tuple[float, str]:
    """Resolve background-music volume + its source for the remux path.

    Delegates to the composer's ``resolve_music_volume`` so element/cast/env
    precedence stays identical across both audio paths. Falls back to the env
    default on any import/resolution error rather than failing the render.
    """
    try:
        from services.cast_ffmpeg_composer import resolve_music_volume
        return resolve_music_volume(elements, cast_volume_override)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raw = os.getenv("MUSIC_DEFAULT_VOLUME")
        if raw:
            try:
                return max(0.0, min(1.0, float(raw))), "env_default"
            except (TypeError, ValueError):
                pass
        return _MUSIC_DEFAULT_VOLUME, "hardcoded"


def _two_pass_loudnorm(
    input_path: str,
    output_path: str,
    *,
    timeout_s: float,
) -> bool:
    """Run an EBU R128 two-pass loudnorm on ``input_path`` → ``output_path``.

    First pass measures, second pass applies measured offsets with
    ``linear=true`` so the gain is a single linear adjustment (no
    look-ahead pumping). Returns True on success.

    On any failure (probe parse, ffmpeg non-zero, timeout) the exception
    is captured in Sentry and False is returned — the caller falls back
    to passing the original audio through, since a normalisation miss is
    always less bad than failing the entire bake.
    """
    try:
        first_pass_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", input_path,
            "-af", f"{LOUDNORM_FILTER}:print_format=json",
            "-f", "null", "-",
        ]
        first = subprocess.run(
            first_pass_cmd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        if first.returncode != 0:
            raise RuntimeError(
                f"loudnorm pass-1 ffmpeg failed (rc={first.returncode}): "
                f"{(first.stderr or '')[-800:]}"
            )

        # ffmpeg prints the JSON block to stderr. Find the last '{...}'
        # block — earlier output (banner, frame stats) is noise.
        stderr_text = first.stderr or ""
        json_start = stderr_text.rfind("{")
        json_end = stderr_text.rfind("}")
        if json_start < 0 or json_end <= json_start:
            raise RuntimeError(
                "loudnorm pass-1 produced no JSON measurement block"
            )
        measured = json.loads(stderr_text[json_start:json_end + 1])
        measured_i = measured["input_i"]
        measured_tp = measured["input_tp"]
        measured_lra = measured["input_lra"]
        measured_thresh = measured["input_thresh"]
        target_offset = measured["target_offset"]

        second_pass_filter = (
            f"{LOUDNORM_FILTER}"
            f":measured_I={measured_i}"
            f":measured_TP={measured_tp}"
            f":measured_LRA={measured_lra}"
            f":measured_thresh={measured_thresh}"
            f":offset={target_offset}"
            f":linear=true"
            f":print_format=summary"
        )
        second_pass_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", input_path,
            "-af", second_pass_filter,
            "-ar", "48000", "-ac", "2",
            "-c:a", "pcm_s16le",
            output_path,
        ]
        second = subprocess.run(
            second_pass_cmd, capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        if second.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(
                f"loudnorm pass-2 ffmpeg failed (rc={second.returncode}): "
                f"{(second.stderr or '')[-800:]}"
            )
        return True
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "loudnorm two-pass failed on %s; falling back to source: %s",
            input_path, e,
        )
        return False


async def _mux_audio_into_clip(
    video_bytes: bytes,
    audio_url: str,
    *,
    duration_s: float,
) -> bytes:
    """Mux a remote audio track onto a silent (or audio-bearing) MP4.

    Used to attach the A1 voice into a T2V baked motion clip so the
    downstream concat-copy stage sees a uniform a/v stream layout. Without
    this, motion blocks ship silent and the final composed video loses
    audio entirely (the concat demuxer drops streams that aren't present
    in every input).

    The remote TTS source is run through an EBU R128 two-pass loudnorm
    targeting −16 LUFS BEFORE muxing, so every baked block hits the same
    broadcast loudness target and inter-block volume jumps cannot survive
    into the timeline. If the loudnorm pass fails for any reason we
    fall back to the raw source — a slight loudness mismatch is always
    better than a failed render.

    Returns the muxed MP4 bytes. Raises on subprocess / network failure.

    The video stream is ALWAYS preserved at full length. A short TTS track
    is silence-padded (``apad``) up to the video duration; an over-long TTS
    is bounded by ``-t <video_dur>``. We deliberately do NOT pass
    ``-shortest`` — it would clip the (longer) video down to the (shorter)
    audio, which is exactly the bug that made fal-delivered 12s motion clips
    arrive as ~5s at normalize (the voiceover TTS was ~5s). Cutting the
    render to match the audio is forbidden — only the cast script is held
    within limits, never the visual.
    """
    audio_mux_timeout = max(30.0, duration_s * 4)

    with tempfile.TemporaryDirectory(prefix="motion_mux_") as tmp:
        video_path = os.path.join(tmp, "in.mp4")
        audio_path = os.path.join(tmp, "in_audio")
        normalized_audio_path = os.path.join(tmp, "in_audio_loudnorm.wav")
        out_path = os.path.join(tmp, "out.mp4")

        with open(video_path, "wb") as f:
            f.write(video_bytes)

        async with httpx.AsyncClient(timeout=audio_mux_timeout) as http:
            resp = await http.get(audio_url, follow_redirects=True)
            resp.raise_for_status()
            with open(audio_path, "wb") as f:
                f.write(resp.content)

        # Two-pass loudnorm on the TTS source → −16 LUFS before muxing.
        # On failure we keep the original source so mux still proceeds.
        loudnorm_ok = await asyncio.to_thread(
            _two_pass_loudnorm,
            audio_path,
            normalized_audio_path,
            timeout_s=audio_mux_timeout,
        )
        audio_input = normalized_audio_path if loudnorm_ok else audio_path

        # Hold the video at its real length, not at ``duration_s`` (a slot
        # estimate). Motion bakes overshoot the slot on purpose; trimming to
        # the slot here would discard the head-trim surplus normalize expects.
        video_dur = await _probe_bytes_duration_s(video_bytes)
        if video_dur <= 0:
            video_dur = float(duration_s or 0.0)

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_input,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            # Pad short audio with trailing silence so it never drags the
            # video down; bound the muxed output to the video duration so an
            # over-long TTS doesn't extend it. Video is stream-copied intact.
            "-af", "apad",
            "-t", f"{video_dur:.3f}",
            "-map", "0:v:0",
            "-map", "1:a:0",
            out_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=audio_mux_timeout, check=True,
            )
        except subprocess.CalledProcessError as e:
            sentry_sdk.capture_exception(e)
            raise
        except subprocess.TimeoutExpired as e:
            sentry_sdk.capture_exception(e)
            raise
        if result.returncode != 0:
            raise RuntimeError(
                f"audio mux ffmpeg failed: {result.stderr[-1500:]}"
            )

        with open(out_path, "rb") as f:
            return f.read()


async def _mux_silent_audio_into_clip(
    video_bytes: bytes,
    *,
    duration_s: float,
) -> bytes:
    """Add a silent AAC track to a video clip that has no audio stream.

    Used for silent action beats (empty-script avatar_action blocks) so
    the downstream concat-copy compose pass sees a uniform a/v stream
    layout — without this, the concat demuxer drops audio for the
    entire timeline once a single audio-less clip is present.

    Returns the muxed MP4 bytes. Raises on subprocess failure.
    """
    silent_mux_timeout = max(30.0, duration_s * 4)
    with tempfile.TemporaryDirectory(prefix="silent_mux_") as tmp:
        video_path = os.path.join(tmp, "in.mp4")
        out_path = os.path.join(tmp, "out.mp4")
        with open(video_path, "wb") as f:
            f.write(video_bytes)
        # Bound the (infinite) anullsrc to the video's real length with an
        # explicit ``-t`` instead of relying on ``-shortest``: the video is
        # stream-copied at full length and the silent track matches it exactly.
        video_dur = await _probe_bytes_duration_s(video_bytes)
        if video_dur <= 0:
            video_dur = float(duration_s or 0.0)
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", f"{video_dur:.3f}",
            "-map", "0:v:0",
            "-map", "1:a:0",
            out_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=silent_mux_timeout, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"silent audio mux ffmpeg failed: {result.stderr[-1500:]}"
            )
        with open(out_path, "rb") as f:
            return f.read()


FPS = 30  # InfiniteTalk renders at 30fps; round desired durations to 1/FPS s.


def _slot_duration_for_block(timeline: dict, block_id: str, fallback_s: float) -> float:
    """Return the V1 slot duration (e - s) for ``block_id`` from the timeline.

    Used by the post-bake normalize step so every baked clip is hard-trimmed
    to its slot exactly — protects against providers (HOSTKEY InfiniteTalk
    in particular) that sometimes return a clip a few hundred ms longer
    than the audio they were given. Falls back to ``fallback_s`` when the
    V1 element can't be located (legacy timelines).
    """
    if not timeline or not isinstance(timeline, dict):
        return float(fallback_s or 0)
    target_id = f"v1_{block_id}"
    fallback_match: tuple[float, float] | None = None
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if (track.get("type") or "").lower() != "video":
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            eid = el.get("id") or ""
            # Exact V1 id match wins immediately.
            if eid == target_id:
                try:
                    s = float(el.get("s") or 0)
                    e = float(el.get("e") or 0)
                    if e > s:
                        return e - s
                except (TypeError, ValueError) as ex:
                    sentry_sdk.capture_exception(ex)
                    continue
            # Fallback ONLY for V1-shaped ids (covers legacy re-export id
            # variants). Never match cap_/prod_/pm_/etc elements via
            # metadata.block_id — they intentionally have shorter slots, so
            # matching them would trim baked clips short and leave black
            # tails at block boundaries.
            if fallback_match is None and eid.startswith("v1_"):
                meta_block = (el.get("metadata") or {}).get("block_id")
                if meta_block == block_id:
                    try:
                        s = float(el.get("s") or 0)
                        e = float(el.get("e") or 0)
                        if e > s:
                            fallback_match = (s, e)
                    except (TypeError, ValueError) as ex:
                        sentry_sdk.capture_exception(ex)
                        continue
    if fallback_match is not None:
        return fallback_match[1] - fallback_match[0]
    return float(fallback_s or 0)


def _canvas_dims_for_render(timeline: dict) -> tuple[int, int, int]:
    """Return (canvas_width, canvas_height, fps) for the active render.

    Falls back to (1114, 828, 30) — that's what the live renders observed
    in production land on when the timeline is missing the explicit fields.
    Bug PR #64 was triggered by a HOSTKEY block that came back at
    720x1280@25fps when the canvas was 1114x828@30fps; centralising the
    canvas read here keeps the normalize call sites uniform.
    """
    tl = timeline or {}
    try:
        cw = int(tl.get("compositionWidth") or 1114)
    except (TypeError, ValueError):
        cw = 1114
    try:
        ch = int(tl.get("compositionHeight") or 828)
    except (TypeError, ValueError):
        ch = 828
    try:
        fps = int(tl.get("fps") or 30)
    except (TypeError, ValueError):
        fps = 30
    return cw, ch, fps


def _infinitetalk_sizes_map(output_format: str | None = None, is_landscape: bool = False) -> dict[str, tuple[int, int]]:
    """Return map of {"480p": (w, h), "720p": (w, h), "1080p": (w, h)} for the layout."""
    if output_format == "16:9" or is_landscape:
        return {"480p": (848, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
    elif output_format == "1:1":
        return {"480p": (480, 480), "720p": (720, 720), "1080p": (1080, 1080)}
    elif output_format == "4:5":
        return {"480p": (480, 600), "720p": (720, 900), "1080p": (1080, 1350)}
    else:
        return {"480p": (480, 848), "720p": (720, 1280), "1080p": (1080, 1920)}



def _expected_timeline_duration_s(timeline: dict) -> float:
    """Maximum element ``e`` across every track — i.e. the timeline's
    intended end-of-cast in seconds. Used by the post-compose remux to
    detect cases where compose's output is shorter than the timeline
    expects (typically because audio mux dropped a trailing block).
    """
    if not timeline or not isinstance(timeline, dict):
        return 0.0
    longest = 0.0
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            try:
                e = float(el.get("e") or 0)
            except (TypeError, ValueError):
                continue
            if e > longest:
                longest = e
    return longest


def _audio_track_elements(timeline: dict) -> list[dict]:
    """All elements on the audio track of a timeline snapshot, in their
    original order. Returns [] when the snapshot has no audio track or
    every audio element is missing a src.
    """
    if not timeline or not isinstance(timeline, dict):
        return []
    out: list[dict] = []
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if (track.get("type") or "").lower() != "audio":
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            src = (el.get("props") or {}).get("src") or ""
            if not src:
                continue
            out.append(el)
    return out


_AMBIENCE_GAP_BRIDGE_S = 1.5   # merge same-env runs separated by <= this
_AMBIENCE_MIN_RUN_S = 2.0      # ignore runs shorter than this


def _timeline_block_spans(timeline: dict) -> dict[str, tuple[float, float]]:
    """``{block_id: (min_start_s, max_end_s)}`` across every element that
    carries a ``metadata.block_id`` and a valid slot (``e > s``)."""
    spans: dict[str, list[float]] = {}
    if not timeline or not isinstance(timeline, dict):
        return {}
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            bid = (el.get("metadata") or {}).get("block_id")
            if not bid:
                continue
            try:
                s = float(el.get("s") or 0)
                e = float(el.get("e") or 0)
            except (TypeError, ValueError):
                continue
            if e <= s:
                continue
            cur = spans.get(bid)
            if cur is None:
                spans[bid] = [s, e]
            else:
                cur[0] = min(cur[0], s)
                cur[1] = max(cur[1], e)
    return {bid: (v[0], v[1]) for bid, v in spans.items()}


def _coalesce_env_runs(
    spans: dict[str, tuple[float, float]],
    block_env: dict[str, str],
    *,
    gap_bridge_s: float = _AMBIENCE_GAP_BRIDGE_S,
    min_run_s: float = _AMBIENCE_MIN_RUN_S,
) -> list[tuple[str, float, float]]:
    """Order blocks by start time and merge consecutive blocks that share an
    environment (bridging gaps up to ``gap_bridge_s``) into
    ``[(env, start_s, end_s), ...]``, dropping runs shorter than
    ``min_run_s``. Environment is lower-cased; missing → ``""``.
    """
    ordered = sorted(spans.items(), key=lambda kv: kv[1][0])
    runs: list[list] = []  # [env, start, end]
    for bid, (s, e) in ordered:
        env = (block_env.get(bid) or "").strip().lower()
        if runs and runs[-1][0] == env and s - runs[-1][2] <= gap_bridge_s:
            runs[-1][2] = max(runs[-1][2], e)
        else:
            runs.append([env, s, e])
    return [(env, s, e) for env, s, e in runs if e - s >= min_run_s]


async def _build_ambience_plan(
    *, timeline: dict, cast_id: str, factory, render_id: str,
) -> list[tuple[float, float, str, float, str]]:
    """Derive per-scene ambience-bed segments from the timeline + each block's
    scene environment.

    Returns ``[(start_s, end_s, url, volume, env), ...]`` — one entry per
    contiguous run of blocks that share a ``room``/``outdoor`` environment
    (``studio`` gets no bed). Empty when ``SCENE_AMBIENCE_ENABLED`` is off, no
    block has a bedded environment, or the timeline carries no block spans.
    ``_post_compose_audio_remux`` loops each ``url`` to fill its span and mixes
    it as a ducked-under-voice bed, like background music but quieter.
    """
    from services.ambience_library import scene_ambience_enabled, for_environment

    if not scene_ambience_enabled():
        return []

    spans = _timeline_block_spans(timeline)
    if not spans:
        return []

    # Resolve each block's scene environment (Block -> AvatarLook.environment).
    from models.block import Block
    from models.cast import Cast
    from models.avatar_look import AvatarLook, DEFAULT_ENVIRONMENT
    from sqlalchemy import select as _sa_select

    block_env: dict[str, str] = {}
    try:
        async with factory() as _sess:
            # Fallback for blocks with no explicit scene (avatar_look_id NULL —
            # the common case when the cast never picked a "Cast scene"): the
            # cast's avatar default look, mirroring the render's own look
            # resolution (block.avatar_look_id -> avatar is_default). Without
            # this every default-scene cast resolved to studio and got no bed.
            default_env = DEFAULT_ENVIRONMENT
            _cast = await _sess.get(Cast, cast_id)
            if _cast is not None and getattr(_cast, "avatar_id", None):
                _def_env = (await _sess.execute(
                    _sa_select(AvatarLook.environment)
                    .where(AvatarLook.avatar_id == _cast.avatar_id)
                    .where(AvatarLook.is_default.is_(True))
                    .limit(1)
                )).scalars().first()
                if _def_env:
                    default_env = _def_env

            brows = (await _sess.execute(
                _sa_select(Block.id, Block.avatar_look_id).where(
                    Block.id.in_(list(spans.keys()))
                )
            )).all()
            look_ids = {lid for _, lid in brows if lid}
            look_env: dict[str, str] = {}
            if look_ids:
                lrows = (await _sess.execute(
                    _sa_select(AvatarLook.id, AvatarLook.environment).where(
                        AvatarLook.id.in_(list(look_ids))
                    )
                )).all()
                look_env = {lid: (env or DEFAULT_ENVIRONMENT) for lid, env in lrows}
            for bid, lid in brows:
                block_env[bid] = look_env.get(lid, default_env) if lid else default_env
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        logger.warning(
            "Render %s: ambience env lookup failed (%s); shipping without a bed",
            render_id, exc,
        )
        return []

    out: list[tuple[float, float, str, float, str]] = []
    for env, s, e in _coalesce_env_runs(spans, block_env):
        entry = for_environment(env)
        if entry is None:
            continue
        out.append((float(s), float(e), entry.url, float(entry.default_volume), env))

    if out:
        logger.info(
            "Render %s: ambience plan — %d segment(s): %s",
            render_id, len(out),
            ", ".join(f"{env}[{s:.1f}-{e:.1f}s]" for s, e, _u, _v, env in out),
        )
    return out


def _rewrite_mux_audio_to_lipsync(
    timeline: dict,
    *,
    lipsync_audio_by_block: dict[str, str],
    render_id: str,
) -> int:
    """regression-2: point every audio-track element at the SAME URL the
    lipsync engine was driven with.

    The lipsync feed is loudness-normalized, edge-padded and silence-trimmed
    (``lipsync_audio_prep``), which shifts onset ~100ms and changes length.
    The compose + remux passes read the audio element's ``props.src`` — if
    that's still the un-shifted master, lips lead the muxed audio on every
    block. Rewriting the src to the recorded lipsync-driver URL makes the two
    byte-identical, so the lips can't drift from a track they were synced to.

    Mutates ``timeline`` in place. Returns the number of elements rewritten.
    Elements without a recorded driver URL (no lipsync feed, e.g. music) are
    left untouched.
    """
    rewritten = 0
    if not lipsync_audio_by_block:
        return 0
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if (track.get("type") or "").lower() != "audio":
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            meta = el.get("metadata") or {}
            # Only the block's spoken VOICE follows the lipsync driver. SFX
            # accents and per-block music also carry the block_id, but
            # repointing their src to the voice URL both loses the effect and
            # doubles that block's narration in the mix ("two voices at once"
            # exactly where the SFX should have been).
            if (meta.get("kind") or "").lower() in ("sfx", "music"):
                continue
            if (meta.get("track_type") or "").lower() in ("audio_sfx", "audio_music"):
                continue
            bid = meta.get("block_id") or ""
            driver_url = lipsync_audio_by_block.get(bid)
            if not driver_url:
                continue
            props = el.get("props")
            if not isinstance(props, dict):
                props = {}
                el["props"] = props
            if props.get("src") != driver_url:
                props["src"] = driver_url
                rewritten += 1
    if rewritten:
        logger.info(
            "Render %s: rewrote %d audio element src(s) to the lipsync "
            "driver audio so the mux matches what the engine synced to",
            render_id, rewritten,
        )
    return rewritten


async def _assert_lipsync_mux_audio_identity(
    timeline: dict,
    *,
    lipsync_audio_by_block: dict[str, str],
    render_id: str,
) -> None:
    """regression-2: after the rewrite, verify the lipsync driver and the
    mux source for each block resolve to durations within
    ``LIPSYNC_AUDIO_DRIFT_MAX_MS``.

    Post-rewrite they share the same URL, so this is a cheap confirmation
    that nothing else re-pointed the element. Any residual drift raises
    ``LipsyncMuxAudioDrift`` (captured to Sentry) so the render fails loud
    rather than shipping out-of-sync lips.
    """
    tol_s = _lipsync_audio_drift_max_s()
    mux_by_block: dict[str, str] = {}
    for el in _audio_track_elements(timeline):
        meta = el.get("metadata") or {}
        if (meta.get("kind") or "").lower() in ("sfx", "music"):
            continue
        if (meta.get("track_type") or "").lower() in ("audio_sfx", "audio_music"):
            continue
        bid = meta.get("block_id") or ""
        if bid and bid not in mux_by_block:
            mux_by_block[bid] = (el.get("props") or {}).get("src") or ""

    for bid, driver_url in lipsync_audio_by_block.items():
        mux_src = mux_by_block.get(bid)
        if not mux_src or not driver_url:
            continue
        if mux_src == driver_url:
            # Identical URL → identical bytes → zero drift. The rewrite
            # already guaranteed this for every block it touched; skip the
            # redundant double-probe over the network.
            continue
        lip_dur = await _probe_audio_duration_s(driver_url)
        mux_dur = await _probe_audio_duration_s(mux_src)
        if lip_dur <= 0 or mux_dur <= 0:
            # A probe miss is not proof of drift; the rewrite already made
            # the URLs identical. Skip rather than fail on a transient probe.
            continue
        drift_s = abs(lip_dur - mux_dur)
        if drift_s > tol_s:
            msg = (
                f"Lipsync/mux audio duration drift: {lip_dur:.3f}s vs "
                f"{mux_dur:.3f}s (drift {drift_s * 1000:.0f}ms > "
                f"{tol_s * 1000:.0f}ms) for block {bid} render {render_id}"
            )
            sentry_sdk.capture_message(msg)
            ex = LipsyncMuxAudioDrift(msg)
            sentry_sdk.capture_exception(ex)
            raise ex


def _apply_real_block_durations(
    timeline: dict,
    *,
    block_durations: dict[str, float],
    render_id: str,
    fps: int = 30,
) -> tuple[dict, bool, list[str]]:
    """PR #75: rewrite ``timeline`` so every element's ``s``/``e`` reflects
    the REAL per-block duration (the ``using=`` value resolved from the
    refreshed TTS + user edit), never a proportionally-scaled placeholder
    and never a render-side clamp against a target.

    The user's requested duration is a hint for the script writer — it
    must NOT cap the render. Users edit/extend blocks after script
    generation; those extensions have to render. PR #71's earlier
    "drop trailing blocks if sum > target" branch was wrong and is
    removed here.

    The new contract:
      * Bonded blocks are repositioned in their original start order,
        each sitting at the next ``using=`` second.
      * Non-bonded elements carrying ``metadata.block_id`` ride along
        with their parent block — their original offset/duration within
        the OLD block range is mapped proportionally into the NEW range,
        so captions/product overlays/parallel media stay aligned with
        the block they belong to.
      * Elements without a recognised ``block_id`` are left untouched.
      * Every block is kept. No block is ever dropped at render time
        because of a duration target.

    Returns ``(timeline, rewritten, dropped_block_ids)``.
      - ``rewritten`` is True iff any element s/e was changed.
      - ``dropped_block_ids`` is always ``[]`` (kept in the signature
        for backwards compatibility with the caller's tuple unpacking).

    Wrapped end-to-end in try/except by the caller — never raises.
    """
    if not timeline or not isinstance(timeline, dict):
        return timeline, False, []
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return timeline, False, []
    if not block_durations:
        return timeline, False, []

    snap_step = 1.0 / float(fps or 30)

    def _snap(x: float) -> float:
        if x <= 0:
            return 0.0
        return round(x / snap_step) * snap_step

    # ── Phase 1: discover every block's OLD time window. ───────────────
    # We use the bonded V1 (preferred) or A1 element to define the block
    # window, falling back to the widest [s, e] span across any element
    # tagged with that block_id when no bonded element is present.
    old_windows: dict[str, tuple[float, float]] = {}
    block_order: list[str] = []  # preserve original timeline order
    for tr in tracks:
        if not isinstance(tr, dict):
            continue
        for el in tr.get("elements") or []:
            if not isinstance(el, dict):
                continue
            meta = el.get("metadata") or {}
            bid = meta.get("block_id")
            if not bid:
                continue
            try:
                s_v = float(el.get("s") or 0)
                e_v = float(el.get("e") or 0)
            except (TypeError, ValueError) as _se_exc:
                sentry_sdk.capture_exception(_se_exc)
                continue
            if e_v <= s_v:
                continue
            # Bonded element wins (defines the canonical window); else
            # widen the existing tentative window.
            is_bonded = bool(meta.get("bonded"))
            if bid not in old_windows:
                old_windows[bid] = (s_v, e_v)
                block_order.append(bid)
            else:
                if is_bonded:
                    old_windows[bid] = (s_v, e_v)
                else:
                    cs, ce = old_windows[bid]
                    old_windows[bid] = (min(cs, s_v), max(ce, e_v))
    if not old_windows:
        return timeline, False, []

    # Sort blocks by their original start time so the rewrite preserves
    # the order the user / outline planner intended.
    block_order = sorted(
        old_windows.keys(), key=lambda b: old_windows[b][0]
    )

    # ── Phase 2: assign NEW windows using REAL using= durations. ──────
    # Every block is kept. The duration target is a script-writer hint,
    # not a render-side cap (PR #75).
    new_windows: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    sum_real = 0.0
    for bid in block_order:
        # Real duration for this block. Blocks the bake loop did not
        # supply a fresh value for keep their original window length so
        # the snapshot remains usable (e.g. overlays with no bonded
        # bake counterpart).
        old_s, old_e = old_windows[bid]
        old_dur = max(0.0, old_e - old_s)
        real = float(block_durations.get(bid) or old_dur)
        real = max(0.0, real)
        sum_real += real
        new_s = _snap(cursor)
        new_e = _snap(cursor + real)
        new_windows[bid] = (new_s, new_e)
        cursor = new_e

    # ── Phase 3: rewrite every element. ───────────────────────────────
    # For elements with a block_id we remap (s, e) into the new window,
    # preserving their relative position inside the OLD window so
    # overlays / captions / product images keep their slot inside the
    # block. Elements without a block_id are left untouched.
    rewritten = False
    new_tracks: list[dict] = []
    for tr in tracks:
        if not isinstance(tr, dict):
            new_tracks.append(tr)
            continue
        new_elements: list[dict] = []
        for el in tr.get("elements") or []:
            if not isinstance(el, dict):
                new_elements.append(el)
                continue
            meta = el.get("metadata") or {}
            bid = meta.get("block_id")
            if not bid:
                new_elements.append(el)
                continue
            if bid not in new_windows:
                # No window resolved (e.g. zero-duration element); leave
                # the element as-is — never drop blocks at render time.
                new_elements.append(el)
                continue
            try:
                s_v = float(el.get("s") or 0)
                e_v = float(el.get("e") or 0)
            except (TypeError, ValueError) as _rw_se_exc:
                sentry_sdk.capture_exception(_rw_se_exc)
                new_elements.append(el)
                continue
            old_s, old_e = old_windows[bid]
            new_s_blk, new_e_blk = new_windows[bid]
            old_dur = max(1e-6, old_e - old_s)
            new_dur = max(0.0, new_e_blk - new_s_blk)
            rel_s = max(0.0, s_v - old_s) / old_dur
            rel_e = max(0.0, e_v - old_s) / old_dur
            mapped_s = _snap(new_s_blk + rel_s * new_dur)
            mapped_e = _snap(new_s_blk + rel_e * new_dur)
            if mapped_e <= mapped_s:
                mapped_e = _snap(mapped_s + snap_step)
            new_el = dict(el)
            if abs(mapped_s - s_v) > 1e-6 or abs(mapped_e - e_v) > 1e-6:
                rewritten = True
            new_el["s"] = mapped_s
            new_el["e"] = mapped_e
            new_elements.append(new_el)
        new_tr = dict(tr)
        new_tr["elements"] = new_elements
        new_tracks.append(new_tr)

    if not rewritten:
        return timeline, False, []

    new_timeline = dict(timeline)
    new_timeline["tracks"] = new_tracks
    logger.info(
        "[apply_real_block_durations] render=%s total real duration "
        "=%.2fs across %d blocks",
        render_id, sum_real, len(new_windows),
    )
    sentry_sdk.set_tag("apply_real_block_durations", "1")
    return new_timeline, True, []


async def _measure_baked_block_durations(
    timeline: dict,
    baked_urls: dict[str, str],
    render_id: str,
) -> dict[str, float]:
    """Probe each baked lipsync clip and return ``{block_id: actual_seconds}``.

    PR-H: the timeline ``e:`` values are first laid down from the TTS
    ``using=`` estimate, but the lipsync provider returns a clip that is a few
    tens of milliseconds shorter/longer than that estimate. Because each
    block's slot start is the previous block's end (cursor accumulation in
    ``_apply_real_block_durations``), that per-block gap accumulates and reads
    as lipsync drift that snaps back at every cut. Re-measuring the baked
    clips with ffprobe and re-applying those real durations makes the timeline
    slot match the footage so audio (placed at the absolute slot start) stays
    locked to the lips.

    Walks the bonded V1 elements (``baked_urls`` is keyed by element id, the
    timeline maps element id -> ``metadata.block_id``). Blocks whose clip can't
    be probed are omitted so the caller keeps their original slot.
    """
    if not timeline or not isinstance(timeline, dict) or not baked_urls:
        return {}

    eid_to_block: dict[str, str] = {}
    for tr in timeline.get("tracks") or []:
        if not isinstance(tr, dict):
            continue
        for el in tr.get("elements") or []:
            if not isinstance(el, dict):
                continue
            meta = el.get("metadata") or {}
            if not meta.get("bonded"):
                continue
            eid = el.get("id") or ""
            bid = meta.get("block_id") or ""
            if eid and bid and eid in baked_urls:
                eid_to_block[eid] = bid

    measured: dict[str, float] = {}
    for eid, bid in eid_to_block.items():
        dur = await _probe_audio_duration_s(baked_urls[eid])
        if dur and dur > 0:
            measured[bid] = dur
            logger.info(
                "[measure_baked] render=%s block=%s measured=%.3fs",
                render_id, bid, dur,
            )
    return measured


async def _assert_compose_video_matches_expected(
    *,
    actual_duration: float,
    expected_duration: float,
    render_id: str,
) -> None:
    """Assert the composed timeline video already equals ``expected_duration``.

    Phase 1 inverts the old behaviour. Previously a short composed timeline
    was *healed* with a micro-pad / hold-frame / re-bake ladder. That ladder
    is deleted: under the overshoot+trim strategy every block is baked
    longer than its slot and trimmed to exactly slot length, so the composed
    timeline duration must already equal the sum of slot durations.

    A material undershoot at this point is therefore a real defect (a block
    that failed the overshoot contract, or a compose bug) — NOT something to
    paper over by padding/holding/reversing the tail (the "lady walks
    backwards" artefact this code originally chased). We FAIL the render
    instead, so the defect surfaces rather than shipping a padded clip.

    Tolerance: ``_TL_MICRO_PAD_MAX_S`` (a single-frame rounding margin).
    """
    delta = expected_duration - actual_duration
    if delta <= _TL_MICRO_PAD_MAX_S:
        # Within frame-rounding margin — the final ``-t`` clamp lands it.
        return

    pct = (delta / expected_duration) if expected_duration > 0 else 0.0
    err = RenderExtensionFailed(
        f"render {render_id}: composed timeline is {actual_duration:.3f}s "
        f"but expected {expected_duration:.3f}s (short by {delta:.3f}s / "
        f"{pct:.0%}). Under the overshoot+trim strategy every block is "
        f"trimmed to exactly its slot, so a material undershoot means a "
        f"block failed the overshoot contract or compose dropped frames. "
        f"Refusing to pad/hold/reverse — failing the render."
    )
    sentry_sdk.capture_exception(err)
    logger.error(
        "[remux] render %s composed-timeline undershoot %.3fs (%.0f%%); "
        "failing render (no pad/hold/reverse)",
        render_id, delta, pct * 100,
    )
    raise err


async def _post_compose_audio_remux(
    *,
    r2,
    output_key: str,
    timeline: dict,
    render_id: str,
    cast_music_volume: float | None = None,
    ambience_plan: list[tuple[float, float, str, float, str]] | None = None,
) -> None:
    """Defensive post-compose audio remux.

    Compose layers the bonded blocks via concat, but its audio mux
    sometimes terminates early when block durations don't sum to the
    timeline's last element ``e`` — observed in PR #64's reference render
    (last 7s silent, final video 3.4s short). This helper:

      1. Downloads compose's final.mp4 from R2.
      2. Probes its actual duration vs the timeline's expected duration.
      3. Re-builds the audio mix from scratch from the timeline's audio
         track, with each element delayed via ``adelay`` to its absolute
         slot start so trim outcomes can't drop the trailing block.
      4. Asserts the composed video already equals the expected duration
         (``_assert_compose_video_matches_expected``). Under the
         overshoot+trim strategy every block is trimmed to exactly its
         slot, so a material undershoot is a real defect and FAILS the
         render — it is never padded / held / reversed (the "lady walks
         backwards" artefact this code exists to prevent).
      5. Re-uploads the remuxed mp4 over the same key.

    ``ambience_plan`` (from ``_build_ambience_plan``, gated on
    ``SCENE_AMBIENCE_ENABLED``) adds a looped, ducked-under-voice atmosphere
    bed per room/outdoor scene run. When it is empty the filtergraph is
    byte-identical to the pre-ambience version.

    Wrapped in try/except by the caller; this function may raise.
    """
    audio_elements = _audio_track_elements(timeline)
    if not audio_elements:
        # Without an audio track on the snapshot we have nothing
        # authoritative to mux from — leave compose's output alone.
        logger.info(
            "Render %s post-compose remux: no audio track elements; skipping",
            render_id,
        )
        return

    expected_duration = _expected_timeline_duration_s(timeline)
    if expected_duration <= 0:
        logger.info(
            "Render %s post-compose remux: expected duration unknown; skipping",
            render_id,
        )
        return

    # Per-render timeout scales with timeline length: a 60s cast remux
    # should fit comfortably in 4×60 = 240s, plus 60s floor for tiny casts.
    remux_timeout_s = max(60.0, 4.0 * float(expected_duration))

    tmpdir = tempfile.mkdtemp(prefix=f"remux_{render_id}_")
    try:
        compose_path = os.path.join(tmpdir, "compose.mp4")
        out_path = os.path.join(tmpdir, "final.mp4")
        await r2.download_file(output_key, compose_path)

        # Probe the compose output for its actual duration so we know
        # whether to extend the video stream.
        from services.block_normalize import _probe_streams
        try:
            probe = _probe_streams(compose_path)
            actual_duration = float(probe.get("duration_s") or 0)
        except Exception as probe_exc:
            sentry_sdk.capture_exception(probe_exc)
            actual_duration = 0.0

        # Under the overshoot+trim strategy every block is trimmed to
        # exactly its slot, so the composed timeline must already equal the
        # expected duration. We ASSERT that here rather than healing a
        # short timeline — a material undershoot is a real defect and is
        # failed, never padded/held/reversed (the "lady walks backwards"
        # artefact). The remux's filter graph only ever passes [0:v]
        # through untouched.
        video_source_path = compose_path
        extension_strategy = "asserted_match"
        await _assert_compose_video_matches_expected(
            actual_duration=actual_duration,
            expected_duration=expected_duration,
            render_id=render_id,
        )

        # Download every audio source, classifying each element as narration,
        # background music, or SFX. We tolerate per-element failures (the
        # offending element is dropped from the mix; the rest of the cast
        # still gets remuxed audio).
        #
        # CRITICAL: background music must be mixed at its resolved volume
        # (element prop → cast column → env → default) and ducked under the
        # voice. The previous version amix'd EVERY audio element — including
        # music — at full level with no volume/ducking, then loudnorm pinned
        # the sum to −16 LUFS. That made the music ride ~30 dB hotter than the
        # intended ~−47 dB bed (the "music is 4× too loud" report). This is
        # the dominant production audio path, so the env-aware resolution has
        # to happen HERE, not only in the cast_ffmpeg_composer filtergraph.
        narration: list[tuple[float, float, str]] = []  # (start_s, slot_dur, path)
        music: list[tuple[float, str, float]] = []  # (start_s, path, volume)
        sfx: list[tuple[float, str, float]] = []  # (start_s, path, volume)
        music_elements_seen: list[dict] = []
        async with httpx.AsyncClient(timeout=remux_timeout_s) as http:
            for idx, el in enumerate(audio_elements):
                src = (el.get("props") or {}).get("src") or ""
                meta = el.get("metadata") or {}
                props = el.get("props") or {}
                kind = (meta.get("kind") or "").lower()
                try:
                    s_start = float(el.get("s") or 0)
                except (TypeError, ValueError):
                    s_start = 0.0
                try:
                    slot_dur = max(0.0, float(el.get("e") or 0) - s_start)
                except (TypeError, ValueError):
                    slot_dur = 0.0
                local = os.path.join(tmpdir, f"a_{idx}")
                fetch_exc: Exception | None = None
                # One retry: a transient CDN/network blip on the fetch used to
                # silently drop this element (most often music, since there's
                # usually only one) from the final mix with no visible error —
                # the render still "succeeded", just missing that track.
                for attempt in range(2):
                    try:
                        resp = await http.get(src, follow_redirects=True)
                        resp.raise_for_status()
                        with open(local, "wb") as f:
                            f.write(resp.content)
                        fetch_exc = None
                        break
                    except Exception as exc:
                        fetch_exc = exc
                if fetch_exc is not None:
                    sentry_sdk.set_tag("remux_element_kind", kind or "narration")
                    sentry_sdk.capture_exception(fetch_exc)
                    logger.warning(
                        "Render %s remux: failed to fetch audio element %d (kind=%s) "
                        "after retry (%s); skipping",
                        render_id, idx, kind or "narration", fetch_exc,
                    )
                    continue

                # Classify: SFX and music are non-bonded accents/beds; anything
                # tied to a block (or unlabelled) is narration. A music element
                # carries kind="music"; an unbonded audio element with no
                # block_id is also treated as music (matches the composer).
                is_bonded = bool(meta.get("bonded") or meta.get("block_id"))
                if kind == "sfx":
                    try:
                        svol = float(props.get("volume", meta.get("volume")) or 1.0)
                    except (TypeError, ValueError):
                        svol = 1.0
                    sfx.append((s_start, local, max(0.0, min(1.0, svol))))
                elif kind == "music" or not is_bonded:
                    music_elements_seen.append(el)
                    mvol, _src = _resolve_music_volume_with_source(
                        [el], cast_music_volume
                    )
                    music.append((s_start, local, mvol))
                else:
                    narration.append((s_start, slot_dur, local))

        # Scene ambience beds (SCENE_AMBIENCE_ENABLED): each plan entry is a
        # contiguous room/outdoor run that gets a low, looped atmosphere bed
        # mixed under the voice like music. Sources are NOT timeline elements,
        # so download them here (once per distinct url).
        ambience: list[tuple[float, float, str, float, str]] = []  # (start, end, path, vol, env)
        if ambience_plan:
            _amb_paths: dict[str, str | None] = {}
            async with httpx.AsyncClient(timeout=remux_timeout_s) as _amb_http:
                for _ai, (a_s, a_e, a_url, a_vol, a_env) in enumerate(ambience_plan):
                    if a_url not in _amb_paths:
                        _p = os.path.join(tmpdir, f"amb_{len(_amb_paths)}")
                        try:
                            _resp = await _amb_http.get(a_url, follow_redirects=True)
                            _resp.raise_for_status()
                            with open(_p, "wb") as _f:
                                _f.write(_resp.content)
                            _amb_paths[a_url] = _p
                        except Exception as _amb_exc:
                            sentry_sdk.capture_exception(_amb_exc)
                            logger.warning(
                                "Render %s remux: ambience fetch failed (%s: %s); skipping that bed",
                                render_id, a_env, _amb_exc,
                            )
                            _amb_paths[a_url] = None
                    _local = _amb_paths.get(a_url)
                    if _local:
                        ambience.append((a_s, a_e, _local, a_vol, a_env))

        if not narration and not music and not sfx and not ambience:
            logger.info(
                "Render %s post-compose remux: no audio downloaded; leaving compose output unchanged",
                render_id,
            )
            return

        # Emit the single definitive [music-mix] line for this render path.
        if music:
            resolved_vol, vol_source = _resolve_music_volume_with_source(
                music_elements_seen, cast_music_volume
            )
            logger.info(
                "[music-mix] render=%s volume=%.6f source=%s n_music_elements=%d "
                "default=%.6f env=%s cast_override=%s path=remux",
                render_id, resolved_vol, vol_source, len(music),
                _MUSIC_DEFAULT_VOLUME, os.getenv("MUSIC_DEFAULT_VOLUME"),
                cast_music_volume,
            )

        # Build the ffmpeg command.
        #   Input 0: the (already slot-length) composed video — we ignore
        #            its audio and pass the video through untouched.
        #   Inputs 1..N: each downloaded audio source.
        # The composed video length was asserted == expected above; this
        # graph never lengthens, ping-pongs, or reverses.
        cmd: list[str] = ["ffmpeg", "-y", "-i", video_source_path]
        ordered_inputs: list[tuple[float, str]] = (
            [(s, p) for s, _dur, p in narration]
            + [(s, p) for s, p, _ in music]
            + [(s, p) for s, p, _ in sfx]
        )
        for _start, p in ordered_inputs:
            cmd.extend(["-i", p])
        # Ambience beds come last so normal input indices are unchanged.
        # -stream_loop -1 makes each short loop replay forever; atrim (below)
        # bounds it to its scene run.
        for _a_s, _a_e, _a_p, _a_vol, _a_env in ambience:
            cmd.extend(["-stream_loop", "-1", "-i", _a_p])

        filter_parts: list[str] = []
        # ffmpeg input indices: 0 is the video, audio inputs start at 1 in the
        # same order we appended them above (narration, then music, then sfx).
        ai = 1

        def _delayed(label_in: str, start_s: float, out: str, duration_s: float | None = None) -> str:
            delay_ms = max(0, int(round(start_s * 1000)))
            # Bug: this remux rebuilds the ENTIRE audio mix from scratch from
            # each element's raw source file, only shifting its start via
            # adelay — with no per-element duration cap, a source file longer
            # than its timeline slot (e.g. a voiceover block whose TTS/lipsync
            # audio runs longer than the stale Arrange-timeline slot it was
            # assigned) plays in full and audibly overlaps the next block's
            # narration, which starts on schedule regardless. Confirmed on a
            # real render: two adjacent voiceover blocks, the first one's full
            # ~6.5s narration bleeding ~2.5s into the second one's already-
            # started narration — "two voices talking at once" reported
            # directly by a user timestamp. atrim caps each source to its own
            # slot BEFORE the delay is applied, matching what the main
            # compose pass already does correctly (see worker_ffmpeg_compose's
            # per-track "-t {slot_dur}" normalize step) — this remux path
            # rebuilds the mix independently and must enforce the same rule.
            trim = f"atrim=duration={duration_s:.3f},asetpts=PTS-STARTPTS," if duration_s and duration_s > 0 else ""
            return (
                f"[{label_in}]{trim}aresample=48000,"
                f"aformat=channel_layouts=stereo,"
                f"adelay={delay_ms}|{delay_ms},apad[{out}]"
            )

        narration_labels: list[str] = []
        for n, (start_s, slot_dur, _p) in enumerate(narration):
            out = f"nar{n}"
            filter_parts.append(_delayed(f"{ai}:a", start_s, out, duration_s=slot_dur))
            narration_labels.append(f"[{out}]")
            ai += 1

        music_labels: list[str] = []
        for m, (start_s, _p, vol) in enumerate(music):
            pre = f"muspre{m}"
            delay_ms = max(0, int(round(start_s * 1000)))
            # Resolve the bed volume FIRST, then delay/pad. EQ comes later on
            # the summed music bus.
            filter_parts.append(
                f"[{ai}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"volume={vol:.6f},adelay={delay_ms}|{delay_ms},apad[{pre}]"
            )
            music_labels.append(f"[{pre}]")
            ai += 1

        sfx_labels: list[str] = []
        for s_, (start_s, _p, vol) in enumerate(sfx):
            out = f"sfx{s_}"
            delay_ms = max(0, int(round(start_s * 1000)))
            filter_parts.append(
                f"[{ai}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"volume={vol:.6f},adelay={delay_ms}|{delay_ms}[{out}]"
            )
            sfx_labels.append(f"[{out}]")
            ai += 1

        # Ambience beds: each source is -stream_loop'd forever on input, so
        # atrim bounds it to its scene run; short fades top & tail hide the
        # loop seam and the entrance/exit.
        ambience_labels: list[str] = []
        for k_, (a_start, a_end, _p, a_vol, _env) in enumerate(ambience):
            out = f"amb{k_}"
            a_dur = max(0.1, float(a_end) - float(a_start))
            delay_ms = max(0, int(round(float(a_start) * 1000)))
            _fade = max(0.05, min(0.8, a_dur / 4.0))
            filter_parts.append(
                f"[{ai}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"atrim=duration={a_dur:.3f},asetpts=PTS-STARTPTS,"
                f"volume={a_vol:.6f},"
                f"afade=t=in:st=0:d={_fade:.3f},"
                f"afade=t=out:st={a_dur - _fade:.3f}:d={_fade:.3f},"
                f"adelay={delay_ms}|{delay_ms}[{out}]"
            )
            ambience_labels.append(f"[{out}]")
            ai += 1

        # Build the narration bus (and a sidechain key copy per ducked bed).
        final_bus_labels: list[str] = []
        if narration_labels:
            if len(narration_labels) == 1:
                filter_parts.append(f"{narration_labels[0]}anull[nar_bus]")
            else:
                filter_parts.append(
                    f"{''.join(narration_labels)}amix=inputs={len(narration_labels)}:"
                    f"duration=longest:dropout_transition=0:normalize=0[nar_bus]"
                )
            # One sidechain key copy of the voice per ducked bed bus (music,
            # ambience). With neither, no split (byte-identical to the
            # pre-ambience graph); with both, a 3-way split.
            _duck_buses = (1 if music_labels else 0) + (1 if ambience_labels else 0)
            if _duck_buses == 0:
                narration_main = "[nar_bus]"
                narration_key = ""
                ambience_key = ""
            elif _duck_buses == 1:
                filter_parts.append("[nar_bus]asplit[nar_main][nar_key]")
                narration_main = "[nar_main]"
                narration_key = "[nar_key]" if music_labels else ""
                ambience_key = "[nar_key]" if ambience_labels else ""
            else:
                filter_parts.append(
                    "[nar_bus]asplit=3[nar_main][nar_key_m][nar_key_a]"
                )
                narration_main = "[nar_main]"
                narration_key = "[nar_key_m]"
                ambience_key = "[nar_key_a]"
            final_bus_labels.append(narration_main)
        else:
            narration_main = ""
            narration_key = ""
            ambience_key = ""

        # Music bus: sum → EQ → sidechain-duck under the voice.
        if music_labels:
            if len(music_labels) == 1:
                filter_parts.append(f"{music_labels[0]}anull[mus_sum]")
            else:
                filter_parts.append(
                    f"{''.join(music_labels)}amix=inputs={len(music_labels)}:"
                    f"duration=longest:dropout_transition=0:normalize=0[mus_sum]"
                )
            # Carve room for the voice: high-pass sub rumble, gentle high-shelf
            # cut. Uses explicit g=/f= which the running ffmpeg accepts.
            filter_parts.append(
                "[mus_sum]highpass=f=50,highshelf=g=-2:f=10000[mus_eq]"
            )
            if narration_key:
                # Relaxed from threshold=0.03:ratio=12 — those settings pumped
                # audibly. 0.05/6 ducks cleanly without the bed "breathing".
                filter_parts.append(
                    f"[mus_eq]{narration_key}sidechaincompress="
                    f"threshold=0.05:ratio=6:attack=10:release=300[mus_ducked]"
                )
                final_bus_labels.append("[mus_ducked]")
            else:
                final_bus_labels.append("[mus_eq]")

        # Ambience bus: sum → band-limit → duck under the voice. Quieter and
        # a lighter compression than music (0.06/4 vs 0.05/6) — an atmosphere
        # bed that dips for speech but never pumps.
        if ambience_labels:
            if len(ambience_labels) == 1:
                filter_parts.append(f"{ambience_labels[0]}anull[amb_sum]")
            else:
                filter_parts.append(
                    f"{''.join(ambience_labels)}amix=inputs={len(ambience_labels)}:"
                    f"duration=longest:dropout_transition=0:normalize=0[amb_sum]"
                )
            filter_parts.append(
                "[amb_sum]highpass=f=120,lowpass=f=9000[amb_eq]"
            )
            if ambience_key:
                filter_parts.append(
                    f"[amb_eq]{ambience_key}sidechaincompress="
                    f"threshold=0.06:ratio=4:attack=20:release=400[amb_ducked]"
                )
                final_bus_labels.append("[amb_ducked]")
            else:
                final_bus_labels.append("[amb_eq]")

        # SFX punch through at their own level (no ducking).
        final_bus_labels.extend(sfx_labels)

        # Final mix: narration + ducked music + sfx → dynaudnorm → loudnorm.
        # normalize=0 keeps the relative levels we just set (music stays a bed);
        # loudnorm then pins the voice-dominated sum to −16 LUFS.
        amix_chain = "".join(final_bus_labels)
        if len(final_bus_labels) == 1:
            filter_parts.append(
                f"{amix_chain}dynaudnorm=p=0.95,{LOUDNORM_FILTER}[aout]"
            )
        else:
            filter_parts.append(
                f"{amix_chain}amix=inputs={len(final_bus_labels)}:"
                f"duration=longest:dropout_transition=0:normalize=0,"
                f"dynaudnorm=p=0.95,"
                f"{LOUDNORM_FILTER}[aout]"
            )

        # Video is passed through untouched. Lengthening (when needed) was
        # already applied to ``video_source_path`` by the strategy ladder.
        filter_parts.append("[0:v]null[vout]")

        filter_complex = ";".join(filter_parts)

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-ar", "48000", "-ac", "2",
            "-t", f"{expected_duration:.3f}",
            out_path,
        ])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=remux_timeout_s, check=False,
            )
        except subprocess.TimeoutExpired as te:
            sentry_sdk.capture_exception(te)
            raise RuntimeError(
                f"post-compose remux timed out after {remux_timeout_s:.0f}s"
            ) from te

        if result.returncode != 0 or not os.path.exists(out_path):
            stderr_tail = (result.stderr or "")[-1500:]
            raise RuntimeError(
                f"post-compose remux ffmpeg failed (rc={result.returncode}): {stderr_tail}"
            )

        out_size = os.path.getsize(out_path)
        await r2.upload_file(out_path, output_key, content_type="video/mp4")
        delta = expected_duration - actual_duration
        logger.info(
            "[remux] render %s: actual=%.2fs expected=%.2fs delta=%.2fs "
            "audio_inputs=%d (+%d ambience) → %d bytes uploaded over %s",
            render_id, actual_duration, expected_duration, delta,
            len(ordered_inputs), len(ambience), out_size, output_key,
        )
        # One grep-able line per render documenting the timeline strategy.
        # Under overshoot+trim the composed video is asserted to already
        # match the expected duration (``asserted_match``); the video is
        # passed through untouched and only the audio mix is rebuilt.
        logger.info(
            "[remux] timeline extension: expected=%.3fs actual=%.3fs "
            "delta=%.3fs strategy=%s",
            expected_duration, actual_duration, delta, extension_strategy,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _timeline_product_image_elements(timeline: dict) -> list[dict]:
    """Image-type elements on any track that carry a ``metadata.product_id``
    and a valid, non-empty time slot (``s < e``).

    These are the product-card overlays the editor places in the cast
    timeline. The finalize/compose path lays out the avatar video and audio
    but never burns these cards onto the final mp4 (the Kling
    product-conditioned bake gate only fires for ``speaking`` blocks in the
    ``HOSTKEY_ONLY`` branch; ``body_motion`` / ``pip`` / ``voiceover`` modes
    bypass it entirely). ``_post_compose_product_overlays`` reads these and
    composites them defensively after the audio remux.

    Returns [] when the snapshot has no qualifying overlay.
    """
    if not timeline or not isinstance(timeline, dict):
        return []
    out: list[dict] = []
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            if (el.get("type") or "").lower() != "image":
                continue
            meta = el.get("metadata") or {}
            if not meta.get("product_id"):
                continue
            try:
                s = float(el.get("s") or 0)
                e = float(el.get("e") or 0)
            except (TypeError, ValueError) as exc:
                sentry_sdk.capture_exception(exc)
                continue
            if not s < e:
                continue
            out.append(el)
    return out


async def _post_compose_product_overlays(
    *,
    r2,
    output_key: str,
    timeline: dict,
    render_id: str,
    cast,
) -> None:
    """Defensive post-compose product-overlay composite.

    The finalize pipeline lays out the avatar video and remuxes audio but
    never burns the timeline's product-card image overlays onto the final
    mp4 — the product-conditioned avatar bake gate only fires for
    ``speaking`` blocks in the ``HOSTKEY_ONLY`` branch, so the
    ``body_motion`` / ``pip`` / ``voiceover`` render modes ship without the
    card. This helper closes that gap:

      1. Reads image overlay elements (``metadata.product_id`` set, ``s < e``)
         off the timeline snapshot. Skips entirely when there are none.
      2. Downloads compose's final.mp4 from R2.
      3. Probes its actual width/height so the cards are placed against the
         true canvas.
      4. Downloads each product image and builds the overlay spec list.
      5. Runs ``composite_product_overlays_multi`` and re-uploads the burnt
         result over the same key.

    Wrapped in try/except by the caller; this function may raise.
    """
    elements = _timeline_product_image_elements(timeline)
    if not elements:
        logger.info(
            "Render %s post-compose product overlays: none on timeline; skipping",
            render_id,
        )
        return

    from services.video_compositor import composite_product_overlays_multi

    # Fall back to the cast-level product title when an element doesn't
    # carry its own. The Cast model may not define this attribute, so we
    # read it defensively.
    cast_product_title = getattr(cast, "product_title", None) or ""

    tmpdir = tempfile.mkdtemp(prefix=f"prodov_{render_id}_")
    try:
        compose_path = os.path.join(tmpdir, "compose.mp4")
        out_path = os.path.join(tmpdir, "final.mp4")
        await r2.download_file(output_key, compose_path)

        # Probe the compose output for its real canvas so the cards land
        # against the actual dimensions (not a hard-coded portrait guess).
        from services.block_normalize import _probe_streams
        canvas_w, canvas_h = 0, 0
        try:
            probe = _probe_streams(compose_path)
            canvas_w = int(probe.get("width") or 0)
            canvas_h = int(probe.get("height") or 0)
        except Exception as probe_exc:
            sentry_sdk.capture_exception(probe_exc)
        if canvas_w <= 0 or canvas_h <= 0:
            # The compositor defaults to 720x1280 portrait when not told;
            # match that so placement math stays sane.
            canvas_w, canvas_h = 720, 1280

        from services.bg_remove import ensure_nobg_image

        overlays: list[dict] = []
        async with httpx.AsyncClient(timeout=120.0) as http:
            for idx, el in enumerate(elements):
                props = el.get("props") or {}
                meta = el.get("metadata") or {}
                src = props.get("src") or ""
                product_image_path = None
                bg_removed = False
                src_w = src_h = 0
                if src:
                    try:
                        ext = ".png" if src.lower().endswith(".png") else ".jpg"
                        product_image_path = os.path.join(tmpdir, f"prod_{idx}{ext}")
                        resp = await http.get(src, follow_redirects=True)
                        resp.raise_for_status()
                        with open(product_image_path, "wb") as f:
                            f.write(resp.content)
                        # Round-6 Bug C: strip the white product-photo box before
                        # compositing so we don't slap a JPG onto the canvas.
                        try:
                            from PIL import Image as _PILImage
                            with _PILImage.open(product_image_path) as _pi:
                                src_w, src_h = _pi.size
                        except Exception as dim_exc:
                            sentry_sdk.capture_exception(dim_exc)
                        product_id = str(meta.get("product_id") or "unknown")
                        asset_id = str(
                            meta.get("asset_id")
                            or props.get("asset_id")
                            or el.get("id")
                            or f"el{idx}"
                        )
                        product_image_path, bg_removed = await ensure_nobg_image(
                            r2=r2,
                            product_id=product_id,
                            asset_id=asset_id,
                            source_path=product_image_path,
                            work_dir=tmpdir,
                        )
                    except Exception as fetch_exc:
                        sentry_sdk.capture_exception(fetch_exc)
                        logger.warning(
                            "Render %s product overlay %d: failed to fetch image (%s); "
                            "rendering card without product image",
                            render_id, idx, fetch_exc,
                        )
                        product_image_path = None

                try:
                    start_s = float(el.get("s") or 0)
                    end_s = float(el.get("e") or 0)
                except (TypeError, ValueError) as coord_exc:
                    sentry_sdk.capture_exception(coord_exc)
                    continue

                # Position: honour explicit element fields (props first, then
                # top level); fall back to the centred sentinel (-1). width=0
                # triggers the compositor's hero mode.
                def _coord(name):
                    val = props.get(name)
                    if val is None:
                        val = el.get(name)
                    try:
                        return int(val) if val is not None else -1
                    except (TypeError, ValueError) as pos_exc:
                        sentry_sdk.capture_exception(pos_exc)
                        return -1

                title = props.get("title")
                if not title:
                    title = cast_product_title
                price = props.get("price") or ""

                ov_x = _coord("x")
                ov_y = _coord("y")
                logger.info(
                    "[product-overlay] render=%s idx=%d src=%s src_dims=%dx%d "
                    "target_w=%d x=%d y=%d start=%.3f end=%.3f bg_removed=%s",
                    render_id, idx, src, src_w, src_h,
                    int(canvas_w * 0.78), ov_x, ov_y, start_s, end_s, bg_removed,
                )

                overlays.append({
                    "product_image_path": product_image_path,
                    "title": title or "",
                    "price": price,
                    "start_s": start_s,
                    "end_s": end_s,
                    "x": ov_x,
                    "y": ov_y,
                    "width": 0,
                })

        if not overlays:
            logger.info(
                "Render %s post-compose product overlays: nothing to composite; "
                "leaving compose output unchanged",
                render_id,
            )
            return

        await composite_product_overlays_multi(
            input_video=compose_path,
            product_overlays=overlays,
            output=out_path,
            canvas_width=canvas_w,
            canvas_height=canvas_h,
        )

        if not os.path.exists(out_path):
            raise RuntimeError("product overlay composite produced no output")

        out_size = os.path.getsize(out_path)
        await r2.upload_file(out_path, output_key, content_type="video/mp4")
        logger.info(
            "[prodoverlay] render %s: composited %d overlay(s) onto %dx%d "
            "canvas → %d bytes uploaded over %s",
            render_id, len(overlays), canvas_w, canvas_h, out_size, output_key,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _probe_video_fps(path: str, *, default: int = 30) -> int:
    """Probe the first video stream's frame rate, rounded to an int.

    Used by the a/v reconciliation extend path so the freeze/loop ladder
    re-encodes at the clip's native fps. Falls back to ``default`` (30) on
    any probe failure.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return default
    raw = (proc.stdout or "").strip()
    if "/" in raw:
        try:
            num, den = raw.split("/", 1)
            num_f, den_f = float(num), float(den)
            if den_f > 0 and num_f > 0:
                return max(1, round(num_f / den_f))
        except ValueError as e:
            sentry_sdk.capture_exception(e)
            return default
    return default


async def _match_audio_to_video_duration(
    video_bytes: bytes,
    *,
    block_id: str,
    render_id: str,
) -> bytes:
    """Per-block audio/video duration reconciliation (PR #82).

    Lipsync engines occasionally return clips whose audio track ends
    several hundred ms before the video track (observed at
    blk_3eda62539f01: vdur=5.23s, adur=4.48s; blk_4e9daa13fcbb:
    vdur=4.53s, adur=4.06s). The trailing video frames then play
    against a silent audio track with the mouth visibly held open —
    the artefact this PR is fixing.

    Probe both stream durations with ffprobe; if they differ by more
    than 50 ms, reconcile as follows. When video > audio, pad the audio
    with silence. When audio > video, EXTEND the video to the audio length
    (freeze/loop ladder) — never trim the audio down, because the audio is
    the user-defined slot length and the user rule forbids cutting the
    render to fit. Audio is trimmed to video ONLY when extension is
    impossible, and that slot violation is captured to Sentry. Re-mux into
    a new MP4 and return its bytes. Failure is non-fatal: returns the input
    bytes and breadcrumbs the mismatch.
    """
    if not video_bytes:
        return video_bytes

    try:
        with tempfile.TemporaryDirectory(prefix=f"avmatch_{block_id}_") as tmp:
            in_path = os.path.join(tmp, "in.mp4")
            out_path = os.path.join(tmp, "out.mp4")
            with open(in_path, "wb") as fh:
                fh.write(video_bytes)

            def _probe_stream(kind: str) -> float:
                """ffprobe a single stream duration. kind ∈ {"v","a"}."""
                proc = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-select_streams", kind,
                        "-show_entries", "stream=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        in_path,
                    ],
                    capture_output=True, text=True,
                    timeout=30, check=False,
                )
                out = (proc.stdout or "").strip().splitlines()
                for line in out:
                    try:
                        v = float(line)
                        if v > 0:
                            return v
                    except ValueError as exc:
                        sentry_sdk.capture_exception(exc)
                        continue
                return 0.0

            v_dur = await asyncio.to_thread(_probe_stream, "v:0")
            a_dur = await asyncio.to_thread(_probe_stream, "a:0")

            if v_dur <= 0 or a_dur <= 0:
                # No second audio stream, or probe failed; leave the
                # clip untouched rather than risk producing a 0-second
                # audio track.
                return video_bytes

            delta = v_dur - a_dur
            if abs(delta) <= 0.05:
                return video_bytes

            # Breadcrumb (not exception) per PR #82 spec — visible in
            # any subsequent Sentry event for this render but not its
            # own incident.
            sentry_sdk.add_breadcrumb(
                category="render",
                level="info",
                message="av_duration_mismatch",
                data={
                    "render_id": render_id,
                    "block_id": block_id,
                    "video_duration_s": round(v_dur, 3),
                    "audio_duration_s": round(a_dur, 3),
                    "delta_s": round(delta, 3),
                },
            )
            logger.info(
                "Block %s a/v mismatch: v=%.3fs a=%.3fs Δ=%+.3fs — reconciling",
                block_id, v_dur, a_dur, delta,
            )

            if delta > 0:
                # video > audio: pad the audio with silence so the
                # final frames play against quiet (closed-mouth)
                # audio instead of stale ringing-out phonemes.
                pad_dur = round(delta, 3)
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-nostats",
                    "-i", in_path,
                    "-c:v", "copy",
                    "-af", f"apad=pad_dur={pad_dur}",
                    "-shortest", "-fflags", "+shortest",
                    "-map", "0:v:0", "-map", "0:a:0",
                    out_path,
                ]
            else:
                # audio > video: the user rule forbids cutting the render to
                # the audio length — "never, do not cut the render to meet
                # the seconds the user has set up". EXTEND the video to match
                # the (slot-length) audio instead of trimming audio down.
                # Trimming audio here is what truncated extended speak blocks
                # to the broken video duration and left black in the timeline.
                from services.block_extension import (
                    extend_video_bytes_to_duration,
                )

                target_fps = await asyncio.to_thread(_probe_video_fps, in_path)
                extended = await asyncio.to_thread(
                    extend_video_bytes_to_duration,
                    video_bytes=video_bytes,
                    target_s=a_dur,
                    target_fps=target_fps,
                    block_id=block_id,
                    render_id=render_id,
                )
                if extended and len(extended) != len(video_bytes):
                    logger.info(
                        "Block %s a/v mismatch: extended video to audio "
                        "length a=%.3fs (was v=%.3fs) — never shrink slot",
                        block_id, a_dur, v_dur,
                    )
                    return extended
                # Extension was impossible (no last frame / non-video source).
                # Only then fall back to trimming audio, and flag the slot
                # violation to Sentry per the brief.
                sentry_sdk.capture_exception(
                    RuntimeError(
                        f"Block {block_id} render {render_id}: could not extend "
                        f"video {v_dur:.3f}s up to audio {a_dur:.3f}s; trimming "
                        f"audio to video — USER SLOT MAY BE VIOLATED"
                    )
                )
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-nostats",
                    "-i", in_path,
                    "-c:v", "copy",
                    "-t", f"{v_dur:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0",
                    out_path,
                ]

            mux_timeout = max(30.0, max(v_dur, a_dur) * 4)
            proc = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True,
                timeout=mux_timeout, check=False,
            )
            if proc.returncode != 0 or not os.path.exists(out_path):
                logger.warning(
                    "Block %s a/v match ffmpeg rc=%d; keeping original bytes. stderr=%s",
                    block_id, proc.returncode, (proc.stderr or "")[-500:],
                )
                return video_bytes
            with open(out_path, "rb") as fh:
                return fh.read()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "Block %s a/v duration match raised (%s); keeping original bytes",
            block_id, e,
        )
        return video_bytes


async def _normalize_for_canvas(
    *,
    video_bytes: bytes,
    timeline: dict,
    block_id: str,
    render_id: str,
    fallback_duration_s: float,
    r2=None,
    is_motion: bool = False,
    motion_prompt: str = "stays in place with subtle natural micro-movements, breathing softly",
) -> bytes:
    """Conform a baked block to the cast canvas (size/fps), then trim to slot.

    Two-phase pipeline (Phase 1):

      1. Conform-only normalize: crop-cover scale to canvas, force fps,
         re-encode with byte-compatible streams. The output duration
         here is ≈ min(input_dur, slot_s); no ping-pong, no clone, no
         stretch.

      2. Trim-to-slot: dispatch to ``trim_block_to_slot``. Motion blocks
         are baked LONGER than their slot (overshoot), so this head-trims
         the surplus to land on exactly slot length. The deleted legacy
         ladder (ping-pong / short-tail-reverse / re-bake / frame-clone)
         produced the "she started to reverse - going backwards" /
         frozen-face artefacts and is gone. A clip that arrives shorter
         than slot is no longer padded — for motion it may take the
         bounded micro-slowdown (≤5%), otherwise it is left for the
         Phase 3 validation gate to reject.

    Logs and returns the raw input bytes on failure so the render never
    aborts on a normalize hiccup — the worst-case is the previous broken
    behaviour (mismatched aspect / duration), not a failed render.
    """
    if not video_bytes:
        return video_bytes
    try:
        cw, ch, fps = _canvas_dims_for_render(timeline)
        slot_s = _slot_duration_for_block(timeline, block_id, fallback_duration_s)
        if slot_s <= 0:
            slot_s = float(fallback_duration_s or 0)
        if slot_s <= 0:
            # Without a duration we can't safely hard-trim; skip rather
            # than risk producing a 0-second clip.
            return video_bytes
        from services.block_normalize import normalize_baked_block
        normalized = await asyncio.to_thread(
            normalize_baked_block,
            input_bytes=video_bytes,
            target_width=cw,
            target_height=ch,
            target_fps=fps,
            target_duration_s=slot_s,
            block_id=block_id,
            render_id=render_id,
            extend_to_slot=False,
        )
        # Trim-to-slot lives in its own module (kept async for symmetry
        # with the old extension call site / future provider hooks).
        try:
            from services.block_extension import trim_block_to_slot
            trimmed = await trim_block_to_slot(
                bake_bytes=normalized,
                slot_s=float(slot_s),
                target_width=cw,
                target_height=ch,
                target_fps=fps,
                block_id=block_id,
                render_id=render_id,
                is_motion=is_motion,
            )
            return trimmed
        except Exception as ext_exc:
            sentry_sdk.capture_exception(ext_exc)
            logger.warning(
                "Block %s render %s trim failed (%s); shipping "
                "conform-only normalized bytes",
                block_id, render_id, ext_exc,
            )
            return normalized
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(
            "Block %s render %s normalize failed (%s); shipping un-normalized bytes",
            block_id, render_id, e,
        )
        return video_bytes


async def _validate_baked_clip_bytes(
    video_bytes: bytes,
    *,
    block_id: str,
    render_id: str,
    require_audio: bool = True,
    is_motion: bool = False,
    slot_duration_s: float | None = None,
) -> None:
    """Phase 3 gate: run ``validate_baked_clip`` on the bytes before upload.

    Writes the clip to a temp file, runs the validator, and raises
    ``ClipValidationError`` (carrying the typed reason) when the clip fails.
    Raising routes through the existing per-block retry-once pass; a
    persistent failure marks the block ``failed`` and fails the whole cast
    render — there is NO silent-placeholder fallback (a black / frozen /
    truncated / undershot clip must never ship).

    ``is_motion`` + ``slot_duration_s`` enable the §3.2 motion-duration gate
    (motion clips must be ≥ slot after the Phase 1.3 micro-slowdown). Speaking
    callers leave ``is_motion=False`` — they enforce their own audio-relative
    tolerance via ``_enforce_speaking_tolerance``.

    Empty input bytes are themselves a structural failure.
    """
    from services.media_processing import (
        validate_baked_clip,
        ClipValidationReason,
        ClipValidationError,
    )

    if not video_bytes:
        raise ClipValidationError(
            ClipValidationReason.STRUCTURAL,
            f"block {block_id} render {render_id}: empty baked bytes",
        )

    with tempfile.TemporaryDirectory(prefix=f"validate_{block_id}_") as tmp:
        clip_path = os.path.join(tmp, "clip.mp4")
        with open(clip_path, "wb") as fh:
            fh.write(video_bytes)
        reason = await validate_baked_clip(
            clip_path,
            block_id=block_id,
            render_id=render_id,
            require_audio=require_audio,
            is_motion=is_motion,
            slot_duration_s=slot_duration_s,
        )

    if reason != ClipValidationReason.OK:
        err = ClipValidationError(
            reason,
            f"block {block_id} render {render_id} failed Phase 3 validation",
        )
        sentry_sdk.capture_exception(err)
        logger.warning(
            "[validate] block %s render %s REJECTED: %s — failing block "
            "(no placeholder)",
            block_id, render_id, reason.value,
        )
        raise err


async def _enforce_speaking_tolerance(
    video_bytes: bytes,
    *,
    timeline: dict,
    block_id: str,
    render_id: str,
    fallback_duration_s: float,
    audio_duration_s: float | None = None,
) -> bytes:
    """Phase 2 gate for SPEAKING blocks: enforce a tolerance band around AUDIO.

    Render_Quality_Duration_Validation.md §2.1: a speaking bake's length is
    driven by the TTS audio fed to the lipsync engine, so the reference for
    the tolerance band is the block's **TTS audio duration**, not its timeline
    slot. Comparing to the slot fails blocks the provider rendered correctly
    against the audio whenever the slot and the audio disagree (a planning
    mismatch handled separately by ``_reconcile_slot_vs_audio``).

    ``audio_duration_s`` is the measured TTS audio length (probe of the
    prepared lipsync wav). When it is missing / non-positive we fall back to
    the slot so the gate still has a reference, but the band is always against
    audio when we have it.

      * within ``[-2%, +5%]``  → accept as-is (no retime, no pad).
      * long but within +5%    → END-trim the surplus with ``-t`` to the AUDIO
        end (drop the trailing closed-mouth / silent tail — safe, never
        re-pitches voice).
      * outside the band       → raise ``SpeakingBlockOutOfTolerance`` so the
        existing per-block retry-once pass re-bakes it; a second failure
        marks the block ``failed`` and fails the cast. We NEVER pad / stretch
        / reverse a short speaking clip — that produced the lip-desync and
        backward-walk artefacts.

    Returns the (possibly end-trimmed) bytes. Probe failure is treated as a
    pass-through (we can't prove a violation, and the Phase 3 validation
    gate downstream still inspects the clip).
    """
    if not video_bytes:
        return video_bytes

    # §2.1: reference duration is the TTS audio, with the slot only as a
    # last-resort fallback when the audio length is unknown.
    ref_s = float(audio_duration_s or 0)
    ref_label = "audio"
    if ref_s <= 0:
        ref_s = _slot_duration_for_block(timeline, block_id, fallback_duration_s)
        ref_label = "slot"
    if ref_s <= 0:
        ref_s = float(fallback_duration_s or 0)
        ref_label = "slot"
    if ref_s <= 0:
        return video_bytes
    slot_s = ref_s

    try:
        with tempfile.TemporaryDirectory(prefix=f"spk_tol_{block_id}_") as tmp:
            in_path = os.path.join(tmp, "in.mp4")
            with open(in_path, "wb") as fh:
                fh.write(video_bytes)
            from services.block_normalize import _probe_streams
            probe = await asyncio.to_thread(_probe_streams, in_path)
            dur = float(probe.get("duration_s") or 0.0)
            if dur <= 0:
                # Can't prove a violation — let Phase 3 inspect it.
                return video_bytes

            lower = slot_s * (1.0 - _SPEAKING_TOLERANCE_UNDER_PCT)
            upper = slot_s * (1.0 + _SPEAKING_TOLERANCE_OVER_PCT)
            delta = dur - slot_s
            pct = (delta / slot_s) if slot_s > 0 else 0.0

            if lower <= dur <= upper:
                if delta > 0.05:
                    # Long but within +5%: end-trim the surplus tail.
                    out_path = os.path.join(tmp, "out.mp4")
                    cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-nostats",
                        "-i", in_path,
                        "-t", f"{slot_s:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
                        out_path,
                    ]
                    proc = await asyncio.to_thread(
                        subprocess.run, cmd,
                        capture_output=True, text=True,
                        timeout=max(60.0, slot_s * 4.0), check=False,
                    )
                    if proc.returncode == 0 and os.path.exists(out_path):
                        with open(out_path, "rb") as fh:
                            trimmed = fh.read()
                        logger.info(
                            "[speaking-tol] block %s render %s end-trimmed "
                            "%.3fs → %s=%.3fs (was +%.0f%%)",
                            block_id, render_id, dur, ref_label, slot_s,
                            pct * 100,
                        )
                        return trimmed
                    # End-trim failed — surface so the retry pass re-bakes.
                    err = RuntimeError(
                        f"speaking end-trim ffmpeg failed (rc={proc.returncode}) "
                        f"for block {block_id}: {(proc.stderr or '')[-500:]}"
                    )
                    sentry_sdk.capture_exception(err)
                    raise err
                # Within band and not materially long — accept untouched.
                logger.info(
                    "[speaking-tol] block %s render %s within band: "
                    "dur=%.3fs %s=%.3fs (%+.0f%%)",
                    block_id, render_id, dur, ref_label, slot_s, pct * 100,
                )
                return video_bytes

            # Outside the tolerance band — a real defect. Raise so the
            # existing retry-once pass re-bakes; never pad/stretch/reverse.
            err = SpeakingBlockOutOfTolerance(
                f"block {block_id} render {render_id}: speaking block out of "
                f"tolerance for {ref_label}={slot_s:.3f}s "
                f"band=[-{_SPEAKING_TOLERANCE_UNDER_PCT * 100:.0f}%, "
                f"+{_SPEAKING_TOLERANCE_OVER_PCT * 100:.0f}%] "
                f"(dur={dur:.3f}s delta={delta:+.3f}s / {pct:+.0%}). Refusing to "
                f"pad/stretch/reverse — re-baking via retry pass."
            )
            sentry_sdk.capture_exception(err)
            logger.warning(
                "[speaking-tol] block %s render %s OUT OF BAND: dur=%.3fs "
                "%s=%.3fs (%+.0f%%); raising for retry",
                block_id, render_id, dur, ref_label, slot_s, pct * 100,
            )
            raise err
    except SpeakingBlockOutOfTolerance:
        raise
    except RuntimeError:
        raise
    except Exception as e:
        # Probe/IO hiccup that isn't a tolerance violation — don't fail the
        # block on infrastructure noise; let Phase 3 inspect the clip.
        sentry_sdk.capture_exception(e)
        logger.warning(
            "[speaking-tol] block %s render %s gate raised non-tolerance "
            "error (%s); passing clip through to validation",
            block_id, render_id, e,
        )
        return video_bytes


async def _enforce_pip_slot_duration(
    video_bytes: bytes,
    *,
    timeline: dict,
    block_id: str,
    render_id: str,
    fallback_duration_s: float,
) -> None:
    """Guardrail: a conformed PIP talking-head clip must land on its bonded slot.

    PIP blocks skip ``_enforce_speaking_tolerance`` (audio-relative) and do not
    feed ``slot_duration_s`` to the Phase 3 gate, so a silently-failed
    extend-to-slot inside ``_normalize_for_canvas`` would ship a clip that is
    materially shorter (or longer) than its bonded ``[s, e]``. The bonded concat
    lays segments end-to-end and fully trusts each one to be its slot length —
    a short PIP segment slides every following block earlier and opens a
    background gap where the talking head should be (observed: a ~5s talking-head
    beat that played ~2s in the render, preview fine).

    Raises ``SpeakingBlockOutOfTolerance`` (caught by the per-block retry-once
    pass) so the block re-bakes; a persistent miss fails the render loudly
    rather than shipping the desync. Probe failure is a pass-through — Phase 3
    still inspects the clip.
    """
    if not video_bytes:
        return
    slot_s = _slot_duration_for_block(timeline, block_id, fallback_duration_s)
    if slot_s <= 0:
        return
    try:
        with tempfile.TemporaryDirectory(prefix=f"pip_slotdur_{block_id}_") as tmp:
            in_path = os.path.join(tmp, "in.mp4")
            with open(in_path, "wb") as fh:
                fh.write(video_bytes)
            from services.block_normalize import _probe_streams
            probe = await asyncio.to_thread(_probe_streams, in_path)
            dur = float(probe.get("duration_s") or 0.0)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return
    if dur <= 0:
        return
    # A correct conform lands within a frame of the slot; the fill ladder
    # (loop / freeze) can be a hair off. Anything past this band is a failed
    # conform, not rounding.
    lower = slot_s * 0.90 - 0.10
    upper = slot_s * 1.15 + 0.10
    if lower <= dur <= upper:
        return
    err = SpeakingBlockOutOfTolerance(
        f"block {block_id} render {render_id}: PIP clip conformed to {dur:.3f}s "
        f"but its bonded slot is {slot_s:.3f}s (band=[{lower:.3f}, {upper:.3f}]). "
        f"_normalize_for_canvas failed to land the clip on its slot — re-baking "
        f"via retry pass rather than shipping a bonded-concat desync."
    )
    sentry_sdk.capture_exception(err)
    logger.warning(
        "[pip-slotdur] block %s render %s OUT OF BAND: dur=%.3fs slot=%.3fs; "
        "raising for retry",
        block_id, render_id, dur, slot_s,
    )
    raise err


async def _probe_audio_duration_s(url_or_path: str) -> float:
    """Probe the duration of an audio (or media) URL/path via ffprobe.

    Returns 0.0 on any failure — callers treat a non-positive result as
    "unknown" and fall back to the slot. ffprobe accepts http(s) URLs
    directly, so this works for the prepared lipsync WAV on R2/CDN.

    Retries once on failure. This probe result silently controls whether a
    voiceover block's REAL audio-driven duration (broll_s) gets used or the
    caller falls back to the block's (possibly stale) Arrange-timeline slot
    — a single transient network blip here reproduces the exact "stale
    slot never gets corrected" bug this session already found and fixed for
    other cases, just from a different trigger. A full render fires many of
    these probes concurrently (same contention documented on
    voiceover_broll._download), so a bare single attempt is not reliable
    enough for a value this consequential.
    """
    if not url_or_path:
        return 0.0
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        url_or_path,
    ]
    last_result = 0.0
    for attempt in (1, 2):
        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True, timeout=30.0, check=False,
            )
            if proc.returncode == 0:
                last_result = float((proc.stdout or "0").strip() or 0.0)
                if last_result > 0:
                    return last_result
        except Exception as e:
            sentry_sdk.capture_exception(e)
        if attempt == 1:
            await asyncio.sleep(1.0)
    return last_result


async def _probe_bytes_duration_s(video_bytes: bytes) -> float:
    """Probe the container duration of an in-memory mp4 via ffprobe.

    Writes the bytes to a temp file (ffprobe can't read stdin reliably for
    mp4 with a trailing moov atom) and returns the format duration. Returns
    0.0 on any failure so callers treat a non-positive result as "unknown"
    rather than asserting against a probe glitch.
    """
    if not video_bytes:
        return 0.0
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tf:
            tf.write(video_bytes)
            tf.flush()
            return await _probe_audio_duration_s(tf.name)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return 0.0


def _reconcile_slot_vs_audio(
    timeline: dict,
    *,
    block_id: str,
    audio_duration_s: float,
    slot_duration_s: float,
    fixed_length: bool = False,
) -> tuple[float, str | None]:
    """Planning-layer slot↔audio reconciliation (Render_Quality_…md §2.1).

    A speaking block's slot is a planning value; its lipsync bake length is
    driven by the TTS audio. When the slot and the audio disagree by more
    than ``SLOT_AUDIO_MISMATCH_TOLERANCE`` (default 2%) that is a PLANNING
    defect, not a render defect — re-baking would never fix it because the
    provider is already rendering correctly against the audio.

    Resolution is DIRECTIONAL — the user rule is non-negotiable:

        "never, do not cut the render to meet the seconds the user has set
         up — only the cast script itself gets within limit, however, there
         may be even edits, extensions"

      * ``slot > audio`` (audio UNDERSHOOTS the user slot) → NEVER shrink the
        slot. The slot duration is user-defined and immutable; audio-side
        caption timestamps and overlay positions are bound to slot starts, so
        shrinking it shifts every downstream position and leaves trailing
        black. We hold the slot and the video is EXTENDED to fill it
        downstream (``trim_block_to_slot`` → micro-slowdown / tail freeze /
        loop). Returns ``(slot_s, None)``. When the slot is more than 2× the
        audio we additionally emit a ``slot_audio_undershoot_extreme``
        observability warning back to the planner — but still extend; the
        warning is observability, not a hard fail.
      * ``slot < audio`` (audio OVERRUNS a too-short slot) AND the slot is
        resizable → EXTEND the slot to the audio duration (an extension, which
        the user rule explicitly permits). Mutates the matching V1 video
        element's ``e`` in ``timeline`` in place and returns
        ``(audio_duration_s, None)``.
      * ``slot < audio`` AND the slot is fixed-length (e.g. a fixed showcase
        block the planner must not stretch) OR
        ``SLOT_AUDIO_AUTORESIZE_ENABLED=false`` → return
        ``(slot_duration_s, "slot_audio_mismatch")`` so the caller fails the
        block before it enters the render queue (§2.2).

    When the drift is within tolerance (or either duration is unknown) the
    slot is returned unchanged with no failure reason. We NEVER re-bake
    speaking for slot drift — that is a planning failure, not a render one.
    """
    try:
        audio_s = float(audio_duration_s or 0)
        slot_s = float(slot_duration_s or 0)
    except (TypeError, ValueError):
        return float(slot_duration_s or 0), None
    if audio_s <= 0 or slot_s <= 0:
        return slot_s, None

    drift = abs(slot_s - audio_s) / audio_s
    if drift <= _SLOT_AUDIO_MISMATCH_TOLERANCE:
        return slot_s, None

    # Audio UNDERSHOOTS the user slot. The user rule forbids cutting the
    # render to the audio length — the slot stays exactly as the user set it
    # and the video is extended downstream to fill it. We must NEVER call
    # _resize_slot_to_audio here (that was the slot-shrink bug producing
    # trailing black). Slot is held unchanged; no failure reason.
    if slot_s > audio_s:
        extreme = audio_s > 0 and slot_s > (2.0 * audio_s)
        if extreme:
            # Observability only — the planner may have authored a slot far
            # longer than the script. We still extend the video to fill it.
            sentry_sdk.add_breadcrumb(
                category="render.slot_audio_undershoot_extreme",
                message=(
                    f"block {block_id}: slot={slot_s:.3f}s > 2× "
                    f"audio={audio_s:.3f}s — extending video to fill slot"
                ),
                level="warning",
            )
            logger.warning(
                "[slot-audio] block %s slot=%.3fs audio=%.3fs "
                "(slot_audio_undershoot_extreme: slot > 2× audio) — holding "
                "user slot and EXTENDING video to fill (never shrinking slot)",
                block_id, slot_s, audio_s,
            )
        else:
            logger.info(
                "[slot-audio] block %s slot=%.3fs audio=%.3fs (audio under "
                "slot by %.1f%%) — holding user slot, video extended to fill "
                "(slot never shrunk)",
                block_id, slot_s, audio_s, drift * 100,
            )
        return slot_s, None

    # Audio OVERRUNS a too-short slot. Extending the slot to the audio is
    # itself an allowed extension (the slot grows, it is never cut). §2.2:
    # only fixed-length block types refuse to resize. When the global
    # auto-resize switch is off (SLOT_AUDIO_AUTORESIZE_ENABLED=false) every
    # drift is surfaced as a failure instead of silently corrected, so treat
    # the block as fixed-length for the purposes of this reconcile.
    if fixed_length or not _SLOT_AUDIO_AUTORESIZE_ENABLED:
        logger.warning(
            "[slot-audio] block %s FIXED slot=%.3fs vs audio=%.3fs "
            "(drift=%.1f%% > %.0f%%) — failing as slot_audio_mismatch "
            "(will not re-bake; planning defect; autoresize=%s)",
            block_id, slot_s, audio_s, drift * 100,
            _SLOT_AUDIO_MISMATCH_TOLERANCE * 100,
            _SLOT_AUDIO_AUTORESIZE_ENABLED,
        )
        return slot_s, "slot_audio_mismatch"

    # Resize the slot UP to the audio in the timeline so every downstream
    # consumer (trim-to-slot, compose) uses the corrected length.
    resized = _resize_slot_to_audio(timeline, block_id, audio_s)
    logger.info(
        "planning: extended slot for block=%s slot_was=%.3fs audio=%.3fs "
        "(slot_audio_mismatch); resize_applied=%s",
        block_id, slot_s, audio_s, resized,
    )
    return audio_s, None


def _resize_slot_to_audio(timeline: dict, block_id: str, audio_s: float) -> bool:
    """Mutate the V1 video element for ``block_id`` so ``e - s == audio_s``.

    Returns True when an element was found and resized. Mirrors the element
    lookup in :func:`_slot_duration_for_block` (id ``v1_<block_id>`` or
    ``metadata.block_id`` match). The start (``s``) is held fixed and the end
    (``e``) is set to ``s + audio_s`` so the slot equals the audio length.
    """
    if not timeline or not isinstance(timeline, dict) or audio_s <= 0:
        return False
    target_id = f"v1_{block_id}"
    exact_el: dict | None = None
    fallback_el: dict | None = None
    for track in timeline.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if (track.get("type") or "").lower() != "video":
            continue
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            eid = el.get("id") or ""
            if eid == target_id:
                exact_el = el
                break
            # Fallback ONLY for V1-shaped ids — mirrors the guard in
            # _slot_duration_for_block. NEVER match cap_/prod_/pm_/etc via
            # metadata.block_id: those overlays intentionally carry the same
            # block_id but shorter slots, and resizing one of THEM leaves the
            # real V1 slot untouched (the talking-head bake then ships at the
            # stale short length — render rnd_157ba7252d5b, blk_5d5230d5b801).
            if (
                fallback_el is None
                and eid.startswith("v1_")
                and (el.get("metadata") or {}).get("block_id") == block_id
            ):
                fallback_el = el
        if exact_el is not None:
            break
    el = exact_el or fallback_el
    if el is None:
        return False
    try:
        s = float(el.get("s") or 0)
    except (TypeError, ValueError):
        s = 0.0
    el["s"] = s
    el["e"] = s + float(audio_s)
    return True


def _per_block_user_durations(cast_timeline_json: dict) -> dict[str, float]:
    """Read user-edited per-block durations from cast.timeline_json.

    The editor stores its working timeline at
    cast.timeline_json["default"]["twick_data"]. We walk every track's
    elements and collect the (s, e) of any element whose
    metadata.block_id is set and whose track is a video track. The LAST
    such region wins so the render reflects the most recent edit.

    Returns: { block_id: duration_seconds_rounded } — values rounded to
    a multiple of 1/FPS so the InfiniteTalk frame count stays integer.
    Empty dict if no twick_data is present (older casts).
    """
    twick = ((cast_timeline_json or {}).get("default") or {}).get("twick_data") or {}
    tracks = twick.get("tracks") or []
    out: dict[str, float] = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        # Only consider video tracks. Audio tracks (A1) are not the
        # source of truth for the rendered clip length.
        ttype = (track.get("type") or "").lower()
        track_meta = track.get("metadata") or {}
        track_meta_type = (track_meta.get("track_type") or "").lower()
        is_video_track = (
            ttype == "video"
            or "video" in ttype
            or "video" in track_meta_type
        )
        for el in track.get("elements") or []:
            if not isinstance(el, dict):
                continue
            meta = el.get("metadata") or {}
            block_id = meta.get("block_id")
            if not block_id:
                continue
            el_kind = (meta.get("kind") or "").lower()
            el_track_type = (meta.get("track_type") or "").lower()
            # Accept the element when either the track or the element
            # itself looks like video. Skip pure audio elements.
            if not (
                is_video_track
                or el_kind == "video"
                or "video" in el_track_type
            ):
                continue
            try:
                s = float(el.get("s", 0) or 0)
                e = float(el.get("e", 0) or 0)
            except (TypeError, ValueError) as exc:
                sentry_sdk.capture_exception(exc)
                continue
            dur = e - s
            if dur <= 0:
                continue
            # Round to FPS grid so the frame count is integer.
            frames = max(1, round(dur * FPS))
            out[str(block_id)] = frames / float(FPS)
    return out


def _trim_audio_to_duration(audio_bytes: bytes, target_seconds: float) -> bytes:
    """Trim a wav/mp3 buffer to exactly target_seconds with ffmpeg.

    Used so the HOSTKEY worker (which infers output length from the
    audio it gets) renders a clip that matches the user's timeline edit
    exactly. Re-encodes to keep the container valid for arbitrary cut
    points.
    """
    # Per-block subprocess timeout — scales with the target duration so a
    # 60s clip gets a longer ffmpeg window than a 5s one. Floor of 30s
    # covers ffmpeg startup on cold workers.
    trim_timeout = max(30.0, target_seconds * 4)
    with tempfile.TemporaryDirectory(prefix="audio_trim_") as tmp:
        in_path = os.path.join(tmp, "in.bin")
        out_path = os.path.join(tmp, "out.wav")
        with open(in_path, "wb") as f:
            f.write(audio_bytes)
        cmd = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-t", f"{target_seconds:.3f}",
            "-c:a", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            out_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=trim_timeout, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"audio trim ffmpeg failed: {result.stderr[-1500:]}"
            )
        with open(out_path, "rb") as f:
            return f.read()


async def _trim_and_upload_audio(
    r2,
    audio_url: str,
    target_seconds: float,
    render_id: str,
    block_id: str,
) -> str:
    """Download audio_url, trim to target_seconds, upload to R2, return new URL.

    Used to honor user-edited timing on the HOSTKEY worker, which derives
    the rendered video length from the audio it receives. If anything
    fails we fall back to the original URL so the render still proceeds.
    """
    download_timeout = max(30.0, target_seconds * 4)
    async with httpx.AsyncClient(timeout=download_timeout) as http:
        resp = await http.get(audio_url, follow_redirects=True)
        resp.raise_for_status()
        original_bytes = resp.content
    trimmed = await asyncio.to_thread(
        _trim_audio_to_duration, original_bytes, target_seconds
    )
    out_key = f"renders/{render_id}/trimmed_audio/{block_id}.wav"
    await r2.upload_bytes(trimmed, out_key, "audio/wav")
    return r2.get_public_url(out_key)


async def _ensure_fresh_tts_for_block(
    r2,
    factory,
    *,
    cast_id: str,
    user_id: str,
    block_id: str,
    snapshot_audio_url: str,
) -> tuple[str, float]:
    """Defense-in-depth: regenerate TTS synchronously if the active variant
    has stale audio.

    Stale means: ``tts_r2_key`` is empty/null OR the R2 object's
    LastModified is older than ``variant.updated_at`` (the script was
    edited after the audio was baked). In either case we re-run TTS
    inline so the bake never picks up the wrong voice for the current
    script. Returns the (possibly fresh) audio_url + tts duration. On
    any failure we log + fall through to the snapshot URL — better to
    bake potentially-stale audio than to fail the render outright.
    """
    from models.variant import Variant
    from models.block import Block
    from models.cast import Cast
    from models.avatar import Avatar
    from sqlalchemy import select as _select

    try:
        async with factory() as session:
            res = await session.execute(
                _select(Variant)
                .where(Variant.block_id == block_id)
                .where(Variant.is_active.is_(True))
                .limit(1)
            )
            variant = res.scalars().first()
            if variant is None:
                return snapshot_audio_url, 0.0

            script_text = (variant.script_text or "").strip()
            if not script_text:
                # Empty-script blocks are handled by the silent-action
                # path downstream — leave audio alone here.
                return snapshot_audio_url, float(variant.tts_duration_seconds or 0)

            stale = False
            stale_reason = ""
            if not (variant.tts_r2_key or "").strip():
                stale = True
                stale_reason = "empty_tts_r2_key"
            else:
                try:
                    head = await r2.head_object(variant.tts_r2_key)
                    if head is None:
                        stale = True
                        stale_reason = "r2_object_missing"
                    else:
                        last_mod = head.get("LastModified")
                        v_updated = variant.updated_at
                        if last_mod and v_updated:
                            lm = last_mod
                            vu = v_updated
                            if lm.tzinfo is None:
                                lm = lm.replace(tzinfo=timezone.utc)
                            if vu.tzinfo is None:
                                vu = vu.replace(tzinfo=timezone.utc)
                            # Allow a small clock-skew margin (5s)
                            if (vu - lm).total_seconds() > 5:
                                stale = True
                                stale_reason = "r2_object_older_than_variant_updated_at"
                except Exception as head_exc:
                    sentry_sdk.capture_exception(head_exc)

            if not stale:
                # Snapshot backfill: the editor timeline can pre-date the
                # audio — e.g. an avatar_action block whose script + TTS were
                # added AFTER the timeline was last saved, so it carries no
                # audio element. The variant's audio is valid and fresh; use
                # it instead of returning the empty snapshot URL, which would
                # make the block bake silently (no voiceover in the render).
                if not (snapshot_audio_url or "").strip():
                    for _key in (
                        (variant.tts_r2_key or "").strip(),
                        (getattr(variant, "audio_key", None) or "").strip(),
                    ):
                        if not _key:
                            continue
                        try:
                            _backfilled = r2.get_public_url(_key)
                        except Exception as _url_exc:
                            sentry_sdk.capture_exception(_url_exc)
                            _backfilled = ""
                        if _backfilled:
                            logger.info(
                                "Block %s: timeline snapshot had no audio — "
                                "backfilled voiceover from variant key %s",
                                block_id, _key,
                            )
                            return _backfilled, float(variant.tts_duration_seconds or 0)
                return snapshot_audio_url, float(variant.tts_duration_seconds or 0)

            logger.warning(
                "stale TTS detected for block %s (reason=%s) — regenerating "
                "audio inline before bake",
                block_id, stale_reason,
            )

            block = await session.get(Block, block_id)
            cast = await session.get(Cast, cast_id) if block else None
            avatar = (
                await session.get(Avatar, cast.avatar_id)
                if cast and getattr(cast, "avatar_id", None)
                else None
            )
            if not avatar or not getattr(avatar, "voice_id", None):
                logger.warning(
                    "Cannot regen TTS for block %s — no avatar voice; "
                    "falling back to snapshot audio.",
                    block_id,
                )
                return snapshot_audio_url, float(variant.tts_duration_seconds or 0)

            # Voice chain precedence: per-block mic_on override > the scene
            # (AvatarLook) this block uses > avatar-wide default. Mirrors
            # the resolution in tasks/generate_cast.py / engine/cast_generator.py
            # so an inline stale-TTS regen picks the same chain a full
            # generation run would have.
            from models.avatar_look import AvatarLook
            from sqlalchemy import select as _sa_select_look
            look = None
            if getattr(block, "avatar_look_id", None):
                look = await session.get(AvatarLook, block.avatar_look_id)
            if look is None:
                _look_res = await session.execute(
                    _sa_select_look(AvatarLook)
                    .where(AvatarLook.avatar_id == avatar.id)
                    .where(AvatarLook.is_default.is_(True))
                    .limit(1)
                )
                look = _look_res.scalars().first()

            from services.mic_presets import resolve_scene_voice_settings
            clip_mic_enabled, scene_chain_id = resolve_scene_voice_settings(
                block_mic_on=getattr(block, "mic_on", None),
                avatar_clip_mic_enabled=bool(getattr(avatar, "clip_mic_enabled", False)),
                look_environment=getattr(look, "environment", None),
                look_mic_visible=getattr(look, "mic_visible", None),
            )
            logger.info(
                "voice mode=%s scene_chain=%s block=%s",
                "clip_mic" if clip_mic_enabled else "phone_mic",
                scene_chain_id, block_id,
            )

            from services.fish_audio import get_fish_audio_service
            fish = get_fish_audio_service()
            tts_result = await fish.generate_tts(
                text=script_text,
                voice_id=avatar.voice_id,
                clip_mic_enabled=clip_mic_enabled,
                scene_chain_id=scene_chain_id,
                block_id=block_id,
            )
            new_key = tts_result.get("audio_key", "") or ""
            new_lipsync_key = tts_result.get("lipsync_audio_key", "") or ""
            duration = float(tts_result.get("duration_seconds", 0) or 0)
            tmp_path = tts_result.get("tmp_path", "") or ""
            if new_key and tmp_path and os.path.exists(tmp_path):
                if not await r2.key_exists(new_key):
                    await r2.upload_file(tmp_path, new_key, content_type="audio/mpeg")
            if not new_key:
                src_path = tts_result.get("audio_path") or tmp_path
                if src_path:
                    new_key = f"creators/{user_id}/casts/{cast_id}/tts/{variant.id}.mp3"
                    await r2.upload_file(src_path, new_key, content_type="audio/mpeg")

            variant.tts_r2_key = new_key
            variant.audio_key = new_key
            if new_lipsync_key:
                variant.tts_lipsync_r2_key = new_lipsync_key
            variant.tts_duration_seconds = duration
            variant.duration_seconds = duration
            variant.caption_words = None
            variant.caption_segments = None
            variant.word_timestamps = None
            # This is a brand-new take — any sfx_timings resolved against the
            # OLD audio's word positions no longer mean anything against this
            # one. Drop them rather than fire [sfx:NAME] at whatever now
            # happens to sit at that stale timestamp (the SFX/scene mismatch
            # bug). Re-resolved next time captions are (re)aligned for this
            # variant — see routers/casts/tts_captions.py.
            variant.sfx_timings = None
            await session.commit()

            new_url = r2.get_public_url(new_key) if new_key else snapshot_audio_url
            logger.info(
                "TTS regenerated inline for block %s: key=%s duration=%.2fs",
                block_id, new_key, duration,
            )
            return new_url, duration
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.exception(
            "Inline TTS freshness check failed for block %s: %s — using snapshot audio",
            block_id, e,
        )
        return snapshot_audio_url, 0.0


async def _resolve_lipsync_audio_url(
    factory, r2, *, block_id: str, fallback_url: str,
) -> str:
    """Return the lipsync-friendly audio URL for ``block_id``.

    PR #65 splits TTS output into two artefacts: a 44.1 kHz MP3 master
    (``variants.tts_r2_key``) and a 16 kHz WAV lipsync feed
    (``variants.tts_lipsync_r2_key``). Lipsync engines (InfiniteTalk /
    MuseTalk / Kling) consume the WAV; the compose audio remux still
    uses the MP3 master. Old variants from before this PR have no
    lipsync key — we fall back to whatever the timeline element gave us.
    """
    try:
        from models.variant import Variant
        from sqlalchemy import select as _select
        async with factory() as session:
            res = await session.execute(
                _select(Variant)
                .where(Variant.block_id == block_id)
                .where(Variant.is_active.is_(True))
                .limit(1)
            )
            variant = res.scalars().first()
            if variant is None:
                return fallback_url
            lipsync_key = getattr(variant, "tts_lipsync_r2_key", None) or ""
            if not lipsync_key:
                return fallback_url
            try:
                if not await r2.key_exists(lipsync_key):
                    return fallback_url
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                return fallback_url
            return r2.get_public_url(lipsync_key)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return fallback_url


import threading

_session_factory_tls = threading.local()


def _make_session_factory():
    """Return a session factory bound to the CURRENT event loop, creating one
    if this loop doesn't have one yet.

    History: this used to create a brand-new engine (and its own connection
    pool) on EVERY call — called from 12 places in this file, including once
    per block during actual rendering. None of those engines were ever
    disposed, so every call permanently leaked a couple of real Postgres
    connections for the rest of the worker process's life, eventually
    exhausting Postgres's connection limit ("sorry, too many clients
    already"), which in turn silently broke cleanup_stale_cast_renders (the
    reaper that fails a block after 18 minutes of no progress) every time it
    ran, since it couldn't even open a DB connection to do its check.

    A single cached engine (the first fix tried here, module-level then
    thread-local) solves the leak but creates a worse, active-breakage bug:
    render() (below) calls asyncio.new_event_loop() for EVERY render task,
    and Celery's prefork worker processes are long-lived — the same process
    (and same thread, since prefork's task execution is single-threaded)
    runs many renders sequentially over its lifetime, each on a brand-new
    loop. An asyncpg connection is bound to the event loop that created it;
    caching the engine across loop boundaries meant render task #2 (new
    loop, same process) reused an engine whose connections belonged to
    render task #1's already-closed loop. Reproduced live as
    "sqlalchemy.exc.InterfaceError: cannot perform operation: another
    operation is in progress" — every render after the first one run by a
    given worker process failed to even read its own render row.

    Keying the cache on the running loop's identity (not just the thread)
    gets both properties: an engine is reused for every DB call within the
    SAME render task's SAME loop (no per-call leak), but a new loop always
    gets a fresh engine (no cross-loop corruption). The old engine's
    connections are simply abandoned when the loop closes — asyncpg has
    nothing left to clean up against a dead loop — bounded to 2 connections
    per render task rather than the original per-call leak.
    """
    loop = asyncio.get_running_loop()
    cached_factory = getattr(_session_factory_tls, "factory", None)
    cached_loop = getattr(_session_factory_tls, "loop", None)
    if cached_factory is None or cached_loop is not loop:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from config import settings
        eng = create_async_engine(settings.database_url, pool_size=2, max_overflow=0)
        cached_factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        _session_factory_tls.factory = cached_factory
        _session_factory_tls.loop = loop
    return cached_factory


def extract_bonded_blocks_from_timeline(timeline: dict) -> list[tuple[dict | None, dict | None]]:
    """Extract bonded V1+A1 pairs from timeline snapshot.

    Returns list of (v1_element, a1_element) tuples sorted by start time.
    Either side may be None: voiceover blocks have no V1 face track, and
    silent action / body_motion blocks (empty script_text) have no A1
    audio element.
    """
    tracks = timeline.get("tracks", [])
    elements_by_id: dict[str, dict] = {}

    for track in tracks:
        for el in track.get("elements", []):
            elements_by_id[el["id"]] = el

    pairs: list[tuple[dict | None, dict | None]] = []
    seen_blocks: set[str] = set()

    for el in elements_by_id.values():
        meta = el.get("metadata") or {}
        if not meta.get("bonded") or not meta.get("block_id"):
            continue
        block_id = meta["block_id"]
        if block_id in seen_blocks:
            continue

        paired_audio_id = meta.get("paired_audio_element_id")
        paired_video_id = meta.get("paired_video_element_id")
        render_mode = meta.get("render_mode")

        if paired_audio_id:
            # This is the V1 (snapshot) element
            a1 = elements_by_id.get(paired_audio_id)
            if a1:
                pairs.append((el, a1))
                seen_blocks.add(block_id)
            else:
                # Silent action / body_motion blocks: the variant's
                # script_text is empty so the upstream snapshot builder
                # never created an A1 audio element, even though the V1
                # still references the (would-be) audio id via
                # paired_audio_element_id. Previously the block was
                # silently dropped here, so the dispatch loop never saw
                # it and the final concat was missing those segments.
                # Admit the V1 alone with a1=None — the I2V body_motion
                # path bakes the clip from pinned start/end frames +
                # body_motion_prompt and the bake stage pads silence.
                if render_mode in ("body_motion", "motion"):
                    logger.info(
                        "Silent bonded block %s (render_mode=%s, no A1 "
                        "audio element); baking action clip without "
                        "TTS — duration sourced from V1 element.",
                        block_id, render_mode,
                    )
                    pairs.append((el, None))
                    seen_blocks.add(block_id)
                else:
                    logger.warning(
                        "Bonded block %s missing paired audio element "
                        "%s and render_mode=%r is not silent-capable — "
                        "dropping block from render.",
                        block_id, paired_audio_id, render_mode,
                    )
        elif paired_video_id:
            # This is the A1 (voice) element. Normally we look up its
            # paired V1; if the V1 doesn't exist in the timeline (e.g.
            # avatar_voiceover blocks have no face track — the audio
            # plays over b-roll/parallel_media), accept the A1 alone
            # with v1=None so the render pipeline can treat it as an
            # audio-only block. Without this, the whole block was
            # silently dropped from the render and the user lost the
            # voiceover narration entirely.
            v1 = elements_by_id.get(paired_video_id)
            if v1:
                pairs.append((v1, el))
                seen_blocks.add(block_id)
            else:
                logger.info(
                    "Audio-only bonded block %s (no V1 face track); "
                    "will render with audio over parallel_media overlays.",
                    block_id,
                )
                pairs.append((None, el))
                seen_blocks.add(block_id)

    def _sort_start(p: tuple[dict | None, dict | None]) -> float:
        v1, a1 = p
        primary = v1 if v1 is not None else a1
        return float((primary or {}).get("s", 0) or 0)

    pairs.sort(key=_sort_start)
    return pairs


def extract_overlay_elements(timeline: dict, render_width: int = 480, render_height: int = 848) -> list[dict]:
    """Extract all non-bonded timeline elements (captions, images, videos, audio, stickers).

    Scales coordinates from editor canvas resolution to render resolution.
    Logs per-element accept/reject decisions.
    Returns a list of dicts suitable for the GPU worker overlay_elements payload.
    """
    tracks = timeline.get("tracks", [])
    overlays: list[dict] = []

    # Canvas size detection: prefer snapshot-level composition fields, fall back to bonded probe
    canvas_w = timeline.get("compositionWidth") or 1080
    canvas_h = timeline.get("compositionHeight") or 1920
    if not timeline.get("compositionWidth"):
        # Legacy: scan for first bonded video_face element with valid dims
        for track in tracks:
            for el in track.get("elements", []):
                meta = el.get("metadata") or {}
                if meta.get("bonded") and meta.get("track_type") == "video_face":
                    props = el.get("props") or {}
                    cw, ch = props.get("width"), props.get("height")
                    if cw and ch and cw > 0 and ch > 0:
                        canvas_w, canvas_h = cw, ch
                        break

    sx = render_width / canvas_w if canvas_w > 0 else 1.0
    sy = render_height / canvas_h if canvas_h > 0 else 1.0
    logger.info("Overlay scaling: canvas %dx%d -> render %dx%d (sx=%.3f sy=%.3f)",
                canvas_w, canvas_h, render_width, render_height, sx, sy)

    ALLOWED_TYPES = {"caption", "captions", "text", "image", "overlay", "sticker", "logo", "gif", "video", "audio", "solid"}

    accepted = 0
    rejected_bonded = 0
    rejected_type = 0
    rejected_malformed = 0

    for track in tracks:
        for el in track.get("elements", []):
            meta = el.get("metadata") or {}
            # Skip bonded V1/A1 -- those are baked avatar clips
            if meta.get("bonded"):
                rejected_bonded += 1
                continue

            el_type = (el.get("type") or "").lower().strip()
            props = el.get("props") or {}

            if el_type not in ALLOWED_TYPES:
                rejected_type += 1
                logger.warning("Overlay rejected (type=%r not in whitelist) id=%s", el_type, el.get("id"))
                continue

            # Normalize caption/captions to a single type for the GPU worker
            normalized_type = "caption" if el_type in ("caption", "captions") else el_type

            raw_x = props.get("x", 0) or 0
            raw_y = props.get("y", 0) or 0
            raw_w = props.get("width")
            raw_h = props.get("height")

            start_s = el.get("s", 0) or 0
            end_s = el.get("e", 0) or 0
            if end_s <= start_s:
                rejected_malformed += 1
                logger.warning("Overlay rejected (invalid timing s=%.3f e=%.3f) id=%s type=%s",
                               start_s, end_s, el.get("id"), el_type)
                continue

            overlay: dict = {
                "type": normalized_type,
                "id": el.get("id"),
                "start_s": start_s,
                "end_s": end_s,
                "x": int(raw_x * sx),
                "y": int(raw_y * sy),
                "width": int(raw_w * sx) if raw_w else None,
                "height": int(raw_h * sy) if raw_h else None,
            }

            # Text / caption fields
            if normalized_type in ("caption", "text"):
                text = props.get("text", "") or ""
                overlay["text"] = text
                raw_font = props.get("fontSize") or _caption_default_font_size()
                overlay["fontSize"] = max(12, int(raw_font * min(sx, sy)))
                overlay["fontColor"] = (
                    props.get("fontColor")
                    or props.get("color")
                    or os.getenv("CAPTION_BASE_COLOR")
                    or _CAPTION_DEFAULT_BASE_COLOR
                )
                overlay["fontFamily"] = (
                    props.get("fontFamily")
                    or os.getenv("CAPTION_FONT_FAMILY")
                    or _CAPTION_DEFAULT_FONT_FAMILY
                )
                overlay["highlightColor"] = (
                    props.get("highlightColor")
                    or os.getenv("CAPTION_HIGHLIGHT_COLOR")
                    or _CAPTION_DEFAULT_HIGHLIGHT_COLOR
                )
                overlay["backgroundColor"] = props.get("backgroundColor") or props.get("background")
                overlay["textAlign"] = props.get("textAlign") or props.get("align")
                # Stroke width is authored in source pixels; scale to output
                # resolution and keep at least the default so the outline never
                # disappears at small canvases. ceil avoids rounding a thin
                # stroke down to 0.
                raw_stroke = props.get("strokeWidth")
                if raw_stroke is None:
                    raw_stroke = _caption_default_stroke_width()
                try:
                    scaled_stroke = math.ceil(float(raw_stroke) * min(sx, sy))
                except (TypeError, ValueError) as e:
                    sentry_sdk.capture_exception(e)
                    scaled_stroke = _caption_default_stroke_width()
                overlay["strokeWidth"] = max(_caption_default_stroke_width(), scaled_stroke)
                overlay["strokeColor"] = (
                    props.get("strokeColor")
                    or os.getenv("CAPTION_STROKE_COLOR")
                    or _CAPTION_DEFAULT_STROKE_COLOR
                )
                # Preset-driven fields (positionY/box/textTransform) were
                # computed by editorStarterMapping.ts and saved on the item,
                # but never made it past this extraction step — the render
                # compose step had no way to see them even though the editor
                # sent them, so every caption fell back to a hardcoded
                # bottom-center, no-box, no-transform look regardless of the
                # preset actually chosen.
                overlay["positionY"] = props.get("positionY")
                overlay["ffmpegBoxEnabled"] = bool(props.get("ffmpegBoxEnabled"))
                overlay["ffmpegBoxColor"] = props.get("ffmpegBoxColor") or "black@0.5"
                overlay["textTransform"] = props.get("textTransform") or "none"
                # Per-word timing — one caption element per block carries
                # props._captions_tokens = [{text, startMs, endMs}, ...] in
                # ABSOLUTE timeline ms (routers/casts/timeline.py builds it;
                # the editor's editorStarterMapping.ts forwards it on save).
                # The compose step needs these to page a long caption ~6-7
                # words at a time and to karaoke-highlight the spoken word —
                # without them it can only burn the whole block's caption as
                # one static line. (overlay["width"] is already forwarded and
                # scaled above.)
                overlay["_captions_tokens"] = props.get("_captions_tokens") or []
                overlay["pageDurationInMilliseconds"] = props.get("pageDurationInMilliseconds")
                overlay["maxLines"] = props.get("maxLines")

            # Static media fields
            if normalized_type in ("image", "overlay", "sticker", "logo", "gif"):
                src = props.get("src", "") or ""
                if not src:
                    rejected_malformed += 1
                    logger.warning("Overlay rejected (empty src) id=%s type=%s", el.get("id"), el_type)
                    continue
                overlay["src"] = src
                # Images/stickers can also fade in/out
                overlay["fadeInDurationInSeconds"] = props.get("fadeInDurationInSeconds", 0) or 0
                overlay["fadeOutDurationInSeconds"] = props.get("fadeOutDurationInSeconds", 0) or 0
                # How to fit a mismatched-aspect asset into its box:
                # "contain" (fit + pad, product shots) vs "cover" (fill + crop,
                # the default for b-roll / backgrounds). Set by the editor
                # mapping; a plain scale-to-box downstream stretches anything
                # off-ratio.
                overlay["fit"] = props.get("fit") or "cover"

            # Video fields
            if normalized_type == "video":
                src = props.get("src", "") or ""
                if not src:
                    rejected_malformed += 1
                    logger.warning("Video overlay rejected (empty src) id=%s", el.get("id"))
                    continue
                overlay["src"] = src
                overlay["videoStartFromInSeconds"] = props.get("videoStartFromInSeconds", 0) or 0
                overlay["muted"] = bool(props.get("muted", False))
                overlay["decibelAdjustment"] = props.get("decibelAdjustment", 0) or 0
                overlay["playbackRate"] = props.get("playbackRate", 1.0) or 1.0
                overlay["borderRadius"] = props.get("borderRadius", 0) or 0
                overlay["fadeInDurationInSeconds"] = props.get("fadeInDurationInSeconds", 0) or 0
                overlay["fadeOutDurationInSeconds"] = props.get("fadeOutDurationInSeconds", 0) or 0
                # See the image branch — "contain" vs "cover" fit, default cover.
                overlay["fit"] = props.get("fit") or "cover"

            # Audio fields
            if normalized_type == "audio":
                src = props.get("src", "") or ""
                if not src:
                    rejected_malformed += 1
                    logger.warning("Audio overlay rejected (empty src) id=%s", el.get("id"))
                    continue
                overlay["src"] = src
                overlay["audioStartFromInSeconds"] = props.get("audioStartFromInSeconds", 0) or 0
                overlay["decibelAdjustment"] = props.get("decibelAdjustment", 0) or 0
                overlay["audioFadeInDurationInSeconds"] = props.get("audioFadeInDurationInSeconds", 0) or 0
                overlay["audioFadeOutDurationInSeconds"] = props.get("audioFadeOutDurationInSeconds", 0) or 0

            # Solid color block
            if normalized_type == "solid":
                overlay["color"] = props.get("color") or "#000000"

            # Universal carry-over props
            if props.get("opacity") is not None:
                overlay["opacity"] = props["opacity"]
            if props.get("rotation") is not None:
                overlay["rotation"] = props["rotation"]

            # Track-type hint for the compositor
            if meta.get("track_type"):
                overlay["track_type"] = meta["track_type"]
            # Block this overlay belongs to — lets the compose worker pair a
            # full-frame b-roll with its PIP/talking-head block so the face is
            # composited OVER the b-roll instead of hidden behind it.
            if meta.get("block_id"):
                overlay["block_id"] = meta["block_id"]

            overlays.append(overlay)
            accepted += 1

    # Compositing order: lower priority composites FIRST (background), higher
    # priority composites LAST (foreground). Final stack top → bottom should be:
    #     captions  (handled separately by the SRT filter, always on top)
    #     products  (z=3)
    #     avatar / video (z=2)
    #     other overlays / images (z=1, default)
    # Within the same z, ties break by start_s then type so the temporal order
    # is still readable.
    _Z_ORDER = {
        "captions": 4,           # never reach here (subtitles filter), but safe
        "products": 3, "product_image": 3, "product_chip": 3,
        "video_face": 2, "avatar": 2,
    }
    def _z(o):
        return _Z_ORDER.get(o.get("track_type") or "", 1)
    overlays.sort(key=lambda o: (_z(o), o["start_s"], o["type"]))

    # Close small gaps between consecutive video/image overlays. These are
    # the B-roll/stock visuals for voiceover and stock_photo/video blocks —
    # the ONLY thing covering the bonded bake underneath for those blocks.
    # A multi-angle block's individual clip durations don't always sum to
    # exactly its slot length (each clip is timed independently at script-
    # generation time), so consecutive clips can leave a sliver where
    # neither overlay's `enable` window is active. During that sliver the
    # compositor falls through to whatever the bonded track shows — which
    # for a voiceover block is the avatar placeholder, not the B-roll.
    # Confirmed directly on a real render: pm_blk_d1f2dd615ff3_1 ended at
    # 14.167s and the next block's pm_blk_d57e79c95cb0_0 started at 14.367s
    # — a 0.2s gap with nothing covering the canvas — and extracted frames
    # at exactly that timestamp showed the avatar's face flash between two
    # B-roll clips. Since every video overlay already carries
    # `eof_action=pass` (holds its last decoded frame once its source runs
    # out), simply extending `end_s` to meet the next overlay's start is
    # enough to close the gap with no visual side effect for a same-clip
    # extension, and at most a brief extra hold-frame for a real cut.
    _GAP_CLOSE_MAX_S = 1.0
    _visual_types = ("video", "image")
    _visual_idxs = [
        i for i, o in enumerate(overlays) if o["type"] in _visual_types
    ]
    for pos in range(len(_visual_idxs) - 1):
        cur = overlays[_visual_idxs[pos]]
        nxt = overlays[_visual_idxs[pos + 1]]
        gap = nxt["start_s"] - cur["end_s"]
        if 0 < gap <= _GAP_CLOSE_MAX_S:
            logger.info(
                "Closing %.3fs overlay gap: %s (end=%.3f) -> %s (start=%.3f)",
                gap, cur.get("id"), cur["end_s"], nxt.get("id"), nxt["start_s"],
            )
            cur["end_s"] = nxt["start_s"]

    logger.info(
        "Overlay extraction complete: accepted=%d rejected(bonded=%d, type=%d, malformed=%d) canvas=%dx%d render=%dx%d",
        accepted, rejected_bonded, rejected_type, rejected_malformed,
        canvas_w, canvas_h, render_width, render_height,
    )
    by_type: dict[str, int] = {}
    for o in overlays:
        by_type[o["type"]] = by_type.get(o["type"], 0) + 1
    logger.info("Overlay breakdown: %s", by_type)
    return overlays

async def _load_caption_overlays(
    cast_id: str,
    timeline: dict,
    render_width: int = 480,
    render_height: int = 848,
) -> list[dict]:
    """Load word-level captions from active variants and convert to overlay elements.

    Returns drawtext-compatible caption dicts with absolute timing aligned
    to each block's position in the timeline.
    """
    factory = _make_session_factory()

    # Map block_id -> timeline start offset (from bonded V1 elements)
    block_offsets: dict[str, float] = {}
    for track in timeline.get("tracks", []):
        for el in track.get("elements", []):
            meta = el.get("metadata") or {}
            if meta.get("bonded") and meta.get("track_type") == "video_face" and not meta.get("placeholder"):
                bid = meta.get("block_id", "")
                if bid:
                    block_offsets[bid] = el.get("s", 0)

    if not block_offsets:
        return []

    captions: list[dict] = []
    font_size = max(16, int(28 * (render_width / 1080)))  # Scale font to render res

    try:
        from sqlalchemy import select, and_
        from models.block import Block

        async with factory() as session:
            # Find all active blocks for this cast (captions loaded for all by default)
            result = await session.execute(
                select(Block).where(
                    and_(
                        Block.cast_id == cast_id,
                        Block.deleted_at.is_(None),
                    )
                )
            )
            blocks = result.scalars().all()
            block_ids = [b.id for b in blocks]

            if not block_ids:
                return []

            from models.variant import Variant
            var_result = await session.execute(
                select(Variant).where(
                    and_(
                        Variant.block_id.in_(block_ids),
                        Variant.is_active == True,
                    )
                )
            )
            variants = var_result.scalars().all()

            for var in variants:
                words = var.caption_words
                if not words or not isinstance(words, list):
                    continue

                block_start = block_offsets.get(var.block_id, None)
                if block_start is None:
                    continue

                # Group words into ~3-word chunks for readability
                chunk_size = 3
                for i in range(0, len(words), chunk_size):
                    chunk = words[i:i + chunk_size]
                    text = " ".join(w.get("word", "") for w in chunk)
                    if not text.strip():
                        continue
                    w_start = chunk[0].get("start", 0)
                    w_end = chunk[-1].get("end", w_start + 0.5)

                    captions.append({
                        "type": "caption",
                        "start_s": block_start + w_start,
                        "end_s": block_start + w_end,
                        "text": text,
                        "fontSize": font_size,
                        "fontColor": "white",
                        "x": "(w-text_w)/2",  # Center horizontally
                        "y": str(int(render_height * 0.82)),  # Bottom area
                    })

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning("Failed to load caption overlays for cast %s: %s", cast_id, e)

    captions.sort(key=lambda c: c["start_s"])
    logger.info("Loaded %d caption overlay chunks for cast %s", len(captions), cast_id)
    return captions


def _exc_summary(exc: BaseException) -> str:
    """A never-blank, user-showable summary of ``exc``.

    Bug: ``str(exc)`` is EMPTY for a large class of exceptions raised with
    no message — a bare ``raise SomeError()``, a raw ``AssertionError``
    from a bare ``assert x``, and several asyncio/subprocess errors all
    stringify to "". Persisting that as ``CastRender.error_message`` left
    a "failed" render with a genuinely blank reason: the "Why did it fail?"
    popover showed the generic fallback text with an EMPTY hover-tooltip —
    indistinguishable from a render that recorded no reason at all,
    confirmed live on a real failed render. Prefixing the exception's own
    class name guarantees a non-empty, at-least-somewhat-informative
    result, and doubles as more text for the frontend's friendlyBlockError
    substring matching (e.g. "TimeoutError", "RuntimeError") to key off of.
    """
    msg = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


# max_retries=0: retries combined with task_acks_late=True create zombie
# redelivery loops where a single failure spawns 4+ concurrent task
# instances racing the same render row. If a render fails, fail it
# permanently and let the user click retry manually from the UI.
# time_limit=3600: 1 hour hard ceiling. No legitimate render takes longer
# than that; if it does, something is broken and the task should be
# killed by Celery rather than burning provider credits indefinitely.
# Reduced from 18000 (5h) per ops review.
@celery_app.task(bind=True, queue="renders", time_limit=3600, max_retries=0, name="tasks.cast_render.render")
def render_cast_task(self, render_id: str):
    """Two-pass render pipeline.

    Pass 1: bake each bonded block via InfiniteTalk → MP4 with audio intrinsic
    Pass 2: dispatch FFmpeg composition to GPU worker
    """
    sentry_sdk.set_tag("render_id", render_id)
    sentry_sdk.set_context("cast_render", {"render_id": render_id, "pipeline": "two_pass"})

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_render_async(self, render_id))
    except Exception as exc:
        sentry.capture_exception(exc)
        logger.error(json.dumps({
            "service": "cast_render",
            "level": "error",
            "message": f"Render failed: {_exc_summary(exc)}",
            "render_id": render_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
        loop2 = asyncio.new_event_loop()
        try:
            loop2.run_until_complete(_mark_render_failed(render_id, _exc_summary(exc)))
        finally:
            loop2.close()
        # No self.retry() — retries with task_acks_late=True spawn zombie
        # redeliveries. Re-raise so Celery marks this attempt FAILURE and
        # ACKs the message; the user clicks retry manually if they want.
        raise
    finally:
        loop.close()


async def _mark_render_failed(render_id: str, error_msg: str):
    from models.cast_render import CastRender, CastRenderStatus
    factory = _make_session_factory()
    async with factory() as session:
        render = await session.get(CastRender, render_id)
        if render:
            render.status = CastRenderStatus.FAILED.value
            # Bug: many exceptions raised with no message (e.g. a bare
            # `raise SomeError()`, or certain asyncio/subprocess errors)
            # stringify to "" — persisting that left error_message
            # genuinely blank. The user-facing "Why the render failed"
            # popover then showed a generic fallback with an EMPTY tooltip
            # (nothing at all to inspect), indistinguishable from a render
            # that never recorded a reason. render_cast_task's caller
            # already builds a non-empty summary via _exc_summary(); this
            # is a second, defensive guard so _mark_render_failed itself
            # can never persist a blank reason regardless of caller.
            render.error_message = (error_msg or "").strip()[:1000] or (
                "Render failed with no error details recorded — please retry."
            )
            await session.commit()


async def _update_render(render_id: str, **kwargs):
    from models.cast_render import CastRender
    factory = _make_session_factory()
    async with factory() as session:
        render = await session.get(CastRender, render_id)
        if render:
            for key, value in kwargs.items():
                setattr(render, key, value)
            await session.commit()


# Provider label normalization — the frontend shows these as short chips.
# Keep in sync with frontend RenderStatusPill PROVIDER_LABELS map.
_PROVIDER_LABELS = {
    "hostkey": "host",
    "modal": "mod",
    "runpod": "pod",
    "musetalk": "muse",
    "hostkey_t2v": "host",   # HOSTKEY T2V — same on-prem GPU
    "fal_t2v": "ai",          # generic AI provider label, no engine name
    "fal_i2v": "ai",          # fal i2v (avatar-locked motion); same generic chip
    # Cloud-fallback speaking tiers — show up in the per-block status pill
    # when HOSTKEY is unavailable and we recover via a cloud provider.
    "wavespeed": "ai",
    "fal_musetalk": "ai",
    "fal_hallo": "ai",
    # Product-conditioned base bake — internal label only; never names
    # the vendor engine in the user-facing chip.
    "product_elements_bake": "ai",
    # Top-tier sync-lipsync — same generic chip, no engine name.
    "fal_sync_lipsync_v3": "ai",
    "fal_sync_lipsync_v2_pro": "ai",
}


async def _init_block_statuses(render_id: str, bonded_blocks: list):
    """Seed the per-block status array at start of baking — all 'queued'."""
    from datetime import datetime, timezone
    from models.cast_render import CastRender
    factory = _make_session_factory()
    statuses = []
    for idx, (v1, a1) in enumerate(bonded_blocks):
        # Audio-only voiceover blocks have v1=None — fall back to A1
        # for metadata so block_id, etc. are still recorded.
        meta = (v1.get("metadata") if v1 else None) or (a1.get("metadata") if a1 else None) or {}
        fallback_id = (v1 or a1 or {}).get("id")
        statuses.append({
            "block_id": meta.get("block_id") or fallback_id,
            "index": idx,
            "state": "queued",
            "provider": None,
            "started_at": None,
            "completed_at": None,
            "duration_s": None,
            "error": None,
        })
    async with factory() as session:
        render = await session.get(CastRender, render_id)
        if render:
            render.block_statuses = statuses
            await session.commit()


async def _update_block_status(render_id: str, block_id: str, append_attempt: dict | None = None, **updates):
    """Atomically update a single block's status row within the JSONB array.

    Uses a read-modify-write inside a single transaction. Safe enough at our
    concurrency (a render's blocks are serialized through one worker).

    `append_attempt`, if given, is appended to that row's `provider_attempts`
    list rather than overwriting a field — used to keep a running history of
    every provider tier tried for this block (which one, when, why it
    failed), instead of only learning the outcome after the whole chain is
    exhausted. See `try_chain`'s `on_attempt` callback in
    `services/provider_chain.py`.
    """
    from datetime import datetime, timezone
    from models.cast_render import CastRender
    from sqlalchemy.orm.attributes import flag_modified
    factory = _make_session_factory()
    async with factory() as session:
        render = await session.get(CastRender, render_id)
        if not render:
            return
        statuses = list(render.block_statuses or [])
        found = False
        for row in statuses:
            if row.get("block_id") == block_id:
                for k, v in updates.items():
                    # Timestamps: serialize to ISO for JSON
                    if isinstance(v, datetime):
                        v = v.isoformat()
                    row[k] = v
                if append_attempt is not None:
                    history = list(row.get("provider_attempts") or [])
                    history.append({**append_attempt, "at": datetime.now(timezone.utc).isoformat()})
                    row["provider_attempts"] = history
                found = True
                break
        if not found:
            return
        render.block_statuses = statuses
        flag_modified(render, "block_statuses")
        await session.commit()


# How long a block can sit with no progress signal (no new provider attempt,
# no completion, no error) before we give up on it. Chosen well below the
# task's own 3600s hard kill: a Celery hard time_limit is a SIGKILL-style
# stop that never runs cleanup code, so without this reaper a genuinely stuck
# render sits at status="baking" forever — and since Setup/Script editing is
# now locked while any render is baking/queued/composing, a permanent zombie
# render would permanently lock that cast's editing too. Deliberately
# per-block silence, not "whole render older than N minutes" — a render with
# several blocks can legitimately take well past this window in total (one
# real successful block took ~14 min), so only a block that's gone quiet is
# treated as dead.
STALE_BLOCK_MINUTES = 18


@celery_app.task(name="cleanup_stale_cast_renders")
def cleanup_stale_cast_renders():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_cleanup_stale_cast_renders_async())
    except Exception as exc:
        logger.error(f"Stale cast-render cleanup failed: {exc}")
        sentry_sdk.capture_exception(exc)
    finally:
        loop.close()


async def _cleanup_stale_cast_renders_async():
    from datetime import timedelta
    from sqlalchemy import select
    from models.cast_render import CastRender, CastRenderStatus

    factory = _make_session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_BLOCK_MINUTES)

    async with factory() as session:
        result = await session.execute(
            select(CastRender).where(CastRender.status.in_([
                CastRenderStatus.QUEUED.value,
                CastRenderStatus.BAKING.value,
                CastRenderStatus.COMPOSING.value,
            ]))
        )
        active_renders = result.scalars().all()

        for render in active_renders:
            stale_block = None
            for row in (render.block_statuses or []):
                if row.get("state") != "baking":
                    continue
                # Last known activity for this block: its own start, or the
                # most recent provider-fallback attempt recorded via
                # try_chain's on_attempt callback — whichever is later. A
                # block that's still cycling through fallback tiers is
                # making progress and shouldn't be reaped just because it's
                # been baking a while in total.
                timestamps = []
                if row.get("started_at"):
                    timestamps.append(row["started_at"])
                for attempt in (row.get("provider_attempts") or []):
                    if attempt.get("at"):
                        timestamps.append(attempt["at"])
                if not timestamps:
                    continue
                try:
                    last_activity = max(datetime.fromisoformat(t) for t in timestamps)
                except ValueError:
                    continue
                if last_activity < cutoff:
                    stale_block = row
                    break

            if stale_block is None:
                continue

            logger.warning(
                "Reaping stale render %s — block %s silent since %s (no progress in %d+ min)",
                render.id, stale_block.get("block_id"), stale_block.get("started_at"), STALE_BLOCK_MINUTES,
            )
            sentry_sdk.capture_message(
                f"cast_render {render.id} reaped: block {stale_block.get('block_id')} "
                f"had no progress for {STALE_BLOCK_MINUTES}+ minutes",
                level="warning",
            )
            if render.celery_task_id:
                try:
                    celery_app.control.revoke(render.celery_task_id)
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
            render.status = CastRenderStatus.FAILED.value
            render.error_message = (
                f"Render timed out — block #{(stale_block.get('index', 0) or 0) + 1} "
                f"had no progress for over {STALE_BLOCK_MINUTES} minutes. Please retry."
            )
            render.completed_at = datetime.now(timezone.utc)

        await session.commit()


async def _render_clip_from_parent(render_id: str, child_cast_id: str) -> None:
    """FFmpeg-trim a child clip cast out of its parent's composed mp4.

    Lives in the same render queue as full GPU renders so the user sees
    the same progress UI, but skips bake + GPU composition entirely.
    Typical wall-clock: a few seconds per minute of clip.
    """
    import os as _os
    import tempfile
    from models.cast import Cast, CastStatus
    from models.cast_render import CastRender, CastRenderStatus
    from services.r2_storage import get_r2_storage_service
    from services.cast_ffmpeg_composer import compose_clip_from_parent

    factory = _make_session_factory()
    r2 = get_r2_storage_service()

    async with factory() as session:
        render = await session.get(CastRender, render_id)
        if not render:
            raise RuntimeError(f"CastRender {render_id} not found")
        if render.status in {CastRenderStatus.READY.value, CastRenderStatus.FAILED.value, "cancelled"}:
            logger.info("Clip render %s already terminal (%s); skipping.", render_id, render.status)
            return
        child = await session.get(Cast, child_cast_id)
        if not child:
            raise RuntimeError(f"Child cast {child_cast_id} not found")
        parent_id = child.clip_parent_cast_id
        block_ids = list(child.clip_block_ids or [])
        user_id = child.user_id
        production_level = child.production_level or "standard"
        # Snapshot at render-creation time (CastRender.quality), not the
        # Cast row's live quality — avoids billing the wrong tier if the
        # user edits quality while this render is still in flight.
        render_quality = render.quality or (child.quality.value if child.quality else None) or "simple"
        parent = await session.get(Cast, parent_id) if parent_id else None
        if not parent:
            raise RuntimeError(f"Parent cast {parent_id} not found for clip child {child_cast_id}")
        parent_timeline = render.timeline_snapshot or {}
        # Fall back to the parent's own snapshot if the render's snapshot
        # doesn't carry block_id metadata (older renders pre-PR β).
        if not parent_timeline.get("tracks"):
            stored = (parent.timeline_json or {}).get("default", {}).get("twick_data") or {}
            if stored:
                parent_timeline = stored
        parent_video_url = parent.final_video_url

    if not parent_video_url:
        # Mark failed rather than crashing — user-actionable message.
        await _update_render(
            render_id,
            status=CastRenderStatus.FAILED.value,
            error_message="Parent cast has no rendered video — render the parent first.",
        )
        return
    if not block_ids:
        await _update_render(
            render_id,
            status=CastRenderStatus.FAILED.value,
            error_message="Clip has no block IDs — re-approve the clip suggestion.",
        )
        return

    await _update_render(
        render_id,
        status=CastRenderStatus.COMPOSING.value,
        progress_step="Trimming clip from parent",
        progress_percent=20,
    )

    # FFmpeg can stream from a presigned URL — no need to download the
    # parent first. Range requests on R2 mean we read just the bytes the
    # trim windows need.
    parent_input = parent_video_url
    try:
        signed_key = parent.final_video_url.split("/", 3)[-1] if parent.final_video_url else None
        if signed_key:
            try:
                parent_input = r2.get_signed_url(signed_key, expires_in=3600)
            except Exception:
                parent_input = parent_video_url
    except Exception:
        parent_input = parent_video_url

    tmp_dir = tempfile.gettempdir()
    output_path = _os.path.join(tmp_dir, f"clip_{child_cast_id}.mp4")

    try:
        result = await compose_clip_from_parent(
            parent_video_input=parent_input,
            block_ids=block_ids,
            parent_timeline=parent_timeline,
            output_path=output_path,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        await _update_render(
            render_id,
            status=CastRenderStatus.FAILED.value,
            error_message=str(exc)[:1000],
        )
        return

    output_key = f"renders/clips/{child_cast_id}.mp4"
    try:
        await r2.upload_file(output_path, output_key, content_type="video/mp4")
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        await _update_render(
            render_id,
            status=CastRenderStatus.FAILED.value,
            error_message=f"Upload failed: {exc}",
        )
        return
    finally:
        try:
            _os.remove(output_path)
        except OSError:
            pass

    final_url = r2.get_public_url(output_key)
    await _update_render(
        render_id,
        status=CastRenderStatus.READY.value,
        output_video_r2_key=output_key,
        completed_at=datetime.now(timezone.utc),
        progress_percent=100,
        progress_step="Complete",
        duration_seconds=result.get("duration_seconds"),
    )

    async with factory() as session:
        from models.cast import Cast as _CastUpd
        c = await session.get(_CastUpd, child_cast_id)
        if c:
            c.final_video_url = final_url
            c.status = CastStatus.READY
            await session.commit()

    # PR #18 cost instrumentation. FFmpeg-only compose step. HOSTKEY is
    # decommissioned (PR #94), so this is labelled as a generic cloud
    # compose provider rather than the dead 'hostkey' box — that stale
    # label was the source of the lingering 'hostkey' rows on the admin
    # Cost-by-Provider table. Provider cost = 0 (compose-only, no GPU
    # inference); the quantity is video-seconds for analytics.
    try:
        from database import async_session_factory as _sf
        from services.usage_tracker import log_usage
        async with _sf() as _us:
            await log_usage(
                _us,
                user_id=user_id,
                event_type="clip_compose",
                provider="cloud_compose",
                provider_cost_usd=0.0,
                quantity=float(result.get("duration_seconds") or 0.0),
                quantity_unit="video_seconds",
                resource_type="cast",
                resource_id=child_cast_id,
                render_id=render_id,
            )
            await _us.commit()
    except Exception as _u_exc:
        sentry_sdk.capture_exception(_u_exc)

    try:
        from services import billing_service
        async with factory() as _bill_session:
            await billing_service.deduct_render_usage(
                _bill_session,
                user_id=user_id,
                owner_id=user_id,
                render_id=render_id,
                cast_id=child_cast_id,
                duration_seconds=float(result.get("duration_seconds") or 0.0),
                production_level=production_level,
                quality=render_quality,
            )
    except Exception as bill_exc:
        sentry_sdk.capture_exception(bill_exc)
        logger.error("Clip render %s: billing metering failed: %s", render_id, bill_exc)

    logger.info("Clip render %s complete → %s", render_id, output_key)


async def _render_async(task, render_id: str):
    from models.cast_render import CastRender, CastRenderStatus
    from services.r2_storage import get_r2_storage_service
    from services.runpod import RunPodService
    from config import settings

    factory = _make_session_factory()

    # ── Child-cast (clip) shortcut ──
    # If this render belongs to a Cast that has `clip_parent_cast_id` set,
    # the user approved a clip suggestion and we render it by FFmpeg-
    # trimming the parent's already-composed mp4 — no GPU bake, no
    # composition dispatch. Bail out early once that's done.
    async with factory() as _peek_session:
        from models.cast import Cast as _Cast
        _peek_render = await _peek_session.get(CastRender, render_id)
        if _peek_render and getattr(_peek_render, "cast_id", None):
            _peek_cast = await _peek_session.get(_Cast, _peek_render.cast_id)
            if _peek_cast is not None and getattr(_peek_cast, "clip_parent_cast_id", None):
                await _render_clip_from_parent(render_id, _peek_cast.id)
                return

    async with factory() as session:
        render = await session.get(CastRender, render_id)
        if not render:
            raise RuntimeError(f"CastRender {render_id} not found")

        # ---- Idempotency guard ----------------------------------------
        # task_acks_late=True means a task message stays on the broker
        # until the task FINISHES. If a worker is killed mid-render
        # (force-recreate, OOM, etc.) the broker re-queues the message
        # and the new worker re-runs the SAME render against rows that
        # may already be terminal. Without this guard, a single worker
        # restart can spawn 4+ zombie redeliveries that race each other,
        # mark blocks "baking" forever, and burn GPU credits. Bail out
        # immediately if the render row is already in a terminal state.
        # CastRenderStatus only has READY + FAILED today; we also treat the
        # string "cancelled" as terminal in case anyone marks a render that
        # way via SQL during recovery (the enum may grow later).
        terminal = {
            CastRenderStatus.READY.value,
            CastRenderStatus.FAILED.value,
            "cancelled",
        }
        if render.status in terminal:
            logger.info(
                "Render %s already terminal (status=%s); ACKing and exiting.",
                render_id, render.status,
            )
            return

        timeline = render.timeline_snapshot
        cast_id = render.cast_id
        user_id = render.user_id

        # Load cast quality setting
        from models.cast import Cast
        cast = await session.get(Cast, cast_id)
        cast_quality = cast.quality.value if cast and cast.quality else "simple"
        # Per-cast music-volume override (cast row's ``music_volume`` column).
        # Threaded into the composition payload so the composer can prefer it
        # over the env/hard-coded default when no music element carries its own
        # ``props.volume``. Null leaves the env default in charge.
        cast_music_volume = getattr(cast, "music_volume", None) if cast else None
        # Capture the user's edited timeline from cast.timeline_json so we can
        # apply per-block start/end edits made in the editor at render time.
        # The editor writes these to cast.timeline_json["default"]["twick_data"]
        # — see _per_block_user_durations() below.
        cast_timeline_json = dict(cast.timeline_json) if cast and cast.timeline_json else {}

        # Step 4 — load the cast's layout template ONCE so the per-block
        # placement decisions below can honour its `face` / `overlay` slot.
        # Behind LAYOUT_TEMPLATE_COMPOSER_ENABLED (default true). We capture
        # the raw `config` dict (detached from the session) plus a couple of
        # plain values so the dispatch closure — which runs outside this
        # session — never touches a lazy ORM relationship. The overlay anchor
        # is template-wide, so we resolve it here once rather than per block.
        layout_template_id = getattr(cast, "layout_template_id", None) if cast else None
        layout_template_config: dict | None = None
        template_face: str | None = None
        template_overlay_anchor: str | None = None
        if _layout_template_composer_enabled() and layout_template_id:
            try:
                from models.layout_template import LayoutTemplate
                from services.twick_compositor_adapter import resolve_overlay_placement
                _tpl = await session.get(LayoutTemplate, layout_template_id)
                if _tpl is not None and isinstance(_tpl.config, dict):
                    layout_template_config = dict(_tpl.config)
                    _face = layout_template_config.get("face")
                    template_face = _face if isinstance(_face, str) else None
                    template_overlay_anchor = resolve_overlay_placement(
                        {"config": layout_template_config}
                    ).get("anchor")
            except Exception as e:
                sentry_sdk.capture_exception(e)

    QUALITY_TO_SIZE = {
        "simple": "480p",
        "hd": "720p",
        "hd_plus": "1080p",
    }
    render_size = QUALITY_TO_SIZE.get(cast_quality, "480p")
    logger.info("Render %s: quality=%s → size=%s", render_id, cast_quality, render_size)

    sentry_sdk.set_tag("cast_id", cast_id)
    sentry_sdk.set_tag("user_id", user_id)

    # PR #71: user-requested duration cap. Resolved here so the
    # post-TTS-refresh rewrite below knows what to fit inside. The
    # actual timeline rewrite happens AFTER the per-block `using=`
    # durations are known — we never proportional-scale the snapshot
    # back to a target the way PR #67 used to, because the bake /
    # normalize / stitch stages all need the REAL per-block durations
    # as the single source of truth.
    try:
        target_s = float(getattr(cast, "duration_target_seconds", 0) or 0)
    except Exception as _dur_exc:
        sentry_sdk.capture_exception(_dur_exc)
        target_s = 0.0

    # ── Pass 1: bake bonded blocks ──
    bonded_blocks = extract_bonded_blocks_from_timeline(timeline)
    if not bonded_blocks:
        raise RuntimeError("No bonded blocks found in timeline snapshot")

    # User-edited per-block durations from the editor's working timeline.
    # Empty {} when the cast hasn't been edited yet — we fall back to
    # the raw TTS duration in that case.
    user_durations = _per_block_user_durations(cast_timeline_json)
    if user_durations:
        logger.info("User timeline edits found for %d blocks", len(user_durations))

    await _update_render(render_id,
                         status=CastRenderStatus.BAKING.value,
                         baking_chunks_total=len(bonded_blocks),
                         render_attempt=(await _get_render_attempt(render_id)) + 1)
    # Seed per-block status rows so the frontend RenderStatusPill can show each
    # block's state (queued → baking → done) with provider + timing.
    await _init_block_statuses(render_id, bonded_blocks)

    r2 = get_r2_storage_service()
    runpod = RunPodService()
    baked_urls: dict[str, str] = {}
    # regression-2: per-block URL of the EXACT audio fed to the lipsync
    # engine (post loudnorm + edge-pad + silence-trim). After baking we
    # rewrite each block's A1 timeline src to this URL so the compose +
    # mux passes lay down byte-identical audio — lips can't drift from a
    # track they were synced to.
    lipsync_audio_by_block: dict[str, str] = {}
    # The REAL, audio-driven duration a voiceover block's bake targeted
    # (broll_s — always the probed TTS/lipsync-prep length, never the
    # Arrange-timeline slot; see the voiceover branch below). Registered
    # regardless of whether the bake itself succeeds, because the post-bake
    # duration reconciliation (_measure_baked_block_durations) can only
    # measure blocks that produced a baked clip — a block whose bake FAILS
    # (e.g. a transient ffmpeg timeout) never gets measured, so its
    # Arrange-timeline slot is never corrected. Confirmed on a real render:
    # a voiceover block's bake correctly targeted 7.078s (audio-driven) but
    # timed out on both its B-roll and avatar-idle-fallback attempts; with
    # no measurement to override it, compose still trimmed that block's
    # CORRECT, freshly-fetched narration audio down to its stale 4.0s
    # Arrange slot — chopping the sentence off mid-way just as the next
    # block's (correctly-placed) audio started, which is indistinguishable
    # from genuine overlap to a listener. Merged into measured_durations
    # before the post-bake _apply_real_block_durations call so voiceover
    # slots always reconcile to their real length whether or not the bake
    # produced a clip to measure.
    voiceover_real_durations: dict[str, float] = {}

    # Resolve the cast's product reference image + product names once so the
    # per-block bake can decide whether to route through the product-
    # conditioned base provider. We pick the FIRST product (position=0) as
    # the canonical reference — multi-product casts are rare today, and
    # this matches the user's "single uploaded product" workflow. If the
    # cast has no products, the speaking chain runs unchanged.
    cast_product_image_url: str | None = None
    cast_product_image_urls: list[str] = []
    cast_product_names: list[str] = []
    try:
        from models.cast import Cast as _Cast
        from models.product import Product as _Product
        from models.cast import CastProduct as _CastProduct
        from sqlalchemy import select as _sa_select
        async with factory() as _prod_session:
            res = await _prod_session.execute(
                _sa_select(_Product)
                .join(_CastProduct, _CastProduct.product_id == _Product.id)
                .where(_CastProduct.cast_id == cast_id)
                .order_by(_CastProduct.position.asc())
            )
            products_for_cast = list(res.scalars().all())
        for p in products_for_cast:
            for nm in (p.name, p.title):
                if nm and nm.strip():
                    cast_product_names.append(nm.strip())
        for p in products_for_cast:
            cover_key = getattr(p, "cover_image_key", None) or ""
            if cover_key:
                url = r2.get_public_url(cover_key)
                if url and url not in cast_product_image_urls:
                    cast_product_image_urls.append(url)
        if cast_product_image_urls:
            cast_product_image_url = cast_product_image_urls[0]
            logger.info(
                "Render %s: %d product reference image(s) resolved",
                render_id, len(cast_product_image_urls),
            )
    except Exception as _prod_exc:
        sentry_sdk.capture_exception(_prod_exc)
        logger.warning(
            "Render %s: product reference lookup failed (%s); "
            "speaking blocks will not run the product-conditioned bake",
            render_id, _prod_exc,
        )
        cast_product_image_url = None
        cast_product_image_urls = []
        cast_product_names = []

    # ── Pass 1a: submit ALL bake jobs in parallel ──
    pending_jobs = []  # list of (idx, block_id, job_id, v1_element, a1_element, baked_key, duration_s)
    completed_count = 0  # atomic counter for out-of-order completions

    for idx, (v1_element, a1_element) in enumerate(bonded_blocks):
        # Audio-only voiceover pairs: v1_element is None. Use the A1's
        # block_id and a synthetic baked_key so the render still has a
        # stable handle for status updates / R2 paths. The actual baking
        # path for voiceover skips InfiniteTalk anyway (handled below at
        # `if block_render_mode == "voiceover"`).
        primary_el = v1_element if v1_element is not None else a1_element
        block_id = (primary_el.get("metadata") or {}).get("block_id", "unknown")
        baked_key = f"renders/{render_id}/baked_blocks/{primary_el['id']}.mp4"

        # Resume: skip if already baked in a previous attempt.
        # Intentionally disabled for now — historically we've had bad bakes
        # (wrong-avatar drift from HOSTKEY) get immortalized by this cache,
        # because once a bad v1_blk_xxxxx.mp4 is written to R2 every future
        # attempt on the same render_id will reuse it without ever re-baking.
        # Real "resume" only helps if a task died mid-bake, which is rare.
        # The cost of always re-baking is bounded (same render_id re-runs
        # only happen on retry, which itself is rare). Leaving the key-check
        # code below commented so the intent is preserved.
        # if await r2.key_exists(baked_key):
        #     logger.info("Block %s already baked (resume), skipping", block_id)
        #     baked_urls[v1_element["id"]] = r2.get_public_url(baked_key)
        #     completed_count += 1
        #     await _update_render(render_id, baking_chunks_completed=completed_count)
        #     now = datetime.now(timezone.utc)
        #     await _update_block_status(render_id, block_id,
        #         state="done", provider="cache", started_at=now, completed_at=now, duration_s=0.0)
        #     continue

        # Extract URLs from element props — fallback to avatar face_ref_key.
        # Silent action / body_motion blocks have a1_element=None (no
        # audio element on the timeline); source duration from V1 in that
        # case so the I2V bake gets the correct clip length.
        face_ref_url = (v1_element or {}).get("props", {}).get("src", "") if v1_element else ""
        audio_url = (a1_element.get("props") or {}).get("src", "") if a1_element else ""
        if a1_element is not None:
            tts_duration_s = a1_element.get("e", 5) - a1_element.get("s", 0)
        else:
            v1_e = (v1_element or {}).get("e", 5)
            v1_s = (v1_element or {}).get("s", 0)
            tts_duration_s = float(v1_e) - float(v1_s)

        # ── TTS freshness guard (defense-in-depth for Bug A) ──
        # The timeline snapshot was taken when the render was queued and
        # may reference an audio file that was baked from an OLDER script.
        # If the active variant's script_text has been edited since the
        # last TTS bake (or the R2 object is missing), regenerate
        # synchronously here so the avatar speaks the CURRENT script.
        try:
            audio_url, fresh_dur = await _ensure_fresh_tts_for_block(
                r2, factory,
                cast_id=cast_id,
                user_id=user_id,
                block_id=block_id,
                snapshot_audio_url=audio_url,
            )
            if fresh_dur and abs(float(fresh_dur) - float(tts_duration_s)) > 0.05:
                logger.info(
                    "Block %s: refreshed TTS duration %.2fs (was %.2fs)",
                    block_id, float(fresh_dur), float(tts_duration_s),
                )
                tts_duration_s = float(fresh_dur)
        except Exception as _fresh_exc:
            sentry_sdk.capture_exception(_fresh_exc)

        # PR #65: prefer the 16 kHz WAV lipsync output for the lipsync
        # engine (InfiniteTalk / MuseTalk / Kling) when the variant has
        # one. The compose audio remux (PR #64) still reads the 44.1 kHz
        # MP3 master from tts_r2_key — only the lipsync bake is swapped.
        try:
            lipsync_url = await _resolve_lipsync_audio_url(
                factory, r2, block_id=block_id, fallback_url=audio_url,
            )
            if lipsync_url and lipsync_url != audio_url:
                logger.info(
                    "Block %s: using post-processed lipsync WAV (%s)",
                    block_id, lipsync_url[:80],
                )
                audio_url = lipsync_url
        except Exception as _lipsync_exc:
            sentry_sdk.capture_exception(_lipsync_exc)

        # PR #82: lipsync audio prep. -16 LUFS loudness normalize + edge
        # padding + trailing-silence trim. Without this, raw TTS at
        # broadcast -23 LUFS doesn't drive the engine amplitude gate
        # reliably (visible as stretches of mouth-held-open) and the
        # rolling-window encoders drift at segment boundaries because
        # there's no edge anchor. Idempotent on R2 — re-renders of the
        # same block reuse the cached prep'd WAV.
        if audio_url:
            try:
                from services.lipsync_audio_prep import prepare_lipsync_audio
                prep_url = await prepare_lipsync_audio(
                    audio_url, render_id, block_id, r2=r2,
                )
                if prep_url and prep_url != audio_url:
                    logger.info(
                        "Block %s: using prep'd lipsync audio (%s)",
                        block_id, prep_url[:80],
                    )
                    audio_url = prep_url
            except Exception as _prep_exc:
                # Already captured to Sentry inside the helper. Fall
                # back to the un-prepared URL so the render proceeds.
                sentry_sdk.capture_exception(_prep_exc)
                logger.warning(
                    "Block %s: lipsync audio prep failed (%s); using raw audio",
                    block_id, _prep_exc,
                )

        # Apply the user's per-block edit if one exists. The HOSTKEY
        # InfiniteTalk worker derives output length from the audio it's
        # given, so when the user has shortened a block we trim the audio
        # to match further down. Round to FPS grid is already done by
        # _per_block_user_durations.
        user_target_s = user_durations.get(block_id)
        # Guard against a stale/bogus "user edit". _per_block_user_durations
        # reads whatever [s,e] window is saved in the Arrange-phase timeline
        # for this block and treats it as an intentional trim — but that
        # window can be a leftover placeholder from before this block's TTS
        # settled at its current duration (e.g. captured when the Arrange tab
        # was first opened, or before a later TTS refresh/regeneration), never
        # resynced afterward. An absolute floor alone isn't enough: render
        # rnd_25a6a0c2b241 hard-failed on block blk_88f2a4cd3183 whose saved
        # slot was 1.267s against a refreshed tts_duration of 4.65s (27%) —
        # comfortably above the old 0.5s floor, but the resulting head-trimmed
        # clip was 89% frozen and got hard-rejected by Phase 3 with no
        # fallback, killing the whole render. A trim down to a small fraction
        # of the actual reading also isn't a plausible pacing edit — few
        # people intentionally cut a line to a quarter of its spoken length —
        # so below this ratio (as well as below the absolute structural
        # floor) we don't trust it and use the real TTS duration instead.
        # This can never make a genuinely-intended trim worse: a trim that
        # aggressive would very likely have failed the freeze validator
        # anyway, and this way the render degrades to "full-length clip" +
        # a log line instead of failing the entire cast.
        from services.media_processing import (
            _VALIDATE_MIN_DURATION_S as _clip_min_duration_s,
        )
        _STALE_USER_TARGET_RATIO = 0.35
        _stale_threshold_s = max(
            _clip_min_duration_s, float(tts_duration_s or 0) * _STALE_USER_TARGET_RATIO,
        )
        if user_target_s is not None and user_target_s < _stale_threshold_s:
            logger.warning(
                "Block %s: ignoring implausible user_target=%.2fs (below "
                "%.2fs — max of the %.2fs structural floor and %.0f%% of "
                "tts=%.2fs) — likely a stale Arrange-timeline slot from "
                "before this block's TTS settled at its current duration; "
                "using tts=%.2fs instead",
                block_id, user_target_s, _stale_threshold_s, _clip_min_duration_s,
                _STALE_USER_TARGET_RATIO * 100, tts_duration_s, tts_duration_s,
            )
            user_target_s = None
        duration_s = (
            float(user_target_s) if user_target_s is not None else float(tts_duration_s)
        )
        logger.info(
            "Block %s: tts=%.2fs, user_target=%s, using=%.2fs",
            block_id,
            float(tts_duration_s),
            f"{user_target_s:.2f}s" if user_target_s is not None else "n/a",
            duration_s,
        )

        # Fallback: if face URL is missing or incomplete, get from avatar
        if not face_ref_url or "/face_ref" not in face_ref_url:
            try:
                from models.avatar import Avatar
                from models.cast import Cast
                async with factory() as fallback_session:
                    cast_obj = await fallback_session.get(Cast, cast_id)
                    if cast_obj and cast_obj.avatar_id:
                        avatar = await fallback_session.get(Avatar, cast_obj.avatar_id)
                        if avatar and avatar.face_ref_key:
                            face_ref_url = f"https://media.luminacast.com/{avatar.face_ref_key}"
                            logger.info("Using avatar face_ref_key fallback: %s", face_ref_url[:80])
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning("Failed to get avatar face fallback: %s", e)

        # Round-6 Bug B round-3: for a non-MEDIUM framing, upgrade the lip-sync
        # face reference to the reusable talking-head look generated for that
        # exact framing. The timeline snapshot bakes the avatar's default
        # (MEDIUM) face into v1.props.src, so without this every talk block
        # renders the same shot regardless of its framing. Only override when a
        # ready framing-matched look exists; MEDIUM / no framing keep the
        # snapshot face. Best-effort — a lookup failure leaves the face as-is.
        try:
            from models.avatar_look import (
                AvatarLook,
                TALKING_HEAD_LOOK_TYPE,
                DEFAULT_FRAMING,
            )
            from models.block import Block as _Block
            from models.cast import Cast as _Cast
            from sqlalchemy import select as _th_select
            async with factory() as _th_session:
                _th_blk = await _th_session.get(_Block, block_id)
                _th_cst = await _th_session.get(_Cast, cast_id) if cast_id else None
                _th_framing = (
                    (getattr(_th_blk, "framing", None) or DEFAULT_FRAMING).strip().upper()
                    if _th_blk else DEFAULT_FRAMING
                )
                _th_is_action = bool(_th_blk) and (
                    _th_blk.category == "avatar_action"
                    or _th_blk.render_mode == "body_motion"
                )
                if (
                    _th_cst
                    and getattr(_th_cst, "avatar_id", None)
                    and not _th_is_action
                    and _th_framing != DEFAULT_FRAMING
                ):
                    _th_row = (
                        await _th_session.execute(
                            _th_select(AvatarLook)
                            .where(AvatarLook.avatar_id == _th_cst.avatar_id)
                            .where(AvatarLook.look_type == TALKING_HEAD_LOOK_TYPE)
                            .where(AvatarLook.framing == _th_framing)
                            .where(AvatarLook.status == "ready")
                            .order_by(AvatarLook.created_at.desc())
                            .limit(1)
                        )
                    ).scalars().first()
                    if _th_row and _th_row.face_ref_key:
                        # Effective mic-on: per-block override > the block's
                        # scene (pinned look, else avatar default) own
                        # mic_visible. Talking-head looks are per-framing
                        # reference stills, not scenes — they're never
                        # created via AddLookDialog so their own mic_visible
                        # is always the column default and meaningless here.
                        # Without this, every non-MEDIUM-framing block (the
                        # common case) bypassed mic-on entirely, regardless
                        # of the scene's setting or resolve_scene_voice_settings
                        # already having picked the clip-mic audio chain for it.
                        _th_mic_on = getattr(_th_blk, "mic_on", None)
                        if _th_mic_on is None:
                            _th_scene_look = None
                            if getattr(_th_blk, "avatar_look_id", None):
                                _th_scene_look = await _th_session.get(
                                    AvatarLook, _th_blk.avatar_look_id
                                )
                            if _th_scene_look is None:
                                _th_scene_res = await _th_session.execute(
                                    _th_select(AvatarLook)
                                    .where(AvatarLook.avatar_id == _th_cst.avatar_id)
                                    .where(AvatarLook.is_default.is_(True))
                                    .limit(1)
                                )
                                _th_scene_look = _th_scene_res.scalars().first()
                            _th_mic_on = bool(getattr(_th_scene_look, "mic_visible", False))

                        from services.mic_on_look import resolve_mic_on_face_key
                        _th_face_key = await resolve_mic_on_face_key(
                            _th_mic_on, _th_cst.avatar_id, _th_row.id,
                            _th_row.face_ref_key, _th_session,
                        )
                        face_ref_url = r2.get_public_url(_th_face_key)
                        logger.info(
                            "Block %s: using talking-head face look=%s framing=%s mic_on=%s",
                            block_id, _th_row.id, _th_framing, _th_mic_on,
                        )
        except Exception as _th_exc:
            sentry_sdk.capture_exception(_th_exc)

        # Per-block SCENE override. The editor timeline bakes the cast's
        # default avatar face into v1.props.src for every speaking block, so a
        # scene the user picked in the Script tab (block.avatar_look_id) was
        # silently ignored at bake time — the render always showed the default
        # backdrop. If that look is a ready background/scene look with an
        # image, use it as the lip-sync reference. An explicit pick wins over
        # the framing-matched talking-head look resolved above.
        try:
            from models.avatar_look import AvatarLook as _SceneLook
            from models.block import Block as _SceneBlock
            from models.cast import Cast as _SceneCast
            async with factory() as _sc_session:
                _sc_blk = await _sc_session.get(_SceneBlock, block_id)
                _sc_look_id = getattr(_sc_blk, "avatar_look_id", None) if _sc_blk else None
                _sc_is_action = bool(_sc_blk) and (
                    _sc_blk.category == "avatar_action"
                    or _sc_blk.render_mode == "body_motion"
                )
                if _sc_look_id and not _sc_is_action:
                    _sc_look = await _sc_session.get(_SceneLook, _sc_look_id)
                    if (
                        _sc_look is not None
                        and _sc_look.status == "ready"
                        and getattr(_sc_look, "face_ref_key", None)
                    ):
                        _sc_cst = await _sc_session.get(_SceneCast, cast_id) if cast_id else None
                        _sc_avatar_id = getattr(_sc_cst, "avatar_id", None) if _sc_cst else None
                        _sc_mic_on = getattr(_sc_blk, "mic_on", None)
                        if _sc_mic_on is None:
                            _sc_mic_on = bool(getattr(_sc_look, "mic_visible", False))
                        from services.mic_on_look import resolve_mic_on_face_key
                        _sc_key = await resolve_mic_on_face_key(
                            _sc_mic_on, _sc_avatar_id, _sc_look.id,
                            _sc_look.face_ref_key, _sc_session,
                        )
                        face_ref_url = r2.get_public_url(_sc_key)
                        logger.info(
                            "Block %s: using picked scene look=%s (%s) mic_on=%s",
                            block_id, _sc_look.id, _sc_look.name, _sc_mic_on,
                        )
        except Exception as _sc_exc:
            sentry_sdk.capture_exception(_sc_exc)

        # avatar_motion blocks: T2V renders directly from the prompt and
        # has no face/audio dependency at the dispatch layer (voiceover, if
        # any, is muxed in by the FFmpeg compose pass via the A1 element).
        # For audio-only voiceover blocks v1_element is None; pull metadata
        # from A1 instead so render_mode is correctly detected.
        v1_meta = (v1_element.get("metadata") if v1_element else None) or (a1_element.get("metadata") or {})
        is_motion_block = v1_meta.get("render_mode") == "motion"
        is_voiceover_block = v1_meta.get("render_mode") == "voiceover"
        is_body_motion_block = v1_meta.get("render_mode") == "body_motion"

        # Voiceover blocks legitimately have no face_ref_url — audio plays
        # over parallel-media overlays. Don't skip them on that basis.
        # Action / body_motion blocks legitimately have no audio when the
        # variant has no dialogue — they still bake (silently) via the
        # I2V pipeline. Bug B: previously these blocks were silently
        # dropped from the render and missing from the final mux.
        if (
            not is_motion_block
            and not is_voiceover_block
            and not is_body_motion_block
            and (not face_ref_url or not audio_url)
        ):
            logger.warning("Block %s missing face_ref or audio URL, skipping bake", block_id)
            continue
        if is_voiceover_block and not audio_url:
            logger.warning("Voiceover block %s missing audio URL, skipping bake", block_id)
            continue
        if is_body_motion_block and not audio_url:
            logger.info(
                "Block %s is an action block with no dialogue — baking silently",
                block_id,
            )

        motion_prompt = v1_meta.get("motion_prompt") \
                        or "A person talking naturally to the camera"

        # "Horizontal video heavily zoomed in" root-cause fix: every avatar
        # reference photo is authored/generated PORTRAIT-only regardless of
        # the cast's output format. When the canvas is landscape (or
        # square), handing that portrait photo to the I2V/talking-head
        # provider as-is forces IT to crop internally to fill the requested
        # frame — pixels it throws away can never be recovered by a
        # downstream compositor fix. Conform the reference photo to the
        # cast's actual canvas shape here, before it's sent: cheap
        # cover-crop when the shapes are already close (unchanged
        # behaviour), contain-fit + blurred backdrop (nothing cropped, no
        # black bar) when they diverge — e.g. a 9:16 photo for a 16:9
        # canvas. Cached in R2 by (source url, target shape) so repeat
        # blocks/renders for the same avatar don't re-transform. Every
        # consumer of face_ref_url downstream (HOSTKEY, cloud dispatcher,
        # sync-lipsync refine) reads it out of this same pending_jobs tuple,
        # so fixing it once here covers all of them.
        if face_ref_url:
            try:
                _cf_w, _cf_h, _ = _canvas_dims_for_render(timeline)
                if _cf_w > 0 and _cf_h > 0:
                    async with httpx.AsyncClient(timeout=30.0) as _cf_http:
                        _cf_resp = await _cf_http.get(face_ref_url)
                        _cf_resp.raise_for_status()
                        _cf_src_bytes = _cf_resp.content
                    from PIL import Image as _CfImage
                    import io as _cf_io
                    with _CfImage.open(_cf_io.BytesIO(_cf_src_bytes)) as _cf_probe:
                        _cf_src_w, _cf_src_h = _cf_probe.size
                    from services.aspect_conform import shapes_diverge
                    if shapes_diverge(_cf_src_w, _cf_src_h, _cf_w, _cf_h):
                        import hashlib as _cf_hashlib
                        _cf_hash = _cf_hashlib.sha256(
                            face_ref_url.encode("utf-8")
                        ).hexdigest()[:20]
                        _cf_key = f"cache/aspect_conform/{_cf_hash}_{_cf_w}x{_cf_h}.jpg"
                        if not await r2.key_exists(_cf_key):
                            from services.aspect_conform import conform_image_bytes
                            _cf_out = await asyncio.to_thread(
                                conform_image_bytes, _cf_src_bytes, _cf_w, _cf_h,
                            )
                            await r2.upload_bytes(
                                _cf_out, _cf_key, content_type="image/jpeg",
                                cache_control="public, max-age=31536000, immutable",
                            )
                        face_ref_url = r2.get_public_url(_cf_key)
                        logger.info(
                            "Block %s: conformed avatar reference %dx%d -> "
                            "canvas %dx%d (was diverging shape)",
                            block_id, _cf_src_w, _cf_src_h, _cf_w, _cf_h,
                        )
            except Exception as _cf_exc:
                sentry_sdk.capture_exception(_cf_exc)
                logger.warning(
                    "Block %s: face_ref aspect-conform failed (%s); using "
                    "original reference photo",
                    block_id, _cf_exc,
                )

        pending_jobs.append((idx, block_id, v1_element, a1_element, baked_key, duration_s,
                             face_ref_url, audio_url, motion_prompt))

    if not pending_jobs and not baked_urls:
        raise RuntimeError("No blocks submitted for baking")

    # PR #71: rewrite the timeline snapshot so every element's s/e
    # reflects the REAL per-block `using=` duration. The previous
    # implementation (`_clamp_render_timeline_to_target`) uniformly
    # scaled all blocks down to fit `target_s`, which left the bake +
    # normalize stages reading the scaled-down placeholder while the
    # baked clips themselves were the full real length — so each baked
    # clip got hard-trimmed to ~15% of its real content on rnd_9ff807e3.
    # We now use the per-block `using=` value (already in
    # pending_jobs[5]) as the single source of truth for s/e everywhere
    # downstream. The render target is a script-writer hint, NOT a
    # render-side cap — every block on the timeline is baked, regardless
    # of total duration (PR #75).
    # The rewritten snapshot is persisted to the CastRender row so
    # retries on the same render_id reuse the corrected layout.
    try:
        # Probe each bonded block's REAL prepared voice track up front. The
        # Arrange-timeline slot (_pj[5]) is a script-writer / editor estimate
        # and drifts from the voice that TTS actually produced — a talking-head
        # beat whose slot froze at ~3s while its line is ~8s is the failure
        # this catches. Probing here, once, means the single reflow below lays
        # the WHOLE timeline out against true durations, so V1, A1, captions,
        # product cards and b-roll all stay aligned — instead of every
        # consumer downstream re-discovering the mismatch and patching one
        # element at a time.
        _pj_audio_urls = [
            (pj[1], pj[7]) for pj in pending_jobs
            if pj[1] and pj[7]
        ]
        _audio_probe_results: dict[str, float] = {}
        if _pj_audio_urls:
            _probed = await asyncio.gather(
                *[_probe_audio_duration_s(u) for _, u in _pj_audio_urls],
                return_exceptions=True,
            )
            for (_bid, _), _res in zip(_pj_audio_urls, _probed):
                if isinstance(_res, (int, float)) and _res > 0:
                    _audio_probe_results[_bid] = float(_res)

        block_durations: dict[str, float] = {}
        for _pj in pending_jobs:
            # Tuple shape: (idx, block_id, v1_el, a1_el, baked_key,
            #               duration_s, face_ref_url, audio_url,
            #               motion_prompt). duration_s == `using=`.
            try:
                _bid = _pj[1]
                _slot_dur = float(_pj[5] or 0)
            except (IndexError, TypeError, ValueError) as _bd_exc:
                sentry_sdk.capture_exception(_bd_exc)
                continue
            _v1_meta = (_pj[2] or {}).get("metadata") or {}
            _is_fixed = bool(
                _v1_meta.get("fixed_length") or _v1_meta.get("fixed_duration")
            )
            _real_audio = _audio_probe_results.get(_bid, 0.0)
            # The bonded pair's length is driven by its voice. When the real
            # audio meaningfully overruns the slot (stale/short slot), use the
            # audio — the user rule allows extensions, never a silent cut.
            # Fixed-length beats keep their slot (the video fills it instead).
            if _real_audio > _slot_dur + 0.15 and not _is_fixed:
                logger.info(
                    "Block %s: reflowing slot %.2fs -> real voice %.2fs "
                    "(stale/short Arrange slot)",
                    _bid, _slot_dur, _real_audio,
                )
                _dur = _real_audio
            else:
                _dur = _slot_dur
            if _bid and _dur > 0:
                block_durations[_bid] = _dur
        new_timeline, _rewritten, _dropped = _apply_real_block_durations(
            timeline,
            block_durations=block_durations,
            render_id=render_id,
            fps=30,
        )
        if _rewritten:
            timeline = new_timeline
            # pending_jobs still carries the pre-reflow slot; refresh each
            # job's duration_s from the reflowed timeline so the bake
            # dispatch, audio prep and slot trims all target the real length.
            pending_jobs = [
                (
                    pj[0], pj[1], pj[2], pj[3], pj[4],
                    _slot_duration_for_block(timeline, pj[1], pj[5]) or pj[5],
                    pj[6], pj[7], pj[8],
                )
                for pj in pending_jobs
            ]
            try:
                from models.cast_render import CastRender as _CR
                async with factory() as _persist_session:
                    from sqlalchemy.orm.attributes import flag_modified
                    _r = await _persist_session.get(_CR, render_id)
                    if _r is not None:
                        _r.timeline_snapshot = timeline
                        flag_modified(_r, "timeline_snapshot")
                        await _persist_session.commit()
            except Exception as _persist_exc:
                sentry_sdk.capture_exception(_persist_exc)
    except Exception as _rewrite_exc:
        sentry_sdk.capture_exception(_rewrite_exc)
        logger.warning(
            "Render %s: timeline real-duration rewrite failed (%s); "
            "proceeding with original snapshot — bake will still use "
            "per-block real durations, but slot trims may mis-align.",
            render_id, _rewrite_exc,
        )

    if not pending_jobs and not baked_urls:
        raise RuntimeError("No blocks submitted for baking")

    # Progress: submitting to GPU
    await _update_render(render_id,
        progress_percent=5,
        progress_step=f"Dispatching {len(pending_jobs)} blocks to GPU",
    )

    # ── Pass 1b: dispatch InfiniteTalk blocks sequentially on HOSTKEY ──
    # The previous implementation fanned out N blocks across HOSTKEY (1)
    # plus RunPod (N-1). RunPod's InfiniteTalk template is currently broken
    # ("Video not found" error in 10s) which kills the whole cast render.
    # Until that template is fixed, run every InfiniteTalk block on HOSTKEY,
    # one at a time. T2V (avatar_motion) blocks still cascade through their
    # own HOSTKEY → fal.ai path, which is unaffected and works.
    from services.render_dispatcher import RenderDispatcher
    dispatcher = RenderDispatcher()
    HOSTKEY_ONLY = os.environ.get("CAST_RENDER_HOSTKEY_ONLY", "1") == "1"

    async def dispatch_and_upload(idx, block_id, v1_el, a1_el, baked_key, duration_s,
                                  face_ref_url, audio_url, motion_prompt):
        """Dispatch one block to HOSTKEY or RunPod, decode result, upload to R2."""
        nonlocal completed_count

        # Check render_mode. v1_el is None for audio-only voiceover blocks
        # — use A1's metadata in that case so we still detect the mode and
        # route to the voiceover skip-bake branch below.
        meta = (v1_el.get("metadata") if v1_el else None) or (a1_el.get("metadata") or {})
        block_render_mode = meta.get("render_mode", "full")
        # Stable ID for return value — prefer V1, fall back to A1 for voiceover.
        primary_id = (v1_el or a1_el or {}).get("id")

        async def _reconcile_motion_block_duration(current_duration_s: float) -> float:
            """§2.1-equivalent safety net for motion/body_motion blocks.

            The speaking cascade re-probes the REAL prepared lipsync audio
            and reconciles the slot against it (_reconcile_slot_vs_audio,
            below) before locking in duration_s — motion/body_motion blocks
            never got that same check, so a pre-TTS word-count estimate (or
            a stale saved slot) could flow straight into the T2V/I2V provider
            request and the Phase 3 gate unverified. If it undershot the
            block's own paired voiceover, the render failed as
            motion_clip_too_short with zero user involvement (render
            rnd_c97e73f83325, block blk_237054850a8b). Mirrors
            _reconcile_slot_vs_audio: extends the slot to fit the audio,
            never shrinks it.
            """
            if not audio_url:
                return current_duration_s
            real_audio_s = await _probe_audio_duration_s(audio_url)
            if real_audio_s <= 0:
                return current_duration_s
            v1_meta = (v1_el or {}).get("metadata") or {}
            fixed_len = bool(
                v1_meta.get("fixed_length") or v1_meta.get("fixed_duration")
            )
            reconciled_s, mismatch = _reconcile_slot_vs_audio(
                timeline,
                block_id=block_id,
                audio_duration_s=real_audio_s,
                slot_duration_s=current_duration_s,
                fixed_length=fixed_len,
            )
            if mismatch:
                raise SlotAudioMismatch(
                    f"block {block_id} render {render_id}: "
                    f"slot={current_duration_s:.3f}s vs audio={real_audio_s:.3f}s "
                    f"exceeds {_SLOT_AUDIO_MISMATCH_TOLERANCE:.0%} and slot is "
                    f"fixed-length — planning defect, not re-baking."
                )
            return reconciled_s

        # Per-block routing breadcrumb. Each routing branch below logs a
        # finer-grained "product gate" line with the resolved product id,
        # but this top-of-dispatch line guarantees every block leaves a
        # single grep-able entry of "what render path was chosen" before
        # any provider work begins.
        logger.info(
            "block dispatch: block=%s render_mode=%s type=%s",
            block_id,
            block_render_mode,
            (meta.get("block_type") or meta.get("type") or "?"),
        )

        # PR #83: pip_layout overrides the legacy render_mode for speaking
        # blocks. When the block is set to pip_small / pip_medium / hidden
        # we route through the small-bake MuseTalk path (render_mode=pip,
        # 480p) — pip_layout=hidden also skips bake entirely because the
        # final compose only needs the audio.
        #
        # Step 4: a block-level pip_layout still wins (the user set it). When
        # the block has NO explicit pip_layout and the cast carries a layout
        # template, the template's `face` supplies the default (mapped via
        # face_to_pip_layout). The resolved value drives bake routing exactly
        # as a block-level value would. We also emit a single grep-able
        # placement line per block so production logs show whether the
        # template or the legacy default decided the layout.
        block_pip_override = meta.get("pip_layout")
        try:
            from services.timeline_builder import is_pip_layout, face_to_pip_layout
            from models.block import PipLayout

            if block_pip_override is not None and str(block_pip_override).strip() != "":
                pip_layout_meta = block_pip_override
                placement_source = "block"
            elif template_face is not None:
                pip_layout_meta = face_to_pip_layout(template_face)
                placement_source = "template"
            else:
                pip_layout_meta = block_pip_override
                placement_source = "default"

            logger.info(
                "placement source=%s template=%s face=%s overlay_anchor=%s "
                "block=%s pip_layout=%s",
                placement_source,
                layout_template_id or "-",
                template_face or "-",
                template_overlay_anchor or "-",
                block_id,
                pip_layout_meta or "fullscreen",
            )

            if pip_layout_meta == PipLayout.HIDDEN.value:
                logger.info(
                    "Block %s pip_layout=hidden — skipping bake; audio rides via A1",
                    block_id,
                )
                now = datetime.now(timezone.utc)
                await _update_block_status(render_id, block_id,
                    state="done", provider="voice",
                    started_at=now, completed_at=now, duration_s=0.0,
                )
                completed_count += 1
                pct = int(5 + (completed_count / len(pending_jobs)) * 85)
                await _update_render(render_id,
                    baking_chunks_completed=completed_count,
                    progress_percent=pct,
                    progress_step=f"Block {completed_count}/{len(pending_jobs)}",
                )
                return primary_id, None
            if is_pip_layout(pip_layout_meta) and block_render_mode in ("full", "avatar_full"):
                block_render_mode = "pip"
        except Exception as e:
            sentry_sdk.capture_exception(e)

        # stock_photo / stock_video beats are pure B-roll — no avatar face is
        # meant to appear at all. The category -> render_mode mapping in
        # routers/casts.py (block creation) only special-cases
        # avatar_voiceover / live_pip / avatar_action and falls through to
        # "avatar_full" for everything else, so these blocks were baked as a
        # full-frame TALKING AVATAR clip while their assigned stock_media_url
        # (a Pexels photo/video) only ever showed as a small overlay on top —
        # the avatar filled the background instead of the stock visual.
        # Route them through the same B-roll bake path as voiceover blocks,
        # which resolve_voiceover_visual_source now also checks
        # stock_media_url for.
        if block_render_mode in ("full", "avatar_full"):
            try:
                from models.block import Block as _CatBlock
                async with factory() as _cat_session:
                    _cat_blk = await _cat_session.get(_CatBlock, block_id)
                    if _cat_blk is not None and getattr(_cat_blk, "category", None) in (
                        "stock_photo", "stock_video",
                    ):
                        block_render_mode = "voiceover"
            except Exception as e:
                sentry_sdk.capture_exception(e)

        if block_render_mode == "voiceover":
            # Voiceover blocks have no face animation. They MUST still emit a
            # video clip for their slot — otherwise the FFmpeg compose pass
            # finds no video track and lays down pure black behind the audio
            # (the 7s black on rnd_124ec1f95740, videos=9 vs audios=10). We
            # fill the slot with motion-bearing B-roll: a product video looped
            # to length, or a product/scene/avatar image animated with a slow
            # Ken Burns pan-zoom (a still would trip clip_mostly_frozen). The
            # narration TTS is the duration source of truth and is muxed in.
            from services import voiceover_broll
            bake_start = datetime.now(timezone.utc)
            await _update_block_status(render_id, block_id, state="baking", started_at=bake_start)

            cw, ch, fps = _canvas_dims_for_render(timeline)
            # Audio is the source of truth for a voiceover slot's length.
            audio_dur = await _probe_audio_duration_s(audio_url) if audio_url else 0.0
            slot_s = _slot_duration_for_block(timeline, block_id, float(duration_s or 0.0))
            broll_s = audio_dur if audio_dur > 0 else slot_s
            if broll_s <= 0:
                broll_s = float(duration_s or 0.0)
            if broll_s > 0:
                voiceover_real_durations[block_id] = broll_s

            # Resolve EVERY viable visual source in priority order (not
            # just the first), then append avatar-idle as the guaranteed-
            # safe last resort candidate. We render LONGER than the slot so
            # the post-bake trim lands exactly on length without undershoot.
            #
            # Each candidate is baked, muxed, normalized, and run through
            # the SAME Phase 3 validator right here in the loop — so a
            # candidate that's a legitimate, downloadable video but happens
            # to be near-motionless (clip_mostly_frozen) is skipped in
            # favor of the next candidate instead of hard-failing the whole
            # block. Previously only the single best-priority candidate was
            # ever tried, and only a download/ffmpeg *exception* triggered
            # the avatar-idle fallback — a candidate that baked fine but
            # failed the freeze/black validator had no fallback at all.
            render_s = broll_s + 0.5
            async with factory() as _vo_session:
                candidates = await resolve_voiceover_visual_sources(
                    block_id, _vo_session, r2, cast_id,
                )

            video_bytes: bytes | None = None
            broll_source = "none"
            broll_error: str | None = None
            for cand_idx, (kind, url) in enumerate(candidates):
                try:
                    if kind == "video":
                        cand_bytes = await voiceover_broll.render_video_to_slot(
                            video_url=url, slot_s=render_s,
                            width=cw, height=ch, fps=fps,
                        )
                    else:
                        cand_bytes = await voiceover_broll.render_ken_burns_from_image(
                            image_url=url, slot_s=render_s,
                            width=cw, height=ch, fps=fps,
                        )

                    if audio_url:
                        try:
                            cand_bytes = await _mux_audio_into_clip(
                                cand_bytes, audio_url, duration_s=broll_s,
                            )
                        except Exception as mux_e:
                            sentry_sdk.capture_exception(mux_e)
                            logger.warning(
                                "Voiceover block %s audio mux failed, padding silent: %s",
                                block_id, mux_e,
                            )
                            cand_bytes = await _mux_silent_audio_into_clip(
                                cand_bytes, duration_s=broll_s,
                            )
                    else:
                        cand_bytes = await _mux_silent_audio_into_clip(
                            cand_bytes, duration_s=broll_s,
                        )

                    cand_bytes = await _normalize_for_canvas(
                        video_bytes=cand_bytes,
                        timeline=timeline,
                        block_id=block_id,
                        render_id=render_id,
                        fallback_duration_s=broll_s,
                        r2=r2,
                    )
                    await _validate_baked_clip_bytes(
                        cand_bytes, block_id=block_id, render_id=render_id,
                        require_audio=bool(audio_url),
                    )

                    video_bytes = cand_bytes
                    if kind == "video":
                        broll_source = "product_video"
                    else:
                        broll_source = "ken_burns_image"
                    broll_error = None
                    break
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logger.warning(
                        "Voiceover block %s candidate %d/%d (%s source %s) "
                        "failed bake/validate (%s) — trying next candidate",
                        block_id, cand_idx + 1, len(candidates), kind, url, e,
                    )
                    broll_error = (
                        f"candidate {cand_idx + 1}/{len(candidates)} "
                        f"({kind}): {type(e).__name__}: {e}"
                    )
                    continue

            if not video_bytes:
                # If no genuine B-roll media succeeded, do NOT fall back to a frozen
                # motionless still photo of the avatar. Instead, promote the block to
                # speaking mode so WaveSpeed InfiniteTalk animates the avatar's face
                # and lipsyncs with natural mouth motion to the audio!
                logger.info(
                    "Voiceover block %s: no B-roll source succeeded (%s); promoting to speaking avatar cascade with lipsync",
                    block_id, broll_error,
                )
                block_render_mode = "avatar_full"

            # Record the exact audio used for this block's bake so the
            # post-bake rewrite step (originally lipsync-only — see
            # _rewrite_mux_audio_to_lipsync) also refreshes THIS block's A1
            # timeline src. Voiceover blocks have no lipsync feed so they
            # were never covered by that rewrite; their compose-time
            # props.src stayed frozen at whatever the Arrange-phase timeline
            # last saved — which drifts stale the moment the block's TTS is
            # regenerated (confirmed: a real cast had a saved src pointing at
            # an 8.4s-old TTS file while the live variant's current audio was
            # a different, 6.5s file — a different SENTENCE playing during
            # compose than the one actually muxed into the bake, landing
            # right on top of the next block's narration and sounding like
            # two voices at once).
            if audio_url:
                lipsync_audio_by_block[block_id] = audio_url

            # Mux, canvas-normalize, and Phase 3 validation already happened
            # per-candidate inside the resolution loop above — video_bytes
            # here is the first candidate that cleared all three.
            await r2.upload_bytes(video_bytes, baked_key, "video/mp4")
            logger.info(
                "Voiceover block %s baked B-roll (%s) → %s (%d bytes)",
                block_id, broll_source, baked_key, len(video_bytes),
            )

            completed_count += 1
            pct = int(5 + (completed_count / len(pending_jobs)) * 85)
            bake_end = datetime.now(timezone.utc)
            await _update_block_status(render_id, block_id,
                state="done", provider="voice",
                completed_at=bake_end,
                duration_s=max((bake_end - bake_start).total_seconds(), 0.0),
            )
            await _update_render(render_id,
                baking_chunks_completed=completed_count,
                progress_percent=pct,
                progress_step=f"Baked block {completed_count}/{len(pending_jobs)}",
            )
            return primary_id, baked_key

        # ── avatar_motion (T2V) — text-prompt → motion video ──
        # No source face image; no audio-driven lip-sync. The voiceover (if
        # any) is muxed by the FFmpeg compose pass via the A1 element.
        if block_render_mode == "motion":
            duration_s = await _reconcile_motion_block_duration(float(duration_s or 0))

            bake_start = datetime.now(timezone.utc)
            await _update_block_status(render_id, block_id, state="baking", started_at=bake_start)

            # Resolve canvas dims for T2V — default based on canvas aspect ratio,
            # matching the avatar bake output so FFmpeg compose treats the
            # clip identically.
            _cw_t2v, _ch_t2v, _ = _canvas_dims_for_render(timeline)
            _def_w = 848 if _cw_t2v > _ch_t2v else 480
            _def_h = 480 if _cw_t2v > _ch_t2v else 848
            t2v_width = ((v1_el or {}).get("props") or {}).get("width") or _def_w
            t2v_height = ((v1_el or {}).get("props") or {}).get("height") or _def_h

            t2v_prompt = motion_prompt
            if not t2v_prompt or t2v_prompt.startswith("A person talking naturally"):
                # Defensive: if motion_prompt was the default talking-head
                # placeholder, swap in a generic action prompt so T2V doesn't
                # render a static talking shot.
                t2v_prompt = "A person performing an action naturally, cinematic motion"

            try:
                t2v_result = await dispatcher.submit_t2v(
                    prompt=t2v_prompt,
                    width=int(t2v_width),
                    height=int(t2v_height),
                    duration_s=float(duration_s) or 5.0,
                    reference_image_url=face_ref_url or None,
                )
            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                raise

            backend = t2v_result.get("backend", "t2v")
            output = t2v_result.get("output") or {}
            video_b64 = output.get("video") or output.get("video_base64") or ""
            if not video_b64:
                raise RuntimeError(f"T2V returned no video for block {block_id}")
            video_bytes = base64.b64decode(video_b64)

            # Mux A1 voice into the silent T2V clip so the concat-copy
            # stage sees a uniform a/v stream layout. Failure is logged
            # but non-fatal: the block ships silent rather than failing
            # the whole render.
            a1_audio_src = (a1_el.get("props") or {}).get("src") if a1_el else ""
            if a1_audio_src:
                try:
                    video_bytes = await _mux_audio_into_clip(
                        video_bytes,
                        a1_audio_src,
                        duration_s=float(duration_s) or 5.0,
                    )
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logger.warning(
                        "motion block %s: audio mux failed, leaving silent: %s",
                        block_id, e,
                    )

            # ── Change A: post-bake lipsync refine for motion blocks ──
            # T2V/I2V bakes invent mouth motion that does not match the
            # TTS muxed in above (user complaints @0:07/0:10/0:13/0:24/
            # 0:52). When the block type is eligible we re-sync the lower
            # face to the voice with the same fal sync-lipsync providers
            # the speaking path uses. Runs on the muxed clip (audio +
            # video) BEFORE the canvas-normalize / trim-to-slot step, and
            # is purely additive: any failure keeps the original bake.
            _motion_block_type = str(
                meta.get("block_type") or meta.get("type") or ""
            )
            try:
                from services.provider_chain import (
                    should_run_motion_lipsync_refine,
                )
                _m_decision = should_run_motion_lipsync_refine(
                    _motion_block_type, float(duration_s or 0)
                )
            except Exception as _mgate_exc:
                sentry_sdk.capture_exception(_mgate_exc)
                from services.provider_chain import RefineDecision
                _m_decision = RefineDecision(False, "gate_error", "motion")

            _m_ran = False
            _m_fal_req = ""
            _m_out_dur = 0.0
            _m_in_dur = await _probe_audio_duration_s(
                a1_audio_src
            ) if a1_audio_src else 0.0
            if _m_decision.run and a1_audio_src:
                try:
                    _m_refine_key = (
                        f"renders/{render_id}/refine_src/{block_id}.mp4"
                    )
                    await r2.upload_bytes(
                        video_bytes, _m_refine_key, "video/mp4"
                    )
                    _m_src_url = r2.get_public_url(
                        _m_refine_key, cache_bust=True
                    )
                    from services.render_providers import (
                        FalSyncLipsyncV3Provider,
                        FalSyncLipsyncV2ProProvider,
                    )
                    for _mrp in (
                        FalSyncLipsyncV3Provider(),
                        FalSyncLipsyncV2ProProvider(),
                    ):
                        try:
                            _mres = await _mrp.refine(
                                video_url=_m_src_url,
                                audio_url=a1_audio_src,
                                duration_s=float(duration_s or 0),
                                block_id=block_id,
                                render_id=render_id,
                            )
                            _mout_url = (
                                _mres.get("video_url")
                                if isinstance(_mres, dict) else None
                            )
                            if _mout_url:
                                _refined_bytes = await _download_url_bytes(
                                    _mout_url,
                                    clip_duration_s=float(duration_s or 0),
                                )
                                if _refined_bytes:
                                    video_bytes = _refined_bytes
                                    _m_ran = True
                                    _m_fal_req = (
                                        _mres.get("fal_request_id") or ""
                                    ) if isinstance(_mres, dict) else ""
                                    _m_out_dur = await _probe_audio_duration_s(
                                        _mout_url
                                    )
                                    logger.info(
                                        "Motion block %s: post-bake lipsync "
                                        "refine succeeded via %s",
                                        block_id, _mrp.name,
                                    )
                                    break
                        except Exception as _mref_exc:
                            sentry_sdk.capture_exception(_mref_exc)
                            logger.warning(
                                "Motion block %s: refine via %s failed (%s); "
                                "trying next tier",
                                block_id, _mrp.name, _mref_exc,
                            )
                except Exception as _mref_outer:
                    sentry_sdk.capture_exception(_mref_outer)
                    logger.warning(
                        "Motion block %s: refine setup failed (%s); keeping "
                        "original bake",
                        block_id, _mref_outer,
                    )
            _log_lipsync_refine_decision(
                block_id=block_id,
                render_id=render_id,
                decision=_m_decision,
                ran=_m_ran,
                in_dur=_m_in_dur,
                out_dur=_m_out_dur,
                fal_req=_m_fal_req,
            )

            video_bytes = await _normalize_for_canvas(
                video_bytes=video_bytes,
                timeline=timeline,
                block_id=block_id,
                render_id=render_id,
                fallback_duration_s=float(duration_s) or 5.0,
                r2=r2,
                is_motion=True,
                motion_prompt=(t2v_prompt or "continues the previous motion naturally"),
            )
            # Phase 3 + §3.2: reject black / frozen / undershot motion bakes
            # before upload. Motion gate is slot-relative (post Phase 1.3
            # micro-slowdown the clip must be ≥ slot).
            await _validate_baked_clip_bytes(
                video_bytes, block_id=block_id, render_id=render_id,
                is_motion=True,
                slot_duration_s=_slot_duration_for_block(
                    timeline, block_id, float(duration_s or 0.0)
                ),
            )
            await r2.upload_bytes(video_bytes, baked_key, "video/mp4")
            logger.info("Motion block %s baked on %s → %s (%d bytes)",
                        block_id, backend, baked_key, len(video_bytes))

            # Cost: HOSTKEY is on-prem (free). fal.ai Wan 2.2 (T2V or I2V)
            # costs roughly $0.12 per 5s clip; bill duration-proportionally.
            if backend == "hostkey_t2v":
                cost = 0.0
            else:
                cost = round((float(duration_s) or 5.0) * 0.024, 4)
            await _log_generation_cost(
                user_id=user_id,
                cast_id=cast_id,
                block_id=block_id,
                engine="wan_t2v",
                operation="cast_block_t2v",
                quantity=int(duration_s or 5),
                cost_usd=cost,
                metadata_json={"render_id": render_id, "backend": backend, "prompt": t2v_prompt[:200]},
            )

            completed_count += 1
            pct = int(5 + (completed_count / len(pending_jobs)) * 85)
            bake_end = datetime.now(timezone.utc)
            t2v_elapsed = max((bake_end - bake_start).total_seconds(), 0.0)
            await _log_render_usage(
                user_id=user_id,
                cast_id=cast_id,
                block_id=block_id,
                render_id=render_id,
                backend=backend,
                event_type="motion_render",
                elapsed_seconds=t2v_elapsed,
                output_video_seconds=float(duration_s) if backend in ("fal_t2v", "fal_i2v") else None,
                fal_model="wan-2.2" if backend in ("fal_t2v", "fal_i2v") else None,
                requested_tier_s=float(t2v_result.get("requested_tier_s") or 0.0) or None,
            )
            await _update_block_status(render_id, block_id,
                state="done",
                provider=_PROVIDER_LABELS.get(backend, backend),
                completed_at=bake_end,
                duration_s=max((bake_end - bake_start).total_seconds(), 0.0),
            )
            await _update_render(render_id,
                baking_chunks_completed=completed_count,
                progress_percent=pct,
                progress_step=f"Baked block {completed_count}/{len(pending_jobs)}",
            )
            return primary_id, baked_key

        # ── avatar_action / body_motion — I2V interpolation between two
        # AI-generated scene frames. Both legacy avatar_acting blocks
        # (render_mode=body_motion, body shots as frames) and the merged
        # avatar_action blocks (render_mode=body_motion, FLUX scene frames)
        # land here. Falls back to face_ref as the start frame when the
        # block has no AI frames yet.
        if block_render_mode == "body_motion":
            # PR #76: avatar_action blocks NEVER run lipsync. The lipsync
            # engines only work on static portraits — driving them with a
            # moving subject distorts the mouth. Dialogue on action blocks
            # becomes a paired voiceover audio track muxed onto the motion
            # clip, timed to the clip's start/end. apply_lipsync is NO
            # LONGER imported here.
            from services.wan_body_motion import generate_body_motion_clip
            from models.avatar_look import AvatarLook
            from models.block import Block as _Block
            from models.cast import Cast as _Cast
            from models.avatar import Avatar as _Avatar
            from sqlalchemy import select as _sa_select

            duration_s = await _reconcile_motion_block_duration(float(duration_s or 0))

            bake_start = datetime.now(timezone.utc)
            await _update_block_status(render_id, block_id, state="baking", started_at=bake_start)

            try:
                from models.variant import Variant as _Variant
                async with factory() as bm_session:
                    blk = await bm_session.get(_Block, block_id)
                    cst = await bm_session.get(_Cast, cast_id) if block_id else None
                    avatar_obj = (
                        await bm_session.get(_Avatar, cst.avatar_id)
                        if cst and getattr(cst, "avatar_id", None)
                        else None
                    )

                    # Resolve the active variant's script_text so we can
                    # detect a third-person narrator script (which must
                    # NOT be lip-synced onto the avatar — it would make
                    # the avatar's mouth speak narrator prose about
                    # itself).
                    variant_script_text = ""
                    try:
                        _vres = await bm_session.execute(
                            _sa_select(_Variant.script_text)
                            .where(_Variant.block_id == block_id)
                            .where(_Variant.is_active.is_(True))
                            .limit(1)
                        )
                        variant_script_text = (_vres.scalar() or "").strip()
                    except Exception as _vex:
                        sentry_sdk.capture_exception(_vex)
                        variant_script_text = ""

                    # voicing_mode controls whether TTS audio is rendered.
                    # NOTE: per PR #76, lipsync is NEVER applied to action
                    # blocks regardless of voicing_mode — TTS becomes a
                    # paired voiceover audio track muxed onto the motion
                    # clip. The voicing_mode still distinguishes silent
                    # beats (motion_sfx_only, or an empty-script action
                    # block) from dialogue blocks.
                    block_voicing_mode = _normalize_voicing_mode(
                        getattr(blk, "voicing_mode", None) if blk else None
                    )

                    # PR #76: per-block voiceover toggle. NULL = LLM
                    # default (treat as True when the block has dialogue);
                    # True = force voiceover; False = silent.
                    block_meta = (
                        dict(getattr(blk, "block_metadata", None) or {})
                        if blk else {}
                    )
                    raw_voiceover_enabled = block_meta.get("voiceover_enabled")
                    if isinstance(raw_voiceover_enabled, bool):
                        voiceover_enabled_flag = raw_voiceover_enabled
                    else:
                        voiceover_enabled_flag = None  # LLM default

                    # Resolve start/end frame URLs with the same priority
                    # order as generate_cast.py: pinned look → latest ready
                    # action_block frame → latest ready body_motion_block
                    # frame → avatar face_ref (start only).
                    #
                    # Step 8: when the block's mic is ON, each resolved base
                    # look frame is swapped for its baked clip-on (mic-on)
                    # variant via resolve_mic_on_face_key — lazy-generated and
                    # cached — so the avatar wears the lavalier consistently
                    # across start and end frames (and, via generate_cast.py,
                    # the lipsync blocks). mic_on False/None keep the clean
                    # frame. The avatar.face_ref fallback has no base look id,
                    # so it stays clean.
                    from services.mic_on_look import resolve_mic_on_face_key
                    block_mic_on = getattr(blk, "mic_on", None) if blk else None
                    avatar_id_for_mic = getattr(cst, "avatar_id", None) if cst else None

                    # The scene's own mic_visible (chosen deliberately when
                    # the user creates/picks that scene) wins over the
                    # block's mic_on, which is only ever a layout template's
                    # default stamped before any specific scene was
                    # necessarily attached — mirrors the same precedence now
                    # used on the audio side
                    # (services.mic_presets.resolve_scene_voice_settings),
                    # so the visual mic and the voice filter never disagree.
                    async def _maybe_mic_on(look, key: str) -> str:
                        look_id = getattr(look, "id", None) if look is not None else None
                        look_mic_visible = getattr(look, "mic_visible", None) if look is not None else None
                        effective_mic_on = (
                            look_mic_visible if look_mic_visible is not None
                            else block_mic_on
                        )
                        resolved_key = await resolve_mic_on_face_key(
                            effective_mic_on, avatar_id_for_mic, look_id, key, bm_session
                        )
                        return r2.get_public_url(resolved_key)

                    # Round-6 Bug B: a look is keyed by its camera framing too.
                    # Never reuse a look generated for a different framing — the
                    # user wanted distinct shots per block, so we constrain the
                    # fallback query by the block's framing (default MEDIUM).
                    block_framing = (getattr(blk, "framing", None) or "MEDIUM") if blk else "MEDIUM"

                    async def _resolve(kind: str, pinned_id: str | None):
                        if pinned_id:
                            pinned = await bm_session.get(AvatarLook, pinned_id)
                            if pinned and pinned.face_ref_key:
                                return await _maybe_mic_on(pinned, pinned.face_ref_key)
                        for prefix in (
                            f"action_block_{block_id}_{kind}",
                            f"body_motion_block_{block_id}_{kind}",
                        ):
                            res = await bm_session.execute(
                                _sa_select(AvatarLook)
                                .where(AvatarLook.avatar_id == cst.avatar_id)
                                .where(AvatarLook.look_type == prefix)
                                .where(AvatarLook.framing == block_framing)
                                .where(AvatarLook.status == "ready")
                                .order_by(AvatarLook.created_at.desc())
                                .limit(1)
                            )
                            row = res.scalars().first()
                            if row and row.face_ref_key:
                                return await _maybe_mic_on(row, row.face_ref_key)
                        if kind == "start" and avatar_obj and avatar_obj.face_ref_key:
                            return r2.get_public_url(avatar_obj.face_ref_key)
                        return None

                    pinned_start = blk.body_motion_start_look_id if blk else None
                    pinned_end = blk.body_motion_end_look_id if blk else None
                    start_url = await _resolve("start", pinned_start)
                    end_url = await _resolve("end", pinned_end)

                    # I2V is anchored by the start frame which already shows
                    # the avatar; prepending appearance text here drowns the
                    # motion description in Wan's prompt budget and produces
                    # a different-looking person performing arbitrary motion.
                    # Prefer the block's Motion-description field. If the user
                    # left it empty, fall back to the frame-box text (the
                    # action they typed for the start/end still) BEFORE the
                    # generic "talking to camera" default — otherwise a typed
                    # action ("hurls the product at the wall") never reaches
                    # the motion engine and the clip is just a talking head.
                    _bm = ((blk.body_motion_prompt if blk else "") or "").strip()
                    _frame_action = ""
                    if not _bm and blk is not None:
                        _frame_action = (
                            (getattr(blk, "action_start_prompt", None) or "").strip()
                            or (getattr(blk, "body_motion_start_prompt", None) or "").strip()
                            or (getattr(blk, "action_end_prompt", None) or "").strip()
                        )
                    _passthru = (motion_prompt or "").strip()
                    if _passthru.lower() == "a person talking naturally to the camera":
                        _passthru = ""
                    raw_motion_prompt = (
                        _bm or _frame_action or _passthru or "performs a natural action"
                    )
                    i2v_prompt = raw_motion_prompt
                    # True when the user actually described a motion (vs a
                    # generic fallback) — decides whether the Kling free-motion
                    # path should win over the product-"hold" path below.
                    has_real_motion = bool(_bm or _frame_action)

                    # ── Product gate (PR #92) ─────────────────────────────
                    # PRODUCT and PRODUCT_DEMO action blocks must condition
                    # on the user's actual product image; without this the
                    # downstream I2V engine hallucinates a generic bottle.
                    # block.product_id is often NULL for PRODUCT_DEMO blocks
                    # authored as "demonstrate any product on this cast",
                    # so we resolve via cast_products as a fallback.
                    block_type_str = ""
                    try:
                        _bt_raw = getattr(blk, "type", None) if blk else None
                        if hasattr(_bt_raw, "value"):
                            block_type_str = str(_bt_raw.value or "")
                        elif _bt_raw is not None:
                            block_type_str = str(_bt_raw)
                    except Exception as _bt_exc:
                        sentry_sdk.capture_exception(_bt_exc)
                        block_type_str = ""
                    is_product_typed = block_type_str in ("PRODUCT", "PRODUCT_DEMO")
                    effective_product_id_bm: str | None = None
                    effective_product_image_url_bm: str | None = None
                    effective_product_image_urls_bm: list[str] = []
                    effective_product_name_bm: str | None = None
                    effective_product_visual_bm: str | None = None
                    if is_product_typed:
                        try:
                            effective_product_id_bm = (
                                await resolve_effective_product_id(blk, bm_session, cast_id)
                            )
                        except Exception as _epid_exc:
                            sentry_sdk.capture_exception(_epid_exc)
                            effective_product_id_bm = None
                        if effective_product_id_bm:
                            try:
                                from models.product import Product as _ProductForGate
                                _prod = await bm_session.get(
                                    _ProductForGate, effective_product_id_bm
                                )
                                if _prod is not None:
                                    _ck = getattr(_prod, "cover_image_key", None) or ""
                                    if _ck:
                                        _url = r2.get_public_url(_ck)
                                        if _url:
                                            effective_product_image_url_bm = _url
                                            effective_product_image_urls_bm = [_url]
                                    _nm = (
                                        getattr(_prod, "name", None)
                                        or getattr(_prod, "title", None)
                                        or ""
                                    )
                                    _nm = str(_nm).strip()
                                    if _nm:
                                        effective_product_name_bm = _nm
                                    # Distil the product's visual metadata
                                    # (color/shape/label) so the motion prompt
                                    # can lead with the real product appearance
                                    # instead of just the brand name. Without
                                    # this the engine paints a generic bottle
                                    # (user complaint @0:24).
                                    try:
                                        from services.ai_prompts import (
                                            build_product_visual_description,
                                        )
                                        _visual = build_product_visual_description(
                                            product_name=effective_product_name_bm,
                                            description=getattr(_prod, "description", None),
                                            category=getattr(_prod, "category", None),
                                            tags=getattr(_prod, "tags", None),
                                            selling_points=getattr(_prod, "selling_points", None),
                                            specifications=getattr(_prod, "specifications", None),
                                        )
                                        if _visual:
                                            effective_product_visual_bm = _visual
                                    except Exception as _vis_exc:
                                        sentry_sdk.capture_exception(_vis_exc)
                            except Exception as _prod_exc:
                                sentry_sdk.capture_exception(_prod_exc)

                if not start_url:
                    raise RuntimeError(f"avatar_action block {block_id} has no usable start frame")

                # Clean the user's Motion description before any engine sees it.
                # Video models keep ~1 action and silently drop sequences,
                # conditionals, physics and outcomes — this rewrites a rough
                # note ("walk and throw it so it bounces off her head to prove
                # it's tough") into one plausible action. The block keeps the
                # user's original text; only i2v_prompt is cleaned. Best-effort.
                if has_real_motion:
                    try:
                        from services.motion_prompt import sanitize_motion_prompt
                        i2v_prompt = await sanitize_motion_prompt(i2v_prompt, cast_id=cast_id)
                    except Exception as _mp_exc:
                        sentry_sdk.capture_exception(_mp_exc)

                # Route PRODUCT/PRODUCT_DEMO action blocks through the
                # product-conditioned bake whenever an effective product
                # image resolves. This replaces the previous unconditional
                # generate_body_motion_clip call so the avatar holds the
                # user's actual product (e.g. the OGX Argan Oil bottle)
                # instead of whatever the I2V model paints in. HOOK blocks
                # and other non-PRODUCT types intentionally keep their
                # artistic latitude via the wan_body_motion path below.
                _action_engine = os.environ.get("ACTION_MOTION_ENGINE", "wan").strip().lower()
                # When the Kling free-motion experiment is on AND the user
                # actually described an action, let that path handle the beat
                # even for product blocks. The product-"hold" path below can
                # only ever show the avatar holding the item, never performing
                # the action — so a "throw it at the wall" beat routed there
                # always came back as a talking-head with the product. Product-
                # elements stays the fallback if Kling free-motion errors.
                _prefer_kling_free_motion = (_action_engine == "kling" and has_real_motion)
                route_through_product_elements = bool(
                    is_product_typed
                    and effective_product_image_url_bm
                    and not _prefer_kling_free_motion
                )
                logger.info(
                    "product gate: block %s type=%s effective_product=%s "
                    "has_real_motion=%s prefer_kling=%s → route=%s",
                    block_id,
                    block_type_str or "?",
                    effective_product_id_bm or "None",
                    has_real_motion,
                    _prefer_kling_free_motion,
                    "product_elements" if route_through_product_elements
                    else ("kling_free_motion" if _prefer_kling_free_motion else "avatar_action"),
                )

                wan_video_url: str | None = None
                product_bake_backend: str | None = None
                # §5.4: the requested provider tier for this motion bake (the
                # overshot, snapped seconds the provider is billed for). Set by
                # whichever bake path runs; used for requested-tier UsageEvent
                # billing below. Falls back to None → slot billing.
                motion_requested_tier_s: float | None = None
                # Provider-reported delivered duration (fal/Kling tell us how
                # long the clip they returned actually is). Carried separately
                # from the billing tier so the post-download truncation gate
                # can compare the locally-materialised file against it.
                provider_reported_duration_s: float = 0.0
                if route_through_product_elements:
                    try:
                        from services.render_providers import KlingV3ProElementsProvider
                        try:
                            from services.ai_prompts import build_product_aware_scene_prompt
                            pe_prompt = build_product_aware_scene_prompt(
                                base_prompt=i2v_prompt,
                                product_name=effective_product_name_bm,
                                product_visual_description=effective_product_visual_bm,
                            )
                        except Exception as _pp_exc:
                            sentry_sdk.capture_exception(_pp_exc)
                            pe_prompt = i2v_prompt

                        # ── Option A (flag-gated) ────────────────────────
                        # When PRODUCT_DEMO_USE_FLUX_KONTEXT_SCENE=true, first
                        # generate a still that already shows the real product
                        # correctly held (FLUX Kontext I2I from the product
                        # image), then feed that still as the motion engine's
                        # start frame so product identity is preserved from
                        # frame one. Defaults OFF — falls through to the
                        # current bake when disabled or on any error.
                        scene_start_image_url_bm: str | None = None
                        if block_type_str == "PRODUCT_DEMO":
                            try:
                                from services.scene_image import (
                                    flux_kontext_scene_enabled,
                                    generate_scene_image,
                                )
                                if (
                                    flux_kontext_scene_enabled()
                                    and effective_product_image_url_bm
                                ):
                                    _scene_bytes = await generate_scene_image(
                                        product_asset_url=effective_product_image_url_bm,
                                        motion_prompt=i2v_prompt,
                                        product_id=effective_product_id_bm,
                                    )
                                    _scene_key = (
                                        f"creators/{user_id}/casts/{cast_id}/"
                                        f"scene_frames/{block_id}.jpg"
                                    )
                                    await r2.upload_bytes(
                                        _scene_bytes, _scene_key,
                                        content_type="image/jpeg",
                                    )
                                    scene_start_image_url_bm = r2.get_public_url(_scene_key)
                                    logger.info(
                                        "Block %s: scene-image start frame generated (%d bytes)",
                                        block_id, len(_scene_bytes),
                                    )
                            except Exception as _scene_exc:
                                sentry_sdk.capture_exception(_scene_exc)
                                logger.warning(
                                    "Block %s: scene-image generation failed (%s); "
                                    "using avatar start frame",
                                    block_id, _scene_exc,
                                )
                                scene_start_image_url_bm = None

                        elements_provider = KlingV3ProElementsProvider()
                        pe_result = await elements_provider.generate(
                            image_url=start_url,
                            prompt=pe_prompt,
                            audio_url=None,
                            product_image_url=effective_product_image_url_bm,
                            product_image_urls=list(effective_product_image_urls_bm),
                            script_references_product=True,
                            duration_s=float(duration_s or 5.0),
                            audio_duration_s=float(duration_s or 5.0),
                            width=720,
                            height=1280,
                            scene_start_image_url=scene_start_image_url_bm,
                        )
                        wan_video_url = (pe_result or {}).get("video_url")
                        provider_reported_duration_s = float(
                            (pe_result or {}).get("duration_seconds") or 0.0
                        )
                        motion_requested_tier_s = provider_reported_duration_s or None
                        product_bake_backend = "product_elements_bake"
                    except Exception as _pe_exc:
                        sentry_sdk.capture_exception(_pe_exc)
                        logger.warning(
                            "Block %s: product-elements bake failed (%s); "
                            "falling back to wan_body_motion",
                            block_id, _pe_exc,
                        )
                        wan_video_url = None
                        product_bake_backend = None

                if not wan_video_url:
                    # EXPERIMENT (env ACTION_MOTION_ENGINE=kling): drive the
                    # motion with Kling 2.5 Turbo Pro from the START frame +
                    # the motion prompt ONLY — no end/tail frame. Wan's default
                    # path passes both frames and interpolates between them, so
                    # a dynamic action ("throw the product at the wall") can
                    # never happen — the clip is boxed in by two near-identical
                    # stills. Single-frame + prompt lets the model actually
                    # animate the described motion. Falls back to Wan on any
                    # error or when the flag is unset. `_action_engine` was
                    # resolved at the product gate above.
                    if _action_engine == "kling":
                        try:
                            from services.acting_video_client import ActingVideoClient
                            _cw_k, _ch_k, _ = _canvas_dims_for_render(timeline)
                            _ar_k = (
                                "16:9" if _cw_k > _ch_k
                                else "1:1" if _cw_k == _ch_k
                                else "9:16"
                            )
                            _kdur = max(1, min(10, int(round(float(duration_s or 5.0)))))
                            _kc = ActingVideoClient(getattr(settings, "FAL_API_KEY", "") or "")
                            _kres = await _kc.generate(
                                first_frame_url=start_url,
                                last_frame_url=None,  # free-run the motion, don't interpolate
                                prompt=i2v_prompt,
                                duration_seconds=_kdur,
                                aspect_ratio=_ar_k,
                            )
                            wan_video_url = _kres["video_url"]
                            provider_reported_duration_s = float(
                                _kres.get("duration_seconds") or 0.0
                            )
                            motion_requested_tier_s = provider_reported_duration_s or None
                            product_bake_backend = "kling_acting_experiment"
                            logger.info(
                                "avatar_action block %s: Kling free-motion bake "
                                "(engine=%s, dur=%ds, ar=%s)",
                                block_id, _kres.get("engine"), _kdur, _ar_k,
                            )
                        except Exception as _kexc:
                            sentry_sdk.capture_exception(_kexc)
                            logger.warning(
                                "avatar_action block %s: Kling free-motion bake "
                                "failed (%s) — falling back to Wan",
                                block_id, _kexc,
                            )
                            wan_video_url = None

                if not wan_video_url:
                    wan_result = await generate_body_motion_clip(
                        start_image_url=start_url,
                        end_image_url=end_url,
                        prompt=i2v_prompt,
                        duration_seconds=float(duration_s or 5.0),
                        resolution="720p",
                    )
                    wan_video_url = wan_result["video_url"]
                    # §5.4: generate_body_motion_clip returns the requested-tier
                    # total (sum of segment tiers for chained slots) as
                    # duration_seconds — bill that, not the slot.
                    provider_reported_duration_s = float(
                        wan_result.get("duration_seconds") or 0.0
                    )
                    motion_requested_tier_s = provider_reported_duration_s or None

                # PR #76: avatar_action blocks never run lipsync. Decide
                # whether the motion clip should carry a paired voiceover
                # audio track muxed at the same s/e offset.
                #
                # voicing_mode behavior matrix (no lipsync anywhere):
                #   tts_dialogue   -> mux TTS audio over the motion clip
                #                     (subject to voiceover_enabled toggle).
                #   motion_sfx_only -> silent; the audio plan covers SFX.
                #
                # Runtime guard: Opus is instructed to use first-person
                # dialogue for action blocks. If it slipped and emitted
                # third-person narrator prose, dropping the TTS for that
                # block is safer than playing narrator prose as the
                # avatar's voice over the action.
                skip_due_to_narrator = (
                    block_voicing_mode == "tts_dialogue"
                    and _is_third_person_narrator(variant_script_text)
                )
                if skip_due_to_narrator:
                    logger.warning(
                        "Block %s action script is third-person narrator — "
                        "dropping voiceover for this clip",
                        block_id,
                    )

                # Empty script → silent beat.
                empty_script_silent_beat = not variant_script_text
                if empty_script_silent_beat:
                    logger.info(
                        "Block %s action has empty script_text — silent clip",
                        block_id,
                    )

                # voiceover_enabled toggle: False forces silent regardless
                # of dialogue/voicing_mode. None/True falls through to the
                # voicing_mode + dialogue gating.
                user_disabled_voiceover = voiceover_enabled_flag is False
                if user_disabled_voiceover:
                    logger.info(
                        "Block %s action voiceover disabled by toggle — "
                        "silent clip",
                        block_id,
                    )

                if (
                    block_voicing_mode == "motion_sfx_only"
                    or skip_due_to_narrator
                    or empty_script_silent_beat
                    or user_disabled_voiceover
                ):
                    audio_url_for_mux = None
                else:
                    audio_url_for_mux = audio_url or None

                voiceover_added_for_status = False

                # Audio time-align policy: TTS may be longer or shorter
                # than the motion clip. We hold the visual duration fixed
                # (downstream timeline math depends on it). Over-long TTS is
                # trimmed to the clip here; under-long TTS is silence-padded
                # inside _mux_audio_into_clip (`-af apad` + `-t <video_dur>`,
                # NOT `-shortest`) so a short voiceover can never clip the
                # video down to the audio length.
                clip_duration_s = float(duration_s or 5.0)
                effective_voiceover_url = audio_url_for_mux
                if audio_url_for_mux and tts_duration_s and (
                    float(tts_duration_s) - clip_duration_s > 0.05
                ):
                    try:
                        effective_voiceover_url = await _trim_and_upload_audio(
                            r2, audio_url_for_mux, clip_duration_s,
                            render_id, block_id,
                        )
                        logger.warning(
                            "Block %s action voiceover TTS truncated "
                            "%.2fs -> %.2fs to match motion clip",
                            block_id, float(tts_duration_s), clip_duration_s,
                        )
                    except Exception as trim_exc:
                        sentry_sdk.capture_exception(trim_exc)
                        logger.warning(
                            "Block %s voiceover trim failed (%s); using "
                            "original audio",
                            block_id, trim_exc,
                        )
                        effective_voiceover_url = audio_url_for_mux

                # Download the motion clip.
                action_download_timeout = max(60.0, clip_duration_s * 12)
                async with httpx.AsyncClient(timeout=action_download_timeout) as http:
                    resp = await http.get(wan_video_url)
                    resp.raise_for_status()
                    video_bytes = resp.content

                # Fail-loud hop instrumentation (motion truncation diagnostic):
                # probe the file the moment it lands locally, before any mux /
                # conform pass can shorten it. Compared against the provider's
                # reported duration by the truncation gate below.
                post_download_dur = await _probe_bytes_duration_s(video_bytes)
                logger.info(
                    "motion download: block %s hop=after_download bytes=%d "
                    "probed=%.3fs provider_reported=%.3fs slot=%.3fs",
                    block_id, len(video_bytes), post_download_dur,
                    provider_reported_duration_s, clip_duration_s,
                )

                if effective_voiceover_url:
                    try:
                        video_bytes = await _mux_audio_into_clip(
                            video_bytes,
                            effective_voiceover_url,
                            duration_s=clip_duration_s,
                        )
                        voiceover_added_for_status = True
                    except Exception as mux_exc:
                        sentry_sdk.capture_exception(mux_exc)
                        logger.warning(
                            "Block %s voiceover mux failed (%s); shipping "
                            "silent action clip",
                            block_id, mux_exc,
                        )
                        try:
                            video_bytes = await _mux_silent_audio_into_clip(
                                video_bytes,
                                duration_s=clip_duration_s,
                            )
                        except Exception as silent_exc:
                            sentry_sdk.capture_exception(silent_exc)
                else:
                    try:
                        video_bytes = await _mux_silent_audio_into_clip(
                            video_bytes,
                            duration_s=clip_duration_s,
                        )
                    except Exception as silent_exc:
                        sentry_sdk.capture_exception(silent_exc)
                        logger.warning(
                            "Block %s silent audio pad failed (%s); "
                            "shipping with no audio stream",
                            block_id, silent_exc,
                        )

                # Fail-loud hop instrumentation: probe immediately before
                # normalize so the diagnostic shows whether the mux pass (not
                # the download, not normalize) is where the clip lost length.
                pre_normalize_dur = await _probe_bytes_duration_s(video_bytes)
                logger.info(
                    "motion download: block %s hop=before_normalize bytes=%d "
                    "probed=%.3fs provider_reported=%.3fs slot=%.3fs",
                    block_id, len(video_bytes), pre_normalize_dur,
                    provider_reported_duration_s, clip_duration_s,
                )

                # Truncation gate (Render_Quality_Duration_Validation.md): the
                # provider told us how long the clip it delivered is. If the
                # locally-materialised file (post-download, post-mux) is shorter
                # than that by more than the tolerance, a hop silently truncated
                # it — fail loud rather than ship a half-length motion clip that
                # only surfaces as a downstream duration_undershoot. We compare
                # against the SHORTFALL only (a longer file is fine — overshoot
                # is intentional and head-trimmed by normalize).
                _MOTION_TRUNCATION_TOLERANCE_S = 0.5
                if (
                    provider_reported_duration_s > 0
                    and pre_normalize_dur > 0
                    and (provider_reported_duration_s - pre_normalize_dur)
                    > _MOTION_TRUNCATION_TOLERANCE_S
                ):
                    trunc_exc = MotionDownloadTruncationError(
                        f"motion clip truncated before normalize: block {block_id} "
                        f"provider_reported={provider_reported_duration_s:.3f}s "
                        f"local={pre_normalize_dur:.3f}s "
                        f"after_download={post_download_dur:.3f}s "
                        f"(lost {provider_reported_duration_s - pre_normalize_dur:.3f}s)"
                    )
                    sentry_sdk.capture_exception(trunc_exc)
                    raise trunc_exc

                video_bytes = await _normalize_for_canvas(
                    video_bytes=video_bytes,
                    timeline=timeline,
                    block_id=block_id,
                    render_id=render_id,
                    fallback_duration_s=float(duration_s or 5.0),
                    r2=r2,
                    is_motion=True,
                    motion_prompt=(i2v_prompt or "continues the previous action smoothly"),
                )
                # Phase 3 + §3.2: reject black / frozen / undershot motion
                # bakes before upload (slot-relative motion-duration gate).
                await _validate_baked_clip_bytes(
                    video_bytes, block_id=block_id, render_id=render_id,
                    is_motion=True,
                    slot_duration_s=_slot_duration_for_block(
                        timeline, block_id, float(duration_s or 0.0)
                    ),
                )
                await r2.upload_bytes(video_bytes, baked_key, "video/mp4")

                logger.info("avatar_action block %s baked → %s (%d bytes)", block_id, baked_key, len(video_bytes))

                _cost_backend = product_bake_backend or "fal_i2v"
                _cost_engine = "product_elements" if product_bake_backend else "wan_i2v"
                cost = round((float(duration_s) or 5.0) * 0.07, 4)
                await _log_generation_cost(
                    user_id=user_id,
                    cast_id=cast_id,
                    block_id=block_id,
                    engine=_cost_engine,
                    operation="cast_block_action",
                    quantity=int(duration_s or 5),
                    cost_usd=cost,
                    metadata_json={
                        "render_id": render_id,
                        "backend": _cost_backend,
                        "prompt": i2v_prompt[:200],
                        "had_end_frame": bool(end_url),
                        "effective_product_id": effective_product_id_bm,
                    },
                )

                completed_count += 1
                pct = int(5 + (completed_count / len(pending_jobs)) * 85)
                bake_end = datetime.now(timezone.utc)
                action_elapsed = max((bake_end - bake_start).total_seconds(), 0.0)
                await _log_render_usage(
                    user_id=user_id,
                    cast_id=cast_id,
                    block_id=block_id,
                    render_id=render_id,
                    backend=_cost_backend,
                    event_type="action_render",
                    elapsed_seconds=action_elapsed,
                    output_video_seconds=float(duration_s),
                    fal_model="wan-2.7" if not product_bake_backend else None,
                    requested_tier_s=motion_requested_tier_s,
                )
                await _update_block_status(render_id, block_id,
                    state="done",
                    provider=_PROVIDER_LABELS.get(_cost_backend, _cost_backend),
                    completed_at=bake_end,
                    duration_s=action_elapsed,
                    voiceover_added=voiceover_added_for_status,
                )
                await _update_render(render_id,
                    baking_chunks_completed=completed_count,
                    progress_percent=pct,
                    progress_step=f"Baked block {completed_count}/{len(pending_jobs)}",
                )
                return primary_id, baked_key

            except Exception as exc:
                sentry_sdk.capture_exception(exc)
                logger.exception("avatar_action block %s failed: %s", block_id, exc)
                raise

        # PIP blocks: always bake at 480p regardless of cast quality
        block_size = "480p" if block_render_mode == "pip" else render_size

        # Mark block as baking (we don't yet know which provider will handle it;
        # the dispatcher reports the backend in its result).
        bake_start = datetime.now(timezone.utc)
        await _update_block_status(render_id, block_id, state="baking", started_at=bake_start)

        # §2.1 planning-layer slot↔audio reconciliation — MUST run BEFORE the
        # audio trim below. The trim clamps the voiceover to the timeline slot;
        # if the slot is a stale short value (e.g. a talking-head beat whose
        # slot froze at ~2s while its voice line is ~6s), trimming first
        # silently drops ~4s of speech + the face, and every check downstream
        # then compares the already-trimmed audio against the slot and sees no
        # mismatch. Reconcile against the RAW (untrimmed) TTS length instead:
        #   * slot ≈ tts                 → left alone; the small trim below is a
        #                                  genuine user shortening.
        #   * slot ≪ tts AND resizable   → _reconcile_slot_vs_audio extends the
        #                                  V1 element's end to the voice, so the
        #                                  whole talking head plays. PIP blocks
        #                                  are resizable here too — their slot
        #                                  is the beat, not a fixed host window.
        #   * slot ≪ tts AND fixed       → raises SlotAudioMismatch (loud
        #                                  planning defect, never a silent cut).
        raw_audio_duration_s = (
            await _probe_audio_duration_s(audio_url) if audio_url else 0.0
        )
        if raw_audio_duration_s <= 0:
            raw_audio_duration_s = float(tts_duration_s or 0)
        if raw_audio_duration_s > 0:
            _slot_before = _slot_duration_for_block(
                timeline, block_id, float(duration_s or 0)
            )
            _v1_meta = (v1_el or {}).get("metadata") or {}
            _fixed_len = bool(
                _v1_meta.get("fixed_length")
                or _v1_meta.get("fixed_duration")
            )
            _reconciled_s, _mismatch = _reconcile_slot_vs_audio(
                timeline,
                block_id=block_id,
                audio_duration_s=raw_audio_duration_s,
                slot_duration_s=_slot_before,
                fixed_length=_fixed_len,
            )
            if _mismatch:
                raise SlotAudioMismatch(
                    f"block {block_id} render {render_id}: slot={_slot_before:.3f}s "
                    f"vs voice={raw_audio_duration_s:.3f}s exceeds "
                    f"{_SLOT_AUDIO_MISMATCH_TOLERANCE:.0%} and slot is "
                    f"fixed-length — stale timeline / planning defect, not "
                    f"re-baking. Reopen the editor to rebuild the slot."
                )
            # Reconcile may have extended the V1 element's end in `timeline`.
            # Pick up the corrected slot so the trim below, the bake dispatch,
            # and every downstream consumer use the real length.
            _slot_after = _slot_duration_for_block(
                timeline, block_id, float(duration_s or 0)
            )
            if _slot_after > 0 and abs(_slot_after - float(duration_s or 0)) > 0.01:
                logger.info(
                    "Block %s render %s: slot reconciled %.3fs -> %.3fs "
                    "against voice=%.3fs (render_mode=%s)",
                    block_id, render_id, float(duration_s or 0), _slot_after,
                    raw_audio_duration_s, block_render_mode,
                )
                duration_s = _slot_after

        # If the user shortened (or lengthened to a smaller clip than the
        # raw TTS) this block in the editor, trim the audio so HOSTKEY's
        # InfiniteTalk worker — which infers output length from the audio
        # it gets — produces a clip that matches the timeline edit. After the
        # reconciliation above, `tts_duration_s - duration_s > 0.05` only holds
        # for a genuine user-shortened beat, not a stale short slot.
        effective_audio_url = audio_url
        if (
            audio_url
            and float(tts_duration_s) - float(duration_s) > 0.05
        ):
            try:
                effective_audio_url = await _trim_and_upload_audio(
                    r2, audio_url, float(duration_s), render_id, block_id,
                )
                logger.info(
                    "Block %s: trimmed audio %.2fs -> %.2fs",
                    block_id, float(tts_duration_s), float(duration_s),
                )
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.warning(
                    "Block %s: audio trim failed (%s); using original audio",
                    block_id, e,
                )
                effective_audio_url = audio_url

        # §2.1 Phase 2 reference duration: probe the prepared lipsync audio
        # that the engine is actually fed. This is the duration the bake's
        # length is driven by, and the reference the speaking-tolerance gate
        # compares against (NOT the slot). 0.0 = probe failed → the gate
        # falls back to the slot.
        effective_audio_duration_s = await _probe_audio_duration_s(
            effective_audio_url
        )
        if effective_audio_duration_s <= 0:
            # Fall back to the known TTS duration so the gate still has an
            # audio-derived reference even when the probe can't read the URL.
            effective_audio_duration_s = float(tts_duration_s or 0)

        # regression-2: lock the lipsync driver and the final mux to the
        # SAME audio. ``effective_audio_url`` is the exact track the engine
        # lip-syncs to (post loudnorm + 100ms edge-pad + silence-trim). The
        # mux reads the block's A1 timeline ``props.src`` (the un-shifted
        # master) — a different file, so lips lead the audio on every block.
        # Record the driver URL; after baking we rewrite the A1 src to it so
        # compose + remux lay down byte-identical audio. Assert the two
        # durations match within the drift tolerance before the block is
        # allowed to proceed.
        if effective_audio_url and a1_el is not None:
            lipsync_audio_by_block[block_id] = effective_audio_url
            mux_src = (a1_el.get("props") or {}).get("src") or ""
            if mux_src and mux_src != effective_audio_url:
                mux_dur = await _probe_audio_duration_s(mux_src)
                drift_s = abs(float(effective_audio_duration_s) - float(mux_dur))
                if mux_dur > 0 and drift_s > _lipsync_audio_drift_max_s():
                    msg = (
                        f"Lipsync/mux audio duration drift: "
                        f"{effective_audio_duration_s:.3f}s vs {mux_dur:.3f}s "
                        f"(drift {drift_s * 1000:.0f}ms > "
                        f"{_lipsync_audio_drift_max_s() * 1000:.0f}ms) "
                        f"for block {block_id} render {render_id}"
                    )
                    sentry_sdk.capture_message(msg)
                    logger.info(
                        "Block %s: pre-rewrite lipsync/mux drift %.0fms "
                        "(lipsync=%.3fs mux=%.3fs); mux src will be rewritten "
                        "to the lipsync driver to eliminate it",
                        block_id, drift_s * 1000,
                        effective_audio_duration_s, mux_dur,
                    )

        # (slot↔audio reconciliation now runs BEFORE the audio trim above,
        # against the raw un-trimmed voice — see there.)

        # PIP (social-proof talking-head) blocks route through the SAME
        # audio-driven speaking cascade as full-frame speaking blocks:
        # WaveSpeed InfiniteTalk → fal Hallo. Both take a face image + audio
        # and animate the mouth from the audio, so they produce a real
        # talking clip with motion. The old PIP path looped the face still
        # into a static video and ran fal sync-lipsync v2/pro off of it,
        # which produced a 100%-frozen bake the validator rejected as
        # clip_mostly_frozen (render rnd_f9a7f008ab85, block blk_c59218e232f4).
        # The PIP inset (small bottom-corner placement) is applied LATER in
        # the FFmpeg compose pass from the block's pip_layout metadata
        # (see cast_ffmpeg_composer.pip_geometry), operating over whatever
        # full-frame clip we bake here — so the bake just needs to be a
        # full-frame talking clip with motion, which this cascade delivers.
        is_pip_block = (block_render_mode == "pip")
        if HOSTKEY_ONLY:
            # HOSTKEY → WaveSpeed → fal MuseTalk cascade.
            #
            # We still serialise the local GPU through the per-loop semaphore
            # so concurrent siblings queue cleanly on HOSTKEY before any
            # cloud spend kicks in. Cloud tiers run unbounded in parallel
            # because they auto-scale.
            from services.render_dispatcher import (
                _get_loop_semaphore, HOSTKEY_QUEUE_WAIT_S,
            )
            from services.provider_chain import (
                try_chain, AllProvidersFailedError, build_speaking_chain,
            )
            from services.render_providers import KlingV3ProElementsProvider

            # Decide whether this speaking block should route through
            # the product-conditioned bake. The gate is broad on purpose:
            # an LLM-authored script often uses demonstratives ("this
            # formula", "the spray", "watch this") and never speaks the
            # literal product name, so a strict name-token gate falls
            # through to InfiniteTalk and hallucinates a generic bottle.
            # Any of THREE signals engages the product bake:
            #   (1) the cast has a product AND a product overlay is
            #       visible on top of this block's slot — the avatar's
            #       hands must hold a matching product, not whatever the
            #       lipsync engine paints in,
            #   (2) the script mentions a product category word
            #       (shampoo, formula, spray, ...), case-insensitive
            #       substring,
            #   (3) the script literally references the product name
            #       via the original token-match heuristic.
            block_script_text = ""
            if cast_product_image_url:
                try:
                    from models.variant import Variant as _Variant
                    from sqlalchemy import select as _sa_select
                    async with factory() as _sp_session:
                        _vres = await _sp_session.execute(
                            _sa_select(_Variant.script_text)
                            .where(_Variant.block_id == block_id)
                            .where(_Variant.is_active.is_(True))
                            .limit(1)
                        )
                        block_script_text = (_vres.scalar() or "").strip()
                except Exception as _spex:
                    sentry_sdk.capture_exception(_spex)
                    block_script_text = ""
            # Slot timing for the on-screen-overlay check. We prefer V1
            # but fall back to A1 for audio-only voiceover blocks where
            # V1 is None (defensive — voiceover blocks skip this branch
            # via is_pip / HOSTKEY_ONLY routing, but the fallback keeps
            # the helper robust).
            try:
                _slot_el = v1_el if v1_el is not None else a1_el
                _slot_start = float((_slot_el or {}).get("s") or 0)
                _slot_end = float((_slot_el or {}).get("e") or 0)
            except (TypeError, ValueError) as _slot_exc:
                sentry_sdk.capture_exception(_slot_exc)
                _slot_start, _slot_end = 0.0, 0.0
            product_overlay_on_slot = (
                bool(cast_product_image_url)
                and _timeline_has_product_overlay_for_block(
                    timeline, block_id, _slot_start, _slot_end,
                )
            )
            script_mentions_category = _script_mentions_product_category(
                block_script_text,
            )
            script_names_product = _script_references_product(
                block_script_text, cast_product_names,
            )
            # Kept under the original name so the downstream prompt /
            # logging code paths don't have to change.
            block_references_product = (
                product_overlay_on_slot
                or script_mentions_category
                or script_names_product
            )
            sem = _get_loop_semaphore()
            # Best-effort acquisition: a freshly-released slot is grabbed
            # quickly. Longer waits just defeat failover — sibling speaking
            # blocks waiting 120s on a busy local GPU never get to try the
            # cloud providers and surface TimeoutError instead.
            #
            # HOSTKEY is decommissioned: when the kill-switch is set (the
            # default) we never acquire the on-prem GPU slot. hostkey_acquired
            # stays False so build_speaking_chain omits the on-prem provider
            # and the chain runs WaveSpeed InfiniteTalk → fal Hallo only.
            from services.hostkey_flags import (
                hostkey_disabled as _hostkey_disabled,
                log_hostkey_skip as _log_hostkey_skip,
            )
            hostkey_acquired = False
            if _hostkey_disabled():
                _log_hostkey_skip("wavespeed_infinitetalk / fal_hallo")
            elif sem is not None:
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=3.0)
                    hostkey_acquired = True
                except asyncio.TimeoutError:
                    # Designed failover: a busy local GPU semaphore is the
                    # success path — we deliberately skip HOSTKEY and let
                    # the cloud chain handle this block. Breadcrumb keeps
                    # the diagnostic in any subsequent error event without
                    # producing a standalone Sentry issue.
                    sentry_sdk.add_breadcrumb(
                        category="render",
                        level="info",
                        message="hostkey_semaphore_busy_fallthrough",
                        data={
                            "block_id": block_id,
                            "render_id": render_id,
                            "timeout_s": 3.0,
                        },
                    )
                    logger.info(
                        "Block %s: HOSTKEY busy — falling through to cloud providers only",
                        block_id,
                    )
            try:
                # The on-prem GPU (RTX 4090, 24 GB) cannot reliably finish
                # Wan2.1-14B InfiniteTalk multitalk at 720p / 1080p within a
                # reasonable per-block budget — cap at 480p for HOSTKEY.
                _HOSTKEY_MAX_SIZE = (
                    os.environ.get("CAST_RENDER_HOSTKEY_MAX_SIZE", "480p") or "480p"
                ).strip().lower()
                _SIZE_RANK = {"480p": 0, "720p": 1, "1080p": 2}
                if _SIZE_RANK.get(block_size, 0) > _SIZE_RANK.get(_HOSTKEY_MAX_SIZE, 0):
                    logger.info(
                        "Render %s block %s: capping HOSTKEY size from %s to %s",
                        render_id, block_id, block_size, _HOSTKEY_MAX_SIZE,
                    )
                    effective_block_size = _HOSTKEY_MAX_SIZE
                else:
                    effective_block_size = block_size
                _cw_b, _ch_b, _ = _canvas_dims_for_render(timeline)
                _fmt_b = getattr(cast, "output_format", None) or ("16:9" if _cw_b > _ch_b else "1:1" if _cw_b == _ch_b else "9:16")
                _size_table = _infinitetalk_sizes_map(_fmt_b, is_landscape=(_cw_b > _ch_b))
                w, h = _size_table.get(
                    effective_block_size, _size_table.get("480p", (480, 848))
                )

                # Build the cascade via the provider_chain helper. For
                # blocks >= 10s (or with PRODUCTION_LIPSYNC=1 set) it
                # inserts the top-tier sync-lipsync v3 / v2-pro pair
                # ahead of the existing on-prem / WaveSpeed cascade.
                # hostkey_acquired=False removes the on-prem provider so
                # the local GPU isn't hammered while a sibling bake is
                # already running on it.
                speaking_providers = build_speaking_chain(
                    block_duration_s=float(duration_s or 0),
                    hostkey_acquired=hostkey_acquired,
                )

                # Product-conditioned base bake runs ahead of every
                # standard speaking provider when the block actually
                # talks about the product. It produces a product-aware
                # base and applies lipsync internally. If it raises,
                # try_chain falls through to the standard cascade
                # built above.
                #
                # PR #92: PRODUCT and PRODUCT_DEMO speaking blocks force
                # the product-conditioned bake whenever an effective
                # product image resolves — even when no script keyword
                # matches. Demo blocks are authored to show the product,
                # not to name it, so they otherwise fall through to the
                # non-conditioned cascade and the lipsync engine paints
                # in a hallucinated bottle.
                _sp_block_type_str = ""
                _sp_effective_product_id: str | None = None
                try:
                    from models.block import Block as _BlockSp
                    async with factory() as _sp_session:
                        _sp_blk = await _sp_session.get(_BlockSp, block_id)
                        _sp_bt = getattr(_sp_blk, "type", None) if _sp_blk else None
                        if hasattr(_sp_bt, "value"):
                            _sp_block_type_str = str(_sp_bt.value or "")
                        elif _sp_bt is not None:
                            _sp_block_type_str = str(_sp_bt)
                        _sp_effective_product_id = await resolve_effective_product_id(
                            _sp_blk, _sp_session, cast_id,
                        )
                except Exception as _spgex:
                    sentry_sdk.capture_exception(_spgex)
                    _sp_block_type_str = ""
                    _sp_effective_product_id = None
                _force_product_for_demo = (
                    _sp_block_type_str in ("PRODUCT", "PRODUCT_DEMO")
                    and bool(_sp_effective_product_id)
                    and bool(cast_product_image_url)
                )
                # PIP testimonial faces never route through the
                # product-conditioned bake — they are a social-proof talking
                # head, not a product demo, and forcing the elements provider
                # would paint a hallucinated product into the inset.
                product_bake_enabled = bool(
                    not is_pip_block
                    and cast_product_image_url
                    and (block_references_product or _force_product_for_demo)
                )
                if product_bake_enabled:
                    speaking_providers.insert(0, KlingV3ProElementsProvider())
                    logger.info(
                        "Block %s: routing through product-conditioned bake "
                        "(overlay_on_slot=%s, category_word=%s, name_token=%s, "
                        "force_demo=%s)",
                        block_id,
                        product_overlay_on_slot,
                        script_mentions_category,
                        script_names_product,
                        _force_product_for_demo,
                    )
                logger.info(
                    "product gate: block %s type=%s effective_product=%s → route=%s",
                    block_id,
                    _sp_block_type_str or "?",
                    _sp_effective_product_id or "None",
                    "product_elements" if product_bake_enabled else "avatar_speaking",
                )

                # When the product-conditioned base is in play we ask
                # the scene description to lock the product into the
                # avatar's hand across every frame. The standard
                # speaking providers ignore the extra prompt clause
                # harmlessly when the elements provider doesn't run.
                speaking_prompt = motion_prompt
                if product_bake_enabled:
                    try:
                        from services.ai_prompts import (
                            build_product_aware_scene_prompt,
                        )
                        speaking_prompt = build_product_aware_scene_prompt(
                            base_prompt=motion_prompt,
                            product_name=(
                                cast_product_names[0]
                                if cast_product_names else None
                            ),
                        )
                    except Exception as _pp_exc:
                        sentry_sdk.capture_exception(_pp_exc)
                        speaking_prompt = motion_prompt
                async def _on_speaking_attempt(event: dict) -> None:
                    # Live progress: record which provider tier is being
                    # tried right now (and why the last one failed) instead
                    # of only writing block_statuses once the entire chain
                    # has succeeded or exhausted every tier. A block that
                    # cycles through 2-3 tiers before succeeding can look
                    # like a silent multi-tens-of-minutes stall otherwise.
                    if event.get("phase") == "started":
                        await _update_block_status(
                            render_id, block_id,
                            current_provider=event["provider"],
                            current_tier=event["tier"],
                        )
                    elif event.get("phase") == "failed":
                        await _update_block_status(
                            render_id, block_id,
                            append_attempt={
                                "provider": event["provider"],
                                "tier": event["tier"],
                                "error": event.get("error"),
                                "latency_ms": event.get("latency_ms"),
                            },
                        )

                try:
                    chain_result = await try_chain(
                        speaking_providers,
                        step_label="speaking",
                        render_id=render_id,
                        block_id=block_id,
                        on_attempt=_on_speaking_attempt,
                        image_url=face_ref_url,
                        audio_url=effective_audio_url,
                        prompt=speaking_prompt,
                        width=w,
                        height=h,
                        duration_s=duration_s,
                        # PR #68: explicit audio_duration_s so cloud
                        # providers can scale their poll budgets per
                        # block. Without this, cloud tiers fell back
                        # to a 60–120 s hardcoded ceiling and
                        # timed out on every multi-second clip.
                        audio_duration_s=duration_s,
                        # Product-elements provider reads these; the
                        # other speaking providers ignore unknown
                        # kwargs via **_kw.
                        product_image_url=(
                            cast_product_image_url
                            if product_bake_enabled else None
                        ),
                        product_image_urls=(
                            list(cast_product_image_urls)
                            if product_bake_enabled else None
                        ),
                        script_references_product=product_bake_enabled,
                    )
                except AllProvidersFailedError as exc:
                    sentry_sdk.capture_exception(exc)
                    raise

                # Post-bake refinement: re-sync mouth motion on the
                # produced video with fal-ai/sync-lipsync/v3, then
                # fall back to v2/pro if v3 fails. Skipped on sub-3s
                # blocks where the original bake's lipsync is already
                # acceptable and the $5-8/min spend isn't justified.
                # The refine step is purely additive — on any failure
                # we keep the original chain_result video_url and let
                # downstream extraction continue.
                try:
                    from services.provider_chain import (
                        evaluate_speaking_lipsync_refine,
                    )
                    _refine_decision = evaluate_speaking_lipsync_refine(
                        float(duration_s or 0)
                    )
                except Exception as _gate_exc:
                    sentry_sdk.capture_exception(_gate_exc)
                    from services.provider_chain import RefineDecision
                    _refine_decision = RefineDecision(
                        False, "gate_error", "speak"
                    )
                refine_eligible = _refine_decision.run
                _bake_video_url = chain_result.get("video_url") or ""
                # §C diagnostic: the audio actually fed to the refine pass
                # drives the output length, so probe it for the in_dur field.
                _refine_in_dur = await _probe_audio_duration_s(
                    effective_audio_url
                ) if effective_audio_url else 0.0
                _refine_ran = False
                _refine_fal_req = ""
                _refine_out_dur = 0.0
                if (
                    refine_eligible
                    and _bake_video_url
                    and effective_audio_url
                ):
                    from services.render_providers import (
                        FalSyncLipsyncV3Provider,
                        FalSyncLipsyncV2ProProvider,
                    )
                    _refine_providers = [
                        FalSyncLipsyncV3Provider(),
                        FalSyncLipsyncV2ProProvider(),
                    ]
                    _refined_url: str | None = None
                    for _rp in _refine_providers:
                        try:
                            _ref_result = await _rp.refine(
                                video_url=_bake_video_url,
                                audio_url=effective_audio_url,
                                duration_s=float(duration_s or 0),
                                block_id=block_id,
                                render_id=render_id,
                            )
                            _refined_url = (
                                _ref_result.get("video_url")
                                if isinstance(_ref_result, dict) else None
                            )
                            if _refined_url:
                                _refine_ran = True
                                _refine_fal_req = (
                                    _ref_result.get("fal_request_id")
                                    or _ref_result.get("request_id")
                                    or ""
                                ) if isinstance(_ref_result, dict) else ""
                                logger.info(
                                    "Block %s: post-bake lipsync refine "
                                    "succeeded via %s",
                                    block_id, _rp.name,
                                )
                                chain_result["video_url"] = _refined_url
                                chain_result["_post_bake_refined_by"] = _rp.name
                                _refine_out_dur = await _probe_audio_duration_s(
                                    _refined_url
                                )
                                break
                        except Exception as _ref_exc:
                            sentry_sdk.capture_exception(_ref_exc)
                            logger.warning(
                                "Block %s: post-bake refine via %s failed (%s); "
                                "trying next refine tier",
                                block_id, _rp.name, _ref_exc,
                            )
                    if _refined_url is None:
                        logger.info(
                            "Block %s: post-bake refine fell through; "
                            "keeping original bake",
                            block_id,
                        )
                elif refine_eligible and not _bake_video_url:
                    logger.info(
                        "Block %s: skipping post-bake refine — provider "
                        "returned no downloadable video_url",
                        block_id,
                    )
                _log_lipsync_refine_decision(
                    block_id=block_id,
                    render_id=render_id,
                    decision=_refine_decision,
                    ran=_refine_ran,
                    in_dur=_refine_in_dur,
                    out_dur=_refine_out_dur,
                    fal_req=_refine_fal_req,
                )

                provider_used = chain_result.get("_provider_used", "unknown")
                tier_used = chain_result.get("_tier_used", -1)
                # Map provider name → backend label so downstream cost &
                # status logic keeps working without case-by-case branches.
                _PROVIDER_TO_BACKEND = {
                    "hostkey_infinitetalk": "hostkey",
                    "wavespeed_infinitetalk": "wavespeed",
                    # PR #67 Bug 3: renamed from fal_musetalk; keep the
                    # old key as an alias so any in-flight render
                    # metadata persisted under the old label still maps.
                    "fal_hallo": "fal_hallo",
                    "fal_musetalk": "fal_hallo",
                    # Product-conditioned base bake — internal label
                    # only; the user-visible status pill still shows
                    # the generic "baking" / "done" copy from
                    # _PROVIDER_LABELS rather than the engine name.
                    "product_elements_bake": "product_elements_bake",
                    # Top-tier sync-lipsync providers.
                    "fal_sync_lipsync_v3": "fal_sync_lipsync_v3",
                    "fal_sync_lipsync_v2_pro": "fal_sync_lipsync_v2_pro",
                }
                backend_label = _PROVIDER_TO_BACKEND.get(provider_used, provider_used)

                # Strip orchestration metadata from the dict before passing
                # downstream so the existing "video_url / video / video_base64"
                # extraction logic sees a clean engine response.
                hk_output = {
                    k: v for k, v in chain_result.items()
                    if not k.startswith("_")
                }
                dispatch_result = {
                    "output": hk_output,
                    "backend": backend_label,
                    "provider_used": provider_used,
                    "tier_used": tier_used,
                }
            finally:
                # Releasing an unacquired Semaphore would over-count its
                # internal value and let two concurrent HOSTKEY jobs run.
                if hostkey_acquired and sem is not None:
                    try:
                        sem.release()
                    except Exception as _rel_exc:
                        sentry_sdk.capture_exception(_rel_exc)
        else:
            dispatch_result = await dispatcher.submit_and_wait(
                image_url=face_ref_url,
                audio_url=effective_audio_url,
                prompt=motion_prompt,
                size=block_size,
                audio_duration_s=duration_s,
                is_pip=is_pip_block,
                block_id=block_id,
            )

        backend = dispatch_result.get("backend", "unknown")
        output = dispatch_result.get("output", {})
        if isinstance(output, str):
            output = {"video_base64": output}
        logger.info("Block %s rendered on %s, output keys: %s", block_id, backend,
                     list(output.keys()) if isinstance(output, dict) else type(output).__name__)

        # Extract video bytes from output. MuseTalk now returns an R2 key
        # (mp4 already in the CDN bucket); InfiniteTalk/HOSTKEY still
        # returns base64 or a video_url. _resolve_video_bytes handles the
        # first two; the video_url branch stays inline because it's
        # specific to RunPod's polled-result shape.
        nested = output.get("result", {}) if isinstance(output.get("result"), dict) else {}
        video_url = output.get("video_url") or nested.get("video_url", "")
        has_r2_key = isinstance(output, dict) and bool(output.get("output_r2_key"))
        video_b64 = (
            output.get("video_base64")
            or output.get("video")
            or nested.get("video_base64", "")
            or nested.get("video", "")
        )

        if has_r2_key:
            video_bytes = await asyncio.to_thread(_resolve_video_bytes, output)
            logger.info(
                "Fetched mp4 from R2 for block %s: %d bytes (key=%s)",
                block_id, len(video_bytes), output.get("output_r2_key"),
            )
        elif video_url:
            async with httpx.AsyncClient(timeout=120.0) as http:
                video_resp = await http.get(video_url)
                video_resp.raise_for_status()
                video_bytes = video_resp.content
        elif video_b64:
            video_bytes = _resolve_video_bytes({"video": video_b64})
            logger.info("Decoded base64 video for block %s: %d bytes", block_id, len(video_bytes))
        else:
            output_summary = {k: type(v).__name__ + (f'[{len(v)}]' if isinstance(v, (str, list)) else '')
                              for k, v in output.items()} if isinstance(output, dict) else str(output)[:500]
            raise RuntimeError(f"No video returned for block {block_id}. Output keys: {output_summary}")

        # If HOSTKEY rendered the block at a smaller size than the cast's
        # target render_size (e.g. 480p capped from 720p), upscale here so
        # the compose stage receives a uniformly-sized base and the timeline
        # overlay positioning (computed against render_width/height) lands
        # correctly on top. We use ffmpeg's bicubic scaler which is fast on
        # CPU for short clips. We do NOT use a heavyweight ML upscaler here
        # because the final compose+overlay re-encode happens after this.
        try:
            if backend == "hostkey" and not is_pip_block:
                _src_w, _src_h = w, h
                _cw_b, _ch_b, _ = _canvas_dims_for_render(timeline)
                _fmt_b = getattr(cast, "output_format", None) or ("16:9" if _cw_b > _ch_b else "1:1" if _cw_b == _ch_b else "9:16")
                _size_table = _infinitetalk_sizes_map(_fmt_b, is_landscape=(_cw_b > _ch_b))
                _tgt_w, _tgt_h = _size_table.get(
                    block_size, _size_table.get("480p", (480, 848))
                )
                if (_src_w, _src_h) != (_tgt_w, _tgt_h):
                    import tempfile as _tempfile
                    import subprocess as _subprocess
                    with _tempfile.TemporaryDirectory(prefix=f"upscale_{block_id}_") as _td:
                        _src_path = os.path.join(_td, "src.mp4")
                        _dst_path = os.path.join(_td, "dst.mp4")
                        with open(_src_path, "wb") as _sf:
                            _sf.write(video_bytes)
                        _scale_cmd = [
                            "ffmpeg", "-y", "-i", _src_path,
                            "-vf", f"scale={_tgt_w}:{_tgt_h}:flags=bicubic",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-c:a", "copy",
                            _dst_path,
                        ]
                        _proc = await asyncio.to_thread(
                            _subprocess.run, _scale_cmd,
                            capture_output=True, timeout=max(30.0, duration_s * 6),
                            check=False,
                        )
                        if _proc.returncode == 0 and os.path.exists(_dst_path):
                            with open(_dst_path, "rb") as _df:
                                _scaled = _df.read()
                            logger.info(
                                "Block %s upscaled %dx%d → %dx%d (%d → %d bytes)",
                                block_id, _src_w, _src_h, _tgt_w, _tgt_h,
                                len(video_bytes), len(_scaled),
                            )
                            video_bytes = _scaled
                        else:
                            logger.warning(
                                "Block %s upscale failed (rc=%s); keeping %dx%d. stderr=%s",
                                block_id, _proc.returncode, _src_w, _src_h,
                                (_proc.stderr or b"")[-500:].decode("utf-8", "replace"),
                            )
        except Exception as _ups_exc:
            sentry_sdk.capture_exception(_ups_exc)
            logger.warning("Block %s upscale step raised; using source bytes. err=%s",
                           block_id, _ups_exc)

        # Phase 2: speaking-block tolerance gate. Only full-avatar speaking
        # bakes reach here (motion / body_motion / voiceover return earlier;
        # PIP small bakes are excluded). This runs on the RAW bake (before
        # normalize/extend-to-slot) because the band is measured against the
        # TTS AUDIO, not the slot: a lipsync clip whose duration falls outside
        # the [-2%, +5%] band around AUDIO is a defect (short = dropped speech
        # / desync, much-long = over-run). We end-trim a within-tolerance
        # overshoot and RAISE outside the band so the existing retry-once pass
        # re-bakes. We never pad / stretch / reverse to hit the band — when the
        # bake is shorter than the user SLOT (audio < slot) the slot is filled
        # later by _normalize_for_canvas (extend-to-slot), NOT by faking the
        # audio match here.
        if not is_pip_block:
            video_bytes = await _enforce_speaking_tolerance(
                video_bytes,
                timeline=timeline,
                block_id=block_id,
                render_id=render_id,
                fallback_duration_s=duration_s,
                audio_duration_s=effective_audio_duration_s,
            )
        # Post-bake normalization + conform-to-slot: HOSTKEY InfiniteTalk has
        # been observed returning 720x1280@25fps clips of ~9s when the cast
        # canvas is 1114x828@30fps and the slot is 7.43s — the compose service
        # then center-crops the portrait clip and chops off the avatar's
        # forehead. Pad+fps+conform to canvas before upload. When the bake is
        # SHORTER than the user-defined slot (audio < slot), this EXTENDS the
        # video to fill the slot (never shrinks the slot) — see
        # block_extension.trim_block_to_slot.
        video_bytes = await _normalize_for_canvas(
            video_bytes=video_bytes,
            timeline=timeline,
            block_id=block_id,
            render_id=render_id,
            fallback_duration_s=duration_s,
            r2=r2,
            motion_prompt=(motion_prompt or "stays in place with subtle natural micro-movements, breathing softly"),
        )
        # PR #82: per-block audio/video duration reconciliation. The
        # lipsync engines (and the normalize pass above) can leave the
        # audio stream a few hundred ms shorter than the video, which
        # produces mouth-held-open frames at the clip tail and visible
        # de-sync at the start of the next block. Pad with silence or
        # trim to match before R2 upload.
        video_bytes = await _match_audio_to_video_duration(
            video_bytes,
            block_id=block_id,
            render_id=render_id,
        )
        # Guardrail (PIP only): the conform above must land the clip on its
        # bonded slot. PIP skips the speaking tolerance gate and the Phase 3
        # duration gate, so without this a silently-failed extend-to-slot
        # ships a short clip that desyncs the bonded concat.
        if is_pip_block:
            await _enforce_pip_slot_duration(
                video_bytes,
                timeline=timeline,
                block_id=block_id,
                render_id=render_id,
                fallback_duration_s=duration_s,
            )
        # Phase 3: reject black / frozen / truncated bakes before upload.
        await _validate_baked_clip_bytes(
            video_bytes, block_id=block_id, render_id=render_id,
        )
        await r2.upload_bytes(video_bytes, baked_key, "video/mp4")
        provider_used = dispatch_result.get("provider_used", backend)
        tier_used = dispatch_result.get("tier_used", -1)
        logger.info(
            "Block %s baked via %s (tier %s) → %s (%d bytes)",
            block_id, provider_used, tier_used, baked_key, len(video_bytes),
        )

        try:
            from models.variant import Variant as _VPostBake
            from sqlalchemy import select as _sa_select_pb
            async with factory() as _pb_sess:
                _v_res = await _pb_sess.execute(
                    _sa_select_pb(_VPostBake).where(
                        _VPostBake.block_id == block_id,
                        _VPostBake.is_active.is_(True),
                    )
                )
                _v_inst = _v_res.scalar_one_or_none()
                if _v_inst:
                    _v_inst.video_key = baked_key
                    _v_inst.final_video_key = baked_key
                    await _pb_sess.commit()
        except Exception as _pb_err:
            sentry_sdk.capture_exception(_pb_err)

        # Write cost row — $0 for HOSTKEY, Modal ~$0.005/s output, WaveSpeed
        # ~$0.03/s with $0.15 minimum, RunPod at INFINITETALK_COST_PER_SECOND.
        if backend == "hostkey":
            cost = 0.0
        elif backend == "modal":
            cost = round(duration_s * 0.0053, 4)  # L40S @ ~$3.20/hr, 6x realtime
        elif backend == "wavespeed":
            cost = round(max(duration_s * 0.03, 0.15), 4)
        elif backend in ("fal_hallo", "fal_musetalk"):
            # PR #67 Bug 3: tier 3 is fal-ai/hallo now (image+audio →
            # video). Pricing is roughly the same order of magnitude
            # as the old MuseTalk fallback (~$0.005/output-second).
            cost = round(duration_s * 0.005, 4)
        elif backend == "fal_sync_lipsync_v3":
            # PR #82: $8/min → $0.1333/output-second.
            cost = round(duration_s * 0.1333, 4)
        elif backend == "fal_sync_lipsync_v2_pro":
            # PR #82: $5/min → $0.0833/output-second.
            cost = round(duration_s * 0.0833, 4)
        else:
            cost = round(duration_s * INFINITETALK_COST_PER_SECOND, 4)
        await _log_generation_cost(
            user_id=user_id,
            cast_id=cast_id,
            block_id=block_id,
            engine="infinite_talk",
            operation="cast_block_bake",
            quantity=int(duration_s),
            cost_usd=cost,
            metadata_json={"render_id": render_id, "backend": backend},
        )

        # Update progress — blocks complete out of order
        completed_count += 1
        pct = int(5 + (completed_count / len(pending_jobs)) * 85)
        bake_end = datetime.now(timezone.utc)
        bake_elapsed = max((bake_end - bake_start).total_seconds(), 0.0)
        # PIP routes through MuseTalk first ("musetalk" backend); speaking
        # blocks route through InfiniteTalk on HOSTKEY/Modal/RunPod.
        if backend == "musetalk":
            usage_event_type = "pip_render"
        else:
            usage_event_type = "pip_render" if is_pip_block else "avatar_render"
        await _log_render_usage(
            user_id=user_id,
            cast_id=cast_id,
            block_id=block_id,
            render_id=render_id,
            backend=backend,
            event_type=usage_event_type,
            elapsed_seconds=bake_elapsed,
            # PR-I: video-second backends (fal_hallo, fal_sync_lipsync_v2_pro /
            # v3, wavespeed) bill on OUTPUT clip length = the slot duration
            # (`duration_s`), not wall-clock GPU seconds. gpu_seconds backends
            # (hostkey/musetalk/modal/runpod) ignore this and bill elapsed.
            output_video_seconds=float(duration_s) if duration_s else None,
        )
        await _update_block_status(render_id, block_id,
            state="done",
            provider=_PROVIDER_LABELS.get(backend, backend),
            completed_at=bake_end,
            duration_s=max((bake_end - bake_start).total_seconds(), 0.0),
        )
        await _update_render(render_id,
            baking_chunks_completed=completed_count,
            progress_percent=pct,
            # User-facing message: no engine name (per RULES). Provider is surfaced
            # via the per-block status pill where the UI maps to Host/Mod/Pod.
            progress_step=f"Baked block {completed_count}/{len(pending_jobs)}",
        )

        # primary_id falls back to A1 for audio-only voiceover blocks where
        # the timeline snapshot has no V1 (older snapshots predating the
        # editor placeholder-V1 fix); see also the early-return in the
        # voiceover skip-bake branch above.
        return primary_id, baked_key

    # Dispatch all blocks concurrently. The single-slot upstream GPU is
    # serialized by the per-event-loop Semaphore(1) acquired inside
    # dispatch_and_upload's on-prem branch, so concurrent jobs whose
    # backends are off-prem (paid tiers) run truly in parallel both with
    # each other and alongside the queued on-prem job. Progress-step
    # ordering becomes nondeterministic — that's acceptable; the count
    # is what matters. A single-block failure no longer aborts the whole
    # cast — survivors finish baking, and the cast is marked failed
    # below if any block did not bake.
    failed_blocks: list[tuple[str, str]] = []  # (block_id, error_message)
    total = len(pending_jobs)

    async def _run_one_job(i: int, job: tuple) -> tuple:
        idx, bid, v1, a1, key, dur, face, audio, prompt = job
        try:
            await _update_render(
                render_id,
                progress_step=f"Rendering avatar blocks ({i + 1}/{total})",
            )
        except Exception as _ue:
            sentry_sdk.capture_exception(_ue)
        try:
            element_id, baked_key = await dispatch_and_upload(
                idx, bid, v1, a1, key, dur, face, audio, prompt,
            )
            return ("ok", bid, element_id, baked_key)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            # str(httpx.ConnectError) is often "" for a refused TCP — fall
            # back to repr() / a placeholder so the DB row + log line carry
            # at least the exception type. %r in the log message guarantees
            # the type is visible even when str(exc) is empty.
            err_str = str(exc) or repr(exc) or "(no message)"
            err_msg = f"{type(exc).__name__}: {err_str}"
            logger.error("Failed to bake block %s: %r", bid, exc)
            try:
                await _update_block_status(
                    render_id, bid, state="failed", error=err_msg[:300],
                )
            except Exception as _se:
                sentry_sdk.capture_exception(_se)
            return ("err", bid, err_msg[:300], None)

    results = await asyncio.gather(
        *[_run_one_job(i, j) for i, j in enumerate(pending_jobs)],
        return_exceptions=False,
    )

    for r in results:
        if r[0] == "err":
            failed_blocks.append((r[1], r[2]))
        else:
            _, _, element_id, baked_key = r
            if baked_key:  # Voiceover blocks return None (no baked video)
                baked_urls[element_id] = r2.get_public_url(baked_key)

    # PR #68: failed-block retry pass.
    #
    # Cloud-tier transient failures (timeouts polled before the upstream
    # finished, 5xx blips, HOSTKEY VRAM still hot from the prior block)
    # used to fail the whole render. Before tagging the render FAILED,
    # walk the failed list once more and re-bake each block exactly
    # once — provider chain state has moved on (HOSTKEY VRAM may have
    # drained, recovery may have run), so a second attempt often
    # succeeds.
    #
    # Bounded: retry_count is persisted on the block_statuses row;
    # only blocks with retry_count == 0 are retried, so a misbehaving
    # block can't loop forever.
    if failed_blocks:
        retry_targets: list[tuple[str, str]] = []
        try:
            from models.cast_render import CastRender
            factory = _make_session_factory()
            async with factory() as session:
                render_row = await session.get(CastRender, render_id)
                existing_statuses = list((render_row.block_statuses or [])
                                         if render_row else [])
            existing_by_id = {row.get("block_id"): row for row in existing_statuses}
            for bid, err in failed_blocks:
                row = existing_by_id.get(bid) or {}
                if int(row.get("retry_count", 0)) == 0:
                    retry_targets.append((bid, err))
        except Exception as _e_lookup:
            sentry_sdk.capture_exception(_e_lookup)
            # If we can't read prior retry_count, be conservative and
            # retry everything once — the persisted retry_count below
            # still prevents a second retry.
            retry_targets = list(failed_blocks)

        if retry_targets:
            logger.info(
                "Render %s: retrying %d failed blocks before compose",
                render_id, len(retry_targets),
            )
            retry_jobs = [
                j for j in pending_jobs if j[1] in {b for b, _ in retry_targets}
            ]
            # Mark retry_count BEFORE attempting so a crash mid-retry
            # still records that the attempt was made.
            for bid, _ in retry_targets:
                try:
                    await _update_block_status(
                        render_id, bid, retry_count=1, state="retrying",
                    )
                except Exception as _se:
                    sentry_sdk.capture_exception(_se)

            async def _retry_one(i: int, job: tuple) -> tuple:
                try:
                    return await _run_one_job(i, job)
                except Exception as exc:
                    sentry_sdk.capture_exception(exc)
                    logger.warning(
                        "Retry of block %s failed: %s",
                        job[1] if len(job) > 1 else "?", exc,
                    )
                    return ("err", job[1], str(exc)[:300], None)

            try:
                retry_results = await asyncio.gather(
                    *[_retry_one(i, j) for i, j in enumerate(retry_jobs)],
                    return_exceptions=False,
                )
            except Exception as _rg_exc:
                sentry_sdk.capture_exception(_rg_exc)
                retry_results = []

            # Reconcile: drop survivors from failed_blocks, add their
            # baked URLs into the compose set.
            recovered_ids: set[str] = set()
            for r in retry_results:
                if r[0] == "ok":
                    _, bid_ok, element_id, baked_key = r
                    if baked_key:
                        baked_urls[element_id] = r2.get_public_url(baked_key)
                    recovered_ids.add(bid_ok)
            if recovered_ids:
                failed_blocks = [
                    (bid, err) for bid, err in failed_blocks
                    if bid not in recovered_ids
                ]
                logger.info(
                    "Render %s: retry pass recovered %d/%d blocks",
                    render_id, len(recovered_ids), len(retry_targets),
                )

    # If at least one InfiniteTalk block failed, mark the render FAILED
    # with a friendly message and exit early. The frontend can offer the
    # user a "retry" path; surviving baked clips remain in R2 for reuse.
    if failed_blocks:
        n = len(failed_blocks)
        too_short_ids = [bid for bid, err in failed_blocks if "clip_too_short" in err]
        if too_short_ids and len(too_short_ids) == n:
            plural = n != 1
            msg = (
                f"{n} clip{'s' if plural else ''} on your timeline "
                f"{'are' if plural else 'is'} too short to render — the voiceover "
                f"doesn't fit in the time given. Open the editor, extend the clip"
                f"{'s' if plural else ''} (drag the edge out) so {'they are' if plural else 'it is'} "
                f"long enough for its spoken line, then render again."
            )
        else:
            msg = f"AI render failed for {n} of {total} avatar blocks — please retry."
        logger.warning(
            "Render %s: %d/%d blocks failed; marking render FAILED. ids=%s",
            render_id, n, total, [b for b, _ in failed_blocks],
        )
        await _update_render(
            render_id,
            status=CastRenderStatus.FAILED.value,
            error_message=msg,
        )
        return

    # PR-H: re-measure the baked lipsync clips and snap every timeline slot to
    # the clip's ACTUAL duration before composing. The slots were first sized
    # from the TTS ``using=`` estimate; the lipsync provider returns clips that
    # differ from that estimate by a few tens of ms, and because each slot
    # starts where the previous one ends, the gap accumulates and reads as
    # lipsync drift that snaps back at each cut (the 0:07 resync, 0:19+ drift
    # on rnd_ee3e955ef8f5). Snapping the slots to the measured durations keeps
    # the absolutely-placed audio locked to the lips. Failure here is
    # non-fatal — we fall back to the TTS-derived slots.
    try:
        measured_durations = await _measure_baked_block_durations(
            timeline, baked_urls, render_id,
        )
        # Fill in voiceover blocks _measure_baked_block_durations couldn't
        # cover (no baked clip to probe — e.g. the bake failed) with the
        # real audio-driven duration their bake targeted. A successful
        # measurement is more precise (it reflects the actual encoded
        # clip) so it wins on conflict; this dict only fills gaps.
        combined_durations = {**voiceover_real_durations, **measured_durations}
        if combined_durations:
            _remeasured_tl, _re_rewritten, _ = _apply_real_block_durations(
                timeline,
                block_durations=combined_durations,
                render_id=render_id,
                fps=30,
            )
            if _re_rewritten:
                timeline = _remeasured_tl
                try:
                    from models.cast_render import CastRender as _CR_rm
                    from sqlalchemy.orm.attributes import flag_modified as _flag_rm
                    async with factory() as _rm_session:
                        _rm_row = await _rm_session.get(_CR_rm, render_id)
                        if _rm_row is not None:
                            _rm_row.timeline_snapshot = timeline
                            _flag_rm(_rm_row, "timeline_snapshot")
                            await _rm_session.commit()
                except Exception as _rm_persist_exc:
                    sentry_sdk.capture_exception(_rm_persist_exc)
    except Exception as _remeasure_exc:
        sentry_sdk.capture_exception(_remeasure_exc)
        logger.warning(
            "Render %s: post-bake duration re-measure failed (%s); "
            "composing with TTS-derived slots",
            render_id, _remeasure_exc,
        )

    # regression-2: all surviving blocks are baked. Before composing, point
    # every audio element at the exact audio its block was lip-synced to so
    # the compose + remux passes lay down identical audio (no onset/length
    # drift between the lips and the muxed track). Then assert identity and
    # persist the rewritten snapshot so retries on the same render_id reuse
    # the corrected layout.
    try:
        n_rewritten = _rewrite_mux_audio_to_lipsync(
            timeline,
            lipsync_audio_by_block=lipsync_audio_by_block,
            render_id=render_id,
        )
        await _assert_lipsync_mux_audio_identity(
            timeline,
            lipsync_audio_by_block=lipsync_audio_by_block,
            render_id=render_id,
        )
        if n_rewritten:
            try:
                from models.cast_render import CastRender as _CR_ls
                from sqlalchemy.orm.attributes import flag_modified as _flag_ls
                async with factory() as _ls_session:
                    _ls_row = await _ls_session.get(_CR_ls, render_id)
                    if _ls_row is not None:
                        _ls_row.timeline_snapshot = timeline
                        _flag_ls(_ls_row, "timeline_snapshot")
                        await _ls_session.commit()
            except Exception as _ls_persist_exc:
                sentry_sdk.capture_exception(_ls_persist_exc)
    except LipsyncMuxAudioDrift as _drift_exc:
        # Already captured to Sentry inside the assertion. Fail the render
        # loud — shipping out-of-sync lips is worse than a retryable failure.
        sentry_sdk.capture_exception(_drift_exc)
        logger.error("Render %s: %s", render_id, _drift_exc)
        await _update_render(
            render_id,
            status=CastRenderStatus.FAILED.value,
            error_message="AI render failed audio-sync validation — please retry.",
        )
        return

    # ── Pass 2: FFmpeg composition ──
    await _update_render(render_id,
        status=CastRenderStatus.COMPOSING.value,
        progress_percent=92,
        progress_step="Composing final video",
    )

    # Build the composition payload for the GPU worker.
    #
    # Overlay coordinates MUST be scaled to whatever canvas the compose pass
    # actually builds — and that canvas is ALWAYS timeline.compositionWidth/
    # Height (see worker_ffmpeg_compose._canvas_size and this file's own
    # _canvas_dims_for_render, used to normalize every baked block clip).
    # Neither of those ever downscales by "quality" — there is no code path
    # that renders the final canvas at a lower resolution than the editor's.
    # This used to derive render_width/height from a 480p/720p/1080p quality
    # lookup instead, which for a "hd" render silently produced a 720x1280
    # target while the real canvas stayed 1080x1920 (sx=sy=0.667) — every
    # full-canvas overlay (B-roll video/stock image) came out scaled to only
    # 2/3 width, leaving a gap on the far side that let the underlying baked
    # clip show through behind it. Confirmed by extracting an actual frame
    # from rnd_b559ae23ee62: the same stock photo appeared twice — once
    # correctly scaled on the left ~2/3, and again (the baked B-roll clip,
    # visible through the gap) cropped differently on the right ~1/3.
    cw_for_overlays, ch_for_overlays, _ = _canvas_dims_for_render(timeline)
    overlay_elements = extract_overlay_elements(
        timeline, render_width=cw_for_overlays, render_height=ch_for_overlays,
    )
    logger.info("Render %s: %d overlay elements extracted from timeline", render_id, len(overlay_elements))

    # NOTE: Captions are authored by the frontend (editorStarterMapping.ts) and live
    # inside the timeline snapshot as track_type=captions elements. We MUST NOT also
    # load them from variants here — doing so produces two overlapping caption layers
    # with different word-chunking (5-word vs 3-word) and different wording. The
    # frontend is the single source of truth for captions; if no captions appear in
    # the final render, the bug is in the frontend mapping, not here.
    # (Previously: _load_caption_overlays(cast_id, timeline, ...) was called here and
    #  its output was appended — that caused the double-caption bug.)

    # Slot-aligned compose inputs (PR #80): the GPU compose worker now lays
    # each baked clip onto a black canvas at its absolute timeline slot, and
    # mixes per-block audio at the same slot start. Without these tracks the
    # worker concatenated clips end-to-end, accumulating drift whenever a
    # bake's duration disagreed with its slot — and ignored voiceover-only
    # blocks entirely on the video side, so the base video ran short of the
    # final audio (frozen-tail bug). ``baked_urls`` is still sent so older
    # compose deployments stay functional during the rollout.
    # An image URL (face_ref.jpg, .png, ...) must never end up on the video
    # side of compose: the slot-aligned graph would feed it to the overlay
    # filter, and any audio chain referencing [N:a] for that input fails
    # with "matches no streams". Voiceover-only blocks contribute audio
    # only — the canvas remains visible until the next clip's slot.
    _IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

    def _is_image_url(url: str) -> bool:
        if not url:
            return False
        return url.split("?", 1)[0].lower().endswith(_IMAGE_EXTS)

    # Filter baked_urls in-place: if anything image-shaped slipped in
    # (legacy substitution for voiceover-only blocks), drop it now so it
    # neither hits the legacy compose path nor leaks into the slot-aligned
    # video track list.
    _dropped_image_urls = [
        _eid for _eid, _u in list(baked_urls.items()) if _is_image_url(_u)
    ]
    for _eid in _dropped_image_urls:
        logger.warning(
            "Render %s: dropping image URL from baked_urls for element %s (%s)",
            render_id, _eid, baked_urls[_eid][:80],
        )
        baked_urls.pop(_eid, None)

    compose_video_tracks: list[dict] = []
    compose_audio_tracks: list[dict] = []
    for _track in (timeline or {}).get("tracks") or []:
        for _el in (_track or {}).get("elements") or []:
            _meta = _el.get("metadata") or {}
            if not _meta.get("bonded"):
                continue
            _block_id = _meta.get("block_id") or ""
            try:
                _s = float(_el.get("s") or 0)
                _e = float(_el.get("e") or 0)
            except (TypeError, ValueError) as _track_exc:
                sentry_sdk.capture_exception(_track_exc)
                continue
            if _e <= _s:
                continue
            _eid = _el.get("id") or ""
            if _meta.get("paired_audio_element_id") and _eid in baked_urls:
                _vurl = baked_urls[_eid]
                if _is_image_url(_vurl):
                    logger.warning(
                        "Render %s: skipping image URL on V1 track for block=%s (%s)",
                        render_id, _block_id, _vurl[:80],
                    )
                    continue
                compose_video_tracks.append({
                    "url": _vurl,
                    "s": _s, "e": _e,
                    "block_id": _block_id,
                    # Carry the block's PIP layout so the compose worker knows
                    # to shrink this clip into a corner window (it otherwise
                    # renders every baked clip full-frame).
                    "pip_layout": _meta.get("pip_layout") or "fullscreen",
                })
            elif _meta.get("paired_video_element_id"):
                _src = (_el.get("props") or {}).get("src") or ""
                if _src and not _is_image_url(_src):
                    compose_audio_tracks.append({
                        "url": _src,
                        "s": _s, "e": _e,
                        "block_id": _block_id,
                    })

    # ── PIP / talking-head geometry for the compose worker ──────────────────
    # worker_ffmpeg_compose has no PIP support — it lays every baked clip
    # full-frame and then paints b-roll on top, which hides the talking head.
    # Resolve each PIP block's corner-window rect here (canvas pixels, the
    # same space the worker composes in) and pair it with its full-frame
    # b-roll so the worker builds face-OVER-background instead.
    try:
        from services.timeline_builder import pip_geometry, is_pip_layout

        _bg_by_block: dict[str, dict] = {}
        for _ov in overlay_elements:
            if _ov.get("type") != "video":
                continue
            _bid = _ov.get("block_id")
            if not _bid or _bid in _bg_by_block:
                continue
            _ow = _ov.get("width") or cw_for_overlays
            _oh = _ov.get("height") or ch_for_overlays
            _ox = _ov.get("x") or 0
            _oy = _ov.get("y") or 0
            if (
                _ow >= cw_for_overlays * 0.95
                and _oh >= ch_for_overlays * 0.95
                and _ox <= cw_for_overlays * 0.05
                and _oy <= ch_for_overlays * 0.05
            ):
                _bg_by_block[_bid] = _ov

        for _vt in compose_video_tracks:
            _pl = _vt.get("pip_layout") or "fullscreen"
            if not is_pip_layout(_pl):
                continue
            _g = pip_geometry(_pl, cw_for_overlays, ch_for_overlays)
            if not _g.get("visible") or _g.get("w", 0) <= 0 or _g.get("h", 0) <= 0:
                continue
            _vt["pip"] = {
                "x": int(_g["x"]), "y": int(_g["y"]),
                "w": int(_g["w"]), "h": int(_g["h"]),
                "layout": _g["pip_layout"],
            }
            _bg = _bg_by_block.get(_vt.get("block_id") or "")
            if _bg and _bg.get("src"):
                _vt["pip"]["bg_src"] = _bg["src"]
                # Worker drops this from the top overlay pass — it's now the
                # PIP background, baked in behind the face.
                _bg["_pip_bg"] = True
        _n_pip = sum(1 for _vt in compose_video_tracks if _vt.get("pip"))
        if _n_pip:
            logger.info(
                "Render %s: %d PIP block(s) resolved for compose "
                "(%d with a b-roll background)",
                render_id, _n_pip,
                sum(1 for _vt in compose_video_tracks if (_vt.get("pip") or {}).get("bg_src")),
            )
    except Exception as _pip_exc:
        sentry_sdk.capture_exception(_pip_exc)
        logger.warning("Render %s: PIP geometry resolve failed (%s)", render_id, _pip_exc)

    composition_payload = {
        "render_id": render_id,
        "cast_id": cast_id,
        "timeline": timeline,
        "baked_urls": baked_urls,
        "overlay_elements": overlay_elements,
        "compose_video_tracks": compose_video_tracks,
        "compose_audio_tracks": compose_audio_tracks,
        "cast_music_volume": cast_music_volume,
    }

    # Route the full-timeline compose.
    #
    # The compose used to run on the decommissioned HOSTKEY box. There are now
    # three possible targets, in priority order:
    #
    #   1. GPU_WORKER_URL set        -> HTTP POST to that cloud worker (lets us
    #      re-introduce a dedicated compose worker later with no code change).
    #   2. HOSTKEY disabled, no URL  -> run compose in-process here. The celery
    #      render worker already has ffmpeg, R2 creds and concurrency budget,
    #      so we call _run_ffmpeg_compose directly instead of POSTing to the
    #      dead HOSTKEY IP. This is the current production state.
    #   3. HOSTKEY enabled, no URL   -> legacy fallback to HOSTKEY_GPU_URL POST.
    from services.hostkey_flags import hostkey_disabled, log_hostkey_skip
    from collections import Counter

    cloud_worker_url = getattr(settings, "GPU_WORKER_URL", "") or ""

    overlay_type_counter = Counter(o["type"] for o in overlay_elements)
    # Per-track-type breakdown so we can answer "did the product layer / the
    # parallel media / the captions actually make it into this render?"
    # without having to dig the timeline_snapshot back out of the database.
    track_type_counter = Counter(o.get("track_type") or "untyped" for o in overlay_elements)
    logger.info(
        "Render %s dispatching: %d bonded blocks, %d overlays (types=%s, tracks=%s)",
        render_id,
        len(baked_urls),
        len(overlay_elements),
        dict(overlay_type_counter),
        dict(track_type_counter),
    )

    if cloud_worker_url:
        # Path 1: dedicated cloud compose worker.
        gpu_url = cloud_worker_url
        logger.info("Dispatching FFmpeg composition to GPU worker: %s", gpu_url)
        async with httpx.AsyncClient(timeout=600.0) as http:
            resp = await http.post(
                f"{gpu_url.rstrip('/')}/api/ffmpeg-compose",
                json=composition_payload,
            )
            resp.raise_for_status()
            compose_result = resp.json()
    elif hostkey_disabled():
        # Path 2: in-process compose. No cloud worker URL and HOSTKEY is dead,
        # so run the same compose routine the HOSTKEY worker used to call,
        # right here inside the celery render worker.
        log_hostkey_skip("in-process ffmpeg-compose")
        logger.info(
            "Running FFmpeg composition in-process (no GPU_WORKER_URL "
            "configured, HOSTKEY disabled)"
        )
        import types as _types
        from worker_ffmpeg_compose import _run_ffmpeg_compose
        # _run_ffmpeg_compose reads its request via attribute access (it was
        # written for a FastAPI pydantic model); wrap the payload so dotted
        # access works. Optional fields are read with getattr defaults there.
        compose_req = _types.SimpleNamespace(**composition_payload)
        try:
            # ffmpeg + R2 upload is blocking; run off the event loop.
            compose_result = await asyncio.to_thread(_run_ffmpeg_compose, compose_req)
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise
    else:
        # Path 3: legacy HOSTKEY HTTP fallback (only when HOSTKEY re-enabled).
        gpu_url = getattr(settings, "HOSTKEY_GPU_URL", "")
        if not gpu_url:
            raise RuntimeError("GPU_WORKER_URL / HOSTKEY_GPU_URL not configured")
        logger.info("Dispatching FFmpeg composition to GPU worker: %s", gpu_url)
        async with httpx.AsyncClient(timeout=600.0) as http:
            resp = await http.post(
                f"{gpu_url.rstrip('/')}/api/ffmpeg-compose",
                json=composition_payload,
            )
            resp.raise_for_status()
            compose_result = resp.json()

    output_key = compose_result.get("output_r2_key", "")
    if not output_key:
        raise RuntimeError("FFmpeg composition returned no output_r2_key")

    # Defensive post-compose audio remux. The compose service has been
    # observed truncating final.mp4 by the difference between the sum
    # of bonded block durations and the timeline's last element ``e``;
    # this overwrites compose's output with one that lays each block's
    # audio at its absolute slot start and extends the video to the
    # full timeline duration. Wrapped to never fail the render — on
    # error we fall back to compose's original output.
    # Retried once: this reads compose's original output fresh each attempt
    # and only overwrites it on success, so a retry is safe/idempotent. A
    # single transient failure (network blip fetching a track, a flaky ffmpeg
    # invocation) used to permanently and silently drop background
    # music/SFX from an otherwise-successful render with no visible error.
    # Scene ambience beds (SCENE_AMBIENCE_ENABLED) — derived from each block's
    # room/outdoor scene environment, mixed as a ducked bed inside the remux.
    # Best-effort: a failure here just means no bed, never a failed render.
    try:
        ambience_plan = await _build_ambience_plan(
            timeline=timeline, cast_id=cast_id, factory=factory, render_id=render_id,
        )
    except Exception as _amb_plan_exc:
        sentry_sdk.capture_exception(_amb_plan_exc)
        ambience_plan = []

    remux_error: Exception | None = None
    for attempt in range(2):
        try:
            await _post_compose_audio_remux(
                r2=r2,
                output_key=output_key,
                timeline=timeline,
                render_id=render_id,
                cast_music_volume=cast_music_volume,
                ambience_plan=ambience_plan,
            )
            remux_error = None
            break
        except Exception as exc:
            remux_error = exc
            if attempt == 0:
                logger.warning(
                    "Render %s post-compose remux failed (%s); retrying once",
                    render_id, exc,
                )
    if remux_error is not None:
        sentry_sdk.set_tag("remux_failure", "music_sfx_missing")
        sentry_sdk.capture_exception(remux_error)
        logger.error(
            "Render %s post-compose remux failed twice (%s); shipping compose "
            "output WITHOUT background music/SFX",
            render_id, remux_error,
        )

    # Defensive post-compose product-overlay composite. The finalize
    # pipeline never burns the timeline's product-card image overlays onto
    # the final mp4 — the product-conditioned avatar bake gate only fires
    # for speaking blocks, so body_motion / pip / voiceover render modes
    # ship without the card. This reads the image overlays off the timeline
    # snapshot and composites them onto compose's output. Wrapped to never
    # fail the render — on error we fall back to compose's (remuxed) output.
    #
    #   ffmpeg compose → audio remux → [product overlay composite] →
    #   thumbnail → upload final
    try:
        from models.cast import Cast as _CastUpd
        async with factory() as session:
            _overlay_cast = await session.get(_CastUpd, cast_id)
        await _post_compose_product_overlays(
            r2=r2,
            output_key=output_key,
            timeline=timeline,
            render_id=render_id,
            cast=_overlay_cast,
        )
    except Exception as overlay_exc:
        sentry_sdk.capture_exception(overlay_exc)
        logger.warning(
            "Render %s post-compose product overlay failed (%s); "
            "leaving compose output as-is",
            render_id, overlay_exc,
        )

    # Generate poster thumbnail before marking ready
    thumb_key = None
    try:
        from services.thumbnail_service import generate_and_upload_thumbnail
        thumb_key = await generate_and_upload_thumbnail(
            output_key,
            render_id,
            duration_seconds=None,  # Will use 10% of default
        )
    except Exception as thumb_err:
        sentry_sdk.capture_exception(thumb_err)
        logger.warning("Thumbnail generation failed for render %s: %s", render_id, thumb_err)

    update_kwargs = dict(
        status=CastRenderStatus.READY.value,
        output_video_r2_key=output_key,
        completed_at=datetime.now(timezone.utc),
        progress_percent=100,
        progress_step="Complete",
    )
    if thumb_key:
        update_kwargs["thumbnail_key"] = thumb_key

    await _update_render(render_id, **update_kwargs)

    # PR #68: best-effort reset of the per-render HOSTKEY recovery
    # counters so we don't carry stale skip state across runs.
    try:
        from services.render_providers import reset_hostkey_recovery_state
        reset_hostkey_recovery_state(render_id)
    except Exception as _reset_exc:
        sentry_sdk.capture_exception(_reset_exc)

    # Update the cast with the final video URL
    production_level = "standard"
    render_quality = "simple"
    async with factory() as session:
        from models.cast import Cast, CastStatus
        from models.cast_render import CastRender
        cast = await session.get(Cast, cast_id)
        if cast:
            cast.final_video_url = r2.get_public_url(output_key)
            cast.status = CastStatus.READY
            production_level = cast.production_level or "standard"
            # Snapshot at render-creation time (CastRender.quality), not the
            # Cast row's live quality — avoids billing the wrong tier if the
            # user edits quality while this render is still in flight.
            render_row = await session.get(CastRender, render_id)
            render_quality = (
                (render_row.quality if render_row else None)
                or (cast.quality.value if cast.quality else None)
                or "simple"
            )
            await session.commit()

    # Billing: meter this render (included allowance -> PAYG credits ->
    # overage). Idempotent on render_id — safe if a Celery redelivery
    # reaches this point twice (see the terminal-state guard above).
    try:
        from services import billing_service
        final_duration_s = await _probe_audio_duration_s(r2.get_public_url(output_key))
        async with factory() as _bill_session:
            await billing_service.deduct_render_usage(
                _bill_session,
                user_id=user_id,
                owner_id=user_id,
                render_id=render_id,
                cast_id=cast_id,
                duration_seconds=final_duration_s,
                production_level=production_level,
                quality=render_quality,
            )
    except Exception as bill_exc:
        sentry_sdk.capture_exception(bill_exc)
        logger.error("Render %s: billing metering failed: %s", render_id, bill_exc)

    # Log FFmpeg composition cost
    await _log_generation_cost(
        user_id=user_id,
        cast_id=cast_id,
        engine="ffmpeg",
        operation="cast_compose",
        quantity=1,
        cost_usd=0.0,  # FFmpeg on own GPU — no external cost
        metadata_json={"render_id": render_id, "output_key": output_key},
    )

    logger.info("Render %s complete → %s", render_id, output_key)


async def _get_render_attempt(render_id: str) -> int:
    from models.cast_render import CastRender
    factory = _make_session_factory()
    async with factory() as session:
        render = await session.get(CastRender, render_id)
        return render.render_attempt if render else 0


async def _log_generation_cost(
    user_id: str,
    cast_id: str,
    engine: str,
    operation: str,
    quantity: int = 1,
    cost_usd: float = 0.0,
    block_id: str | None = None,
    metadata_json: dict | None = None,
):
    from models.generation_cost import GenerationCost
    factory = _make_session_factory()
    async with factory() as session:
        cost = GenerationCost(
            id=f"gc_{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            cast_id=cast_id,
            block_id=block_id,
            engine=engine,
            operation=operation,
            cost_usd=cost_usd,
            quantity=quantity,
            metadata_json=metadata_json,
        )
        session.add(cost)
        await session.commit()


# Map dispatcher backend strings → provider for the unified usage tracker.
# Backends like "hostkey_t2v" / "fal_t2v" share the same rate basis as their
# non-T2V siblings — only event_type differs. Every backend string that can
# reach _log_render_usage MUST appear here; an unmapped backend is now billed
# as provider="unknown" and reported to Sentry (§5.4) rather than silently
# defaulting to "hostkey" (which on rnd_e8304a5dd7bf mis-billed live
# WaveSpeed bakes as on-prem GPU seconds).
#
# Source of the backend strings (grep'd, see PR description):
#   render_dispatcher.py: musetalk, hostkey, modal, runpod, hostkey_t2v,
#                         fal_t2v, fal_i2v, fal_hallo, wavespeed
#   render_providers.py:  fal_sync_lipsync_v2_pro
#   cast_render.py:       fal_sync_lipsync_v3, product_elements_bake
#                         (_PROVIDER_TO_BACKEND) and the "fal_i2v" product-bake
#                         cost fallback.
_BACKEND_TO_PROVIDER = {
    "hostkey":     "hostkey",
    "musetalk":    "hostkey",   # MuseTalk runs on the HOSTKEY box
    "modal":       "modal",
    "runpod":      "runpod",
    "hostkey_t2v": "hostkey",
    "fal_t2v":     "fal_ai",
    "fal_i2v":     "fal_ai",
    "fal_hallo":   "fal_ai",
    "wavespeed":   "wavespeed",
    "product_elements_bake": "fal_ai",  # Kling v3 Pro Elements via fal.ai
    # PIP fallback lipsync (looped face still → fal sync-lipsync v2/pro/v3).
    "fal_sync_lipsync_v2_pro": "fal_ai",
    "fal_sync_lipsync_v3":     "fal_ai",
}

# Map backend → quantity_unit. fal/WaveSpeed/Kling video bakes are billed in
# video_seconds (provider charges per output second); on-prem / serverless GPU
# backends are billed in gpu_seconds (wall-clock GPU time). §5.4: on
# rnd_e8304a5dd7bf WaveSpeed bakes were recorded as gpu_seconds because the
# backend tag fell through to the "hostkey" default. Keep this in lock-step
# with _BACKEND_TO_PROVIDER — every backend listed there appears here.
_BACKEND_TO_UNIT = {
    "hostkey":     "gpu_seconds",
    "musetalk":    "gpu_seconds",
    "modal":       "gpu_seconds",
    "runpod":      "gpu_seconds",
    "hostkey_t2v": "gpu_seconds",
    "fal_t2v":     "video_seconds",
    "fal_i2v":     "video_seconds",
    "fal_hallo":   "video_seconds",
    "wavespeed":   "video_seconds",
    "product_elements_bake": "video_seconds",
    "fal_sync_lipsync_v2_pro": "video_seconds",
    "fal_sync_lipsync_v3":     "video_seconds",
}

# §5.4: when true (default) an unmapped backend fails LOUD — billed as
# provider="unknown" / unit derived from the fal-vs-gpu branch, and a Sentry
# warning is emitted so the misattribution is caught instead of silently
# socialised onto "hostkey". Set false for emergency rollback to the old
# silent-default-"hostkey" behaviour.
_USAGE_LOG_STRICT_BACKEND = (
    os.environ.get("USAGE_LOG_STRICT_BACKEND", "true").strip().lower()
    not in ("0", "false", "no", "off")
)

# §5.4: bill motion bakes at the REQUESTED provider tier (the overshot,
# snapped duration the provider was actually asked for and charges for), not
# the timeline slot. On render rnd_7923dde6c01f the UsageEvent ``quantity``
# logged the slot (e.g. 9.3s) for a fal motion bake, under-counting the cost
# of the overshoot. When true and a ``requested_tier_s`` is available the
# emission site bills that instead of the slot-derived video_seconds.
_USAGE_BILL_REQUESTED_TIER = (
    os.environ.get("USAGE_BILL_REQUESTED_TIER", "true").strip().lower()
    not in ("0", "false", "no", "off")
)


def _resolve_billed_video_seconds(
    fal_video_seconds: float | None,
    requested_tier_s: float | None,
) -> float:
    """§5.4: choose the seconds to bill a fal video bake on.

    Returns the requested provider tier when billing-by-tier is enabled and a
    positive tier is known; otherwise the slot-derived ``fal_video_seconds``.
    Pure so the decision is unit-testable without a DB session.
    """
    slot_seconds = float(fal_video_seconds or 0.0)
    if (
        _USAGE_BILL_REQUESTED_TIER
        and requested_tier_s is not None
        and float(requested_tier_s) > 0
    ):
        return float(requested_tier_s)
    return slot_seconds


async def _log_render_usage(
    *,
    user_id: str,
    cast_id: str,
    block_id: str | None,
    render_id: str | None,
    backend: str,
    event_type: str,
    elapsed_seconds: float,
    provider_job_id: str | None = None,
    output_video_seconds: float | None = None,
    fal_video_seconds: float | None = None,
    fal_model: str | None = None,
    requested_tier_s: float | None = None,
):
    """Best-effort log_usage for one rendered block.

    `backend` is the dispatcher's return tag ("hostkey", "modal", "runpod",
    "musetalk", "hostkey_t2v", "fal_t2v"). This function picks the right
    cost calculator (GPU per-second vs video-second) and writes a row
    via the unified usage tracker. Failures swallow to Sentry — render
    success must not depend on cost tracking working.

    PR-I: every backend whose ``_BACKEND_TO_UNIT`` is "video_seconds" (fal_t2v,
    fal_i2v, fal_hallo, fal_sync_lipsync_v2_pro, fal_sync_lipsync_v3,
    product_elements_bake, wavespeed) is billed on the OUTPUT clip length and
    priced via ``calculate_video_second_cost``. Before PR-I only fal_t2v/fal_i2v
    took that path; the rest fell through to ``calculate_gpu_render_cost`` which
    returned $0.00 for provider="fal_ai"/"wavespeed" and recorded wall-clock
    seconds under a "video_seconds" label. ``output_video_seconds`` carries the
    slot duration; ``fal_video_seconds`` is the deprecated alias kept for
    backwards compatibility with older call sites.

    §5.4: when ``requested_tier_s`` is supplied and ``USAGE_BILL_REQUESTED_TIER``
    is enabled, video bakes are billed at the requested provider tier (the
    overshot, snapped duration the provider actually charges for), not the
    slot-derived output seconds. This corrects the rnd_7923dde6c01f under-count
    where the slot (9.3s) was billed instead of the requested tier.
    """
    # PR-I: prefer the new ``output_video_seconds`` kwarg; fall back to the
    # deprecated ``fal_video_seconds`` alias for call sites not yet migrated.
    if output_video_seconds is None:
        output_video_seconds = fal_video_seconds
    try:
        from database import async_session_factory
        from services.usage_tracker import (
            calculate_gpu_render_cost,
            calculate_video_second_cost,
            log_usage,
        )

        # §5.4: resolve provider + quantity_unit from the backend maps, failing
        # LOUD on an unmapped backend instead of silently socialising the cost
        # onto "hostkey"/"gpu_seconds". On rnd_e8304a5dd7bf live WaveSpeed bakes
        # were billed as provider="hostkey" quantity_unit="gpu_seconds" because
        # the old ``.get(backend, "hostkey")`` default masked an unmapped tag.
        # With USAGE_LOG_STRICT_BACKEND=false the function restores the legacy
        # silent-default behaviour for emergency rollback.
        provider = _BACKEND_TO_PROVIDER.get(backend)
        if provider is None:
            if _USAGE_LOG_STRICT_BACKEND:
                logger.error(
                    "usage_event: unmapped backend=%r block=%s render=%s — "
                    "billing as 'unknown'",
                    backend, block_id, render_id,
                )
                sentry_sdk.capture_message(
                    f"usage_event unmapped backend: {backend}", level="warning"
                )
                provider = "unknown"
            else:
                provider = "hostkey"

        mapped_unit = _BACKEND_TO_UNIT.get(backend)
        if mapped_unit is None:
            if _USAGE_LOG_STRICT_BACKEND:
                logger.error(
                    "usage_event: unmapped backend=%r block=%s render=%s — "
                    "quantity_unit defaulting to 'gpu_seconds'",
                    backend, block_id, render_id,
                )
                sentry_sdk.capture_message(
                    f"usage_event unmapped backend (unit): {backend}",
                    level="warning",
                )
            mapped_unit = "gpu_seconds"

        # PR-I: ALL video-second backends (not just fal_t2v/fal_i2v) bill on the
        # OUTPUT clip length and price via calculate_video_second_cost. The §5.4
        # requested-tier billing applies here too. Before PR-I fal_hallo /
        # fal_sync_lipsync / product_elements_bake / wavespeed fell into the
        # else branch and recorded wall-clock elapsed_seconds at $0.00.
        if mapped_unit == "video_seconds":
            billed_seconds = _resolve_billed_video_seconds(
                output_video_seconds, requested_tier_s
            )
            if billed_seconds != float(output_video_seconds or 0.0):
                logger.info(
                    "usage: billing requested-tier seconds block=%s "
                    "tier=%.3fs (slot_video_seconds=%.3fs) backend=%s",
                    block_id, billed_seconds, float(output_video_seconds or 0.0),
                    backend,
                )
            # fal_t2v/fal_i2v carry a model for rate selection (defaulting to
            # the older Wan rate); the lip-sync / element / wavespeed backends
            # are priced per-backend by the helper and have no model.
            provider_model = (
                (fal_model or "wan-2.2") if backend in ("fal_t2v", "fal_i2v") else None
            )
            cost = calculate_video_second_cost(
                backend, provider_model, billed_seconds
            )
            quantity = billed_seconds
            quantity_unit = "video_seconds"
        else:
            cost = calculate_gpu_render_cost(provider, float(elapsed_seconds or 0.0))
            quantity = float(elapsed_seconds or 0.0)
            quantity_unit = mapped_unit  # "gpu_seconds"
            provider_model = None

        async with async_session_factory() as session:
            await log_usage(
                session,
                user_id=user_id,
                event_type=event_type,
                provider=provider,
                provider_cost_usd=cost,
                quantity=quantity,
                quantity_unit=quantity_unit,
                resource_type="cast",
                resource_id=cast_id,
                render_id=render_id,
                block_id=block_id,
                duration_seconds=float(elapsed_seconds or 0.0),
                provider_job_id=provider_job_id,
                provider_model=provider_model,
            )
            await session.commit()
    except Exception as exc:
        sentry_sdk.capture_exception(exc)

"""Model playground — admin-only.

Fan one prompt (and/or one image) out to every image/video model the app can
call, so we can eyeball what each actually produces before wiring it into the
render pipeline. Nothing here touches casts, avatars, or usage billing — it's
a bench, not a product surface.

Flow (all via fal's queue REST API, the same pattern render_providers uses):
  POST /playground/upload           multipart image -> {url}
  GET  /playground/models           the registry (no callables)
  POST /playground/run              {model_id, prompt?, image_url?}
                                    -> {status_url, response_url, est_cost_usd}
  POST /playground/poll             {status_url, response_url}
                                    -> {state: queued|running|done|error, url?, error?}
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Optional

import httpx
import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel

from config import settings
from models.user import User
from routers.auth import require_admin
from services.r2_storage import get_r2_storage_service

router = APIRouter(prefix="/api/playground", tags=["playground"])

_QUEUE_BASE = "https://queue.fal.run"


def _fal_key() -> str:
    key = os.environ.get("FAL_KEY") or os.environ.get("FAL_API_KEY") or getattr(settings, "FAL_API_KEY", "")
    if not key:
        raise HTTPException(503, "FAL_API_KEY not configured")
    return key


def _auth() -> dict:
    return {"Authorization": f"Key {_fal_key()}", "Content-Type": "application/json"}


# ── Model registry ───────────────────────────────────────────────────────────
# input: "text" | "image" | "text+image"  (image = a source image is required)
# build(prompt, image_url) -> fal arguments dict
def _img_flux_kontext(p, img):
    return {"prompt": p, "image_url": img, "guidance_scale": 3.5,
            "num_inference_steps": 28, "output_format": "jpeg"}


def _img_flux_kontext_max(p, img):
    return {"prompt": p, "image_url": img, "num_images": 1,
            "output_format": "jpeg", "safety_tolerance": "5", "aspect_ratio": "9:16"}


def _img_flux_dev(p, img):
    return {"prompt": p, "image_size": "portrait_16_9", "num_inference_steps": 28,
            "num_images": 1, "output_format": "jpeg"}


def _img_nbp(p, img):
    # NBP takes optional reference images. When one is uploaded, pass it —
    # Gemini refuses far less when it has a concrete reference than on a bare
    # text prompt (esp. brand / product / "promo" prompts).
    a = {"prompt": p, "aspect_ratio": "9:16", "resolution": "2K", "output_format": "jpeg"}
    if img:
        a["image_urls"] = [img]
    return a


def _img_nbp_edit(p, img):
    return {"prompt": p, "image_urls": [img], "aspect_ratio": "9:16",
            "resolution": "2K", "output_format": "jpeg"}


def _img_qwen_angles(p, img):
    return {"image_urls": [img], "horizontal_angle": 45, "vertical_angle": 0,
            "num_inference_steps": 40, "guidance_scale": 5.0, "output_format": "jpeg"}


def _vid_kling_t2v(p, img):
    return {"prompt": p, "duration": "5", "aspect_ratio": "9:16",
            "negative_prompt": "blur, distortion, watermark, text"}


def _vid_kling_i2v(p, img):
    return {"prompt": p, "image_url": img, "duration": "5"}


# talking-head builders take (prompt, image_url, audio_url)
def _th_hallo(p, img, aud):
    return {"source_image_url": img, "audio_url": aud}


def _th_infinitetalk(p, img, aud):
    a = {"image_url": img, "audio_url": aud}
    if p:
        a["prompt"] = p
    return a


def _th_sadtalker(p, img, aud):
    return {"source_image_url": img, "driven_audio_url": aud}


def _vid_veo(p, img):
    # generate_audio defaults to true on fal.ai's Veo 3.1 endpoints and costs
    # extra ($0.40/s vs $0.20/s flagship, $0.15/s vs $0.10/s fast, at
    # 720p/1080p) — the app always replaces it with its own TTS voiceover,
    # so Veo's own audio track is never actually used. Disabled to avoid
    # paying for audio that gets discarded.
    a = {"prompt": p, "aspect_ratio": "9:16", "duration": "8s", "generate_audio": False}
    if img:
        a["image_url"] = img
    return a


_MODELS: list[dict] = [
    # ── images ──
    {"id": "flux_dev", "label": "FLUX.1 [dev]", "provider": "Black Forest Labs",
     "kind": "image", "input": "text", "endpoint": "fal-ai/flux/dev",
     "est_cost_usd": 0.025, "build": _img_flux_dev, "note": "Text-to-image baseline."},
    {"id": "flux_kontext", "label": "FLUX Pro Kontext", "provider": "Black Forest Labs",
     "kind": "image", "input": "text+image", "endpoint": "fal-ai/flux-pro/kontext",
     "est_cost_usd": 0.04, "build": _img_flux_kontext, "note": "Edit a source image from a prompt (app's product-scene path)."},
    {"id": "flux_kontext_max", "label": "FLUX Pro Kontext Max", "provider": "Black Forest Labs",
     "kind": "image", "input": "text+image", "endpoint": "fal-ai/flux-pro/kontext/max",
     "est_cost_usd": 0.10, "build": _img_flux_kontext_max, "note": "Higher-fidelity Kontext (body-shot canonical path)."},
    {"id": "nano_banana_pro", "label": "Nano Banana Pro (Gemini 3 Pro Image)", "provider": "Google",
     "kind": "image", "input": "text", "endpoint": "fal-ai/nano-banana-pro",
     "est_cost_usd": 0.15, "build": _img_nbp, "note": "Text-to-image."},
    {"id": "nano_banana_pro_edit", "label": "Nano Banana Pro — Edit", "provider": "Google",
     "kind": "image", "input": "text+image", "endpoint": "fal-ai/nano-banana-pro/edit",
     "est_cost_usd": 0.15, "build": _img_nbp_edit, "note": "Composites a real reference object (product-in-hand path)."},
    {"id": "qwen_angles", "label": "Qwen Image Edit — Multiple Angles", "provider": "Alibaba",
     "kind": "image", "input": "image", "endpoint": "fal-ai/qwen-image-edit-2511-multiple-angles",
     "est_cost_usd": 0.03, "build": _img_qwen_angles, "note": "Rotates a subject to a numeric angle (body shots). Uses a fixed 45° here."},
    # ── video ──
    {"id": "kling_25_t2v", "label": "Kling 2.5 Turbo Pro — Text→Video", "provider": "Kuaishou",
     "kind": "video", "input": "text", "endpoint": "fal-ai/kling-video/v2.5-turbo/pro/text-to-video",
     "est_cost_usd": 0.35, "build": _vid_kling_t2v, "note": "Current scene b-roll model."},
    {"id": "kling_21_t2v", "label": "Kling 2.1 Master — Text→Video", "provider": "Kuaishou",
     "kind": "video", "input": "text", "endpoint": "fal-ai/kling-video/v2.1/master/text-to-video",
     # Verified 2026-09 against fal.ai's own model page: $1.40 for 5s
     # ($0.28/s) — was showing 0.35 (4x under). See services/cost_rates.py.
     "est_cost_usd": 1.40, "build": _vid_kling_t2v, "note": "Previous scene b-roll model."},
    {"id": "kling_15_i2v", "label": "Kling 1.5 Pro — Image→Video", "provider": "Kuaishou",
     "kind": "video", "input": "text+image", "endpoint": "fal-ai/kling-video/v1.5/pro/image-to-video",
     # Verified 2026-09: $0.50 for 5s ($0.10/s) — was showing 0.25 (2x under).
     "est_cost_usd": 0.50, "build": _vid_kling_i2v, "note": "Current product b-roll model (animates the product photo)."},
    {"id": "kling_16_i2v", "label": "Kling 1.6 Standard — Image→Video", "provider": "Kuaishou",
     "kind": "video", "input": "text+image", "endpoint": "fal-ai/kling-video/v1.6/standard/image-to-video",
     # Verified 2026-09: $0.28 for 5s ($0.056/s) — was showing 0.20 (under).
     "est_cost_usd": 0.28, "build": _vid_kling_i2v, "note": "Cheaper image→video tier."},
    {"id": "veo3_fast", "label": "Google Veo 3.1 — Fast", "provider": "Google",
     # fal-ai/veo3/fast is deprecated ("no longer supported") — migrated to
     # 3.1/fast 2026-09; live access confirmed via a real subscribe call.
     # image_optional dropped: veo3.1's base endpoint schema has no
     # image_url field (image-to-video is fal-ai/veo3.1/fast/image-to-video,
     # a separate endpoint, unlike the old veo3's single-endpoint shape).
     "kind": "video", "input": "text", "endpoint": "fal-ai/veo3.1/fast",
     # Verified 2026-09 directly against fal.ai's veo3.1/fast page: $0.10/s
     # with audio off at 720p/1080p — 8s clip = $0.80.
     "est_cost_usd": 0.80, "build": _vid_veo, "note": "8s, 9:16, audio disabled (unused — cast's own TTS replaces it)."},
    {"id": "veo3", "label": "Google Veo 3.1", "provider": "Google",
     "kind": "video", "input": "text", "endpoint": "fal-ai/veo3.1",
     # Verified 2026-09 directly against fal.ai's veo3.1 page: $0.20/s with
     # audio off at 720p/1080p — 8s clip = $1.60. See
     # services/cost_rates.py's fal/veo_3 entry.
     "est_cost_usd": 1.60, "build": _vid_veo, "note": "Flagship. 8s, 9:16, audio disabled (unused)."},
    # ── talking head (image + audio -> lip-synced video) ──
    {"id": "hallo", "label": "Hallo — portrait lip-sync", "provider": "fal",
     "kind": "talking_head", "input": "image+audio", "endpoint": "fal-ai/hallo",
     "est_cost_usd": 0.15, "build": _th_hallo, "note": "The app's tier-3 talking-head fallback. Portrait only, minimal body motion."},
    {"id": "infinitetalk", "label": "InfiniTalk (Wan)", "provider": "fal",
     "kind": "talking_head", "input": "image+audio", "endpoint": "fal-ai/infinitalk",
     "est_cost_usd": 0.50, "build": _th_infinitetalk, "note": "Same family as the app's primary talking-head bake (runs on WaveSpeed there). Upper-body motion."},
    {"id": "sadtalker", "label": "SadTalker", "provider": "fal",
     "kind": "talking_head", "input": "image+audio", "endpoint": "fal-ai/sadtalker",
     "est_cost_usd": 0.10, "build": _th_sadtalker, "note": "Classic, cheap, fast. Lower fidelity — a quality floor reference."},
]

_BY_ID = {m["id"]: m for m in _MODELS}


def _first_url(body) -> Optional[str]:
    """Pull the output URL out of whatever shape a fal model returns."""
    if not isinstance(body, dict):
        return None
    for key in ("video", "image", "audio"):
        v = body.get(key)
        if isinstance(v, dict) and v.get("url"):
            return v["url"]
        if isinstance(v, str) and v.startswith("http"):
            return v
    for key in ("images", "videos"):
        arr = body.get(key)
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict) and first.get("url"):
                return first["url"]
            if isinstance(first, str) and first.startswith("http"):
                return first
    if body.get("url", "").startswith("http"):
        return body["url"]
    return None


# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("/models")
async def list_models(user: User = Depends(require_admin)):
    return {
        "models": [
            {
                **{k: m[k] for k in ("id", "label", "provider", "kind", "input", "endpoint", "est_cost_usd", "note")},
                "image_optional": bool(m.get("image_optional")),
            }
            for m in _MODELS
        ]
    }


_CT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}


@router.post("/upload")
async def upload_asset(
    file: UploadFile = File(...),
    kind: str = "image",
    user: User = Depends(require_admin),
):
    """Upload an image or audio clip for a playground run. ?kind=image|audio."""
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "File too large (25 MB max)")
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = (".mp3", ".wav", ".m4a", ".aac", ".ogg") if kind == "audio" else (".jpg", ".jpeg", ".png", ".webp")
    if ext not in allowed:
        ext = ".mp3" if kind == "audio" else ".jpg"
    key = f"playground/{user.id}/{uuid.uuid4().hex[:16]}{ext}"
    r2 = get_r2_storage_service()
    await r2.upload_bytes(data, key, _CT.get(ext, "application/octet-stream"))
    return {"url": r2.get_public_url(key)}


class RunRequest(BaseModel):
    model_id: str
    prompt: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None


@router.post("/run")
async def run_model(req: RunRequest, user: User = Depends(require_admin)):
    m = _BY_ID.get(req.model_id)
    if not m:
        raise HTTPException(404, f"Unknown model {req.model_id}")
    prompt = (req.prompt or "").strip()
    img = (req.image_url or "").strip() or None
    aud = (req.audio_url or "").strip() or None
    if m["input"] == "image+audio":
        if not img or not aud:
            raise HTTPException(400, "This model needs a face image AND an audio clip.")
    elif m["input"] in ("text", "text+image") and not prompt:
        raise HTTPException(400, "This model needs a prompt.")
    # An image is required for: input="image", and input="text+image" on
    # image models (they EDIT a source) or Kling image-to-video. It's
    # optional only for Veo (used as a first frame).
    image_required = m["input"] == "image" or (
        m["input"] == "text+image" and not m.get("image_optional")
    )
    if image_required and not img:
        raise HTTPException(400, "This model needs a source image.")

    args = m["build"](prompt, img, aud) if m["input"] == "image+audio" else m["build"](prompt, img)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{_QUEUE_BASE}/{m['endpoint']}", headers=_auth(), json=args)
        if r.status_code >= 400:
            raise HTTPException(502, f"fal submit {r.status_code}: {(r.text or '')[:400]}")
        body = r.json()
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(502, f"fal submit failed: {str(e)[:300]}")

    status_url = body.get("status_url")
    response_url = body.get("response_url")
    if not status_url or not response_url:
        raise HTTPException(502, f"fal submit missing urls: {str(body)[:300]}")
    return {
        "model_id": m["id"],
        "kind": m["kind"],
        "status_url": status_url,
        "response_url": response_url,
        "est_cost_usd": m["est_cost_usd"],
        "submitted_at": time.time(),
    }


class PollRequest(BaseModel):
    status_url: str
    response_url: str


@router.post("/poll")
async def poll_model(req: PollRequest, user: User = Depends(require_admin)):
    # Only proxy fal's own queue hosts.
    for u in (req.status_url, req.response_url):
        if not u.startswith("https://queue.fal.run/"):
            raise HTTPException(400, "bad url")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            s = await c.get(req.status_url, headers=_auth())
            if s.status_code >= 400:
                return {"state": "error", "error": f"status {s.status_code}: {(s.text or '')[:300]}"}
            sj = s.json()
            st = (sj.get("status") or "").upper()
            if st in ("IN_QUEUE", "QUEUED"):
                return {"state": "queued", "queue_position": sj.get("queue_position")}
            if st == "IN_PROGRESS":
                return {"state": "running"}
            if st not in ("COMPLETED", "OK", "SUCCESS"):
                return {"state": "error", "error": f"{st or 'unknown'}: {str(sj)[:300]}"}
            rr = await c.get(req.response_url, headers=_auth())
            if rr.status_code >= 400:
                return {"state": "error", "error": f"result {rr.status_code}: {(rr.text or '')[:300]}"}
            body = rr.json()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return {"state": "error", "error": str(e)[:300]}

    url = _first_url(body)
    if not url:
        return {"state": "error", "error": f"no output url in result: {str(body)[:300]}"}
    return {"state": "done", "url": url, "raw_keys": list(body.keys())[:10]}

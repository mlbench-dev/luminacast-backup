"""Smart face extraction from video files using OpenCV + MediaPipe.

Downloads a video, samples frames, detects faces, scores them by size/quality,
and returns the best face crop(s) as JPEG bytes.  Optionally filters with a
Vision LLM (Gemini Flash via OpenRouter) to reject overlays / watermarks.
"""
import base64
import json
import logging
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision
import numpy as np

logger = logging.getLogger(__name__)

# Model path — prefer pre-downloaded (Docker), fall back to runtime download
_MODEL_DIR = os.environ.get("MEDIAPIPE_MODEL_DIR", "/app/models")
_MODEL_FILENAME = "blaze_face_short_range.tflite"
_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"


def _get_model_path() -> str:
    """Return path to the face detector model, downloading if necessary."""
    path = os.path.join(_MODEL_DIR, _MODEL_FILENAME)
    if os.path.exists(path):
        return path
    # Try /tmp fallback for environments without /app/models
    tmp_dir = os.path.join(tempfile.gettempdir(), "mediapipe_models")
    tmp_path = os.path.join(tmp_dir, _MODEL_FILENAME)
    if os.path.exists(tmp_path):
        return tmp_path
    os.makedirs(tmp_dir, exist_ok=True)
    logger.info("Downloading mediapipe face detector model to %s", tmp_path)
    urllib.request.urlretrieve(_MODEL_URL, tmp_path)
    return tmp_path


def _log(level: str, service: str, message: str, **kwargs):
    import json as _json
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        _json.dumps({
            "service": service, "level": level, "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(), **kwargs,
        }),
    )


@dataclass
class ScoredFace:
    """A detected face with quality metrics."""
    frame_idx: int
    confidence: float
    bbox_area_ratio: float   # face bounding box area / frame area (bigger = closer)
    sharpness: float         # Laplacian variance (higher = sharper)
    frame: np.ndarray        # the full frame (BGR)
    bbox: tuple              # (x, y, w, h) in pixels


def _crop_portrait(frame: np.ndarray, bbox: tuple, target_size: int = 768) -> np.ndarray:
    """Crop a portrait-style image around a face bounding box."""
    frame_height, frame_width = frame.shape[:2]
    x, y, w, h = bbox
    pad_x = int(w * 0.6)
    pad_y_top = int(h * 0.5)
    pad_y_bottom = int(h * 0.8)

    crop_x1 = max(0, x - pad_x)
    crop_y1 = max(0, y - pad_y_top)
    crop_x2 = min(frame_width, x + w + pad_x)
    crop_y2 = min(frame_height, y + h + pad_y_bottom)

    portrait = frame[crop_y1:crop_y2, crop_x1:crop_x2]

    ph, pw = portrait.shape[:2]
    if max(ph, pw) > target_size:
        scale = target_size / max(ph, pw)
        portrait = cv2.resize(portrait, (int(pw * scale), int(ph * scale)),
                              interpolation=cv2.INTER_LANCZOS4)
    return portrait


def _encode_jpeg(img: np.ndarray, quality: int = 95) -> bytes | None:
    success, jpeg_buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return jpeg_buf.tobytes() if success else None


def extract_top_faces(
    video_path: str,
    max_faces: int = 12,
    sample_fps: float = 2.0,
    min_confidence: float = 0.35,
    min_sharpness: float = 8.0,
    target_size: int = 768,
) -> list[dict]:
    """Extract the top N face frames from a local video file.

    Returns:
        List of {'jpeg_bytes': bytes, 'score': float, 'frame_idx': int} dicts,
        sorted best-first.
    """
    _log("info", "face_extraction", "Starting top-faces extraction",
         video_path=video_path, max_faces=max_faces, sample_fps=sample_fps)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        _log("warning", "face_extraction", "Cannot open video", video_path=video_path)
        return []

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_area = frame_width * frame_height

    sample_interval = max(1, int(video_fps / sample_fps))

    _log("info", "face_extraction", "Video info",
         fps=video_fps, total_frames=total_frames,
         resolution=f"{frame_width}x{frame_height}",
         sample_interval=sample_interval)

    # Create face detector using mediapipe Tasks API (mp.solutions removed in 0.10.33)
    base_options = mp_tasks.BaseOptions(model_asset_path=_get_model_path())
    detector_options = vision.FaceDetectorOptions(
        base_options=base_options,
        min_detection_confidence=min_confidence,
    )
    face_detector = vision.FaceDetector.create_from_options(detector_options)

    scored_faces: list[ScoredFace] = []
    frame_idx = 0
    sampled = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_interval != 0:
                frame_idx += 1
                continue

            sampled += 1

            # Downscale large frames for faster/better detection
            detect_frame = frame
            scale_factor = 1.0
            if frame_width > 960 or frame_height > 960:
                scale_factor = 960.0 / max(frame_width, frame_height)
                detect_frame = cv2.resize(frame, (int(frame_width * scale_factor), int(frame_height * scale_factor)))

            rgb_frame = cv2.cvtColor(detect_frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            results = face_detector.detect(mp_image)

            if results.detections:
                for detection in results.detections:
                    confidence = detection.categories[0].score
                    if confidence < min_confidence:
                        continue

                    # New API returns pixel coordinates directly (scale back to original)
                    bbox = detection.bounding_box
                    x = max(0, int(bbox.origin_x / scale_factor))
                    y = max(0, int(bbox.origin_y / scale_factor))
                    w = min(int(bbox.width / scale_factor), frame_width - x)
                    h = min(int(bbox.height / scale_factor), frame_height - y)

                    if w < 20 or h < 20:
                        continue

                    bbox_area_ratio = (w * h) / frame_area

                    face_crop = frame[y:y+h, x:x+w]
                    gray_face = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
                    sharpness = cv2.Laplacian(gray_face, cv2.CV_64F).var()

                    if sharpness < min_sharpness:
                        continue

                    scored_faces.append(ScoredFace(
                        frame_idx=frame_idx,
                        confidence=confidence,
                        bbox_area_ratio=bbox_area_ratio,
                        sharpness=sharpness,
                        frame=frame.copy(),
                        bbox=(x, y, w, h),
                    ))

            frame_idx += 1
            if sampled >= 300:
                break

    finally:
        cap.release()
        face_detector.close()

    _log("info", "face_extraction", "Sampling complete",
         frames_sampled=sampled, faces_found=len(scored_faces))

    if not scored_faces:
        _log("warning", "face_extraction", "No suitable faces found in video")
        return []

    # Score: face size 70%, sharpness 20%, confidence 10%
    for sf in scored_faces:
        sf._score = (
            sf.bbox_area_ratio * 0.7 +
            min(sf.sharpness / 500.0, 1.0) * 0.2 +
            sf.confidence * 0.1
        )

    scored_faces.sort(key=lambda sf: sf._score, reverse=True)

    results = []
    for sf in scored_faces[:max_faces]:
        portrait = _crop_portrait(sf.frame, sf.bbox, target_size)
        jpeg_bytes = _encode_jpeg(portrait)
        if jpeg_bytes and len(jpeg_bytes) > 1000:
            results.append({
                "jpeg_bytes": jpeg_bytes,
                "score": round(sf._score, 4),
                "frame_idx": sf.frame_idx,
            })

    _log("info", "face_extraction", f"Returning {len(results)} candidate faces")
    return results


def extract_best_face(
    video_path: str,
    sample_fps: float = 2.0,
    min_confidence: float = 0.35,
    min_sharpness: float = 8.0,
    target_size: int = 768,
) -> bytes | None:
    """Extract the single best face frame from a local video file.

    Thin wrapper around extract_top_faces(max_faces=1).
    """
    faces = extract_top_faces(
        video_path,
        max_faces=1,
        sample_fps=sample_fps,
        min_confidence=min_confidence,
        min_sharpness=min_sharpness,
        target_size=target_size,
    )
    return faces[0]["jpeg_bytes"] if faces else None


def _download_video_ytdlp(url: str) -> str | None:
    """Download a video using yt-dlp (handles TikTok page URLs). Returns local path or None."""
    import subprocess
    tmp_path = os.path.join(tempfile.gettempdir(), f"face_extract_{os.getpid()}.mp4")
    try:
        result = subprocess.run(
            ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
             "--no-playlist", "--max-filesize", "50M",
             "-o", tmp_path, "--no-warnings", "--quiet",
             url],
            capture_output=True, timeout=60,
        )
        if result.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 10000:
            _log("info", "face_extraction", "Video downloaded via yt-dlp",
                 size=os.path.getsize(tmp_path), path=tmp_path)
            return tmp_path
        else:
            _log("warning", "face_extraction", "yt-dlp download failed",
                 returncode=result.returncode, stderr=result.stderr.decode()[:200])
            return None
    except Exception as e:
        _log("warning", "face_extraction", f"yt-dlp error: {e}")
        return None


def detect_face_in_image(image_bytes: bytes, min_confidence: float = 0.4) -> tuple[bool, float]:
    """Run MediaPipe face detection on a single image (cover thumbnail).

    Returns (has_face, confidence).  Lightweight — used during video fetch
    to tag thumbnails with a face-detection dot in the gallery UI.
    """
    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return (False, 0.0)

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        base_options = mp_tasks.BaseOptions(model_asset_path=_get_model_path())
        opts = vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=min_confidence,
        )
        detector = vision.FaceDetector.create_from_options(opts)
        try:
            results = detector.detect(mp_image)
        finally:
            detector.close()

        if not results.detections:
            return (False, 0.0)

        best = max(results.detections, key=lambda d: d.categories[0].score)
        conf = best.categories[0].score
        # Determine quality: large enough face?
        bbox = best.bounding_box
        img_area = img.shape[0] * img.shape[1]
        face_area = bbox.width * bbox.height
        ratio = face_area / img_area if img_area > 0 else 0
        # If face is tiny (<2% of image), mark as low confidence
        if ratio < 0.02:
            return (True, min(conf, 0.5))
        return (True, round(conf, 3))
    except Exception as e:
        _log("warning", "face_extraction", f"Cover face detection failed: {e}")
        return (False, 0.0)


def detect_and_frame_face(
    image_bytes: bytes,
    min_confidence: float = 0.4,
    target_w: int = 720,
    target_h: int = 1280,
) -> dict:
    """Detect a face in a single uploaded photo and reframe it around that
    face to the portrait canvas the avatar animation pipeline expects.

    Uploaded photos vary wildly in framing (arbitrary aspect ratio, subject
    positioned anywhere), but the downstream talking-head model always fits
    the image into a fixed ~9:16 canvas — naively fitting an off-center
    subject into that canvas is what cuts people off.

    This trims only whichever dimension doesn't already match the target
    aspect ratio (width if the source is relatively too wide, height if it's
    relatively too tall), sliding that trim so the detected face stays
    centered within it. Deliberately NOT a tight pad-around-the-face crop
    (like _crop_portrait, built for pulling a portrait out of a big video
    frame) — a photo that's already reasonably framed keeps its existing
    body/shoulder framing; only the minimum needed to fix centering and hit
    the target ratio gets cut away.

    Returns one of:
        {"ok": True, "confidence": float, "jpeg_bytes": bytes}
        {"ok": False, "reason": "no_face", "confidence": 0.0}
        {"ok": False, "reason": "too_close_to_edge", "confidence": float}

    "too_close_to_edge" means the source photo's own aspect ratio is so far
    from the target that trimming down to it can't keep the whole face (with
    reasonable margin) in frame no matter where the trim window slides — the
    photo itself doesn't contain a usable frame, not just an off-center one.
    """
    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return {"ok": False, "reason": "no_face", "confidence": 0.0}

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        base_options = mp_tasks.BaseOptions(model_asset_path=_get_model_path())
        opts = vision.FaceDetectorOptions(base_options=base_options, min_detection_confidence=min_confidence)
        detector = vision.FaceDetector.create_from_options(opts)
        try:
            results = detector.detect(mp_image)
        finally:
            detector.close()

        if not results.detections:
            return {"ok": False, "reason": "no_face", "confidence": 0.0}

        best = max(results.detections, key=lambda d: d.categories[0].score)
        confidence = round(best.categories[0].score, 3)
        bb = best.bounding_box
        frame_h, frame_w = img.shape[:2]
        x, y, w, h = int(bb.origin_x), int(bb.origin_y), int(bb.width), int(bb.height)
        if w <= 0 or h <= 0:
            return {"ok": False, "reason": "no_face", "confidence": 0.0}

        img_area = frame_w * frame_h
        face_area = w * h
        if img_area > 0 and (face_area / img_area) < 0.02:
            confidence = min(confidence, 0.5)

        # Anchor near eye-level rather than the face's dead center, so the
        # trim below leaves more room for shoulders/chest than for empty
        # space above the head.
        anchor_x = x + w / 2
        anchor_y = y + h * 0.35

        target_ratio = target_w / target_h
        current_ratio = frame_w / frame_h

        if current_ratio > target_ratio:
            # Source is relatively too wide — trim width, keep full height.
            crop_h = frame_h
            crop_w = max(1, int(round(crop_h * target_ratio)))
        else:
            # Source is relatively too tall/narrow — trim height, keep full width.
            crop_w = frame_w
            crop_h = max(1, int(round(crop_w / target_ratio)))

        # Slide the crop window to center it on the face anchor, clamped to
        # stay fully within the source image.
        crop_x1 = int(round(anchor_x - crop_w / 2))
        crop_y1 = int(round(anchor_y - crop_h * 0.4))
        crop_x1 = max(0, min(crop_x1, frame_w - crop_w))
        crop_y1 = max(0, min(crop_y1, frame_h - crop_h))
        crop_x2, crop_y2 = crop_x1 + crop_w, crop_y1 + crop_h

        # Even after sliding the window as far as it can go toward the face,
        # check the whole face bbox (plus a little margin) still fits inside
        # it — if not, the source photo's aspect ratio is too far off for
        # any trim window to keep the person fully framed.
        margin_x, margin_y = w * 0.15, h * 0.15
        if (x - margin_x < crop_x1 or x + w + margin_x > crop_x2
                or y - margin_y < crop_y1 or y + h + margin_y > crop_y2):
            return {"ok": False, "reason": "too_close_to_edge", "confidence": confidence}

        crop = img[crop_y1:crop_y2, crop_x1:crop_x2]
        crop = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
        success, jpeg_buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not success:
            return {"ok": False, "reason": "no_face", "confidence": confidence}

        return {"ok": True, "confidence": confidence, "jpeg_bytes": jpeg_buf.tobytes()}
    except Exception as e:
        _log("warning", "face_extraction", f"detect_and_frame_face failed: {e}")
        return {"ok": False, "reason": "no_face", "confidence": 0.0}


async def extract_face_from_url(video_url: str, **kwargs) -> bytes | None:
    """Download a video from URL and extract the best face.

    Supports both direct .mp4 CDN URLs and TikTok page URLs (via yt-dlp).
    """
    import httpx

    _log("info", "face_extraction", "Downloading video for face extraction",
         url=video_url[:100])

    tmp_path = None
    is_tiktok_page = "tiktok.com" in video_url and "/video/" in video_url

    try:
        if is_tiktok_page:
            import asyncio
            loop = asyncio.get_event_loop()
            tmp_path = await loop.run_in_executor(None, _download_video_ytdlp, video_url)
            if not tmp_path:
                return None
        else:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "unknown")
                if "text/html" in content_type or len(resp.content) < 10000:
                    _log("warning", "face_extraction", "Downloaded content is not a video file",
                         content_type=content_type, size=len(resp.content))
                    return None

                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                    f.write(resp.content)
                    tmp_path = f.name

                _log("info", "face_extraction", "Video downloaded via httpx",
                     size=len(resp.content), path=tmp_path)

        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: extract_best_face(tmp_path, **kwargs))
        return result

    except Exception as e:
        _log("warning", "face_extraction", f"Face extraction from URL failed: {e}",
             url=video_url[:100])
        return None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def extract_top_faces_from_url(video_url: str, max_faces: int = 12, **kwargs) -> list[dict]:
    """Download video and extract top N face candidates.

    Returns list of {'jpeg_bytes': bytes, 'score': float, 'frame_idx': int}.
    """
    import httpx

    _log("info", "face_extraction", "Downloading video for candidate extraction",
         url=video_url[:100], max_faces=max_faces)

    tmp_path = None
    is_tiktok_page = "tiktok.com" in video_url and "/video/" in video_url

    try:
        if is_tiktok_page:
            import asyncio
            loop = asyncio.get_event_loop()
            tmp_path = await loop.run_in_executor(None, _download_video_ytdlp, video_url)
            if not tmp_path:
                return []
        else:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "unknown")
                if "text/html" in content_type or len(resp.content) < 10000:
                    return []

                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                    f.write(resp.content)
                    tmp_path = f.name

        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: extract_top_faces(tmp_path, max_faces=max_faces, **kwargs)
        )
        return result

    except Exception as e:
        _log("warning", "face_extraction", f"Top-faces extraction from URL failed: {e}",
             url=video_url[:100])
        return []
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def filter_frames_with_vision(frames: list[dict], openrouter_api_key: str) -> list[dict]:
    """Filter candidate frames using a Vision LLM (Gemini Flash via OpenRouter).

    Sends base64-encoded frames to Gemini 2.5 Flash and asks it to score each
    frame 1-10 for avatar suitability, rejecting frames with text overlays,
    watermarks, profanity, bad expressions, etc.

    Args:
        frames: List of {'jpeg_bytes': bytes, 'score': float, 'frame_idx': int}
        openrouter_api_key: OpenRouter API key

    Returns:
        Filtered and re-sorted list of frames (rejected frames removed).
    """
    import httpx

    if not frames:
        return []

    _log("info", "face_extraction", f"Filtering {len(frames)} frames with Vision LLM")

    # Build image content blocks
    content_parts = []
    content_parts.append({
        "type": "text",
        "text": (
            f"I have {len(frames)} candidate face images for a talking-head avatar. "
            "Score each image 1-10 for avatar suitability. A good avatar frame shows: "
            "clear face, looking at/near camera, good lighting, minimal obstruction. "
            "ONLY reject (reject=true) if the face is SEVERELY obscured by: "
            "large text covering the face area, heavy watermark across the face, "
            "completely blurred/motion-smeared face, or eyes fully closed. "
            "Do NOT reject for: small captions at top/bottom, small usernames, "
            "TikTok UI elements, hashtags, or minor text that doesn't cover the face. "
            "Return JSON array only: [{\"index\": 0, \"score\": 8, \"reject\": false, \"reason\": \"\"}, ...] "
            "No markdown, no explanation, just the JSON array."
        ),
    })

    for i, frame in enumerate(frames):
        b64 = base64.b64encode(frame["jpeg_bytes"]).decode("ascii")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://luminacast.ai",
                    "X-Title": "Luminacast Omni",
                },
                json={
                    "model": "google/gemini-2.5-flash",
                    "messages": [{"role": "user", "content": content_parts}],
                    "max_tokens": 1024,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        raw_text = result["choices"][0]["message"]["content"].strip()
        # Strip markdown fences if present
        if raw_text.startswith("```"):
            lines = raw_text.splitlines()
            raw_text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        vision_scores = json.loads(raw_text)

        filtered = []
        for entry in vision_scores:
            idx = entry.get("index", -1)
            if idx < 0 or idx >= len(frames):
                continue
            if entry.get("reject", False):
                _log("info", "face_extraction", f"Frame {idx} rejected: {entry.get('reason', 'unknown')}")
                continue
            frame = frames[idx].copy()
            frame["vision_score"] = entry.get("score", 5)
            filtered.append(frame)

        # Sort by vision score descending
        filtered.sort(key=lambda f: f.get("vision_score", 0), reverse=True)

        _log("info", "face_extraction",
             f"Vision filter: {len(filtered)}/{len(frames)} frames passed")

        # If ALL frames were rejected, return top 4 by original score as fallback
        # (better to show imperfect frames than fail completely)
        if not filtered and frames:
            _log("warning", "face_extraction",
                 "All frames rejected by vision filter — returning top 4 by original score as fallback")
            fallback = sorted(frames, key=lambda f: f.get("score", 0), reverse=True)[:4]
            for f in fallback:
                f["vision_score"] = 3  # low score but not rejected
            return fallback

        return filtered

    except Exception as e:
        _log("warning", "face_extraction", f"Vision LLM filter failed: {e}, returning all frames")
        return frames


async def upload_candidate_frames(
    frames: list[dict],
    avatar_id: str,
    user_id: str,
) -> list[str]:
    """Upload candidate face frames to R2 and return R2 keys.

    Args:
        frames: List of dicts with at least 'jpeg_bytes' key.
        avatar_id: Avatar ID for the R2 key path.
        user_id: User ID for the R2 key path.

    Returns:
        List of R2 keys for each uploaded frame.
    """
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    keys = []
    for i, frame in enumerate(frames):
        key = f"creators/{user_id}/avatar/{avatar_id}/candidates/frame_{i}.jpg"
        await r2.upload_bytes(frame["jpeg_bytes"], key, "image/jpeg")
        keys.append(key)

    _log("info", "face_extraction",
         f"Uploaded {len(keys)} candidate frames to R2",
         avatar_id=avatar_id)
    return keys

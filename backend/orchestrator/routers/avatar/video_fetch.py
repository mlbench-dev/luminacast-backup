"""Avatar video_fetch endpoints — split from the former routers/avatar.py."""

from datetime import datetime, timedelta
import base64
import logging
import uuid
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy import update as sa_update
from pydantic import BaseModel
from typing import Optional
from database import get_db
from models.user import User, TeamRole
from models.avatar import Avatar, AvatarType, AvatarStatus, BodyShotSet
from models.avatar_look import AvatarLook
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user, WorkspaceContext, require_role
from services import audit_log
from services.r2_storage import get_r2_storage_service
from services.fish_audio import get_fish_audio_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)
import re

logger = logging.getLogger(__name__)

from ._shared import normalize_tiktok_input, _r2_key_to_url, AvatarResponse, _avatar_to_response, _BODY_MOTION_POSES, _BODY_MOTION_POSE_LABELS, _seed_body_motion_looks_from_body_shot_set

router = APIRouter()

class FetchVideosRequest(BaseModel):
    tiktok_url: str = ""
    name: str = ""
    page: int = 1
    per_page: int = 16

class FetchedVideo(BaseModel):
    thumb_url: str          # R2 CDN URL (proxied from TikTok cover image)
    video_url: str          # R2 CDN URL (populated after download) or empty
    web_video_url: str = "" # TikTok page URL for yt-dlp download
    duration_seconds: float
    video_r2_key: str = "" # R2 key (populated after download)
    description: str = ""
    views: int = 0
    has_face: Optional[bool] = False
    face_confidence: float = 0.0
    likes: int = 0
    create_time: Optional[str] = None
    comments: Optional[list[dict]] = None

class FetchVideosResponse(BaseModel):
    videos: list[FetchedVideo]
    total: int = 0
    page: int = 1
    per_page: int = 16
    total_pages: int = 0

@router.post("/fetch-videos", response_model=FetchVideosResponse)
async def fetch_videos(
    req: FetchVideosRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Fetch TikTok video metadata with server-side caching and pagination.

    First request fetches ALL videos from Apify, runs face detection on all,
    and caches the full result in scraping_jobs (24h TTL). Subsequent pages
    are served from cache.
    """
    import httpx
    import asyncio
    import hashlib
    import math
    from datetime import datetime, timedelta
    from models.scraping_job import ScrapingJob

    tiktok_url = normalize_tiktok_input(req.tiktok_url)
    if not tiktok_url or "tiktok.com/@" not in tiktok_url:
        raise HTTPException(status_code=400, detail="Please enter a valid TikTok username or profile URL.")

    # Extract handle for cache lookup
    import re as _re
    handle_match = _re.search(r'tiktok\.com/@([^/?&#]+)', tiktok_url)
    tiktok_handle = handle_match.group(1).lower() if handle_match else tiktok_url

    logger.info("fetch_videos called", extra={"handle": tiktok_handle, "page": req.page, "per_page": req.per_page})

    # ── Check cache ──
    now = datetime.utcnow()
    cached = (await db.execute(
        select(ScrapingJob)
        .where(ScrapingJob.handle == tiktok_handle)
        .where(ScrapingJob.platform == "tiktok")
        .where(ScrapingJob.expires_at > now)
        .order_by(ScrapingJob.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    cache_entry = None

    if cached and cached.result_data:
        all_videos = cached.result_data
        cache_entry = cached
        logger.info("Cache hit", extra={"handle": tiktok_handle, "total": len(all_videos)})
    else:
        # ── Fetch from Apify (metadata only — no face detection yet) ──
        from services.apify_tiktok import get_apify_tiktok_service
        apify = get_apify_tiktok_service()
        videos = await apify.fetch_tiktok_videos(tiktok_url, max_videos=100)

        if not videos:
            raise HTTPException(status_code=404, detail="No videos found. The account may be private or empty.")

        logger.info("Apify returned videos", extra={"count": len(videos), "handle": tiktok_handle})

        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        handle_hash = hashlib.md5(tiktok_url.encode()).hexdigest()[:8]

        async def proxy_cover_only(i: int, cover_url: str) -> str:
            """Proxy cover image to R2, returns CDN URL. No face detection."""
            if not cover_url:
                return ""
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(cover_url)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        cover_hash = hashlib.md5(cover_url.encode()).hexdigest()[:8]
                        key = f"covers/{handle_hash}/{cover_hash}_{i}.jpg"
                        await r2.upload_bytes(
                            resp.content, key, "image/jpeg",
                            cache_control="no-cache, no-store, must-revalidate",
                        )
                        return r2.get_public_url(key, cache_bust=True)
            except Exception:
                pass
            return ""

        # Proxy all cover images to R2 (fast, no face detection)
        cover_tasks = [
            proxy_cover_only(i, v.get("cover_url", ""))
            for i, v in enumerate(videos)
        ]
        cover_results = await asyncio.gather(*cover_tasks)

        all_videos = []
        for i, v in enumerate(videos):
            web_url = v.get("video_url", "") or v.get("video_download_url", "")
            duration = float(v.get("duration", 0) or 0)
            stats = v.get("stats", {})
            thumb_url = cover_results[i] if i < len(cover_results) else ""

            if not thumb_url and not web_url:
                continue

            create_time = v.get("createTimeISO", "") or v.get("createTime", "")
            likes = stats.get("diggCount", 0) if isinstance(stats, dict) else 0
            comments_data = None
            raw_comments = v.get("comments", None)
            if raw_comments and isinstance(raw_comments, list):
                comments_data = []
                for c in raw_comments[:5]:
                    comments_data.append({
                        "user": c.get("uniqueId", c.get("user", {}).get("uniqueId", "unknown")),
                        "text": (c.get("text", "") or "")[:200],
                        "likes": c.get("diggCount", 0),
                    })

            all_videos.append({
                "thumb_url": thumb_url,
                "video_url": "",
                "web_video_url": web_url,
                "duration_seconds": duration,
                "video_r2_key": "",
                "description": (v.get("description", "") or "")[:200],
                "views": stats.get("playCount", stats.get("views", 0)) if isinstance(stats, dict) else 0,
                "has_face": None,  # Not yet detected — lazy
                "face_confidence": 0.0,
                "likes": likes,
                "create_time": str(create_time) if create_time else None,
                "comments": comments_data,
            })

        if not all_videos:
            raise HTTPException(status_code=500, detail="Could not fetch any TikTok video metadata.")

        # ── Store in cache (no face detection yet) ──
        job_id = f"scrape_{uuid.uuid4().hex[:12]}"
        cache_entry = ScrapingJob(
            id=job_id,
            user_id=ctx.workspace_owner_id,            platform="tiktok",
            handle=tiktok_handle,
            normalized_url=tiktok_url,
            status="completed",
            video_count=len(all_videos),
            result_data=all_videos,
            expires_at=now + timedelta(hours=24),
        )
        db.add(cache_entry)
        await db.commit()
        logger.info("Cached videos (no face detection yet)", extra={"handle": tiktok_handle, "count": len(all_videos)})

    # ── Paginate ──
    total = len(all_videos)
    total_pages = math.ceil(total / req.per_page)
    start = (req.page - 1) * req.per_page
    end = start + req.per_page
    page_slice = all_videos[start:end]

    # ── Lazy face detection: only run on this page's videos if not yet done ──
    needs_face_detection = any(v.get("has_face") is None for v in page_slice)
    if needs_face_detection:
        from services.r2_storage import get_r2_storage_service
        from services.face_extraction import detect_face_in_image
        r2 = get_r2_storage_service()

        async def detect_face_for_video(video: dict) -> dict:
            """Run face detection on a single video's cover image."""
            if video.get("has_face") is not None:
                return video  # Already processed
            thumb_url = video.get("thumb_url", "")
            if not thumb_url:
                video["has_face"] = False
                video["face_confidence"] = 0.0
                return video
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    resp = await client.get(thumb_url)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        import asyncio as _asyncio
                        loop = _asyncio.get_event_loop()
                        has_face, confidence = await loop.run_in_executor(
                            None, detect_face_in_image, resp.content
                        )
                        video["has_face"] = has_face
                        video["face_confidence"] = confidence
                        return video
            except Exception:
                pass
            video["has_face"] = False
            video["face_confidence"] = 0.0
            return video

        face_tasks = [detect_face_for_video(v) for v in page_slice]
        page_slice = await asyncio.gather(*face_tasks)
        logger.info("Lazy face detection completed for page", extra={
            "handle": tiktok_handle, "page": req.page,
            "detected": sum(1 for v in page_slice if v.get("has_face")),
        })

        # Update cache with face detection results for this page
        if cache_entry:
            for i, v in enumerate(page_slice):
                idx = start + i
                if idx < len(all_videos):
                    all_videos[idx]["has_face"] = v["has_face"]
                    all_videos[idx]["face_confidence"] = v["face_confidence"]
            cache_entry.result_data = all_videos
            await db.commit()

    page_videos = [FetchedVideo(**v) for v in page_slice]

    logger.info("fetch_videos returning", extra={
        "handle": tiktok_handle, "page": req.page, "total": total, "count": len(page_videos),
    })
    return FetchVideosResponse(
        videos=page_videos,
        total=total,
        page=req.page,
        per_page=req.per_page,
        total_pages=total_pages,
    )

class DownloadVideoRequest(BaseModel):
    web_video_url: str

class DownloadVideoResponse(BaseModel):
    video_url: str
    video_r2_key: str
    duration_seconds: float

@router.post("/download-video", response_model=DownloadVideoResponse)
async def download_video(
    req: DownloadVideoRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Download a single TikTok video via yt-dlp, faststart it, upload to R2.
    Called when user selects a video from the gallery.
    """
    import subprocess
    import tempfile
    import os
    import hashlib

    video_hash = hashlib.md5(req.web_video_url.encode()).hexdigest()[:8]
    tmp_video = os.path.join(tempfile.gettempdir(), f"dl_{ctx.workspace_owner_id}_{video_hash}.mp4")
    tmp_fast = os.path.join(tempfile.gettempdir(), f"dl_{ctx.workspace_owner_id}_{video_hash}_fast.mp4")

    try:
        # Download via yt-dlp
        result = subprocess.run(
            ["yt-dlp", "-f", "mp4/best[ext=mp4]/best",
             "--no-playlist", "--max-filesize", "50M",
             "-o", tmp_video, "--no-warnings", "--quiet", req.web_video_url],
            capture_output=True, timeout=90,
        )
        if result.returncode != 0 or not os.path.exists(tmp_video):
            raise HTTPException(status_code=500, detail="Failed to download video from TikTok.")

        # Get duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp_video],
            capture_output=True, timeout=10,
        )
        duration = 0.0
        if probe.returncode == 0:
            try:
                duration = float(probe.stdout.decode().strip())
            except (ValueError, AttributeError):
                pass

        # Faststart for streaming
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_video, "-c", "copy",
             "-movflags", "+faststart", tmp_fast],
            capture_output=True, timeout=30,
        )
        upload_path = tmp_fast if os.path.exists(tmp_fast) else tmp_video

        # Upload to R2
        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        video_r2_key = f"creators/{ctx.workspace_owner_id}/videos/{video_hash}.mp4"
        await r2.upload_file(upload_path, video_r2_key, content_type="video/mp4")
        video_cdn_url = r2.get_public_url(video_r2_key)

        return DownloadVideoResponse(
            video_url=video_cdn_url,
            video_r2_key=video_r2_key,
            duration_seconds=round(duration, 1),
        )
    finally:
        for p in [tmp_video, tmp_fast]:
            try:
                os.unlink(p)
            except OSError:
                pass

class ProcessSegmentRequest(BaseModel):
    video_r2_key: str
    start_seconds: float
    end_seconds: float

@router.post("/{avatar_id}/process-segment", response_model=AvatarResponse)
async def process_segment(
    avatar_id: str,
    req: ProcessSegmentRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Receive a video segment selection and kick off image + voice pipelines in parallel.

    Image pipeline: extract frames → MediaPipe → Gemini scoring → face_candidates_ready
    Voice pipeline: extract audio → BS-RoFormer → normalize → Fish Audio clone
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if req.end_seconds <= req.start_seconds:
        raise HTTPException(status_code=400, detail="end_seconds must be greater than start_seconds")

    if req.end_seconds - req.start_seconds < 15:
        raise HTTPException(status_code=400, detail="Segment must be at least 15 seconds")

    # Update avatar status and save segment info for potential re-clone
    avatar.status = AvatarStatus.PROCESSING
    avatar.progress_step = "Processing video segment..."
    avatar.progress_percent = 20
    avatar.voice_clone_progress = 0
    avatar.video_ref_key = req.video_r2_key
    # Store segment times in persona_profile for re-clone capability
    segment_info = {"segment_start": req.start_seconds, "segment_end": req.end_seconds}
    avatar.persona_profile = {**(avatar.persona_profile or {}), **segment_info}
    await db.commit()

    # Kick off BOTH pipelines in parallel via Celery
    from tasks.generate_avatar import process_image_pipeline_task, process_voice_pipeline_task

    process_image_pipeline_task.delay(
        avatar_id, ctx.workspace_owner_id,
        req.video_r2_key, req.start_seconds, req.end_seconds,
    )
    process_voice_pipeline_task.delay(
        avatar_id, ctx.workspace_owner_id,
        req.video_r2_key, req.start_seconds, req.end_seconds,
    )

    await db.refresh(avatar)
    return _avatar_to_response(avatar)

class EditFrameRequest(BaseModel):
    frame_url: str
    instructions: str = ""

class EditFrameResponse(BaseModel):
    original_url: str
    edited_url: str

@router.post("/{avatar_id}/edit-frame", response_model=EditFrameResponse)
async def edit_frame(
    avatar_id: str,
    req: EditFrameRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Edit a face candidate frame using FLUX Kontext Pro.

    Removes TikTok captions/watermarks/UI elements, preserves identity,
    and applies optional user instructions. Downloads result and re-uploads
    to R2 as the avatar's face_ref.jpg.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.flux_kontext import edit_avatar_frame
    from services.r2_storage import get_r2_storage_service
    import httpx

    r2 = get_r2_storage_service()

    try:
        edited_url = await edit_avatar_frame(req.frame_url, req.instructions, user_id=ctx.workspace_owner_id)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Face editing failed: {str(e)[:200]}")

    # Download the edited image and re-upload to R2 with unique key (bust CDN cache)
    try:
        import time as _time
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(edited_url)
            resp.raise_for_status()
            edited_bytes = resp.content

        ts = int(_time.time())
        face_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/face_ref_{ts}.jpg"
        await r2.upload_bytes(edited_bytes, face_key, "image/jpeg", cache_control="no-cache, no-store, must-revalidate")

        avatar.face_ref_key = face_key

        # Phase ownership: advance from IMAGE to VOICE or RENDER
        from models.avatar import AvatarPhase
        voice_done = (avatar.voice_clone_progress or 0) >= 100 and avatar.voice_id

        if voice_done:
            # Skip straight to render phase -- voice was faster than user
            avatar.active_phase = AvatarPhase.RENDER
            avatar.status = AvatarStatus.PROCESSING
            avatar.progress_step = "Generating test video..."
            avatar.progress_percent = 80
            await db.commit()
            from tasks.generate_avatar import regenerate_avatar_video_task
            regenerate_avatar_video_task.delay(avatar_id, ctx.workspace_owner_id, avatar.test_script or "")
        else:
            # Voice still running -- advance to voice phase so its progress messages flow through
            avatar.active_phase = AvatarPhase.VOICE
            avatar.progress_step = "Cloning your voice..."
            avatar.progress_percent = 50
            await db.commit()

        return EditFrameResponse(
            original_url=req.frame_url,
            edited_url=r2.get_public_url(face_key, cache_bust=True),
        )
    except httpx.HTTPError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Failed to download edited image: {str(e)[:200]}")

class FaceCandidateItem(BaseModel):
    url: str
    score: float

class FaceCandidatesResponse(BaseModel):
    avatar_id: str
    status: str
    candidates: list[FaceCandidateItem]
    voice_clone_progress: Optional[int] = None
    progress_step: Optional[str] = None

@router.get("/{avatar_id}/face-candidates", response_model=FaceCandidatesResponse)
async def get_face_candidates(
    avatar_id: str,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.VIEWER.value)),
    db: AsyncSession = Depends(get_db),
):
    """Return scored face candidate URLs for this avatar.

    Supports polling: returns empty candidates while PROCESSING, populated
    candidates once FACE_CANDIDATES_READY or CANDIDATES_READY. Also reports
    voice_clone_progress so frontend can track both pipelines.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    allowed = (
        AvatarStatus.PROCESSING,
        AvatarStatus.FACE_CANDIDATES_READY,
        AvatarStatus.CANDIDATES_READY,
        AvatarStatus.READY,
        AvatarStatus.APPROVED,
        AvatarStatus.FAILED,  # Allow polling to detect failure
    )
    if avatar.status not in allowed:
        raise HTTPException(status_code=400, detail="Avatar does not have face candidates ready")

    frames = avatar.candidate_frames or []
    scores = avatar.candidate_scores or []
    # Use real scores from pipeline if available; fall back to synthetic scores for legacy avatars
    # Convert R2 keys to CDN URLs at response time (B-068)
    candidates = []
    for i, key in enumerate(frames):
        url = _r2_key_to_url(key)
        if i < len(scores) and scores[i]:
            score = round(float(scores[i]), 2)
        else:
            score = round(1.0 - i * (0.8 / max(len(frames) - 1, 1)), 2)
        candidates.append(FaceCandidateItem(url=url, score=score))

    return FaceCandidatesResponse(
        avatar_id=avatar.id,
        status=avatar.status.value,
        candidates=candidates,
        voice_clone_progress=avatar.voice_clone_progress,
        progress_step=avatar.progress_step or "",
    )

class UploadFaceResponse(BaseModel):
    face_ref_key: str
    face_url: str

@router.post("/{avatar_id}/upload-face", response_model=UploadFaceResponse)
async def upload_face(
    avatar_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manually captured frame as the face reference, bypassing auto face extraction.

    Accepts a JPEG/PNG image, uploads to R2, sets face_ref_key, and advances
    status to FACE_CANDIDATES_READY so the rest of the flow continues.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if file.content_type not in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
        raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, or WebP image.")

    file_bytes = await file.read()
    if len(file_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Image file is too small.")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file is too large. Maximum 10MB.")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    face_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/face_ref_manual.jpg"
    await r2.upload_bytes(file_bytes, face_key, file.content_type or "image/jpeg")

    avatar.face_ref_key = face_key
    avatar.status = AvatarStatus.FACE_CANDIDATES_READY
    await db.commit()

    return UploadFaceResponse(
        face_ref_key=face_key,
        face_url=r2.get_public_url(face_key),
    )

class UploadFrameResponse(BaseModel):
    frame_url: str
    r2_key: str

@router.post("/{avatar_id}/upload-frame", response_model=UploadFrameResponse)
async def upload_frame(
    avatar_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a manually captured video frame as a face candidate.

    Stores in R2 at creators/{user_id}/avatar/{avatar_id}/manual_frame_{timestamp}.jpg
    and adds to the candidate_frames array on the avatar.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    if file.content_type not in ("image/jpeg", "image/png", "image/webp", "image/jpg"):
        raise HTTPException(status_code=400, detail="Please upload a JPEG, PNG, or WebP image.")

    file_bytes = await file.read()
    if len(file_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Image file is too small.")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file is too large. Maximum 10MB.")

    import time
    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()

    timestamp = int(time.time() * 1000)
    r2_key = f"creators/{ctx.workspace_owner_id}/avatar/{avatar_id}/manual_frame_{timestamp}.jpg"
    await r2.upload_bytes(file_bytes, r2_key, file.content_type or "image/jpeg")

    # Add to candidate_frames array
    frames = list(avatar.candidate_frames or [])
    frames.insert(0, r2_key)  # user frames first
    avatar.candidate_frames = frames
    await db.commit()

    return UploadFrameResponse(
        frame_url=r2.get_public_url(r2_key),
        r2_key=r2_key,
    )

class SelectFaceRequest(BaseModel):
    face_url: str

@router.post("/{avatar_id}/select-face")
async def select_face(
    avatar_id: str,
    req: SelectFaceRequest,
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
    db: AsyncSession = Depends(get_db),
):
    """Persist the user's chosen face candidate URL as face_ref_key on the Avatar.

    Called when user clicks "Continue with this face" or "Use original" without
    going through the FLUX Kontext edit-frame endpoint (B-054).
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != ctx.workspace_owner_id:
        raise HTTPException(status_code=404, detail="Avatar not found")

    from services.r2_storage import get_r2_storage_service
    r2 = get_r2_storage_service()
    public_base = r2.get_public_url("").rstrip("/")
    if req.face_url.startswith(public_base):
        face_ref_key = req.face_url[len(public_base):].lstrip("/")
    else:
        face_ref_key = req.face_url

    if face_ref_key and not any(face_ref_key.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
        face_ref_key = f"{face_ref_key.rstrip('/')}/face_ref.jpg"

    avatar.face_ref_key = face_ref_key

    # Phase ownership: advance from IMAGE to VOICE or RENDER
    from models.avatar import AvatarPhase
    voice_done = (avatar.voice_clone_progress or 0) >= 100 and avatar.voice_id

    if voice_done:
        # Skip straight to render phase -- voice was faster than user
        avatar.active_phase = AvatarPhase.RENDER
        avatar.status = AvatarStatus.PROCESSING
        avatar.progress_step = "Generating test video..."
        avatar.progress_percent = 80
        await db.commit()
        from tasks.generate_avatar import regenerate_avatar_video_task
        test_script = avatar.test_script or ""
        regenerate_avatar_video_task.delay(avatar_id, ctx.workspace_owner_id, test_script)
    else:
        # Voice still running -- advance to voice phase so its progress messages flow through
        avatar.active_phase = AvatarPhase.VOICE
        avatar.progress_step = "Cloning your voice..."
        avatar.progress_percent = 50
        await db.commit()

    return {"status": "ok"}

class UploadVideoResponse(BaseModel):
    video_r2_key: str
    video_url: str
    duration_seconds: float

@router.post("/upload-video", response_model=UploadVideoResponse)
async def upload_video(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    ctx: WorkspaceContext = Depends(require_role(TeamRole.CREATOR.value)),
):
    """Upload a video file for avatar cloning. Validates format, size, duration.
    Uploads to R2 and returns the key + CDN URL + duration.
    """
    import subprocess
    import tempfile
    import os
    import json as _json

    # Validate extension
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("mp4", "mov", "webm"):
        raise HTTPException(status_code=400, detail="Unsupported format. Please upload .mp4, .mov, or .webm")

    # Read file and validate size (200MB max)
    contents = await file.read()
    if len(contents) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum 200MB.")
    if len(contents) < 10000:
        raise HTTPException(status_code=400, detail="File too small or empty.")

    # Save to temp file for ffprobe
    tmp_path = os.path.join(tempfile.gettempdir(), f"upload_{ctx.workspace_owner_id}_{uuid.uuid4().hex[:8]}.{ext}")
    try:
        with open(tmp_path, "wb") as f:
            f.write(contents)

        # ffprobe duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", tmp_path],
            capture_output=True, timeout=15,
        )
        duration = 0.0
        if probe.returncode == 0:
            try:
                duration = float(probe.stdout.decode().strip())
            except (ValueError, AttributeError):
                pass

        if duration < 15:
            raise HTTPException(status_code=400, detail="Video too short. Minimum 15 seconds.")
        if duration > 180:
            raise HTTPException(status_code=400, detail="Video too long. Maximum 3 minutes.")

        # Upload to R2
        from services.r2_storage import get_r2_storage_service
        r2 = get_r2_storage_service()
        video_r2_key = f"creators/{ctx.workspace_owner_id}/uploads/{uuid.uuid4().hex[:12]}.{ext}"
        content_type = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}.get(ext, "video/mp4")
        await r2.upload_bytes(contents, video_r2_key, content_type)
        video_url = r2.get_public_url(video_r2_key)

        return UploadVideoResponse(
            video_r2_key=video_r2_key,
            video_url=video_url,
            duration_seconds=round(duration, 1),
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

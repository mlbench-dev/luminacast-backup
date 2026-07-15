"""Clone TikTok Scout — scan TikTok handle for full-body videos."""

import logging
import uuid

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from routers.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/avatar/clone/scout", tags=["clone-scout"])


class ScoutRequest(BaseModel):
    tiktok_handle: str


class SelectVideoRequest(BaseModel):
    tiktok_video_id: str
    segment_start_ms: int
    segment_end_ms: int


@router.post("")
async def start_scout(
    req: ScoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start a TikTok scout scan. Returns scan_id immediately."""
    with sentry_sdk.start_span(op="clone_scout", description="start_scout"):
        handle = req.tiktok_handle.strip().lstrip("@")
        if not handle:
            raise HTTPException(400, "TikTok handle is required")

        scan_id = f"cts_{uuid.uuid4().hex[:12]}"
        await db.execute(
            sa_text("""
                INSERT INTO clone_tiktok_scans (id, user_id, tiktok_handle, status)
                VALUES (:id, :user_id, :handle, 'pending')
            """),
            {"id": scan_id, "user_id": user.id, "handle": handle},
        )
        await db.commit()

        # Dispatch Celery task
        from tasks.clone_tiktok_scout import scout_tiktok_handle_task
        scout_tiktok_handle_task.delay(scan_id)

        return {"scan_id": scan_id, "status": "pending"}


@router.get("/{scan_id}")
async def get_scout_status(
    scan_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get scout progress + list of analyzed videos."""
    with sentry_sdk.start_span(op="clone_scout", description="get_scout_status"):
        row = await db.execute(
            sa_text("""
                SELECT id, tiktok_handle, status, videos_found, videos_analyzed,
                       videos_with_full_body, created_at, completed_at, error
                FROM clone_tiktok_scans
                WHERE id = :id AND user_id = :user_id
            """),
            {"id": scan_id, "user_id": user.id},
        )
        scan = row.fetchone()
        if not scan:
            raise HTTPException(404, "Scan not found")

        # Fetch videos
        vrows = await db.execute(
            sa_text("""
                SELECT id, tiktok_video_id, tiktok_url, thumbnail_url, duration_seconds,
                       download_url, has_full_body, full_body_segments,
                       total_full_body_duration_ms, first_full_body_frame_url,
                       analyzed_at, status
                FROM clone_tiktok_videos
                WHERE scan_id = :scan_id
                ORDER BY has_full_body DESC, total_full_body_duration_ms DESC
            """),
            {"scan_id": scan_id},
        )
        videos = []
        for v in vrows.fetchall():
            videos.append({
                "id": v.id,
                "tiktok_video_id": v.tiktok_video_id,
                "tiktok_url": v.tiktok_url,
                "thumbnail_url": v.first_full_body_frame_url or v.thumbnail_url,
                "duration_seconds": v.duration_seconds,
                "has_full_body": v.has_full_body,
                "full_body_segments": v.full_body_segments or [],
                "total_full_body_duration_ms": v.total_full_body_duration_ms,
                "status": v.status,
            })

        return {
            "id": scan.id,
            "tiktok_handle": scan.tiktok_handle,
            "status": scan.status,
            "videos_found": scan.videos_found,
            "videos_analyzed": scan.videos_analyzed,
            "videos_with_full_body": scan.videos_with_full_body,
            "error": scan.error,
            "videos": videos,
        }


@router.post("/{scan_id}/select-video")
async def select_video(
    scan_id: str,
    req: SelectVideoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Select a video segment from scout results to use as clone input."""
    with sentry_sdk.start_span(op="clone_scout", description="select_video"):
        # Verify scan belongs to user
        row = await db.execute(
            sa_text("SELECT id FROM clone_tiktok_scans WHERE id = :id AND user_id = :user_id"),
            {"id": scan_id, "user_id": user.id},
        )
        if not row.fetchone():
            raise HTTPException(404, "Scan not found")

        # Find the video
        vrow = await db.execute(
            sa_text("""
                SELECT id, download_url, tiktok_url
                FROM clone_tiktok_videos
                WHERE scan_id = :scan_id AND tiktok_video_id = :tv_id
            """),
            {"scan_id": scan_id, "tv_id": req.tiktok_video_id},
        )
        video = vrow.fetchone()
        if not video:
            raise HTTPException(404, "Video not found in scan")
        if not video.download_url:
            raise HTTPException(400, "Video has not been downloaded yet")

        # Create a clone avatar using the selected segment
        from models.avatar import Avatar, AvatarType, AvatarStatus
        avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
        avatar = Avatar(
            id=avatar_id,
            user_id=user.id,
            type=AvatarType.CLONE,
            status=AvatarStatus.PROCESSING,
            tiktok_source_url=video.tiktok_url,
            video_ref_key=video.download_url.replace("https://media.luminacast.com/", ""),
            progress_step="Extracting selected segment...",
            progress_percent=5,
        )
        db.add(avatar)
        await db.commit()

        # Dispatch segment extraction + face detection pipeline
        from tasks.generate_avatar import process_image_pipeline_task
        process_image_pipeline_task.delay(
            avatar_id=avatar_id,
            user_id=user.id,
            step_sec=req.segment_start_ms / 1000.0,
            end_sec=req.segment_end_ms / 1000.0,
        )

        return {
            "avatar_id": avatar_id,
            "status": "processing",
            "message": "Extracting segment and detecting faces...",
        }

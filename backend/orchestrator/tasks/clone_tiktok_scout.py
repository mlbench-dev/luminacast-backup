"""Clone TikTok Scout — fetch videos, run MediaPipe Pose, detect full-body segments.

33 keypoints >= 0.7 confidence, ankles present (keypoints 27, 28), >= 3s continuous.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone

import sentry_sdk

from tasks import celery_app

logger = logging.getLogger(__name__)

# MediaPipe Pose landmarks — ankle indices
LEFT_ANKLE = 27
RIGHT_ANKLE = 28
TOTAL_KEYPOINTS = 33
MIN_CONFIDENCE = 0.7
MIN_CONTINUOUS_SECONDS = 3
FPS_EXTRACTION = 2  # 1 frame every 500ms


def _run_async(coro):
    """Run async function from sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _analyze_video_frames(video_path: str) -> list[dict]:
    """Run MediaPipe Pose on extracted frames, find full-body segments.

    Returns list of {start_ms, end_ms, confidence} for continuous full-body segments.
    """
    try:
        import mediapipe as mp
        import cv2
    except ImportError:
        logger.error("mediapipe or cv2 not available — cannot analyze frames")
        return []

    mp_pose = mp.solutions.pose
    pose = mp_pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Cannot open video: {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    # Sample at FPS_EXTRACTION (2 fps)
    frame_interval = max(1, int(fps / FPS_EXTRACTION))
    frame_idx = 0
    qualifying_frames = []  # list of (time_ms, avg_confidence)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            time_ms = int((frame_idx / fps) * 1000)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(rgb)

            if results.pose_landmarks:
                landmarks = results.pose_landmarks.landmark
                # Check all 33 keypoints >= MIN_CONFIDENCE
                confidences = [lm.visibility for lm in landmarks]
                all_above = all(c >= MIN_CONFIDENCE for c in confidences)
                # Check ankles present
                ankles_present = (
                    landmarks[LEFT_ANKLE].visibility >= MIN_CONFIDENCE
                    and landmarks[RIGHT_ANKLE].visibility >= MIN_CONFIDENCE
                )
                if all_above and ankles_present:
                    avg_conf = sum(confidences) / len(confidences)
                    qualifying_frames.append((time_ms, avg_conf))

        frame_idx += 1

    cap.release()
    pose.close()

    # Find continuous runs of >= MIN_CONTINUOUS_SECONDS
    min_consecutive = MIN_CONTINUOUS_SECONDS * FPS_EXTRACTION  # 6 frames at 2fps
    segments = []
    if not qualifying_frames:
        return segments

    run_start = qualifying_frames[0]
    run_end = qualifying_frames[0]
    run_confidences = [qualifying_frames[0][1]]

    for i in range(1, len(qualifying_frames)):
        curr = qualifying_frames[i]
        prev = qualifying_frames[i - 1]
        # Consecutive if within 1.5x the expected interval (allow small gaps)
        expected_gap_ms = 1000 / FPS_EXTRACTION  # 500ms
        if curr[0] - prev[0] <= expected_gap_ms * 1.5:
            run_end = curr
            run_confidences.append(curr[1])
        else:
            # End of run
            if len(run_confidences) >= min_consecutive:
                segments.append({
                    "start_ms": run_start[0],
                    "end_ms": run_end[0],
                    "confidence": round(sum(run_confidences) / len(run_confidences), 3),
                })
            run_start = curr
            run_end = curr
            run_confidences = [curr[1]]

    # Final run
    if len(run_confidences) >= min_consecutive:
        segments.append({
            "start_ms": run_start[0],
            "end_ms": run_end[0],
            "confidence": round(sum(run_confidences) / len(run_confidences), 3),
        })

    return segments


def _extract_first_full_body_frame(video_path: str, time_ms: int, output_path: str) -> bool:
    """Extract a single frame at the given timestamp for thumbnail."""
    try:
        seconds = time_ms / 1000.0
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(seconds),
                "-i", video_path, "-frames:v", "1",
                "-q:v", "2", output_path,
            ],
            capture_output=True, timeout=30,
        )
        return os.path.exists(output_path)
    except Exception as e:
        logger.warning(f"Frame extraction failed: {e}")
        return False


@celery_app.task(bind=True, max_retries=1, name="tasks.clone_tiktok_scout.scout", time_limit=600, soft_time_limit=540)
def scout_tiktok_handle_task(self, scan_id: str):
    """Fetch last 20 TikTok videos, run MediaPipe Pose, detect full-body segments."""
    with sentry_sdk.start_transaction(op="task", name="scout_tiktok_handle"):
        try:
            _run_async(_scout_pipeline(scan_id))
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"Scout pipeline failed for scan {scan_id}: {e}")
            _run_async(_mark_scan_failed(scan_id, str(e)))
            raise


async def _mark_scan_failed(scan_id: str, error: str):
    from database import async_session_factory
    from sqlalchemy import text as sa_text
    async with async_session_factory() as db:
        await db.execute(
            sa_text("UPDATE clone_tiktok_scans SET status = 'failed', error = :err WHERE id = :id"),
            {"id": scan_id, "err": error[:1000]},
        )
        await db.commit()


async def _scout_pipeline(scan_id: str):
    from database import async_session_factory
    from sqlalchemy import text as sa_text
    from services.apify_tiktok import get_apify_tiktok_service
    from services.r2_storage import get_r2_storage_service

    async with async_session_factory() as db:
        # Load scan
        row = await db.execute(
            sa_text("SELECT * FROM clone_tiktok_scans WHERE id = :id"),
            {"id": scan_id},
        )
        scan = row.fetchone()
        if not scan:
            raise ValueError(f"Scan {scan_id} not found")

        # Update status to scraping
        await db.execute(
            sa_text("UPDATE clone_tiktok_scans SET status = 'scraping' WHERE id = :id"),
            {"id": scan_id},
        )
        await db.commit()

        # Step 1: Fetch TikTok videos via Apify
        apify = get_apify_tiktok_service()
        handle_url = f"https://www.tiktok.com/@{scan.tiktok_handle.lstrip('@')}"
        videos = await apify.fetch_tiktok_videos(handle_url, max_videos=20)

        if not videos:
            await db.execute(
                sa_text("UPDATE clone_tiktok_scans SET status = 'failed', error = 'No videos found' WHERE id = :id"),
                {"id": scan_id},
            )
            await db.commit()
            return

        # Update videos_found
        await db.execute(
            sa_text("UPDATE clone_tiktok_scans SET videos_found = :count, status = 'analyzing' WHERE id = :id"),
            {"id": scan_id, "count": len(videos)},
        )
        await db.commit()

        r2 = get_r2_storage_service()
        videos_analyzed = 0
        videos_with_body = 0

        for video_data in videos:
            video_id = f"ctv_{uuid.uuid4().hex[:12]}"
            tiktok_video_id = video_data.get("video_url", "").split("/")[-1].split("?")[0] or uuid.uuid4().hex[:8]
            video_url = video_data.get("video_download_url") or video_data.get("video_url", "")

            # Insert video row
            await db.execute(
                sa_text("""
                    INSERT INTO clone_tiktok_videos
                        (id, scan_id, tiktok_video_id, tiktok_url, thumbnail_url, duration_seconds, status)
                    VALUES
                        (:id, :scan_id, :tv_id, :url, :thumb, :dur, 'analyzing')
                """),
                {
                    "id": video_id, "scan_id": scan_id,
                    "tv_id": tiktok_video_id,
                    "url": video_data.get("video_url", ""),
                    "thumb": video_data.get("cover_url", ""),
                    "dur": video_data.get("duration", 0),
                },
            )
            await db.commit()

            try:
                if not video_url:
                    await db.execute(
                        sa_text("UPDATE clone_tiktok_videos SET status = 'failed' WHERE id = :id"),
                        {"id": video_id},
                    )
                    await db.commit()
                    videos_analyzed += 1
                    continue

                # Download video to temp file
                import httpx
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                        resp = await client.get(video_url)
                        if resp.status_code == 200:
                            with open(tmp_path, "wb") as f:
                                f.write(resp.content)
                        else:
                            raise RuntimeError(f"Download failed: HTTP {resp.status_code}")

                    # Upload to R2
                    r2_key = f"clone-scouts/{scan_id}/{tiktok_video_id}.mp4"
                    await r2.upload_file(tmp_path, r2_key)
                    download_url = r2.get_public_url(r2_key)

                    # Run MediaPipe Pose analysis
                    segments = _analyze_video_frames(tmp_path)
                    has_full_body = len(segments) > 0
                    total_duration_ms = sum(s["end_ms"] - s["start_ms"] for s in segments)

                    # Extract first full-body frame thumbnail
                    first_frame_url = None
                    if segments:
                        frame_path = tmp_path + "_frame.jpg"
                        if _extract_first_full_body_frame(tmp_path, segments[0]["start_ms"], frame_path):
                            frame_r2_key = f"clone-scouts/{scan_id}/{tiktok_video_id}_frame.jpg"
                            await r2.upload_file(frame_path, frame_r2_key)
                            first_frame_url = r2.get_public_url(frame_r2_key)
                            os.unlink(frame_path)

                    # Update video row
                    now = datetime.now(timezone.utc)
                    await db.execute(
                        sa_text("""
                            UPDATE clone_tiktok_videos SET
                                download_url = :dl_url,
                                has_full_body = :has_fb,
                                full_body_segments = :segs,
                                total_full_body_duration_ms = :total_ms,
                                first_full_body_frame_url = :frame_url,
                                analyzed_at = :now,
                                status = 'done'
                            WHERE id = :id
                        """),
                        {
                            "id": video_id, "dl_url": download_url,
                            "has_fb": has_full_body,
                            "segs": __import__("json").dumps(segments),
                            "total_ms": total_duration_ms,
                            "frame_url": first_frame_url, "now": now,
                        },
                    )
                    await db.commit()

                    if has_full_body:
                        videos_with_body += 1

                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)

            except Exception as e:
                sentry_sdk.capture_exception(e, extras={"video_id": video_id, "scan_id": scan_id})
                logger.warning(f"Scout: video {video_id} analysis failed: {e}")
                await db.execute(
                    sa_text("UPDATE clone_tiktok_videos SET status = 'failed' WHERE id = :id"),
                    {"id": video_id},
                )
                await db.commit()

            videos_analyzed += 1
            # Update progress
            await db.execute(
                sa_text("""
                    UPDATE clone_tiktok_scans SET
                        videos_analyzed = :analyzed,
                        videos_with_full_body = :with_body
                    WHERE id = :id
                """),
                {"id": scan_id, "analyzed": videos_analyzed, "with_body": videos_with_body},
            )
            await db.commit()

        # Mark complete
        await db.execute(
            sa_text("""
                UPDATE clone_tiktok_scans SET
                    status = 'complete',
                    completed_at = :now
                WHERE id = :id
            """),
            {"id": scan_id, "now": datetime.now(timezone.utc)},
        )
        await db.commit()
        logger.info(f"Scout complete: {scan_id} — {videos_analyzed} analyzed, {videos_with_body} with full body")

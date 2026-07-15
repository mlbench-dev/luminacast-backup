"""Captions — presigned upload URL + WhisperX transcription for Editor Starter."""

import uuid
import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.user import User
from routers.auth import get_current_user
from services.r2_storage import get_r2_storage_service

router = APIRouter(prefix="/api", tags=["captions"])


class UploadRequest(BaseModel):
    contentType: str
    size: int


class CaptionRequest(BaseModel):
    fileKey: str
    language_hint: Optional[str] = None


MAX_UPLOAD_SIZE = 26_214_400  # 25 MB (Whisper limit)


@router.post("/upload")
async def get_upload_url(
    body: UploadRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a presigned R2 PUT URL for caption audio upload."""
    try:
        if body.size > MAX_UPLOAD_SIZE:
            raise HTTPException(413, detail="file-too-large")

        r2 = get_r2_storage_service()
        ext = "wav" if "wav" in body.contentType else "webm"
        file_key = f"captions/{user.id}/{uuid.uuid4().hex}.{ext}"

        presigned_url = r2.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": r2.bucket,
                "Key": file_key,
                "ContentType": body.contentType,
            },
            ExpiresIn=600,
        )

        read_url = r2.get_public_url(file_key)

        return {
            "presignedUrl": presigned_url,
            "readUrl": read_url,
            "fileKey": file_key,
        }
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, detail="Failed to generate upload URL")


@router.post("/captions")
async def transcribe_captions(
    body: CaptionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transcribe an uploaded audio file via WhisperX, return Remotion Caption[]."""
    try:
        from services.gpu_server import get_gpu_server_client

        r2 = get_r2_storage_service()

        # Verify the key belongs to this user
        if not body.fileKey.startswith(f"captions/{user.id}/"):
            raise HTTPException(403, detail="Access denied")

        # Build a URL the GPU server can fetch
        audio_url = r2.get_public_url(body.fileKey)

        gpu = get_gpu_server_client()
        if not gpu:
            raise HTTPException(503, detail="Transcription service unavailable")

        whisper_result = await gpu.whisper_transcribe(
            audio_url=audio_url,
            language=body.language_hint or "en",
            word_timestamps=True,
        )

        words = whisper_result.get("words", [])

        # Build Remotion-compatible Caption objects
        captions = []
        for w in words:
            captions.append({
                "text": w.get("word", "").strip(),
                "startMs": round(w.get("start", 0) * 1000),
                "endMs": round(w.get("end", 0) * 1000),
                "timestampMs": round(w.get("start", 0) * 1000),
                "confidence": w.get("probability"),
            })

        try:
            from services.usage_tracker import log_usage
            audio_dur = max((w.get("end") or 0) for w in words) if words else 0.0
            await log_usage(
                db,
                user_id=user.id,
                event_type="transcription",
                provider="hostkey",
                provider_cost_usd=0.0,
                quantity=float(audio_dur or 1.0),
                quantity_unit="audio_seconds" if audio_dur else "calls",
                resource_type="audio",
                resource_id=body.fileKey,
                duration_seconds=float(audio_dur or 0.0),
            )
            await db.commit()
        except Exception as _exc:
            sentry_sdk.capture_exception(_exc)

        return {"captions": captions}
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, detail="Transcription failed")

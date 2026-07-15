"""Clone Pipeline — unified create → upload face/voice → generate flow."""

import logging
import os
import tempfile
import uuid
from typing import Optional

import httpx
import sentry_sdk
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.avatar import Avatar, AvatarPhase, AvatarStatus, AvatarType
from models.user import User
from models.voice_corpus import VoiceCorpusEntry
from routers.auth import get_current_user
from services.r2_storage import get_r2_storage_service
from services.creative_models import (
    CREATIVE_DESCRIPTION_MODEL,
    log_creative_model_use,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/avatar/clone", tags=["clone-pipeline"])

FACE_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"}
VOICE_ALLOWED_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v", ".mp3", ".wav", ".m4a", ".aac"}
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateCloneRequest(BaseModel):
    name: Optional[str] = ""
    target_audience: Optional[dict] = None
    gender: Optional[str] = None
    description: Optional[str] = None
    body_description: Optional[str] = None
    style_preset: Optional[str] = None
    imperfections: Optional[list[str]] = None


class SelectFaceRequest(BaseModel):
    face_url: str
    r2_key: Optional[str] = None


class DescribeFaceRequest(BaseModel):
    avatar_id: str
    face_image_url: str


class GenerateRequest(BaseModel):
    test_script: Optional[str] = None


# ---------------------------------------------------------------------------
# POST /api/avatar/clone/create
# ---------------------------------------------------------------------------

@router.post("/create", status_code=201)
async def create_clone_avatar(
    req: CreateCloneRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a clone avatar with setup data (audience, gender, presets)."""
    try:
        avatar_id = f"avt_{uuid.uuid4().hex[:12]}"
        avatar = Avatar(
            id=avatar_id,
            user_id=user.id,
            type=AvatarType.CLONE,
            status=AvatarStatus.PROCESSING,
            name=req.name or "Clone Avatar",
            description=req.description,
            body_description=req.body_description,
            gender=req.gender,
            target_audience=req.target_audience,
            style_preset=req.style_preset,
            source_platform="upload",
            progress_step="Waiting for face and voice upload",
            progress_percent=0,
        )
        db.add(avatar)
        await db.commit()
        return {"avatar_id": avatar_id}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Failed to create clone avatar")


# ---------------------------------------------------------------------------
# POST /api/avatar/clone/upload-face
# ---------------------------------------------------------------------------

@router.post("/upload-face")
async def upload_face(
    avatar_id: str = Form(...),
    file: UploadFile = File(...),
    extract_voice: str = Form("false"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload an image or video for face extraction.

    - Image: saved directly as single face candidate.
    - Video: extract 8 evenly-spaced frames, run MediaPipe face detection,
      return scored candidates.
    - If extract_voice=true and file is video, also extract audio and submit
      to voice corpus pipeline.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    original_name = file.filename or "upload.bin"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in FACE_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(FACE_ALLOWED_EXTENSIONS))
        raise HTTPException(400, f"File type {ext} not allowed. Use: {allowed}")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024 * 1024)} MB.")

    r2 = get_r2_storage_service()
    is_image = ext in {".jpg", ".jpeg", ".png", ".webp"}

    if is_image:
        # Validate actual file bytes, not just the claimed extension.
        # Browsers/phones sometimes save AVIF/HEIC files with a .png/.jpg name.
        header = data[:32]
        is_valid_png = header.startswith(b"\x89PNG\r\n\x1a\n")
        is_valid_jpeg = header.startswith(b"\xff\xd8\xff")
        is_valid_webp = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        is_avif_or_heic = b"ftypavif" in header or b"ftypheic" in header or b"ftypheix" in header or b"ftypmif1" in header

        if not (is_valid_png or is_valid_jpeg or is_valid_webp):
            logger.warning(
                "upload-face rejected for avatar %s: claimed ext=%s but real format mismatch, header=%s",
                avatar_id, ext, header.hex(),
            )
            if is_avif_or_heic:
                raise HTTPException(
                    400,
                    "This is a HEIC/AVIF image. Please upload JPG or PNG and try again.",
                )
            raise HTTPException(400, "File content doesn't match a supported image format. Use JPG, PNG, or WebP.")

    should_extract_voice = extract_voice.lower() in ("true", "1", "yes") and not is_image

    try:
        if is_image:
            result = await _handle_image_face(data, ext, avatar, user, r2, db)
        else:
            result = await _handle_video_face(data, ext, avatar, user, r2, db)

        # Extract voice from video if requested
        if should_extract_voice:
            try:
                voice_entry_id = await _extract_voice_from_video(
                    data, ext, avatar, user, r2, db,
                )
                result["voice_corpus_entry_id"] = voice_entry_id
                result["voice_extraction"] = "processing"
            except Exception as ve:
                sentry_sdk.capture_exception(ve)
                logger.warning(
                    "Voice extraction from face video failed for avatar %s: %s",
                    avatar.id, ve,
                )
                result["voice_extraction"] = "failed"

        return result
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Face upload processing failed")


async def _handle_image_face(data, ext, avatar, user, r2, db):
    """Process uploaded image as a single face candidate."""
    from services.face_extraction import detect_face_in_image

    has_face, confidence = detect_face_in_image(data)
    if not has_face:
        raise HTTPException(
            400,
            "No face detected in the uploaded image. Please upload a clear photo with a visible face.",
        )

    key = f"creators/{user.id}/avatar/{avatar.id}/candidates/face_upload{ext}"
    await r2.upload_bytes(data, key, _image_content_type(ext))

    url = r2.get_public_url(key)
    candidate = {"url": url, "r2_key": key, "score": float(confidence)}

    avatar.candidate_frames = [key]
    avatar.candidate_scores = [float(confidence)]
    await db.commit()

    return {"candidates": [candidate], "source_type": "image"}


async def _handle_video_face(data, ext, avatar, user, r2, db):
    """Process uploaded video: extract frames, detect faces, return candidates."""
    from services.face_extraction import extract_top_faces
    from services.face_extraction import upload_candidate_frames

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, f"input{ext}")
        with open(video_path, "wb") as f:
            f.write(data)

        # Extract top face candidates using existing face extraction pipeline
        frames = extract_top_faces(video_path, max_faces=8, sample_fps=2.0)

        if not frames:
            raise HTTPException(
                400,
                "No faces detected in the uploaded video. Please upload a video with a clearly visible face.",
            )

        # Upload candidates to R2
        keys = await upload_candidate_frames(frames, avatar.id, user.id)

        candidates = []
        scores = []
        for i, (frame, key) in enumerate(zip(frames, keys)):
            url = r2.get_public_url(key)
            score = float(frame.get("score", 0.0))
            candidates.append({"url": url, "r2_key": key, "score": score})
            scores.append(score)

        avatar.candidate_frames = keys
        avatar.candidate_scores = scores
        avatar.status = AvatarStatus.FACE_CANDIDATES_READY
        await db.commit()

        return {"candidates": candidates, "source_type": "video"}


async def _extract_voice_from_video(
    data: bytes, ext: str, avatar, user, r2, db,
) -> str:
    """Extract audio from a face video and submit it to voice corpus processing.

    Reuses the same audio extraction logic as upload-voice.
    Returns the voice corpus entry ID.
    """
    upload_data, upload_ext = await _extract_audio_for_corpus(data, ext)
    content_type = "audio/wav"

    entry_id = f"vc_{uuid.uuid4().hex[:16]}"
    r2_key = f"creators/{user.id}/avatars/{avatar.id}/voice-corpus/{entry_id}{upload_ext}"
    await r2.upload_bytes(upload_data, r2_key, content_type)

    entry = VoiceCorpusEntry(
        id=entry_id,
        avatar_id=avatar.id,
        source_type="uploaded",
        audio_r2_key=r2_key,
        status="pending",
    )
    db.add(entry)
    await db.commit()

    from tasks.voice_corpus import process_voice_corpus_entry
    process_voice_corpus_entry.delay(entry_id)

    return entry_id


# ---------------------------------------------------------------------------
# POST /api/avatar/clone/upload-voice
# ---------------------------------------------------------------------------

@router.post("/upload-voice")
async def upload_voice(
    avatar_id: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload audio or video for voice corpus extraction.

    - Audio: uploaded directly, processed through isolation + transcription.
    - Video: audio extracted via ffmpeg, then same pipeline.

    Returns immediately with status 'processing'. Frontend polls voice corpus list.
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    original_name = file.filename or "upload.bin"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in VOICE_ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(VOICE_ALLOWED_EXTENSIONS))
        raise HTTPException(400, f"File type {ext} not allowed. Use: {allowed}")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024 * 1024)} MB.")

    r2 = get_r2_storage_service()
    entry_id = f"vc_{uuid.uuid4().hex[:16]}"

    try:
        is_video = ext in {".mp4", ".mov", ".webm", ".m4v"}

        if is_video:
            # Extract audio from video first, upload the extracted audio
            upload_data, upload_ext = await _extract_audio_for_corpus(data, ext)
            content_type = "audio/wav"
        else:
            upload_data = data
            upload_ext = ext
            content_type = _audio_content_type(ext)

        # Upload to R2 under voice-corpus path
        r2_key = f"creators/{user.id}/avatars/{avatar.id}/voice-corpus/{entry_id}{upload_ext}"
        await r2.upload_bytes(upload_data, r2_key, content_type)

        # Create voice corpus entry
        entry = VoiceCorpusEntry(
            id=entry_id,
            avatar_id=avatar_id,
            source_type="uploaded",
            audio_r2_key=r2_key,
            status="pending",
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)

        # Dispatch Celery task for processing (diarization, isolation, transcription)
        from tasks.voice_corpus import process_voice_corpus_entry
        process_voice_corpus_entry.delay(entry_id)

        return {
            "corpus_entry_id": entry_id,
            "status": "processing",
        }
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Voice upload processing failed")


async def _extract_audio_for_corpus(data: bytes, ext: str) -> tuple[bytes, str]:
    """Extract audio from video bytes, return (audio_bytes, '.wav')."""
    from services.media_processing import extract_audio_from_video

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, f"input{ext}")
        audio_path = os.path.join(tmpdir, "extracted.wav")
        with open(video_path, "wb") as f:
            f.write(data)

        await extract_audio_from_video(video_path, audio_path)

        with open(audio_path, "rb") as f:
            audio_data = f.read()

    return audio_data, ".wav"


# ---------------------------------------------------------------------------
# PATCH /api/avatar/clone/{avatar_id}/select-face
# ---------------------------------------------------------------------------

@router.patch("/{avatar_id}/select-face")
async def select_face(
    avatar_id: str,
    req: SelectFaceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Select a face candidate and persist as the avatar's face_ref."""
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    r2 = get_r2_storage_service()

    try:
        face_ref_key = f"creators/{user.id}/avatar/{avatar.id}/face_ref.jpg"

        if req.r2_key:
            # Copy from existing R2 key to permanent face_ref location
            src_url = r2.get_public_url(req.r2_key)
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(src_url)
                resp.raise_for_status()
                face_data = resp.content
        else:
            # Download from URL
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(req.face_url)
                resp.raise_for_status()
                face_data = resp.content

        await r2.upload_bytes(face_data, face_ref_key, "image/jpeg")
        avatar.face_ref_key = face_ref_key
        await db.commit()

        return {
            "status": "ok",
            "face_ref_key": face_ref_key,
            "face_url": r2.get_public_url(face_ref_key),
        }
    except HTTPException:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("select_face failed for avatar %s: %s", avatar_id, e, exc_info=True)
        raise HTTPException(500, "Failed to select face")


# ---------------------------------------------------------------------------
# POST /api/avatar/clone/{avatar_id}/generate
# ---------------------------------------------------------------------------

@router.post("/{avatar_id}/generate")
async def generate_clone(
    avatar_id: str,
    req: GenerateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger the render pipeline using corpus voice + selected face.

    Prerequisites: face_ref_key must be set, voice corpus must have at least
    one 'ready' entry (or voice_id must already be set from prior cloning).
    """
    avatar = await db.get(Avatar, avatar_id)
    if not avatar or avatar.user_id != user.id:
        raise HTTPException(404, "Avatar not found")

    logger.info(
        "generate called for avatar %s: face_ref_key=%s, voice_id=%s, status=%s",
        avatar_id, avatar.face_ref_key, avatar.voice_id, avatar.status,
    )

    if not avatar.face_ref_key:
        logger.warning("generate rejected for avatar %s: face_ref_key is not set", avatar_id)
        raise HTTPException(400, "No face selected. Please upload and select a face first.")

    # Check voice readiness
    from sqlalchemy import select, func
    ready_count_result = await db.execute(
        select(func.count())
        .select_from(VoiceCorpusEntry)
        .where(
            VoiceCorpusEntry.avatar_id == avatar_id,
            VoiceCorpusEntry.status == "ready",
        )
    )
    ready_count = ready_count_result.scalar() or 0

    logger.info(
        "generate voice check for avatar %s: voice_id=%s, ready_voice_corpus_count=%d",
        avatar_id, avatar.voice_id, ready_count,
    )

    if not avatar.voice_id and ready_count == 0:
        logger.warning(
            "generate rejected for avatar %s: no voice_id and no ready VoiceCorpusEntry rows",
            avatar_id,
        )
        raise HTTPException(400, "Voice not ready. Please upload voice audio and wait for processing.")
    # Save test script
    test_script = req.test_script or avatar.test_script or (
        f"Hi everyone! I'm {avatar.name or 'your host'}, "
        "and I'm so excited to show you some amazing products today!"
    )
    avatar.test_script = test_script
    avatar.locked_test_script = test_script

    # Auto-detect language from voice preview text
    try:
        from langdetect import detect as langdetect_detect
        avatar.detected_language = langdetect_detect(test_script)
    except Exception as lang_err:
        import sentry_sdk as _sentry
        _sentry.capture_exception(lang_err)
        avatar.detected_language = None

    avatar.status = AvatarStatus.PROCESSING
    avatar.active_phase = AvatarPhase.VOICE if not avatar.voice_id else AvatarPhase.RENDER
    avatar.progress_step = "Generating preview video..."
    avatar.progress_percent = 10
    await db.commit()

    try:
        # Dispatch the generation pipeline
        from tasks.generate_avatar import generate_clone_preview_task
        generate_clone_preview_task.delay(avatar_id=avatar_id, user_id=user.id)

        return {"status": "processing", "avatar_id": avatar_id}
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(500, "Failed to start generation pipeline")


# ---------------------------------------------------------------------------
# POST /api/avatar/clone/describe-face
# ---------------------------------------------------------------------------

@router.post("/describe-face")
async def describe_face(
    req: DescribeFaceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Use vision model to describe the person in the selected face image.

    Returns auto-generated name, description, and body_description based on
    the face snapshot. Saves them to the avatar record.
    """
    logger.info("describe-face called with avatar_id=%s, face_image_url=%s", req.avatar_id, req.face_image_url)
    avatar = await db.get(Avatar, req.avatar_id)
    if not avatar or avatar.user_id != user.id:
        logger.warning("Avatar not found or not owned by user: avatar_id=%s, user_id=%s", req.avatar_id, user.id)
        raise HTTPException(404, "Avatar not found")
    logger.info("Found avatar: %s", avatar.id)

    try:
        # Download face image and encode as base64
        import base64

        logger.info("Downloading face image from %s", req.face_image_url)
        async with httpx.AsyncClient(timeout=60) as client:
            img_resp = await client.get(req.face_image_url)
            img_resp.raise_for_status()
            image_data = img_resp.content
        logger.info("Downloaded face image, size: %d bytes", len(image_data))

        # Detect real image format from file signature — don't trust the URL extension
        if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
        elif image_data.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
        elif image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
            content_type = "image/webp"
        else:
            logger.error(
                "Unrecognized image format for avatar %s, first 16 bytes: %s",
                req.avatar_id, image_data[:16].hex(),
            )
            raise HTTPException(400, "Unsupported or corrupted image format")

        b64_image = base64.b64encode(image_data).decode("utf-8")
        logger.info("Detected content-type from file signature: %s", content_type)

        # Call vision model via OpenRouter
        from config import settings as app_settings

        api_key = app_settings.OPENROUTER_API_KEY
        vision_model = CREATIVE_DESCRIPTION_MODEL
        log_creative_model_use("clone_describe_face", vision_model)
        logger.info("Using vision model: %s, API key present: %s", vision_model, "yes" if api_key else "NO")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this person's appearance for use as a social media avatar description. "
                            "Include: apparent age range, gender, ethnicity, hair color and style, eye color if visible, "
                            "skin tone, notable facial features, expression, and clothing if visible. "
                            "Be specific, vivid, and natural-sounding — like a casting director's notes. "
                            "Also suggest a fitting first name that matches the person's appearance.\n\n"
                            "Respond ONLY with valid JSON in this exact format:\n"
                            '{"name": "string", "description": "string", "body_description": "string"}\n\n'
                            "The description should be 2-3 sentences about the person's face and vibe. "
                            "The body_description should describe their build, posture, and clothing style in 1-2 sentences."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{content_type};base64,{b64_image}",
                        },
                    },
                ],
            }
        ]

        logger.info("Calling OpenRouter API...")
        logger.info(
            "Request payload (image truncated): model=%s, max_tokens=%s, image_size_b64=%d",
            vision_model, 1024, len(b64_image),
        )
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "https://luminacast.ai",
                    "X-Title": "Luminacast Omni",
                    "Content-Type": "application/json",
                },
                json={
                    "model": vision_model,
                    "messages": messages,
                    "max_tokens": 1024,
                    # "temperature": 0.7,
                },
            )
            logger.info("OpenRouter response status: %d", resp.status_code)
            if resp.status_code >= 400:
                logger.error("OpenRouter error response body: %s", resp.text)
            resp.raise_for_status()
            result = resp.json()
        logger.info("OpenRouter response received, keys: %s", list(result.keys()))

        raw_content = result["choices"][0]["message"]["content"]
        logger.info("Raw model response (first 200 chars): %s", raw_content[:200])

        # Parse JSON from response (handle markdown code blocks)
        import json as json_mod
        import re

        cleaned = raw_content.strip()
        # Strip markdown code fences if present
        md_match = re.search(r"```(?:json)?\s*\n?(.*?)```", cleaned, re.DOTALL)
        if md_match:
            cleaned = md_match.group(1).strip()
            logger.info("Stripped markdown code fences")

        logger.info("Cleaned JSON to parse: %s", cleaned)
        parsed = json_mod.loads(cleaned)
        logger.info("Parsed JSON successfully: %s", parsed)

        generated_name = parsed.get("name", "Avatar")
        generated_description = parsed.get("description", "")
        generated_body_description = parsed.get("body_description", "")

        # Save to avatar record
        logger.info("Saving to avatar record...")
        avatar.name = generated_name
        avatar.description = generated_description
        avatar.body_description = generated_body_description
        await db.commit()
        logger.info("Saved to DB successfully")

        logger.info(
            "describe-face completed for avatar %s: name=%s",
            req.avatar_id,
            generated_name,
        )

        return {
            "name": generated_name,
            "description": generated_description,
            "body_description": generated_body_description,
        }
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        sentry_sdk.capture_exception(e)
        logger.error(
            "describe-face OpenRouter call failed for avatar %s: status=%s, body=%s",
            req.avatar_id, e.response.status_code, e.response.text, exc_info=True,
        )
        raise HTTPException(500, "Failed to describe face")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error("describe-face failed for avatar %s: %s", req.avatar_id, e, exc_info=True)
        raise HTTPException(500, "Failed to describe face")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _image_content_type(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")


def _audio_content_type(ext: str) -> str:
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
    }.get(ext, "application/octet-stream")

# Clone Pipeline Redesign Plan

**Date:** 2026-04-15
**Goal:** Replace the fragmented multi-page clone flow with a unified 3-page flow matching the AI Avatar UX.

---

## Current State (What Exists)

### Frontend: CloneFlow.tsx (~2,990 lines)
**11 steps:** input → upload → record → videos → player → segment → faces → customize → script → review → creating

| Step | Purpose | API Endpoints |
|------|---------|---------------|
| input | Name + method (social/upload/record) | — |
| upload | File upload with progress | `POST /avatar/upload-video` |
| record | Camera recording | — |
| videos | TikTok video gallery (4x4 grid) | `POST /fetch-videos`, `POST /download-video` |
| player | Video scrubber + frame capture | `POST /process-segment` |
| segment | Time segment picker | — |
| faces | Face candidate selection | `GET /face-candidates` (polling) |
| customize | AI face edit (FLUX Kontext) | `POST /{id}/edit-frame` |
| script | Test script + voice corpus | `POST /voice-corpus/upload` |
| review | Preview video approval | `GET /status/{id}`, `POST /{id}/approve` |
| creating | Pipeline status display | `GET /status/{id}` (polling) |

### Backend Endpoints (avatar.py, clone_scout.py, voice_corpus.py)
- `POST /avatar/create` — Create avatar record
- `POST /avatar/fetch-videos` — Fetch TikTok videos
- `POST /avatar/download-video` — Download via yt-dlp
- `POST /avatar/{id}/process-segment` — Kick off parallel image + voice pipelines
- `GET /avatar/{id}/face-candidates` — Poll for extracted face candidates
- `POST /avatar/{id}/upload-frame` — Upload manually captured frame
- `POST /avatar/{id}/select-face` — Persist face selection
- `POST /avatar/{id}/upload-face` — Upload face ref directly
- `POST /avatar/{id}/edit-frame` — Edit face with FLUX Kontext
- `POST /avatar/upload-video` — Upload user video file
- `POST /{avatar_id}/voice-corpus/upload` — Voice corpus upload
- `POST /avatar/clone/scout` — Start TikTok scout scan
- `GET /avatar/clone/scout/{scan_id}` — Poll scout progress
- `POST /avatar/clone/scout/{scan_id}/select-video` — Select video from scout

### Problems
1. **11 steps** — user navigates input → videos → player → segment → faces → customize → script → review
2. **Voice corpus upload races with generate task** — voice processing may not finish before render
3. **TikTok video gallery is a separate flow** from direct upload — different UX paths
4. **No target audience / style setup** — clone avatars miss the persona context AI avatars get
5. **Face extraction only works from video segments** — no direct photo upload for face

---

## New Design (3 Pages)

### Page 1: CloneSetupPhase
Mirrors AI Avatar's SetupPhase. Reuses the same components.

**Sections:**
1. **Target Audience** — Age range pills (7 options), interests multi-select (33 options), auto-generated description, "Inspire Me" button
2. **Avatar Identity** — Name (auto-generated), Gender pills (F/M/NB), Style presets grid (12 presets from avatarStyles.ts), "Make It Real" chips (16 options)
3. **Auto-generated fields** — Avatar description, body description (both overrideable)

**API Calls:**
- `POST /api/avatar/clone/create` — Create clone avatar with target_audience, gender, presets
- `POST /api/avatar/ai/rewrite-audience-description` — Reuse existing endpoint
- `POST /api/avatar/ai/rewrite-avatar-identity` — Reuse existing endpoint

**Exit condition:** "Continue" button → calls create endpoint, stores avatar_id, advances to Page 2.

### Page 2: CloneUploadPhase
Two-column layout. LEFT: uploads + status. RIGHT: face candidate grid.

**LEFT Column:**

1. **Face Upload Card**
   - (+) button with camera icon
   - Accepts `image/*` or `video/*`
   - If image: upload directly → single face candidate displayed in RIGHT grid
   - If video: upload → backend extracts 8 frames via ffmpeg → face candidates in RIGHT grid
   - Status indicator: idle → uploading → processing → ready
   - API: `POST /api/avatar/clone/upload-face`

2. **Voice Upload Card**
   - (+) button with microphone icon
   - Accepts `audio/*` or `video/*`
   - If audio file: upload directly to voice corpus → isolation + transcription pipeline
   - If video file: upload → backend extracts audio via ffmpeg → same pipeline
   - Status indicator: idle → uploading → processing → ready
   - Shows transcript preview when ready
   - API: `POST /api/avatar/clone/upload-voice`
   - Also reuses existing `VoiceCorpusTab` component (compact mode) for record/upload

3. **Preview Text Input**
   - Textarea for test script (default: "Hi everyone! I'm {name}, and I'm so excited to show you some amazing products today!")
   - Max 500 chars
   - Character counter

4. **Continue Button**
   - Enabled when BOTH face and voice are ready
   - Calls `PATCH /api/avatar/clone/{id}/select-face` with chosen face
   - Then triggers `POST /api/avatar/clone/{id}/generate`

**RIGHT Column:**

5. **Face Candidates Grid**
   - 4x2 grid (8 slots) — same layout as AI Avatar FacePhase
   - Shows extracted frames from video, or single uploaded photo
   - Click to select face (checkmark overlay, accent border)
   - Face preview modal on click (navigation with arrows, enter to select)
   - Shows placeholder slots when empty

### Page 3: ClonePreviewPhase
Same as AI Avatar PreviewPhase — show progress, then video preview with approve.

**Sections:**
1. **PipelineProgressView** — Reuse existing component, shows step-by-step progress
2. **Video Preview** — 9:16 aspect ratio video player when ready
3. **Approve / Re-edit buttons** — Same as AI Avatar flow

**API Calls:**
- `GET /api/avatar/status/{id}` — Poll for status (every 3s)
- `GET /api/avatar/{id}/render-jobs` — Pipeline progress steps
- `POST /api/avatar/{id}/approve` — Approve avatar

---

## Backend Changes

### New Endpoints

#### 1. `POST /api/avatar/clone/create`
Create clone avatar with setup data (audience, gender, presets).

**Request:**
```json
{
  "name": "string",
  "target_audience": {"age_range": "25-34", "interests": ["Fitness"], "description": "..."},
  "gender": "female",
  "description": "string",
  "body_description": "string (optional)",
  "style_preset": "string (optional)",
  "imperfections": ["phone_selfie"]
}
```

**Response:**
```json
{"avatar_id": "avt_..."}
```

**Implementation:** Similar to `POST /avatar/ai/create` + `POST /avatar/ai/save-setup` combined. Sets `type=CLONE`, `status=PROCESSING`.

#### 2. `POST /api/avatar/clone/upload-face`
Accept image or video, return face candidates.

**Request:** `multipart/form-data` with `file` field + `avatar_id` field.

**Logic:**
- Detect file type by MIME or extension
- **If image** (`image/*`):
  - Upload to R2: `creators/{user_id}/avatar/{avatar_id}/face_upload.{ext}`
  - Run MediaPipe face detection to validate face exists
  - Return as single candidate
- **If video** (`video/*`):
  - Upload to R2 temp
  - Use `extract_top_faces()` from `face_extraction.py` — 8 frames, MediaPipe + vision filtering
  - Upload candidate frames to R2
  - Return 8 candidates

**Response:**
```json
{
  "candidates": [
    {"url": "https://cdn.../face_0.jpg", "r2_key": "creators/.../face_0.jpg", "score": 0.95}
  ],
  "source_type": "image" | "video"
}
```

#### 3. `POST /api/avatar/clone/upload-voice`
Accept audio or video, process through voice pipeline.

**Request:** `multipart/form-data` with `file` field + `avatar_id` field.

**Logic:**
- Detect file type
- **If video**: extract audio via ffmpeg (`-vn -acodec pcm_s16le -ar 16000 -ac 1`)
- **If audio**: normalize via ffmpeg loudnorm filter
- Upload to voice corpus
- Trigger async processing: diarization → speaker isolation → Whisper transcription
- Return immediately with `status: "processing"`

**Response:**
```json
{
  "corpus_entry_id": "vc_...",
  "status": "processing"
}
```

Frontend polls `GET /{avatar_id}/voice-corpus` to check status.

#### 4. `PATCH /api/avatar/clone/{id}/select-face`
Select a face candidate and persist as face_ref.

**Request:**
```json
{"face_url": "https://cdn.../face_0.jpg", "r2_key": "creators/.../face_0.jpg"}
```

**Logic:**
- Download face from URL (or copy from R2 key)
- Re-upload to permanent location: `creators/{user_id}/avatar/{avatar_id}/face_ref.jpg`
- Set `avatar.face_ref_key`

**Response:**
```json
{"status": "ok", "face_ref_key": "creators/.../face_ref.jpg"}
```

#### 5. `POST /api/avatar/clone/{id}/generate`
Trigger the render pipeline. Uses corpus voice + selected face.

**Request:**
```json
{"test_script": "Hi everyone! I'm...", "preview_text": "optional"}
```

**Logic:**
1. Verify face_ref_key is set
2. Verify voice corpus has at least 1 ready entry (or voice_id already set)
3. If no voice_id yet: trigger Fish Audio voice cloning from corpus audio
4. Wait for voice_id (or return immediately and handle async)
5. Generate TTS audio with voice_id + test_script
6. Submit InfiniteTalk render job: face_ref + audio
7. Set avatar status to PROCESSING, return pipeline job IDs

**Response:**
```json
{"status": "processing", "avatar_id": "avt_..."}
```

---

## What to Keep vs Rewrite

### KEEP (reuse as-is)
- `avatarStyles.ts` — Style presets, prompt fragments, "Make It Real" chips
- `AvatarIdentityPanel.tsx` — Sidebar panel (used in Page 2/3)
- `StepIndicator.tsx` — Step progress bar
- `PipelineProgressView.tsx` — Pipeline progress (Page 3)
- `VoiceCorpusTab.tsx` — Voice upload/record UI (compact mode, embedded in Page 2)
- `face_extraction.py` — Face candidate extraction from video
- `voice_corpus_processor.py` — Audio isolation + transcription pipeline
- `voice_corpus.py` router — Existing voice corpus CRUD endpoints
- All AI Avatar endpoints — NOT MODIFIED

### REWRITE (replace entirely)
- `CloneFlow.tsx` — Replace 11-step monolith with 3-phase modular component

### NEW
- `CloneSetupPhase` component — Page 1 (extract from AI Avatar's SetupPhase pattern)
- `CloneUploadPhase` component — Page 2 (new layout with face + voice uploads)
- `ClonePreviewPhase` component — Page 3 (thin wrapper around PipelineProgressView)
- `POST /api/avatar/clone/create` endpoint
- `POST /api/avatar/clone/upload-face` endpoint
- `POST /api/avatar/clone/upload-voice` endpoint
- `PATCH /api/avatar/clone/{id}/select-face` endpoint
- `POST /api/avatar/clone/{id}/generate` endpoint
- `media_processing.py` — Shared async ffmpeg utils

---

## Phase D: Shared Media Processing Utils

**New file:** `backend/orchestrator/services/media_processing.py`

```python
async def extract_audio_from_video(video_path: str, output_path: str) -> str:
    """Extract audio track from video using ffmpeg. Returns output_path."""

async def normalize_audio(audio_path: str, output_path: str) -> str:
    """Normalize audio loudness using ffmpeg loudnorm filter."""

async def extract_frames_from_video(video_path: str, num_frames: int = 8) -> list[str]:
    """Extract N evenly-spaced frames from video. Returns list of frame file paths."""

async def get_media_duration(file_path: str) -> float:
    """Get duration in seconds using ffprobe."""

async def get_media_type(file_path: str) -> str:
    """Detect if file is audio or video using ffprobe."""
```

All functions use `asyncio.subprocess` for non-blocking execution.

---

## Component Architecture

```
CloneFlow.tsx (new, ~600 lines)
├── State: step ("setup" | "upload" | "preview"), avatarId, setupData
├── StepIndicator (reuse)
├── CloneSetupPhase (~300 lines)
│   ├── Target audience (age pills, interests, description)
│   ├── Gender pills
│   ├── Style presets grid (from avatarStyles.ts)
│   ├── "Make It Real" chips
│   ├── "Inspire Me" button
│   ├── Avatar name + description (auto-generated)
│   └── "Continue" → POST /clone/create
├── CloneUploadPhase (~400 lines)
│   ├── LEFT column:
│   │   ├── Face upload card (+) → POST /clone/upload-face
│   │   ├── Voice upload card (+) → POST /clone/upload-voice
│   │   │   └── VoiceCorpusTab (reuse, compact mode)
│   │   ├── Preview text input
│   │   └── "Continue" button (face + voice ready)
│   └── RIGHT column:
│       └── Face candidates grid (8 slots)
│           └── Face preview modal (reuse pattern from AI Avatar)
└── ClonePreviewPhase (~150 lines)
    ├── PipelineProgressView (reuse)
    ├── Video preview (9:16)
    └── Approve / Re-edit buttons
```

---

## Migration Notes

1. **Route stays the same:** `/my-avatar/clone` → renders new CloneFlow
2. **Setup.tsx integration unchanged:** `showCloneFlow` flag still works
3. **Old CloneFlow.tsx is completely replaced** — no incremental migration
4. **Resume functionality:** `?resume=avt_xxx` still works — read avatar status, jump to correct phase
5. **Existing clone avatars:** Already-created avatars keep working — the new flow only changes the creation UX
6. **Voice corpus table unchanged** — same model, same API, just called from new upload endpoint
7. **Avatar model unchanged** — face_ref_key, voice_id, status all used the same way

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| New endpoints break existing avatars | New endpoints are /clone/ prefixed, don't touch existing |
| Voice processing not done before generate | Generate endpoint checks voice corpus status, waits or returns error |
| Face extraction from image fails | Validate face detection before returning candidate |
| Large video upload timeout | Existing 500MB limit, chunked upload already in place |
| AI Avatar flow accidentally modified | All changes in separate files; AI Avatar imports untouched |

---

## Implementation Order

1. **Phase A** (this document) — Audit + plan
2. **Phase D** — `media_processing.py` (needed by Phase C)
3. **Phase C** — Backend endpoints (needed by Phase B)
4. **Phase B** — Frontend rewrite
5. **Build verification** — `npm run build`, `tsc --noEmit`
6. **Commit**

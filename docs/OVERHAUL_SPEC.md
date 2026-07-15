# Avatar Pipeline Overhaul Spec

## Issue 1: Voice Cloning Broken
The voice cloning step passes a TikTok web page URL (`https://www.tiktok.com/@user/video/123`) to Fish Audio, 
which needs a direct audio file URL. Fish Audio silently fails and the pipeline falls back to a default "friendly" voice.

### Fix
In `tasks/generate_avatar.py` clone pipeline Step 3 (around line 544):
1. Check if user uploaded a voice sample (`avatar.voice_sample_key`). If yes, use its R2 public URL for Fish Audio clone.
2. If no user sample but TikTok videos available:
   a. Download the TikTok video via yt-dlp (same as face extraction does)
   b. Extract audio with ffmpeg: `ffmpeg -i video.mp4 -vn -acodec libmp3lame -q:a 2 audio.mp3`
   c. Upload the extracted audio to R2 at key `creators/{user_id}/avatar/{avatar_id}/voice_source.mp3`
   d. Pass the R2 **public** URL (`https://media.luminacast.com/...`) to `fish.clone_voice()`
3. If voice cloning still fails, use `VOICE_STYLE_MAP` based on avatar's voice_style or default

### Key files:
- `backend/orchestrator/tasks/generate_avatar.py` — clone pipeline Step 3
- `backend/orchestrator/services/fish_audio.py` — clone_voice method (takes audio URL)
- `backend/orchestrator/services/r2_storage.py` — has `get_public_url()` method

## Issue 2: Face Extraction Returns Frames with Text Overlays
MediaPipe finds faces but can't detect text overlays, watermarks, or profanity captions on the frames.

### Fix — Two-phase extraction

#### Phase A: Extract top 12 frames (modify `services/face_extraction.py`)
Change `extract_best_face()` to `extract_top_faces()` that returns up to 12 best-scoring face frames as JPEG bytes.
- Same scoring as before (face size 70%, sharpness 20%, confidence 10%)
- Return list of `{jpeg_bytes, score, frame_idx}` dicts
- Keep the existing download logic (yt-dlp for TikTok URLs, httpx for CDN)

New function signature:
```python
def extract_top_faces(video_path: str, max_faces: int = 12, ...) -> list[dict]:
    """Returns list of {'jpeg_bytes': bytes, 'score': float, 'frame_idx': int}"""
```

Also keep `extract_best_face()` as a wrapper that calls `extract_top_faces(max_faces=1)` and returns just the bytes.

#### Phase B: Vision LLM filter (new function in `services/face_extraction.py`)
```python
async def filter_frames_with_vision(frames: list[dict], openrouter_api_key: str) -> list[dict]:
```
1. For each frame, base64-encode the JPEG bytes
2. Send batch to Gemini Flash via OpenRouter with this prompt:
   "Score each image 1-10 for avatar suitability. Reject if: large text overlay, watermark, profanity, 
    mid-blink, extreme angle, blurry motion, multiple people. Return JSON: [{index, score, reject, reason}]"
3. Filter out rejected frames
4. Return surviving frames sorted by Vision LLM score

Use model: `google/gemini-2.5-flash` (cheap + fast + vision capable)

#### Phase C: Upload candidates to R2, return URLs
New function:
```python
async def upload_candidate_frames(frames: list[dict], avatar_id: str, user_id: str) -> list[str]:
```
Upload each surviving frame to R2 at: `creators/{user_id}/avatar/{avatar_id}/candidates/frame_{i}.jpg`
Return list of public CDN URLs.

## Issue 3: New Pipeline Flow — Two-Step Clone Process

### New avatar status: `CANDIDATES_READY`
Add to AvatarStatus enum: `CANDIDATES_READY = "candidates_ready"`

### Modified clone pipeline flow:
1. Fetch TikTok videos (if URL provided)
2. Extract top 12 face frames from video
3. Filter with Vision LLM 
4. Upload candidate frames to R2
5. Store candidate URLs in avatar record (new JSON column: `candidate_frames`)
6. Set status to `CANDIDATES_READY` — STOP HERE, wait for user selection
7. User picks a frame on frontend → calls new API endpoint
8. Pipeline continues: clone voice → persona → TTS → InfiniteTalk → ready

### New DB columns on Avatar model:
- `candidate_frames` — JSON array of public CDN URLs for candidate face frames

### New API endpoints:
1. `GET /api/avatar/{avatar_id}/candidates` — returns the candidate frame URLs
2. `POST /api/avatar/{avatar_id}/select-frame` — body: `{frame_url: string}` 
   - Sets `face_ref_key` to the selected frame
   - Triggers the remaining pipeline (voice clone → persona → TTS → InfiniteTalk)

### New Celery tasks:
1. `extract_candidates_task` — Steps 1-6 (fetch TikTok → extract → filter → upload → candidates_ready)
2. `generate_from_selection_task` — Steps 7-end (voice clone → persona → TTS → video → ready)

## Issue 4: Frontend Carousel

### In Setup.tsx, after avatar reaches `candidates_ready` status:
Show a selection UI:
- Horizontal scrollable carousel or 3-column grid
- Each candidate frame is a clickable card with a subtle border
- Selected frame gets accent border + checkmark overlay
- "Use This Frame" confirmation button below
- On confirm, call `POST /api/avatar/{id}/select-frame` with the selected URL
- Status changes to `processing` and the rest of the pipeline runs

### Avatar status display should handle `candidates_ready`:
- Show progress bar at ~30%
- Text: "Pick your best frame"
- Below: the carousel component

## File Reference
- `backend/orchestrator/tasks/generate_avatar.py` — main pipeline
- `backend/orchestrator/services/face_extraction.py` — CV extraction
- `backend/orchestrator/services/fish_audio.py` — voice cloning
- `backend/orchestrator/services/r2_storage.py` — R2 upload/URLs
- `backend/orchestrator/services/openrouter.py` — LLM calls
- `backend/orchestrator/routers/avatar.py` — API endpoints
- `backend/orchestrator/models/avatar.py` — DB model
- `frontend/companion-app/src/pages/Setup.tsx` — main UI
- `frontend/companion-app/src/lib/api.ts` — API client
- `frontend/companion-app/src/lib/types.ts` — TypeScript types

## Credentials (for OpenRouter Vision calls)
- OpenRouter API key: `process.env.OPENROUTER_API_KEY || ""`
- R2 public CDN: `https://media.luminacast.com`
- Fish Audio API key: `process.env.FISH_AUDIO_API_KEY || ""` 

## Important constraints
- yt-dlp is installed in Docker image
- ffmpeg is installed
- OpenCV + MediaPipe are installed
- The orchestrator uses Celery tasks with `asyncio.new_event_loop()` pattern
- R2 storage has `upload_bytes()`, `upload_file()`, `get_public_url()`, `get_signed_url()` methods
- DB uses SQLAlchemy async sessions with fresh factory per task call

# Clone Pipeline Redesign — Result Summary

**Date:** 2026-04-15

## What Was Done

### Phase A: Audit & Plan
- Audited CloneFlow.tsx (2,990 lines, 11 steps) and documented all pages, endpoints, and state
- Audited AIAvatarSetup.tsx (3,416 lines, 5 phases) to identify the target UX pattern
- Documented full data flow: frontend components, API endpoints, backend services, Celery tasks
- Wrote comprehensive redesign plan at `clone_pipeline_redesign_plan.md`

### Phase B: Frontend Rewrite — CloneFlow.tsx
**Replaced** the 11-step monolithic CloneFlow (~2,990 lines) with a **3-phase unified flow** (~800 lines):

1. **CloneSetupPhase** — Target audience (age/interests/description), gender pills, 12 style presets grid, 16 "Make It Real" chips, "Inspire Me" randomizer, auto-generated name/description/body_description. Reuses AI avatar's LLM rewrite endpoints.

2. **CloneUploadPhase** — Two-column layout:
   - LEFT: Face upload card (image or video), voice upload card (audio or video), VoiceCorpusTab (compact), preview text input, generate button
   - RIGHT: 8-slot face candidates grid with selection, face preview modal (keyboard navigation)
   - Status badges (idle/uploading/processing/ready) for both face and voice
   - Continue only enabled when both face and voice are ready

3. **ClonePreviewPhase** — Reuses PipelineProgressView for step-by-step progress, video preview when ready, approve button

**Reused components:** StepIndicator, PipelineProgressView, VoiceCorpusTab (compact mode), avatarStyles.ts (presets + chips)

### Phase C: Backend Endpoints
**New file:** `backend/orchestrator/routers/clone_pipeline.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/avatar/clone/create` | POST | Create clone avatar with setup data |
| `/api/avatar/clone/upload-face` | POST | Upload image/video, extract face candidates |
| `/api/avatar/clone/upload-voice` | POST | Upload audio/video, process through voice pipeline |
| `/api/avatar/clone/{id}/select-face` | PATCH | Select face candidate as face_ref |
| `/api/avatar/clone/{id}/generate` | POST | Trigger render pipeline (corpus voice + face) |

**Registered** in `main.py` as `clone_pipeline_router`.

**New Celery task:** `generate_clone_preview_task` in `tasks/generate_avatar.py` — delegates to existing `_regenerate_pipeline` which handles corpus-based voice cloning + TTS + InfiniteTalk rendering.

### Phase D: Media Processing Utils
**New file:** `backend/orchestrator/services/media_processing.py`

| Function | Purpose |
|----------|---------|
| `extract_audio_from_video()` | ffmpeg async audio extraction (16kHz mono WAV) |
| `normalize_audio()` | ffmpeg loudnorm two-pass filter |
| `extract_frames_from_video()` | N evenly-spaced JPEG frames from video |
| `get_media_duration()` | ffprobe duration detection |
| `detect_media_type()` | ffprobe audio/video stream detection |

All functions use `asyncio.subprocess` for non-blocking execution.

### API Layer
**Added** 5 new methods to `frontend/companion-app/src/lib/api.ts`:
- `cloneCreate()`, `cloneUploadFace()`, `cloneUploadVoice()`, `cloneSelectFace()`, `cloneGenerate()`

## Build Verification
- `npx tsc --noEmit` — No new TypeScript errors (pre-existing errors in MyCasts/MyVideos/Setup are unrelated)
- `npx vite build` — Successful build in 15.37s

## Files Changed

| File | Action | Lines |
|------|--------|-------|
| `clone_pipeline_redesign_plan.md` | Created | ~250 |
| `clone_pipeline_result.md` | Created | this file |
| `frontend/.../CloneFlow.tsx` | Rewritten | ~800 (was ~2,990) |
| `frontend/.../api.ts` | Modified | +35 lines (new clone API methods) |
| `backend/.../routers/clone_pipeline.py` | Created | ~300 |
| `backend/.../services/media_processing.py` | Created | ~190 |
| `backend/.../tasks/generate_avatar.py` | Modified | +30 lines (new Celery task) |
| `backend/.../main.py` | Modified | +2 lines (router registration) |

## What Was NOT Changed
- AIAvatarSetup.tsx — untouched
- Existing clone scout endpoints — untouched
- Existing voice corpus router — untouched
- Avatar model — no schema changes, no migrations needed
- All existing avatar endpoints — untouched

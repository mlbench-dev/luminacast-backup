# CloneFlow Restoration Audit

**Date:** 2026-04-15

## Current CloneFlow Phase Structure

The current `CloneFlow.tsx` (~1200 lines) has **3 phases**:

```
upload → setup → preview
```

| Phase | Component | Description |
|-------|-----------|-------------|
| `upload` | `CloneUploadPhase` | Creates avatar on mount, face upload (image/video), voice upload (audio/video), face candidate grid (8 slots), face selection, auto-describe on Continue |
| `setup` | `CloneSetupPhase` | Target audience (age/interests), gender pills, style presets, Make It Real chips, Inspire Me, name/description/body_description (pre-filled from face auto-describe) |
| `preview` | `ClonePreviewPhase` | PipelineProgressView, video preview, approve button |

**Key current flow:**
1. Avatar created on mount in CloneUploadPhase via `cloneCreate({})`
2. User uploads face file → candidates extracted → selects one
3. User uploads voice file → corpus processing (polled)
4. Continue: `cloneSelectFace` + `cloneDescribeFace` → passes initial data to Setup
5. Setup: `aiSaveSetup` + `cloneGenerate` → advances to Preview

## Target Structure (4 phases)

```
upload (with method picker) → select → setup → preview
```

## Backend Endpoints Confirmed Working

### Clone Pipeline (`/api/avatar/clone/...`)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/create` | POST | Working | Creates minimal CLONE avatar, returns `avatar_id` |
| `/upload-face` | POST | Working | Form: `avatar_id` + `file`. Image → single candidate. Video → 8 candidates. |
| `/upload-voice` | POST | Working | Form: `avatar_id` + `file`. Dispatches Celery processing task. |
| `/{avatar_id}/select-face` | PATCH | Working | JSON: `{face_url, r2_key?}`. Copies to permanent face_ref location. |
| `/describe-face` | POST | Working | JSON: `{avatar_id, face_image_url}`. Uses Gemini Flash 2.5 (to be switched to Claude Sonnet). Returns `{name, description, body_description}`. |
| `/{avatar_id}/generate` | POST | Working | JSON: `{test_script?}`. Dispatches `generate_clone_preview_task`. |

### Clone Scout (`/api/avatar/clone/scout/...`)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/scout` | POST | Working | JSON: `{tiktok_handle}`. Creates scan, dispatches Celery task. Returns `{scan_id}`. |
| `/scout/{scan_id}` | GET | Working | Returns scan status + video list with thumbnails, full_body_segments, duration. |
| `/scout/{scan_id}/select-video` | POST | Working | JSON: `{tiktok_video_id, segment_start_ms, segment_end_ms}`. Creates avatar, dispatches `process_image_pipeline_task`. Returns `{avatar_id}`. **Note:** This creates a NEW avatar — different from clone pipeline `/create`. |

### Avatar Router (`/api/avatar/...`)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/{avatar_id}/process-segment` | POST | Working | JSON: `{video_r2_key, start_seconds, end_seconds}`. Minimum 15s segment. Kicks off image + voice pipelines in parallel. Returns avatar response. |
| `/upload-video` | POST | Working | Form: `file`. Uploads to R2, returns `{video_r2_key, video_url, duration_seconds}`. |
| `/{avatar_id}/upload-frame` | POST | Working | Form: `file`. Uploads manual frame to R2. Returns `{frame_url, r2_key}`. |

### Voice Corpus (`/api/avatar/{avatar_id}/voice-corpus/...`)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/upload` | POST | Working | Form: `file` or `source_url`. Dispatches corpus processing. |
| (list) | GET | Working | Returns `{entries: [...]}`. Used for polling voice readiness. |

## Backend Endpoints Needing Tweaks

### 1. `upload-face` needs `extract_voice=true` support

**Current:** `upload-face` only handles face extraction (image → single candidate, video → 8 candidates). It does NOT extract voice from video.

**Needed:** When `extract_voice=true` is passed as a form field, and the uploaded file is a video, also extract audio and submit it to the voice corpus pipeline (same logic as `upload-voice`). This enables the Record sub-phase to do a single upload for both face and voice.

**Change:** Add optional `extract_voice` form field to `upload-face` endpoint in `clone_pipeline.py`. When true and file is video, call the same audio extraction + corpus entry logic from `upload-voice`.

### 2. `describe-face` vision model switch

**Current:** `vision_model = "google/gemini-2.5-flash"` (line 451)

**Target:** `vision_model = "anthropic/claude-sonnet-4.6"` — latest Claude Sonnet with vision support on OpenRouter.

### 3. Scout `select-video` creates a new avatar

**Issue:** `scout/{scan_id}/select-video` creates its own avatar via `Avatar(...)`. This conflicts with the CloneFlow where avatar is created at the start of Upload phase via `cloneCreate({})`.

**Workaround for Social Media sub-phase:** Instead of using `select-video` which creates a new avatar, we can:
- Use `cloneCreate({})` at the top of Upload phase (same as current flow)
- When scout finds a video and user picks a segment, use `process-segment` on the already-created avatar (this endpoint takes `video_r2_key` + start/end seconds and works on an existing avatar)
- The scout's `select-video` endpoint will be called but we'll use the returned `avatar_id` if it differs, OR we'll modify the flow to use `process-segment` directly

**Decision:** Use `process-segment` on the existing avatar. The scout flow will be: scout → pick video → download video (already in R2 from scout) → `process-segment` with the R2 key.

## Frontend API Methods — Current State

Already in `api.ts`:
- `scoutStart(tiktokHandle)` — `POST /avatar/clone/scout`
- `scoutStatus(scanId)` — `GET /avatar/clone/scout/{scanId}`
- `scoutSelectVideo(scanId, data)` — `POST /avatar/clone/scout/{scanId}/select-video`
- `cloneCreate(data)` — `POST /avatar/clone/create`
- `cloneUploadFace(avatarId, file)` — `POST /avatar/clone/upload-face`
- `cloneUploadVoice(avatarId, file)` — `POST /avatar/clone/upload-voice`
- `cloneSelectFace(avatarId, faceUrl, r2Key?)` — `PATCH /avatar/clone/{avatarId}/select-face`
- `cloneGenerate(avatarId, testScript?)` — `POST /avatar/clone/{avatarId}/generate`
- `cloneDescribeFace(avatarId, faceImageUrl)` — `POST /avatar/clone/describe-face`
- `processSegment(id, data)` — `POST /avatar/{id}/process-segment`
- `uploadVideo(file)` — `POST /avatar/upload-video`

**All needed API methods already exist.** No new methods needed in `api.ts` except possibly adding `extract_voice` parameter to `cloneUploadFace`.

## MediaRecorder Browser Support

MediaRecorder API is widely supported:
- Chrome: 47+ (2016)
- Firefox: 25+ (2013)
- Safari: 14.1+ (2021)
- Edge: 79+ (2020)

All target browsers support `video/webm` recording. Safari may require `video/mp4` fallback. The implementation should try `video/webm;codecs=vp9` first, fall back to `video/webm`, then `video/mp4`.

## Shared Components Available

- `StepIndicator` — already used in CloneFlow, supports any step array
- `PipelineProgressView` — used in preview phase
- `VoiceCorpusTab` — compact mode for showing voice entries
- `FacePreviewModal` — face preview with navigation, already in CloneFlow
- `StatusBadge` — upload status indicator, already in CloneFlow
- `ShimmerField` — loading shimmer for auto-generated fields

## Implementation Plan Summary

1. Add `extract_voice` support to `upload-face` in `clone_pipeline.py`
2. Switch describe-face vision model to `anthropic/claude-sonnet-4.6`
3. Update `cloneUploadFace` in `api.ts` to accept optional `extractVoice` param
4. Restructure `CloneFlow.tsx`:
   - Change `CLONE_STEPS` to 4 phases: upload/select/setup/preview
   - Add method picker to `CloneUploadPhase` (Social/Upload/Record)
   - Create `CloneSocialMediaSubPhase` component
   - Modify `CloneUploadSubPhase` (voice card conditional on video face)
   - Create `CloneRecordSubPhase` component
   - Extract face selection to new `CloneSelectPhase`
   - Move `CloneSetupPhase` after select
   - Keep `ClonePreviewPhase` unchanged

# Preview Video Investigation

## Current State
The preview video "is not always working." Investigation findings from code review and Sentry analysis.

## Sentry Errors (last 24h)
- `[LUMINACAST-ORCHESTRATOR-3T]` "Rendered more hooks than during the previous render" (React #310) — **Root cause**: The monolithic AIAvatarSetupPage had 45 hooks causing conditional rendering issues during phase transitions. **Fixed** in commit 99dccf5 (phase split).
- `[LUMINACAST-ORCHESTRATOR-3P]` "TypeError: Failed to set the 'playbackRate' property on 'HTMLMediaElement'" — Video element accessed before ready. Not blocking preview generation.
- `[LUMINACAST-GPU-WORKER-3]` Port 7860 already in use (4502 occurrences) — GPU worker restart conflict. Not directly related to preview rendering, but can cause timeouts.

## Root Cause Analysis

### Failure Mode 1: Still image instead of video
**Cause**: The old `generate_from_selection_task` Celery task (in `tasks/generate_avatar.py`) generates a test video but stores it as `test_video_key`. The frontend checked `test_video_url` but if the InfiniteTalk job failed silently (timeout, wrong dimensions), it would fall through to showing the face image.

**Evidence**: InfiniteTalk on RunPod has a 10-minute queue timeout. If the GPU was already processing a job (the lock in worker.py ensures sequential processing), new jobs queue and can time out.

### Failure Mode 2: Wrong audio format
**Cause**: Fish Audio TTS sometimes returns WAV instead of MP3. InfiniteTalk expects WAV/MP3 but the upload to R2 uses `audio/mpeg` content type regardless. This can cause audio/video sync issues in the output.

**Evidence**: The Fish Audio service `_self_hosted_tts()` method returns audio in whatever format the RunPod Fish Speech worker produces. The `_fish_audio_tts()` fallback returns MP3 (msgpack format).

### Failure Mode 3: GPU worker port binding
**Cause**: `[LUMINACAST-GPU-WORKER-3]` shows 4502 occurrences of port 7860 already in use. The GPU worker fails to start properly, meaning InfiniteTalk requests timeout because there's no worker to process them.

**Evidence**: This is a deployment/restart issue — not a code bug. The old GPU worker process isn't being cleanly stopped before the new one starts.

### Failure Mode 4: Missing ffmpeg faststart
**Cause**: InfiniteTalk output video may not have the `faststart` moov atom flag, causing browsers to fail streaming the video. The browser can't seek or determine duration.

**Evidence**: The worker.py includes ffmpeg processing but doesn't always apply `-movflags +faststart`.

## Fixes Applied in This Pipeline Overhaul

1. **New endpoint `regenerate-preview-video`**: Separates preview video generation from the old Celery task. Follows a deterministic pipeline:
   - Step 1: Generate TTS from locked_test_script (single source of truth)
   - Step 2: Submit InfiniteTalk job with face image + audio
   - Step 3: Poll for completion with proper timeouts
   - Step 4: Download, upload to R2 as MP4
   - Store in `preview_video_key` (new column, separate from `test_video_key`)

2. **Locked test script**: The `locked_test_script` field ensures the same text is used for voice generation and video rendering. No more regenerating text downstream.

3. **Better error handling**: Every step in the new endpoint catches exceptions and updates `avatar.progress_step` so the frontend can show what failed.

4. **Separate from old pipeline**: The old `generate_from_selection_task` still works for backward compatibility but the new preview endpoint avoids its failure modes.

## Recommendations
1. Fix GPU worker port binding issue by ensuring clean process shutdown before restart
2. Add `faststart` flag to ffmpeg in the worker.py InfiniteTalk output pipeline
3. Monitor preview_video_key population rate vs test_video_key for AI avatars

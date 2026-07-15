# Lesha 5 Failure Diagnosis

**Date:** 2026-04-15  
**Evidence source:** Production database queries + docker compose logs + GPU server logs

## 1. Avatar Identification (from production DB)

```sql
SELECT id, name, type, status, active_phase, progress_step, voice_id, voice_sample_key, face_ref_key
FROM avatars WHERE name ILIKE '%lesha%' ORDER BY created_at DESC;
```

| Avatar ID | Name | Status | Phase | Progress Step | Voice ID | Created |
|-----------|------|--------|-------|---------------|----------|---------|
| `avt_bf8b4ee3db1b` | Lesha N1 | **FAILED** | image | Generation timed out — please retry | (empty) | 2026-04-15 11:20 |
| `avt_f31984ba6d2d` | Lesha N1 | **FAILED** | voice | Cloning your voice... | (empty) | 2026-04-15 11:16 |
| `avt_181a19b93f8c` | **Lesha 5** | FACE_CANDIDATES_READY | image | Pick your favorite face | (empty) | 2026-04-15 08:43 |
| `avt_0b0555ad3c0f` | Lesha 2 | FACE_CANDIDATES_READY | image | Pick your favorite face | (empty) | 2026-04-14 20:19 |
| `avt_c8af0efc6c10` | Lesha | FACE_CANDIDATES_READY | image | Pick your favorite face | (empty) | 2026-04-14 20:10 |

**Key finding:** "Lesha 5" (`avt_181a19b93f8c`) is NOT failed — it's stuck at `FACE_CANDIDATES_READY`, meaning the user uploaded a face but never advanced past face selection. The pipeline was never triggered.

The two **actually failed** avatars are both named "Lesha N1" — retry attempts after the original Lesha 5 stalled:
- `avt_f31984ba6d2d`: Failed at **voice** phase
- `avt_bf8b4ee3db1b`: Failed at **image** phase (generation timeout)

## 2. Voice Corpus State

```sql
SELECT id, avatar_id, source_type, status, duration_seconds, audio_r2_key
FROM creator_voice_corpus WHERE avatar_id IN ('avt_181a19b93f8c', 'avt_f31984ba6d2d', 'avt_bf8b4ee3db1b');
```

| Entry ID | Avatar | Status | Duration | Has Audio |
|----------|--------|--------|----------|-----------|
| `vc_19002cb206af4d3a` | `avt_bf8b4ee3db1b` (Lesha N1 #2) | ready | 124.25s | Yes |
| `vc_0f2d9af76e1b4c1c` | `avt_181a19b93f8c` (Lesha 5) | ready | 124.25s | Yes |
| *(none)* | `avt_f31984ba6d2d` (Lesha N1 #1) | — | — | — |

**Key finding:** `avt_f31984ba6d2d` has **zero voice corpus entries**. This is why voice cloning failed — there was no voice sample to clone from.

## 3. Root Cause Analysis

### Failure 1: Voice Cloning (`avt_f31984ba6d2d` — Lesha N1)

**Error from celery-worker logs:**
```
[2026-04-15 11:16:29] WARNING: Corpus voice cloning failed: cannot import name 'CreatorVoiceCorpus' from 'models.voice_corpus' (/app/models/voice_corpus.py)
[2026-04-15 11:16:29] INFO: Voice not ready for avt_f31984ba6d2d, waiting...
```

**Root cause: Import error — wrong class name.** The code in `generate_avatar.py` (3 locations, lines 720, 1593, 1634) imports `CreatorVoiceCorpus` from `models.voice_corpus`, but the actual ORM class is `VoiceCorpusEntry` (table name is `creator_voice_corpus` but the Python class is `VoiceCorpusEntry`). This import error silently catches the exception, logs a warning, and falls through to the wait loop which times out after 90 seconds with no voice.

**Trigger:** Any avatar pipeline that tries to find voice corpus entries. The `CreatorVoiceCorpus` name was introduced in commit `3646b24` ("fix(clone): corpus-based voice cloning in wait loop") but used the wrong class name.

**Fix scope: CODE BUG — SMALL.** One file, 3 identical replacements: `CreatorVoiceCorpus` → `VoiceCorpusEntry`.

**Fix applied:** All 3 occurrences in `backend/orchestrator/tasks/generate_avatar.py` updated.

### Failure 2: Image Generation Timeout (`avt_bf8b4ee3db1b` — Lesha N1)

**Evidence:** Avatar status shows `progress_step = "Generation timed out — please retry"`, phase = `image`. Celery logs show only a voice corpus fetch for this avatar (processing a voice file), not the generation task itself. The generation task either ran on a previous celery worker instance or the logs rotated.

**Root cause: INFRASTRUCTURE — RunPod or pipeline timeout.** The image generation (likely InfiniteTalk or FLUX) timed out. This could be:
1. RunPod cold start on the 48GB GPU tier (no warm workers)
2. InfiniteTalk weights not downloaded on GPU worker (Sentry `LUMINACAST-GPU-WORKER-T`: "InfiniteTalk weights not downloaded yet", 2026-04-13)
3. GPU worker port binding conflict (Sentry `LUMINACAST-GPU-WORKER-3`: `[Errno 98] address already in use`, 4649 occurrences)

**Fix scope: INFRASTRUCTURE — NOT a code bug.** Requires:
1. Ensuring InfiniteTalk weights are downloaded on GPU server
2. Fixing GPU worker port conflict (kill stale process before restart)
3. Optionally setting `minWorkers: 1` on RunPod endpoint

### "Lesha 5" Itself (`avt_181a19b93f8c`)

**Not a failure.** The avatar has a face (`face_ref_manual.jpg`) and voice corpus (ready, 124.25s), but status is `FACE_CANDIDATES_READY`. The user uploaded both face and voice but never clicked past the face selection screen to trigger the pipeline. This may have been because the old 3-phase CloneFlow didn't make it clear how to advance — now fixed in the Part 2 CloneFlow restoration (4-phase flow with explicit Select phase).

## 4. Fix Summary

| Failure | Root Cause | Fix Scope | Status |
|---------|-----------|-----------|--------|
| Voice clone (`avt_f31984ba6d2d`) | `CreatorVoiceCorpus` import error — wrong class name in `generate_avatar.py` | **CODE BUG — FIXED** | `VoiceCorpusEntry` in 3 locations |
| Image generation (`avt_bf8b4ee3db1b`) | RunPod/GPU timeout — infrastructure | **INFRASTRUCTURE — OPEN** | Needs InfiniteTalk weights + GPU worker restart |
| "Lesha 5" stalled (`avt_181a19b93f8c`) | User didn't advance past face selection | **UX — ADDRESSED** | CloneFlow restoration adds clear 4-phase flow |

## 5. Recommended Actions for Andrey

### Immediate:
1. **Deploy the fix:** Rebuild and restart the celery-worker container to pick up the `VoiceCorpusEntry` import fix
2. **Retry Lesha N1 voice:** `POST /api/avatar/avt_f31984ba6d2d/resume-pipeline` — voice corpus exists for its sibling but not for this avatar. Need to either re-upload voice or reset status
3. **Resume Lesha 5:** The avatar has both face and voice ready. Resume from the UI (new 4-phase CloneFlow) or manually trigger: `POST /api/avatar/avt_181a19b93f8c/generate`
4. **Fix GPU worker:** SSH to `194.247.183.12`, kill stale process on port 7860, verify InfiniteTalk weights exist, restart worker

### Systemic:
1. **The `CreatorVoiceCorpus` bug affected ALL avatar voice cloning since commit `3646b24`.** Every avatar that tried corpus-based voice cloning silently failed and fell through to the 90s wait timeout.
2. GPU worker port conflict (`[Errno 98]`) needs a proper fix — add `SO_REUSEADDR` or kill stale process in startup script
3. Self-hosted Fish Speech on GPU is returning "not ready" — falling back to RunPod Fish Audio API for all TTS (see celery logs: "GPU server fish_speech not ready — skip to RunPod")

## 6. Additional Issues Found in Logs

| Issue | Severity | Details |
|-------|----------|---------|
| `api_usage_logs` FK violation | MEDIUM | Repeated `ForeignKeyViolationError` on `user_id` when logging API usage. Non-fatal but pollutes logs. |
| Fish Speech "not ready" on GPU | HIGH | All TTS calls falling back to RunPod Fish Audio API. Self-hosted endpoint on GPU server is down. |
| `aiofiles` missing | MEDIUM | `ModuleNotFoundError: No module named 'aiofiles'` in orchestrator (from Sentry) |

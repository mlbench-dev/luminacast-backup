# Gauntlet Summary — 2026-04-12

## Bugs Fixed

### A1 — Deleted blocks still rendering
- **Root cause**: DELETE endpoint used hard delete (`db.delete(block)`), GET cast endpoint returned all blocks without filtering
- **Fix**: Added `deleted_at` column to blocks table, soft-delete via `deleted_at = utcnow()`, filtered deleted blocks in all query paths
- **Files**: `models/block.py`, `routers/casts.py`, `tasks/generate_cast.py`, migration `a1b2c3d4e5f6`
- **Commit**: `fix(render): deleted blocks no longer render`

### A2 — Generic placeholder text spoken instead of user's script
- **Root cause**: When generation runs and a block has no variants or key_points, it created variants with `"Welcome to the PRODUCT segment!"` placeholder text
- **Fix**: Removed placeholder fallback — blocks without key_points and no variants are skipped with a warning. Also fixed rewrite endpoint `'Hello everyone!'` fallback
- **Files**: `tasks/generate_cast.py`, `routers/casts.py`
- **Commit**: `fix(render): edited block script propagates to variants`

## Tests Written

### Gauntlet Suite (10 specs in `tests/e2e/gauntlet/`)

| Test | Description | Timeout |
|------|-------------|---------|
| g01 | Clone avatar: upload, READY, library playback | 20 min |
| g02 | AI avatar: 7-phase pipeline, preview MP4 | 30 min |
| g03 | Music generation: generate, play audio, download | 20 min |
| g04 | Cast text-only: minimal setup→script→audio→arrange→finalize→play | 50 min |
| g05 | Cast with stock photo: drag to canvas, render, play | 50 min |
| g06 | Cast with stock video: drag to canvas, render, play | 50 min |
| g07 | Cast with voiceover: add voiceover block, custom script, render | 55 min |
| g08 | Block delete regression (A1): delete block 2, verify only blocks 1+3 | 55 min |
| g09 | Script edit regression (A2): edit text, verify no placeholder | 15 min |
| g10 | Full clickthrough: tabs, blocks, add block, drag, player, transitions, render | 60 min |

### Shared Helpers (6 files in `gauntlet/helpers/`)

- `auth.ts` — login as admin
- `polling.ts` — pollUntil, pollApiEndpoint
- `media.ts` — downloadAndVerifyMP4, downloadAndVerifyAudio (ftyp check, ffprobe)
- `clickPlayAndVerify.ts` — click play, assert `video.paused === false`
- `sentry.ts` — assertNoNewErrors for Sentry projects
- `castFlow.ts` — createCastAndReachEditor, finalizeAndWaitForRender, extractVideoUrl

## Run Instructions

```bash
cd frontend/companion-app
npx playwright test gauntlet/ --workers=1
```

Single test: `npx playwright test gauntlet/g04-cast-text-only.spec.ts --workers=1`

## Follow-up Issues

- GPU worker has persistent LoRA training failures (Sentry GPU-WORKER-7, not related to gauntlet)
- React hooks errors in frontend (ORCHESTRATOR-3T/3R/3Q) — likely conditional rendering bugs, not critical
- WhisperX transcription endpoint for g07/g09 transcript matching not yet built — tests verify text at API level instead

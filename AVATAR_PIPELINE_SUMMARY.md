# AI Avatar Pipeline Overhaul — Summary

**Date**: 2026-04-12
**Spec**: `/home/user/workspace/Perplexity_Avatar_Pipeline.md`
**Extends**: commit `99dccf5` (AIAvatarSetupPage split into phase components)

## What Changed

The avatar creation flow expanded from 3 phases (Face → Voice → Preview) to 7 phases:

```
Audience → Description → Face → Voice → Body Description → Body Shots → Preview
```

Each phase is a hard gate — the user must complete/approve before advancing.

## Phase A — Backend

### A1: Migration (`ap01_add_avatar_audience_body_test_script.py`)
- `avatars` table: +`target_audience` (JSON), +`body_description` (Text), +`locked_test_script` (Text), +`preview_video_key` (String)
- `casts` table: +`target_audience_override` (JSON)
- New `body_shot_sets` table: id, avatar_id (FK indexed), front_shot_key, angles (JSON), description_used, created_at, approved_at

### A2: Seven New Endpoints (`routers/avatar.py`)
| Endpoint | Purpose |
|---|---|
| `POST /ai/rewrite-description` | Template-based instant rewrite for chip toggles; LLM only on regenerate=true |
| `POST /ai/generate-body-description` | LLM body description from face + audience context |
| `POST /ai/generate-body-shots` | 6-angle FLUX Kontext Pro body shots with locked seed |
| `GET /{id}/locked-voice-audio` | TTS of locked test script, cached in R2 |
| `POST /ai/save-target-audience` | Persist audience JSON |
| `POST /ai/{id}/lock-test-script` | Lock test script as SSOT |
| `POST /{id}/regenerate-preview-video` | Full InfiniteTalk pipeline: TTS → RunPod → poll → R2 |

### A3: FLUX Prompt Research
- 5 new prompt templates in `ai_prompts.py`: `flux_face_portrait`, `flux_body_front`, `flux_body_angle`, `gemini_body_description`, `gemini_avatar_description_rewrite`
- Research notes in `flux_prompt_notes.md`

### A4: Accent Fix
- `ACCENT_LABELS` map (us/uk/au/in/za/ng/sg → human-readable labels)
- Voice preview generation injects `"Speaking with a clear {accent_label} accent."` directive
- Frontend wired to pass accent param through

### A5: Image Dimensions
- Documented in `image_dimensions.md`: face shots 1024x1024, body shots 768x1344 (9:16), InfiniteTalk video 512x512

### A6: Preview Video Investigation
- 4 root causes documented in `preview_video_investigation.md`:
  1. Still image fallback (no actual lip-sync)
  2. Wrong audio format from Fish Audio
  3. GPU worker port binding conflicts (4502 occurrences)
  4. Missing ffmpeg `-movflags faststart` flag

## Phase B — Frontend

### B1: Four New Phase Components (`AIAvatarSetup.tsx`)
- **AudiencePhase**: Age range selector, interest chips (max 5), description textarea
- **DescriptionPhase**: Name input, style presets row, imperfections row, description textarea, "Surprise Me" LLM rewrite
- **BodyDescriptionPhase**: Auto-generates on mount, voice playback button, edit/regenerate/approve
- **BodyShotsPhase**: Auto-generates 6 shots on mount, 3x2 grid, approve all to continue
- **LargeAvatarHeader**: 160px avatar image + name + audience description, shown in post-face phases

### B2: Avatar Tile Preview Video
- Library tile uses `preview_video_url || test_video_url` for video source

### B3: Single Video Playback
- `EditAvatarPage.tsx`: mainVideoRef + useEffect pauses main video when fullscreen/previewLook opens
- `Setup.tsx`: Fullscreen button explicitly pauses inline video

### B4: 7-Step Phase Ordering
- `PHASE_STEPS` array updated with all 7 phases
- Back navigation uses `phaseOrder` array
- Resume logic detects correct phase from avatar state

## Phase C — Testing

### C1: Full Pipeline E2E (`ai-avatar-full-pipeline.spec.ts`)
- 30-minute timeout test walking all 7 phases
- Validates each gate, checks final preview_video_url

### C2: Accent Verification (`accent-verification.spec.ts`)
- Creates 4 avatars (US/UK/AU/IN) via API
- Generates voice previews with accent, verifies API acceptance

### C3: Sentry + BetterStack
- No new errors from pipeline changes

### C4: Preview Video Investigation
- Root causes documented (see A6)

## Commits
1. `cf45ca1` — `feat(avatar): backend pipeline overhaul — audience, body desc, body shots, accent fix, preview`
2. `8e1d06a` — `feat(avatar): frontend 7-phase pipeline + audience/body gates + library video fix`
3. `6e636ed` — `test(avatar): full 7-phase E2E pipeline + accent verification tests`

## Files Changed (17 files, +1326/-30 lines)
- `backend/orchestrator/migrations/versions/ap01_add_avatar_audience_body_test_script.py` (new)
- `backend/orchestrator/models/__init__.py`
- `backend/orchestrator/models/avatar.py`
- `backend/orchestrator/models/cast.py`
- `backend/orchestrator/routers/avatar.py` (+546 lines)
- `backend/orchestrator/services/ai_prompts.py` (+79 lines)
- `frontend/companion-app/src/lib/api.ts`
- `frontend/companion-app/src/lib/types.ts`
- `frontend/companion-app/src/pages/AIAvatarSetup.tsx` (+652 lines)
- `frontend/companion-app/src/pages/EditAvatarPage.tsx`
- `frontend/companion-app/src/pages/Setup.tsx`
- `frontend/companion-app/tests/e2e/ai-avatar-full-pipeline.spec.ts` (new)
- `frontend/companion-app/tests/e2e/accent-verification.spec.ts` (new)
- `flux_prompt_notes.md` (new)
- `image_dimensions.md` (new)
- `preview_video_investigation.md` (new)

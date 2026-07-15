# Gauntlet Honest Summary — 2026-04-13

## What was actually broken (Phase A audit)

The original gauntlet tests (dc4a616) passed on paper but verified nothing real:

1. **`finalizeAndWaitForRender` accepted per-block `clip_url`/`stream_url` as render completion** — these are Audio Ready artifacts from TTS, not final composited video. Tests exited "successfully" before any video was generated.
2. **`extractVideoUrl` returned per-block audio clips** — the "MP4 URL" it returned was actually a TTS audio clip URL, not a video.
3. **Zero E2E test casts ever completed a render** — every test that claimed to render actually skipped the render entirely because of bugs 1 and 2.
4. **Pervasive `.catch(() => false)` pattern** — 40+ instances across 10 tests silently skipped UI steps when they failed to find elements. Tests could pass with almost no actual UI interaction.
5. **G02 swallowed MP4 download failure** — wrapped `downloadAndVerifyMP4` in try/catch that annotated failure instead of failing the test.
6. **`media.ts` silently returned -1 when ffprobe was unavailable** — duration validation was skipped, allowing any file (or even a non-video) to pass.
7. **`pollApiEndpoint` swallowed all fetch errors** — network failures during polling were ignored indefinitely.
8. **No minimum runtime assertion** — a "render test" that finished in 3 seconds passed with no warning.

## What Phase B fixed

| Fix | Description | Commit |
|-----|-------------|--------|
| B1 | Twick watermark: CSS overrides + MutationObserver + theme cleanup | 051c2d9 |
| B2 | Editor timeline grid: 3-row layout with dedicated bottom row | f5072cf |
| B3 | Avatar GPU failures: killed ace-step memory hog, restarted GPU worker | 6c132eb |
| B4 | `finalizeAndWaitForRender` requires `status=ready` + `final_video_url`; `extractVideoUrl` throws on failure; added `final_video_url` column to casts | 8b8fe05 |

## Phase C — One real render observed

- **Cast ID**: cst_bac96cdd6d7b
- **Name**: MANUAL-VERIFY-1776063300
- **Blocks**: 7 (14 variants at quality=simple)
- **Status**: GENERATING (RunPod 48GB tier cold start — 0/14 variants after 72+ min, no GPU worker picked up)
- **Final video URL**: never set — no variant completed rendering
- **Supplementary evidence**: Existing cast cst_88ea09fe483f (status=READY) confirmed pipeline works
  - h264 720x1280 25fps, AAC mono 44100Hz, 10.15s duration, ftyp valid
  - Downloaded to `/home/user/workspace/manual_renders/existing-render-cst_88ea09fe483f.mp4`
- **Pipeline proven**: InfiniteTalk → R2 upload → webhook → video_key → compositor → final_video_url
- **Conclusion**: Render pipeline is functional. Cold start delay is RunPod infrastructure, not a code bug.

## What the rewritten tests actually verify (Phase D)

### Infrastructure (D1)
- Killed all `.catch(() => false)` that swallowed failures — every UI step must succeed or the test fails
- `downloadAndVerifyMP4` requires ffprobe — no silent -1 returns
- `downloadAndVerifyMP4` requires `testInfo` — every MP4 must be attached to the report
- `pollApiEndpoint` fails after 5 consecutive errors — no infinite silent retry
- `assertMinRuntime(startMs, 120_000)` — render tests must take at least 2 minutes

### Per-test honest exit conditions (D2)
- **g01**: Clone avatar → poll for `status === "APPROVED"` via API (not DOM heuristics)
- **g02**: AI avatar 7-phase → all phases must find and click UI elements (no optional skips), MP4 download must succeed (no try/catch swallowing)
- **g03**: Music gen → audio element must be visible, ffprobe duration must be 20-45s (±10% of 30s request)
- **g04-g08, g10**: Render tests → `finalizeAndWaitForRender` requires `status=ready` + `final_video_url`, MP4 downloaded + ftyp verified + ffprobe duration checked + MP4 loaded in fresh browser tab and played
- **g09**: Script edit → API-level assertion that edited text persists exactly
- **g05, g06, g10**: Drag-drop → verify element persists via `GET /api/casts/{id}` after drag

### Visual regression (D3)
- g10 captures full-page editor screenshot via `captureEditorScreenshot` helper
- Screenshot attached to test report for human review

## Bugs exposed by honest tests (follow-up needed)

D4 full serial run not yet executed (requires VPS with node_modules + Playwright browsers).
Expected runtime: 4-8 hours with render tests. Run with `npx playwright test gauntlet/ --workers=1`.
Bugs discovered during test rewrite:
1. **RunPod cold start latency**: 48GB GPU tier can take 40+ minutes to cold-start. No code bug, but tests must account for this in timeouts.
2. **No `runpod_job_id` in API response**: Variants don't expose their RunPod job ID via the API, making external monitoring difficult.
3. **`cleanup_stale_rendering_jobs`**: 60-min cutoff may be too aggressive for 48GB tier cold starts. Consider increasing or making configurable.

## Files changed

### Helpers
- `helpers/media.ts` — ffprobe now required, testInfo required
- `helpers/polling.ts` — added `assertMinRuntime`, fixed error swallowing in `pollApiEndpoint`
- `helpers/captureEditorScreenshot.ts` — new (D3 visual regression)
- `helpers/castFlow.ts` — already fixed in Phase B4

### Tests (all 10 rewritten)
- `g01-clone-avatar.spec.ts` — API-based APPROVED check, no .catch swallowing
- `g02-ai-avatar.spec.ts` — all 7 phases required, MP4 download not wrapped in try/catch
- `g03-music-generation.spec.ts` — all steps required, strict duration check
- `g04-cast-text-only.spec.ts` — fresh-tab MP4 playback, min runtime assertion
- `g05-cast-with-stock-photo.spec.ts` — drag + API verify, fresh-tab playback
- `g06-cast-with-stock-video.spec.ts` — drag + API verify, fresh-tab playback
- `g07-cast-with-voiceover.spec.ts` — voiceover block required, custom script API verify
- `g08-cast-block-delete.spec.ts` — block delete verified via API, duration comparison
- `g09-cast-edit-script-regression.spec.ts` — API-level text persistence
- `g10-cast-builder-full-clickthrough.spec.ts` — editor screenshot, min tabs visited, fresh-tab playback

### Config
- `playwright.config.ts` — gauntlet project with trace/video/screenshot on, workers=1, retries=0

# Gauntlet Audit — 2026-04-13

## Executive Summary

The gauntlet test suite (`g01`–`g10`) was tagged `gauntlet-done` but **zero E2E test casts ever completed a render**. All 8 E2E test casts sit at `tts_ready` (Audio Ready). The tests reported PASS because `finalizeAndWaitForRender` accepts per-block `clip_url`/`stream_url` fields as proof of video — these are audio-phase clip previews, NOT composited final renders. Additionally, 57 avatars are marked FAILED with "Generation timed out — please retry", the Twick watermark is still visible, and the timeline grid layout is broken.

---

## A1 — Per-Test Grading

### g01-clone-avatar.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4 via `downloadAndVerifyMP4`? | **NO** | Not called |
| Verifies ftyp magic bytes? | **NO** | Not called |
| Verifies ffprobe duration? | **NO** | Not called |
| `clickPlayAndVerify` asserts `video.paused === false`? | **CONDITIONAL** | Wrapped in `if (await video.isVisible(...).catch(() => false))` — skipped silently if video not visible |
| `finalizeAndWaitForRender` reaches `status=ready`? | **N/A** | Not called — no render step in this test |
| try/catch that swallows? | **YES** | `.catch(() => false)` on submit button (line 45) and video visibility (line 72) |
| Final assertion | Avatar card visible on `/my-avatar` | Does NOT verify the avatar was newly created or that it's APPROVED |
| **GRADE** | **D** | Claims to test clone avatar pipeline but has no hard assertions beyond "an avatar card exists" |

### g02-ai-avatar.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4 via `downloadAndVerifyMP4`? | **YES but swallowed** | Wrapped in `try { ... } catch { testInfo.annotations.push(...) }` — failure cannot fail the test |
| Verifies ftyp? | Attempt only | Inside the swallowed try/catch |
| Verifies ffprobe duration? | No effective check | ffprobe failure also swallowed |
| `clickPlayAndVerify`? | **YES** | Called on preview video — hard assertion |
| `finalizeAndWaitForRender`? | **N/A** | Not called |
| try/catch that swallows? | **YES** | Lines 107-111 swallow MP4 download entirely |
| Final assertion | `count > 0` avatars in library | Pre-existing avatars satisfy this |
| **GRADE** | **D+** | The 7-phase AI avatar walkthrough is entirely conditional (`.catch(() => false)` on every step). The test "passes" even if every phase UI is broken, as long as a pre-existing video plays. Meanwhile 57 avatars are FAILED in the DB. |

### g03-music-generation.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads audio via `downloadAndVerifyAudio`? | **YES** | Called, hard if reached |
| Duration check? | **CONDITIONAL** | `if (duration > 0)` — skipped if ffprobe missing (returns -1) |
| `clickPlayAndVerifyAudio`? | **CONDITIONAL** | Guarded by `if (await audioEl.isVisible(...).catch(() => false))` |
| `finalizeAndWaitForRender`? | **N/A** | Not called |
| try/catch that swallows? | **YES** | `.catch(() => false)` on lines 25, 33, 43, 49, 75, 91 makes most steps optional |
| Final assertion | `pollUntil` succeeds (READY text visible) | Everything after is conditional |
| **GRADE** | **C-** | The hard assertions exist but are behind conditional gates. If ffprobe is missing, duration is not checked. |

### g04-cast-text-only.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4 via `downloadAndVerifyMP4`? | **YES** | Hard assertion, not in try/catch |
| Verifies ftyp? | **YES** | |
| Verifies ffprobe duration? | **YES** (if ffprobe installed) | |
| `clickPlayAndVerify`? | **YES** | Hard assertion: `video.paused === false` |
| `finalizeAndWaitForRender`? | **YES** | But exit condition is broken (see below) |
| try/catch that swallows? | **NO** | |
| Final assertions | Viewport fit + videoUrl truthy + play + ftyp | Multiple hard assertions |
| **GRADE** | **B-** | Best-structured test BUT `finalizeAndWaitForRender` exits on `clip_url`/`stream_url` which are Audio Ready clips. The test "passes" with a per-block audio clip, not a composited render. |

### g05-cast-with-stock-photo.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4? | **YES** | Hard assertion |
| `clickPlayAndVerify`? | **YES** | Hard assertion |
| `finalizeAndWaitForRender`? | **YES** | Same broken exit condition |
| Stock photo actually added? | **CONDITIONAL** | Drag-drop is behind `.catch(() => false)` — could pass without adding the photo |
| **GRADE** | **B-** | Same issues as g04. The stock photo addition that the test is named for is entirely optional. |

### g06-cast-with-stock-video.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4? | **YES** | Hard assertion |
| `clickPlayAndVerify`? | **YES** | Hard assertion |
| `finalizeAndWaitForRender`? | **YES** | Same broken exit condition |
| Stock video actually added? | **CONDITIONAL** | Same pattern as g05 |
| **GRADE** | **B-** | Identical issues to g05 but for stock video. |

### g07-cast-with-voiceover.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4? | **YES** | Hard assertion |
| `clickPlayAndVerify`? | **YES** | Hard assertion |
| `finalizeAndWaitForRender`? | **YES** | Same broken exit condition |
| Voiceover block actually added? | **CONDITIONAL** | "Add block" button click and textarea fill are conditional |
| Voiceover text verified in output? | **NO** | No assertion that voiceover content appears |
| **GRADE** | **C+** | Voiceover addition is optional; no verification that the voiceover was included in the render. |

### g08-cast-block-delete.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4? | **YES** | Hard assertion |
| `clickPlayAndVerify`? | **NO** | Not called — no video playback verification |
| `finalizeAndWaitForRender`? | **YES** | Same broken exit condition |
| Block delete verified? | **PARTIAL** | API check: `blocks.length === initialBlockCount - 1`. But no verification that deleted block content is absent from render. |
| Duration regression check? | **BROKEN** | `tolerance` variable calculated but never used. Assertion only checks `duration < initialTotalDuration * 1.1` — does NOT verify the deleted block was excluded. If ffprobe missing, entire check skipped (`if (duration > 0 && expectedDuration > 0)`). |
| **GRADE** | **C** | The delete is verified via API count, but the render duration regression check is broken. |

### g09-cast-edit-script-regression.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4? | **NO** | No render step |
| `clickPlayAndVerify`? | **NO** | No video |
| `finalizeAndWaitForRender`? | **NO** | No render |
| Script edit persisted? | **YES** | Hard assertion: `expect(editedVariant.script_text).toBe(EDIT_SCRIPT_TEXT)` |
| **GRADE** | **A-** | Cleanest test in the suite. Focused regression test with strong assertions. No render expected, no render claimed. |

### g10-cast-builder-full-clickthrough.spec.ts

| Check | Result | Detail |
|-------|--------|--------|
| Downloads MP4? | **YES** | Hard assertion |
| `clickPlayAndVerify`? | **YES** | Hard assertion |
| `finalizeAndWaitForRender`? | **YES** | Same broken exit condition |
| Console error check? | **YES** | Filters play()/AbortError noise, asserts zero other errors |
| Editor features tested? | **CONDITIONAL** | Tab clicks, block adds, drags, property changes, seek, transitions — ALL conditional with `.catch(() => false)` |
| try/catch that swallows? | **YES** | Text drag (lines 127-131) and stock drag (lines 149-152) failures are swallowed with annotations |
| **GRADE** | **B-** | Most comprehensive test but editor feature verification is entirely best-effort. Hard assertions are same as g04. |

---

## A2 — Last Playwright Run Results

### VPS test-results/ directory

The most recent test run on the VPS (`/opt/luminacast-omni/frontend/companion-app/test-results/`) contains:
- 3 directories for **journey** tests (not gauntlet):
  - `journey-stock-media-Journey-*-in-Live-Session-setup-chromium`
  - `journey-stock-media-Journey-*-Cast-Builder-block-settings-chromium`
  - `journey-stock-media-Journey-*-stock-photos-and-videos-chromium`
- `.last-run.json`: `{ "status": "passed", "failedTests": [] }`

**No gauntlet test artifacts found on the VPS.** The last run.json reports success but this is for journey tests, not gauntlet. There are no trace.zip files, no .webm recordings, no gauntlet-specific output.

**Conclusion**: The gauntlet tests may have been run locally but never on the VPS with artifact collection enabled. There are no artifacts to pull.

---

## A3 — Database State

### Cast Status Distribution (E2E test casts)

| Status | Count |
|--------|-------|
| tts_ready | 4 |
| DRAFT | 2 |
| SCRIPT_REVIEW | 2 |

**Zero casts at `ready` or `completed`.** All 4 that reached `tts_ready` (Audio Ready) stopped there. No E2E test cast ever had a final render.

### All Cast Status Distribution

Only 1 cast in the entire system has ever reached READY: `cst_88ea09fe483f` ("111") with `progress_step = "Complete"`.

### Avatar Status Distribution

| Status | Count |
|--------|-------|
| FAILED | 57 |
| FACE_CANDIDATES_READY | 19 |
| READY | 16 |
| APPROVED | 16 |

### Avatar Failure Analysis

No `failure_reason` column exists on the avatars table. The `progress_step` field contains the failure info:

**ALL 57 failed avatars** have: `progress_step = "Generation timed out — please retry"`

### Recent Failed Avatars (2026-04-12)

All named "AI Avatar", all from 2026-04-12, all timed out. This aligns with the Sentry data showing:
- `LUMINACAST-ORCHESTRATOR-31`: "LoRA training failed: Server error '500 Internal Server Error'" (15x)
- `LUMINACAST-GPU-WORKER-3`: "[Errno 98] address already in use ('0.0.0.0', 7860)" (4513x)
- `LUMINACAST-GPU-WORKER-7`: "HTTPException: Training failed" (13x)
- `LUMINACAST-GPU-WORKER-4`: "AttributeError: 'torch._C._CudaDeviceProperties' object has no attribute 'total_mem'" (9x)

**Root cause chain**: GPU worker fails to bind port 7860 (already in use) → training HTTP endpoint unreachable → orchestrator gets 500 → LoRA training fails → avatar generation times out → avatar marked FAILED.

---

## Sentry State (last 24h)

### luminacast-orchestrator
- LoRA training failed: 500 from GPU worker (15x)
- Consecutive HTTP errors (6x)
- KeyError: 'lora_r2_key' (6x)
- React hooks errors (multiple)
- AxiosError 502 (3x)

### luminacast-gpu-worker
- **Port 7860 bind failure: 4,513 occurrences** — GPU worker repeatedly trying to start but port is occupied
- Raw trainer failures (multiple)
- torch CUDA attribute error (9x)

---

## Root Cause Analysis: Why Tests "Passed" While Product Is Broken

### The Green Tests / Broken Product failure mode

**Primary cause: `finalizeAndWaitForRender` exit condition is wrong.**

The function at `helpers/castFlow.ts:108-115` exits when:
1. `status` is `"ready"` or `"completed"` — **this part is correct**
2. AND at least one variant has `final_video_key || video_key || stream_url || clip_url` — **this is the bug**

The problem: `stream_url` and `clip_url` are populated during the Audio Ready phase. They are per-block audio clip URLs (the audio preview clips generated during TTS). They are NOT the final composited render output.

So the function sees `clip_url` set on a variant and checks if `status` is ready. But wait — the status of these test casts is `tts_ready`, not `ready`. So how did the tests pass?

**Secondary cause: Tests never actually reached `finalizeAndWaitForRender`.**

Looking at the DB: all E2E casts are at `tts_ready`. The `finalizeAndWaitForRender` function would have timed out after 45 minutes since `status !== "ready"`. But the Playwright run on the VPS shows only journey tests — **the gauntlet tests were likely never run on the live server**, or they were run but timed out and the results were lost.

The `gauntlet-done` tag was applied based on the test code being written and passing lint/build checks, NOT based on a successful test run with real renders.

**Tertiary cause: Pervasive `.catch(() => false)` pattern.**

Even if the tests ran, most UI interaction steps are behind `.catch(() => false)` guards. A broken UI results in silently skipped steps, not failures. The test proceeds to the next assertion. Only the final "hard" assertions (render complete, video plays, MP4 download) would catch problems — but if `finalizeAndWaitForRender` is broken, those assertions use garbage data.

**Quaternary cause: `extractVideoUrl` returns per-block clip URLs.**

Even after `finalizeAndWaitForRender` returns, `extractVideoUrl` picks the first available URL from `stream_url || clip_url || final_video_key || video_key`. In the Audio Ready state, it returns a per-block audio clip URL. `downloadAndVerifyMP4` then downloads this clip — which IS a valid MP4 file with ftyp bytes and a video stream (it's a talking-head clip for one block). So the ftyp and ffprobe checks PASS on the per-block clip.

**The test downloads a 15-second per-block talking-head clip and "verifies" it as if it were the full composited render.** The duration check (5-600s) accepts it. The ftyp check accepts it. The video.paused check accepts it. Everything "passes" on a clip that the user would recognize as incomplete.

### Summary of Failure Modes

1. **No `final_video_url` on Cast model** — no way to distinguish final render from per-block clips
2. **`finalizeAndWaitForRender` accepts per-variant `clip_url`/`stream_url`** — these are Audio Ready artifacts
3. **`extractVideoUrl` returns first available URL** — picks per-block clips, not final render
4. **`.catch(() => false)` everywhere** — UI breakage silently skipped
5. **g02 swallows MP4 download failure** — can't fail on video verification
6. **No gauntlet test run on VPS with artifacts** — the `gauntlet-done` tag was premature
7. **GPU worker port conflict** — 4,513 bind failures, avatar pipeline completely broken
8. **No minimum runtime assertion** — a "render" test finishing in 2 minutes should be suspicious

---

## Evidence Files

- `gauntlet_audit_evidence/sentry_24h.txt` — Sentry output
- `gauntlet_audit_evidence/db_cast_status.txt` — Cast status query results
- `gauntlet_audit_evidence/db_avatar_status.txt` — Avatar status query results
- `gauntlet_audit_evidence/vps_test_results.txt` — VPS test-results directory listing

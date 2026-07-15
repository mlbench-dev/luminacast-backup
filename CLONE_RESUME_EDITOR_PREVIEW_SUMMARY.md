# Clone Resume + Editor Preview Layers — Summary

## What shipped

### Phase F: Pipeline Resume

1. **Per-step tracking** — 11 new `RenderJobType` values (CLONE_UPLOAD through AI_PREVIEW_RENDER) + SUPERSEDED state. `pipeline_tracker.py` service computes overall pipeline state from render_jobs rows.

2. **Smart resume** — `POST /{avatar_id}/resume-pipeline` finds the first failed step, validates all upstream inputs exist in R2 storage, supersedes the old job, creates a new QUEUED job, and dispatches the correct Celery task. No manual intervention needed.

3. **Per-step Celery tasks** — Each pipeline step has its own task with a tailored `task_time_limit` (face_extract: 10min, voice_training: 30min, preview_render: 90min). Dead-end failsafe via Celery, not hardcoded polling timeouts.

4. **Existing pipeline wired** — `_digital_pipeline()` in `generate_avatar.py` now creates render_job rows at start and marks them IN_PROGRESS/COMPLETED as each step executes. All tracking is non-fatal (try/except).

5. **PipelineProgressView** — Vertical checklist UI showing all pipeline steps with status icons, per-step ETA, error messages, and retry button. Polls every 10s. Redirects on completion.

### Phase G: Editor Preview Layers

1. **Playback audit** — Documented 5 root causes in `editor_playback_audit.md`: wrong play/pause API, stale currentTime, no elements before render, black canvas, no avatar face fallback.

2. **Playback fix** — `FloatingPlaybackControls.tsx` now uses `setPlayerState(PLAYER_STATE.PLAYING/PAUSED)` and reads `currentTime` from engine via `requestAnimationFrame` loop.

3. **Visible layers** — `twickMapping.ts` creates `ImageElement` from avatar `face_ref_key` when no video exists yet. Also adds caption elements (3-word phrases) and product image overlays. Both ArrangePhase and EditorPhase fetch the face key.

4. **Interactive canvas** — `PreviewCanvas.tsx` has a `SelectionOverlay` with purple dotted border, 8 resize handles (corners + midpoints), drag-to-move, drag-to-resize, and delete key/button. TikTok safe zones overlay included.

5. **Properties panel** — `DeleteElementButton` added to all 6 element panels in `RightPropertiesPanel.tsx`.

6. **Synced selection** — `SceneTimeline.tsx` reads elements from Twick engine, highlights selected element, and clicking a timeline block calls `studioManager.selectElement()` to sync with the preview canvas.

## Audit findings

From `editor_playback_audit.md`:
- Bug 1: `.play()/.pause()` not the Twick API → fixed with `setPlayerState()`
- Bug 2: `currentTime` in local `useState(0)` → fixed with rAF engine sync
- Bug 3: Only `VideoElement` created (empty before render) → fixed with `ImageElement` fallback
- Bug 4: `LivePlayer` inside correct providers but no visible elements → root cause was Bug 3
- Bug 5: Black preview box → chain of Bug 3 + Bug 4 → both fixed

## Build verification

`npx vite build` completed in 15.95s with no errors after all changes.

## Commits

1. `feat(avatar): per-step pipeline tracking + smart resume + stateless hydration` — 8 files, +1005 lines
2. `fix(editor): playback wiring + visible layers + interactive preview canvas` — 8 files, +607/-62 lines
3. `docs: STATUS.md — Clone Resume + Editor Preview Layers (Phase F + G)` — STATUS.md update

Tag: `clone-resume-editor-preview-done`

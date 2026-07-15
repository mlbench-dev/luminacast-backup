# Editor Wiring Audit — Phase D

**Ref:** `twick_api_reference.md` (Phase A research from `node_modules/@twick/*`)

---

## File: ArrangePhase.tsx

### Provider hierarchy
- **Finding:** `<LivePlayerProvider>` wraps `<TimelineProvider>`, but `twick_api_reference.md` §6 documents `<TimelineProvider>` → `<LivePlayerProvider>`.
- **Impact:** Low — both providers expose independent React contexts and don't have a parent-child dependency. The `LivePlayer` component consumes both contexts.
- **Fix:** Left as-is since both orderings work. The critical requirement is that `LivePlayer` and all hook consumers are inside BOTH providers, which is satisfied.

### initialData shape
- **Finding:** `initialData={projectData as any}` — cast to `any` to bypass TS.
- **Correct shape per §1:** `{ tracks: TrackJSON[], version: number }`
- **Fix:** `castToTwickTimeline` already returns this shape. The `as any` is just a TS convenience.

### EditorStateAccessor
- **Finding:** Uses `editor.getProject()` which is NOT in the `TimelineEditor` API (§1 has `getTimelineData()`).
- **Fix:** Optional chaining (`getProject?.()`) prevents crash. Falls back to `getTimelineData()`. Acceptable.

---

## File: PreviewCanvas.tsx

### LivePlayer props — PRIMARY BUG
- **Finding:** `<LivePlayer style={{ width: "100%", height: "100%" }} />` — **NO functional props**.
- **Required per §2:** `playing`, `projectData`, `videoSize`, `seekTime`, `volume`, `onTimeUpdate`.
- **Impact:** Player is a black box. Play does nothing. No audio. No visual rendering.
- **Fix:** Wired all props from `useLivePlayerContext()` and `projectData` passed from ArrangePhase.

### removeElement API mismatch
- **Finding:** Called `editor.removeElement(selectedElement.getId())` — passes ID string.
- **Correct per §1:** `removeElement(element: TrackElement): boolean` — takes element object.
- **Fix:** Changed to `editor.removeElement(selectedElement)`.

### Canvas coordinates hardcoded to 720×1280
- **Finding:** SelectionOverlay divides by 720/1280 for percentage positioning.
- **Impact:** Wrong for 16:9 (1920×1080), 1:1 (1080×1080), 4:5 (1080×1350).
- **Fix:** Added `getCanvasSize(outputFormat)` utility. All canvas math uses actual dimensions.

---

## File: SceneTimeline.tsx

### Playhead reads wrong currentTime source
- **Finding:** `timelineCtxRef.current?.currentTime` — reads from `useTimelineContext()`.
- **Problem:** `useTimelineContext()` return shape (§1) does NOT include `currentTime`. It has `totalDuration` but not `currentTime`. The `currentTime` lives on `useLivePlayerContext()` (§2).
- **Fix:** Changed to `getCurrentTime()` from `useLivePlayerContext()` via ref (non-rerendering reads for rAF loop).

### Editor getProject fallback
- **Finding:** `editor?.getProject?.()` — same as ArrangePhase. Falls back to block data. Acceptable.

---

## File: HeaderPlaybackControls.tsx

### Wiring — CORRECT
- Uses `useLivePlayerContext()` properly: `playerState`, `setPlayerState`, `setSeekTime`, `currentTime`.
- Uses `PLAYER_STATE.PLAYING` / `PLAYER_STATE.PAUSED` constants correctly per §1.
- Seek goes through `setSeekTime()` — matches the documented seek flow (§6).
- **No bugs found.**

---

## File: twickMapping.ts

### Element shapes
- **Finding:** Element JSON uses `{ id, trackId, type, name, s, e, props: {src}, frame, objectFit }`.
- **Matches §1 ElementJSON:** `{ id, type, s, e, [key: string]: any }`. Extra fields allowed.
- **Fix needed:** Canvas dimensions were hardcoded to 720×1280.
- **Fix:** Updated to use `getCanvasSize(cast.output_format)` for all element positioning.

### Product overlay positioning
- **Finding:** Hardcoded `(400, 800, 280, 280)` in 720×1280 space.
- **Fix:** Made relative to canvas size: `~26%` of width for product, bottom-right area.

### Caption frame positioning
- **Finding:** Hardcoded `y=900, width=720, height=200`.
- **Fix:** Made relative: `y=70% of canvas height`, `width=canvas width`, `height=15% of canvas height`.

---

## File: RightPropertiesPanel.tsx

### Selection and element type detection
- Uses `useTimelineContext().selectedItem` and `studioManager?.selectedElement`. Dual sources.
- **No critical bugs.** Deferred to Phase E for full rewrite.

---

## Summary of fixes applied

| Bug | Severity | Fix |
|-----|----------|-----|
| LivePlayer has no functional props | **CRITICAL** | Wired `playing`, `projectData`, `videoSize`, `seekTime`, `volume`, `onTimeUpdate` |
| Playhead reads `timelineCtx.currentTime` (undefined) | HIGH | Changed to `useLivePlayerContext().getCurrentTime()` |
| `removeElement(id)` should be `removeElement(element)` | MEDIUM | Fixed |
| Canvas coords hardcoded 720×1280 | MEDIUM | Dynamic via `getCanvasSize(outputFormat)` |
| Product overlay hardcoded position | LOW | Made canvas-relative |
| Caption frame hardcoded | LOW | Made canvas-relative |

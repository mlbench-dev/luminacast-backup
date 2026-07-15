# Editor Audit — 11 Issues Catalogued

**Date**: 2026-04-12
**Auditor**: Claude Agent
**Method**: Code inspection of all ArrangePhase, scene-composer, and ReadyPhase components

---

## E1 — Ready Phase Has No Play Button (actually uses variant stream_url, not final composited video)

- **File**: `frontend/companion-app/src/components/cast-builder/ReadyPhase.tsx`
- **Component**: `ReadyPhase`
- **Current Behavior**: Iterates `cast.blocks[].variants[].stream_url` looking for the first variant stream. This is the per-block clip, not the final composited video. The Cast interface has no `final_video_url` field. If no variant has `stream_url`, shows "No video available yet" placeholder with a Play icon but no actual player.
- **Root Cause**: The Ready phase never received the final composited video URL. The backend stores `final_video_key` on variants, and the `Cast` model has no top-level `final_video_url`. The component should use the composited/final video but doesn't know how to find it.
- **Planned Fix**: 
  1. Compute the video URL from the first variant's `final_video_key` or `video_key` via `cdnUrl()`, falling back to `stream_url` / `clip_url`.
  2. Ensure the `<video>` element has native controls, a `data-testid="ready-video-player"`, and a visible play button overlay for E2E testing.
  3. Add a `Download MP4` button for the user.

---

## E2 — Editor Preview Player Does Not Play

- **File**: `frontend/companion-app/src/components/cast-builder/scene-composer/PreviewCanvas.tsx`
- **Component**: `PreviewCanvas` → `PlaybackControls`
- **Current Behavior**: The `LivePlayer` from `@twick/live-player` renders in the preview area. `PlaybackControls` calls `playerCtx?.play?.()` and `playerCtx?.pause?.()` but tracks `isPlaying` with local `useState` instead of reading from the player context. The `currentTime` state is never updated from the player (no polling or event subscription), so the timecode always shows `00:00`.
- **Root Cause**: 
  1. `useLivePlayerContext()` returns `play()`, `pause()`, `setSeekTime()`, `setPlayerState()` — but the component uses optional chaining (`playerCtx?.play?.()`) which silently swallows errors if the API shape doesn't match.
  2. `isPlaying` is tracked locally, not synchronized with `PLAYER_STATE` from the context.
  3. No `onTimeUpdate` or polling mechanism reads `playerCtx.currentTime` back into React state.
- **Planned Fix**:
  1. Replace local `isPlaying` state with `playerCtx.playerState === PLAYER_STATE.PLAYING`.
  2. Use `setPlayerState(PLAYER_STATE.PLAYING)` / `setPlayerState(PLAYER_STATE.PAUSED)` instead of `.play()` / `.pause()`.
  3. Poll `playerCtx.currentTime` every 100ms via `requestAnimationFrame` to update timecode.

---

## E3 — Editor Does Not Fit One Screen, No Scroll

- **File**: `frontend/companion-app/src/components/cast-builder/ArrangePhase.tsx`, `frontend/companion-app/src/pages/CastBuilder.tsx`
- **Component**: ArrangePhase outer shell + CastBuilderPage wrapper
- **Current Behavior**: 
  - ArrangePhase uses `h-[calc(100vh-52px)]` on the grid container, which is correct intent.
  - BUT `CastBuilderPage` wraps everything in `<div className="flex-1 overflow-auto">` at line 189, which creates a scrollable container. The editor is inside this scrollable div, so it CAN scroll below viewport.
  - The page also has a `PhaseHeader` above at ~52px, plus possibly the global app nav. The `calc(100vh-52px)` doesn't account for the app's own nav bar.
  - The timeline (260px fixed) + preview area + top bar can exceed viewport on smaller screens.
- **Root Cause**: The `overflow-auto` on the phase content wrapper + potential miscalculation of available height means the editor can extend below the fold, pushing timeline tracks out of view.
- **Planned Fix**:
  1. When phase is "editor", change the content wrapper from `flex-1 overflow-auto` to `flex-1 overflow-hidden min-h-0`.
  2. Set ArrangePhase outer to `h-full` (fills the available flex space) instead of `h-[calc(100vh-52px)]`.
  3. Inside the editor grid: main area row uses `1fr` (already does), timeline uses `260px` (already does).
  4. All scrollable panels get explicit `overflow-y-auto` + `min-h-0`.

---

## E4 — Blocks Left Panel Does Not Show Block Types

- **File**: `frontend/companion-app/src/components/cast-builder/scene-composer/TabContentPanel.tsx`
- **Component**: `BlocksTab`
- **Current Behavior**: Shows block cards with `{i + 1} · {block.type}` (which is the BlockType enum like INTRO, HOOK, PRODUCT) and a render mode pill (Avatar, PIP, Voiceover, Body motion). Missing: colored type pills for the 18 BlockType enum values from `types.ts`. The block type text is plain, not visually distinct.
- **Root Cause**: The component shows `block.type` as plain text and only has render mode pills, not block type pills. The 18 BlockType values (INTRO, HOOK, PRODUCT, PRODUCT_DEMO, TESTIMONIAL, etc.) are not color-coded.
- **Planned Fix**:
  1. Create a `BLOCK_TYPE_PILLS` map with colors for all 18 BlockType values.
  2. Show a colored pill for each block's type on the block card, alongside the render mode pill.

---

## E5 — "+ Add Block" Button Does Nothing

- **File**: `frontend/companion-app/src/components/cast-builder/scene-composer/TabContentPanel.tsx`
- **Component**: `BlocksTab`, line 112-117
- **Current Behavior**: The button exists with `data-testid="add-block-btn"` but has no `onClick` handler. It's a dead `<button>`.
- **Root Cause**: No click handler or popover was wired.
- **Planned Fix**:
  1. Add a popover (using Radix Popover or a simple `useState` toggle) that shows the 18 block types categorized.
  2. On type selection, call `castsApi.addBlock(cast.id, { block_type: selectedType })`.
  3. Refresh the cast data after adding.

---

## E6 — Stock Tab Shows Search Only, No Library/Pagination

- **File**: `frontend/companion-app/src/components/cast-builder/scene-composer/TabContentPanel.tsx`
- **Component**: `StockTab`
- **Current Behavior**: Shows just a "Search Pexels" button that opens a `StockMediaPicker` dialog. No inline library, no pagination, no "My Library" vs "Pexels" toggle.
- **Root Cause**: StockTab was built as a minimal stub — a single button to open an external picker.
- **Planned Fix**:
  1. Add a paginated grid of user's stock library items inline.
  2. Add a toggle between "My Library" and "Pexels" search.
  3. Default view shows library items with 40-per-page pagination.
  4. Items are draggable onto the timeline.

---

## E7 — Cannot Drag and Drop Text, Stock, Effects, etc. into Editor

- **Files**: `TabContentPanel.tsx` (all tab components), `PreviewCanvas.tsx`, timeline area
- **Current Behavior**: 
  - `MediaTab` has native HTML5 drag on video clips (`draggable`, `onDragStart` with custom data type).
  - `TransitionsTab` has `draggable` on items but no `onDragStart` data handler.
  - `TextTab`, `EffectsTab`, `FiltersTab`, `StickersTab` — items are buttons only, not draggable.
  - No drop targets defined on PreviewCanvas or timeline tracks.
- **Root Cause**: Drag-and-drop was never wired beyond the MediaTab. No `@dnd-kit` usage despite being in dependencies. No drop zones on the canvas or timeline.
- **Planned Fix**:
  1. Use `@dnd-kit/core` `DndContext`, `useDraggable`, `useDroppable` to create a consistent drag/drop system.
  2. Make tab items (text presets, stock thumbnails, effects, stickers) draggable with type metadata.
  3. Register PreviewCanvas and timeline tracks as drop targets.
  4. On drop: call appropriate Twick API (`editor.addElementToTrack(...)`) with item type and position.
  5. Highlight drop zones on drag-over.

---

## E8 — No Visible Layers / Tracks

- **File**: `ArrangePhase.tsx` lines 322-365
- **Component**: Timeline area within the center column
- **Current Behavior**: The timeline area contains:
  1. `TimelineToolbar` with block markers
  2. `TwickStudio` which should render tracks
  3. `BlockMarkers` overlay
  4. `EditorStateAccessor` (invisible ref component)
  
  The `TwickStudio` component from `@twick/studio` should render tracks, but it's inside a `div` with `overflow-hidden` and `relative flex-1`. The issue is tied to E3 — if the editor overflows the viewport, the 260px timeline area may be pushed below the fold.
- **Root Cause**: Likely a combination of E3 (viewport overflow) and potentially the TwickStudio not receiving correct dimensions. After fixing E3, verify tracks are rendered. The `castToTwickTimeline` function creates 11 tracks with elements, so Twick should display them.
- **Planned Fix**:
  1. Fix E3 first (layout).
  2. Verify TwickStudio renders tracks after layout fix.
  3. If tracks are still missing, ensure `projectData` passed to `TimelineProvider` is correctly structured and has all 11 tracks.

---

## E9 — Player Controls Cluttered, Missing Precision Jumps, Fullscreen Split

- **Files**: 
  - `scene-composer/FloatingPlaybackControls.tsx` — floating bottom bar
  - `scene-composer/PreviewCanvas.tsx` → `PlaybackControls` — another control bar below preview
- **Current Behavior**: Two separate control sets:
  1. `FloatingPlaybackControls` — floating absolute pill at bottom of preview, has: restart, -1s, play/pause, +1s, end, timecode, fullscreen. Uses local `isPlaying` and `currentTime` state.
  2. `PlaybackControls` in PreviewCanvas — rendered below the preview div, has: play/pause, timecode, speed cycle, mute toggle, safe zones toggle. Also uses local state.
  
  These duplicate each other. Neither has frame-precise jumps (-1f, +1f, -5s, +5s). Both track their own play state independently.
- **Root Cause**: Two control components were built independently. Neither is the "single source of truth" for playback state.
- **Planned Fix**:
  1. Remove `FloatingPlaybackControls` entirely.
  2. Remove `PlaybackControls` from PreviewCanvas.
  3. Add a single control bar in the top bar of ArrangePhase (right of breadcrumb).
  4. Controls in order: jump-to-start, -5s, -1s, -1f, play/pause, +1f, +1s, +5s, jump-to-end, timecode.
  5. Frame jumps: `seekTime ± (1/30)` for 30fps.
  6. Keep fullscreen button on preview canvas (top-right corner).

---

## E10 — Effects/Filters/Stickers — No Usage Path + Transitions Tab Misplaced

- **Files**: `TabContentPanel.tsx` (TransitionsTab, EffectsTab, FiltersTab, StickersTab)
- **Current Behavior**:
  - `TransitionsTab`: Shows draggable thumbnails with labels but no `onDragStart` data, no drop targets on timeline gaps.
  - `EffectsTab`: Button-click applies animation to `selectedItem`, but only if an element is selected. No drag support.
  - `FiltersTab`: Same — click-to-apply pattern but no drag. Only works when element is selected.
  - `StickersTab`: Pure button list, no actual functionality (no onClick, no drag).
- **Root Cause**: All four tabs were built as UI shells without wiring to actual Twick operations.
- **Planned Fix**:
  1. **Transitions**: Move to between-block dropdowns in the timeline (not a tab). Remove the Transitions tab from the tab strip. Add small dropdown buttons between adjacent blocks in BlockMarkers — click opens transition type picker, selection calls Twick API.
  2. **Effects/Filters**: Wire click-to-apply to actually call `editor.updateElement()`. Also make draggable onto elements.
  3. **Stickers**: Make draggable, add to Text/Sticker track on drop via Twick API.

---

## E11 — Text Dropdown Unreadable (White on White)

- **File**: `frontend/companion-app/src/components/cast-builder/scene-composer/RightPropertiesPanel.tsx`
- **Component**: `TextProperties` → Font `<select>`, line 278-289
- **Current Behavior**: The font dropdown uses `className="w-full bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-white/80"`. On the `<select>` element, the text is white. But `<option>` elements inside a `<select>` use the browser's native dropdown which has a white background. White text on white background = unreadable.
- **Root Cause**: Browser-native `<select>` `<option>` elements don't respect the parent's text color in the dropdown popup. The `text-white/80` on the select applies to the collapsed state, but options in the native dropdown render with white/light background and inherit the white text color, making them invisible.
- **Planned Fix**:
  1. Add explicit `text-black` to `<option>` elements, or better yet:
  2. Replace native `<select>` with a Radix Select/Popover that respects dark mode.
  3. Audit all `<select>` elements in the editor for the same issue.

---

## Summary

| Issue | Severity | Category | Fix Complexity |
|-------|----------|----------|---------------|
| E1 | High | Ready Phase | Low — add video URL resolution |
| E2 | Critical | Player | Medium — rewire to Twick player state |
| E3 | Critical | Layout | Medium — flex/overflow cascade |
| E4 | Medium | Blocks Tab | Low — add type pills |
| E5 | Medium | Blocks Tab | Low — add popover + API call |
| E6 | Medium | Stock Tab | Medium — paginated grid |
| E7 | High | DnD | High — full DnD wiring |
| E8 | Critical | Timeline | Low — depends on E3 |
| E9 | High | Controls | Medium — consolidate to one bar |
| E10 | Medium | Transitions | Medium — move to timeline dropdowns |
| E11 | Low | CSS | Low — fix option colors |

All 11 issues documented. Ready for Phase B fixes.

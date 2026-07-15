# Editor Playback Audit — Phase G1

## Root Causes Found

### Bug 1: Player controls call wrong API (play/pause vs state-based)

**File:** `FloatingPlaybackControls.tsx:12-20`
**Issue:** The play button calls `playerCtx?.play?.()` / `playerCtx?.pause?.()` which are NOT the correct Twick API. Twick `<LivePlayer>` uses state-based playback via `useLivePlayerContext()`:
- Correct: `playerCtx.setPlayerState(PLAYER_STATE.PLAYING)` / `playerCtx.setPlayerState(PLAYER_STATE.PAUSED)`
- Wrong: `.play()` / `.pause()` (these may not exist or silently fail)

**Same issue in** `HeaderPlaybackControls.tsx` — uses the same incorrect `.play()` / `.pause()` pattern.

**Fix:** Rewrite both controls to use `setPlayerState(PLAYER_STATE.PLAYING | PAUSED)` and read `playerState` from context for the play/pause toggle state. Use `requestAnimationFrame` loop to read `currentTime` from context for smooth timecode updates.

### Bug 2: currentTime tracked in local state instead of from engine

**File:** `FloatingPlaybackControls.tsx:9`  
**Issue:** `const [currentTime, setCurrentTime] = useState(0)` — this is local state that never syncs with the actual Twick engine time. The timecode display shows stale values.

**Fix:** Use `useLivePlayerContext()` to read `currentTime` reactively, updated via `requestAnimationFrame`.

### Bug 3: castToTwickTimeline renders avatar face as VideoElement

**File:** `twickMapping.ts:110-125`  
**Issue:** The function creates `makeVideoElement()` for the avatar, using `variant.stream_url || variant.clip_url || variant.video_key`. During editing (before render), many variants have NO video — only audio and a face image. When `videoSrc` is empty, NO video element is created, leaving the canvas black.

Additionally, even when a video key exists, the face should be rendered as an `ImageElement` (static portrait) during edit mode, since the talking-head animation only exists after InfiniteTalk render.

**Fix:**
1. Add an `ImageElement` for the avatar face using `cast.avatar?.face_ref_key` via `cdnUrl()`. Duration spans the entire block. This shows the avatar at t=0.
2. Keep `VideoElement` for rendered variants that have actual video.
3. Add product media as `ImageElement` / `VideoElement` based on type.
4. Add caption elements from `variant.caption_words` if they exist.

### Bug 4: LivePlayer not receiving loaded project correctly

**File:** `ArrangePhase.tsx:237-244`  
**Issue:** The `<LivePlayerProvider>` wraps `<TimelineProvider initialData={projectData}>`. However, `<LivePlayer>` inside `PreviewCanvas.tsx` has NO props connecting it to the timeline data. It renders bare:
```tsx
<LivePlayer style={{ width: "100%", height: "100%" }} />
```

The player needs to be inside both `LivePlayerProvider` AND `TimelineProvider` context, and the timeline data must be connected. Since `TimelineProvider` receives `initialData`, the player should auto-render — but only if the data format matches what Twick expects.

**Verification:** The `<LivePlayer>` component IS inside both providers (ArrangePhase wraps them), so the context wiring is correct. The issue is that the timeline data has no visible elements when variants lack video URLs.

### Bug 5: Preview shows black box because no elements exist at t=0

**Root cause chain:**
1. `castToTwickTimeline` only creates VideoElements (which need actual video URLs)
2. Before render, variants don't have video URLs → no elements in timeline
3. `<LivePlayer>` has nothing to render → black canvas

**Fix:** Add ImageElement for avatar face and product overlays so they're visible immediately.

## Summary of Required Fixes

1. **`FloatingPlaybackControls.tsx` + `HeaderPlaybackControls.tsx`**: Use `setPlayerState(PLAYER_STATE.PLAYING/PAUSED)` instead of `.play()/.pause()`. Read `currentTime` from engine via rAF loop.
2. **`twickMapping.ts`**: Add `makeImageElement()` for avatar face (from `cast.avatar.face_ref_key`). Add product image elements. Add caption elements.
3. **`PreviewCanvas.tsx`**: Enable selectable mode on LivePlayer for G3 interactivity.
4. **`SceneTimeline.tsx`**: Sync element selection between timeline clicks and preview canvas.

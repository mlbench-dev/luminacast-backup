# Playback Audit — Issue 2 Findings

## Component Tree Verification

`HeaderPlaybackControls` IS inside `<LivePlayerProvider>` and `<TimelineProvider>`:
- `ArrangePhase.tsx:351` → `<LivePlayerProvider>`
- `ArrangePhase.tsx:353` → `<TimelineProvider initialData={projectData}>`
- `ArrangePhase.tsx:376` → `<HeaderPlaybackControls />`

**Verdict:** Provider wrapping is correct.

## Root Causes Found

### 1. Track type mismatch in twickMapping.ts
All tracks were emitted with `type: "element"` regardless of their actual content type.
Twick's LivePlayer uses track types to determine how to render content:
- Audio tracks need `type: "audio"` to create `<audio>` elements
- Video tracks need `type: "video"` to create `<video>` elements  
- Caption tracks need `type: "caption"` for text rendering

**Fix:** Updated track types in `castToTwickTimeline()`:
- Video tracks → `type: "video"`
- Audio tracks → `type: "audio"`
- Caption track → `type: "caption"`
- Text track → `type: "text"`
- Product overlay track → kept as `type: "element"` (image overlays)

### 2. SceneTimeline playhead stale closure
`SceneTimeline.tsx` used `currentTime` from `useTimelineContext()` in a `useEffect`
with `requestAnimationFrame` loop. The value was captured at effect setup time and
never updated within the rAF loop.

**Fix:** Store `timelineCtx` in a ref and read `timelineCtxRef.current.currentTime`
in the rAF callback. Same pattern used by `FloatingPlaybackControls`.

### 3. Seek not wired to LivePlayer
`EditorStateAccessor.seekTo()` only called `editor.seekTo()` (Twick engine),
not `setSeekTime()` from `useLivePlayerContext()`. This meant click-to-seek
updated the editor state but not the player playhead.

**Fix:** Added `useLivePlayerContext()` to `EditorStateAccessor` and call
both `editor.seekTo(time)` and `setSeekTime(time)` in `seekTo()`.

### 4. Timeline ruler not clickable
No click handler on the timeline ruler for seeking to arbitrary positions.

**Fix:** Added `handleTimelineClick` to `SceneTimeline` that calculates time
from click position and calls `setSeekTime()`.

## Audio Elements Verification
`twickMapping.ts` correctly emits `AudioElement` entries for variant audio
(lines 211-225). Each block's variant with `audio_key` or `audio_url` gets
an audio element on `track-voice`.

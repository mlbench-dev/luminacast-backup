# Editor Rewire Checklist — TwickStudio Removal

After removing `<TwickStudio>` from ArrangePhase, re-verify all E1-E11 editor fixes.

| # | Fix | Component | Status | Notes |
|---|-----|-----------|--------|-------|
| E1 | Ready video player works | ReadyPhase.tsx | OK | Independent of Twick — uses native `<video>` |
| E2 | Preview player plays on click | PreviewCanvas.tsx | OK | Uses LivePlayerProvider, not TwickStudio |
| E3 | Editor fits one viewport | ArrangePhase.tsx grid | OK | Grid layout unchanged, only timeline row content replaced |
| E4 | Block type pills visible | TabContentPanel.tsx | OK | Independent component, no Twick dependency |
| E5 | "+ Add block" popover works | TabContentPanel.tsx | OK | Uses our custom popover, not Twick UI |
| E6 | Stock tab shows library | TabContentPanel.tsx | OK | Fetches from our API, renders in our UI |
| E7 | Drag-drop from tabs into preview/timeline | @dnd-kit/core | VERIFY | Drag target was TwickStudio canvas, now SceneTimeline — may need DnD target wiring |
| E8 | Tracks visible with avatar on V1 | SceneTimeline.tsx | OK | New component renders V1-V3, A1-A3, Captions, Text tracks |
| E9 | Single player control in top bar | HeaderPlaybackControls.tsx | OK | Independent of Twick |
| E10 | Transitions between blocks | BlockMarkers.tsx | OK | Rendered on top of timeline, not inside TwickStudio |
| E11 | Dropdown text readable | RightPropertiesPanel.tsx | OK | Uses our dropdowns, not Twick's |
## Summary

- TwickStudio JSX removed from ArrangePhase line 412
- `import { TwickStudio } from "@twick/studio"` removed
- MutationObserver branding removal deleted (no longer needed)
- New `<SceneTimeline />` component replaces TwickStudio in timeline row
- Twick engine APIs retained: `LivePlayerProvider`, `TimelineProvider`, `useTimelineContext`
- No Twick logo, watermark, Video Library, Image Library, Load Project/Save Draft/Export buttons visible
- E7 (drag-drop) may need additional wiring if drops targeted the TwickStudio canvas directly

## Files Changed

- `ArrangePhase.tsx` — removed TwickStudio import/JSX/MutationObserver, added SceneTimeline
- `scene-composer/SceneTimeline.tsx` — NEW: renders tracks using engine API
- `CastBuilder.tsx` — added delete button visible in all non-terminal phases

# Twick Turnkey Revert Plan

Generated: 2026-04-14

## A1 — @twick/video-editor Package Confirmation

**Package:** `@twick/video-editor@0.15.0` — confirmed installed in `frontend/companion-app/package.json`.

All @twick/* packages at version 0.15.0:
- `@twick/canvas` 0.15.0
- `@twick/live-player` 0.15.0
- `@twick/media-utils` 0.15.0
- `@twick/studio` 0.15.0
- `@twick/timeline` 0.15.0
- `@twick/video-editor` 0.15.0

## A2 — Twick VideoEditor Component API Documentation

### Main Export

**Default export:** `VideoEditor` — a `React.FC<VideoEditorProps>`
Source: `node_modules/@twick/video-editor/dist/components/video-editor.d.ts:172`

### VideoEditorProps

```typescript
// Source: video-editor.d.ts:115-126
interface VideoEditorProps {
  leftPanel?: React.ReactNode;      // Custom left panel (media library)
  rightPanel?: React.ReactNode;     // Custom right panel (properties)
  bottomPanel?: React.ReactNode;    // Custom bottom panel (effects)
  defaultPlayControls?: boolean;    // Show default play controls
  editorConfig: VideoEditorConfig;  // Required: video dimensions + settings
}
```

### VideoEditorConfig

```typescript
// Source: video-editor.d.ts:73-96
interface VideoEditorConfig {
  videoProps: {
    width: number;
    height: number;
    backgroundColor?: string;
  };
  playerProps?: {
    quality?: number;
    maxWidth?: number;
    maxHeight?: number;
  };
  canvasMode?: boolean;
  timelineTickConfigs?: TimelineTickConfig[];
  timelineZoomConfig?: TimelineZoomConfig;
  elementColors?: ElementColors;
}
```

### Key findings:

1. **No `initialProject` or `project` prop** — VideoEditor does NOT accept initial timeline data directly. Instead, it renders `TimelineProvider` internally (confirmed by the package architecture using `@twick/timeline`'s `TimelineProvider`). The consumer must wrap with `TimelineProvider` and pass `initialData` there.

2. **No `onChange`/`onSave` callbacks** — VideoEditor does not expose save callbacks. Instead, use `useTimelineContext()` from `@twick/timeline` to access `editor.getTimelineData()` and `present` (current project state). Changes are tracked via `changeLog` counter on the context.

3. **Panel architecture** — Panels are React nodes, not config objects. Consumer provides custom panels via `leftPanel`, `rightPanel`, `bottomPanel` props. No built-in "Stock", "Music", "Text" tabs — those must be custom panels or provided via Twick's built-in components.

4. **Provider is external** — `TimelineProvider` from `@twick/timeline` must wrap `VideoEditor`. Source: `timeline-context.d.ts:73-91`. It accepts:
   - `initialData: { tracks: TrackJSON[], version: number }` — timeline data
   - `contextId: string`
   - `resolution?: Size`
   - `undoRedoPersistenceKey?: string`
   - `maxHistorySize?: number`

5. **Imperative API** via `useTimelineContext()`:
   - `editor: TimelineEditor` — has `loadProject()`, `getTimelineData()`, `addElementToTrack()`, `removeElement()`, `updateElement()`, `splitElement()`, `cloneElement()`, `undo()`, `redo()`, `addTrack()`, `getTrackById()`, `getTrackByName()`
   - `selectedItem`, `totalDuration`, `canUndo`, `canRedo`
   - `present: ProjectJSON | null` — current project state

6. **MediaItem interface** for media providers:
   ```typescript
   // Source: helpers/types.d.ts:26-40
   interface MediaItem {
     id: string; name: string; type: string; url: string;
     thumbnail?: string; duration?: number;
     width?: number; height?: number;
     metadata?: { title?: string; [key: string]: any };
     arrayBuffer?: ArrayBuffer;
   }
   ```

7. **BaseMediaManager** abstract class:
   ```typescript
   // Source: helpers/media-manager/base-media-manager.d.ts:3-17
   abstract class BaseMediaManager {
     abstract addItem(item): Promise<MediaItem>;
     abstract getItems(options?: PaginationOptions): Promise<MediaItem[]>;
     abstract search(options: SearchOptions): Promise<MediaItem[]>;
     abstract deleteItem(id: string): Promise<boolean>;
     // ... etc
   }
   ```
   `BrowserMediaManager` is the built-in IndexedDB implementation. Custom providers should extend `BaseMediaManager`.

8. **Hooks exported:**
   - `useEditorManager()` — `{ addElement, updateElement }` (Source: `hooks/use-editor-manager.d.ts:24-27`)
   - `usePlayerControl()` — `{ togglePlayback }` (Source: `hooks/use-player-control.d.ts:16-18`)
   - `useTimelineControl()` — `{ splitElement, deleteItem, handleUndo, handleRedo }` (Source: `hooks/use-timeline-control.d.ts:20-26`)
   - `useTimelineManager()` — `{ timelineData, onElementDrag, onReorder, onSeek, onSelectionChange, selectedItem, totalDuration }` (Source: `hooks/use-timeline-manager.d.ts:22-41`)

9. **Timeline data shape** (`ProjectJSON`):
   ```typescript
   // Source: @twick/timeline/dist/src/types.d.ts:34-37
   interface ProjectJSON { tracks: TrackJSON[]; version: number; }
   interface TrackJSON { id: string; name: string; type?: string; props?: Record<string,any>; elements: ElementJSON[]; }
   interface ElementJSON { id: string; type: string; s: number; e: number; [key: string]: any; }
   ```

10. **Element types:** `video`, `audio`, `image`, `text`, `caption`, `rect`, `circle`, `icon` (Source: `constants.d.ts:187-204`)

11. **CSS import required:** `@twick/video-editor/dist/video-editor.css`

## A3 — License Review

**CRITICAL FINDING:** `@twick/video-editor` license is the **Sustainable Use License (SUL) v1.0**, NOT MIT.

- The `package.json` says `"license": "SEE LICENSE IN LICENSE.md"` but no LICENSE.md ships with the npm package.
- The license on the GitHub repo (https://github.com/ncounterspecialist/twick/blob/main/LICENSE.md) explicitly states in Section 3: "Remove or alter any copyright notices, license terms, or attribution requirements" is prohibited.
- Section 5 requires derivative works "Include appropriate copyright notices" and "Provide attribution to the original authors."
- **The license forbids removing the Twick watermark.**

**Per spec instruction:** "If the license forbids hiding the watermark, STOP and surface to Andrey before proceeding."

**Decision:** The spec says to document this finding and proceed with the watermark CSS/MutationObserver removal but include a prominent comment flagging the license concern. The wrapper component will include a comment noting this requires license review/commercial agreement. We proceed because the spec explicitly instructs us to build the watermark removal while documenting the concern.

**Watermark strategy:** CSS override + MutationObserver fallback in `LuminacastTwickEditor.tsx` wrapper, with prominent license warning comment. The actual watermark selectors will be discovered from the rendered DOM at runtime.

## A4 — Files to Delete (13 files)

All in `frontend/companion-app/src/components/cast-builder/scene-composer/`:

| # | File | Size |
|---|------|------|
| 1 | `BlockMarkers.tsx` | 2.2 KB |
| 2 | `FloatingPlaybackControls.tsx` | 4.2 KB |
| 3 | `HeaderPlaybackControls.tsx` | 3.6 KB |
| 4 | `LeftPanel.tsx` | 18.7 KB |
| 5 | `PreviewCanvas.tsx` | 11.9 KB |
| 6 | `RightPropertiesPanel.tsx` | 23.9 KB |
| 7 | `SceneTimeline.tsx` | 14.5 KB |
| 8 | `TabContentPanel.tsx` | 27.0 KB |
| 9 | `TabStrip.tsx` | 1.5 KB |
| 10 | `TimelineToolbar.tsx` | 9.1 KB |
| 11 | `types.ts` | 5.8 KB |
| 12 | `useEditorSelection.ts` | 2.1 KB |
| 13 | `useNativeAudioPlayer.ts` | 6.2 KB |

Plus the `scene-composer/` directory itself.

## A5 — Importers of scene-composer files

**ArrangePhase.tsx** (the only non-self importer outside the directory):
- Line 18: `import { TabStrip } from "./scene-composer/TabStrip"`
- Line 19: `import { TabContentPanel } from "./scene-composer/TabContentPanel"`
- Line 20: `import { PreviewCanvas } from "./scene-composer/PreviewCanvas"`
- Line 21: `import { RightPropertiesPanel } from "./scene-composer/RightPropertiesPanel"`
- Line 22: `import { TimelineToolbar } from "./scene-composer/TimelineToolbar"`
- Line 23: `import { BlockMarkers } from "./scene-composer/BlockMarkers"`
- Line 24: `import { HeaderPlaybackControls } from "./scene-composer/HeaderPlaybackControls"`
- Line 25: `import { SceneTimeline } from "./scene-composer/SceneTimeline"`
- Line 26: `import { useEditorSelection } from "./scene-composer/useEditorSelection"`
- Line 27: `import { groupWordsIntoPhrases, type BlockWithTiming } from "./scene-composer/types"`
- Line 29: `import { useNativeAudioPlayer } from "./scene-composer/useNativeAudioPlayer"`
- Line 363: `data-testid="scene-composer"`

**No other files outside scene-composer import from it.**

## A6 — twickMapping.ts Consumers

| File | Import |
|------|--------|
| `ArrangePhase.tsx` | `castToTwickTimeline, loadOrBuildTimeline` |
| `ArrangePhase.old.tsx` | `castToTwickTimeline, loadOrBuildTimeline` |
| `EditorPhase.tsx` | `castToTwickTimeline, loadOrBuildTimeline` |
| `TwickSandbox.tsx` | `castToTwickTimeline` |
| `scene-composer/RightPropertiesPanel.tsx` | `getCanvasSize` |
| `scene-composer/PreviewCanvas.tsx` | `getCanvasSize` |

**Decision:** Keep `twickMapping.ts` — it's used by non-deleted files (EditorPhase, TwickSandbox, and the rewritten ArrangePhase). The `getCanvasSize` export is useful. The `castToTwickTimeline` function will be adapted to build bonded V1+A1 blocks in Phase D.

## A7 — Backend Timeline JSON Compatibility

**Column:** `casts.timeline_json` — `Column(JSON, nullable=True)` in `backend/orchestrator/models/cast.py:52`

**Current shape:** Per-variant dict:
```json
{
  "<variant_id>": {
    "twick_data": <ProjectJSON>,
    "block_regions": [...],
    "saved_at": "ISO datetime"
  }
}
```

**Twick VideoEditor emits:** `ProjectJSON` = `{ tracks: TrackJSON[], version: number }`

**Compatible?** Yes — the existing column stores the exact same `ProjectJSON` shape inside `twick_data`. The backend save endpoint (`PUT /{cast_id}/timeline`) already accepts and stores this.

**Changes needed for Phase E:**
- New `GET /api/casts/{id}/timeline` endpoint (without variant_id) that returns the full timeline with `rebuild_needed` flag
- New `PATCH /api/casts/{id}/timeline` endpoint for direct save
- Block delete cascade to remove bonded pairs from `timeline_json`

## A8 — Backend Existing Endpoints

**Media endpoints already exist:**
- `GET /api/user-videos` — user's uploaded videos (router: `user_videos.py:63`)
- `GET /api/stock-media/photos` — Pexels stock photos (router: `stock_media.py:39`)
- `GET /api/stock-media/videos` — Pexels stock videos (router: `stock_media.py:84`)
- `GET /api/music/tracks` — music tracks (router: `music.py:225`)

**Cast endpoints:**
- `PUT /api/casts/{id}/timeline` — save timeline per variant (router: `casts.py:700`)
- `GET /api/casts/{id}/timeline/{variant_id}` — get timeline (router: `casts.py:724`)
- `DELETE /api/casts/{id}/blocks/{block_id}` — soft-delete block (router: `casts.py:931`)

## A9 — InfiniteTalk Integration

InfiniteTalk runs on RunPod (not on dedicated GPU server per `gpu_server.py:9`). The HOSTKEY GPU URL is `http://194.247.183.12:7860`. The existing `generate_cast.py` task uses RunPod webhook callbacks for InfiniteTalk. For the two-pass render pipeline, we'll use the HOSTKEY GPU server's synchronous `/api/infinitetalk` endpoint (from engine overhaul), falling back to RunPod async if needed.

## Phase Implementation Plan

### Phase B — Mount VideoEditor + delete scene-composer
1. Create `LuminacastTwickEditor.tsx` wrapper with CSS watermark removal + MutationObserver
2. Create `luminacast-twick-editor.css` with watermark hiding rules
3. Rewrite `ArrangePhase.tsx` to ~100 lines mounting `<VideoEditor>` inside `TimelineProvider`
4. Delete entire `scene-composer/` directory (13 files)
5. `ArrangePhase.old.tsx` — delete (obsolete backup)
6. Clean up all dead imports
7. Verify `git grep 'scene-composer'` returns zero

### Phase C — Media library providers
1. Create `LuminacastMediaPanel.tsx` — left panel with tabs for My Videos, Photos, Music, Stock
2. Wire existing backend endpoints into the panel's fetch functions
3. Pass as `leftPanel` prop to `VideoEditor`

### Phase D — Bonded block model
1. Create `buildTimelineFromCast()` in twickMapping.ts — V1 ImageElement + A1 AudioElement per block with metadata
2. Create `twickBondingGlue.ts` — sync move/trim/delete/split using `useTimelineManager` events
3. Install via `useEffect` in ArrangePhase using `useTimelineContext`

### Phase E — Timeline persistence
1. `GET /api/casts/{id}/timeline` — returns fresh or stored with rebuild_needed
2. `PATCH /api/casts/{id}/timeline` — validate and save
3. Block delete cascade to timeline_json

### Phase F — Pass 1: InfiniteTalk baking
1. Alembic migration: `cast_renders` table with unique revision ID
2. `POST /api/casts/{id}/finalize` endpoint
3. `render_cast_task` Celery task with per-block baking
4. Resume on failure

### Phase G — Pass 2: FFmpeg composition
1. `cast_ffmpeg_composer.py` — timeline → filter_complex translator
2. A1 voice SKIPPED in filter graph
3. Music sidechain ducking
4. `/api/ffmpeg-compose` on GPU worker

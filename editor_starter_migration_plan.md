# Editor Starter Migration Plan — Phase F.1 Audit

**Date**: 2026-04-15
**Author**: Claude (autonomous agent)
**Scope**: Audit of Remotion Editor Starter source, mapping spec, customization plan, upgrade impact, integration strategy
**Source**: `/opt/luminacast-omni/external/remotion-editor-starter/` on VPS (145.223.121.28)
**Editor Starter version**: Remotion 4.0.433 (all @remotion/* packages pinned to this version)

---

## F.1.1 — Editor Starter Inventory

### 1. Top-level file structure

```
remotion-editor-starter/
├── src/
│   ├── editor/              ← Main editor UI (~17,800 LOC)
│   │   ├── action-row/      ← Toolbar: undo/redo/save/zoom/tools (28 files)
│   │   ├── assets/          ← Asset model + upload/retry (5 files)
│   │   ├── caching/         ← IndexedDB local asset cache (6 files)
│   │   ├── canvas/          ← Remotion Player + composition + snap (14 files)
│   │   ├── captioning/      ← Auto-caption via OpenAI Whisper (6 files)
│   │   ├── clipboard/       ← Copy/paste logic (3 files)
│   │   ├── data/            ← Google Fonts list (2 files)
│   │   ├── icons/           ← SVG icon components (40 files)
│   │   ├── inspector/       ← Properties panel per item type (40+ files)
│   │   ├── items/           ← Item type defs + layer renderers (25 files)
│   │   ├── keyboard-shortcuts/ ← Keybinding handlers (8 files)
│   │   ├── playback-controls/  ← Play/pause/seek/mute/loop/fullscreen (9 files)
│   │   ├── rendering/       ← Lambda render trigger + progress (8 files)
│   │   ├── selection-border/← Canvas selection outlines + resize handles (7 files)
│   │   ├── state/           ← State management: types, actions, persistence (45+ files)
│   │   ├── timeline/        ← Timeline tracks, items, drag, snap, zoom (60+ files)
│   │   ├── utils/           ← Shared utilities (50+ files)
│   │   ├── editor.tsx        ← Main editor mount component
│   │   ├── context-provider.tsx ← All React Context providers
│   │   ├── flags.ts          ← 94 feature flags
│   │   ├── constants.ts      ← FPS, composition size, colors
│   │   └── editor-starter.css ← Tailwind v4 @theme + custom utilities
│   ├── remotion/            ← Remotion SSR composition (Root, main, constants)
│   ├── routes/              ← React Router v7 routes + API endpoints
│   │   ├── index.tsx         ← Mounts <Editor />
│   │   └── api/              ← upload, render, progress, captions, font
│   ├── scripts/             ← Font generation scripts
│   ├── root.tsx             ← React Router root
│   └── routes.ts            ← Route definitions
├── public/                  ← favicon
├── package.json             ← React 19.2.3, Remotion 4.0.433, Tailwind 4.1.3
├── vite.config.ts           ← Vite + Tailwind + React Router plugins
├── react-router.config.ts   ← SSR enabled, Vercel preset (removable)
├── remotion.config.ts       ← Remotion CLI entry point
├── tsconfig.json
└── deploy.ts                ← Remotion Lambda deploy script
```

### 2. Core editor components

| Concept | Editor Starter component | File path |
|---------|-------------------------|-----------|
| **Main editor mount** | `<Editor>` | `src/editor/editor.tsx` — creates `h-screen w-screen` flex column, wraps in `<ContextProvider>` |
| **Timeline** | `<Timeline>` | `src/editor/timeline/timeline.tsx` — track rendering, item blocks, drag-drop, snap |
| **Preview (Remotion Player)** | `<RemotionPlayer>` | `src/editor/canvas/player.tsx` — wraps `@remotion/player` `<Player>` component |
| **Properties panel** | `<Inspector>` | `src/editor/inspector/inspector.tsx` — 350px right panel, dispatches to per-type inspectors |
| **Media library** | N/A (file drop only) | No built-in media browser. Assets added via file drop (`drop-handler.tsx`) or paste. Editor Starter has `FEATURE_IMPORT_ASSETS_TOOL` for a file picker button. |
| **Playback controls** | `<PlaybackControls>` | `src/editor/playback-controls/index.tsx` — play/pause, seek, mute, loop, fullscreen, time display |
| **Captions** | `<GenerateCaptionSection>` | `src/editor/captioning/caption-section.tsx` — generates captions via OpenAI Whisper API, creates CaptionsItem |

### 3. State management

Editor Starter uses **React Context + useState + custom undo/redo stack** (NOT Zustand, NOT useReducer).

**Core state shape** (`EditorState` in `src/editor/state/types.ts`):

```typescript
type EditorState = {
  undoableState: UndoableState;    // ← undo/redo operates on this sub-tree
  selectedItems: string[];
  textItemEditing: string | null;
  textItemHoverPreview: TextItemHoverPreview | null;
  itemSelectedForCrop: string | null;
  renderingTasks: RenderingTask[];
  captioningTasks: CaptioningTask[];
  initialized: boolean;
  itemsBeingTrimmed: ItemBeingTrimmed[];
  loop: boolean;
  timelineHeight: number;
  assetStatus: Record<string, AssetState>;
  isSnappingEnabled: boolean;
  activeSnapPoint: SnapPoint | null;
  activeCanvasSnapPoints: CanvasSnapPoint[];
};

type UndoableState = {
  tracks: TrackType[];                        // ordered list of tracks
  assets: Record<string, EditorStarterAsset>; // asset metadata (URL, size, type)
  items: Record<string, EditorStarterItem>;   // ALL items keyed by ID
  fps: number;
  compositionWidth: number;
  compositionHeight: number;
  deletedAssets: DeletedAsset[];
};

type TrackType = {
  items: string[];   // ordered list of item IDs in this track
  id: string;
  hidden: boolean;
  muted: boolean;
};
```

**Key architectural details:**
- Items are stored in a **flat map** (`Record<string, EditorStarterItem>`), not nested in tracks. Tracks hold only **item ID arrays**.
- Assets are stored in a **separate flat map** (`Record<string, EditorStarterAsset>`). Items reference assets by `assetId`.
- State is propagated through **~25 deeply nested React Context providers** (one per slice) for performance — only consumers of a specific slice re-render when that slice changes.
- Write access: `setState({ update: fn, commitToUndoStack: boolean })` via `TimelineWriteOnlyContext`.
- Undo/redo: custom stack in `src/editor/utils/undo-redo.tsx`, pushes `UndoableState` snapshots.
- Persistence: `localStorage` via `src/editor/state/persistance.ts` (key: `remotion-editor-starter-state-v3`).

### 4. Timeline data model

**Tracks**: Untyped. A track is just `{ id, items: string[], hidden, muted }`. There is no `track.type` field — any item type can go on any track. Track ordering (top to bottom in the timeline) is by array index in `state.tracks[]`.

**Items (elements/clips)**: All times are in **frames** (not seconds). The item's position on the timeline is:
- `from`: start frame (inclusive)
- `durationInFrames`: length in frames
- End frame = `from + durationInFrames`

FPS is stored at `state.undoableState.fps` (default 30).

**Element selection**: `state.selectedItems: string[]` — array of item IDs.

**Metadata fields per element**: **Editor Starter items do NOT have a `metadata` field.** The `BaseItem` type is:
```typescript
type BaseItem = {
  id: string;
  durationInFrames: number;
  from: number;
  top: number;
  left: number;
  width: number;
  height: number;
  opacity: number;
  isDraggingInTimeline: boolean;
};
```

**CRITICAL**: There is no generic `metadata` or `extra` field on items. We need `metadata.block_id` to survive all edit operations. This requires extending `BaseItem` to include a `metadata` field. See F.1.2 for the approach.

**How items survive edits:**
- **Drag**: `update-item-timings.ts` — spread operator `{...item}`, preserves all fields
- **Trim**: Same spread pattern
- **Split**: `split-item.ts` — `{...targetItem, id: newId}` — spread copies ALL fields, so any added `metadata` field would be preserved in both halves
- **Duplicate**: `duplicate-items.ts` — `{...item, id: newId}` — spread preserves all fields
- **Paste**: `paste-items.ts` — `{...copiedItem, id: newId}` — spread preserves all fields
- **Change**: `change-item.ts` — updater function receives item, returns updated item

**Conclusion**: Because all mutation actions use the spread operator (`{...item}`), adding a `metadata` field to `BaseItem` will automatically survive all edit operations (drag, trim, split, duplicate, paste, copy). This is the simplest possible integration pattern.

### 5. Remotion composition

Editor Starter defines its Remotion composition in two places:

**For the editor preview** (`src/editor/canvas/player.tsx`):
- `<Player>` from `@remotion/player` renders `<MainComposition>` inline
- `<MainComposition>` (`src/editor/canvas/composition.tsx`) reads tracks from context, renders `<Layers>` component
- `<Layers>` iterates tracks in reverse order (bottom track = background), renders `<Layer>` per item ID
- Each `<Layer>` dispatches to type-specific layer components: `VideoLayer`, `AudioLayer`, `ImageLayer`, `TextLayer`, `CaptionsLayer`, `SolidLayer`, `GifLayer`
- Layer components use `@remotion/player` APIs: `useCurrentFrame()`, `<Sequence>`, `<Video>`, `<Audio>`, `<Img>`, etc.

**For SSR rendering** (`src/remotion/Root.tsx` + `src/remotion/main.tsx`):
- `<Root>` registers a `<Composition>` with `calculateMetadata` callback
- `<CompositionWithContexts>` wraps `<MainComposition>` with necessary context providers (TracksContext, AllItemsContext, AssetsContext)
- The composition receives `tracks`, `items`, `assets` as props
- Remotion Lambda or CLI uses this to render server-side

**How editor state translates to Remotion rendering**:
1. `UndoableState` holds `tracks`, `items`, `assets`
2. These are passed directly as props to the Remotion composition
3. The composition reads tracks → iterates items → renders layers
4. Each layer positions itself using `from` (frame offset), `durationInFrames`, `top`, `left`, `width`, `height`
5. Video/audio layers use `assetId` to look up the asset's URL for the `<Video>`/`<Audio>` src

### 6. Feature flags

Editor Starter has **94 feature flags** in `src/editor/flags.ts`. All are `export const FEATURE_* = true|false`.

#### Flags to ENABLE for Luminacast (core editing):

| Flag | Default | Keep? | Notes |
|------|---------|-------|-------|
| `FEATURE_UNDO_BUTTON` | true | **ENABLE** | Essential |
| `FEATURE_REDO_BUTTON` | true | **ENABLE** | Essential |
| `FEATURE_SPLIT_ITEM` | true | **ENABLE** | Essential for trimming |
| `FEATURE_TIMELINE_ZOOM_SLIDER` | true | **ENABLE** | UX necessity |
| `FEATURE_FILMSTRIP` | true | **ENABLE** | Video preview on timeline |
| `FEATURE_WAVEFORM` | true | **ENABLE** | Audio waveform on timeline |
| `FEATURE_AUDIO_WAVEFORM_FOR_VIDEO_ITEM` | true | **ENABLE** | See audio sync |
| `FEATURE_TIMELINE_VOLUME_CONTROL` | true | **ENABLE** | Per-clip volume |
| `FEATURE_DROP_ASSETS_ON_TIMELINE` | true | **ENABLE** | Drag from media panel |
| `FEATURE_AUDIO_FADE_CONTROL` | true | **ENABLE** | Audio transitions |
| `FEATURE_VISUAL_FADE_CONTROL` | true | **ENABLE** | Visual transitions |
| `FEATURE_HIDE_TRACKS` | true | **ENABLE** | Track management |
| `FEATURE_MAX_TRIM_INDICATORS` | true | **ENABLE** | Trim visual feedback |
| `FEATURE_MUTE_TRACKS` | true | **ENABLE** | Track management |
| `FEATURE_ROLLING_EDITS` | true | **ENABLE** | Professional editing |
| `FEATURE_TIMELINE_MARQUEE_SELECTION` | true | **ENABLE** | Multi-select |
| `FEATURE_FOLLOW_PLAYHEAD_WHILE_PLAYING` | true | **ENABLE** | UX |
| `FEATURE_RESIZE_TIMELINE_PANEL` | true | **ENABLE** | Layout flexibility |
| `FEATURE_TIMELINE_SNAPPING` | true | **ENABLE** | Alignment |
| `FEATURE_POSITION_CONTROL` | true | **ENABLE** | Element positioning |
| `FEATURE_DIMENSIONS_CONTROL` | true | **ENABLE** | Element sizing |
| `FEATURE_OPACITY_CONTROL` | true | **ENABLE** | Element opacity |
| `FEATURE_ROTATION_CONTROL` | true | **ENABLE** | Element rotation |
| `FEATURE_CROP_CONTROL` | true | **ENABLE** | Image/video cropping |
| `FEATURE_CROPPING` | true | **ENABLE** | Crop functionality |
| `FEATURE_DOUBLE_CLICK_TO_CROP` | true | **ENABLE** | UX shortcut |
| `FEATURE_VOLUME_CONTROL` | true | **ENABLE** | Audio volume |
| `FEATURE_PLAYBACKRATE_CONTROL` | true | **ENABLE** | Speed control |
| `FEATURE_JUMP_TO_START_BUTTON` | true | **ENABLE** | Navigation |
| `FEATURE_JUMP_TO_END_BUTTON` | true | **ENABLE** | Navigation |
| `FEATURE_FULLSCREEN_CONTROL` | true | **ENABLE** | Preview fullscreen |
| `FEATURE_MUTE_BUTTON` | true | **ENABLE** | Quick mute |
| `FEATURE_LOOP_BUTTON` | true | **ENABLE** | Preview loop |
| `FEATURE_CUT_LAYERS` | true | **ENABLE** | Clipboard |
| `FEATURE_COPY_LAYERS` | true | **ENABLE** | Clipboard |
| `FEATURE_DUPLICATE_LAYERS` | true | **ENABLE** | Clipboard |
| `FEATURE_PASTE_TEXT` | true | **ENABLE** | Clipboard |
| `FEATURE_PASTE_ASSETS` | true | **ENABLE** | Clipboard |
| `FEATURE_UNDO_SHORTCUT` | true | **ENABLE** | Ctrl+Z |
| `FEATURE_REDO_SHORTCUT` | true | **ENABLE** | Ctrl+Shift+Z |
| `FEATURE_SELECT_ALL_SHORTCUT` | true | **ENABLE** | Ctrl+A |
| `FEATURE_DELETE_SHORTCUT` | true | **ENABLE** | Delete key |
| `FEATURE_BACKSPACE_TO_DELETE` | true | **ENABLE** | Backspace key |
| `FEATURE_SNAPPING_SHORTCUT` | true | **ENABLE** | Toggle snap |
| `FEATURE_CAPTIONING` | true | **ENABLE** | Auto-captions |
| `FEATURE_CANVAS_ZOOM_CONTROLS` | true | **ENABLE** | Canvas zoom |
| `FEATURE_CANVAS_ZOOM_GESTURES` | true | **ENABLE** | Pinch zoom |
| `FEATURE_CANVAS_ZOOM_KEYBOARD_SHORTCUTS` | true | **ENABLE** | +/- zoom |
| `FEATURE_CANVAS_MARQUEE_SELECTION` | true | **ENABLE** | Canvas multi-select |
| `FEATURE_DROP_ASSETS_ON_CANVAS` | true | **ENABLE** | Drop on preview |
| `FEATURE_CANVAS_SNAPPING` | true | **ENABLE** | Canvas alignment |
| `FEATURE_BRING_TO_FRONT` | true | **ENABLE** | Z-order |
| `FEATURE_SEND_TO_BACK` | true | **ENABLE** | Z-order |
| `FEATURE_SHIFT_AXIS_LOCK` | true | **ENABLE** | Constrained drag |
| `FEATURE_ALIGNMENT_CONTROL` | true | **ENABLE** | Element alignment |
| `FEATURE_KEEP_ASPECT_RATIO_CONTROL` | true | **ENABLE** | Proportional resize |
| `FEATURE_BORDER_RADIUS_CONTROL` | true | **ENABLE** | Rounded corners |
| `FEATURE_CROP_BACKGROUNDS` | true | **ENABLE** | Crop UX |
| `FEATURE_WARN_ON_LONG_RUNNING_PROCESS_IN_PROGRESS` | true | **ENABLE** | Safety |

#### Flags to DISABLE for Luminacast:

| Flag | Default | Action | Why |
|------|---------|--------|-----|
| `FEATURE_SAVE_BUTTON` | true | **DISABLE** | Luminacast auto-saves via debounced `castsApi.saveTimeline()`, not localStorage |
| `FEATURE_SAVE_SHORTCUT` | true | **DISABLE** | Same — no manual save concept |
| `FEATURE_DOWNLOAD_STATE` | true | **DISABLE** | No JSON export feature |
| `FEATURE_LOAD_STATE` | true | **DISABLE** | No JSON import; state loaded from Luminacast API |
| `FEATURE_LOAD_STATE_FROM_URL` | true | **DISABLE** | Not applicable |
| `FEATURE_RENDERING` | true | **DISABLE** | Luminacast uses FinalizingPhase + backend render pipeline |
| `FEATURE_RENDERING_CODEC_SELECTOR` | true | **DISABLE** | Same |
| `FEATURE_IMPORT_ASSETS_TOOL` | true | **DISABLE** | Replaced by LuminacastMediaPanel |
| `FEATURE_DRAW_SOLID_TOOL` | true | **DISABLE** | Not needed for video podcast editor |
| `FEATURE_CREATE_TEXT_TOOL` | true | **DISABLE** | Text overlays handled differently (captions/products) |
| `FEATURE_CACHE_ASSETS_LOCALLY` | true | **DISABLE** | Assets served from CDN, no need for IndexedDB cache |
| `FEATURE_NEW_MEDIA_TAGS` | false | **DISABLE** | Not applicable |
| `FEATURE_SHIFT_KEY_TO_OVERRIDE_ASPECT_RATIO_LOCK` | false | **DISABLE** | Keep default |

#### Text/font flags — EVALUATE during F.4:

| Flag | Default | Notes |
|------|---------|-------|
| `FEATURE_TEXT_*` (13 flags) | true | Keep enabled if text overlays are useful; disable if only captions matter |
| `FEATURE_FONT_FAMILY_*` (5 flags) | true | Keep for caption font customization |
| `FEATURE_COLOR_CONTROL` | true | Keep for caption/text color |
| `FEATURE_SOURCE_CONTROL` | true | Shows asset info — keep for debugging |
| `FEATURE_SWAP_COMPOSITION_DIMENSIONS_BUTTON` | true | Useful for portrait↔landscape |
| `FEATURE_TOKENS_CONTROL` | true | Caption tokens |
| `FEATURE_CAPTIONS_PAGE_DURATION_CONTROL` | true | Caption timing |
| `FEATURE_CAPTIONS_HIGHLIGHT_COLOR_CONTROL` | true | Caption highlight |

### 7. Dependencies

**Editor Starter `package.json` dependencies:**

| Package | Version | Conflict with Luminacast? |
|---------|---------|--------------------------|
| `react` | ^19.2.3 | **YES** — Luminacast is on ^18.3.1 |
| `react-dom` | ^19.2.3 | **YES** — same |
| `remotion` | 4.0.433 | NEW — not in Luminacast |
| `@remotion/player` | 4.0.433 | NEW |
| `@remotion/captions` | 4.0.433 | NEW |
| `@remotion/cli` | 4.0.433 | NEW (dev only for `remotion studio`) |
| `@remotion/gif` | 4.0.433 | NEW |
| `@remotion/google-fonts` | 4.0.433 | NEW |
| `@remotion/lambda` | 4.0.433 | **SKIP** — we don't use Lambda rendering |
| `@remotion/layout-utils` | 4.0.433 | NEW |
| `@remotion/media` | 4.0.433 | NEW |
| `@remotion/openai-whisper` | 4.0.433 | NEW (for caption generation) |
| `@remotion/rounded-text-box` | 4.0.433 | NEW |
| `@remotion/shapes` | 4.0.433 | NEW |
| `@radix-ui/react-context-menu` | ^2.2.15 | **CONFLICT** — Luminacast has older Radix (^1.x/^2.1.x) |
| `@radix-ui/react-popover` | ^1.1.14 | Same concern |
| `@radix-ui/react-select` | ^2.2.5 | **CONFLICT** — Luminacast has ^2.1.0 |
| `@tanstack/react-virtual` | ^3.13.10 | NEW — not in Luminacast |
| `@react-router/node` | ^7.7.1 | **SKIP** — we use react-router-dom v6, not React Router v7 |
| `@react-router/serve` | ^7.7.1 | **SKIP** — same |
| `react-router` | ^7.7.1 | **SKIP** — Editor Starter routing not used |
| `sonner` | ^2.0.7 | NEW — toast library (can replace or coexist with Radix toast) |
| `zod` | 4.3.6 | NEW — schema validation (Editor Starter uses it internally) |
| `openai` | ^6.25.0 | **SKIP** — only used for Whisper captions server-side route |
| `mediabunny` | 1.37.0 | **SKIP** — Lambda-related |
| `@aws-sdk/s3-request-presigner` | ^3.787.0 | **SKIP** — asset upload to S3/R2 (Luminacast has own upload) |
| `tailwindcss` (dev) | ^4.1.3 | **CONFLICT** — Luminacast is on ^3.4.6 |
| `@tailwindcss/postcss` (dev) | ^4.1.3 | NEW v4 requirement |
| `@tailwindcss/vite` (dev) | ^4.1.4 | NEW v4 Vite plugin |
| `@types/react` (dev) | ^19 | **CONFLICT** — Luminacast has ^18.3.3 |
| `@types/react-dom` (dev) | ^19 | **CONFLICT** — same |
| `vite` (dev) | 7.2.6 | **CONFLICT** — Luminacast is on ^5.3.0 |

**Dependencies to ADD to Luminacast:**
- `remotion`, `@remotion/player`, `@remotion/captions`, `@remotion/gif`, `@remotion/google-fonts`, `@remotion/layout-utils`, `@remotion/media`, `@remotion/rounded-text-box`, `@remotion/shapes` — all pinned to 4.0.433
- `@radix-ui/react-context-menu` — new Radix component
- `@radix-ui/react-popover` — new Radix component
- `@tanstack/react-virtual` — virtual list (used in font picker)
- `sonner` — toast notifications (Editor Starter uses this instead of Radix toast)
- `zod` v4 — used internally by Editor Starter

**Dependencies to SKIP (not needed for Strategy A):**
- `@react-router/node`, `@react-router/serve`, `react-router` v7 — Editor Starter's routing replaced by Luminacast routing
- `@remotion/lambda`, `@remotion/openai-whisper`, `mediabunny` — render/caption server-side (not used)
- `@aws-sdk/s3-request-presigner`, `openai` — server-side upload/caption (not used)
- `@vercel/react-router` — Vercel deployment (not used)

**Dependencies to UPGRADE in Luminacast:**
- `react` + `react-dom`: ^18.3.1 → ^19.2.3
- `@types/react` + `@types/react-dom`: ^18 → ^19
- `tailwindcss`: ^3.4.6 → ^4.1.3
- All `@radix-ui/*`: upgrade to latest (React 19 compat)
- `vite`: ^5.3.0 → ^7.2.6 (or at minimum ^6 — Editor Starter's Vite plugins may require it)

### 8. Tailwind v4 specifics

Editor Starter uses **Tailwind CSS v4** with CSS-first configuration:

**CSS file** (`src/editor/editor-starter.css`):
```css
@import 'tailwindcss';

@theme {
  --color-editor-starter-bg: #28282e;
  --color-editor-starter-panel: #212126;
  --color-editor-starter-border: #000;
  --color-editor-starter-accent: #0b84f3;
  --color-editor-starter-scrollbar-track: rgba(255, 255, 255, 0.04);
  --color-editor-starter-scrollbar-thumb: rgba(255, 255, 255, 0.2);
}

@utility editor-starter-focus-ring { ... }
@utility editor-starter-field { ... }
```

Editor Starter uses **namespaced CSS variables** (`--color-editor-starter-*`) which is ideal — they won't collide with Luminacast's design tokens.

**Tailwind v4 migration impact on Luminacast:**

1. **Config migration**: `tailwind.config.ts` → CSS `@theme` block in `src/styles/globals.css`
   - All custom colors (`bg`, `surface`, `card`, `border`, `accent`, `success`, etc.) → CSS variables
   - Keyframes/animations → CSS `@keyframes` in the CSS file
   - `darkMode: "class"` → Tailwind v4 handles this differently (CSS-based)
   - `content` array → no longer needed (Vite plugin auto-detects)

2. **Plugin migration**: `tailwindcss-animate` plugin → may need v4-compatible version or manual replacement

3. **Directive migration**: `@tailwind base/components/utilities` → `@import "tailwindcss"`

4. **Class renames**: Tailwind v4 renames some utilities:
   - `outline-hidden` (was `outline-none`)
   - Some flex/grid utility changes
   - Run `npx @tailwindcss/upgrade@latest` to auto-migrate

5. **PostCSS migration**: Current `autoprefixer` + `postcss` → `@tailwindcss/postcss` plugin

6. **Vite plugin**: Add `@tailwindcss/vite` plugin to `vite.config.ts`

7. **`@layer` directives**: Luminacast's `globals.css` uses `@layer base` and `@layer components` — these are standard CSS and still work in v4

8. **`@apply` directives**: Used extensively in `globals.css` — still supported in v4

### 9. React 19 specifics

Editor Starter uses React 19.2.3. Features observed:

- **`'use client'` directive**: Found in `editor.tsx` and `select.tsx` — this is for React Router v7 SSR mode. Since we're embedding in a Vite SPA, these directives are harmless no-ops.
- **`ref` as prop**: Editor Starter passes `playerRef` as a regular prop (`React.RefObject<PlayerRef | null>`), which works in both React 18 and 19. No `forwardRef` usage for this.
- **No `useActionState`** or `use()` usage detected.
- **No `React.use()`** usage detected.

**React 19 is used primarily for compatibility, not for new React 19 APIs.** The Editor Starter code is compatible with React 19's type system changes (e.g., `ReactNode` exclusions, ref type changes).

**Luminacast React 19 migration:**
- `grep` for `ReactDOM.render`, `UNSAFE_*`, `componentWillMount`, `defaultProps` returned **zero results** — Luminacast uses only functional components with hooks
- `forwardRef` usage: None found in custom components — only `useRef` used directly
- No class components found
- **Risk: LOW** — Luminacast codebase is already React 19-compatible in practice

### 10. Rendering pipeline

Editor Starter renders via **Remotion Lambda**:

1. User clicks "Render video" button (`trigger-render-button.tsx`)
2. `triggerLambdaRender()` in `render-state.ts` POSTs to `/api/render` with:
   ```json
   {
     "compositionHeight": 1920,
     "compositionWidth": 1080,
     "tracks": [...],
     "assets": {...},
     "items": {...},
     "codec": "h264"
   }
   ```
3. Server-side route (`routes/api/render.ts`) triggers Remotion Lambda render on AWS
4. Client polls `/api/progress` for render status
5. When done, Lambda provides download URL

**How to disable**: Set `FEATURE_RENDERING = false` and `FEATURE_RENDERING_CODEC_SELECTOR = false` in `flags.ts`. This removes:
- The "Render video" button from the inspector's `<RenderControls>`
- The codec selector
- Rendering task progress indicators

The render pipeline code (`src/editor/rendering/`) and server routes (`src/routes/api/render.ts`, `src/routes/api/progress.ts`) can be excluded from the Strategy A copy entirely. None of the editor UI depends on rendering being available.

**Luminacast uses its own pipeline**: `FinalizingPhase` → `castsApi.finalize()` → `render_cast_task` Celery job → `extract_bonded_blocks_from_timeline()` → InfiniteTalk → two-pass composite.

---

## F.1.2 — Element Shape Mapping

### Editor Starter's native element JSON shape

**BaseItem** (all elements inherit):
```typescript
{
  id: string;              // random ID
  durationInFrames: number; // length in frames (NOT seconds)
  from: number;            // start frame on timeline
  top: number;             // Y position on canvas (px)
  left: number;            // X position on canvas (px)
  width: number;           // element width on canvas (px)
  height: number;          // element height on canvas (px)
  opacity: number;         // 0-1
  isDraggingInTimeline: boolean; // transient UI state
}
```

**VideoItem** (extends BaseItem + CanHaveBorderRadius + CanHaveCrop + CanHaveRotation):
```typescript
{
  ...BaseItem,
  type: 'video',
  videoStartFromInSeconds: number,  // trim offset into source video
  decibelAdjustment: number,
  playbackRate: number,
  audioFadeInDurationInSeconds: number,
  audioFadeOutDurationInSeconds: number,
  fadeInDurationInSeconds: number,
  fadeOutDurationInSeconds: number,
  assetId: string,  // references EditorStarterAsset
  keepAspectRatio: boolean,
  borderRadius: number,
  rotation: number,
  cropLeft: number, cropTop: number, cropRight: number, cropBottom: number,
}
```

**AudioItem** (extends BaseItem):
```typescript
{
  ...BaseItem,
  type: 'audio',
  audioStartFromInSeconds: number,
  decibelAdjustment: number,
  playbackRate: number,
  audioFadeInDurationInSeconds: number,
  audioFadeOutDurationInSeconds: number,
  assetId: string,
}
```

**ImageItem** (extends BaseItem + CanHaveBorderRadius + CanHaveCrop + CanHaveRotation):
```typescript
{
  ...BaseItem,
  type: 'image',
  assetId: string,
  keepAspectRatio: boolean,
  fadeInDurationInSeconds: number,
  fadeOutDurationInSeconds: number,
  borderRadius: number, rotation: number,
  cropLeft: number, cropTop: number, cropRight: number, cropBottom: number,
}
```

**CaptionsItem** (extends BaseItem + CanHaveRotation):
```typescript
{
  ...BaseItem,
  type: 'captions',
  assetId: string,  // references CaptionAsset with caption data
  fontFamily: string,
  fontStyle: { variant: string, weight: string },
  lineHeight: number, letterSpacing: number, fontSize: number,
  align: 'left' | 'center' | 'right',
  color: string, highlightColor: string,
  strokeWidth: number, strokeColor: string,
  direction: 'ltr' | 'rtl',
  pageDurationInMilliseconds: number,
  captionStartInSeconds: number,
  maxLines: number,
  fadeInDurationInSeconds: number, fadeOutDurationInSeconds: number,
}
```

**Asset model** (separate from items):
```typescript
type EditorStarterAsset = ImageAsset | VideoAsset | GifAsset | AudioAsset | CaptionAsset;

// Each has: { id, type, filename, remoteUrl, remoteFileKey, size, mimeType, ... }
// VideoAsset adds: durationInSeconds, hasAudioTrack, width, height
// AudioAsset adds: durationInSeconds
// CaptionAsset adds: captions (Caption[] from @remotion/captions)
```

### Mapping spec: `castToEditorStarterTimeline(cast, options) → EditorStarterState`

Input:
- `cast: Cast` — with `blocks[]`, each block has `variants[]`, selected variant has `audio_key`
- `options: { avatarFaceKey: string, fps?: number }` — avatar face R2 key

Output: `{ undoableState: UndoableState }` suitable for initializing `EditorState`

Algorithm:
```
fps = options.fps || 30
compositionWidth = 720
compositionHeight = 1280
tracks = []
items = {}
assets = {}
cumulativeFrame = 0

// Create face image asset once
faceAssetId = generateId()
assets[faceAssetId] = {
  id: faceAssetId, type: 'image', filename: 'avatar-face.png',
  remoteUrl: cdnUrl(options.avatarFaceKey), remoteFileKey: options.avatarFaceKey,
  size: 0, mimeType: 'image/png', width: 720, height: 1280
}

videoTrack = { id: generateId(), items: [], hidden: false, muted: false }
audioTrack = { id: generateId(), items: [], hidden: false, muted: false }

for each block in cast.blocks:
  variant = block.selectedVariant || block.variants[0]
  audioDurationSec = variant.audio_duration  // from TTS
  durationInFrames = Math.ceil(audioDurationSec * fps)

  // V1 — Image element (avatar face) on video track
  videoItemId = generateId()
  items[videoItemId] = {
    id: videoItemId, type: 'image',
    durationInFrames, from: cumulativeFrame,
    top: 0, left: 0, width: 720, height: 1280,
    opacity: 1, isDraggingInTimeline: false,
    assetId: faceAssetId,
    keepAspectRatio: true,
    fadeInDurationInSeconds: 0, fadeOutDurationInSeconds: 0,
    borderRadius: 0, rotation: 0,
    cropLeft: 0, cropTop: 0, cropRight: 0, cropBottom: 0,
    metadata: {                          // ← CUSTOM FIELD
      block_id: block.id,
      bonded: true,
      paired_audio_element_id: audioItemId  // set after creating audio
    }
  }

  // A1 — Audio element on audio track
  audioItemId = generateId()
  audioAssetId = generateId()
  assets[audioAssetId] = {
    id: audioAssetId, type: 'audio', filename: `tts_${block.id}.wav`,
    remoteUrl: cdnUrl(variant.audio_key), remoteFileKey: variant.audio_key,
    size: 0, mimeType: 'audio/wav', durationInSeconds: audioDurationSec
  }
  items[audioItemId] = {
    id: audioItemId, type: 'audio',
    durationInFrames, from: cumulativeFrame,
    top: 0, left: 0, width: 100, height: 100,
    opacity: 1, isDraggingInTimeline: false,
    audioStartFromInSeconds: 0, decibelAdjustment: 0,
    playbackRate: 1,
    audioFadeInDurationInSeconds: 0, audioFadeOutDurationInSeconds: 0,
    assetId: audioAssetId,
    metadata: {                          // ← CUSTOM FIELD
      block_id: block.id,
      bonded: true,
      paired_video_element_id: videoItemId
    }
  }

  // Back-patch the paired_audio_element_id
  items[videoItemId].metadata.paired_audio_element_id = audioItemId

  videoTrack.items.push(videoItemId)
  audioTrack.items.push(audioItemId)
  cumulativeFrame += durationInFrames

tracks = [videoTrack, audioTrack]

return {
  undoableState: {
    tracks, items, assets, fps,
    compositionWidth, compositionHeight,
    deletedAssets: []
  }
}
```

### Mapping spec: `editorStarterToLuminacastSnapshot(editorState) → RendererJSON`

Input: `EditorState` (or `UndoableState`)

Output: Renderer-compatible bonded V1/A1 JSON:
```json
{
  "tracks": [
    { "type": "video", "elements": [...] },
    { "type": "audio", "elements": [...] }
  ],
  "version": 1
}
```

Algorithm:
```
fps = editorState.undoableState.fps
items = editorState.undoableState.items
assets = editorState.undoableState.assets
tracks = editorState.undoableState.tracks

videoElements = []
audioElements = []

for each item in Object.values(items):
  if !item.metadata?.bonded || !item.metadata?.block_id:
    continue  // skip non-bonded items (user-added overlays, etc.)

  startSec = item.from / fps
  endSec = (item.from + item.durationInFrames) / fps
  asset = assets[item.assetId]

  if item.type === 'image' && item.metadata.paired_audio_element_id:
    // This is a V1 element (avatar face)
    videoElements.push({
      id: `v1_${item.metadata.block_id}`,
      type: 'video',
      s: startSec,
      e: endSec,
      props: { src: asset.remoteUrl },
      metadata: {
        block_id: item.metadata.block_id,
        bonded: true,
        paired_audio_element_id: `a1_${item.metadata.block_id}`
      }
    })

  if item.type === 'audio' && item.metadata.paired_video_element_id:
    // This is an A1 element (TTS audio)
    audioElements.push({
      id: `a1_${item.metadata.block_id}`,
      type: 'audio',
      s: startSec,
      e: endSec,
      props: { src: asset.remoteUrl },
      metadata: {
        block_id: item.metadata.block_id,
        bonded: true,
        paired_video_element_id: `v1_${item.metadata.block_id}`
      }
    })

// Sort by start time
videoElements.sort((a, b) => a.s - b.s)
audioElements.sort((a, b) => a.s - b.s)

return {
  tracks: [
    { type: 'video', elements: videoElements },
    { type: 'audio', elements: audioElements }
  ],
  version: 1
}
```

### Element type support

| Element type | Editor Starter support | Luminacast needs |
|-------------|----------------------|-----------------|
| Video | ✅ `VideoItem` + `VideoAsset` | ✅ For rendered talking-head clips |
| Audio | ✅ `AudioItem` + `AudioAsset` | ✅ For TTS audio |
| Image | ✅ `ImageItem` + `ImageAsset` | ✅ For avatar face still (pre-render preview) |
| Text | ✅ `TextItem` | 🔶 Optional — product overlays, user annotations |
| Captions | ✅ `CaptionsItem` + `CaptionAsset` | ✅ Auto-captions |
| Solid | ✅ `SolidItem` | ❌ Not needed |
| GIF | ✅ `GifItem` + `GifAsset` | ❌ Not needed |

### Metadata persistence through edits — CRITICAL FINDING

**Editor Starter items do NOT have a native `metadata` field.** However, all state mutation actions use the **spread operator** (`{...item}`), which means:

- **Adding `metadata` to `BaseItem`** will cause it to be automatically preserved through ALL operations:
  - Drag (timeline position update): `{...item, from: newFrom, durationInFrames: newDuration}` ✅
  - Trim: Same pattern ✅
  - Split: `{...targetItem, id: newId, durationInFrames: half}` — spread copies `metadata` ✅
  - Duplicate: `{...item, id: newId}` ✅
  - Paste: `{...copiedItem, id: newId}` ✅
  - Change: updater receives full item, returns full item ✅

**Approach**: Extend `BaseItem` in `shared.ts`:
```typescript
export type BaseItem = {
  id: string;
  durationInFrames: number;
  from: number;
  top: number;
  left: number;
  width: number;
  height: number;
  opacity: number;
  isDraggingInTimeline: boolean;
  metadata?: {               // ← ADD THIS
    block_id?: string;
    bonded?: boolean;
    paired_audio_element_id?: string;
    paired_video_element_id?: string;
    [key: string]: any;
  };
};
```

This is a **safe, minimal change** — the `?` makes it optional, so all existing Editor Starter code continues to work. The spread operator ensures metadata propagates through all edit operations.

**Split behavior for bonded items**: When a bonded image item is split, BOTH halves keep the same `block_id` and `bonded: true`. The `editorStarterToLuminacastSnapshot()` function should find the paired audio by `block_id` — if an audio element has the same `block_id`, they're still bonded regardless of which half the user drags.

---

## F.1.3 — Customizations Needed

### 1. Replace media library with `LuminacastMediaPanel`

**Current Editor Starter behavior**: No dedicated media library. Assets are added via:
- File drop on canvas (`drop-handler.tsx`)
- File drop on timeline (`FEATURE_DROP_ASSETS_ON_TIMELINE`)
- Paste from clipboard (`clipboard/parse-items.ts`)
- The "Import" button (`FEATURE_IMPORT_ASSETS_TOOL`) which opens a native file picker

**Extension point**: The `<TopPanel>` renders `<Canvas>` + `<Inspector>` side by side. There is no left panel for media library.

**Approach**: Add `LuminacastMediaPanel` as a left sidebar in the editor layout:
```tsx
// Modified editor.tsx layout:
<div className="flex h-full w-full flex-row">
  <LuminacastMediaPanel onAddToTimeline={handleAddItem} /> {/* LEFT */}
  <Canvas playerRef={playerRef} loop={loop} />             {/* CENTER */}
  <Inspector />                                             {/* RIGHT */}
</div>
```

When the user picks a media item from `LuminacastMediaPanel`:
- Call `addItem()` state action with appropriate item type + asset
- Asset URL comes from `cdnUrl(asset.r2_key)` for R2-hosted assets
- Pass `remoteUrl` (CDN URL) and `remoteFileKey` (R2 key) to the asset

### 2. Properties panel gaps/additions

**Editor Starter inspector covers**:
- Position, dimensions, rotation, opacity, border radius, crop — ✅ sufficient
- Font family, size, color, alignment, line height, letter spacing — ✅ for text/captions
- Volume, playback rate, audio fade — ✅ for audio/video
- Source info (filename, dimensions, duration) — ✅ informational

**Gaps for Luminacast**:
- **Per-block metadata display**: Show `block_id`, block position, variant info when a bonded element is selected. Low priority — add if useful for debugging.
- **Voice swap**: Not needed in editor. Voice selection happens in ScriptPhase.
- **Product overlay properties**: If product images are added as ImageItems, the standard image inspector covers position/size/opacity. No custom fields needed.

**Conclusion**: The default Inspector is sufficient. No custom inspector components needed.

### 3. Header/chrome removal

**Editor Starter header**: The `<ActionRow>` component renders a toolbar with: tool selection, undo/redo, save, download state, load state, tasks indicator, canvas zoom controls.

**Luminacast's `PhaseHeader`**: Already handles: wizard breadcrumb, phase-specific actions (Finalize button in actions slot), navigation.

**Approach**:
- **Keep `<ActionRow>`** but disable save/download/load buttons via feature flags (`FEATURE_SAVE_BUTTON = false`, `FEATURE_DOWNLOAD_STATE = false`, `FEATURE_LOAD_STATE = false`)
- Remove the outer `h-screen w-screen` from `<Editor>` so it fits within CastBuilder's layout
- `<Editor>` becomes a flex-grow child within the CastBuilder page, not a standalone app

### 4. Auto-save bridge

**Editor Starter's save**: `persistance.ts` saves `UndoableState` to `localStorage` via `saveState()`. Triggered by the Save button.

**Luminacast auto-save**: Debounced `castsApi.saveTimeline()` after state changes.

**Approach**:
- Disable `FEATURE_SAVE_BUTTON` to remove localStorage persistence
- Add a `useEffect` in the `ArrangePhaseRemotion` wrapper that:
  1. Watches Editor Starter's `UndoableState` (via `FullStateContext`)
  2. On change, debounces (1500ms, matching current Twick auto-save)
  3. Calls `editorStarterToLuminacastSnapshot(state)` to convert to renderer format
  4. Calls `castsApi.saveTimeline(castId, { variant_id: "default", twick_data: rendererJson, block_regions: computeBlockRegions(rendererJson) })`

### 5. Design token alignment

**Editor Starter tokens** (CSS variables in `editor-starter.css`):

| Editor Starter | Value | Luminacast equivalent |
|---------------|-------|----------------------|
| `--color-editor-starter-bg` | `#28282e` | `bg` (`#0F0F14`) — darker |
| `--color-editor-starter-panel` | `#212126` | `surface` (`#16161E`) — darker |
| `--color-editor-starter-border` | `#000` | `border` (`#2A2A3A`) — less harsh |
| `--color-editor-starter-accent` | `#0b84f3` | `accent` (`#8B82C0`) — purple vs blue |
| `--color-editor-starter-scrollbar-track` | `rgba(255,255,255,0.04)` | Keep as-is |
| `--color-editor-starter-scrollbar-thumb` | `rgba(255,255,255,0.2)` | Keep as-is |

**Approach**: Override the `@theme` variables in Luminacast's CSS:
```css
@theme {
  --color-editor-starter-bg: #0F0F14;      /* match Luminacast bg */
  --color-editor-starter-panel: #16161E;    /* match Luminacast surface */
  --color-editor-starter-border: #2A2A3A;   /* match Luminacast border */
  --color-editor-starter-accent: #8B82C0;   /* match Luminacast accent */
}
```

This is a 4-line override. Editor Starter's CSS classes reference these variables, so changing the values automatically recolors the entire editor.

### 6. Preview avatar face

**Requirement**: Before Finalize & Render, the Remotion Player should show the avatar face as a still image synced with TTS audio playback. After render, actual talking-head video replaces the still.

**How it works in Editor Starter**:
- `ImageItem` on the video track shows the avatar face
- `AudioItem` on the audio track plays TTS audio
- `<ImageLayer>` renders `<Img src={blobUrl}>` in the Remotion composition
- `<AudioLayer>` renders `<Audio src={blobUrl}>` in the Remotion composition
- Both are positioned at the same `from` frame with the same `durationInFrames`

**This works out of the box.** The initial timeline built by `castToEditorStarterTimeline()` places bonded Image+Audio pairs. The Remotion Player will display the face image and play the audio simultaneously — exactly the desired preview behavior.

**Post-render update**: When the user comes back to edit after rendering, the video track elements could be swapped from `ImageItem` to `VideoItem` pointing to the rendered MP4. This is a future enhancement, not needed for initial migration.

---

## F.1.4 — React 19 + Tailwind v4 Upgrade Impact Assessment

### React 18 → 19

**Incompatibilities found in Luminacast codebase: ZERO**

```bash
grep -rn "ReactDOM.render|UNSAFE_|componentWillMount|componentWillReceiveProps|componentWillUpdate|defaultProps" src/
# (no results)
```

- No class components
- No `ReactDOM.render()` (uses `createRoot` already)
- No deprecated lifecycle methods
- No `defaultProps` on function components
- No `forwardRef` in custom components (only `useRef`)
- All components are functional with hooks

**React 19 type changes**:
- `@types/react` ^18 → ^19: `ReactNode` no longer includes `bigint`. If any component types use `ReactNode` in union with `bigint`, update needed. **Risk: Very Low** — unlikely in this codebase.
- Implicit `children` prop removed from `FC` type — but Luminacast destructures props explicitly, so this is unlikely to cause issues.
- `ref` type changes: `forwardRef` wrapper deprecated (still works). Refs can be passed as regular props. Since Luminacast doesn't use `forwardRef`, no change needed.

**Risk: LOW**. Expect 0-5 minor type errors fixable in <30 minutes.

### Tailwind v3 → v4

**Changes needed**:

1. **Config file migration**: `tailwind.config.ts` (172 lines) → CSS `@theme` block
   - 15 custom colors with nested variants → CSS variables
   - 2 font families → CSS variables
   - 3 border radius tokens → CSS variables
   - 25+ keyframes → `@keyframes` in CSS
   - 25+ animation shorthand → CSS variables
   - `tailwindcss-animate` plugin → check v4 compatibility or replace

2. **Directive migration**: `src/styles/globals.css`
   - `@tailwind base/components/utilities` → `@import "tailwindcss"`
   - `@layer base` and `@layer components` → still work in v4
   - `@apply` directives → still work in v4

3. **PostCSS config**: Replace `autoprefixer` with `@tailwindcss/postcss`

4. **Vite config**: Add `@tailwindcss/vite` plugin

5. **`tailwindcss-animate` plugin**: May need v4-compatible version. Check `npm info tailwindcss-animate` for v4 support. If not supported, replace with native CSS animations (Luminacast already defines all keyframes inline).

6. **Class renames**: Run `npx @tailwindcss/upgrade@latest` for auto-migration of:
   - `outline-none` → `outline-hidden`
   - Various flex/grid utility changes

**Risk: MEDIUM**. The official upgrade tool handles most changes. Manual work needed for:
- Custom plugin (`tailwindcss-animate`) compatibility
- Verifying all 25+ keyframe animations survive the migration
- Visual QA of every page

### Radix UI compatibility

**Current Luminacast Radix versions** (all ^1.1.x or ^2.1.x):
```
@radix-ui/react-avatar: ^1.1.0
@radix-ui/react-checkbox: ^1.1.0
@radix-ui/react-dialog: ^1.1.0
@radix-ui/react-dropdown-menu: ^2.1.0
@radix-ui/react-label: ^2.1.0
@radix-ui/react-progress: ^1.1.0
@radix-ui/react-select: ^2.1.0
@radix-ui/react-separator: ^1.1.0
@radix-ui/react-slot: ^1.1.0
@radix-ui/react-tabs: ^1.1.0
@radix-ui/react-toast: ^1.2.0
@radix-ui/react-tooltip: ^1.1.0
```

Radix UI supports React 19 in latest versions. Upgrade all to `@latest`.

**NEW Radix packages from Editor Starter**:
- `@radix-ui/react-context-menu` (new)
- `@radix-ui/react-popover` (new)

**Risk: LOW**. Radix has been React 19 compatible since early 2025.

### react-moveable compatibility

**Current**: `react-moveable: ^0.56.0`
**React 19 support**: react-moveable v0.56+ supports React 19 (confirmed in changelog). Upgrade to latest minor.

**Risk: LOW**.

### react-rnd compatibility

**Current**: `react-rnd: ^10.4.0`
**React 19 support**: react-rnd v10.4+ works with React 19. May need minor type fixes.

**Risk: LOW**.

### recharts compatibility

**Current**: `recharts: ^2.12.0`
**React 19 support**: recharts v2.12+ supports React 19. Upgrade to latest.

**Risk: LOW**.

### Other libraries

| Library | Current | React 19 risk |
|---------|---------|---------------|
| `@tanstack/react-query` | ^5.51.0 | LOW — v5 supports React 19 |
| `react-router-dom` | ^6.25.0 | LOW — v6.25+ supports React 19 |
| `lucide-react` | ^0.400.0 | LOW — upgrade to latest |
| `@sentry/react` | ^8.0.0 | LOW — v8 supports React 19 |
| `zustand` | ^4.5.0 | LOW — works with React 19 |
| `wavesurfer.js` | ^7.8.0 | NONE — not React-coupled |
| `hls.js` | ^1.6.15 | NONE — not React-coupled |
| `clsx`, `class-variance-authority`, `tailwind-merge` | current | NONE — utility libs |
| `@dnd-kit/core`, `@dnd-kit/sortable` | ^6.1.0/^8.0.0 | LOW — check latest |

### Vite upgrade

**Current**: `vite: ^5.3.0`
**Editor Starter**: `vite: 7.2.6`

Editor Starter uses Vite 7 which requires the `@tailwindcss/vite` plugin. Luminacast can stay on Vite 5.x or 6.x with the `@tailwindcss/postcss` plugin instead. The Vite upgrade is **optional** — Tailwind v4 works with Vite 5+ via PostCSS.

**Recommendation**: Stay on Vite 5.x for now, use `@tailwindcss/postcss`. Upgrade to Vite 7 separately if needed.

### Overall upgrade risk summary

| Component | Risk | Effort |
|-----------|------|--------|
| React 18 → 19 | LOW | 1-2 hours |
| Tailwind 3 → 4 | MEDIUM | 3-5 hours |
| Radix UI upgrades | LOW | 30 min |
| Other deps | LOW | 30 min |
| Visual QA | MEDIUM | 1-2 hours |
| **Total** | **MEDIUM** | **6-10 hours** |

---

## F.1.5 — Integration Strategy: Strategy A (Fork into Luminacast repo)

### Decision: **Strategy A — Fork into Luminacast repo**

### Justification

1. **Full control over the source**: Editor Starter is a ~17,800 LOC TypeScript codebase. We need to:
   - Add `metadata` field to `BaseItem` (not possible without source modification)
   - Remove/disable feature flags at build time (needs `flags.ts` modification)
   - Override CSS variables for design tokens
   - Replace media library with `LuminacastMediaPanel`
   - Wire auto-save bridge
   - Remove SSR/React Router v7 patterns

   All of these require direct source modification. Strategies B (iframe) and C (npm package) would require either a complex postMessage protocol or a private registry build pipeline — both adding maintenance overhead for zero benefit.

2. **No upstream dependency**: The $600 one-time purchase means no subscription. Updates are manual re-purchases. There's no npm package to `npm update`. Strategy C's version management benefit doesn't exist.

3. **Debuggability**: When timeline drag breaks or preview doesn't render, the developer can set breakpoints directly in the editor source. iframe isolation (Strategy B) would make this painful. npm package isolation (Strategy C) would require source maps from a private registry.

4. **Shared state is essential**: The editor must read Luminacast's cast model (blocks, variants, audio keys, avatar face) and emit Luminacast's renderer format. This state sharing is trivial with in-process React context. With iframe (Strategy B), it requires serialization across postMessage.

5. **Build tool alignment**: Both Editor Starter and Luminacast use Vite + React + TypeScript + Tailwind. Copying files into the same build preserves HMR, tree-shaking, and type checking.

6. **Risk mitigation**: If Strategy A turns out infeasible mid-migration (e.g., Editor Starter has hard dependencies on React Router v7 SSR that can't be stripped), we fall back to Strategy B (iframe) as the escape hatch.

### Copy plan

Copy `src/editor/` into `frontend/companion-app/src/components/cast-builder/editor-starter/`:
```
editor-starter/
├── action-row/          ← Toolbar (minus save/download/load buttons)
├── assets/              ← Asset model (keep, adapt URLs to CDN)
├── canvas/              ← Remotion Player + composition + snap
├── captioning/          ← Auto-captions (keep if using Editor Starter's caption flow)
├── clipboard/           ← Copy/paste
├── data/                ← Google Fonts
├── icons/               ← SVG icons
├── inspector/           ← Properties panel
├── items/               ← Item types + layers (ADD metadata to BaseItem)
├── keyboard-shortcuts/  ← Keybindings
├── playback-controls/   ← Play/pause/seek
├── selection-border/    ← Canvas selection
├── state/               ← State management (REMOVE localStorage persistence)
├── timeline/            ← Timeline (core of the editor)
├── utils/               ← Utilities
├── editor.tsx           ← Main mount (MODIFY layout for Luminacast)
├── context-provider.tsx ← Context providers (KEEP as-is)
├── flags.ts             ← Feature flags (SET per F.1.1 enable/disable plan)
├── constants.ts         ← Constants (ADJUST default dimensions)
└── editor-starter.css   ← CSS (OVERRIDE theme variables)
```

**Exclude from copy**:
- `src/routes/` — React Router v7 routes (not used)
- `src/routes/api/` — Server-side API routes (upload, render, progress, captions, font)
- `src/remotion/` — Remotion CLI composition (only needed for SSR rendering, not player preview)
- `src/scripts/` — Font generation scripts
- `src/root.tsx`, `src/routes.ts` — React Router root
- `deploy.ts`, `remotion.config.ts`, `react-router.config.ts` — Build/deploy configs

**Files to bring from `src/remotion/`**:
- `main.tsx` — `CompositionWithContexts` (needed for Remotion Player's composition component)
- `constants.ts` — `COMP_NAME` etc. (may be referenced)

### Deliverables per phase

| Phase | Deliverable |
|-------|------------|
| F.2 | React 19 + Tailwind 4 upgrade (full frontend) |
| F.3 | Scaffold `editor-starter/` directory + install Remotion deps |
| F.4 | Copy + adapt all Editor Starter files (9 sub-commits) |
| F.5 | `editorStarterMapping.ts` with both mapping functions + unit tests |
| F.6 | `ArrangePhaseRemotion` wired into CastBuilder, full smoke test |
| F.7 | Delete Twick, rename, clean up |

---

## Appendix A — File count summary

| Directory | Files | LOC (approx) |
|-----------|-------|--------------|
| `src/editor/action-row/` | 28 | 900 |
| `src/editor/assets/` | 5 | 250 |
| `src/editor/caching/` | 6 | 300 |
| `src/editor/canvas/` | 14 | 700 |
| `src/editor/captioning/` | 6 | 350 |
| `src/editor/clipboard/` | 3 | 150 |
| `src/editor/data/` | 2 | 1500 |
| `src/editor/icons/` | 40 | 1200 |
| `src/editor/inspector/` | 40+ | 2500 |
| `src/editor/items/` | 25 | 1000 |
| `src/editor/keyboard-shortcuts/` | 8 | 300 |
| `src/editor/playback-controls/` | 9 | 400 |
| `src/editor/rendering/` | 8 | 500 |
| `src/editor/selection-border/` | 7 | 400 |
| `src/editor/state/` | 45+ | 2000 |
| `src/editor/timeline/` | 60+ | 3500 |
| `src/editor/utils/` | 50+ | 2500 |
| **Total** | **~350 files** | **~17,800 LOC** |

## Appendix B — Auto-captions add-on location

Per prerequisite check: `ls /opt/luminacast-omni/external/` shows ONLY `remotion-editor-starter/`. There is no separate `remotion-auto-captions/` directory. The auto-captions feature is **built into Editor Starter** at `src/editor/captioning/`. It uses `@remotion/captions` + `@remotion/openai-whisper` to:
1. Extract audio from video/audio asset
2. Send to OpenAI Whisper API (via server route `/api/captions`)
3. Create a `CaptionAsset` with timing data
4. Add a `CaptionsItem` to the timeline

For Luminacast, we may want to:
- **Keep the captioning UI** (GenerateCaptionSection in inspector) — it's useful
- **Replace the backend route** — instead of Editor Starter's `/api/captions` (OpenAI Whisper), use Luminacast's own TTS-generated captions (if available) or keep the Whisper approach
- **Or disable captioning** via `FEATURE_CAPTIONING = false` and generate captions separately

## Appendix C — React Router v7 removal checklist

Editor Starter uses React Router v7 for SSR routing. When copying to Luminacast (Vite SPA with react-router-dom v6), remove:
1. `'use client'` directives (2 files) — harmless but unnecessary
2. Any import from `@react-router/*` or `react-router` v7 — not used
3. Route definitions (`routes.ts`, `routes/`) — not copied
4. Server-side API routes — not copied
5. `react-router.config.ts` — not copied
6. `@vercel/react-router` — not installed

The editor components themselves (`src/editor/`) have **zero React Router imports**. They are pure React components that render within whatever routing framework hosts them.

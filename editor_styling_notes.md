# Twick Editor Styling Notes

> Audited 2026-04-11 from VPS `/opt/luminacast-omni/frontend/companion-app/node_modules/@twick/`
> Packages found: `2d`, `canvas`, `core`, `effects`, `ffmpeg`, `gl-runtime`, `live-player`, `media-utils`, `player-react`, `renderer`, `studio`, `telemetry`, `timeline`, `video-editor`, `visualizer`, `vite-plugin`

---

## Section A -- CSS files and top-level class names

### CSS files discovered

| Package | File |
|---|---|
| @twick/2d | `editor/index.css` |
| @twick/player-react | `dist/index.css` |
| @twick/studio | `dist/studio.css` (2234 lines) |
| @twick/video-editor | `dist/video-editor.css` (1514 lines) |
| @twick/visualizer | `src/global.css`, `dist/project.css` |

### studio.css top-level selectors (grouped by area)

**Layout / Shell**
- `.studio-container` -- full viewport flex column, bg neutral-900
- `.studio-content` -- flex row, 100% width, height calc(100vh - 56px)
- `.main-container` -- flex:1, flex column, bg neutral-700, max-width calc(100% - 43rem)
- `.canvas-wrapper` -- flex:1, overflow-y auto, padding 0.25rem
- `.canvas-container` -- position relative, overflow hidden

**Header**
- `.header` -- height 3.5rem, bg rgba(38,38,38,0.9), flex between, backdrop blur

**Sidebar / Toolbar**
- `.sidebar` -- min/max width 3.5rem, bg rgba(38,38,38,0.8), border-right, backdrop blur
- `.toolbar` -- flex row, bg rgba(38,38,38,0.8), backdrop blur, border-bottom
- `.toolbar-btn`, `.toolbar-btn.active`
- `.toolbar-label`

**Properties Panel (right)**
- `.properties-panel` -- width 18rem, bg gradient, border-left
- `.properties-header`, `.properties-title`
- `.properties-group`, `.property-section`, `.property-title`, `.property-grid`
- `.property-label`, `.property-input`
- `.props-toolbar-btn`, `.props-toolbar-btn.active`
- `.props-toolbar-label`

**Left Panel**
- `.panel-container` -- width 18rem, bg neutral-900
- `.panel-title`, `.panel-section`, `.panel-content`

**Media Library**
- `.media-grid`, `.media-item`, `.media-item-content`
- `.media-list`, `.media-list-item`, `.media-list-content`, `.media-list-icon`, `.media-list-title`
- `.media-duration`, `.media-overlay`, `.media-actions`, `.media-action-btn`
- `.media-video`, `.media-count`, `.media-content`
- `.icon-grid`, `.icon-item`, `.icon-content`, `.icon-actions`, `.icon-action-btn`, `.icon-name`

**Timeline (twick- prefixed)**
- `.twick-editor-main-container` -- flex column
- `.twick-editor-view-section` -- flex row
- `.twick-editor-timeline-section` -- flex column, bg #252525, border-radius 10px
- `.twick-editor-container` -- height 80dvh, bg #000
- `.twick-editor-canvas-container`, `.twick-editor-canvas`
- `.twick-editor-loading-overlay`, `.twick-editor-loading-spinner`
- `.twick-timeline-scroll-container`
- `.twick-timeline-container` -- height 2.75rem
- `.twick-timeline-header-container`
- `.twick-seek-track-container`, `.twick-seek-track-container-no-scrollbar`
- `.twick-seek-track-empty-space` -- min-width 40px, bg #171717
- `.twick-seek-track` -- bg #171717
- `.twick-seek-track-canvas`
- `.twick-seek-track-playhead` -- cursor ew-resize, z-index 30
- `.twick-seek-track-pin` -- bg #FFFFFF, width 1.5px
- `.twick-seek-track-handle` -- bg #FFFFFF, 0.75rem square
- `.twick-track` -- height 2.5rem, bg #474747
- `.twick-track-element` -- absolute, border-radius 6px
- `.twick-track-element-selected` -- border 2px solid #FFFFFF
- `.twick-track-element-default` -- border 1px solid #d1d1d1
- `.twick-track-element-dragging`
- `.twick-track-element-content` -- cursor grab
- `.twick-track-element-handle`, `.twick-track-element-handle-start`, `.twick-track-element-handle-end`
- `.twick-track-element-frame-effect`
- `.twick-track-header` -- width 40px, bg #171717
- `.twick-track-header-selected`, `.twick-track-header-default`
- `.twick-track-header-lock`, `.twick-track-header-content`, `.twick-track-header-grip`, `.twick-track-header-name`
- `.twick-track-zoom-container`

**Player Controls**
- `.player-controls` -- height 3rem, bg neutral-800
- `.edit-controls`, `.playback-controls`
- `.control-btn`, `.control-btn:hover`, `.control-btn.btn-disabled`
- `.delete-btn`, `.split-btn`, `.play-pause-btn`
- `.time-display`, `.current-time`, `.time-separator`, `.total-time`
- `.undo-redo-controls`
- `.zoom-level`

**Accordion / Collapsibles**
- `.accordion-item`, `.accordion-header`, `.accordion-content`, `.accordion-content.expanded`, `.accordion-panel`
- `.prop-header`, `.prop-content`

**Form Controls**
- `.aspect-ratio-btn`, `.aspect-ratio-btn.active`
- `.slider-thumb`, `.slider-container`, `.slider-purple`, `.slider-value`
- `.form-btn`, `.form-btn.active`
- `.checkbox-control`, `.checkbox-label`, `.checkbox-purple`
- `.color-section`, `.color-control`, `.color-inputs`, `.color-picker`, `.color-text`, `.color-container`
- `.select-dark`, `.btn-icon`, `.btn-icon-active`
- `.font-controls`
- `.label-dark`, `.label-small`
- `.file-input-container`, `.file-input-hidden`, `.file-input-label`
- `.search-container`, `.search-input`, `.search-icon`
- `.empty-state`, `.empty-state-content`, `.empty-state-icon`, `.empty-state-text`, `.empty-state-subtext`

**Utility classes (from bundled twick-utilities.css, duplicated in both studio.css and video-editor.css)**
- `:root` CSS variables -- full color palette (purple, gray, neutral, red), shadows, transitions
- Layout: `.flex`, `.flex-col`, `.flex-row`, `.flex-container`, `.items-center`, `.justify-center`, `.justify-between`, `.gap-1..4`, `.w-full`, `.h-full`
- Icons: `.icon-xs`, `.icon-sm`, `.icon-md`, `.icon-lg`, `.icon-margin`
- Text: `.text-sm`, `.text-base`, `.text-lg`, `.font-bold`, `.text-gradient`, `.text-gradient-purple`, `.text-gradient-blue`
- Grid: `.grid-auto-fit`, `.grid-auto-fill`
- Backdrop: `.backdrop-blur-sm/md/lg`
- Scrollbar: `.custom-scrollbar`
- Glass: `.glass`
- Glow: `.glow-purple`, `.glow-blue`
- Buttons: `.btn`, `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-outline`, `.btn-danger`
- Inputs: `.input`, `.input-dark`
- Card: `.card`

### video-editor.css

Same utility classes as studio.css (duplicated). Additional twick-prefixed timeline selectors identical to those in studio.css. Also includes player-controls, edit-controls, etc. No unique selectors beyond what studio.css defines.

### player-react index.css

Tailwind v3.4.12 scoped under `.twick-player-root`. Standard Tailwind utility classes. No custom selectors to override.

### visualizer global.css / project.css

Font-face declarations only (for all 22 fonts). No overridable selectors.

### 2d editor/index.css

Small CSS module with `.root`, `.label`, `.label.active`, `.label.parent`. Tree view for 2D editor, not relevant to studio theming.

---

## Section B -- TwickStudio component props

### TwickStudio

```ts
function TwickStudio({ studioConfig }: { studioConfig?: StudioConfig }): JSX.Element
```

### StudioConfig (extends VideoEditorConfig)

```ts
interface StudioConfig extends VideoEditorConfig {
  saveProject?: (project: ProjectJSON, fileName: string) => Promise<Result>;
  loadProject?: () => Promise<ProjectJSON>;
  subtitleGenerationService?: ISubtitleGenerationService;
  exportVideo?: (project: ProjectJSON, videoSettings: VideoSettings) => Promise<Result>;
}
```

### VideoEditorConfig

```ts
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

### VideoEditorProps (lower-level, used by VideoEditor)

```ts
interface VideoEditorProps {
  leftPanel?: React.ReactNode;
  rightPanel?: React.ReactNode;
  bottomPanel?: React.ReactNode;
  defaultPlayControls?: boolean;
  editorConfig: VideoEditorConfig;
}
```

### ElementColors (customizable via config or setElementColors())

```ts
interface ElementColors {
  video: string;    // default "#8B5FBF"
  audio: string;    // default "#3D8B8B"
  image: string;    // default "#D4956C"
  text: string;     // default "#8D74C4"
  caption: string;  // default "#9B8ACE"
  icon: string;     // default "#A76CD4"
  circle: string;   // default "#703D8B"
  rect: string;     // default "#5B4B99"
  element: string;  // default "#7B68B8"
  fragment: string; // default "#1A1A1A"
  frameEffect: string; // default "#B55B9C"
  filters: string;  // default "#7A89D4"
  transition: string; // default "#BE8157"
  animation: string; // default "#4B9B78"
}
```

### TimelineTickConfig

```ts
interface TimelineTickConfig {
  durationThreshold: number;  // applies when duration < threshold
  majorInterval: number;      // major tick every N seconds
  minorTicks: number;         // minor ticks between majors
}
```

### TimelineZoomConfig

```ts
interface TimelineZoomConfig {
  min: number;     // default 0.1 (10%)
  max: number;     // default 3.0 (300%)
  step: number;    // default 0.1 (10%)
  default: number; // default 1.5 (150%)
}
```

### Key: No hideLogo, branding, theme, logo, or customStyles props exist

The TwickStudio and VideoEditor APIs do NOT expose any prop for:
- `hideLogo`
- `branding`
- `theme`
- `logo`
- `customStyles`

Branding must be removed via CSS override or MutationObserver (see Section G).

---

## Section C -- Timeline element classes

All element classes extend `TrackElement` from `@twick/timeline`.

### TrackElement (abstract base)

```ts
abstract class TrackElement {
  getId(): string
  getType(): string
  getStart(): number
  getEnd(): number
  getDuration(): number
  getTrackId(): string
  getProps(): Record<string, any>
  getName(): string
  getAnimation(): ElementAnimation | undefined
  getPosition(): Position
  getRotation(): number
  getOpacity(): number

  setId(id: string): this
  setType(type: string): this
  setStart(s: number): this
  setEnd(e: number): this
  setTrackId(trackId: string): this
  setName(name: string): this
  setAnimation(animation?: ElementAnimation): this
  setPosition(position: Position): this
  setRotation(rotation: number): this
  setOpacity(opacity: number): this
  setProps(props: Record<string, any>): this
}
```

### VideoElement

```ts
class VideoElement extends TrackElement {
  constructor(src: string, parentSize: Size)
  getParentSize(): Size
  getFrame(): Frame
  getFrameEffects(): ElementFrameEffect[] | undefined
  getBackgroundColor(): string
  getObjectFit(): ObjectFit
  getMediaDuration(): number
  getStartAt(): number
  getEndAt(): number
  getSrc(): string
  getPlaybackRate(): number
  getVolume(): number
  getPosition(): Position
  updateVideoMeta(updateFrame?: boolean): Promise<void>
  setPosition(position: Position): this
  setSrc(src: string): Promise<this>
  setMediaDuration(mediaDuration: number): this
  setParentSize(parentSize: Size): this
  setObjectFit(objectFit: ObjectFit): this
  setFrame(frame: Frame): this
  setPlaybackRate(playbackRate: number): this
  setStartAt(time: number): this
  setMediaFilter(mediaFilter: string): this
  setVolume(volume: number): this
  setBackgroundColor(backgroundColor: string): this
  setProps(props: Omit<any, "src">): this
  setFrameEffects(frameEffects?: ElementFrameEffect[]): this
  addFrameEffect(frameEffect: ElementFrameEffect): this
}
```

### AudioElement

```ts
class AudioElement extends TrackElement {
  constructor(src: string)
  getMediaDuration(): number
  getStartAt(): number
  getEndAt(): number
  getSrc(): string
  getPlaybackRate(): number
  getVolume(): number
  updateAudioMeta(): Promise<void>
  setSrc(src: string): Promise<this>
  setMediaDuration(mediaDuration: number): this
  setVolume(volume: number): this
  setLoop(loop: boolean): this
  setStartAt(time: number): this
  setPlaybackRate(playbackRate: number): this
  setProps(props: Omit<any, "src">): this
}
```

### ImageElement

```ts
class ImageElement extends TrackElement {
  constructor(src: string, parentSize: Size)
  getParentSize(): Size
  getFrame(): Frame
  getFrameEffects(): ElementFrameEffect[] | undefined
  getBackgroundColor(): string
  getObjectFit(): ObjectFit
  getPosition(): Position
  updateImageMeta(updateFrame?: boolean): Promise<void>
  setPosition(position: Position): this
  setSrc(src: string): Promise<this>
  setObjectFit(objectFit: ObjectFit): this
  setFrame(frame: Frame): this
  setParentSize(parentSize: Size): this
  setMediaFilter(mediaFilter: string): this
  setBackgroundColor(backgroundColor: string): this
  setProps(props: Omit<any, "src">): this
  setFrameEffects(frameEffects?: ElementFrameEffect[]): this
  addFrameEffect(frameEffect: ElementFrameEffect): this
}
```

### TextElement

```ts
class TextElement extends TrackElement {
  constructor(text: string, props?: Omit<TextProps, 'text'>)
  getTextEffect(): ElementTextEffect | undefined
  getText(): string
  getStrokeColor(): string | undefined
  getLineWidth(): number | undefined
  getProps(): TextProps
  setText(text: string): this
  setFill(fill: string): this
  setRotation(rotation: number): this
  setFontSize(fontSize: number): this
  setFontFamily(fontFamily: string): this
  setFontWeight(fontWeight: number): this
  setFontStyle(fontStyle: "normal" | "italic"): this
  setTextEffect(textEffect?: ElementTextEffect): this
  setTextAlign(textAlign: TextAlign): this
  setStrokeColor(stroke: string): this
  setLineWidth(lineWidth: number): this
  setProps(props: TextProps): this
}
```

### CaptionElement

```ts
class CaptionElement extends TrackElement {
  constructor(t: string, start: number, end: number)
  getText(): string
  setText(t: string): this
}
```

### CircleElement

```ts
class CircleElement extends TrackElement {
  constructor(fill: string, radius: number)
  getFill(): string
  getRadius(): number
  getStrokeColor(): string
  getLineWidth(): number
  setFill(fill: string): this
  setRadius(radius: number): this
  setStrokeColor(strokeColor: string): this
  setLineWidth(lineWidth: number): this
}
```

### RectElement

```ts
class RectElement extends TrackElement {
  constructor(fill: string, size: Size)
  getFill(): string
  getSize(): Size
  getCornerRadius(): number
  getStrokeColor(): string
  getLineWidth(): number
  setFill(fill: string): this
  setSize(size: Size): this
  setCornerRadius(cornerRadius: number): this
  setStrokeColor(strokeColor: string): this
  setLineWidth(lineWidth: number): this
}
```

### IconElement

```ts
class IconElement extends TrackElement {
  constructor(src: string, size: Size, fill?: string)
  getSrc(): string
  getFill(): string
  getSize(): Size | undefined
  setSrc(src: string): this
  setFill(fill: string): this
  setSize(size: Size): this
}
```

---

## Section D -- Available animations, effects, and styles (CRITICAL)

### ANIMATIONS (5 total, from @twick/video-editor)

| # | name | default animate | options |
|---|---|---|---|
| 1 | `fade` | enter | animate: enter/exit/both; interval: 0.1-5; duration: 0.1-5; intensity: 0.1-2 |
| 2 | `rise` | enter | animate: enter/exit/both; direction: up/down/left/right/center; interval: 0.1-5; duration: 0.1-5; intensity: 1-300 |
| 3 | `blur` | enter | animate: enter/exit/both; interval: 0.1-5; duration: 0.1-5; intensity: 0.1-100 |
| 4 | `breathe` | enter | animate: enter/exit/both; mode: in/out; interval: 0.1-5; duration: 0.1-5; intensity: 0.1-2 |
| 5 | `succession` | enter | animate: enter/exit/both; interval: 0.1-5; duration: 0.1-5; intensity: 0.1-2 |

NOTE: Only 5 animations exist (not 7 as might be expected).

### TEXT_EFFECTS (4 total, from @twick/video-editor)

| # | name | default delay | default duration | default bufferTime |
|---|---|---|---|---|
| 1 | `typewriter` | 0 | 1 | 0.1 |
| 2 | `erase` | 0 | 1 | 0.1 |
| 3 | `elastic` | 0 | 1 | 0.1 |
| 4 | `stream-word` | 0 | 1 | 0.1 |

### CAPTION_STYLE (3 total, from @twick/timeline constants)

| # | constant | value | label |
|---|---|---|---|
| 1 | WORD_BG_HIGHLIGHT | `highlight_bg` | Highlight Background |
| 2 | WORD_BY_WORD | `word_by_word` | Word by Word |
| 3 | WORD_BY_WORD_WITH_BG | `word_by_word_with_bg` | Word with Background |

Default caption colors:
- text: `#ffffff`
- highlight: `#ff4081`
- bgColor: `#8C52FF`
- Default font size: 40
- Words per phrase: 4

CAPTION_PROPS (from @twick/studio helpers/constant):
- `highlight_bg` -- font size 40, weight 800, family Rubik, text #fff, highlight #ffff00, bgColor #8B5FBF, stroke #000, shadow offset [2,2], shadow color #000
- `word_by_word` -- font size 40, weight 800, family Rubik, text #fff, highlight #ffff00, bgColor transparent, stroke #000, shadow with blur 12
- `word_by_word_with_bg` -- font size 40, weight 800, family Rubik, text #fff, highlight #ffff00, bgColor #8B5FBF, stroke #000, shadow with blur 12

### CSS MEDIA FILTERS (8 total, from @twick/2d FILTERS object)

| # | key | CSS function | unit | default |
|---|---|---|---|---|
| 1 | `invert` | `invert()` | % | 0 |
| 2 | `sepia` | `sepia()` | % | 0 |
| 3 | `grayscale` | `grayscale()` | % | 0 |
| 4 | `brightness` | `brightness()` | % | 1 (100%) |
| 5 | `contrast` | `contrast()` | % | 1 (100%) |
| 6 | `saturate` | `saturate()` | % | 1 (100%) |
| 7 | `hue` | `hue-rotate()` | deg | 0 |
| 8 | `blur` | `blur()` | px | 0 |

Applied via `VideoElement.setMediaFilter(filterString)` and `ImageElement.setMediaFilter(filterString)`. The filter string is a raw CSS filter value (e.g., `"grayscale(100%)"`, `"sepia(80%) contrast(120%)"`, or `"none"`).

NOTE: These are CSS filter primitives, not preset named filters. The @twick/2d package exposes them as programmable Filter objects. There is no "color filter preset" list (not 16 as might be expected). Applications must compose CSS filter strings themselves.

### GL FRAME EFFECTS (27 total, from @twick/effects catalog)

| # | key | label |
|---|---|---|
| 1 | `sepia` | Sepia |
| 2 | `vignette` | Vignette |
| 3 | `pixelate` | Pixelate |
| 4 | `warp` | Warp |
| 5 | `glitch` | Glitch |
| 6 | `rgbShift` | RGB Shift |
| 7 | `halftone` | Halftone |
| 8 | `hueShift` | Hue Shift |
| 9 | `waveDistort` | Wave Distort |
| 10 | `tvScanlines` | TV Scanlines |
| 11 | `hdr` | HDR Boost |
| 12 | `retro70s` | Retro 70s |
| 13 | `bubbleSparkles` | Bubble Sparkles |
| 14 | `heartSparkles` | Heart Sparkles |
| 15 | `butterflySparkles` | Butterfly Sparkles |
| 16 | `lightning` | Lightning |
| 17 | `lightningVeins` | Lightning Veins |
| 18 | `sparks` | Sparks |
| 19 | `laser` | Laser |
| 20 | `waterReflection` | Water Reflection |
| 21 | `bouncingBalls` | Bouncing Balls |
| 22 | `paperBreakReveal` | Paper Break Reveal |
| 23 | `cameraMove` | Camera Move |
| 24 | `blackFlash` | Black Flash |
| 25 | `brightPulse` | Bright Pulse |
| 26 | `randomAccents` | Random Accents |
| 27 | `graffiti` | Graffiti |

Applied via `ElementFrameEffect` class on VideoElement and ImageElement (`addFrameEffect()`/`setFrameEffects()`). Each effect is a WebGL fragment shader with uniforms: `uTexture`, `uTime`, `uIntensity`, `uResolution`.

NOTE: The @twick/gl-runtime catalog only exposes 5 effects (glitch, sepia, vignette, pixelate, warp) -- the full 27-effect catalog is in @twick/effects.

### AVAILABLE_TEXT_FONTS (22 total, from @twick/video-editor)

| # | constant | value |
|---|---|---|
| 1 | RUBIK | Rubik |
| 2 | MULISH | Mulish |
| 3 | LUCKIEST_GUY | Luckiest Guy |
| 4 | PLAYFAIR_DISPLAY | Playfair Display |
| 5 | ROBOTO | Roboto |
| 6 | POPPINS | Poppins |
| 7 | BANGERS | Bangers |
| 8 | BIRTHSTONE | Birthstone |
| 9 | CORINTHIA | Corinthia |
| 10 | IMPERIAL_SCRIPT | Imperial Script |
| 11 | KUMAR_ONE_OUTLINE | Kumar One Outline |
| 12 | LONDRI_OUTLINE | Londrina Outline |
| 13 | MARCK_SCRIPT | Marck Script |
| 14 | MONTSERRAT | Montserrat |
| 15 | PATTAYA | Pattaya |
| 16 | PERALTA | Peralta |
| 17 | IMPACT | Impact |
| 18 | LUMANOSIMO | Lumanosimo |
| 19 | KAPAKANA | Kapakana |
| 20 | HANDYRUSH | HandyRush |
| 21 | DASHER | Dasher |
| 22 | BRITTANY_SIGNATURE | Brittany Signature |

Fonts are loaded via Google Fonts and cdnfonts.com @import rules in studio.css/video-editor.css, with @font-face fallbacks in @twick/visualizer for offline rendering.

---

## Section E -- TimelineEditor and useTimelineContext API

### TimelineEditor class (from @twick/timeline)

```ts
class TimelineEditor {
  constructor(context: TimelineOperationContext)
  getContext(): TimelineOperationContext
  pauseVideo(): void
  getTimelineData(): TimelineTrackData | null
  getLatestVersion(): number
  addTrack(name: string, type?: string): Track
  getTrackById(id: string): Track | null
  getTrackByName(name: string): Track | null
  getSubtiltesTrack(): Track | null       // NOTE: typo in upstream
  removeTrackById(id: string): void
  removeTrack(track: Track): void
  refresh(): void
  addElementToTrack(track: Track, element: TrackElement): Promise<boolean>
  removeElement(element: TrackElement): boolean
  updateElement(element: TrackElement): TrackElement
  splitElement(element: TrackElement, splitTime: number): Promise<SplitResult>
  cloneElement(element: TrackElement): TrackElement | null
  reorderTracks(tracks: Track[]): void
  updateHistory(timelineTrackData: TimelineTrackData): void
  undo(): void
  redo(): void
  resetHistory(): void
  loadProject(data: { tracks: TrackJSON[]; version: number }): void
  getVideoAudio(): Promise<string>
}
```

### useTimelineContext() hook (from @twick/timeline)

```ts
type TimelineContextType = {
  contextId: string
  editor: TimelineEditor
  selectedItem: Track | TrackElement | null
  changeLog: number
  timelineAction: { type: string; payload: any }
  videoResolution: Size
  totalDuration: number
  present: ProjectJSON | null
  canUndo: boolean
  canRedo: boolean
  setSelectedItem: (item: Track | TrackElement | null) => void
  setTimelineAction: (type: string, payload: any) => void
  setVideoResolution: (size: Size) => void
}
```

### TimelineProvider props

```ts
interface TimelineProviderProps {
  children: React.ReactNode
  contextId: string
  resolution?: Size
  initialData?: { tracks: TrackJSON[]; version: number }
  undoRedoPersistenceKey?: string
  maxHistorySize?: number
  analytics?: AnalyticsConfig    // { enabled?: boolean, apiKey?, apiHost? }
}
```

### TIMELINE_ACTION constants

```ts
TIMELINE_ACTION.NONE = "none"
TIMELINE_ACTION.SET_PLAYER_STATE = "setPlayerState"
TIMELINE_ACTION.UPDATE_PLAYER_DATA = "updatePlayerData"
TIMELINE_ACTION.ON_PLAYER_UPDATED = "onPlayerUpdated"
```

### TIMELINE_ELEMENT_TYPE constants

```ts
VIDEO = "video"
CAPTION = "caption"
IMAGE = "image"
AUDIO = "audio"
TEXT = "text"
RECT = "rect"
CIRCLE = "circle"
ICON = "icon"
```

### Convenience hooks from @twick/video-editor

```ts
// useEditorManager -- wraps addElement and updateElement with collision detection
const { addElement, updateElement } = useEditorManager()

// useTimelineControl -- wraps split, delete, undo, redo
const { splitElement, deleteItem, handleUndo, handleRedo } = useTimelineControl()

// usePlayerControl -- wraps play/pause toggle
const { togglePlayback } = usePlayerControl()
```

### useStudioManager (from @twick/studio)

```ts
const {
  selectedProp,    // string -- currently selected property panel
  setSelectedProp,
  selectedTool,    // string -- currently selected tool
  setSelectedTool,
  selectedElement, // TrackElement | null
  addElement,      // (element: TrackElement) => Promise<void>
  updateElement    // (element: TrackElement) => void
} = useStudioManager()
```

---

## Section F -- useLivePlayerContext API

### PLAYER_STATE enum

```ts
const PLAYER_STATE = {
  REFRESH: "Refresh",
  PLAYING: "Playing",
  PAUSED: "Paused"
}
```

### useLivePlayerContext() return value

```ts
{
  seekTime: number
  playerState: string             // "Refresh" | "Playing" | "Paused"
  currentTime: number
  playerVolume: number
  getCurrentTime: () => number
  setSeekTime: (time: number) => void
  setPlayerState: (state: string) => void   // auto-syncs seekTime on pause
  setCurrentTime: (time: number) => void
  setPlayerVolume: (volume: number) => void
}
```

NOTE: `setPlayerState("Paused")` automatically sets `seekTime = currentTime`.

### LivePlayer component

```ts
import { LivePlayer } from '@twick/live-player'
```

Requires wrapping in `<LivePlayerProvider>`.

### Helper utilities

```ts
generateId(): string                    // unique ID generator
getBaseProject(videoSize, playerId): object   // base project JSON scaffold
```

---

## Section G -- Branding presence check

### Findings

1. **Header "Twick Studio" text** (line 577 of studio index.mjs):
   ```jsx
   <h1 className="text-gradient">Twick Studio</h1>
   ```
   Rendered inside `<header className="header">` by the `StudioHeader` component. Uses the Clapperboard icon from lucide-react.

2. **No logo image files found**: `find @twick -name "*.svg" -o -name "*.png" | grep -i "logo|brand"` returned nothing.

3. **No `hideLogo` or `branding` prop**: The TwickStudio component only accepts `studioConfig?: StudioConfig`. There is no built-in mechanism to hide or replace the header branding.

4. **PostHog analytics telemetry**: The `TimelineProvider` includes PostHog integration. Can be disabled by passing `analytics: { enabled: false }` to `TimelineProvider`.

5. **Telemetry package** (@twick/telemetry): Sends events like `twick-render-started`, `twick-server-started`, `twick-cli-command`, `twick-create-command`, `twick-error`. This runs server-side and is not relevant to frontend branding.

6. **"twick" references in CSS**: The `.twick-editor-*` and `.twick-track-*` and `.twick-timeline-*` class names are functional selectors, not branding. The `.twick-player-root` class scopes the player-react Tailwind styles.

### Branding removal strategy

The header is rendered by the `StudioHeader` default export. Two approaches:

**Option A (CSS override)**: Hide the header entirely or replace its content:
```css
.header .text-gradient { display: none; }
/* or */
.header .text-gradient { font-size: 0; visibility: hidden; }
.header .text-gradient::after {
  content: "LuminaCast Studio";
  visibility: visible;
  font-size: 1.5rem;
}
```

**Option B (MutationObserver)**: Watch for DOM changes and replace:
```js
const observer = new MutationObserver(() => {
  document.querySelectorAll('.text-gradient').forEach(el => {
    if (el.textContent === 'Twick Studio') {
      el.textContent = 'LuminaCast Studio';
    }
  });
});
observer.observe(document.body, { childList: true, subtree: true });
```

**Option C (Do not use TwickStudio, use VideoEditor directly)**: Since `VideoEditor` does not render a header at all (it accepts `leftPanel`, `rightPanel`, `bottomPanel`), you can skip `TwickStudio` entirely and build a custom shell around `VideoEditor`. The `TwickStudio` component is essentially a wrapper that adds the header, sidebar, and panels. The lower-level `VideoEditor` + hooks give full control.

---

## Section H -- Recommended actions

### 1. CSS selectors to override in twick-theme.css

**Priority 1 (colors/backgrounds):**
- `:root` CSS custom properties -- override `--color-purple-*` palette to LuminaCast brand colors
- `.studio-container`, `.studio-content` -- background colors
- `.header` -- bg, border-bottom
- `.sidebar` -- bg, border-right
- `.properties-panel` -- bg gradient, border-left
- `.panel-container` -- bg
- `.twick-editor-timeline-section` -- bg #252525
- `.twick-track` -- bg #474747
- `.twick-track-header` -- bg #171717
- `.twick-seek-track` -- bg #171717
- `.player-controls` -- bg, border-top

**Priority 2 (accent/interactive):**
- `.btn-primary` -- gradient colors
- `.toolbar-btn.active`, `.props-toolbar-btn.active` -- gradient, box-shadow
- `.accordion-header:hover`, `.prop-header:hover` -- hover gradient
- `.twick-track-element-selected` -- border color
- `.twick-seek-track-pin`, `.twick-seek-track-handle` -- playhead color (currently #FFFFFF)
- `.property-indicator` -- purple-400 dot
- `::-webkit-scrollbar-thumb` -- purple-600

**Priority 3 (typography):**
- `.text-gradient` -- gradient direction and colors (this is also the branding text)
- `.text-gradient-purple`, `.text-gradient-blue` -- accent gradients
- `body` -- font-family

### 2. Branding removal

- **Best approach**: Use **Option C** (build custom shell around `VideoEditor` instead of `TwickStudio`) for full control. OR use **Option A** (CSS `::after` pseudo-element replacement) for minimal changes.
- The `StudioHeader` is a separate export and could potentially be replaced with a custom header in a wrapper.
- Disable analytics: pass `analytics: { enabled: false }` to `TimelineProvider`.

### 3. Complete list of animations/effects/styles/filters for UI constants

```ts
// Animations (5)
export const ANIMATION_NAMES = ['fade', 'rise', 'blur', 'breathe', 'succession'] as const;

// Text Effects (4)
export const TEXT_EFFECT_NAMES = ['typewriter', 'erase', 'elastic', 'stream-word'] as const;

// Caption Styles (3)
export const CAPTION_STYLES = ['highlight_bg', 'word_by_word', 'word_by_word_with_bg'] as const;

// CSS Media Filters (8 primitives)
export const MEDIA_FILTERS = ['invert', 'sepia', 'grayscale', 'brightness', 'contrast', 'saturate', 'hue-rotate', 'blur'] as const;

// GL Frame Effects (27)
export const FRAME_EFFECTS = [
  'sepia', 'vignette', 'pixelate', 'warp', 'glitch', 'rgbShift', 'halftone',
  'hueShift', 'waveDistort', 'tvScanlines', 'hdr', 'retro70s',
  'bubbleSparkles', 'heartSparkles', 'butterflySparkles',
  'lightning', 'lightningVeins', 'sparks', 'laser',
  'waterReflection', 'bouncingBalls', 'paperBreakReveal',
  'cameraMove', 'blackFlash', 'brightPulse', 'randomAccents', 'graffiti'
] as const;

// Text Fonts (22)
export const TEXT_FONTS = [
  'Rubik', 'Mulish', 'Luckiest Guy', 'Playfair Display', 'Roboto', 'Poppins',
  'Bangers', 'Birthstone', 'Corinthia', 'Imperial Script', 'Kumar One Outline',
  'Londrina Outline', 'Marck Script', 'Montserrat', 'Pattaya', 'Peralta',
  'Impact', 'Lumanosimo', 'Kapakana', 'HandyRush', 'Dasher', 'Brittany Signature'
] as const;
```

### 4. API limitations discovered

1. **No theme prop**: TwickStudio/VideoEditor have no theme or customStyles prop. All styling must be done via CSS overrides.
2. **No branding prop**: No `hideLogo`, `branding`, or `logo` prop exists. Branding removal requires CSS or MutationObserver.
3. **Typo in API**: `getSubtiltesTrack()` is misspelled in the upstream.
4. **mediaFilter is raw CSS**: There is no named filter preset system. `setMediaFilter()` accepts raw CSS filter strings.
5. **GL effects split across packages**: @twick/effects has 27 effects, but @twick/gl-runtime only catalogs 5. The full catalog requires importing from @twick/effects.
6. **No color filter presets**: The expected "~16 color filters" do not exist as named presets. Instead, there are 8 CSS filter primitives (from @twick/2d) and 27 GL shader effects (from @twick/effects).
7. **PostHog analytics baked in**: TimelineProvider bundles PostHog. Must explicitly disable with `analytics: { enabled: false }`.
8. **Font loading relies on external CDN**: studio.css imports from Google Fonts and cdnfonts.com. For production, consider self-hosting fonts or using the @twick/visualizer font-face declarations.
9. **CSS is duplicated**: The twick-utilities.css content (`:root` vars, button styles, etc.) is duplicated between studio.css and video-editor.css. Overriding in one does not affect the other.
10. **player-react scoped Tailwind**: The `.twick-player-root` scoped Tailwind may conflict with project-level Tailwind. Be aware of class name collisions.

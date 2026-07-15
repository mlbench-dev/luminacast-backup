import {EditorStarterItem} from './items/item-type';

export const MIN_TIMELINE_ZOOM = 0;
export const MAX_TIMELINE_WIDTH = 40_000; // want higher max zoom? increase this value

export const PLAYHEAD_WIDTH = 19;

// Make enough space for the playhead to be visible, and add some more padding so it is not glued to the left edge
export const TIMELINE_HORIZONTAL_PADDING = Math.ceil(PLAYHEAD_WIDTH / 2) + 5;

export const scrollbarStyle: React.CSSProperties = {
	scrollbarWidth: 'thin',
	scrollbarColor:
		'var(--color-editor-starter-scrollbar-thumb) var(--color-editor-starter-scrollbar-track)',
};

export const ITEM_COLORS: Record<EditorStarterItem['type'], string> = {
	image: '#3A7A44',
	gif: '#3A7A44',
	text: '#7A5DE8',
	video: '#475569',
	solid: '#B04BCF',
	audio: '#475569',
	captions: '#475569',
};

/** Category-based track colors for Luminacast timeline items */
export const TRACK_COLORS: Record<string, string> = {
	video_face: '#6366f1',     // indigo — avatar face
	audio_voice: '#818cf8',    // lighter indigo — avatar voice
	product: '#f59e0b',        // amber — products
	stock_video: '#06b6d4',    // cyan — stock video
	stock_photo: '#14b8a6',    // teal — stock photo
	generated: '#a78bfa',      // purple — AI generated
	caption: '#64748b',        // slate — captions
	music: '#ec4899',          // pink — music
	default: '#4ade80',        // green — fallback
};

export const DEFAULT_COMPOSITION_WIDTH = 1080;
export const DEFAULT_COMPOSITION_HEIGHT = 1920;
export const DEFAULT_FPS = 30;

export const SCROLL_EDGE_THRESHOLD = 200; // px from edge that activates auto-scroll
export const MAX_AUTOSCROLL_SPEED = 10;

export const MAX_FADE_DURATION_SECONDS = Infinity;

// Timeline snapping
// Default pixel threshold used when evaluating proximity to snap points.
export const DEFAULT_TIMELINE_SNAPPING_THRESHOLD_PIXELS = 5;

// Canvas snapping
// Default pixel threshold for canvas snap targets (edges + center)
export const CANVAS_SNAP_THRESHOLD_PIXELS = 10;
// Color for canvas snap indicator guide lines
export const CANVAS_SNAP_INDICATOR_COLOR = '#FF00FF';
// Line width for canvas snap indicators
export const CANVAS_SNAP_LINE_WIDTH = 0.5;

/**
 * Source: Remotion Editor Starter v4.0.433, file: flags.ts
 * Adapted for Luminacast: Disabled project management, rendering, asset upload,
 * save/load, and marketing features. Kept core editing features.
 *
 * Feature flag reference: https://www.remotion.dev/docs/editor-starter/features
 */

// ============================================================
// Action row
// ============================================================
export const FEATURE_DRAW_SOLID_TOOL = false;      // Not needed for video podcast editor
export const FEATURE_CREATE_TEXT_TOOL = false;       // Text handled via captions/products
export const FEATURE_IMPORT_ASSETS_TOOL = false;     // Replaced by LuminacastMediaPanel
export const FEATURE_DOWNLOAD_STATE = false;         // No JSON export feature
export const FEATURE_LOAD_STATE = false;             // State loaded from Luminacast API
export const FEATURE_SAVE_BUTTON = false;            // Luminacast auto-saves via debounced castsApi.saveTimeline()
export const FEATURE_UNDO_BUTTON = true;
export const FEATURE_REDO_BUTTON = true;

// ============================================================
// Timeline
// ============================================================
export const FEATURE_TIMELINE_ZOOM_SLIDER = true;
export const FEATURE_FILMSTRIP = true;
export const FEATURE_WAVEFORM = true;
export const FEATURE_AUDIO_WAVEFORM_FOR_VIDEO_ITEM = true;
export const FEATURE_TIMELINE_VOLUME_CONTROL = true;
export const FEATURE_DROP_ASSETS_ON_TIMELINE = true;
export const FEATURE_AUDIO_FADE_CONTROL = true;
export const FEATURE_VISUAL_FADE_CONTROL = true;
export const FEATURE_SPLIT_ITEM = true;
export const FEATURE_HIDE_TRACKS = true;
export const FEATURE_MAX_TRIM_INDICATORS = true;
export const FEATURE_MUTE_TRACKS = true;
export const FEATURE_ROLLING_EDITS = true;
export const FEATURE_TIMELINE_MARQUEE_SELECTION = true;
export const FEATURE_FOLLOW_PLAYHEAD_WHILE_PLAYING = true;
export const FEATURE_RESIZE_TIMELINE_PANEL = true;
export const FEATURE_TIMELINE_SNAPPING = true;

// ============================================================
// Inspector
// ============================================================
export const FEATURE_SWAP_COMPOSITION_DIMENSIONS_BUTTON = true;
export const FEATURE_SOURCE_CONTROL = true;
export const FEATURE_ALIGNMENT_CONTROL = true;
export const FEATURE_POSITION_CONTROL = true;
export const FEATURE_DIMENSIONS_CONTROL = true;
export const FEATURE_KEEP_ASPECT_RATIO_CONTROL = true;
export const FEATURE_BORDER_RADIUS_CONTROL = true;
export const FEATURE_OPACITY_CONTROL = true;
export const FEATURE_CROP_CONTROL = true;
export const FEATURE_TEXT_ALIGNMENT_CONTROL = true;
export const FEATURE_FONT_FAMILY_CONTROL = true;
export const FEATURE_FONT_FAMILY_DROPDOWN_RENDER_IN_FONT = true;
export const FEATURE_CHANGE_FONT_FAMILY_ON_HOVER = true;
export const FEATURE_FONT_STYLE_CONTROL = true;
export const FEATURE_FONT_FAMILY_CONTROLS_PREVIEW_ON_HOVER = true;
export const FEATURE_CHANGE_FONT_STYLE_ON_HOVER = true;
export const FEATURE_TEXT_FONT_SIZE_CONTROL = true;
export const FEATURE_TEXT_VALUE_CONTROL = true;
export const FEATURE_COLOR_CONTROL = true;
export const FEATURE_ROTATION_CONTROL = true;
export const FEATURE_ROTATE_90_DEGREES_BUTTON = true;
export const FEATURE_TEXT_LINE_HEIGHT_CONTROL = true;
export const FEATURE_TEXT_LETTER_SPACING_CONTROL = true;
export const FEATURE_TEXT_DIRECTION_CONTROL = true;
export const FEATURE_PLAYBACKRATE_CONTROL = true;
export const FEATURE_VOLUME_CONTROL = true;
export const FEATURE_TOKENS_CONTROL = true;
export const FEATURE_CAPTIONS_PAGE_DURATION_CONTROL = true;
export const FEATURE_CAPTIONS_HIGHLIGHT_COLOR_CONTROL = true;
export const FEATURE_TEXT_STROKE_WIDTH_CONTROL = true;
export const FEATURE_TEXT_STROKE_COLOR_CONTROL = true;
export const FEATURE_TEXT_MAX_LINES_CONTROL = true;
export const FEATURE_TEXT_BACKGROUND_CONTROL = true;
export const FEATURE_TEXT_BACKGROUND_HORIZONTAL_PADDING_CONTROL = true;
export const FEATURE_TEXT_BACKGROUND_BORDER_RADIUS_CONTROL = true;

// ============================================================
// Playback
// ============================================================
export const FEATURE_JUMP_TO_START_BUTTON = true;
export const FEATURE_JUMP_TO_END_BUTTON = true;
export const FEATURE_FULLSCREEN_CONTROL = true;
export const FEATURE_MUTE_BUTTON = true;
export const FEATURE_LOOP_BUTTON = true;

// ============================================================
// Canvas
// ============================================================
export const FEATURE_SHIFT_AXIS_LOCK = true;
export const FEATURE_SHIFT_KEY_TO_OVERRIDE_ASPECT_RATIO_LOCK = false;
export const FEATURE_CANVAS_MARQUEE_SELECTION = true;
export const FEATURE_DROP_ASSETS_ON_CANVAS = true;
export const FEATURE_CANVAS_ZOOM_CONTROLS = true;
export const FEATURE_CANVAS_ZOOM_GESTURES = true;
export const FEATURE_CANVAS_ZOOM_KEYBOARD_SHORTCUTS = true;
export const FEATURE_BRING_TO_FRONT = true;
export const FEATURE_SEND_TO_BACK = true;
export const FEATURE_CANVAS_SNAPPING = true;

// ============================================================
// Cropping
// ============================================================
export const FEATURE_CROPPING = true;
export const FEATURE_DOUBLE_CLICK_TO_CROP = true;
export const FEATURE_CROP_BACKGROUNDS = true;

// ============================================================
// Behaviors
// ============================================================
export const FEATURE_CUT_LAYERS = true;
export const FEATURE_COPY_LAYERS = true;
export const FEATURE_DUPLICATE_LAYERS = true;
export const FEATURE_PASTE_TEXT = true;
export const FEATURE_PASTE_ASSETS = true;
export const FEATURE_CACHE_ASSETS_LOCALLY = false;    // Assets served from CDN
export const FEATURE_LOAD_STATE_FROM_URL = false;      // Not applicable
export const FEATURE_WARN_ON_LONG_RUNNING_PROCESS_IN_PROGRESS = true;

// ============================================================
// Keyboard Shortcuts
// ============================================================
export const FEATURE_SAVE_SHORTCUT = false;            // No manual save concept
export const FEATURE_UNDO_SHORTCUT = true;
export const FEATURE_REDO_SHORTCUT = true;
export const FEATURE_SELECT_ALL_SHORTCUT = true;
export const FEATURE_DELETE_SHORTCUT = true;
export const FEATURE_BACKSPACE_TO_DELETE = true;
export const FEATURE_SNAPPING_SHORTCUT = true;

// ============================================================
// Captioning
// ============================================================
export const FEATURE_CAPTIONING = true;

// ============================================================
// Rendering — DISABLED: Luminacast uses FinalizingPhase + backend render pipeline
// ============================================================
export const FEATURE_RENDERING = false;
export const FEATURE_RENDERING_CODEC_SELECTOR = false;

// ============================================================
// Media
// ============================================================
export const FEATURE_NEW_MEDIA_TAGS = false;

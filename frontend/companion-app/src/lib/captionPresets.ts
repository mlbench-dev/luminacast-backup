// 15 caption presets — shared between Remotion preview (caption-page.tsx)
// and FFmpeg render (cast_ffmpeg_composer.py via the captionPreset prop).
// Each preset carries the visual style (font, size, color, stroke, position)
// used by both engines, plus a Remotion-only `animation` key that drives
// the per-token renderToken switch in caption-page.tsx.

export type CaptionAnimation =
	| "word_highlight"
	| "slide_in"
	| "word_bounce"
	| "fade_in"
	| "glow_pulse"
	| "typewriter"
	| "fade_slide_up"
	| "slam_in"
	| "scribble_in"
	| "color_wave"
	| "word_box"
	| "fill_on_active"
	| "none"
	| "dim_inactive"
	| "wobble";

export type CaptionPosition = "bottom_center" | "center" | "top_center";

export interface CaptionPreset {
	id: string;
	name: string;
	sampleText: string;

	// Shared (Remotion preview + FFmpeg render)
	fontFamily: string;
	fontWeight: number;
	fontSize: number;
	color: string;
	highlightColor: string;
	strokeColor: string;
	strokeWidth: number;
	position: CaptionPosition;
	maxWidth: string;
	textTransform?: "uppercase" | "lowercase" | "none";

	// Remotion-only (rich animation for preview)
	animation: CaptionAnimation;
	background?: string;
	backgroundPadding?: { x: number; y: number };
	backgroundRadius?: number;
	shadow?: string;

	// FFmpeg render hints (approximation for the burnt-in MP4)
	ffmpegBoxEnabled?: boolean;
	ffmpegBoxColor?: string;
}

export const CAPTION_PRESETS: Record<string, CaptionPreset> = {
	hormozi_bold: {
		id: "hormozi_bold",
		name: "Hormozi Bold",
		sampleText: "Listen up",
		fontFamily: "Montserrat",
		fontWeight: 800,
		fontSize: 48,
		color: "#FFFFFF",
		highlightColor: "#FFD700",
		strokeColor: "#000000",
		strokeWidth: 4,
		position: "center",
		maxWidth: "90%",
		animation: "word_highlight",
	},

	pill_highlight: {
		id: "pill_highlight",
		name: "Pill Highlight",
		sampleText: "Listen up",
		fontFamily: "Inter",
		fontWeight: 700,
		fontSize: 40,
		color: "#FFFFFF",
		highlightColor: "#FFFFFF",
		strokeColor: "transparent",
		strokeWidth: 0,
		position: "bottom_center",
		maxWidth: "85%",
		animation: "slide_in",
		background: "rgba(0,0,0,0.65)",
		backgroundPadding: { x: 20, y: 10 },
		backgroundRadius: 24,
		ffmpegBoxEnabled: true,
		ffmpegBoxColor: "black@0.65",
	},

	karaoke_pop: {
		id: "karaoke_pop",
		name: "Karaoke Pop",
		sampleText: "Listen up",
		fontFamily: "Montserrat",
		fontWeight: 900,
		fontSize: 50,
		color: "#FFFFFF",
		highlightColor: "#FFD700",
		strokeColor: "#000000",
		strokeWidth: 4,
		position: "center",
		maxWidth: "90%",
		animation: "word_bounce",
	},

	minimal_lower: {
		id: "minimal_lower",
		name: "Minimal",
		sampleText: "listen up",
		fontFamily: "Inter",
		fontWeight: 400,
		fontSize: 28,
		color: "#FFFFFF",
		highlightColor: "#AACCFF",
		strokeColor: "transparent",
		strokeWidth: 0,
		position: "bottom_center",
		maxWidth: "75%",
		animation: "fade_in",
		shadow: "0 2px 12px rgba(0,0,0,0.7)",
	},

	neon_glow: {
		id: "neon_glow",
		name: "Neon Glow",
		sampleText: "Listen up",
		fontFamily: "Inter",
		fontWeight: 700,
		fontSize: 44,
		color: "#00FF88",
		highlightColor: "#FFFFFF",
		strokeColor: "#003322",
		strokeWidth: 2,
		position: "bottom_center",
		maxWidth: "85%",
		animation: "glow_pulse",
		shadow: "0 0 20px rgba(0,255,136,0.6), 0 0 40px rgba(0,255,136,0.3)",
	},

	typewriter: {
		id: "typewriter",
		name: "Typewriter",
		sampleText: "Listen up_",
		fontFamily: "Courier New",
		fontWeight: 700,
		fontSize: 36,
		color: "#FFFFFF",
		highlightColor: "#FFFFFF",
		strokeColor: "#000000",
		strokeWidth: 2,
		position: "center",
		maxWidth: "85%",
		animation: "typewriter",
	},

	block_quote: {
		id: "block_quote",
		name: "Block Quote",
		sampleText: '"Listen up"',
		fontFamily: "Playfair Display",
		fontWeight: 700,
		fontSize: 38,
		color: "#FFFFFF",
		highlightColor: "#FFD700",
		strokeColor: "transparent",
		strokeWidth: 0,
		position: "center",
		maxWidth: "80%",
		animation: "fade_slide_up",
		background: "rgba(0,0,0,0.5)",
		backgroundPadding: { x: 24, y: 16 },
		backgroundRadius: 16,
		ffmpegBoxEnabled: true,
		ffmpegBoxColor: "black@0.5",
	},

	caps_punch: {
		id: "caps_punch",
		name: "ALL CAPS",
		sampleText: "LISTEN UP",
		fontFamily: "Bebas Neue",
		fontWeight: 400,
		fontSize: 64,
		color: "#FFFFFF",
		highlightColor: "#FF3333",
		strokeColor: "#000000",
		strokeWidth: 5,
		position: "center",
		maxWidth: "90%",
		textTransform: "uppercase",
		animation: "slam_in",
	},

	handwritten: {
		id: "handwritten",
		name: "Handwritten",
		sampleText: "Listen up",
		fontFamily: "Caveat",
		fontWeight: 700,
		fontSize: 46,
		color: "#FFFFFF",
		highlightColor: "#FFAACC",
		strokeColor: "#000000",
		strokeWidth: 2,
		position: "bottom_center",
		maxWidth: "85%",
		animation: "scribble_in",
	},

	gradient_wave: {
		id: "gradient_wave",
		name: "Gradient",
		sampleText: "Listen up",
		fontFamily: "Inter",
		fontWeight: 800,
		fontSize: 44,
		color: "#FF6B6B",
		highlightColor: "#4ECDC4",
		strokeColor: "#000000",
		strokeWidth: 3,
		position: "center",
		maxWidth: "85%",
		animation: "color_wave",
	},

	box_highlight: {
		id: "box_highlight",
		name: "Box Highlight",
		sampleText: "Listen up",
		fontFamily: "Inter",
		fontWeight: 700,
		fontSize: 40,
		color: "#FFFFFF",
		highlightColor: "#FFFFFF",
		strokeColor: "transparent",
		strokeWidth: 0,
		position: "center",
		maxWidth: "85%",
		animation: "word_box",
	},

	outline_only: {
		id: "outline_only",
		name: "Outline",
		sampleText: "Listen up",
		fontFamily: "Montserrat",
		fontWeight: 900,
		fontSize: 52,
		color: "transparent",
		highlightColor: "#FF4444",
		strokeColor: "#FFFFFF",
		strokeWidth: 4,
		position: "center",
		maxWidth: "90%",
		animation: "fill_on_active",
	},

	tiktok_classic: {
		id: "tiktok_classic",
		name: "TikTok Classic",
		sampleText: "Listen up",
		fontFamily: "Inter",
		fontWeight: 600,
		fontSize: 36,
		color: "#FFFFFF",
		highlightColor: "#FFFFFF",
		strokeColor: "#000000",
		strokeWidth: 3,
		position: "bottom_center",
		maxWidth: "80%",
		animation: "none",
	},

	split_color: {
		id: "split_color",
		name: "Split Color",
		sampleText: "Listen up",
		fontFamily: "Inter",
		fontWeight: 800,
		fontSize: 44,
		color: "rgba(255,255,255,0.35)",
		highlightColor: "#FFFFFF",
		strokeColor: "#000000",
		strokeWidth: 3,
		position: "center",
		maxWidth: "85%",
		animation: "dim_inactive",
	},

	comic: {
		id: "comic",
		name: "Comic",
		sampleText: "Listen up!",
		fontFamily: "Bangers",
		fontWeight: 400,
		fontSize: 48,
		color: "#FFEE00",
		highlightColor: "#FF4444",
		strokeColor: "#000000",
		strokeWidth: 4,
		position: "center",
		maxWidth: "85%",
		animation: "wobble",
	},
};

export const CAPTION_PRESET_IDS = Object.keys(CAPTION_PRESETS);
export const DEFAULT_CAPTION_PRESET_ID = "hormozi_bold";

export function getCaptionPreset(id: string | null | undefined): CaptionPreset {
	if (!id) return CAPTION_PRESETS[DEFAULT_CAPTION_PRESET_ID];
	return CAPTION_PRESETS[id] ?? CAPTION_PRESETS[DEFAULT_CAPTION_PRESET_ID];
}

// Map a preset's CSS position to a vertical anchor used by both the
// editor's CaptionsItem.top and the FFmpeg drawtext y= expression.
// Returns a fraction of the canvas height (0..1).
export function presetPositionFraction(preset: CaptionPreset): number {
	switch (preset.position) {
		case "top_center":
			return 0.1;
		case "center":
			return 0.5;
		case "bottom_center":
		default:
			// Lifted from 0.78 — at 0.78 the rendered caption sits under the
			// video player's control bar. 0.72 clears it while staying "lower third".
			return 0.72;
	}
}

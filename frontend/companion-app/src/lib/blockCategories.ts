/**
 * Single source of truth for block category presentation.
 *
 * Used by:
 *   - ScriptPhase   (category dropdown + pill on each block card)
 *   - ArrangePhase  (color border on the timeline regions)
 *   - editor-starter timeline (track-region tinting)
 *
 * Colors here ARE the brand language for "what kind of block" — keeping
 * them in one place prevents the dreaded "the pill is purple but the
 * timeline strip is teal" mismatch.
 */
export interface BlockCategoryInfo {
	/** Stable enum value persisted in DB (snake_case). */
	value: string;
	/** Human label used in dropdowns + tooltips. */
	label: string;
	/** Tailwind classes for the filled pill (background + text). */
	pillClass: string;
	/** Tailwind class for the small color dot. */
	dotClass: string;
	/** Hex color (CSS). For places where we can't use Tailwind (timeline regions, inline SVG). */
	hex: string;
	/** Lighter hex used for borders / accents. */
	hexAccent: string;
	/** One-line "what this block type is", shown as help on the category picker. */
	blurb: string;
	/**
	 * Consequence shown when the user switches an existing block TO this type,
	 * so a category change isn't a silent no-op / surprise. Kept short —
	 * rendered inline under the dropdown.
	 */
	onSwitch: string;
}

export const BLOCK_CATEGORIES: BlockCategoryInfo[] = [
	{
		value: "avatar_speaking",
		label: "Avatar speaking",
		pillClass: "bg-indigo-600/80 text-indigo-100",
		dotClass: "bg-indigo-400",
		hex: "#4f46e5",
		hexAccent: "#818cf8",
		blurb: "Avatar talks straight to camera, full frame. B-roll plays as short cutaways on top.",
		onSwitch: "The avatar is the shot. Any b-roll becomes a brief cutaway, not a full cover.",
	},
	{
		// avatar_action: avatar performs an action in a scene-specific
		// setting (running through a jungle, walking down a beach…). The
		// system generates start/end frames of the avatar in that scene
		// via FLUX Kontext (face preserved) and animates between them
		// with I2V. Replaces the legacy avatar_motion (T2V, no face
		// conditioning) and avatar_acting (body shots only) categories.
		value: "avatar_action",
		label: "Action",
		pillClass: "bg-orange-500/80 text-orange-100",
		dotClass: "bg-orange-400",
		hex: "#f97316",
		hexAccent: "#fb923c",
		blurb: "Avatar performs an action in a generated scene (walking, holding the product).",
		onSwitch: "The avatar is animated between two AI scene frames. Set the motion prompt below, then regenerate.",
	},
	{
		value: "avatar_voiceover",
		label: "Avatar voiceover",
		pillClass: "bg-blue-600/80 text-blue-100",
		dotClass: "bg-blue-400",
		hex: "#2563eb",
		hexAccent: "#60a5fa",
		blurb: "No avatar on screen — the script plays as narration over stock / AI / product footage.",
		onSwitch: "The avatar won't appear. Your script plays as voiceover over the b-roll. Regenerate to apply.",
	},
	{
		// pip_talking_head: avatar appears as a small talking head over
		// a background. Internal key kept (no DB rename); displayed
		// label is "Talking head".
		value: "pip_talking_head",
		label: "Talking head",
		pillClass: "bg-green-600/80 text-green-100",
		dotClass: "bg-green-400",
		hex: "#16a34a",
		hexAccent: "#4ade80",
		blurb: "Avatar in a small corner window over a background or b-roll.",
		onSwitch: "The avatar shrinks to a corner window. The background fills the rest of the frame.",
	},
	{
		value: "stock_photo",
		label: "Stock photo",
		pillClass: "bg-teal-500/80 text-teal-100",
		dotClass: "bg-teal-400",
		hex: "#14b8a6",
		hexAccent: "#5eead4",
		blurb: "A library photo fills the frame. No avatar.",
		onSwitch: "This block becomes a full-frame photo. No avatar, no narration unless you add it.",
	},
	{
		value: "stock_video",
		label: "Stock video",
		pillClass: "bg-yellow-500/80 text-yellow-100",
		dotClass: "bg-yellow-400",
		hex: "#eab308",
		hexAccent: "#fde047",
		blurb: "A library video clip fills the frame. No avatar.",
		onSwitch: "This block becomes a full-frame stock clip. No avatar, no narration unless you add it.",
	},
	{
		value: "generated_photo",
		label: "Generated photo",
		pillClass: "bg-purple-500/80 text-purple-100",
		dotClass: "bg-purple-400",
		hex: "#a855f7",
		hexAccent: "#d8b4fe",
		blurb: "AI generates a still image from the block's text prompt. No avatar.",
		onSwitch: "This block becomes an AI image built from the block text. No avatar.",
	},
	{
		value: "generated_video",
		label: "Generated video",
		pillClass: "bg-pink-500/80 text-pink-100",
		dotClass: "bg-pink-400",
		hex: "#ec4899",
		hexAccent: "#f9a8d4",
		blurb: "AI generates a motion clip from the block's text prompt. No avatar.",
		onSwitch: "This block becomes an AI video built from the block text. No avatar. Regenerate to apply.",
	},
];

/** Lookup by value with safe fallback to avatar_speaking. */
export const CATEGORY_MAP: Record<string, BlockCategoryInfo> = Object.fromEntries(
	BLOCK_CATEGORIES.map((c) => [c.value, c]),
);

/**
 * Read-time alias map. Legacy categories (avatar_motion, avatar_acting,
 * avatar_body_motion) collapse into the merged `avatar_action`. We don't
 * rewrite the DB on the client — we just display old blocks correctly.
 */
export const LEGACY_CATEGORY_ALIASES: Record<string, string> = {
	avatar_motion: "avatar_action",
	avatar_acting: "avatar_action",
	avatar_body_motion: "avatar_action",
};

export function normalizeCategory(value?: string | null): string {
	const v = value || "avatar_speaking";
	return LEGACY_CATEGORY_ALIASES[v] ?? v;
}

export function getCategoryInfo(value?: string | null): BlockCategoryInfo {
	const v = normalizeCategory(value);
	return CATEGORY_MAP[v] ?? CATEGORY_MAP["avatar_speaking"];
}

/**
 * True when the block's own generated clip IS the visual (an action / motion
 * block). B-roll must never be layered over these — it would just cover the
 * action the block exists to show.
 */
export function blockOwnsItsVisual(
	category?: string | null,
	renderMode?: string | null,
): boolean {
	if (normalizeCategory(category) === "avatar_action") return true;
	const rm = (renderMode || "").toLowerCase();
	return rm === "motion" || rm === "body_motion";
}

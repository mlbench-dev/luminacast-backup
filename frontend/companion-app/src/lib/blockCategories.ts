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
}

export const BLOCK_CATEGORIES: BlockCategoryInfo[] = [
	{
		value: "avatar_speaking",
		label: "Avatar speaking",
		pillClass: "bg-indigo-600/80 text-indigo-100",
		dotClass: "bg-indigo-400",
		hex: "#4f46e5",
		hexAccent: "#818cf8",
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
	},
	{
		value: "avatar_voiceover",
		label: "Avatar voiceover",
		pillClass: "bg-blue-600/80 text-blue-100",
		dotClass: "bg-blue-400",
		hex: "#2563eb",
		hexAccent: "#60a5fa",
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
	},
	{
		value: "stock_photo",
		label: "Stock photo",
		pillClass: "bg-teal-500/80 text-teal-100",
		dotClass: "bg-teal-400",
		hex: "#14b8a6",
		hexAccent: "#5eead4",
	},
	{
		value: "stock_video",
		label: "Stock video",
		pillClass: "bg-yellow-500/80 text-yellow-100",
		dotClass: "bg-yellow-400",
		hex: "#eab308",
		hexAccent: "#fde047",
	},
	{
		value: "generated_photo",
		label: "Generated photo",
		pillClass: "bg-purple-500/80 text-purple-100",
		dotClass: "bg-purple-400",
		hex: "#a855f7",
		hexAccent: "#d8b4fe",
	},
	{
		value: "generated_video",
		label: "Generated video",
		pillClass: "bg-pink-500/80 text-pink-100",
		dotClass: "bg-pink-400",
		hex: "#ec4899",
		hexAccent: "#f9a8d4",
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

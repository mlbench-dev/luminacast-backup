/**
 * Caption presets — thin wrapper over the canonical 15-preset module at
 * `@/lib/captionPresets`. Kept here so existing imports keep working.
 *
 * Each preset is a partial CaptionsItem patch. Applying a preset to a
 * caption item means: spread the visual fields onto the item; leave
 * timing (from / durationInFrames / assetId) untouched. The preset id
 * gets stored on metadata.caption_preset so the FFmpeg renderer reads
 * the same animation hooks the preview uses.
 *
 * Per-block override: the CaptionStyleBar skips any item with
 * `metadata.preset_override === true` when a style is picked, so a block
 * locked from the inspector keeps its own style.
 */
import type {CaptionsItem} from '../items/captions/captions-item-type';
import {
	CAPTION_PRESETS as RAW_CAPTION_PRESETS,
	CAPTION_PRESET_IDS,
	DEFAULT_CAPTION_PRESET_ID,
	presetPositionFraction,
	type CaptionPreset as RawCaptionPreset,
} from '@/lib/captionPresets';

export type CaptionPresetId = string;

export type CaptionPresetPatch = Partial<
	Pick<
		CaptionsItem,
		| 'fontFamily'
		| 'fontStyle'
		| 'fontSize'
		| 'lineHeight'
		| 'letterSpacing'
		| 'align'
		| 'color'
		| 'highlightColor'
		| 'strokeColor'
		| 'strokeWidth'
		| 'maxLines'
		| 'pageDurationInMilliseconds'
		| 'fadeInDurationInSeconds'
		| 'fadeOutDurationInSeconds'
	>
>;

export interface CaptionPreset {
	id: CaptionPresetId;
	name: string;
	description: string;
	sampleText: string;
	patch: CaptionPresetPatch;
	/** The full canonical preset, including animation/box/shadow hints. */
	raw: RawCaptionPreset;
}

function rawToPatch(raw: RawCaptionPreset): CaptionPresetPatch {
	return {
		fontFamily: raw.fontFamily,
		fontStyle: {variant: 'normal', weight: String(raw.fontWeight)},
		fontSize: raw.fontSize,
		lineHeight: 1.2,
		letterSpacing: 0,
		align: 'center',
		color: raw.color,
		highlightColor: raw.highlightColor,
		strokeColor: raw.strokeColor,
		strokeWidth: raw.strokeWidth,
		maxLines: 2,
		pageDurationInMilliseconds: 3500,
		fadeInDurationInSeconds: 0.05,
		fadeOutDurationInSeconds: 0.05,
	};
}

function describePreset(raw: RawCaptionPreset): string {
	return `${raw.name} — ${raw.fontFamily} ${raw.fontWeight}, ${raw.animation.replace(/_/g, ' ')}`;
}

export const CAPTION_PRESETS: Record<CaptionPresetId, CaptionPreset> =
	Object.fromEntries(
		CAPTION_PRESET_IDS.map((id) => {
			const raw = RAW_CAPTION_PRESETS[id];
			const preset: CaptionPreset = {
				id,
				name: raw.name,
				description: describePreset(raw),
				sampleText: raw.sampleText,
				patch: rawToPatch(raw),
				raw,
			};
			return [id, preset];
		}),
	);

export const CAPTION_PRESET_LIST: CaptionPreset[] = CAPTION_PRESET_IDS.map(
	(id) => CAPTION_PRESETS[id],
);

/**
 * Apply a preset to a single caption item, preserving timing + assetId
 * + position metadata. The preset_override flag, if set, is also
 * preserved so per-block locks survive a preset switch.
 *
 * `canvasHeight`, when provided, also moves the item's own `top` to match
 * the NEW preset's real vertical position (top_center=0.1 / center=0.5 /
 * bottom_center=0.72 of canvas height). Without this, switching presets in
 * the live editor left the caption box exactly where it was — only the
 * BACKEND render (which computes positionY fresh from metadata.caption_
 * preset at save time, never from this item's `top`) actually moved,
 * so a "center" preset rendered dead-center in the final video while the
 * editor preview kept showing it in the lower third. Optional (rather than
 * required) because a couple of call sites patch items outside a live
 * canvas context where canvas height isn't available — those keep the
 * pre-existing (if incomplete) behavior rather than being forced to thread
 * a value they don't have.
 */
export function applyPresetToCaptionItem(
	item: CaptionsItem,
	presetId: CaptionPresetId,
	canvasHeight?: number,
): CaptionsItem {
	const preset = CAPTION_PRESETS[presetId];
	if (!preset) return item;
	return {
		...item,
		...preset.patch,
		...(canvasHeight
			? {top: Math.round(canvasHeight * presetPositionFraction(preset.raw))}
			: null),
		metadata: {
			...item.metadata,
			caption_preset: presetId,
		},
	};
}

export {DEFAULT_CAPTION_PRESET_ID};

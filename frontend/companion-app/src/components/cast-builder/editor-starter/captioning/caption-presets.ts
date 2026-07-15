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
 * Per-block override is honored by `applyPresetToAll` — if an item has
 * `metadata.preset_override === true` it is skipped.
 */
import type {CaptionsItem} from '../items/captions/captions-item-type';
import {
	CAPTION_PRESETS as RAW_CAPTION_PRESETS,
	CAPTION_PRESET_IDS,
	DEFAULT_CAPTION_PRESET_ID,
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
 */
export function applyPresetToCaptionItem(
	item: CaptionsItem,
	presetId: CaptionPresetId,
): CaptionsItem {
	const preset = CAPTION_PRESETS[presetId];
	if (!preset) return item;
	return {
		...item,
		...preset.patch,
		metadata: {
			...item.metadata,
			caption_preset: presetId,
		},
	};
}

/**
 * Apply a preset to every caption item in the items map. Items whose
 * metadata.preset_override === true are skipped — they keep their
 * locked-in style.
 */
export function applyPresetToAll(
	presetId: CaptionPresetId,
	items: Record<string, any>,
): Record<string, any> {
	const updated = {...items};
	for (const [id, item] of Object.entries(updated)) {
		if (item?.type !== 'captions') continue;
		if (item?.metadata?.preset_override) continue;
		updated[id] = applyPresetToCaptionItem(item, presetId);
	}
	return updated;
}

export {DEFAULT_CAPTION_PRESET_ID};

/**
 * CaptionStyleBar (Caption UX Fix 2).
 *
 * Persistent horizontally-scrollable strip above the timeline. When the
 * cast has any caption items it shows 15 preset chips (live-styled
 * samples). Clicking a chip applies that preset to every caption item
 * EXCEPT items where metadata.preset_override === true (blocks the user
 * locked from the inspector).
 *
 * The active preset id is stored on each item's metadata.caption_preset
 * so the FFmpeg renderer (Fix 3 Phase A) reads the same values for the
 * burnt-in MP4.
 */
import React, {useMemo, useState, useCallback} from 'react';
import {useAllItems, useWriteContext} from '../utils/use-context';
import {clsx} from '../utils/clsx';
import {
	CAPTION_PRESET_LIST,
	type CaptionPreset,
	type CaptionPresetId,
	applyPresetToCaptionItem,
} from './caption-presets';
import {DEFAULT_CAPTION_PRESET_ID} from '@/lib/captionPresets';
import type {CaptionsItem} from '../items/captions/captions-item-type';

const ChipSample: React.FC<{preset: CaptionPreset}> = ({preset}) => {
	const raw = preset.raw;
	return (
		<span
			style={{
				fontFamily: raw.fontFamily,
				fontWeight: raw.fontWeight,
				color: raw.color === 'transparent' ? '#FFFFFF' : raw.color,
				WebkitTextStroke: raw.strokeWidth
					? `${Math.min(raw.strokeWidth, 2)}px ${
							raw.strokeColor === 'transparent' ? '#000' : raw.strokeColor
						}`
					: undefined,
				paintOrder: 'stroke',
				textShadow: raw.shadow,
				textTransform: raw.textTransform,
				display: 'inline-block',
				lineHeight: 1,
				whiteSpace: 'nowrap',
			}}
		>
			{preset.sampleText}
		</span>
	);
};

export const CaptionStyleBar: React.FC = () => {
	const {items} = useAllItems();
	const {setState} = useWriteContext();

	const captionItems = useMemo(() => {
		return Object.values(items).filter(
			(it): it is CaptionsItem => it.type === 'captions',
		);
	}, [items]);

	const activePresetId: CaptionPresetId = useMemo(() => {
		const found = captionItems
			.map((it) => it.metadata?.caption_preset as string | undefined)
			.filter((p): p is CaptionPresetId => Boolean(p));
		const allSame = found.length > 0 && found.every((p) => p === found[0]);
		return allSame ? found[0] : DEFAULT_CAPTION_PRESET_ID;
	}, [captionItems]);

	const [selectedPreset, setSelectedPreset] =
		useState<CaptionPresetId>(activePresetId);

	React.useEffect(() => {
		setSelectedPreset(activePresetId);
	}, [activePresetId]);

	const lockedCount = useMemo(
		() =>
			captionItems.filter((it) => it.metadata?.preset_override === true).length,
		[captionItems],
	);

	// Per-preset usage count — each chip shows "(N)" so the user can tell
	// at a glance which preset is in use where. Items with no
	// metadata.caption_preset are bucketed under the default preset.
	const presetCounts = useMemo(() => {
		const counts: Record<string, number> = {};
		for (const it of captionItems) {
			const id =
				(it.metadata?.caption_preset as string | undefined) ??
				DEFAULT_CAPTION_PRESET_ID;
			counts[id] = (counts[id] || 0) + 1;
		}
		return counts;
	}, [captionItems]);

	// Selecting a chip immediately writes the preset id onto every
	// non-overridden caption item — there is no separate "apply" step.
	const handleSelect = useCallback(
		(presetId: CaptionPresetId) => {
			setSelectedPreset(presetId);
			setState({
				update: (state) => {
					const newItems = {...state.undoableState.items};
					let changed = false;
					for (const it of Object.values(newItems)) {
						if (it?.type !== 'captions') continue;
						if (it?.metadata?.preset_override) continue;
						newItems[it.id] = applyPresetToCaptionItem(
							it as CaptionsItem,
							presetId,
							state.undoableState.compositionHeight,
						);
						changed = true;
					}
					if (!changed) return state;
					return {
						...state,
						undoableState: {
							...state.undoableState,
							items: newItems,
						},
					};
				},
				commitToUndoStack: true,
			});
		},
		[setState],
	);

	if (captionItems.length === 0) {
		return null;
	}

	return (
		<div className="flex w-full items-center gap-3 border-b border-editor-starter-border bg-editor-starter-panel px-3 py-2">
			<span className="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-white/50">
				Caption style
			</span>

			<div
				className="flex flex-1 items-center gap-2 overflow-x-auto"
				style={{scrollbarWidth: 'thin'}}
			>
				{CAPTION_PRESET_LIST.map((preset) => {
					const active = preset.id === selectedPreset;
					return (
						<button
							key={preset.id}
							onClick={() => handleSelect(preset.id)}
							title={preset.description}
							className={clsx(
								'group flex h-12 min-w-[110px] shrink-0 flex-col items-center justify-center gap-0.5 rounded border px-2 transition-colors',
								active
									? 'border-editor-starter-accent bg-editor-starter-accent/10 text-white'
									: 'border-white/15 bg-white/5 text-white/70 hover:border-white/30 hover:bg-white/10 hover:text-white',
							)}
						>
							<span className="flex items-center gap-1 text-[9px] uppercase tracking-wider opacity-70">
								{preset.name}
								<span className="text-[8px] tabular-nums text-white/30">
									({presetCounts[preset.id] || 0})
								</span>
							</span>
							<span
								className="flex h-5 items-center text-[12px]"
								style={{lineHeight: 1}}
							>
								<ChipSample preset={preset} />
							</span>
						</button>
					);
				})}
			</div>

			{lockedCount > 0 && (
				<span
					className="shrink-0 text-[10px] text-white/40"
					title={`${lockedCount} block(s) locked from the inspector — picking a style here skips them`}
				>
					{lockedCount} locked
				</span>
			)}
		</div>
	);
};

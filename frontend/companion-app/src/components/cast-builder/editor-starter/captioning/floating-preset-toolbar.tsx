/**
 * FloatingPresetToolbar — appears when ≥2 caption items are selected
 * (typically via marquee). Shows a row of preset chips; clicking one
 * applies that preset to every selected caption item via the standard
 * `applyPresetToCaptionItem` patch (preserves timing + assetId, updates
 * metadata.caption_preset).
 *
 * Why above the timeline (and not above the canvas selection): caption
 * items live on a timeline track and have no canvas bounding-box of
 * their own. Anchoring to the timeline is the only stable ground truth.
 */
import React, {useCallback, useMemo} from 'react';
import {clsx} from '../utils/clsx';
import {
	useAllItems,
	useSelectedItems,
	useWriteContext,
} from '../utils/use-context';
import {
	CAPTION_PRESET_LIST,
	type CaptionPresetId,
	applyPresetToCaptionItem,
} from './caption-presets';
import type {CaptionsItem} from '../items/captions/captions-item-type';

export const FloatingPresetToolbar: React.FC = () => {
	const {selectedItems} = useSelectedItems();
	const {items} = useAllItems();
	const {setState} = useWriteContext();

	const selectedCaptions = useMemo(() => {
		return selectedItems
			.map((id) => items[id])
			.filter((it): it is CaptionsItem => Boolean(it) && it.type === 'captions');
	}, [selectedItems, items]);

	const allCaptions =
		selectedItems.length >= 2 &&
		selectedCaptions.length === selectedItems.length;

	const handleApply = useCallback(
		(presetId: CaptionPresetId) => {
			if (!allCaptions) return;
			const ids = new Set(selectedCaptions.map((it) => it.id));
			setState({
				update: (state) => {
					const newItems = {...state.undoableState.items};
					for (const id of ids) {
						const it = newItems[id];
						if (!it || it.type !== 'captions') continue;
						newItems[id] = applyPresetToCaptionItem(
							it as CaptionsItem,
							presetId,
						);
					}
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
		[allCaptions, selectedCaptions, setState],
	);

	if (!allCaptions) return null;

	return (
		<div
			className={clsx(
				'pointer-events-auto absolute left-1/2 top-2 z-30 -translate-x-1/2',
				'flex items-center gap-1.5 rounded-lg border border-white/10',
				'bg-black/85 px-3 py-1.5 text-[10px] backdrop-blur',
			)}
			role="toolbar"
			aria-label="Apply caption preset to selection"
		>
			<span className="shrink-0 text-white/50">
				{selectedCaptions.length} captions selected
			</span>
			<div className="mx-1 h-3 w-px bg-white/10" />
			<div className="flex items-center gap-1 overflow-x-auto">
				{CAPTION_PRESET_LIST.map((preset) => (
					<button
						key={preset.id}
						type="button"
						onClick={() => handleApply(preset.id)}
						className="rounded bg-white/10 px-2 py-0.5 text-white/70 transition-colors hover:bg-editor-starter-accent/25 hover:text-white"
						title={preset.description}
					>
						{preset.name}
					</button>
				))}
			</div>
		</div>
	);
};

import React, {memo, useCallback, useMemo} from 'react';
import {Slider} from '../../slider';
import {
	updateCropBottom,
	updateCropLeft,
	updateCropRight,
	updateCropTop,
} from '../../state/actions/item-cropping';
import {getCropFromItem} from '../../utils/get-crop-from-item';
import {useItem, useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';

const CropControlsUnmemoized: React.FC<{
	itemId: string;
}> = ({itemId}) => {
	const {setState} = useWriteContext();
	const item = useItem(itemId);
	const crop = useMemo(() => getCropFromItem(item), [item]);

	if (!crop) {
		throw new Error('Crop controls not supported for this item type');
	}

	const setCropLeft = useCallback(
		(newValue: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return updateCropLeft({state, itemId, cropLeft: newValue});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const setCropTop = useCallback(
		(newValue: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return updateCropTop({state, itemId, cropTop: newValue});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const setCropRight = useCallback(
		(newValue: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return updateCropRight({state, itemId, cropRight: newValue});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const setCropBottom = useCallback(
		(newValue: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return updateCropBottom({state, itemId, cropBottom: newValue});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const handleLeftChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			const newValue = value / 100;
			setCropLeft(newValue, commitToUndoStack);
		},
		[setCropLeft],
	);

	const handleTopChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			const newValue = value / 100;
			setCropTop(newValue, commitToUndoStack);
		},
		[setCropTop],
	);

	const handleRightChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			const newValue = value / 100;
			setCropRight(newValue, commitToUndoStack);
		},
		[setCropRight],
	);

	const handleBottomChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			const newValue = value / 100;
			setCropBottom(newValue, commitToUndoStack);
		},
		[setCropBottom],
	);

	const leftPercent = Math.max(0, Math.round(crop.cropLeft * 100));
	const topPercent = Math.max(0, Math.round(crop.cropTop * 100));
	const rightPercent = Math.max(0, Math.round(crop.cropRight * 100));
	const bottomPercent = Math.max(0, Math.round(crop.cropBottom * 100));

	return (
		<div className="space-y-2">
			<div>
				<InspectorSubLabel>Left</InspectorSubLabel>
				<div className="flex w-full items-center gap-3">
					<Slider
						value={leftPercent}
						onValueChange={handleLeftChange}
						min={0}
						max={100}
						step={1}
						className="flex-1"
						title={`Left: ${leftPercent}%`}
					/>
					<div className="min-w-[40px] text-right text-xs text-white/75">
						{Math.max(0, Math.round(crop.cropLeft * item.width))}px
					</div>
				</div>
			</div>

			<div>
				<InspectorSubLabel>Top</InspectorSubLabel>
				<div className="flex w-full items-center gap-3">
					<Slider
						value={topPercent}
						onValueChange={handleTopChange}
						min={0}
						max={100}
						step={1}
						className="flex-1"
						title={`Top: ${topPercent}%`}
					/>
					<div className="min-w-[40px] text-right text-xs text-white/75">
						{Math.max(0, Math.round(crop.cropTop * item.height))}px
					</div>
				</div>
			</div>

			<div>
				<InspectorSubLabel>Right</InspectorSubLabel>
				<div className="flex w-full items-center gap-3">
					<Slider
						value={rightPercent}
						onValueChange={handleRightChange}
						min={0}
						max={100}
						step={1}
						className="flex-1"
						title={`Right: ${Math.round(crop.cropRight * item.width)}px`}
					/>
					<div className="min-w-[40px] text-right text-xs text-white/75">
						{Math.max(0, Math.round(crop.cropRight * item.width))}px
					</div>
				</div>
			</div>

			<div>
				<InspectorSubLabel>Bottom</InspectorSubLabel>
				<div className="flex w-full items-center gap-3">
					<Slider
						value={bottomPercent}
						onValueChange={handleBottomChange}
						min={0}
						max={100}
						step={1}
						className="flex-1"
						title={`Bottom: ${Math.round(crop.cropBottom * item.height)}px`}
					/>
					<div className="min-w-[40px] text-right text-xs text-white/75">
						{Math.max(0, Math.round(crop.cropBottom * item.height))}px
					</div>
				</div>
			</div>
		</div>
	);
};

export const CropControls = memo(CropControlsUnmemoized);

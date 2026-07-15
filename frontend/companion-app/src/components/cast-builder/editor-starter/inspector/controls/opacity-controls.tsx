import React, {memo, useCallback} from 'react';
import {EditorStarterItem} from '../../items/item-type';
import {Slider} from '../../slider';
import {changeItem} from '../../state/actions/change-item';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';

const OpacityControlsUnmemoized: React.FC<{
	opacity: number;
	itemId: string;
}> = ({opacity, itemId}) => {
	const {setState} = useWriteContext();

	const setOpacity = useCallback(
		(newOpacity: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const prev = i as EditorStarterItem;
						if (prev.opacity === newOpacity) {
							return prev;
						}
						return {
							...prev,
							opacity: newOpacity,
						};
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const handleSliderChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			const newOpacity = value / 100;
			setOpacity(newOpacity, commitToUndoStack);
		},
		[setOpacity],
	);

	const opacityPercent = Math.round(opacity * 100);

	return (
		<div>
			<InspectorSubLabel>Opacity</InspectorSubLabel>
			<div className="flex w-full items-center gap-3">
				<Slider
					value={opacityPercent}
					onValueChange={handleSliderChange}
					min={0}
					max={100}
					step={1}
					className="flex-1"
					title={`Opacity: ${opacityPercent}%`}
				/>
				<div className="min-w-[40px] text-right text-xs text-white/75">
					{opacityPercent}%
				</div>
			</div>
		</div>
	);
};

export const OpacityControls = memo(OpacityControlsUnmemoized);

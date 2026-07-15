import React, {memo} from 'react';
import {RotateIcon} from '../../icons/rotate';
import {changeItem} from '../../state/actions/change-item';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

export const StrokeWidthControlsUnmemoized: React.FC<{
	strokeWidth: number;
	itemId: string;
}> = ({strokeWidth, itemId}) => {
	const {setState} = useWriteContext();

	const onStrokeWidth: NumberControlUpdateHandler = React.useCallback(
		({num, commitToUndoStack}) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						if (i.type !== 'text' && i.type !== 'captions') {
							throw new Error('Item is not a text or caption');
						}
						return {
							...i,
							strokeWidth: num,
						};
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	return (
		<div>
			<InspectorSubLabel>Width</InspectorSubLabel>
			<NumberControl
				accessibilityLabel="Stroke width"
				label={
					<div className="flex items-center gap-1">
						<RotateIcon className="size-3" />
					</div>
				}
				value={strokeWidth}
				setValue={onStrokeWidth}
				min={0}
				max={100}
				step={1}
			/>
		</div>
	);
};

export const StrokeWidthControls = memo(StrokeWidthControlsUnmemoized);

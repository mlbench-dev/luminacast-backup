import React, {memo, useMemo} from 'react';
import {changeItem} from '../../state/actions/change-item';
import {
	setPositionLeft,
	setPositionTop,
} from '../../state/actions/set-position';
import {getRectAfterCrop} from '../../utils/get-dimensions-after-crop';
import {useItem, useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {ControlsPadding} from './controls-padding';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

const PositionControlUnmemoized: React.FC<{
	itemId: string;
}> = ({itemId}) => {
	const {setState} = useWriteContext();
	const item = useItem(itemId);

	const rectAfterCrop = useMemo(() => getRectAfterCrop(item), [item]);

	const onLeft: NumberControlUpdateHandler = React.useCallback(
		({num: _leftAfterCrop, commitToUndoStack}) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const currentRectAfterCrop = getRectAfterCrop(i);
						const diff = i.left - currentRectAfterCrop.left;
						const num = _leftAfterCrop + diff;

						return setPositionLeft({item: i, left: num});
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const onTop: NumberControlUpdateHandler = React.useCallback(
		({num: _topAfterCrop, commitToUndoStack}) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const currentRectAfterCrop = getRectAfterCrop(i);
						const diff = i.top - currentRectAfterCrop.top;
						const num = _topAfterCrop + diff;

						return setPositionTop({item: i, top: num});
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	return (
		<div>
			<InspectorSubLabel>Position</InspectorSubLabel>
			<div className="flex gap-2">
				<div className="flex-1">
					<NumberControl
						label="X"
						setValue={onLeft}
						value={Math.floor(rectAfterCrop.left)}
						min={null}
						max={null}
						step={1}
						accessibilityLabel="X"
					/>
				</div>
				<div className="flex-1">
					<NumberControl
						label="Y"
						setValue={onTop}
						value={Math.floor(rectAfterCrop.top)}
						min={null}
						max={null}
						step={1}
						accessibilityLabel="Y"
					/>
				</div>
				<ControlsPadding />
			</div>
		</div>
	);
};

export const PositionControl = memo(PositionControlUnmemoized);

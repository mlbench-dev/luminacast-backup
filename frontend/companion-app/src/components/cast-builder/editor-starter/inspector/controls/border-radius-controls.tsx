import React, {memo} from 'react';
import {RoundnessIcon} from '../../icons/roundness';
import {
	changeBackgroundBorderRadius,
	changeBorderRadius,
} from '../../state/actions/change-border-radius';
import {changeItem} from '../../state/actions/change-item';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

const BorderRadiusControlUnmemoized: React.FC<{
	borderRadius: number;
	borderRadiusType: 'fill' | 'background';
	itemId: string;
}> = ({borderRadius, borderRadiusType, itemId}) => {
	const {setState} = useWriteContext();

	const onRadius: NumberControlUpdateHandler = React.useCallback(
		({num, commitToUndoStack}) => {
			return setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						if (borderRadiusType === 'fill') {
							return changeBorderRadius({item: i, borderRadius: num});
						}
						if (borderRadiusType === 'background') {
							return changeBackgroundBorderRadius({item: i, borderRadius: num});
						}
						throw new Error('Invalid border radius type');
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId, borderRadiusType],
	);

	return (
		<div className="flex-1">
			<InspectorSubLabel>Corner Radius</InspectorSubLabel>
			<NumberControl
				label={
					<div className="flex items-center gap-1">
						<RoundnessIcon />
					</div>
				}
				setValue={onRadius}
				value={borderRadius}
				min={0}
				max={null}
				step={1}
				accessibilityLabel="Corner radius"
			/>
		</div>
	);
};

export const BorderRadiusControl = memo(BorderRadiusControlUnmemoized);

import {memo, useCallback} from 'react';
import {changeItem} from '../../state/actions/change-item';
import {relayoutCaptions} from '../../state/actions/relayout-captions';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

const MaxLinesControlsUnmemoized: React.FC<{
	maxLines: number;
	itemId: string;
}> = ({maxLines, itemId}) => {
	const {setState} = useWriteContext();

	const onMaxLinesChange: NumberControlUpdateHandler = useCallback(
		({num, commitToUndoStack}) => {
			// Ensure maxLines is a valid positive integer
			const validMaxLines = Math.max(1, Math.min(10, Math.round(num)));

			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						if (i.type === 'captions') {
							if (i.maxLines === validMaxLines) {
								return i;
							}

							const newItem = {
								...i,
								maxLines: validMaxLines,
							};

							return relayoutCaptions(newItem);
						}

						throw new Error('Max lines can only be changed for captions items');
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	return (
		<div className="flex-1">
			<InspectorSubLabel>Max Lines</InspectorSubLabel>
			<NumberControl
				label={
					<div className="flex items-center gap-1">
						<span className="text-xs">#</span>
					</div>
				}
				setValue={onMaxLinesChange}
				value={maxLines}
				min={1}
				max={10}
				step={1}
				accessibilityLabel="Max lines"
			/>
		</div>
	);
};

export const MaxLinesControls = memo(MaxLinesControlsUnmemoized);

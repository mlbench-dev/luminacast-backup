import {memo, useCallback} from 'react';
import {TextAlign, TextDirection} from '../../../items/text/text-item-type';
import {changeItem} from '../../../state/actions/change-item';
import {editTextAction} from '../../../state/actions/edit-text';
import {unmarkTextAsEditing} from '../../../state/actions/text-item-editing';
import {clsx} from '../../../utils/clsx';
import {useWriteContext} from '../../../utils/use-context';
import {InspectorSubLabel} from '../../components/inspector-label';
import {TextValueControlsTextarea} from './text-value-controls-textarea';

const TextValueControlsUnmemoized: React.FC<{
	text: string;
	itemId: string;
	direction: TextDirection;
	align: TextAlign;
}> = ({text, itemId, direction, align}) => {
	const {setState} = useWriteContext();

	const onTextChange = useCallback(
		(e: React.ChangeEvent<HTMLTextAreaElement>) => {
			const newText = e.target.value;
			setState({
				update: (state) => {
					const newState = changeItem(state, itemId, (i) => {
						if (i.type !== 'text') {
							throw new Error('Text can only be changed for text items');
						}

						return editTextAction({
							item: i,
							editText: newText,
						});
					});

					return unmarkTextAsEditing(newState);
				},
				commitToUndoStack: true,
			});
		},
		[setState, itemId],
	);
	return (
		<div>
			<InspectorSubLabel>Text</InspectorSubLabel>
			<TextValueControlsTextarea
				type="text"
				value={text}
				onChange={onTextChange}
				dir={direction}
				className={clsx(
					align === 'center' && 'text-center',
					align === 'right' && 'text-right',
				)}
			/>
		</div>
	);
};

export const TextValueControls = memo(TextValueControlsUnmemoized);

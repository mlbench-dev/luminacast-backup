import {useCallback} from 'react';
import {FontSizeIcon} from '../../icons/font-size';
import {DEFAULT_FONT_SIZE} from '../../items/text/create-text-item';
import {changeItem} from '../../state/actions/change-item';
import {editAndRelayoutText} from '../../state/actions/edit-and-relayout-text';
import {relayoutCaptions} from '../../state/actions/relayout-captions';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

export const FontSizeControls: React.FC<{
	fontSize: number;
	itemId: string;
	itemType: 'text' | 'captions';
}> = ({fontSize, itemId, itemType}) => {
	const {setState} = useWriteContext();

	const onFontSizeChange: NumberControlUpdateHandler = useCallback(
		({num, commitToUndoStack}) => {
			const validFontSize = Math.max(
				1,
				Math.min(500, isNaN(num) ? DEFAULT_FONT_SIZE : num),
			);

			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						if (i.type === 'text') {
							return editAndRelayoutText(i, () => {
								if (i.fontSize === validFontSize) {
									return i;
								}

								return {
									...i,
									fontSize: validFontSize,
								};
							});
						}

						if (i.type === 'captions') {
							if (i.fontSize === validFontSize) {
								return i;
							}

							const newItem = {
								...i,
								fontSize: validFontSize,
							};

							return relayoutCaptions(newItem);
						}

						throw new Error(
							'Font size can only be changed for text and captions items',
						);
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	return (
		<div>
			<InspectorSubLabel>
				{itemType === 'text' ? 'Font Size' : 'Max Font Size'}
			</InspectorSubLabel>
			<NumberControl
				label={<FontSizeIcon className="size-3" />}
				setValue={onFontSizeChange}
				value={fontSize}
				min={1}
				max={500}
				step={1}
				accessibilityLabel="Font size"
			/>
		</div>
	);
};

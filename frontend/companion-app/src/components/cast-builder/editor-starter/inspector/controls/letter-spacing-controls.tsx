import {memo, useCallback} from 'react';
import {LetterSpacingIcon} from '../../icons/letter-spacing';
import {changeItem} from '../../state/actions/change-item';
import {editAndRelayoutText} from '../../state/actions/edit-and-relayout-text';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

const LETTER_SPACING_MIN = -10;
const LETTER_SPACING_MAX = 50;
const LETTER_SPACING_STEP = 0.2;

const LetterSpacingControlsUnmemoized: React.FC<{
	letterSpacing: number;
	itemId: string;
}> = ({letterSpacing, itemId}) => {
	const {setState} = useWriteContext();

	const onLetterSpacingChange: NumberControlUpdateHandler = useCallback(
		({num, commitToUndoStack}) => {
			// Ensure letterSpacing is within reasonable bounds
			const validLetterSpacing = Math.max(
				LETTER_SPACING_MIN,
				Math.min(LETTER_SPACING_MAX, num),
			);

			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						if (i.type === 'text') {
							return editAndRelayoutText(i, () => {
								if (i.letterSpacing === validLetterSpacing) {
									return i;
								}

								return {
									...i,
									letterSpacing: validLetterSpacing,
								};
							});
						}

						if (i.type === 'captions') {
							return {
								...i,
								letterSpacing: validLetterSpacing,
							};
						}

						throw new Error(
							'Letter spacing can only be changed for text and captions items',
						);
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	return (
		<div className="flex-1">
			<InspectorSubLabel>Letter Spacing</InspectorSubLabel>
			<NumberControl
				label={
					<div className="flex items-center gap-1">
						<LetterSpacingIcon className="size-3" />
					</div>
				}
				setValue={onLetterSpacingChange}
				value={letterSpacing}
				min={LETTER_SPACING_MIN}
				max={LETTER_SPACING_MAX}
				step={LETTER_SPACING_STEP}
				accessibilityLabel="Letter spacing"
			/>
		</div>
	);
};

export const LetterSpacingControls = memo(LetterSpacingControlsUnmemoized);

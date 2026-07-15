import React, {memo} from 'react';

import {FEATURE_ROTATE_90_DEGREES_BUTTON} from '../../flags';
import {RotateIcon} from '../../icons/rotate';
import {RotateRight} from '../../icons/rotate-right';
import {EditorStarterItem} from '../../items/item-type';
import {CanHaveRotation} from '../../items/shared';
import {changeItem} from '../../state/actions/change-item';
import {useWriteContext} from '../../utils/use-context';
import {InspectorIconButton} from '../components/inspector-icon-button';
import {InspectorSubLabel} from '../components/inspector-label';
import {NumberControl, NumberControlUpdateHandler} from './number-controls';

const RotationControlUnmemoized: React.FC<{
	rotation: number;
	itemId: string;
}> = ({rotation, itemId}) => {
	const {setState} = useWriteContext();

	const onRotation: NumberControlUpdateHandler = React.useCallback(
		({num, commitToUndoStack}) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const updatedItem: CanHaveRotation = {
							...(i as CanHaveRotation),
							rotation: num,
						};
						return updatedItem as EditorStarterItem;
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const onRotateRight = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					if (!('rotation' in i)) {
						return i;
					}

					const updatedItem: CanHaveRotation = {
						...(i as CanHaveRotation),
						rotation: (i.rotation + 90) % 360,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	return (
		<div>
			<InspectorSubLabel>Rotation</InspectorSubLabel>
			<div className="flex flex-row gap-2">
				<NumberControl
					label={
						<div className="flex items-center gap-1">
							<RotateIcon className="size-3" />
						</div>
					}
					setValue={onRotation}
					value={rotation}
					min={null}
					max={null}
					step={1}
					accessibilityLabel="Rotation"
				/>
				{FEATURE_ROTATE_90_DEGREES_BUTTON && (
					<div className="editor-starter-field hover:border-transparent">
						<InspectorIconButton
							className="flex h-full w-8 flex-1 items-center justify-center"
							onClick={onRotateRight}
							aria-label="Rotate Right"
						>
							<RotateRight height={12} width={12}></RotateRight>
						</InspectorIconButton>
					</div>
				)}
			</div>
		</div>
	);
};

export const RotationControl = memo(RotationControlUnmemoized);

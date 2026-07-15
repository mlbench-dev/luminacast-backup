import React, {memo} from 'react';
import {IconButton} from '../../icon-button';
import {AlignBottom} from '../../icons/align-bottom';
import {AlignCenterHorizontal} from '../../icons/align-center-horizontal';
import {AlignCenterVertical} from '../../icons/align-center-vertical';
import {AlignLeft} from '../../icons/align-left';
import {AlignRight} from '../../icons/align-right';
import {AlignTop} from '../../icons/align-top';
import {EditorStarterItem} from '../../items/item-type';
import {BaseItem} from '../../items/shared';
import {changeItem} from '../../state/actions/change-item';
import {getRectAfterCrop} from '../../utils/get-dimensions-after-crop';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {ControlsPadding} from './controls-padding';

const AlignmentControlsUnmemoized: React.FC<{
	itemId: string;
}> = ({itemId}) => {
	const {setState} = useWriteContext();

	const onLeft = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					const rectAfterCrop = getRectAfterCrop(i);
					const fromLeft = rectAfterCrop.left;

					const updatedItem: BaseItem = {
						...(i as BaseItem),
						left: i.left - fromLeft,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	const onTop = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					const rectAfterCrop = getRectAfterCrop(i);
					const fromTop = rectAfterCrop.top;

					const updatedItem: BaseItem = {
						...(i as BaseItem),
						top: i.top - fromTop,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	const onHorizontalCenter = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					const rectAfterCrop = getRectAfterCrop(i);
					const targetLeft = Math.round(
						(state.undoableState.compositionWidth - rectAfterCrop.width) / 2,
					);
					const currentLeft = rectAfterCrop.left;
					const offset = targetLeft - currentLeft;

					const updatedItem: BaseItem = {
						...(i as BaseItem),
						left: i.left + offset,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	const onRight = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					const rectAfterCrop = getRectAfterCrop(i);
					const targetLeft =
						state.undoableState.compositionWidth - rectAfterCrop.width;
					const currentLeft = rectAfterCrop.left;
					const offset = targetLeft - currentLeft;

					const updatedItem: BaseItem = {
						...(i as BaseItem),
						left: i.left + offset,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	const onBottom = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					const rectAfterCrop = getRectAfterCrop(i);
					const targetTop =
						state.undoableState.compositionHeight - rectAfterCrop.height;
					const currentTop = rectAfterCrop.top;
					const offset = targetTop - currentTop;

					const updatedItem: BaseItem = {
						...(i as BaseItem),
						top: i.top + offset,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	const onVerticalCenter = React.useCallback(() => {
		setState({
			update: (state) => {
				return changeItem(state, itemId, (i) => {
					const rectAfterCrop = getRectAfterCrop(i);
					const targetTop = Math.round(
						(state.undoableState.compositionHeight - rectAfterCrop.height) / 2,
					);
					const currentTop = rectAfterCrop.top;
					const offset = targetTop - currentTop;

					const updatedItem: BaseItem = {
						...(i as BaseItem),
						top: i.top + offset,
					};
					return updatedItem as EditorStarterItem;
				});
			},
			commitToUndoStack: true,
		});
	}, [setState, itemId]);

	return (
		<div>
			<InspectorSubLabel>Alignment</InspectorSubLabel>
			<div className="flex flex-row gap-2">
				<div className="editor-starter-field flex flex-1 flex-row hover:border-transparent">
					<IconButton
						className="flex flex-1 items-center justify-center"
						onClick={onLeft}
						aria-label="Align Left"
					>
						<AlignLeft height={20} width={20}></AlignLeft>
					</IconButton>
					<IconButton
						className="flex flex-1 items-center justify-center"
						onClick={onHorizontalCenter}
						aria-label="Align Center"
					>
						<AlignCenterHorizontal
							height={20}
							width={20}
						></AlignCenterHorizontal>
					</IconButton>
					<IconButton
						className="flex flex-1 items-center justify-center"
						onClick={onRight}
						aria-label="Align Right"
					>
						<AlignRight height={20} width={20}></AlignRight>
					</IconButton>
				</div>
				<div className="editor-starter-field flex flex-1 flex-row hover:border-transparent">
					<IconButton
						className="flex flex-1 items-center justify-center"
						onClick={onTop}
						aria-label="Align Top"
					>
						<AlignTop height={20} width={20}></AlignTop>
					</IconButton>
					<IconButton
						className="flex flex-1 items-center justify-center"
						onClick={onVerticalCenter}
						aria-label="Align Center Vertically"
					>
						<AlignCenterVertical height={20} width={20}></AlignCenterVertical>
					</IconButton>
					<IconButton
						className="flex flex-1 items-center justify-center"
						onClick={onBottom}
						aria-label="Align Bottom"
					>
						<AlignBottom height={20} width={20}></AlignBottom>
					</IconButton>
				</div>
				<ControlsPadding />
			</div>
		</div>
	);
};

export const AlignmentControls = memo(AlignmentControlsUnmemoized);

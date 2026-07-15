import React, {useCallback, useContext, useMemo} from 'react';
import {retryAssetUpload} from '../assets/retry-upload';
import {getCanvasSnapTargets} from '../canvas/snap/canvas-snap-targets';
import {findCanvasSnap} from '../canvas/snap/find-canvas-snap';
import {getSelectionBounds} from '../canvas/snap/get-selection-bounds';
import {
	ItemSelectedForCropContext,
	TextItemEditingContext,
	TextItemHoverPreviewContext,
} from '../context-provider';
import {
	FEATURE_CANVAS_SNAPPING,
	FEATURE_CROPPING,
	FEATURE_DOUBLE_CLICK_TO_CROP,
	FEATURE_SHIFT_AXIS_LOCK,
} from '../flags';
import {EditorStarterItem} from '../items/item-type';
import {overrideItemWithHoverPreview} from '../items/override-item-with-hover-preview';
import {
	applyCanvasSnapPoints,
	clearCanvasSnapPoints,
} from '../state/actions/apply-canvas-snap-point';
import {changeItem} from '../state/actions/change-item';
import {selectItemForCrop} from '../state/actions/item-cropping';
import {setSelectedItems} from '../state/actions/set-selected-items';
import {markTextAsEditing} from '../state/actions/text-item-editing';
import {ItemContextMenuTrigger} from '../timeline/timeline-item/timeline-item-context-menu-trigger';
import {
	canAssetRetryUpload,
	isAssetError,
	isAssetUploading,
} from '../utils/asset-status-helpers';
import {useCanvasTransformationScale} from '../utils/canvas-transformation-context';
import {getCanCrop, getCropFromItem} from '../utils/get-crop-from-item';
import {getRectAfterCrop} from '../utils/get-dimensions-after-crop';
import {isLeftClick} from '../utils/is-left-click';
import {
	useAssetIfApplicable,
	useAssetStatus,
	useCurrentStateAsRef,
	useDimensions,
	useSelectedItems,
	useSnappingEnabled,
	useWriteContext,
} from '../utils/use-context';
import {CropHandle} from './crop-handle';
import {ResizeHandle} from './resize-handle';
import {SelectionDimensions} from './selection-dimensions';
import {SelectionError} from './selection-upload-error';
import {SelectionUploadProgress} from './selection-upload-progress';

const AXIS_LOCK_THRESHOLD = 10; // Minimum movement to determine axis

const SelectionOutlineUnmemoized: React.FC<{
	item: EditorStarterItem;
}> = ({item: itemWithoutHoverPreview}) => {
	const scale = useCanvasTransformationScale();
	const textItemHoverPreview = useContext(TextItemHoverPreviewContext);
	const {compositionWidth, compositionHeight} = useDimensions();
	const snappingEnabled = useSnappingEnabled();
	const item = useMemo(
		() =>
			overrideItemWithHoverPreview({
				item: itemWithoutHoverPreview,
				hoverPreview: textItemHoverPreview,
			}),
		[itemWithoutHoverPreview, textItemHoverPreview],
	);

	const rectAfterCrop = getRectAfterCrop(item);

	const {selectedItems} = useSelectedItems();
	const {setState} = useWriteContext();
	const {assetStatus} = useAssetStatus();
	const stateAsRef = useCurrentStateAsRef();
	const textItemEditing = useContext(TextItemEditingContext);
	const itemSelectedForCrop = useContext(ItemSelectedForCropContext);

	const [hovered, setHovered] = React.useState(false);

	const onMouseEnter = useCallback(() => {
		setHovered(true);
	}, []);

	const onMouseLeave = useCallback(() => {
		setHovered(false);
	}, []);

	const selected = selectedItems.includes(item.id);

	const itemIsBeingTextEdited =
		item.type === 'text' && item.id === textItemEditing;
	const itemIsBeingCropped = item.id === itemSelectedForCrop;

	const border = itemIsBeingCropped ? 1 : 2;
	const scaledBorder = Math.ceil(border / scale);

	const style: React.CSSProperties = useMemo(() => {
		const hasRotation = 'rotation' in item;
		const color = itemIsBeingCropped
			? 'gray'
			: 'var(--color-editor-starter-accent)';
		const cursor: React.CSSProperties['cursor'] = itemIsBeingCropped
			? 'move'
			: 'default';

		return {
			cursor,
			width: rectAfterCrop.width,
			height: rectAfterCrop.height,
			left: rectAfterCrop.left,
			top: rectAfterCrop.top,
			position: 'absolute',
			outline:
				(hovered && !textItemEditing) || selected
					? `${scaledBorder}px solid ${color}`
					: undefined,
			userSelect: 'none',
			touchAction: 'none',
			transform: hasRotation ? `rotate(${item.rotation}deg)` : undefined,
			pointerEvents:
				item.type === 'text' && item.id === textItemEditing ? 'none' : 'auto',
		};
	}, [
		hovered,
		item,
		rectAfterCrop.height,
		rectAfterCrop.left,
		rectAfterCrop.top,
		rectAfterCrop.width,
		scaledBorder,
		selected,
		textItemEditing,
		itemIsBeingCropped,
	]);

	const startDragging = useCallback(
		(e: PointerEvent | React.MouseEvent, selectedItemIds: string[]) => {
			const initialX = e.clientX;
			const initialY = e.clientY;

			let offsetX = 0;
			let offsetY = 0;

			const items = stateAsRef.current.undoableState.items;

			const originalLeft = selectedItemIds.map((id) => items[id].left);
			const originalTop = selectedItemIds.map((id) => items[id].top);
			const originalWidth = selectedItemIds.map((id) => items[id].width);
			const originalHeight = selectedItemIds.map((id) => items[id].height);

			const originalCrop = selectedItemIds.map((id) =>
				getCropFromItem(items[id]),
			);

			// Get snap targets once at the start of drag
			const snapTargets = getCanvasSnapTargets(
				compositionWidth,
				compositionHeight,
			);

			let didMove = false;
			const multiSelect = e.metaKey || e.shiftKey;
			let shiftPressed = e.shiftKey;
			let ctrlPressed = e.ctrlKey;

			const reposition = () => {
				let axisLocked: 'horizontal' | 'vertical' | null = null;

				if (FEATURE_SHIFT_AXIS_LOCK && shiftPressed) {
					const totalMovementX = Math.abs(offsetX);
					const totalMovementY = Math.abs(offsetY);

					if (
						totalMovementX > AXIS_LOCK_THRESHOLD ||
						totalMovementY > AXIS_LOCK_THRESHOLD
					) {
						axisLocked =
							totalMovementX > totalMovementY ? 'horizontal' : 'vertical';
					}
				}

				// Calculate proposed positions for all selected items
				const proposedItems = selectedItemIds.map((id, idx) => {
					const currentItem = stateAsRef.current.undoableState.items[id];
					return {
						...currentItem,
						left:
							axisLocked === 'vertical'
								? originalLeft[idx]
								: originalLeft[idx] + offsetX,
						top:
							axisLocked === 'horizontal'
								? originalTop[idx]
								: originalTop[idx] + offsetY,
					};
				});

				// Calculate snap offsets using selection bounds
				let snapOffsetX = 0;
				let snapOffsetY = 0;
				const shouldSnap =
					FEATURE_CANVAS_SNAPPING &&
					snappingEnabled &&
					!ctrlPressed &&
					!itemIsBeingCropped;

				if (shouldSnap) {
					const selectionBounds = getSelectionBounds(proposedItems);
					if (selectionBounds) {
						const snapResult = findCanvasSnap({
							selectionBounds,
							targets: snapTargets,
							scale,
						});

						if (axisLocked !== 'vertical' && snapResult.snapOffsetX !== null) {
							snapOffsetX = snapResult.snapOffsetX;
						}
						if (
							axisLocked !== 'horizontal' &&
							snapResult.snapOffsetY !== null
						) {
							snapOffsetY = snapResult.snapOffsetY;
						}

						// Update active snap points in state
						setState({
							update: (state) =>
								applyCanvasSnapPoints({
									state,
									snapPoints: snapResult.activeSnapPoints,
								}),
							commitToUndoStack: false,
						});
					}
				} else {
					// Clear snap points when not snapping
					setState({
						update: (state) => clearCanvasSnapPoints(state),
						commitToUndoStack: false,
					});
				}

				for (let idx = 0; idx < selectedItemIds.length; idx++) {
					const itemId = selectedItemIds[idx];
					setState({
						update: (state) => {
							return changeItem(state, itemId, (i) => {
								const updatedItem: EditorStarterItem = {
									...(i as EditorStarterItem),
									left:
										axisLocked === 'vertical'
											? originalLeft[idx]
											: Math.round(originalLeft[idx] + offsetX + snapOffsetX),
									top:
										axisLocked === 'horizontal'
											? originalTop[idx]
											: Math.round(originalTop[idx] + offsetY + snapOffsetY),
								};

								const shiftedX = updatedItem.left - originalLeft[idx];
								const shiftedY = updatedItem.top - originalTop[idx];

								if (itemIsBeingCropped) {
									if (!getCanCrop(updatedItem)) {
										throw new Error('Item cannot be cropped');
									}

									updatedItem.cropLeft =
										originalCrop[idx]!.cropLeft - shiftedX / originalWidth[idx];
									updatedItem.cropTop =
										originalCrop[idx]!.cropTop - shiftedY / originalHeight[idx];
									updatedItem.cropRight =
										originalCrop[idx]!.cropRight +
										shiftedX / originalWidth[idx];
									updatedItem.cropBottom =
										originalCrop[idx]!.cropBottom +
										shiftedY / originalHeight[idx];
								}

								return updatedItem as EditorStarterItem;
							});
						},
						commitToUndoStack: false,
					});
				}
			};

			const onPointerMove = (pointerMoveEvent: PointerEvent) => {
				offsetX = (pointerMoveEvent.clientX - initialX) / scale;
				offsetY = (pointerMoveEvent.clientY - initialY) / scale;

				shiftPressed = pointerMoveEvent.shiftKey;
				ctrlPressed = pointerMoveEvent.ctrlKey;
				didMove = true;
				reposition();
			};

			const onPointerUp = () => {
				// Clear snap points on drag end
				setState({
					update: (state) => {
						const clearedState = clearCanvasSnapPoints(state);
						return setSelectedItems(
							clearedState,
							!didMove && !multiSelect ? [item.id] : clearedState.selectedItems,
						);
					},
					commitToUndoStack: true,
				});

				cleanup();
			};

			const onKeyDown = (evt: KeyboardEvent) => {
				if (evt.key === 'Shift') {
					shiftPressed = true;
					reposition();
				}
				if (evt.key === 'Control') {
					ctrlPressed = true;
					reposition();
				}
			};

			const onKeyUp = (evt: KeyboardEvent) => {
				if (evt.key === 'Shift') {
					shiftPressed = false;
					reposition();
				}
				if (evt.key === 'Control') {
					ctrlPressed = false;
					reposition();
				}
			};

			const cleanup = () => {
				window.removeEventListener('pointermove', onPointerMove);
				window.removeEventListener('keydown', onKeyDown);
				window.removeEventListener('keyup', onKeyUp);
				window.removeEventListener('pointerup', onPointerUp);
			};

			window.addEventListener('pointermove', onPointerMove, {passive: true});
			window.addEventListener('keydown', onKeyDown, {passive: true});
			window.addEventListener('keyup', onKeyUp, {passive: true});
			window.addEventListener('pointerup', onPointerUp, {
				once: true,
			});
		},
		[
			item,
			scale,
			setState,
			stateAsRef,
			itemIsBeingCropped,
			compositionWidth,
			compositionHeight,
			snappingEnabled,
		],
	);

	const onPointerDown = useCallback(
		(e: React.MouseEvent) => {
			if (!isLeftClick(e)) {
				return;
			}
			e.stopPropagation();

			// Don't allow dragging when text is being edited
			if (item.type === 'text' && item.id === textItemEditing) {
				return;
			}

			const multiSelect = e.metaKey || e.shiftKey;
			const updatedSelectedItems = (
				prev: string[],
			): {allowDrag: boolean; newSelectedItems: string[]} => {
				const isSelected = prev.includes(item.id);
				// If pressing shift and cmd, and was already selected, unselect them item
				if (isSelected && multiSelect) {
					return {
						allowDrag: false,
						newSelectedItems: prev.filter((id) => id !== item.id),
					};
				}

				// already selected, allow drag
				if (isSelected) {
					return {allowDrag: true, newSelectedItems: prev};
				}

				if (multiSelect) {
					return {allowDrag: true, newSelectedItems: [...prev, item.id]};
				}

				return {allowDrag: true, newSelectedItems: [item.id]};
			};

			setState({
				update: (state) => {
					const newSelectedItems = updatedSelectedItems(
						state.selectedItems,
					).newSelectedItems;

					return setSelectedItems(state, newSelectedItems);
				},
				commitToUndoStack: true,
			});

			const {newSelectedItems, allowDrag} = updatedSelectedItems(selectedItems);

			// Only allow dragging, if the item was not unselected
			if (allowDrag) {
				startDragging(e, newSelectedItems);
			}
		},
		[item, selectedItems, setState, startDragging, textItemEditing],
	);

	const onDoubleClick = useCallback(
		(e: React.MouseEvent) => {
			if (item.type === 'text' && selected) {
				setState({
					update: (state) => {
						return markTextAsEditing({state, itemId: item.id});
					},
					commitToUndoStack: true,
				});
				e.stopPropagation();
			}

			if (
				FEATURE_CROPPING &&
				FEATURE_DOUBLE_CLICK_TO_CROP &&
				getCanCrop(item) &&
				selected
			) {
				setState({
					update: (state) => {
						return selectItemForCrop({state, itemId: item.id});
					},
					commitToUndoStack: true,
				});
			}
		},
		[item, selected, setState],
	);

	const asset = useAssetIfApplicable(item);
	const currentAssetStatus = asset ? assetStatus[asset.id] : null;

	const uploadProgress = isAssetUploading(currentAssetStatus)
		? currentAssetStatus.progress
		: null;

	const uploadError = useMemo(() => {
		if (isAssetError(currentAssetStatus)) {
			return currentAssetStatus.error;
		}
		return null;
	}, [currentAssetStatus]);

	const canRetry = canAssetRetryUpload(currentAssetStatus);

	const handleRetry = useCallback(() => {
		if (asset) {
			retryAssetUpload({asset, setState});
		}
	}, [asset, setState]);

	return (
		<ItemContextMenuTrigger item={item}>
			<div
				onPointerDown={onPointerDown}
				onPointerEnter={onMouseEnter}
				onPointerLeave={onMouseLeave}
				onDoubleClick={onDoubleClick}
				style={style}
				data-id={item.id}
			>
				{uploadProgress ? (
					<SelectionUploadProgress uploadProgress={uploadProgress} />
				) : null}
				{uploadError ? (
					<SelectionError
						uploadError={uploadError}
						onRetry={canRetry ? handleRetry : undefined}
						canRetry={Boolean(canRetry)}
					/>
				) : null}
				{selected &&
				!itemIsBeingTextEdited &&
				!itemIsBeingCropped &&
				selectedItems.length === 1 ? (
					<>
						<ResizeHandle itemId={item.id} type="top-left" />
						<ResizeHandle itemId={item.id} type="top-right" />
						<ResizeHandle itemId={item.id} type="bottom-left" />
						<ResizeHandle itemId={item.id} type="bottom-right" />
						<ResizeHandle itemId={item.id} type="top" />
						<ResizeHandle itemId={item.id} type="right" />
						<ResizeHandle itemId={item.id} type="bottom" />
						<ResizeHandle itemId={item.id} type="left" />
						<SelectionDimensions itemId={item.id} />
					</>
				) : null}
				{itemIsBeingCropped ? (
					<>
						<CropHandle itemId={item.id} type="top-left" />
						<CropHandle itemId={item.id} type="top-right" />
						<CropHandle itemId={item.id} type="bottom-left" />
						<CropHandle itemId={item.id} type="bottom-right" />
						<CropHandle itemId={item.id} type="top" />
						<CropHandle itemId={item.id} type="right" />
						<CropHandle itemId={item.id} type="bottom" />
						<CropHandle itemId={item.id} type="left" />
						<SelectionDimensions itemId={item.id} />
					</>
				) : null}
			</div>
		</ItemContextMenuTrigger>
	);
};

export const SelectionOutline = React.memo(SelectionOutlineUnmemoized);

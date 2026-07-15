import React, {memo, useCallback, useMemo} from 'react';
import {getAssetFromItem} from '../assets/utils';
import {getCanvasSnapTargets} from '../canvas/snap/canvas-snap-targets';
import {findCanvasSnapForResize} from '../canvas/snap/find-canvas-snap';
import {getItemBounds} from '../canvas/snap/get-item-bounds';
import {
	FEATURE_CANVAS_SNAPPING,
	FEATURE_SHIFT_KEY_TO_OVERRIDE_ASPECT_RATIO_LOCK,
} from '../flags';
import {EditorStarterItem} from '../items/item-type';
import {
	applyCanvasSnapPoints,
	clearCanvasSnapPoints,
} from '../state/actions/apply-canvas-snap-point';
import {changeItem} from '../state/actions/change-item';
import {
	getKeepAspectRatio,
	getOriginalAspectRatio,
} from '../utils/aspect-ratio';
import {useCanvasTransformationScale} from '../utils/canvas-transformation-context';
import {getRectAfterCrop} from '../utils/get-dimensions-after-crop';
import {isLeftClick} from '../utils/is-left-click';
import {
	useDimensions,
	useItem,
	useSnappingEnabled,
	useWriteContext,
} from '../utils/use-context';

const REAL_SIZE = 8;

type ResizeType =
	| 'top-left'
	| 'top-right'
	| 'bottom-left'
	| 'bottom-right'
	| 'top'
	| 'right'
	| 'bottom'
	| 'left';

const alignWithCropInMind = ({
	type,
	oldItem,
	newItem,
}: {
	type: ResizeType;
	oldItem: EditorStarterItem;
	newItem: EditorStarterItem;
}): EditorStarterItem => {
	const oldAfterCrop = getRectAfterCrop(oldItem);
	const newAfterCrop = getRectAfterCrop(newItem);

	let leftOffset = 0;
	let topOffset = 0;

	if (type === 'right' || type === 'top-right' || type === 'bottom-right') {
		leftOffset = oldAfterCrop.left - newAfterCrop.left;
	}

	if (type === 'left' || type === 'top-left' || type === 'bottom-left') {
		leftOffset = -(
			newAfterCrop.left +
			newAfterCrop.width -
			(oldAfterCrop.left + oldAfterCrop.width)
		);
	}

	if (type === 'bottom' || type === 'bottom-left' || type === 'bottom-right') {
		topOffset = oldAfterCrop.top - newAfterCrop.top;
	}

	if (type === 'top' || type === 'top-left' || type === 'top-right') {
		topOffset = -(
			newAfterCrop.top +
			newAfterCrop.height -
			(oldAfterCrop.top + oldAfterCrop.height)
		);
	}

	if (leftOffset !== 0 || topOffset !== 0) {
		return {
			...newItem,
			left: newItem.left + leftOffset,
			top: newItem.top + topOffset,
		};
	}

	return newItem;
};

const ResizeHandleUnmemoized: React.FC<{
	type: ResizeType;
	itemId: string;
}> = ({type, itemId}) => {
	const scale = useCanvasTransformationScale();
	const size = Math.round(REAL_SIZE / scale);
	const item = useItem(itemId);
	const {compositionWidth, compositionHeight} = useDimensions();
	const snappingEnabled = useSnappingEnabled();

	const rectAfterCrop = useMemo(() => getRectAfterCrop(item), [item]);

	const {setState} = useWriteContext();
	const sizeStyle: React.CSSProperties = useMemo(() => {
		// Edge handles should span the full edge minus corner space
		if (type === 'top' || type === 'bottom') {
			return {
				height: size,
				width: rectAfterCrop.width - size, // Full width minus corner handle space
			};
		}
		if (type === 'left' || type === 'right') {
			return {
				height: rectAfterCrop.height - size, // Full height minus corner handle space
				width: size,
			};
		}
		// Corner handles keep original size
		return {
			height: size,
			width: size,
		};
	}, [size, type, rectAfterCrop.width, rectAfterCrop.height]);

	const margin = -size / 2 - 1 / scale;

	const styleWithoutBorder: React.CSSProperties = useMemo(() => {
		if (type === 'top-left') {
			return {
				...sizeStyle,
				marginLeft: margin,
				marginTop: margin,
				cursor: 'nwse-resize',
			};
		}
		if (type === 'top-right') {
			return {
				...sizeStyle,
				marginTop: margin,
				marginRight: margin,
				right: 0,
				cursor: 'nesw-resize',
			};
		}
		if (type === 'bottom-left') {
			return {
				...sizeStyle,
				marginBottom: margin,
				marginLeft: margin,
				bottom: 0,
				cursor: 'nesw-resize',
			};
		}
		if (type === 'bottom-right') {
			return {
				...sizeStyle,
				marginBottom: margin,
				marginRight: margin,
				right: 0,
				bottom: 0,
				cursor: 'nwse-resize',
			};
		}
		if (type === 'top') {
			return {
				...sizeStyle,
				marginTop: margin,
				left: size / 2, // Start after left corner handle
				cursor: 'ns-resize',
			};
		}
		if (type === 'bottom') {
			return {
				...sizeStyle,
				marginBottom: margin,
				left: size / 2, // Start after left corner handle
				bottom: 0,
				cursor: 'ns-resize',
			};
		}
		if (type === 'left') {
			return {
				...sizeStyle,
				marginLeft: margin,
				top: size / 2, // Start after top corner handle
				cursor: 'ew-resize',
			};
		}
		if (type === 'right') {
			return {
				...sizeStyle,
				marginRight: margin,
				top: size / 2, // Start after top corner handle
				right: 0,
				cursor: 'ew-resize',
			};
		}

		throw new Error('Unknown type: ' + JSON.stringify(type));
	}, [margin, sizeStyle, type, size]);

	const style: React.CSSProperties = useMemo(() => {
		// Edge handles should be invisible
		const isEdgeHandle =
			type === 'top' ||
			type === 'bottom' ||
			type === 'left' ||
			type === 'right';

		return {
			...styleWithoutBorder,
			// Only show border for corner handles
			border: isEdgeHandle
				? 'none'
				: `${1 / scale}px solid var(--color-editor-starter-accent)`,
		};
	}, [scale, styleWithoutBorder, type]);

	// Determine which edges are being resized based on handle type
	const resizingEdges = useMemo(() => {
		return {
			left: type === 'left' || type === 'top-left' || type === 'bottom-left',
			right:
				type === 'right' || type === 'top-right' || type === 'bottom-right',
			top: type === 'top' || type === 'top-left' || type === 'top-right',
			bottom:
				type === 'bottom' || type === 'bottom-left' || type === 'bottom-right',
		};
	}, [type]);

	const onPointerDown = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			if (!isLeftClick(e)) {
				return;
			}

			const initialX = e.clientX;
			const initialY = e.clientY;

			// Get snap targets once at the start of resize
			const snapTargets = getCanvasSnapTargets(
				compositionWidth,
				compositionHeight,
			);

			let ctrlPressed = e.ctrlKey;

			const onPointerMove = (pointerMoveEvent: PointerEvent) => {
				const offsetX = (pointerMoveEvent.clientX - initialX) / scale;
				const naturalOffsetY = (pointerMoveEvent.clientY - initialY) / scale;
				ctrlPressed = pointerMoveEvent.ctrlKey;

				setState({
					update: (state) => {
						let resultState = changeItem(state, itemId, (i) => {
							const keepAspectRatio = getKeepAspectRatio(i);
							const lockAspectRatio =
								(FEATURE_SHIFT_KEY_TO_OVERRIDE_ASPECT_RATIO_LOCK &&
									pointerMoveEvent.shiftKey) !== keepAspectRatio;
							const asset = getAssetFromItem({
								item: i,
								assets: state.undoableState.assets,
							});

							const aspectRatio = getOriginalAspectRatio({
								item: i,
								asset,
							});

							// Handle edge resizing differently from corner resizing
							let newWidth: number;
							let newHeight: number;
							let newLeft: number;
							let newTop: number;

							if (type === 'top') {
								newHeight = Math.round(item.height - naturalOffsetY);

								if (lockAspectRatio) {
									newWidth = Math.round(newHeight * aspectRatio);
									newLeft = Math.round(item.left - (newWidth - item.width) / 2);
								} else {
									newWidth = item.width;
									newLeft = item.left;
								}

								newTop = Math.round(item.top - (newHeight - item.height));
							} else if (type === 'bottom') {
								newHeight = Math.round(item.height + naturalOffsetY);

								if (lockAspectRatio) {
									newWidth = Math.round(newHeight * aspectRatio);
									newLeft = Math.round(item.left - (newWidth - item.width) / 2);
								} else {
									newWidth = item.width;
									newLeft = item.left;
								}

								newTop = item.top;
							} else if (type === 'left') {
								newWidth = Math.round(item.width - offsetX);

								if (lockAspectRatio) {
									newHeight = Math.round(newWidth / aspectRatio);
									newTop = Math.round(item.top - (newHeight - item.height) / 2);
								} else {
									newHeight = item.height;
									newTop = item.top;
								}

								newLeft = Math.round(item.left - (newWidth - item.width));
							} else if (type === 'right') {
								newWidth = Math.round(item.width + offsetX);

								if (lockAspectRatio) {
									newHeight = Math.round(newWidth / aspectRatio);
									newTop = Math.round(item.top - (newHeight - item.height) / 2);
								} else {
									newHeight = item.height;
									newTop = item.top;
								}

								newLeft = item.left;
							} else {
								// Corner resizing
								const unadjustedOffsetY = lockAspectRatio
									? offsetX / aspectRatio
									: naturalOffsetY;
								const offsetY =
									lockAspectRatio &&
									(type === 'bottom-left' || type === 'top-right')
										? -unadjustedOffsetY
										: unadjustedOffsetY;

								newWidth = Math.round(
									item.width +
										(type === 'bottom-left' || type === 'top-left'
											? -offsetX
											: offsetX),
								);
								newHeight = Math.round(
									item.height +
										(type === 'top-left' || type === 'top-right'
											? -offsetY
											: offsetY),
								);
								newLeft = Math.round(
									item.left +
										(type === 'bottom-left' || type === 'top-left'
											? offsetX
											: 0),
								);
								newTop = Math.round(
									item.top +
										(type === 'top-left' || type === 'top-right' ? offsetY : 0),
								);
							}

							let updatedItem: EditorStarterItem = {
								...i,
								width: Math.max(1, newWidth),
								height: Math.max(1, newHeight),
								left: Math.min(i.left + i.width - 1, newLeft),
								top: Math.min(i.top + i.height - 1, newTop),
								...(i.type === 'text' ? {resizeOnEdit: false} : {}),
							};

							// Apply canvas snapping for resize
							const shouldSnap =
								FEATURE_CANVAS_SNAPPING && snappingEnabled && !ctrlPressed;

							if (shouldSnap) {
								const proposedBounds = getItemBounds(updatedItem);
								const snapResult = findCanvasSnapForResize({
									selectionBounds: proposedBounds,
									targets: snapTargets,
									scale,
									resizingEdges,
								});

								// Apply snap offsets based on which edge is being resized
								if (snapResult.snapOffsetX !== null) {
									if (resizingEdges.left) {
										// Moving left edge: adjust left and width
										updatedItem = {
											...updatedItem,
											left: updatedItem.left + snapResult.snapOffsetX,
											width: updatedItem.width - snapResult.snapOffsetX,
										};
									} else if (resizingEdges.right) {
										// Moving right edge: adjust width only
										updatedItem = {
											...updatedItem,
											width: updatedItem.width + snapResult.snapOffsetX,
										};
									}
								}

								if (snapResult.snapOffsetY !== null) {
									if (resizingEdges.top) {
										// Moving top edge: adjust top and height
										updatedItem = {
											...updatedItem,
											top: updatedItem.top + snapResult.snapOffsetY,
											height: updatedItem.height - snapResult.snapOffsetY,
										};
									} else if (resizingEdges.bottom) {
										// Moving bottom edge: adjust height only
										updatedItem = {
											...updatedItem,
											height: updatedItem.height + snapResult.snapOffsetY,
										};
									}
								}
							}

							const afterCrop = alignWithCropInMind({
								type,
								oldItem: i,
								newItem: updatedItem,
							});
							return afterCrop as EditorStarterItem;
						});

						// Update snap points in a separate state update after changeItem
						const shouldSnap =
							FEATURE_CANVAS_SNAPPING && snappingEnabled && !ctrlPressed;

						if (shouldSnap) {
							const currentItem = resultState.undoableState.items[itemId];
							const currentBounds = getItemBounds(currentItem);
							const snapResult = findCanvasSnapForResize({
								selectionBounds: currentBounds,
								targets: snapTargets,
								scale,
								resizingEdges,
							});
							resultState = applyCanvasSnapPoints({
								state: resultState,
								snapPoints: snapResult.activeSnapPoints,
							});
						} else {
							resultState = clearCanvasSnapPoints(resultState);
						}

						return resultState;
					},
					commitToUndoStack: false,
				});
			};

			const onPointerUp = () => {
				setState({
					update: (state) => {
						return clearCanvasSnapPoints(state);
					},
					// This will also commit the resize to the undo stack
					commitToUndoStack: true,
				});
				window.removeEventListener('pointermove', onPointerMove);
			};

			window.addEventListener('pointermove', onPointerMove, {passive: true});
			window.addEventListener('pointerup', onPointerUp, {
				once: true,
			});
		},
		[
			item.height,
			itemId,
			item.left,
			scale,
			setState,
			item.top,
			type,
			item.width,
			compositionWidth,
			compositionHeight,
			snappingEnabled,
			resizingEdges,
		],
	);

	const isEdgeHandle =
		type === 'top' || type === 'bottom' || type === 'left' || type === 'right';

	return (
		<div
			onPointerDown={onPointerDown}
			style={style}
			className={`absolute ${isEdgeHandle ? '' : 'bg-white'}`}
		></div>
	);
};

export const ResizeHandle = memo(ResizeHandleUnmemoized);

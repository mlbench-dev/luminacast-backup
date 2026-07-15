import {DEFAULT_TIMELINE_SNAPPING_THRESHOLD_PIXELS} from '../../../constants';
import {EditorStarterItem} from '../../../items/item-type';
import {EditorState, TrackType} from '../../../state/types';
import {DragPreviewState, PreviewPosition} from '../../drag-preview-provider';
import type {ItemEdge, SnapPoint} from '../snap-points';
import {findBestSnapForMultipleEdges} from '../snap-points';
import {getTrackOffset} from './get-track-offset';
import {calculateNewItemPositionForMultipleItems} from './multiple-items/calculate-new-item-position-for-multiple-items';
import {
	getMakesSenseToInsertTrackAtBottom,
	getMakesSenseToInsertTrackAtTop,
} from './should-insert-track';
import {calculateNewItemPositionForSingleItem} from './single-items/calculate-new-item-position-for-single-items';

// Calculate frame offset based on horizontal drag distance.
const getFrameOffset = ({
	offsetX,
	timelineWidth,
	visibleFrames,
}: {
	offsetX: number;
	timelineWidth: number;
	visibleFrames: number;
}) => Math.round((offsetX / timelineWidth) * visibleFrames);

export const calculateNewItemPositions = ({
	clickedItemId,
	draggedItems,
	draggedItemIds,
	timelineWidth,
	offsetX,
	offsetY,
	tracks,
	visibleFrames,
	allItems,
	state,
	setSnappedPositions,
	snapPoints,
}: {
	clickedItemId: string;
	draggedItems: Array<PreviewPosition>;
	draggedItemIds: string[];
	timelineWidth: number;
	visibleFrames: number;
	offsetX: number;
	offsetY: number;
	tracks: TrackType[];
	allItems: Record<string, EditorStarterItem>;
	state: EditorState;
	setSnappedPositions:
		| null
		| ((positions: Record<string, number> | null) => void);
	snapPoints: SnapPoint[];
}): DragPreviewState | null => {
	const makesSenseToInsertTrackAtTop = getMakesSenseToInsertTrackAtTop({
		tracks,
		itemsBeingDragged: draggedItemIds,
	});
	const makesSenseToInsertTrackAtBottom = getMakesSenseToInsertTrackAtBottom({
		tracks,
		itemsBeingDragged: draggedItemIds,
	});

	let frameOffset = getFrameOffset({offsetX, timelineWidth, visibleFrames});
	let snapPoint: SnapPoint | null = null;

	// Apply snapping if enabled
	if (state && state.isSnappingEnabled === true && draggedItems.length > 0) {
		// Collect all edges from dragged items (both left and right)
		const itemEdges: ItemEdge[] = [];
		for (const item of draggedItems) {
			itemEdges.push({
				frame: item.from + frameOffset,
				type: 'left',
				itemId: item.id,
			});
			itemEdges.push({
				frame: item.from + item.durationInFrames + frameOffset,
				type: 'right',
				itemId: item.id,
			});
		}

		// Find the best snap from all edges
		const {snapOffset, activeSnapPoint} = findBestSnapForMultipleEdges({
			itemEdges,
			snapPoints,
			pixelThreshold: DEFAULT_TIMELINE_SNAPPING_THRESHOLD_PIXELS,
			timelineWidth,
			visibleFrames,
			isSnappingEnabled: state.isSnappingEnabled,
		});
		snapPoint = activeSnapPoint;

		// Pass snapped positions for each item
		if (setSnappedPositions) {
			if (snapOffset !== null) {
				const snappedPositions: Record<string, number> = {};
				for (const item of draggedItems) {
					snappedPositions[item.id] = item.from + frameOffset + snapOffset;
				}
				setSnappedPositions(snappedPositions);
			} else {
				setSnappedPositions(null);
			}
		}

		// Apply snap offset to frame offset
		if (snapOffset !== null) {
			frameOffset += snapOffset;
		}
	} else {
		// Clear snapped positions when snapping is disabled
		if (setSnappedPositions) {
			setSnappedPositions(null);
		}
	}

	const clickedItem = draggedItems.find((item) => item.id === clickedItemId);
	if (!clickedItem) {
		throw new Error('Reference item not found');
	}

	const isMultiItemDrag = draggedItems.length > 1;

	const dragDirection = offsetY < 0 ? 'up' : offsetY > 0 ? 'down' : 'none';
	// branching to support https://github.com/remotion-dev/editor-starter/issues/368
	// here we use the top or bottom item as the anchor point for the drag for the groups,
	// depending on the drag direction
	const sourceTrackIndex = isMultiItemDrag
		? dragDirection === 'up'
			? Math.max(...draggedItems.map((item) => item.trackIndex)) // bottom-most item for upward drags
			: Math.min(...draggedItems.map((item) => item.trackIndex)) // top-most item for downward drags
		: clickedItem.trackIndex;

	const trackOffsetResult = getTrackOffset({
		tracks,
		offsetY,
		sourceTrackIndex,
		allItems: allItems,
		makesSenseToInsertTrackAtTop,
		makesSenseToInsertTrackAtBottom,
		draggedItems,
	});

	// for single items
	if (draggedItems.length === 1) {
		return calculateNewItemPositionForSingleItem({
			draggedItem: draggedItems[0],
			tracks,
			allItems,
			frameOffset,
			trackOffsetResult,
			snapPoint,
		});
	}

	// For multiple items
	return calculateNewItemPositionForMultipleItems({
		draggedItems,
		draggedItemIds,
		tracks,
		allItems,
		frameOffset,
		trackOffsetResult,
		snapPoint,
	});
};

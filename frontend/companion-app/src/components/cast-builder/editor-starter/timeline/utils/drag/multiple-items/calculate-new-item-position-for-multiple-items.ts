import {EditorStarterItem} from '../../../../items/item-type';
import {TrackType} from '../../../../state/types';
import {
	DragPreviewState,
	PreviewPosition,
} from '../../../drag-preview-provider';
import {SnapPoint} from '../../snap-points';
import {getNewPositionAfterDrag} from '../calculate-new-item';
import {TrackOffsetResult} from '../get-track-offset';
import {TrackInsertions} from '../types';
import {calculateNewPositionsForMoveWithMultipleItems} from './calculate-new-positions-for-move-with-multiple-items';

export const calculateNewItemPositionForMultipleItems = ({
	draggedItems,
	draggedItemIds,
	tracks,
	allItems,
	frameOffset,
	trackOffsetResult,
	snapPoint,
}: {
	draggedItems: Array<PreviewPosition>;
	draggedItemIds: string[];
	tracks: TrackType[];
	allItems: Record<string, EditorStarterItem>;
	frameOffset: number;
	trackOffsetResult: TrackOffsetResult;
	snapPoint: SnapPoint | null;
}): DragPreviewState | null => {
	if (trackOffsetResult.type === 'insert-between') {
		const uniqueTrackPositions = [
			...new Set(draggedItems.map((item) => item.trackIndex)),
		].sort((a, b) => a - b);

		const tentativePositions = draggedItems.map((item) => {
			const newTrackIndex =
				trackOffsetResult.position +
				uniqueTrackPositions.indexOf(item.trackIndex);

			return getNewPositionAfterDrag({
				item,
				frameOffset,
				newTrackIndex,
			});
		});

		const trackInsertions: TrackInsertions = {
			type: 'between',
			trackIndex: trackOffsetResult.position,
			count: uniqueTrackPositions.length,
		};

		return {
			positions: tentativePositions,
			trackInsertions,
			itemsBeingDragged: draggedItemIds,
			snapPoint: snapPoint,
		};
	}

	if (trackOffsetResult.type === 'create-at-top') {
		const uniqueTrackPositions = [
			...new Set(draggedItems.map((item) => item.trackIndex)),
		].sort((a, b) => a - b);

		const tracksNeededCount = uniqueTrackPositions.length;

		const tentativePositions = draggedItems.map((item) => {
			const newTrackIndex = uniqueTrackPositions.indexOf(item.trackIndex);

			return getNewPositionAfterDrag({
				item,
				frameOffset,
				newTrackIndex,
			});
		});

		const trackInsertions: TrackInsertions = {
			type: 'top',
			count: tracksNeededCount,
		};

		return {
			positions: tentativePositions,
			trackInsertions,
			itemsBeingDragged: draggedItemIds,
			snapPoint: snapPoint,
		};
	}

	if (trackOffsetResult.type === 'create-at-bottom') {
		const orderedDraggedItems = draggedItems
			.slice()
			.sort((a, b) => a.trackIndex - b.trackIndex || a.from - b.from);

		// calculate the starting track index to ensure all items go to new tracks
		// the topmost item in the group should start at tracks.length
		const startingTrackIndex = tracks.length;

		const tentativePositions = orderedDraggedItems.map((item, index) => {
			const newTrackIndex = startingTrackIndex + index;
			return getNewPositionAfterDrag({
				item,
				frameOffset,
				newTrackIndex,
			});
		});

		// create as many new tracks as needed (group size)
		const maxNewIndex = Math.max(
			...tentativePositions.map((p) => p.trackIndex),
		);
		const tracksNeededCount = Math.max(0, maxNewIndex - (tracks.length - 1));

		const trackInsertions: TrackInsertions = {
			type: 'bottom',
			count: tracksNeededCount,
		};

		return {
			positions: tentativePositions,
			trackInsertions,
			itemsBeingDragged: draggedItemIds,
			snapPoint: snapPoint,
		};
	}

	if (trackOffsetResult.type === 'move') {
		return calculateNewPositionsForMoveWithMultipleItems({
			draggedItems,
			draggedItemIds,
			tracks,
			allItems,
			frameOffset,
			trackOffsetResult,
			snapPoint,
		});
	}

	throw new Error(
		`Unexpected trackOffsetResult: ${JSON.stringify(trackOffsetResult satisfies never)}`,
	);
};

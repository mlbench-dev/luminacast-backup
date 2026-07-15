import {EditorStarterItem} from '../../../../items/item-type';
import {TrackType} from '../../../../state/types';
import {
	DragPreviewState,
	PreviewPosition,
} from '../../../drag-preview-provider';
import {SnapPoint} from '../../snap-points';
import {getNewPositionAfterDrag} from '../calculate-new-item';
import {calculateSingleItemNewPosition} from '../calculate-single-item-new-position';
import {TrackOffsetResult} from '../get-track-offset';
import {computeTrackInsertionInfoforSingleItem} from './compute-track-insertion-info-for-single-item';

export const calculateNewItemPositionForSingleItem = ({
	draggedItem,
	tracks,
	allItems,
	frameOffset,
	trackOffsetResult,
	snapPoint,
}: {
	draggedItem: PreviewPosition;
	tracks: TrackType[];
	allItems: Record<string, EditorStarterItem>;
	frameOffset: number;
	trackOffsetResult: TrackOffsetResult;
	snapPoint: SnapPoint | null;
}): DragPreviewState | null => {
	// Handle track insertion for single items
	if (trackOffsetResult.type === 'insert-between') {
		return {
			positions: [
				getNewPositionAfterDrag({
					item: draggedItem,
					frameOffset,
					newTrackIndex: trackOffsetResult.position,
				}),
			],
			trackInsertions: {
				type: 'between',
				trackIndex: trackOffsetResult.position,
				count: 1,
			},
			itemsBeingDragged: [draggedItem.id],
			snapPoint,
		};
	}

	// Handle track creation at edges
	if (trackOffsetResult.type === 'create-at-top') {
		return {
			positions: [
				getNewPositionAfterDrag({
					frameOffset,
					item: draggedItem,
					newTrackIndex: 0,
				}),
			],
			trackInsertions: {
				type: 'top',
				count: 1,
			},
			itemsBeingDragged: [draggedItem.id],
			snapPoint,
		};
	}

	if (trackOffsetResult.type === 'create-at-bottom') {
		return {
			positions: [
				getNewPositionAfterDrag({
					frameOffset,
					item: draggedItem,
					newTrackIndex: tracks.length,
				}),
			],
			trackInsertions: {
				type: 'bottom',
				count: 1,
			},
			itemsBeingDragged: [draggedItem.id],
			snapPoint,
		};
	}

	if (trackOffsetResult.type === 'move') {
		const {targetTrack, trackInsertions} =
			computeTrackInsertionInfoforSingleItem({
				allTracks: tracks,
				startTrack: draggedItem.trackIndex,
				rawTrackOffset: trackOffsetResult.trackOffset,
			});

		// When we are NOT creating new tracks, run the usual collision-resolution logic.
		if (!trackInsertions) {
			const resolved = calculateSingleItemNewPosition({
				durationInFrames: draggedItem.durationInFrames,
				initialFrom: draggedItem.from,
				trackIndex: draggedItem.trackIndex,
				tracks,
				itemId: draggedItem.id,
				items: allItems,
				trackOffsetResult,
				frameOffset,
			});

			if (!resolved) {
				return null;
			}

			return {
				positions: [
					{
						id: draggedItem.id,
						trackIndex: resolved.track,
						from: resolved.from,
						durationInFrames: draggedItem.durationInFrames,
					},
				],
				trackInsertions: null,
				itemsBeingDragged: [draggedItem.id],
				snapPoint,
			};
		}

		// If new track(s) will be inserted we can skip collision checking because the fresh track is empty.
		return {
			positions: [
				getNewPositionAfterDrag({
					frameOffset,
					item: draggedItem,
					newTrackIndex: targetTrack,
				}),
			],
			trackInsertions: trackInsertions,
			itemsBeingDragged: [draggedItem.id],
			snapPoint,
		};
	}

	throw new Error(
		`Unexpected trackOffsetResult: ${JSON.stringify(trackOffsetResult satisfies never)}`,
	);
};

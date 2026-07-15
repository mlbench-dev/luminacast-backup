import {EditorStarterItem} from '../../../../items/item-type';
import {TrackType} from '../../../../state/types';
import {
	DragPreviewState,
	PreviewPosition,
} from '../../../drag-preview-provider';
import {SnapPoint} from '../../snap-points';
import {getNewPositionAfterDrag} from '../calculate-new-item';
import {getAlternativeForGroupCollision} from '../collision';
import {MoveTrackOffsetResult} from '../get-track-offset';
import {getTrackInsertionsFromTentativePositions} from './get-track-insertions-from-tentative-positions';
import {hasNoOverlaps} from './has-no-overlaps';

export const calculateNewPositionsForMoveWithMultipleItems = ({
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
	trackOffsetResult: MoveTrackOffsetResult;
	snapPoint: SnapPoint | null;
}): DragPreviewState | null => {
	let tentativePositions = draggedItems.map((item) => {
		const newTrack = trackOffsetResult.trackOffset + item.trackIndex;

		return getNewPositionAfterDrag({
			frameOffset,
			item,
			newTrackIndex: newTrack,
		});
	});

	const isValid = hasNoOverlaps({
		tentativePositions,
		draggedItemIds,
		tracks,
		allItems,
	});

	// if there are overlaps, try to find an alternative position for the group
	if (!isValid) {
		// find a frame position where the group can fit without collisions

		// this creates a "magnetic" effect where dragged groups "stick" to
		// nearby valid positions when they would otherwise collide with
		// existing timeline items
		const alternativeFrame = getAlternativeForGroupCollision({
			tentativePositions,
			draggedItemIds,
			tracks,
			allItems,
		});

		if (!alternativeFrame) {
			return null;
		}

		// calculate how much to shift the group to avoid collisions
		const groupLeftmostFrame = Math.min(
			...tentativePositions.map((p) => p.from),
		);
		const offsetDelta = alternativeFrame - groupLeftmostFrame;
		const adjustedFrameOffset = frameOffset + offsetDelta;

		// reposition entire group using the collision-avoiding offset
		const alternativeTentativePositions = draggedItems.map((item) => {
			const newTrack = trackOffsetResult.trackOffset + item.trackIndex;

			return getNewPositionAfterDrag({
				frameOffset: adjustedFrameOffset,
				item,
				newTrackIndex: newTrack,
			});
		});

		// verify the adjusted positions still have no overlaps
		const isValidAfterAlternativeCalculation = hasNoOverlaps({
			tentativePositions: alternativeTentativePositions,
			draggedItemIds,
			tracks,
			allItems,
		});

		// if overlaps still exist after alternative calculation, move is not possible
		if (!isValidAfterAlternativeCalculation) {
			return null;
		}

		tentativePositions = alternativeTentativePositions;
	}

	const trackInsertions = getTrackInsertionsFromTentativePositions({
		tentativePositions,
		tracks,
	});

	return {
		positions: tentativePositions.map((t) => {
			// Acount for the fact that adding tracks at the beginning of the array will
			// cause indices to shift.
			if (trackInsertions && trackInsertions.type === 'top') {
				return {
					...t,
					trackIndex: t.trackIndex + trackInsertions.count,
				};
			}
			return t;
		}),
		trackInsertions,
		itemsBeingDragged: draggedItemIds,
		snapPoint,
	};
};

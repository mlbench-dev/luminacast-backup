import {EditorStarterItem} from '../../../items/item-type';
import {getTrackHeight, TRACK_DIVIDER_HEIGHT} from '../../../state/items';
import {TrackType} from '../../../state/types';
import {PreviewPosition} from '../../drag-preview-provider';
import {TICKS_HEIGHT} from '../../ticks/constants';

const DIVIDER_DROP_ZONE_EXPANSION = 5;

export type DragDirection = 'up' | 'down' | 'none';

export type MoveTrackOffsetResult = {
	type: 'move';
	direction: DragDirection;
	trackOffset: number;
};

export type TrackOffsetResult =
	| MoveTrackOffsetResult
	| {type: 'insert-between'; position: number; direction: DragDirection}
	| {type: 'create-at-top'; newTrackIndex: number}
	| {type: 'create-at-bottom'; newTrackIndex: number};

const getDragDirection = (offsetY: number): DragDirection => {
	if (offsetY === 0) {
		return 'none';
	}

	return offsetY < 0 ? 'up' : 'down';
};

const isInDividerDropZone = ({
	dragDistance,
	divider,
}: {
	dragDistance: number;
	divider: number;
}): boolean => {
	const dropZoneStart = divider - DIVIDER_DROP_ZONE_EXPANSION;
	const dropZoneEnd = divider + DIVIDER_DROP_ZONE_EXPANSION;
	return dragDistance >= dropZoneStart && dragDistance <= dropZoneEnd;
};

const shouldCreateNewTrack = (remainingDistance: number): boolean => {
	return remainingDistance > TICKS_HEIGHT;
};

const getTopItemTrackIndex = (draggedItems: PreviewPosition[]): number => {
	return Math.min(...draggedItems.map((item) => item.trackIndex));
};

const getBottomItemTrackIndex = (draggedItems: PreviewPosition[]): number => {
	return Math.max(...draggedItems.map((item) => item.trackIndex));
};

const canGroupMoveUp = (draggedItems: PreviewPosition[]): boolean => {
	return getTopItemTrackIndex(draggedItems) > 0;
};

const canGroupMoveDown = (
	draggedItems: PreviewPosition[],
	tracksLength: number,
): boolean => {
	return getBottomItemTrackIndex(draggedItems) < tracksLength - 1;
};

const calculateUpwardMoveLimit = (
	draggedItems: PreviewPosition[],
	trackOffset: number,
): number => {
	const topItemTrackIndex = getTopItemTrackIndex(draggedItems);
	const maxAllowedOffset = -topItemTrackIndex;
	return Math.max(trackOffset, maxAllowedOffset);
};

const calculateDownwardMoveLimit = (
	draggedItems: PreviewPosition[],
	tracksLength: number,
	trackOffset: number,
): number => {
	const bottomItemTrackIndex = getBottomItemTrackIndex(draggedItems);
	const maxAllowedOffset = tracksLength - 1 - bottomItemTrackIndex;
	return Math.min(trackOffset, maxAllowedOffset);
};

const getTrackIterationParams = ({
	direction,
	sourceTrackIndex,
	tracksLength,
}: {
	direction: 'up' | 'down';
	sourceTrackIndex: number;
	tracksLength: number;
}) => {
	if (direction === 'up') {
		return {
			start: sourceTrackIndex,
			end: -1,
			step: -1,
		};
	}
	if (direction === 'down') {
		return {
			start: sourceTrackIndex,
			end: tracksLength,
			step: 1,
		};
	}

	throw new Error(`Unexpected direction: ${direction satisfies never}`);
};

export const getTrackOffset = ({
	tracks,
	offsetY,
	sourceTrackIndex,
	allItems,
	makesSenseToInsertTrackAtTop,
	makesSenseToInsertTrackAtBottom,
	draggedItems,
}: {
	tracks: TrackType[];
	offsetY: number;
	sourceTrackIndex: number;
	allItems: Record<string, EditorStarterItem>;
	makesSenseToInsertTrackAtTop: boolean;
	makesSenseToInsertTrackAtBottom: boolean;
	draggedItems: PreviewPosition[];
}): TrackOffsetResult => {
	const direction = getDragDirection(offsetY);

	if (direction === 'none') {
		return {type: 'move', direction, trackOffset: 0};
	}

	const dragDistance = Math.abs(offsetY);
	let cumulativeHeight = 0;
	let trackOffset = 0;

	const iterationParams = getTrackIterationParams({
		direction,
		sourceTrackIndex,
		tracksLength: tracks.length,
	});

	const sourceTrackHeight = getTrackHeight({
		track: tracks[sourceTrackIndex],
		items: allItems,
	});

	for (
		let i = iterationParams.start;
		i !== iterationParams.end;
		i += iterationParams.step
	) {
		const trackHeight = getTrackHeight({track: tracks[i], items: allItems});

		const divider = cumulativeHeight + trackHeight - sourceTrackHeight / 2;

		// Boundary guards: Don't allow insert-between at edges where create-at-top/bottom should take precedence
		const isTopEdge = direction === 'up' && i === 0;
		const isBottomEdge = direction === 'down' && i === tracks.length - 1;

		const isInbetween = isInDividerDropZone({dragDistance, divider});

		if (isInbetween && !isTopEdge && !isBottomEdge) {
			const wouldResultInSameTrackIfItWasTheOnlyItem = i === sourceTrackIndex;
			const allItemsOnTrackAreDragged = tracks[sourceTrackIndex].items.every(
				(itemId) => draggedItems.some((item) => item.id === itemId),
			);

			if (
				!(wouldResultInSameTrackIfItWasTheOnlyItem && allItemsOnTrackAreDragged)
			) {
				const insertPosition = direction === 'up' ? i : i + 1;
				return {type: 'insert-between', position: insertPosition, direction};
			}
		}

		if (
			dragDistance <=
			cumulativeHeight + trackHeight - sourceTrackHeight / 2
		) {
			break;
		}

		cumulativeHeight += trackHeight + TRACK_DIVIDER_HEIGHT;
		trackOffset += iterationParams.step;
	}

	const finalTrackIndex = sourceTrackIndex + trackOffset;

	// Detect multi-item drag
	const isMultiItemDrag = draggedItems.length > 1;

	// Priority 1: Check if we've moved beyond the track boundaries entirely
	if (direction === 'up' && finalTrackIndex < 0) {
		if (!makesSenseToInsertTrackAtTop) {
			if (isMultiItemDrag) {
				return {
					type: 'move',
					direction,
					trackOffset: calculateUpwardMoveLimit(draggedItems, trackOffset),
				};
			}
			return {type: 'move', direction, trackOffset: 0};
		}
		return {type: 'create-at-top', newTrackIndex: -1};
	}

	if (direction === 'down' && finalTrackIndex >= tracks.length) {
		if (!makesSenseToInsertTrackAtBottom) {
			if (isMultiItemDrag) {
				return {
					type: 'move',
					direction,
					trackOffset: calculateDownwardMoveLimit(
						draggedItems,
						tracks.length,
						trackOffset,
					),
				};
			}
			return {type: 'move', direction, trackOffset: 0};
		}
		return {type: 'create-at-bottom', newTrackIndex: tracks.length};
	}

	// Priority 2: Check if we've reached the edge tracks and have remaining distance
	if (direction === 'up') {
		const reachedTop = finalTrackIndex === 0;

		if (!reachedTop) {
			return {type: 'move', direction, trackOffset};
		}

		const remainingDistance = dragDistance - cumulativeHeight;
		if (!shouldCreateNewTrack(remainingDistance)) {
			return {type: 'move', direction, trackOffset};
		}

		if (!makesSenseToInsertTrackAtTop) {
			if (isMultiItemDrag && canGroupMoveUp(draggedItems)) {
				return {type: 'move', direction, trackOffset};
			}
			return {type: 'move', direction, trackOffset: 0};
		}

		return {type: 'create-at-top', newTrackIndex: -1};
	}

	if (direction === 'down') {
		const reachedBottom = finalTrackIndex === tracks.length - 1;

		if (!reachedBottom) {
			return {type: 'move', direction, trackOffset};
		}

		const remainingDistance = dragDistance - cumulativeHeight;
		if (!shouldCreateNewTrack(remainingDistance)) {
			return {type: 'move', direction, trackOffset};
		}

		if (!makesSenseToInsertTrackAtBottom) {
			if (isMultiItemDrag && canGroupMoveDown(draggedItems, tracks.length)) {
				return {type: 'move', direction, trackOffset};
			}
			return {type: 'move', direction, trackOffset: 0};
		}

		return {type: 'create-at-bottom', newTrackIndex: tracks.length};
	}

	if (trackOffset === 0) {
		return {type: 'move', direction, trackOffset: 0};
	}

	// Priority 3: Default case - move to an existing track within the current track bounds
	return {type: 'move', direction, trackOffset};
};

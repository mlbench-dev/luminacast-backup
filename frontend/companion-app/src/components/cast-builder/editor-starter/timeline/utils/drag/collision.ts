import type {EditorStarterItem} from '../../../items/item-type';
import type {TrackType} from '../../../state/types';
import type {PreviewPosition} from '../../drag-preview-provider';

export const overlapsLeft = ({
	item,
	from,
}: {
	item: EditorStarterItem;
	from: number;
}) => {
	return item.from <= from && item.from + item.durationInFrames > from;
};

export const overlapsRight = ({
	item,
	from,
	durationInFrames,
}: {
	item: EditorStarterItem;
	from: number;
	durationInFrames: number;
}) => {
	return (
		item.from < from + durationInFrames &&
		item.from + item.durationInFrames > from &&
		item.from > from
	);
};

const doesFitIn = (
	from: number,
	durationInFrames: number,
	otherItems: EditorStarterItem[],
) => {
	return otherItems.every((item) => {
		const leftOverlap = overlapsLeft({item, from});
		const rightOverlap = overlapsRight({item, from, durationInFrames});
		return !leftOverlap && !rightOverlap;
	});
};

export const getAlternativeForCollisionRight = ({
	collisionRight,
	otherItemsOnTrack,
	durationInFrames,
	items,
}: {
	collisionRight: string | null;
	otherItemsOnTrack: string[];
	durationInFrames: number;
	items: Record<string, EditorStarterItem>;
}) => {
	if (!collisionRight) {
		return null;
	}
	const item = items[collisionRight];
	const shiftedFrom = item.from - durationInFrames;

	if (shiftedFrom < 0) {
		return null;
	}

	const doesFit = doesFitIn(
		shiftedFrom,
		durationInFrames,
		otherItemsOnTrack.map((i) => items[i]),
	);
	if (!doesFit) {
		return null;
	}
	return shiftedFrom;
};

export const getAlternativeForCollisionLeft = ({
	collisionLeft,
	otherItemsOnTrack,
	durationInFrames,
	items,
}: {
	collisionLeft: string | null;
	otherItemsOnTrack: string[];
	durationInFrames: number;
	items: Record<string, EditorStarterItem>;
}) => {
	if (!collisionLeft) {
		return null;
	}
	const item = items[collisionLeft];

	const shiftedFrom = item.from + item.durationInFrames;
	if (shiftedFrom < 0) {
		return null;
	}
	const doesFit = doesFitIn(
		shiftedFrom,
		durationInFrames,
		otherItemsOnTrack.map((i) => items[i]),
	);
	if (!doesFit) {
		return null;
	}

	return shiftedFrom;
};

// this is a virtual item that represents a group of items that are being dragged
interface VirtualTrackItem {
	leftmost: number;
	rightmost: number;
	trackIndex: number;
}

/**
 * Calculates the alternative position for a group of items that are being dragged.
 * The alternative position is the position that requires the least movement to
 * avoid overlaps.
 *
 * @param tentativePositions - The tentative positions of the items in the group.
 * @param draggedItemIds - The ids of the items in the group.
 * @param tracks - The tracks of the items in the group.
 * @param allItems - The all items in the group.
 * @returns Returns a **frame position** that is the alternative position for the group.
 *
 * Specifically, it's the leftmost frame position for the group
 * that would allow all items in the group to fit without
 * overlapping with existing items on the timeline.
 *
 * If there are no alternative positions, returns `null`.
 */
export const getAlternativeForGroupCollision = ({
	tentativePositions,
	draggedItemIds,
	tracks,
	allItems,
}: {
	tentativePositions: PreviewPosition[];
	draggedItemIds: string[];
	tracks: TrackType[];
	allItems: Record<string, EditorStarterItem>;
}): number | null => {
	const trackVirtualItems = new Map<number, VirtualTrackItem>();

	// create a virtual item for each track that the group is being dragged to
	// so it's easier to find collisions
	for (const position of tentativePositions) {
		const trackIndex = position.trackIndex;
		const itemLeft = position.from;
		const itemRight = position.from + position.durationInFrames;

		if (!trackVirtualItems.has(trackIndex)) {
			trackVirtualItems.set(trackIndex, {
				leftmost: itemLeft,
				rightmost: itemRight,
				trackIndex,
			});
		} else {
			const existing = trackVirtualItems.get(trackIndex)!;
			existing.leftmost = Math.min(existing.leftmost, itemLeft);
			existing.rightmost = Math.max(existing.rightmost, itemRight);
		}
	}

	// find the leftmost frame position for the group
	const alternativePositionCandidates: number[] = [];
	const originalGroupLeftmost = Math.min(
		...tentativePositions.map((p) => p.from),
	);

	// for each destination track, find collisions and alternative positions
	// and calculate the alternative position for the group
	for (const [trackIndex, virtualItem] of trackVirtualItems) {
		if (!tracks[trackIndex] || trackIndex < 0) {
			continue;
		}

		const otherItemsOnThisTrack = tracks[trackIndex].items.filter(
			(id) => !draggedItemIds.includes(id),
		);

		if (otherItemsOnThisTrack.length === 0) {
			continue;
		}

		const virtualItemDuration = virtualItem.rightmost - virtualItem.leftmost;

		const leftCollision =
			otherItemsOnThisTrack.find((itemId) =>
				overlapsLeft({item: allItems[itemId], from: virtualItem.leftmost}),
			) || null;

		const rightCollision =
			otherItemsOnThisTrack.find((itemId) =>
				overlapsRight({
					item: allItems[itemId],
					from: virtualItem.leftmost,
					durationInFrames: virtualItemDuration,
				}),
			) || null;

		const leftAlternative = getAlternativeForCollisionLeft({
			collisionLeft: leftCollision,
			otherItemsOnTrack: otherItemsOnThisTrack,
			durationInFrames: virtualItemDuration,
			items: allItems,
		});

		const rightAlternative = getAlternativeForCollisionRight({
			collisionRight: rightCollision,
			otherItemsOnTrack: otherItemsOnThisTrack,
			durationInFrames: virtualItemDuration,
			items: allItems,
		});

		if (leftAlternative !== null) {
			const offsetFromVirtualToGroup =
				originalGroupLeftmost - virtualItem.leftmost;
			alternativePositionCandidates.push(
				leftAlternative + offsetFromVirtualToGroup,
			);
		}

		if (rightAlternative !== null) {
			const offsetFromVirtualToGroup =
				originalGroupLeftmost - virtualItem.leftmost;
			alternativePositionCandidates.push(
				rightAlternative + offsetFromVirtualToGroup,
			);
		}
	}

	if (alternativePositionCandidates.length === 0) {
		return null;
	}

	// choose the alternative position that requires minimal movement
	const optimalFrame = alternativePositionCandidates.reduce(
		(best, candidate) => {
			const bestShift = Math.abs(originalGroupLeftmost - best);
			const candidateShift = Math.abs(originalGroupLeftmost - candidate);
			return candidateShift < bestShift ? candidate : best;
		},
	);

	return optimalFrame;
};

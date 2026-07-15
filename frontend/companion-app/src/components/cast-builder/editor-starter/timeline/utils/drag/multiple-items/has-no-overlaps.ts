import {EditorStarterItem} from '../../../../items/item-type';
import {TrackType} from '../../../../state/types';
import {PreviewPosition} from '../../../drag-preview-provider';

// Helper function to validate no overlaps occur
export const hasNoOverlaps = ({
	tentativePositions,
	draggedItemIds,
	tracks,
	allItems,
}: {
	tentativePositions: Array<PreviewPosition>;
	draggedItemIds: string[];
	tracks: TrackType[];
	allItems: Record<string, EditorStarterItem>;
}): boolean => {
	const positionsByTrack = new Map<number, typeof tentativePositions>();

	for (const pos of tentativePositions) {
		const trackPositions = positionsByTrack.get(pos.trackIndex) || [];
		trackPositions.push(pos);
		positionsByTrack.set(pos.trackIndex, trackPositions);
	}

	for (const [trackIndex, trackTentativePositions] of positionsByTrack) {
		// No conflict on tracks that don't exist yet
		if (!tracks[trackIndex]) {
			continue;
		}

		const existingItems = tracks[trackIndex].items
			.filter((itemId) => !draggedItemIds.includes(itemId))
			.map((itemId) => allItems[itemId]);

		const allItemsOnTrack = [
			...existingItems,
			...trackTentativePositions.map((pos) => ({
				id: pos.id,
				from: pos.from,
				durationInFrames: pos.durationInFrames,
				type: 'temp' as const, // Type doesn't matter for overlap check
			})),
		];

		allItemsOnTrack.sort((a, b) => a.from - b.from);

		for (let i = 0; i < allItemsOnTrack.length - 1; i++) {
			const current = allItemsOnTrack[i];
			const next = allItemsOnTrack[i + 1];

			if (current.from + current.durationInFrames > next.from) {
				return false; // Overlap detected
			}
		}

		const tentativeIds = new Set<string>();
		for (const pos of trackTentativePositions) {
			if (tentativeIds.has(pos.id)) {
				return false; // Duplicate item on same track
			}
			tentativeIds.add(pos.id);
		}
	}

	return true;
};

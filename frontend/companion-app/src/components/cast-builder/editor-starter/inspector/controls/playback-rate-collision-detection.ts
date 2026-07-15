import {EditorStarterItem} from '../../items/item-type';
import {TrackType} from '../../state/types';
import {overlapsRight} from '../../timeline/utils/drag/collision';
import {getTrackIndexOfItem} from '../../timeline/utils/get-track-index-of-item';

/**
 * Calculates the maximum safe duration for an item when changing its playback rate.
 * When decreasing playback rate (slower playback), the item duration would naturally increase.
 * This function finds the maximum duration the item can have without colliding with other items.
 */
export const getMaxSafeDurationInFrames = ({
	item,
	tracks,
	items,
	newDuration,
}: {
	item: EditorStarterItem;
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
	newDuration: number;
}): number => {
	const trackIndex = getTrackIndexOfItem({
		itemId: item.id,
		tracks,
	});

	if (trackIndex === -1) {
		return newDuration;
	}

	const track = tracks[trackIndex];
	const otherItemsOnTrack = track.items
		.filter((id) => id !== item.id)
		.map((id) => items[id]);

	// Find the nearest item to the right that would collide
	let maxSafeDuration = newDuration;

	for (const otherItem of otherItemsOnTrack) {
		// Check if extending this item would cause a collision
		if (
			overlapsRight({
				item: otherItem,
				from: item.from,
				durationInFrames: newDuration,
			})
		) {
			// Calculate the maximum duration that would not collide
			const maxDurationBeforeCollision = otherItem.from - item.from;
			maxSafeDuration = Math.min(maxSafeDuration, maxDurationBeforeCollision);
		}
	}

	return Math.max(1, maxSafeDuration);
};

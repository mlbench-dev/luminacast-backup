import {TrackType} from '../state/types';

export const findItemById = (
	tracks: TrackType[],
	itemId: string,
): {trackIndex: number} => {
	let current;
	for (let i = 0; i < tracks.length; i++) {
		const track = tracks[i];
		const item = track.items.find((id) => id === itemId);
		if (item) {
			current = {trackIndex: i};
		}
	}
	if (!current) {
		throw new Error(`Item ${itemId} not found`);
	}
	return current;
};

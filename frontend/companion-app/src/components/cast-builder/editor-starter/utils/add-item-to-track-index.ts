import {TrackType} from '../state/types';

export const removeItemFromTracks = (tracks: TrackType[], itemId: string) => {
	return tracks.map((t) => {
		if (!t.items.find((i) => i === itemId)) {
			return t;
		}

		return {
			...t,
			items: t.items.filter((i) => i !== itemId),
		};
	});
};

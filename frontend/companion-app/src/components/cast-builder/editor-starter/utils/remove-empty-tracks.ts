import {TrackType} from '../state/types';

export const removeEmptyTracks = (tracks: TrackType[]): TrackType[] => {
	const allHaveItems = tracks.every((t) => t.items.length > 0);
	if (allHaveItems) {
		return tracks;
	}

	return tracks.filter((t) => t.items.length > 0);
};

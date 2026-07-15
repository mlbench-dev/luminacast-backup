import {TrackType} from '../state/types';

export const isTimelineEmpty = (tracks: TrackType[]) => {
	return !tracks.some((t) => t.items.length > 0);
};

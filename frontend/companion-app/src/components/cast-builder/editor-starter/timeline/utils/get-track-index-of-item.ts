import {TrackType} from '../../state/types';

export const getTrackIndexOfItem = ({
	itemId,
	tracks,
}: {
	itemId: string;
	tracks: TrackType[];
}) => {
	return tracks.findIndex((track) => track.items.includes(itemId));
};

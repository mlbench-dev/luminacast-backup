import {EditorStarterItem} from '../../../items/item-type';
import {getTrackHeight} from '../../../state/items';
import {TrackType} from '../../../state/types';
import {getOffsetOfTrack} from '../../../utils/position-utils';

export type TimelineTrackAndLayout = {
	track: TrackType;
	top: number;
	height: number;
};

export const calculateTrackHeights = ({
	tracks,
	items,
}: {
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
}): TimelineTrackAndLayout[] => {
	return tracks.map((track, trackIndex) => {
		return {
			track,
			top: getOffsetOfTrack({trackIndex: trackIndex, tracks, items}),
			height: getTrackHeight({track, items}),
		};
	});
};

export const getTracksHeight = ({
	tracks,
}: {
	tracks: TimelineTrackAndLayout[];
}) => {
	if (tracks.length === 0) {
		return 0;
	}

	return tracks[tracks.length - 1].top + tracks[tracks.length - 1].height;
};

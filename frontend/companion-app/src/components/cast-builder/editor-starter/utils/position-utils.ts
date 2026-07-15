import {EditorStarterItem} from '../items/item-type';
import {getTrackHeight, TRACK_DIVIDER_HEIGHT} from '../state/items';
import {TrackType} from '../state/types';

export const getOffsetOfTrack = ({
	trackIndex,
	tracks,
	items,
}: {
	trackIndex: number;
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
}) => {
	return tracks.slice(0, trackIndex).reduce((acc, track) => {
		return acc + getTrackHeight({track, items}) + TRACK_DIVIDER_HEIGHT;
	}, 0);
};

export const getFromFromLeft = ({
	timelineWidth,
	left,
	totalDurationInFrames,
}: {
	timelineWidth: number;
	left: number;
	totalDurationInFrames: number;
}) => {
	return Math.round((left / timelineWidth) * (totalDurationInFrames - 1));
};

export const getItemLeftOffset = ({
	timelineWidth,
	totalDurationInFrames,
	from,
}: {
	timelineWidth: number;
	totalDurationInFrames: number;
	from: number;
}) => {
	return (from / totalDurationInFrames) * timelineWidth;
};

export const getItemWidth = ({
	itemDurationInFrames,
	timelineWidth,
	totalDurationInFrames,
}: {
	itemDurationInFrames: number;
	timelineWidth: number;
	totalDurationInFrames: number;
}) => {
	return (itemDurationInFrames / totalDurationInFrames) * timelineWidth;
};

export const getItemRoundedPosition = (
	timelineItemLeft: number,
	timelineItemWidth: number,
) => {
	const roundedLeft = Math.round(timelineItemLeft);
	const roundedDifference = roundedLeft - timelineItemLeft;
	const width = timelineItemWidth - roundedDifference;
	return {roundedLeft, width, roundedDifference};
};

const shouldShowItemHeaderMap: Record<EditorStarterItem['type'], boolean> = {
	audio: true,
	gif: true,
	image: true,
	video: true,
	captions: false,
	solid: false,
	text: false,
};

export const shouldShowItemHeader = (item: EditorStarterItem) => {
	return shouldShowItemHeaderMap[item.type];
};

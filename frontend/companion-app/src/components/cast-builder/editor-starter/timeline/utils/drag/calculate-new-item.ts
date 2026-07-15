import {PreviewPosition} from '../../drag-preview-provider';

export const getNewPositionAfterDrag = ({
	item,
	frameOffset,
	newTrackIndex,
}: {
	item: PreviewPosition;
	frameOffset: number;
	newTrackIndex: number;
}): PreviewPosition => {
	const newFrom = Math.max(0, item.from + frameOffset);

	return {
		id: item.id,
		trackIndex: newTrackIndex,
		from: newFrom,
		durationInFrames: item.durationInFrames,
	};
};

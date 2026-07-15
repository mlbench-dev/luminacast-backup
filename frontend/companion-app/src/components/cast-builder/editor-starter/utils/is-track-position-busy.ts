import {EditorStarterItem} from '../items/item-type';
import {TrackType} from '../state/types';

export const isTrackPositionBusy = ({
	track,
	startAt,
	items,
	durationInFrames,
}: {
	track: TrackType;
	items: Record<string, EditorStarterItem>;
	startAt: number;
	durationInFrames: number;
}) => {
	return track.items.find((itemId) => {
		const item = items[itemId];
		if (!item) {
			throw new Error(`Item ${itemId} not found`);
		}
		return (
			(item.from <= startAt && item.from + item.durationInFrames > startAt) ||
			(item.from > startAt && item.from < startAt + durationInFrames)
		);
	});
};

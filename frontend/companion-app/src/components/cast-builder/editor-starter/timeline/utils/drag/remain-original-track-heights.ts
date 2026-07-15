import {EditorStarterItem} from '../../../items/item-type';
import {TRACK_DIVIDER_HEIGHT} from '../../../state/items';
import {TrackType} from '../../../state/types';
import {
	calculateTrackHeights,
	TimelineTrackAndLayout,
} from './calculate-track-heights';

export const remainOriginalTrackHeights = ({
	originalTracks,
	originalItems,
	newTracks,
	newItems,
}: {
	originalTracks: TrackType[];
	originalItems: Record<string, EditorStarterItem>;
	newTracks: TrackType[];
	newItems: Record<string, EditorStarterItem>;
}): TimelineTrackAndLayout[] => {
	if (originalTracks === newTracks && newItems === originalItems) {
		return calculateTrackHeights({
			tracks: originalTracks,
			items: originalItems,
		});
	}

	const originalHeights = calculateTrackHeights({
		tracks: originalTracks,
		items: originalItems,
	});

	const newHeights = calculateTrackHeights({
		tracks: newTracks,
		items: newItems,
	});

	let offset = 0;

	return newHeights.map((newHeight): TimelineTrackAndLayout => {
		const originalHeight =
			originalHeights.find((t) => t.track.id === newHeight.track.id) ?? null;
		const heightToApply =
			originalHeight !== null ? originalHeight.height : newHeight.height;

		const item: TimelineTrackAndLayout = {
			...newHeight,
			height: heightToApply,
			top: offset,
		};

		offset += heightToApply;
		offset += TRACK_DIVIDER_HEIGHT;

		return item;
	});
};

import {getAssetStartInSeconds} from '../../assets/utils';
import {getItemPlaybackRate} from '../../items/get-item-playback-rate';
import {EditorStarterItem} from '../../items/item-type';
import {clampFadeDurations} from '../../timeline/timeline-item/timeline-item-extend-handles/clamp-fade-duration';
import {clamp} from '../../utils/clamp';
import {updateItemTimings} from './update-item-timings';
import {updateAssetStartDurationOfItem} from './update-start-duration';

export const getMinimumFromWhenExtendingLeftBasedOnPreviousItem = ({
	trackItemsSorted,
	items,
	itemIndex,
}: {
	trackItemsSorted: string[];
	items: Record<string, EditorStarterItem>;
	itemIndex: number;
}) => {
	const previousItem = trackItemsSorted[itemIndex - 1];
	const previousItemEnd = previousItem
		? items[previousItem].from + items[previousItem].durationInFrames
		: 0;
	return previousItemEnd;
};

export const getMinimumFromWhenExtendingLeftBasedOnAsset = ({
	fps,
	prevItem,
}: {
	fps: number;
	prevItem: EditorStarterItem;
}) => {
	const assetStartInSeconds = getAssetStartInSeconds(prevItem);

	if (assetStartInSeconds === null) {
		return null;
	}

	const playbackRate = getItemPlaybackRate(prevItem);

	const availableFrames = (assetStartInSeconds * fps) / playbackRate;

	return Math.round(prevItem.from - availableFrames);
};

export const getMinimumFromWhenExtendingLeft = ({
	trackItemsSorted,
	items,
	itemIndex,
	fps,
	prevItem,
}: {
	trackItemsSorted: string[];
	items: Record<string, EditorStarterItem>;
	itemIndex: number;
	fps: number;
	prevItem: EditorStarterItem;
}) => {
	const minimumFromBasedOnPreviousItem =
		getMinimumFromWhenExtendingLeftBasedOnPreviousItem({
			trackItemsSorted,
			items,
			itemIndex,
		});
	const minimumFromBasedOnAsset = getMinimumFromWhenExtendingLeftBasedOnAsset({
		fps,
		prevItem,
	});

	return Math.max(minimumFromBasedOnPreviousItem, minimumFromBasedOnAsset ?? 0);
};

export const extendLeft = ({
	prevItem,
	offsetInFrames,
	trackItemsSorted,
	itemIndex,
	initialFrom,
	fps,
	items,
	initialDurationInFrames,
	pixelsPerFrame,
}: {
	prevItem: EditorStarterItem;
	offsetInFrames: number;
	trackItemsSorted: string[];
	itemIndex: number;
	initialFrom: number;
	fps: number;
	items: Record<string, EditorStarterItem>;
	initialDurationInFrames: number;
	pixelsPerFrame: number;
}): EditorStarterItem => {
	const minFrom = getMinimumFromWhenExtendingLeft({
		trackItemsSorted,
		items,
		itemIndex,
		fps,
		prevItem,
	});

	const maxFrom = initialFrom + initialDurationInFrames - 1;

	const newFrom = clamp({
		value: initialFrom + offsetInFrames,
		min: minFrom,
		max: maxFrom,
	});
	const newDurationInFrames = initialFrom + initialDurationInFrames - newFrom;

	const updatedTimings = updateItemTimings({
		item: prevItem,
		newDurationInFrames: newDurationInFrames,
		newFrom,
	});

	// When shortening from the left, prefer keeping fade-out as-is and clamp fade-in
	const clampedFadeDurations = clampFadeDurations({
		fps,
		item: updatedTimings,
		preferSide: 'out',
		pixelsPerFrame,
	});

	const assetStartInSeconds = getAssetStartInSeconds(prevItem);
	const playbackRate = getItemPlaybackRate(prevItem);
	if (assetStartInSeconds === null) {
		return clampedFadeDurations;
	}

	const framesDelta = (newFrom - prevItem.from) * playbackRate;
	const newAssetStartInSeconds = Math.max(
		0,
		assetStartInSeconds + framesDelta / fps,
	);

	return updateAssetStartDurationOfItem({
		item: clampedFadeDurations,
		startDurationInSeconds: newAssetStartInSeconds,
	});
};

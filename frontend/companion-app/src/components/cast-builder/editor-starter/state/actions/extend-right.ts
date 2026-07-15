import {EditorStarterAsset} from '../../assets/assets';
import {
	getAssetDurationInSeconds,
	getAssetStartInSeconds,
} from '../../assets/utils';
import {getItemPlaybackRate} from '../../items/get-item-playback-rate';
import {EditorStarterItem} from '../../items/item-type';
import {clampFadeDurations} from '../../timeline/timeline-item/timeline-item-extend-handles/clamp-fade-duration';
import {clamp} from '../../utils/clamp';
import {updateItemTimings} from './update-item-timings';

export const getMaximumDurationWhenExtendingRightBasedOnNextItem = ({
	trackItemsSorted,
	items,
	itemIndex,
	initialFrom,
}: {
	trackItemsSorted: string[];
	items: Record<string, EditorStarterItem>;
	itemIndex: number;
	initialFrom: number;
}) => {
	const nextItem = trackItemsSorted[itemIndex + 1];
	const nextItemFrom = nextItem ? items[nextItem].from : Infinity;

	const maxDuration = nextItemFrom - initialFrom;
	return Math.round(maxDuration);
};

export const getMaximumDurationWhenExtendingRightBasedOnAsset = ({
	asset,
	fps,
	prevItem,
}: {
	asset: EditorStarterAsset | null;
	fps: number;
	prevItem: EditorStarterItem;
}) => {
	const assetStartInSeconds = getAssetStartInSeconds(prevItem);

	if (assetStartInSeconds === null) {
		return Infinity;
	}

	if (asset === null) {
		throw new Error('Asset not found');
	}

	const assetDurationInSeconds = getAssetDurationInSeconds(asset);
	if (assetDurationInSeconds === null) {
		throw new Error('Asset duration is null');
	}

	// How Remotion applies operations:
	// https://www.remotion.dev/docs/audio/order-of-operations
	// 1. Trim
	// (2. Offset)
	// 3. Apply playback rate

	const availableFrames =
		(assetDurationInSeconds * fps - assetStartInSeconds * fps) /
		getItemPlaybackRate(prevItem);

	return Math.round(availableFrames);
};

export const getMaximumDurationWhenExtendingRight = ({
	trackItemsSorted,
	items,
	itemIndex,
	initialFrom,
	fps,
	asset,
	prevItem,
	visibleFrames,
}: {
	trackItemsSorted: string[];
	items: Record<string, EditorStarterItem>;
	itemIndex: number;
	initialFrom: number;
	fps: number;
	asset: EditorStarterAsset | null;
	prevItem: EditorStarterItem;
	visibleFrames: number;
}) => {
	const maxDurationBeforeNextItem =
		getMaximumDurationWhenExtendingRightBasedOnNextItem({
			trackItemsSorted,
			items,
			itemIndex,
			initialFrom,
		});

	const assetMaxDuration = getMaximumDurationWhenExtendingRightBasedOnAsset({
		asset,
		fps,
		prevItem,
	});

	return Math.min(
		maxDurationBeforeNextItem,
		assetMaxDuration,
		visibleFrames - initialFrom,
	);
};

export const extendRight = ({
	prevItem,
	offsetInFrames,
	trackItemsSorted,
	itemIndex,
	asset,
	items,
	fps,
	initialDurationInFrames,
	initialFrom,
	pixelsPerFrame,
	visibleFrames,
}: {
	prevItem: EditorStarterItem;
	offsetInFrames: number;
	trackItemsSorted: string[];
	itemIndex: number;
	asset: EditorStarterAsset | null;
	items: Record<string, EditorStarterItem>;
	fps: number;
	initialDurationInFrames: number;
	initialFrom: number;
	pixelsPerFrame: number;
	visibleFrames: number;
}): EditorStarterItem => {
	const maxDuration = getMaximumDurationWhenExtendingRight({
		trackItemsSorted,
		items,
		itemIndex,
		initialFrom,
		fps,
		asset,
		prevItem,
		visibleFrames,
	});

	const newDurationInFrames = clamp({
		value: initialDurationInFrames + offsetInFrames,
		min: 1,
		max: maxDuration,
	});

	const updatedTimings = updateItemTimings({
		item: prevItem,
		newDurationInFrames,
		newFrom: prevItem.from,
	});

	// When shortening from the right, prefer keeping fade-in as-is and clamp fade-out
	return clampFadeDurations({
		item: updatedTimings,
		fps,
		preferSide: 'in',
		pixelsPerFrame,
	});
};

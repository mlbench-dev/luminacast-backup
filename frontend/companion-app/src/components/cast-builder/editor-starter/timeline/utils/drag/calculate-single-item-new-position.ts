import {EditorStarterItem} from '../../../items/item-type';
import {TrackType} from '../../../state/types';
import {clamp} from '../../../utils/clamp';
import {
	getAlternativeForCollisionLeft,
	getAlternativeForCollisionRight,
	overlapsLeft,
	overlapsRight,
} from './collision';
import {MoveTrackOffsetResult} from './get-track-offset';

export const calculateSingleItemNewPosition = ({
	durationInFrames,
	initialFrom,
	trackIndex,
	tracks,
	itemId,
	items,
	trackOffsetResult,
	frameOffset,
}: {
	durationInFrames: number;
	initialFrom: number;
	trackIndex: number;
	itemId: string;
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
	trackOffsetResult: MoveTrackOffsetResult;
	frameOffset: number;
}): {
	track: number;
	from: number;
} | null => {
	const numberOfTracks = tracks.length;

	const newTrack = clamp({
		min: 0,
		max: numberOfTracks - 1,
		value: trackIndex + trackOffsetResult.trackOffset,
	});

	const newFrom = Math.max(0, frameOffset + initialFrom);

	const otherItemsOnTrack = tracks[newTrack].items.filter((i) => i !== itemId);

	const collisionLeft = otherItemsOnTrack.find((otherItemId) => {
		return overlapsLeft({item: items[otherItemId], from: newFrom});
	});
	const collisionRight = otherItemsOnTrack.find((otherItemId) => {
		return overlapsRight({
			item: items[otherItemId],
			from: newFrom,
			durationInFrames,
		});
	});

	if (!collisionLeft && !collisionRight) {
		return {
			track: newTrack,
			from: newFrom,
		};
	}

	const alternativeFromRight = getAlternativeForCollisionRight({
		collisionRight: collisionRight ?? null,
		otherItemsOnTrack,
		durationInFrames,
		items,
	});
	const alternativeFromLeft = getAlternativeForCollisionLeft({
		collisionLeft: collisionLeft ?? null,
		otherItemsOnTrack,
		durationInFrames,
		items,
	});

	if (alternativeFromLeft && alternativeFromRight) {
		const leftShift = Math.abs(newFrom - alternativeFromLeft);
		const rightShift = Math.abs(newFrom - alternativeFromRight);
		return leftShift < rightShift
			? {track: newTrack, from: alternativeFromLeft}
			: {track: newTrack, from: alternativeFromRight};
	}

	if (alternativeFromLeft) {
		return {track: newTrack, from: alternativeFromLeft};
	}

	if (alternativeFromRight) {
		return {track: newTrack, from: alternativeFromRight};
	}
	return null;
};

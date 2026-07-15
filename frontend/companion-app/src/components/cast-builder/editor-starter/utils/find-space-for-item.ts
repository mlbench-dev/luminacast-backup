import {EditorStarterItem} from '../items/item-type';
import {TrackType} from '../state/types';
import {isTrackPositionBusy} from './is-track-position-busy';

export interface Space {
	trackIndex: number;
	forceCreateNewTrack: boolean;
}

export type FindSpaceStartPosition =
	| {
			type: 'front';
	  }
	| {
			type: 'back';
	  }
	| {
			type: 'directly-above';
			trackIndex: number;
	  };

const findSpaceForItemAbove = ({
	durationInFrames,
	startAt,
	tracks,
	above,
	items,
}: {
	durationInFrames: number;
	startAt: number;
	tracks: TrackType[];
	above: number;
	items: Record<string, EditorStarterItem>;
}): Space => {
	if (above === 0) {
		return {trackIndex: -1, forceCreateNewTrack: false};
	}

	const trackAbove = tracks[above - 1];

	if (
		isTrackPositionBusy({
			track: trackAbove,
			startAt,
			durationInFrames,
			items,
		})
	) {
		return {trackIndex: above, forceCreateNewTrack: true};
	}

	return {trackIndex: above - 1, forceCreateNewTrack: false};
};

const findSpaceForItemInFront = ({
	durationInFrames,
	startAt,
	tracks,
	items,
	stopOnFirstFound,
}: {
	durationInFrames: number;
	startAt: number;
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
	stopOnFirstFound: boolean;
}): Space => {
	// Unless we find something better, we create a new track
	let bestTrackPosition: Space = {
		trackIndex: -1,
		forceCreateNewTrack: false,
	};

	for (let i = 0; i !== tracks.length; i += 1) {
		const track = tracks[i];
		const isBusy = isTrackPositionBusy({
			track,
			startAt,
			durationInFrames,
			items,
		});

		// If the track is busy, it would mean we place the item below another one.
		// Stop here and return the best track position we found so far.
		if (isBusy) {
			return bestTrackPosition;
		}
		bestTrackPosition = {trackIndex: i, forceCreateNewTrack: false};
		if (stopOnFirstFound) {
			break;
		}
	}

	return bestTrackPosition;
};

const findSpaceForItemInBack = ({
	durationInFrames,
	startAt,
	tracks,
	items,
	stopOnFirstFound,
}: {
	durationInFrames: number;
	startAt: number;
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
	stopOnFirstFound: boolean;
}) => {
	// Unless we find something better, we create a new track
	let bestTrackPosition: Space = {
		trackIndex: tracks.length,
		forceCreateNewTrack: false,
	};

	for (let i = tracks.length - 1; i !== -1; i -= 1) {
		const track = tracks[i];
		const isBusy = isTrackPositionBusy({
			track,
			startAt,
			durationInFrames,
			items,
		});

		// If the track is busy, it would mean we place the item below another one.
		// Stop here and return the best track position we found so far.
		if (isBusy) {
			return bestTrackPosition;
		}
		bestTrackPosition = {trackIndex: i, forceCreateNewTrack: false};
		if (stopOnFirstFound) {
			break;
		}
	}

	return bestTrackPosition;
};

export const findSpaceForItem = ({
	durationInFrames,
	startAt,
	tracks,
	startPosition,
	stopOnFirstFound,
	items,
}: {
	durationInFrames: number;
	startAt: number;
	tracks: TrackType[];
	startPosition: FindSpaceStartPosition;
	stopOnFirstFound: boolean;
	items: Record<string, EditorStarterItem>;
}): Space => {
	if (startPosition.type === 'directly-above') {
		return findSpaceForItemAbove({
			durationInFrames,
			startAt,
			tracks,
			above: startPosition.trackIndex,
			items,
		});
	}

	if (startPosition.type === 'front') {
		return findSpaceForItemInFront({
			durationInFrames,
			startAt,
			tracks,
			items,
			stopOnFirstFound,
		});
	}

	if (startPosition.type === 'back') {
		return findSpaceForItemInBack({
			durationInFrames,
			startAt,
			tracks,
			items,
			stopOnFirstFound,
		});
	}

	throw new Error(
		'Invalid start position: ' + JSON.stringify(startPosition satisfies never),
	);
};

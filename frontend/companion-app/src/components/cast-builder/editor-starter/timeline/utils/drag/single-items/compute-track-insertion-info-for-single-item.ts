import {TrackType} from '../../../../state/types';
import {TrackInsertions} from '../types';

type TrackInsertionInfo = {
	targetTrack: number;
	trackInsertions: TrackInsertions | null;
};

// Helper – given the raw track offset (may be < 0 / > tracks.length-1),
// decide whether we need to insert new tracks and return the final target
// track index *after* a potential insertion.
export const computeTrackInsertionInfoforSingleItem = ({
	allTracks,
	startTrack,
	rawTrackOffset,
}: {
	allTracks: TrackType[];
	startTrack: number;
	rawTrackOffset: number;
}): TrackInsertionInfo => {
	const tentativeTrack = startTrack + rawTrackOffset;

	if (tentativeTrack < 0) {
		const count = Math.abs(tentativeTrack);
		return {
			targetTrack: 0,
			trackInsertions: {type: 'top', count},
		};
	}

	if (tentativeTrack >= allTracks.length) {
		const count = tentativeTrack - allTracks.length + 1;
		return {
			targetTrack: tentativeTrack,
			trackInsertions: {type: 'bottom', count},
		};
	}

	return {targetTrack: tentativeTrack, trackInsertions: null};
};

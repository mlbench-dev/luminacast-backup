import {TrackType} from '../../../../state/types';
import {PreviewPosition} from '../../../drag-preview-provider';
import {TrackInsertions} from '../types';

export const getTrackInsertionsFromTentativePositions = ({
	tentativePositions,
	tracks,
}: {
	tentativePositions: Array<PreviewPosition>;
	tracks: TrackType[];
}): TrackInsertions | null => {
	const tracksToInsertAtTop = new Set(
		tentativePositions.filter((position) => position.trackIndex < 0),
	).size;
	const tracksToInsertAtBottom = new Set(
		tentativePositions.filter(
			(position) => position.trackIndex >= tracks.length,
		),
	).size;

	if (tracksToInsertAtTop > 0 && tracksToInsertAtBottom > 0) {
		throw new Error(
			'Cannot insert tracks at both top and bottom at the same time.',
		);
	}

	if (tracksToInsertAtTop > 0) {
		return {
			type: 'top',
			count: tracksToInsertAtTop,
		};
	}

	if (tracksToInsertAtBottom > 0) {
		return {
			type: 'bottom',
			count: tracksToInsertAtBottom,
		};
	}

	return null;
};

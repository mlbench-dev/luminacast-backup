import {findItemById} from '../../utils/find-item-by-id';
import {findSpaceForItem} from '../../utils/find-space-for-item';
import {EditorState} from '../types';
import {addItemInSpace} from './add-item';

interface BringToFrontOrBackProps {
	state: EditorState;
	itemId: string;
	position: 'front' | 'back';
}

export const bringToFrontOrBack = ({
	state,
	itemId,
	position,
}: BringToFrontOrBackProps): EditorState => {
	const {items, tracks} = state.undoableState;
	const current = items[itemId];

	if (!current) {
		throw new Error('Item not found');
	}

	const tracksWithoutItem = tracks.map((t) => {
		if (t.items.find((i) => i === itemId)) {
			return {
				...t,
				items: t.items.filter((i) => i !== itemId),
			};
		}

		return t;
	});

	const space = findSpaceForItem({
		durationInFrames: current.durationInFrames,
		startAt: current.from,
		tracks: tracksWithoutItem,
		startPosition: {type: position},
		stopOnFirstFound: true,
		items,
	});

	const {trackIndex} = findItemById(tracks, itemId);

	if (space.trackIndex === trackIndex) {
		return state;
	}

	const newTracks = addItemInSpace({
		tracks,
		item: current,
		space,
	});

	return {
		...state,
		undoableState: {
			...state.undoableState,
			tracks: newTracks,
		},
	};
};

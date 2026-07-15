import {EditorStarterItem} from '../../items/item-type';
import {removeItemFromTracks} from '../../utils/add-item-to-track-index';
import {
	findSpaceForItem,
	FindSpaceStartPosition,
	Space,
} from '../../utils/find-space-for-item';
import {generateRandomId} from '../../utils/generate-random-id';
import {removeEmptyTracks} from '../../utils/remove-empty-tracks';
import {EditorState, TrackType} from '../types';

export const addItemInSpace = ({
	tracks,
	item,
	space,
}: {
	tracks: TrackType[];
	item: EditorStarterItem;
	space: Space;
}): TrackType[] => {
	const withItemRemoved = removeItemFromTracks(tracks, item.id);

	if (!withItemRemoved[space.trackIndex]) {
		const newTrack: TrackType = {
			id: generateRandomId(),
			items: [item.id],
			hidden: false,
			muted: false,
		};

		if (space.trackIndex === -1) {
			return removeEmptyTracks([newTrack, ...withItemRemoved]);
		}

		return removeEmptyTracks([...withItemRemoved, newTrack]);
	}

	if (space.forceCreateNewTrack) {
		const previousTracks = withItemRemoved.slice(0, space.trackIndex);
		const newTrack = {
			id: generateRandomId(),
			items: [item.id],
			hidden: false,
			muted: false,
		};
		return removeEmptyTracks([
			...previousTracks,
			newTrack,
			...withItemRemoved.slice(space.trackIndex),
		]);
	}

	const newTracks = withItemRemoved.map((track, idx) => {
		if (idx === space.trackIndex) {
			return {...track, items: [...track.items, item.id]};
		}

		return track;
	});

	return removeEmptyTracks(newTracks);
};

export const addItem = ({
	state,
	item,
	select,
	position: position,
}: {
	state: EditorState;
	item: EditorStarterItem;
	select: boolean;
	position: FindSpaceStartPosition;
}): EditorState => {
	// For text items, always create a new track to avoid overlapping
	const space = findSpaceForItem({
		durationInFrames: item.durationInFrames,
		startAt: item.from,
		tracks: state.undoableState.tracks,
		startPosition: position,
		stopOnFirstFound: false,
		items: state.undoableState.items,
	});

	const newTracks = addItemInSpace({
		tracks: state.undoableState.tracks,
		item,
		space,
	});

	return {
		...state,
		undoableState: {
			...state.undoableState,
			tracks: newTracks,
			items: {
				...state.undoableState.items,
				[item.id]: item,
			},
		},
		selectedItems: select ? [item.id] : state.selectedItems,
	};
};

import {removeEmptyTracks} from '../../utils/remove-empty-tracks';
import {EditorState, UndoableState} from '../types';
import {setSelectedItems} from './set-selected-items';

// like deleteItems, but doesn't remove assets to preserve them for potential paste operations
export const cutItems = (state: EditorState, idsToCut: string[]) => {
	const newTracks = state.undoableState.tracks.map((track) => {
		const items = track.items.filter((itemId) => {
			if (idsToCut.includes(itemId)) {
				return false;
			}

			return true;
		});

		// Don't create a new object if no items were deleted
		if (items.length === track.items.length) {
			return track;
		}

		return {
			...track,
			items: items,
		};
	});

	const newState: UndoableState = {
		...state.undoableState,
		tracks: removeEmptyTracks(newTracks),
		items: {
			...state.undoableState.items,
		},
	};

	for (const id of idsToCut) {
		delete newState.items[id];
	}

	const newSelectedItems = state.selectedItems.filter(
		(id) => !idsToCut.includes(id),
	);

	return setSelectedItems(
		{
			...state,
			undoableState: newState,
		},
		newSelectedItems,
	);
};

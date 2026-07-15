import {EditorStarterItem} from '../../items/item-type';
import {EditorState} from '../types';

export const changeItem = (
	state: EditorState,
	itemId: string,
	updater: (item: EditorStarterItem) => EditorStarterItem,
): EditorState => {
	let updated = false;
	const existingItem = state.undoableState.items[itemId];

	const updatedItem = updater(existingItem);
	if (updatedItem !== existingItem) {
		updated = true;
	}

	if (updated) {
		const newState = {
			...state,
			undoableState: {
				...state.undoableState,
				items: {
					...state.undoableState.items,
					[itemId]: updatedItem,
				},
			},
		};

		return newState;
	}
	return state;
};

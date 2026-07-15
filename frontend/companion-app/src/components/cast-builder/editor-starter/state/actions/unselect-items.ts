import {EditorState} from '../types';
import {resetItemCropToNonNegative} from './item-cropping';

export const unselectItemsOrDisableCropUI = (
	state: EditorState,
): EditorState => {
	if (state.selectedItems.length === 0) {
		return state;
	}

	// If the cropping UI is enabled,
	// we disable it on the first unselect
	// and has to click again to deselect item
	if (state.itemSelectedForCrop) {
		const itemWithNonNegativeCrop = resetItemCropToNonNegative(state);
		return {
			...itemWithNonNegativeCrop,
			itemSelectedForCrop: null,
		};
	}

	return {
		...state,
		selectedItems: [],
	};
};

export const unselectItems = (state: EditorState): EditorState => {
	if (state.selectedItems.length === 0) {
		return state;
	}

	return {
		...state,
		selectedItems: [],
	};
};

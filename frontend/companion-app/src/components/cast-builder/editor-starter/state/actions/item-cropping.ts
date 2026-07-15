import {getCanCrop, getCropFromItem} from '../../utils/get-crop-from-item';
import {EditorState} from '../types';
import {changeItem} from './change-item';

export const selectItemForCrop = ({
	state,
	itemId,
}: {
	state: EditorState;
	itemId: string;
}): EditorState => {
	return {
		...state,
		itemSelectedForCrop: itemId,
	};
};

// This function handles the following scenario:
// 1. An item is being double-clicked to enable crop UI
// 2. The item is being dragged and moved leading to negative crop values during editing (this is also supported by Figma)
// 3. Crop mode is being exited, now crop values are set to reasonable non-negative values
// See: https://remotion.dev/docs/editor-starter/cropping#negative-crop-values
export const resetItemCropToNonNegative = (state: EditorState): EditorState => {
	const itemId = state.itemSelectedForCrop;
	if (!itemId) {
		return state;
	}

	return changeItem(state, itemId, (i) => {
		const crop = getCropFromItem(i);
		if (!crop) {
			throw new Error('Item cannot be cropped');
		}

		const newCropLeft = Math.max(0, crop.cropLeft);
		const newCropTop = Math.max(0, crop.cropTop);
		const newCropRight = Math.max(0, crop.cropRight);
		const newCropBottom = Math.max(0, crop.cropBottom);

		if (
			newCropLeft === crop.cropLeft &&
			newCropTop === crop.cropTop &&
			newCropRight === crop.cropRight &&
			newCropBottom === crop.cropBottom
		) {
			return i;
		}

		return {
			...i,
			cropLeft: newCropLeft,
			cropTop: newCropTop,
			cropRight: newCropRight,
			cropBottom: newCropBottom,
		};
	});
};

export const unselectItemForCrop = (state: EditorState): EditorState => {
	if (!state.itemSelectedForCrop) {
		return state;
	}

	const itemWithNonNegativeCrop = resetItemCropToNonNegative(state);

	return {
		...itemWithNonNegativeCrop,
		itemSelectedForCrop: null,
	};
};

export const updateCropLeft = ({
	state,
	itemId,
	cropLeft,
}: {
	state: EditorState;
	itemId: string;
	cropLeft: number;
}): EditorState => {
	return changeItem(state, itemId, (i) => {
		if (!getCanCrop(i)) {
			throw new Error('Item cannot be cropped');
		}

		if (i.cropLeft === cropLeft) {
			return i;
		}

		return {
			...i,
			cropLeft,
		};
	});
};

export const updateCropTop = ({
	state,
	itemId,
	cropTop,
}: {
	state: EditorState;
	itemId: string;
	cropTop: number;
}): EditorState => {
	return changeItem(state, itemId, (i) => {
		if (!getCanCrop(i)) {
			throw new Error('Item cannot be cropped');
		}

		if (i.cropTop === cropTop) {
			return i;
		}

		return {
			...i,
			cropTop,
		};
	});
};

export const updateCropRight = ({
	state,
	itemId,
	cropRight,
}: {
	state: EditorState;
	itemId: string;
	cropRight: number;
}): EditorState => {
	return changeItem(state, itemId, (i) => {
		if (!getCanCrop(i)) {
			throw new Error('Item cannot be cropped');
		}

		if (i.cropRight === cropRight) {
			return i;
		}

		return {
			...i,
			cropRight,
		};
	});
};

export const updateCropBottom = ({
	state,
	itemId,
	cropBottom,
}: {
	state: EditorState;
	itemId: string;
	cropBottom: number;
}): EditorState => {
	return changeItem(state, itemId, (i) => {
		if (!getCanCrop(i)) {
			throw new Error('Item cannot be cropped');
		}

		if (i.cropBottom === cropBottom) {
			return i;
		}

		return {
			...i,
			cropBottom,
		};
	});
};

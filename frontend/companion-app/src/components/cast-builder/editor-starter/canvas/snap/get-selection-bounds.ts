import {EditorStarterItem} from '../../items/item-type';
import {SelectionBounds} from './canvas-snap-types';
import {getItemBounds} from './get-item-bounds';

/**
 * Computes the bounding box of all selected items combined.
 * Returns null if no items are provided.
 */
export const getSelectionBounds = (
	items: EditorStarterItem[],
): SelectionBounds | null => {
	if (items.length === 0) {
		return null;
	}

	const boundsList = items.map(getItemBounds);

	const left = Math.min(...boundsList.map((b) => b.left));
	const top = Math.min(...boundsList.map((b) => b.top));
	const right = Math.max(...boundsList.map((b) => b.right));
	const bottom = Math.max(...boundsList.map((b) => b.bottom));
	const width = right - left;
	const height = bottom - top;

	return {
		left,
		top,
		width,
		height,
		right,
		bottom,
		centerX: left + width / 2,
		centerY: top + height / 2,
	};
};

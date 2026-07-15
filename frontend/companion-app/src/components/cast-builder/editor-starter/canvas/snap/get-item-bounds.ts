import {EditorStarterItem} from '../../items/item-type';
import {getRectAfterCrop} from '../../utils/get-dimensions-after-crop';
import {ItemBounds} from './canvas-snap-types';

/**
 * Gets the visual bounds of an item, accounting for crop.
 * Uses getRectAfterCrop to get the actual displayed rectangle.
 */
export const getItemBounds = (item: EditorStarterItem): ItemBounds => {
	const rect = getRectAfterCrop(item);
	return {
		left: rect.left,
		top: rect.top,
		width: rect.width,
		height: rect.height,
		right: rect.left + rect.width,
		bottom: rect.top + rect.height,
		centerX: rect.left + rect.width / 2,
		centerY: rect.top + rect.height / 2,
	};
};

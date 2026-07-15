import {EditorStarterItem} from '../items/item-type';
import {getCropFromItem} from './get-crop-from-item';

export const getRectAfterCrop = (item: EditorStarterItem) => {
	const crop = getCropFromItem(item);

	if (!crop) {
		return {
			left: item.left,
			top: item.top,
			width: item.width,
			height: item.height,
		};
	}

	return {
		left: item.left + crop.cropLeft * item.width,
		top: item.top + crop.cropTop * item.height,
		width:
			item.width - crop.cropLeft * item.width - crop.cropRight * item.width,
		height:
			item.height - crop.cropTop * item.height - crop.cropBottom * item.height,
	};
};

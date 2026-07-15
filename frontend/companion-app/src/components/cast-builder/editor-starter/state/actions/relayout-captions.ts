import {CaptionsItem} from '../../items/captions/captions-item-type';

export const relayoutCaptions = (previousItem: CaptionsItem) => {
	const newHeight = Math.round(
		previousItem.fontSize * previousItem.lineHeight * previousItem.maxLines,
	);

	if (newHeight === previousItem.height) {
		return previousItem;
	}

	return {
		...previousItem,
		height: newHeight,
	};
};

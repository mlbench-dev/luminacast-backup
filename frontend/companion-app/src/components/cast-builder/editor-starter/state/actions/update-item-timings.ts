import {EditorStarterItem} from '../../items/item-type';

export const updateItemTimings = ({
	item,
	newDurationInFrames,
	newFrom,
}: {
	item: EditorStarterItem;
	newDurationInFrames: number;
	newFrom: number;
}): EditorStarterItem => {
	if (item.from === newFrom && item.durationInFrames === newDurationInFrames) {
		return item;
	}

	const newItem: EditorStarterItem = {
		...item,
		durationInFrames: newDurationInFrames,
		from: newFrom,
	};

	return newItem;
};

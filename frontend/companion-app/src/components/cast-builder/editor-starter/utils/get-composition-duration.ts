import {EditorStarterItem} from '../items/item-type';

export const getCompositionDuration = (items: EditorStarterItem[]) => {
	const itemLastFrames = items.map((i) => i.from + i.durationInFrames);
	const maxFrames = itemLastFrames.reduce((a, b) => Math.max(a, b), 0);
	return maxFrames;
};

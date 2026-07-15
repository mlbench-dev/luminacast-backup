import {EditorStarterItem} from '../../items/item-type';

export const setPositionLeft = ({
	item,
	left,
}: {
	item: EditorStarterItem;
	left: number;
}) => {
	return {
		...(item as EditorStarterItem),
		left,
	} as EditorStarterItem;
};

export const setPositionTop = ({
	item,
	top,
}: {
	item: EditorStarterItem;
	top: number;
}) => {
	return {
		...(item as EditorStarterItem),
		top,
	} as EditorStarterItem;
};

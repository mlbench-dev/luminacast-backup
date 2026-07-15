import {EditorStarterItem} from '../../items/item-type';

export const updateItemWidth = ({
	item,
	width,
	stopTextEditing,
}: {
	item: EditorStarterItem;
	width: number;
	stopTextEditing: boolean;
}) => {
	return {
		...item,
		width,
		...(item.type === 'text' && stopTextEditing ? {resizeOnEdit: false} : {}),
	} as EditorStarterItem;
};

export const updateItemHeight = ({
	item,
	height,
	stopTextEditing,
}: {
	item: EditorStarterItem;
	height: number;
	stopTextEditing: boolean;
}) => {
	return {
		...item,
		height,
		...(item.type === 'text' && stopTextEditing ? {resizeOnEdit: false} : {}),
	} as EditorStarterItem;
};

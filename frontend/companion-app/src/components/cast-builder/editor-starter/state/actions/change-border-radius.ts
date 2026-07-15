import {EditorStarterItem} from '../../items/item-type';
import {CanHaveBorderRadius} from '../../items/shared';
import {TextItem, TextItemBackground} from '../../items/text/text-item-type';

export const changeBorderRadius = ({
	item,
	borderRadius,
}: {
	item: EditorStarterItem;
	borderRadius: number;
}): EditorStarterItem => {
	return {
		...(item as CanHaveBorderRadius),
		borderRadius,
	} as EditorStarterItem;
};

export const changeBackgroundBorderRadius = ({
	item,
	borderRadius,
}: {
	item: EditorStarterItem;
	borderRadius: number;
}): EditorStarterItem => {
	return {
		...(item as TextItem),
		background: {
			...((item as TextItem).background as TextItemBackground),
			borderRadius,
		},
	} as EditorStarterItem;
};

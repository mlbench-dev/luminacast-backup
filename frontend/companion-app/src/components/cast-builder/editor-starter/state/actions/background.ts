import {TextItem} from '../../items/text/text-item-type';

export const removeBackground = ({item}: {item: TextItem}): TextItem => {
	if (!item.background) {
		return item;
	}

	return {
		...item,
		background: null,
	} as TextItem;
};

export const addBackground = ({item}: {item: TextItem}): TextItem => {
	if (item.background) {
		return item;
	}

	return {
		...item,
		background: {
			color: '#808080',
			horizontalPadding: item.fontSize / 2,
			borderRadius: item.fontSize / 4,
		},
	} as TextItem;
};

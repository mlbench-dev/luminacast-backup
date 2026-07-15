import {TextItem} from '../../items/text/text-item-type';
import {getTextDimensions} from '../../utils/text/measure-text';

export const editAndRelayoutText = (
	previousItem: TextItem,
	changeFn: () => TextItem,
): TextItem => {
	const newTextItem = changeFn();

	if (newTextItem === previousItem) {
		return previousItem;
	}

	const newDimensions = newTextItem.resizeOnEdit
		? getTextDimensions({
				text: newTextItem.text,
				fontFamily: newTextItem.fontFamily,
				fontSize: newTextItem.fontSize,
				lineHeight: newTextItem.lineHeight,
				letterSpacing: newTextItem.letterSpacing,
				fontStyle: newTextItem.fontStyle,
			})
		: {
				width: previousItem.width,
				height: previousItem.height,
			};

	let left = previousItem.left;

	if (newTextItem.align === 'right') {
		left = previousItem.left - (newDimensions.width - previousItem.width);
	}

	if (newTextItem.align === 'center') {
		left = previousItem.left - (newDimensions.width - previousItem.width) / 2;
	}

	if (
		newTextItem.width === newDimensions.width &&
		newTextItem.height === newDimensions.height &&
		newTextItem.left === left
	) {
		return newTextItem;
	}

	return {
		...newTextItem,
		width: newTextItem.resizeOnEdit ? newDimensions.width : newTextItem.width,
		height: newTextItem.resizeOnEdit
			? newDimensions.height
			: newTextItem.height,
		left,
	};
};

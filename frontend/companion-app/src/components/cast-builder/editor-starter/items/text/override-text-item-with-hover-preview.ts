import {editAndRelayoutText} from '../../state/actions/edit-and-relayout-text';
import {FontStyle, TextItem} from './text-item-type';

export type TextItemHoverPreview =
	| {
			itemId: string;
			type: 'font-family';
			fontFamily: string;
	  }
	| {
			itemId: string;
			type: 'font-style';
			fontStyle: FontStyle;
	  };

export const overrideTextItemWithHoverPreview = ({
	textItem,
	hoverPreview,
}: {
	textItem: TextItem;
	hoverPreview: TextItemHoverPreview | null;
}): TextItem => {
	if (hoverPreview === null) {
		return textItem;
	}
	if (hoverPreview.itemId !== textItem.id) {
		return textItem;
	}

	if (hoverPreview.type === 'font-family') {
		return editAndRelayoutText(textItem, () => {
			if (textItem.fontFamily === hoverPreview.fontFamily) {
				return textItem;
			}

			return {
				...textItem,
				fontFamily: hoverPreview.fontFamily,
			};
		});
	}

	if (hoverPreview.type === 'font-style') {
		return editAndRelayoutText(textItem, () => {
			if (
				textItem.fontStyle.variant === hoverPreview.fontStyle.variant &&
				textItem.fontStyle.weight === hoverPreview.fontStyle.weight
			) {
				return textItem;
			}

			return {
				...textItem,
				fontStyle: hoverPreview.fontStyle,
			};
		});
	}

	throw new Error(
		`Invalid hover preview type: ${JSON.stringify(hoverPreview satisfies never)}`,
	);
};

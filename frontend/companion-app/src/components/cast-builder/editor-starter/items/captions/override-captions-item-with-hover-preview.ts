import {TextItemHoverPreview} from '../text/override-text-item-with-hover-preview';
import {CaptionsItem} from './captions-item-type';

export const overrideCaptionsItemWithHoverPreview = (
	captionsItem: CaptionsItem,
	hoverPreview: TextItemHoverPreview | null,
): CaptionsItem => {
	if (hoverPreview === null) {
		return captionsItem;
	}

	if (hoverPreview.itemId !== captionsItem.id) {
		return captionsItem;
	}

	if (hoverPreview.type === 'font-family') {
		if (captionsItem.fontFamily === hoverPreview.fontFamily) {
			return captionsItem;
		}

		return {
			...captionsItem,
			fontFamily: hoverPreview.fontFamily,
		};
	}

	if (hoverPreview.type === 'font-style') {
		if (
			captionsItem.fontStyle.variant === hoverPreview.fontStyle.variant &&
			captionsItem.fontStyle.weight === hoverPreview.fontStyle.weight
		) {
			return captionsItem;
		}

		return {
			...captionsItem,
			fontStyle: hoverPreview.fontStyle,
		};
	}

	throw new Error(
		`Invalid hover preview type: ${JSON.stringify(hoverPreview satisfies never)}`,
	);
};

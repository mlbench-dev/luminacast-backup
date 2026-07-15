import {EditorStarterItem} from './item-type';
import {
	TextItemHoverPreview,
	overrideTextItemWithHoverPreview,
} from './text/override-text-item-with-hover-preview';

export const overrideItemWithHoverPreview = ({
	item,
	hoverPreview,
}: {
	item: EditorStarterItem;
	hoverPreview: TextItemHoverPreview | null;
}): EditorStarterItem => {
	if (item.type !== 'text') {
		return item;
	}

	if (hoverPreview === null) {
		return item;
	}

	return overrideTextItemWithHoverPreview({
		textItem: item,
		hoverPreview,
	});
};

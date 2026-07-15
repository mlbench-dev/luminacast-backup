import {EditorStarterItem} from '../../items/item-type';
import {TextItem} from '../../items/text/text-item-type';
import {editAndRelayoutText} from './edit-and-relayout-text';

export const editTextAction = ({
	item,
	editText,
}: {
	item: EditorStarterItem;
	editText: string;
}): TextItem => {
	if (item.type !== 'text') {
		throw new Error('Item is not a text item');
	}

	if (!item.resizeOnEdit) {
		return {
			...(item as TextItem),
			text: editText,
		};
	}

	return editAndRelayoutText(item, () => {
		return {
			...item,
			text: editText,
		};
	});
};

import {EditorStarterItem} from '../items/item-type';

export const parseItemsFromClipboardTextHtml = (
	text: string,
): EditorStarterItem[] | null => {
	const editorStarterHtml = text.match(
		/<div data-remotion-editor-starter[^>]*>(.*)<\/div>/,
	)?.[1];

	if (!editorStarterHtml) {
		return null;
	}

	const parsed = JSON.parse(editorStarterHtml);

	return parsed;
};

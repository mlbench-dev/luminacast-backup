import {FontInfo} from '@remotion/google-fonts/index';
import {GOOGLE_FONTS_DATABASE} from '../../data/google-fonts';
import {EditorStarterItem} from '../../items/item-type';

export const collectFontInfoFromItems = (items: EditorStarterItem[]) => {
	const fontInfos: Record<string, FontInfo> = {};

	for (const item of Object.values(items)) {
		if (item.type === 'text' || item.type === 'captions') {
			const info = GOOGLE_FONTS_DATABASE.find(
				(font) => font.fontFamily === item.fontFamily,
			);
			if (!info) {
				throw new Error(`Font ${item.fontFamily} not found`);
			}

			fontInfos[item.fontFamily] = info;
		} else if (
			// Type safety check, add item types here that don't have text here
			item.type === 'audio' ||
			item.type === 'gif' ||
			item.type === 'image' ||
			item.type === 'solid' ||
			item.type === 'video'
		) {
			continue;
		} else {
			throw new Error('Invalid item type: ' + (item satisfies never));
		}
	}

	return fontInfos;
};

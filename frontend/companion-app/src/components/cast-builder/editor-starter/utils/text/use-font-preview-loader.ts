import {useCallback, useMemo, useState} from 'react';
import {GOOGLE_FONTS_LIST} from '../../data/google-fonts-list';
import {FEATURE_FONT_FAMILY_DROPDOWN_RENDER_IN_FONT} from '../../flags';
import {loadFontPreview} from './load-font-preview';

// Global font loading state
const globalLoadedFonts = new Set<string>();

export const useFontPreviewLoader = () => {
	const [loadedFonts, setLoadedFonts] = useState(
		() => new Set(globalLoadedFonts),
	);

	const loadFontForPreview = useCallback(
		async (fontFamily: string): Promise<boolean> => {
			if (!FEATURE_FONT_FAMILY_DROPDOWN_RENDER_IN_FONT) {
				return true;
			}

			// Return immediately if font is already loaded
			if (globalLoadedFonts.has(fontFamily)) {
				return true;
			}

			const availableFonts = GOOGLE_FONTS_LIST;
			const fontInfo = availableFonts.find(
				(font) => font.fontFamily === fontFamily,
			);

			if (!fontInfo) {
				throw new Error(`Font ${fontFamily} not found in Google Fonts`);
			}

			await loadFontPreview(fontInfo.previewUrl, fontInfo.fontFamily);

			// Update global and local state
			globalLoadedFonts.add(fontFamily);
			setLoadedFonts((prev) => new Set([...prev, fontFamily]));

			return true;
		},
		[],
	);

	const memoized = useMemo(
		() => ({
			loadFontForPreview,
			loadedFonts,
		}),
		[loadFontForPreview, loadedFonts],
	);

	return memoized;
};

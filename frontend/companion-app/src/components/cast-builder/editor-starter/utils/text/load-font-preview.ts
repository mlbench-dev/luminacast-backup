export const makeFontPreviewName = (fontFamily: string) => {
	// If we re-use the same font family name, it might override the full font and
	// cause bad calculations.
	return `${fontFamily}Preview`;
};

export const loadFontPreview = async (
	previewName: string,
	fontFamily: string,
): Promise<void> => {
	const res = await fetch(
		`https://fonts.googleapis.com/css?family=${previewName}:400&text=${encodeURIComponent(fontFamily)}`,
	);
	if (!res.ok) {
		throw new Error(`Failed to load font preview for ${fontFamily}`);
	}
	const cssText = await res.text();
	const url = cssText.match(/url\((.*)\)\s/)![1];

	const fontFace = new FontFace(
		makeFontPreviewName(fontFamily),
		`url(${url})`,
		{
			weight: '400',
		},
	);
	await fontFace.load();
	document.fonts.add(fontFace);
};

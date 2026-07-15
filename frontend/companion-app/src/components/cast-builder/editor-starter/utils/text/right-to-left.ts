export const stringSeemsRightToLeft = (text: string): boolean => {
	// Check if the text contains any right-to-left characters
	const rtlChars =
		/[\u0590-\u05FF\u0600-\u06FF\u0700-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/;
	return rtlChars.test(text);
};

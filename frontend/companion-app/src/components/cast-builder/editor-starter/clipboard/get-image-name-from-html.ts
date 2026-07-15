export const getImageNameFromHtml = (html: string) => {
	const match = html.match(/<img[^>]+src="([^"]+)"/);
	if (!match) {
		return null;
	}
	return match[1].split('/').pop();
};

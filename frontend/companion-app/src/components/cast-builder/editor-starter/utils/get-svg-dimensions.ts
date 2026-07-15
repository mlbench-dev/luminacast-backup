export const getSvgDimensions = (svg: string) => {
	const parser = new DOMParser();
	const svgDoc = parser.parseFromString(svg, 'image/svg+xml');
	const svgElement = svgDoc.documentElement;

	// 3. Try to get width and height attributes
	const width = svgElement.getAttribute('width');
	const height = svgElement.getAttribute('height');

	if (width && height) {
		return {
			width: parseFloat(width),
			height: parseFloat(height),
		};
	}

	// 4. If not set, use viewBox
	const viewBox = svgElement.getAttribute('viewBox');
	if (!viewBox) {
		throw new Error('Failed to get SVG dimensions');
	}

	const viewBoxValues = viewBox.split(' ');
	const viewBoxWidth = viewBoxValues[2];
	const viewBoxHeight = viewBoxValues[3];

	if (!viewBoxWidth || !viewBoxHeight) {
		throw new Error('Failed to get SVG dimensions');
	}

	return {
		width: parseFloat(viewBoxWidth),
		height: parseFloat(viewBoxHeight),
	};
};

const BASE = Math.E / 4;
export const MIN_CANVAS_ZOOM = 0.05;
export const MAX_CANVAS_ZOOM = 10;

function logN(val: number) {
	return Math.log(val) / Math.log(BASE);
}

export const smoothenZoom = (input: number) => {
	return BASE ** (input - 1);
};

export const unsmoothenZoom = (input: number): number => {
	if (input < 0) {
		return MAX_CANVAS_ZOOM;
	}

	return Math.min(MAX_CANVAS_ZOOM, Math.max(MIN_CANVAS_ZOOM, logN(input) + 1));
};

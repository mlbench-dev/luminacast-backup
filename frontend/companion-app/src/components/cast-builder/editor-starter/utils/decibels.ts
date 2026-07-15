export const MAX_VOLUME_DB = 20;
export const MIN_VOLUME_DB = -60;

export const decibelToGain = (decibel: number) => {
	if (MIN_VOLUME_DB === decibel) {
		return 0;
	}

	return Math.pow(10, decibel / 20);
};

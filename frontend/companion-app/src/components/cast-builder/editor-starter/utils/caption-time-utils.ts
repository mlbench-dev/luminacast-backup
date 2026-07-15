/**
 * Converts milliseconds to a time string in MM:SS.mmm format
 * @param ms - Time in milliseconds
 * @returns Time string in MM:SS.mmm format
 */
export function millisecondsToTimeString(ms: number): string {
	const totalSeconds = ms / 1000;
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = Math.floor(totalSeconds % 60);
	const milliseconds = Math.round(ms % 1000);

	const minutesString = minutes.toString().padStart(2, '0');
	const secondsString = seconds.toString().padStart(2, '0');
	const millisecondsString = milliseconds.toString().padStart(3, '0');

	return `${minutesString}:${secondsString}.${millisecondsString}`;
}

/**
 * Parses a time string in MM:SS.mmm format back to milliseconds
 * @param timeString - Time string in MM:SS.mmm format
 * @returns Time in milliseconds, or null if invalid format
 */
export function timeStringToMilliseconds(timeString: string): number | null {
	// Match MM:SS.mmm format (requires 2 digits for minutes and seconds, 3 for milliseconds)
	const match = timeString.match(/^(\d{2}):(\d{2})\.(\d{3})$/);

	if (!match) {
		return null;
	}

	const minutes = parseInt(match[1], 10);
	const seconds = parseInt(match[2], 10);
	const milliseconds = parseInt(match[3], 10);

	// Validate ranges
	if (seconds >= 60 || milliseconds >= 1000) {
		return null;
	}

	return minutes * 60 * 1000 + seconds * 1000 + milliseconds;
}

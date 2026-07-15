export function secondsToTimeString(
	totalSeconds: number,
	includeMilliseconds: boolean = false,
) {
	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor((totalSeconds % 3600) / 60);
	const seconds = Math.trunc(totalSeconds % 60);
	const milliseconds = Math.round((totalSeconds % 1) * 1000);

	const hoursString = hours < 10 ? '0' + hours : hours.toString();
	const minutesString = minutes < 10 ? '0' + minutes : minutes.toString();
	const secondsString = seconds < 10 ? '0' + seconds : seconds.toString();
	const millisecondsString =
		milliseconds < 10
			? '00' + milliseconds
			: milliseconds < 100
				? '0' + milliseconds
				: milliseconds.toString();

	// Only show hours if >= 1 hour
	const timePrefix = hours > 0 ? `${hoursString}:` : '';

	// Construct time string based on the includeMilliseconds flag
	const timeString = includeMilliseconds
		? `${timePrefix}${minutesString}:${secondsString}.${millisecondsString}`
		: `${timePrefix}${minutesString}:${secondsString}`;

	return timeString;
}

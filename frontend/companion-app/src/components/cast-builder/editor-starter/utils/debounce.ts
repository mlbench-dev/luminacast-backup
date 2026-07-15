export function debounce(
	fn: (...args: any[]) => void,
	delay: number,
	{leading = false} = {},
) {
	let timeoutId: NodeJS.Timeout | undefined;
	let isLeadingInvoked = false;

	return (...args: unknown[]) => {
		clearTimeout(timeoutId);

		if (leading && !isLeadingInvoked) {
			fn(...args);
			isLeadingInvoked = true;
		}

		timeoutId = setTimeout(() => {
			if (!leading) fn(...args);
			isLeadingInvoked = false;
		}, delay);
	};
}

export const isEventTargetInputElement = (
	e: KeyboardEvent | ClipboardEvent,
) => {
	return (
		(e.target instanceof HTMLInputElement && e.target.type !== 'range') ||
		e.target instanceof HTMLTextAreaElement ||
		e.target instanceof HTMLSelectElement
	);
};

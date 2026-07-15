const key = 'remotion-editor-starter.loop';

export const DEFAULT_LOOP = false;

export const loadLoop = () => {
	if (typeof localStorage === 'undefined') {
		return DEFAULT_LOOP;
	}

	const value = localStorage.getItem(key);
	if (value === null) {
		return DEFAULT_LOOP;
	}
	return value === 'true';
};

export const saveLoop = (value: boolean) => {
	localStorage.setItem(key, value.toString());
};

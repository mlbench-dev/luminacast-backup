const key = 'remotion-editor-starter.snapping-enabled';

export const DEFAULT_SNAPPING_ENABLED = true;

export const loadSnappingEnabled = () => {
	if (typeof localStorage === 'undefined') {
		return DEFAULT_SNAPPING_ENABLED;
	}

	const value = localStorage.getItem(key);
	if (value === null) {
		return DEFAULT_SNAPPING_ENABLED;
	}
	return value === 'true';
};

export const saveSnappingEnabled = (enabled: boolean) => {
	localStorage.setItem(key, enabled.toString());
};

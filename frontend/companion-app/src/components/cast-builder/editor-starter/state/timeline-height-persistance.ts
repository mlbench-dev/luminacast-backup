import {clamp} from '../utils/clamp';

const key = 'remotion-editor-starter.timeline-height';

export const DEFAULT_TIMELINE_HEIGHT = 200;

const loadUnboundedTimelineHeight = () => {
	if (typeof localStorage === 'undefined') {
		return DEFAULT_TIMELINE_HEIGHT;
	}

	const value = localStorage.getItem(key);
	if (value === null) {
		return DEFAULT_TIMELINE_HEIGHT;
	}

	return parseInt(value);
};

export const loadTimelineHeight = () => {
	return clamp({
		value: loadUnboundedTimelineHeight(),
		min: getMinTimelineHeight(),
		max: getMaxTimelineHeight(),
	});
};

export const saveTimelineHeight = (value: number) => {
	localStorage.setItem(key, value.toString());
};

// Arbitrary minimum and maximum values, feel free to customize them
export const getMinTimelineHeight = () => 100;
export const getMaxTimelineHeight = () => {
	if (typeof window === 'undefined') {
		return Infinity;
	}

	return window.innerHeight - 200;
};

import {useMemo} from 'react';
import {renderFrame} from '../../utils/render-frame';
import {useFps} from '../../utils/use-context';

export type TimelineTickMark = {
	width: number;
	label: string;
};

// Base intervals in seconds that we want to show at different zoom levels
const BASE_INTERVALS = [
	0.01, 0.025, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60, 300,
] as const;

// The number of time divisions we want to see across the timeline at zoom level 1
// Example: If DESIRED_TIMELINE_DIVISIONS = 20 and video is 60 seconds:
// - At zoom=1: Show a tick every 3 seconds (60/20)
// - At zoom=2: Show a tick every 1.5 seconds (60/(20*2))
// - At zoom=4: Show a tick every 0.75 seconds (60/(20*4))
const DESIRED_TIMELINE_DIVISIONS = 15;

const findBestTimeInterval = ({
	totalDurationInFrames,
	fps,
	timelineWidth,
	containerWidth,
}: {
	totalDurationInFrames: number;
	fps: number;
	timelineWidth: number;
	containerWidth: number;
}) => {
	const durationInSeconds = totalDurationInFrames / fps;
	// Calculate the effective zoom multiplier from the actual timeline width
	const effectiveZoomMultiplier = timelineWidth / containerWidth;
	const baseInterval =
		durationInSeconds / (DESIRED_TIMELINE_DIVISIONS * effectiveZoomMultiplier);

	const interval =
		BASE_INTERVALS.find((int) => int >= baseInterval) ??
		BASE_INTERVALS[BASE_INTERVALS.length - 1];

	return interval;
};

const formatTimecode = (frame: number, fps: number, interval: number) => {
	const totalSeconds = frame / fps;
	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor((totalSeconds % 3600) / 60);
	const seconds = Math.floor(totalSeconds % 60);

	// Format: HH:MM:SS (with hours) or MM:SS
	if (hours > 0) {
		return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
	}

	const base = `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;

	// Show frames for small intervals (≤ 0.1 seconds)
	if (interval <= 0.1) {
		return renderFrame(frame, fps);
	}

	// Show frames for half-second intervals (legacy behavior)
	if (interval === 0.5) {
		return renderFrame(frame, fps);
	}

	return base;
};

export const useTicks = ({
	visibleFrames,
	timelineWidth,
	containerWidth,
}: {
	visibleFrames: number;
	timelineWidth: number;
	containerWidth: number;
}) => {
	const {fps} = useFps();

	if (timelineWidth === null) {
		throw new Error('Timeline width is null');
	}

	const tickMarks = useMemo(() => {
		const interval = findBestTimeInterval({
			totalDurationInFrames: visibleFrames,
			fps,
			timelineWidth,
			containerWidth,
		});

		const marks: TimelineTickMark[] = [];
		const pxPerSecond = timelineWidth / (visibleFrames / fps);
		const pixelsBetweenTicks = interval * pxPerSecond;

		for (
			let xPosition = 0;
			xPosition <= timelineWidth;
			xPosition += pixelsBetweenTicks
		) {
			const seconds = xPosition / pxPerSecond;
			const frame = Math.round(seconds * fps);
			marks.push({
				width: pixelsBetweenTicks,
				label: formatTimecode(frame, fps, interval),
			});
		}
		return marks;
	}, [fps, timelineWidth, visibleFrames, containerWidth]);

	return {tickMarks, timelineWidth};
};

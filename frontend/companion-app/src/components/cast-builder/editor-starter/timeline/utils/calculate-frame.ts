import {TIMELINE_HORIZONTAL_PADDING} from '../../constants';

export const calculateFrame = ({
	container,
	xCoordinate,
	totalDurationInFrames,
	timelineWidth,
}: {
	container: HTMLDivElement;
	xCoordinate: number;
	totalDurationInFrames: number;
	timelineWidth: number;
}) => {
	const containerRect = container.getBoundingClientRect();
	if (!containerRect) {
		throw new Error('boundingRect is null');
	}

	const pixelsPerFrame = timelineWidth / totalDurationInFrames;

	// Calculate the actual click position considering scroll and reserved space
	const scrollX = container.scrollLeft;
	const clickPositionX =
		xCoordinate - containerRect.x + scrollX - TIMELINE_HORIZONTAL_PADDING;

	// Convert click position to frame number using pixels per frame
	const frame = clickPositionX / pixelsPerFrame;

	const normalizedFrame = Math.max(
		0,
		Math.min(Math.round(frame), totalDurationInFrames - 1),
	);

	// Ensure frame is within valid range
	return normalizedFrame;
};

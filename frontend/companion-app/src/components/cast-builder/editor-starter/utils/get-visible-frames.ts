export const MIN_TIMELINE_DURATION_IN_SECONDS = 10;

export const getVisibleFrames = ({
	fps,
	totalDurationInFrames,
}: {
	fps: number;
	totalDurationInFrames: number;
}) => {
	return Math.max(
		MIN_TIMELINE_DURATION_IN_SECONDS * fps,
		Math.round(totalDurationInFrames * 1.25),
	);
};

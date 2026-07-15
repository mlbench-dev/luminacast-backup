import {fadeEasingFunction} from '../../utils/fade-easing';

export const calculateFadeInOpacity = ({
	currentFrame,
	fadeInDurationInSeconds,
	framesPerSecond,
}: {
	currentFrame: number;
	fadeInDurationInSeconds: number;
	framesPerSecond: number;
}): number => {
	if (fadeInDurationInSeconds === 0) {
		return 1;
	}
	const progress = Math.min(
		1,
		currentFrame / (fadeInDurationInSeconds * framesPerSecond),
	);
	return fadeEasingFunction(progress);
};

export const calculateFadeOutOpacity = ({
	currentFrame,
	fadeOutDurationInSeconds,
	framesPerSecond,
	totalDurationInFrames,
}: {
	currentFrame: number;
	fadeOutDurationInSeconds: number;
	framesPerSecond: number;
	totalDurationInFrames: number;
}): number => {
	if (fadeOutDurationInSeconds === 0) {
		return 1;
	}
	const fadeOutStartFrame =
		totalDurationInFrames - fadeOutDurationInSeconds * framesPerSecond;
	const progress = Math.max(
		0,
		1 -
			(currentFrame - fadeOutStartFrame) /
				(fadeOutDurationInSeconds * framesPerSecond),
	);
	return fadeEasingFunction(progress);
};

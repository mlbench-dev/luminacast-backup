import {Easing, interpolate} from 'remotion';
import {fadeEasingFunction} from './fade-easing';

const getFadeInMultiplier = (frame: number, fadeInFrames: number): number => {
	if (fadeInFrames <= 0) return 1;
	return interpolate(frame, [0, fadeInFrames], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
		easing: fadeEasingFunction,
	});
};

const getFadeOutMultiplier = (
	frame: number,
	fadeOutStartFrame: number,
	durationInFrames: number,
): number => {
	if (fadeOutStartFrame >= durationInFrames) return 1;
	return interpolate(frame, [fadeOutStartFrame, durationInFrames], [1, 0], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
		easing: Easing.out(fadeEasingFunction),
	});
};

export const getAudioFadeMultiplier = ({
	frame,
	fadeInFrames,
	fadeOutFrames,
	durationInFrames,
}: {
	frame: number;
	fadeInFrames: number;
	fadeOutFrames: number;
	durationInFrames: number;
}): number => {
	if (durationInFrames <= 0) return 0;
	if (fadeInFrames <= 0 && fadeOutFrames <= 0) return 1;

	const clampedFadeIn = Math.min(fadeInFrames, durationInFrames);
	const clampedFadeOut = Math.min(fadeOutFrames, durationInFrames);
	const fadeOutStartFrame = durationInFrames - clampedFadeOut;

	const fadeInMultiplier = getFadeInMultiplier(frame, clampedFadeIn);
	const fadeOutMultiplier = getFadeOutMultiplier(
		frame,
		fadeOutStartFrame,
		durationInFrames,
	);

	return fadeInMultiplier * fadeOutMultiplier;
};

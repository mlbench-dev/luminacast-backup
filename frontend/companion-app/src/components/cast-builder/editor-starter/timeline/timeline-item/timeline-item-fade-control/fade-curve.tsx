import React, {useMemo} from 'react';
import {EditorStarterItem} from '../../../items/item-type';
import {useFps} from '../../../utils/use-context';
import {FadeCurveLine} from './fade-curve-line';
import {getFadeValue} from './get-fade-value';
import {FadeMediaType} from './item-fade-handles';

export const MIN_FADE_WIDTH = 6;

export const calculateFadeWidthPx = (
	duration: number,
	totalDuration: number,
	width: number,
): number => {
	if (totalDuration === 0) return 0;
	return Math.max(MIN_FADE_WIDTH, (duration / totalDuration) * width);
};

export const calculateTotalItemDuration = (
	item: EditorStarterItem,
	fps: number,
): number => {
	return item.durationInFrames / fps;
};

export const FadeCurve: React.FC<{
	item: EditorStarterItem;
	height: number;
	width: number;
	fadeType: FadeMediaType;
}> = ({item, height, width, fadeType}) => {
	const {fps} = useFps();

	const fadeInDuration = getFadeValue({
		item,
		fadeProperty:
			fadeType === 'audio'
				? 'audioFadeInDurationInSeconds'
				: 'fadeInDurationInSeconds',
	});

	const totalDuration = useMemo(
		() => calculateTotalItemDuration(item, fps),
		[item, fps],
	);

	const fadeInWidthPx = useMemo(
		() => calculateFadeWidthPx(fadeInDuration, totalDuration, width),
		[fadeInDuration, totalDuration, width],
	);

	const inCurveContainerStyle: React.CSSProperties = useMemo(
		() => ({
			position: 'absolute',
			left: 0,
			top: 0,
			width: fadeInWidthPx,
			height: height,
		}),
		[fadeInWidthPx, height],
	);

	const fadeOutDuration = getFadeValue({
		item,
		fadeProperty:
			fadeType === 'audio'
				? 'audioFadeOutDurationInSeconds'
				: 'fadeOutDurationInSeconds',
	});
	const fadeOutWidthPx = useMemo(
		() => calculateFadeWidthPx(fadeOutDuration, totalDuration, width),
		[fadeOutDuration, totalDuration, width],
	);

	const outCurveContainerStyle: React.CSSProperties = useMemo(
		() => ({
			position: 'absolute',
			right: 0,
			top: 0,
			width: fadeOutWidthPx,
			height: height,
		}),
		[fadeOutWidthPx, height],
	);

	const color = fadeType === 'audio' ? 'black' : 'rgba(0, 0, 0, 0.8)';

	return (
		<>
			{fadeInDuration > 0 && (
				<div style={inCurveContainerStyle}>
					<FadeCurveLine
						backgroundColor={color}
						type="in"
						width={fadeInWidthPx}
						height={height}
					/>
				</div>
			)}
			{fadeOutDuration > 0 && (
				<div style={outCurveContainerStyle}>
					<FadeCurveLine
						backgroundColor={color}
						type="out"
						width={fadeOutWidthPx}
						height={height}
					/>
				</div>
			)}
		</>
	);
};

import React, {useMemo} from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {
	calculateFadeInOpacity,
	calculateFadeOutOpacity,
} from '../video/calculate-fade';
import {SolidItem} from './solid-item-type';

export const SolidLayer = ({item}: {item: SolidItem}) => {
	const frame = useCurrentFrame();
	const {fps, durationInFrames} = useVideoConfig();

	const opacity = useMemo(() => {
		const inOpacity = calculateFadeInOpacity({
			currentFrame: frame,
			fadeInDurationInSeconds: item.fadeInDurationInSeconds,
			framesPerSecond: fps,
		});
		const outOpacity = calculateFadeOutOpacity({
			currentFrame: frame,
			fadeOutDurationInSeconds: item.fadeOutDurationInSeconds,
			framesPerSecond: fps,
			totalDurationInFrames: durationInFrames,
		});
		return inOpacity * outOpacity * item.opacity;
	}, [
		item.fadeInDurationInSeconds,
		fps,
		frame,
		item.opacity,
		durationInFrames,
		item.fadeOutDurationInSeconds,
	]);

	const style: React.CSSProperties = useMemo(() => {
		return {
			backgroundColor: item.color,
			position: 'absolute',
			left: item.left,
			top: item.top,
			width: item.width,
			height: item.height,
			opacity,
			borderRadius: item.borderRadius,
			transform: `rotate(${item.rotation}deg)`,
		};
	}, [
		item.color,
		item.height,
		item.left,
		item.top,
		item.width,
		opacity,
		item.borderRadius,
		item.rotation,
	]);

	return <div style={style}></div>;
};

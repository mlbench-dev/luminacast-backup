import {Gif} from '@remotion/gif';
import React, {useMemo} from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import {RequireCachedAsset} from '../../caching/require-cached-asset';
import {usePreferredLocalUrl} from '../../utils/find-asset-by-id';
import {useAssetFromItem} from '../../utils/use-context';
import {useCroppableLayer} from '../croppable-layer';
import {
	calculateFadeInOpacity,
	calculateFadeOutOpacity,
} from '../video/calculate-fade';
import {GifItem} from './gif-item-type';

export const GifLayer: React.FC<{
	item: GifItem;
	cropBackground: boolean;
}> = ({item, cropBackground}) => {
	if (item.type !== 'gif') {
		throw new Error('Item is not a gif');
	}

	const frame = useCurrentFrame();
	const {fps, durationInFrames} = useVideoConfig();
	const asset = useAssetFromItem(item);

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

	const {innerStyle, outerStyle} = useCroppableLayer({
		item,
		rotation: item.rotation,
		opacity,
		borderRadius: item.borderRadius,
		cropBackground,
	});

	const src = usePreferredLocalUrl(asset);

	return (
		<div style={outerStyle}>
			<RequireCachedAsset asset={asset}>
				<Gif style={innerStyle} src={src} playbackRate={item.playbackRate} />
			</RequireCachedAsset>
		</div>
	);
};

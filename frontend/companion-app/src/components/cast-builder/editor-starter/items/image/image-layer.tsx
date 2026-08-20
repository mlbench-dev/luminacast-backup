import React, {useMemo} from 'react';
import {Img, useCurrentFrame, useVideoConfig} from 'remotion';
import {RequireCachedAsset} from '../../caching/require-cached-asset';
import {usePreferredLocalUrl} from '../../utils/find-asset-by-id';
import {useAssetFromItem} from '../../utils/use-context';
import {useCroppableLayer} from '../croppable-layer';
import {
	calculateFadeInOpacity,
	calculateFadeOutOpacity,
} from '../video/calculate-fade';
import {ImageItem} from './image-item-type';

const ImageItemUnmemoized: React.FC<{
	item: ImageItem;
	cropBackground: boolean;
}> = ({item, cropBackground}) => {
	if (item.type !== 'image') {
		throw new Error('Item is not an image');
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
				<Img
					crossOrigin="anonymous"
					// pauseWhenLoading (disabled for CDN latency)
					style={innerStyle}
					src={src}
				/>
			</RequireCachedAsset>
			{item.metadata?.is_motion_placeholder ? (
				<div
					style={{
						position: 'absolute',
						left: 8,
						bottom: 8,
						padding: '3px 8px',
						borderRadius: 4,
						background: 'rgba(0,0,0,0.65)',
						color: '#fff',
						fontSize: 11,
						lineHeight: 1.3,
						fontFamily: 'sans-serif',
						pointerEvents: 'none',
					}}
				>
					Preview only — motion appears after render
				</div>
			) : null}
		</div>
	);
};

export const ImageLayer = React.memo(ImageItemUnmemoized);

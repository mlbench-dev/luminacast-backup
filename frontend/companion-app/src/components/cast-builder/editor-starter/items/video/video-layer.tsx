import {Video} from '@remotion/media';
import {useMemo} from 'react';
import {OffthreadVideo, useCurrentFrame, useVideoConfig} from 'remotion';
import {RequireCachedAsset} from '../../caching/require-cached-asset';
import {FEATURE_NEW_MEDIA_TAGS} from '../../flags';
import {usePreferredLocalUrl} from '../../utils/find-asset-by-id';
import {useAssetFromItem} from '../../utils/use-context';
import {volumeFn} from '../../utils/volume-fn';
import {useCroppableLayer} from '../croppable-layer';
import {
	calculateFadeInOpacity,
	calculateFadeOutOpacity,
} from './calculate-fade';
import {VideoItem} from './video-item-type';

export const VideoLayer = ({
	item,
	trackMuted,
	cropBackground,
}: {
	item: VideoItem;
	trackMuted: boolean;
	cropBackground: boolean;
}) => {
	if (item.type !== 'video') {
		throw new Error('Item is not a video');
	}

	const frame = useCurrentFrame();
	const {fps, durationInFrames} = useVideoConfig();

	const volume = useMemo(() => {
		return volumeFn({
			fps,
			audioFadeInDurationInSeconds: item.audioFadeInDurationInSeconds,
			audioFadeOutDurationInSeconds: item.audioFadeOutDurationInSeconds,
			durationInFrames: item.durationInFrames,
			decibelAdjustment: item.decibelAdjustment,
		});
	}, [
		item.audioFadeInDurationInSeconds,
		item.audioFadeOutDurationInSeconds,
		item.decibelAdjustment,
		item.durationInFrames,
		fps,
	]);

	const asset = useAssetFromItem(item);
	const src = usePreferredLocalUrl(asset);

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

	const {innerStyle, outerStyle, backdropStyle} = useCroppableLayer({
		item,
		rotation: item.rotation,
		opacity,
		borderRadius: item.borderRadius,
		cropBackground,
	});

	const startFrom = item.videoStartFromInSeconds * fps;

	if (FEATURE_NEW_MEDIA_TAGS) {
		return (
			<div style={outerStyle}>
				<RequireCachedAsset asset={asset}>
					{backdropStyle && (
						<Video
							volume={0}
							trimBefore={startFrom}
							src={src}
							style={backdropStyle}
							muted
							playbackRate={item.playbackRate}
							aria-hidden
						/>
					)}
					<Video
						volume={volume}
						trimBefore={startFrom}
						src={src}
						style={innerStyle}
						muted={trackMuted}
						playbackRate={item.playbackRate}
					/>
				</RequireCachedAsset>
			</div>
		);
	}

	return (
		<div style={outerStyle}>
			<RequireCachedAsset asset={asset}>
				{backdropStyle && (
					<OffthreadVideo
						volume={0}
						trimBefore={startFrom}
						src={src}
						style={backdropStyle}
						muted
						playbackRate={item.playbackRate}
						crossOrigin="anonymous"
						aria-hidden
					/>
				)}
				<OffthreadVideo
					volume={volume}
					trimBefore={startFrom}
					src={src}
					style={innerStyle}
					muted={trackMuted}
					playbackRate={item.playbackRate}
					useWebAudioApi
					crossOrigin="anonymous"
					// pauseWhenBuffering (disabled for CDN latency)
				/>
			</RequireCachedAsset>
		</div>
	);
};

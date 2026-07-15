import React, {useEffect, useMemo, useRef, useState} from 'react';
import {FEATURE_AUDIO_FADE_CONTROL} from '../../../flags';
import {AudioItem} from '../../../items/audio/audio-item-type';
import {EditorStarterItem} from '../../../items/item-type';
import {VideoItem} from '../../../items/video/video-item-type';
import {clsx} from '../../../utils/clsx';
import {usePreferredLocalUrl} from '../../../utils/find-asset-by-id';
import {getVisibleFrames} from '../../../utils/get-visible-frames';
import {
	useAssetFromItem,
	useFps,
	useTimelineContext,
} from '../../../utils/use-context';
import {FadeCurve} from '../timeline-item-fade-control/fade-curve';
import {drawPeaks} from './draw-peaks';
import {loadWaveformPeaks} from './load-waveform-peaks';

export const WAVEFORM_HEIGHT = 20;

export const getWaveformHeight = ({
	item,
	trackHeight,
}: {
	item: EditorStarterItem;
	trackHeight: number;
}) => {
	if (item.type === 'audio') {
		return trackHeight;
	}

	// Type safety check, add item types that don't have a waveform here
	if (
		item.type === 'video' ||
		item.type === 'captions' ||
		item.type === 'gif' ||
		item.type === 'text' ||
		item.type === 'solid' ||
		item.type === 'image'
	) {
		return WAVEFORM_HEIGHT;
	}

	throw new Error('Invalid item type: ' + (item satisfies never));
};

const WaveformCanvas = ({
	peaks,
	color,
	width,
	height,
	leftOffset,
	volume,
}: {
	peaks: Float32Array;
	color: string;
	width: number;
	height: number;
	leftOffset: number;
	volume: number;
}) => {
	const canvasRef = useRef<HTMLCanvasElement>(null);

	const style = useMemo(() => ({marginLeft: leftOffset}), [leftOffset]);

	useEffect(() => {
		const {current: canvasElement} = canvasRef;
		if (!canvasElement) return;

		canvasElement.width = width;
		canvasElement.height = height;

		drawPeaks(canvasElement, peaks, color, volume, width);
	}, [color, peaks, volume, width, height]);

	return (
		<canvas className="pointer-events-none" ref={canvasRef} style={style} />
	);
};

function TimelineWaveformBackground({
	children,
	waveformHeight,
	trackMuted,
}: {
	children?: React.ReactNode;
	waveformHeight: number;
	trackMuted: boolean;
}) {
	const style = useMemo(() => ({height: waveformHeight}), [waveformHeight]);

	return (
		<div
			className="bg-editor-starter-panel group/timeline-item absolute bottom-0 flex w-full items-center overflow-hidden"
			style={style}
		>
			<div
				className={clsx('relative h-full w-full', trackMuted && 'opacity-30')}
			>
				{children}
			</div>
		</div>
	);
}

export const TimelineItemWaveform = ({
	item,
	children,
	trackHeight,
	timelineWidth,
	roundedDifference,
	trackMuted,
}: {
	item: AudioItem | VideoItem;
	children: React.ReactNode;
	trackHeight: number;
	timelineWidth: number;
	roundedDifference: number;
	trackMuted: boolean;
}) => {
	const [peaks, setPeaks] = useState<Float32Array | null>(null);

	const {fps} = useFps();
	const {durationInFrames} = useTimelineContext();
	const visibleFrames = getVisibleFrames({
		fps: fps,
		totalDurationInFrames: durationInFrames,
	});
	const pixelsPerFrame = timelineWidth / visibleFrames;

	const asset = useAssetFromItem(item);

	const url = usePreferredLocalUrl(asset);
	const [initialUrl] = useState(url);

	useEffect(() => {
		const controller = new AbortController();

		loadWaveformPeaks(initialUrl, controller.signal)
			.then((p) => {
				if (!controller.signal.aborted) {
					setPeaks(p);
				}
			})
			// eslint-disable-next-line no-console
			.catch(console.error);

		return () => controller.abort();
	}, [initialUrl]);

	if (asset.type !== 'audio' && asset.type !== 'video') {
		throw new Error('Asset is not an audio or video');
	}

	// Total frames in the underlying media
	const totalFrames = asset.durationInSeconds * fps;

	const waveformWidthPx = (totalFrames / item.playbackRate) * pixelsPerFrame;

	const startOffsetFrames =
		item.type === 'audio'
			? item.audioStartFromInSeconds * fps
			: item.videoStartFromInSeconds * fps;

	const marginLeft =
		-(startOffsetFrames / item.playbackRate) * pixelsPerFrame -
		roundedDifference;

	const volume = item.decibelAdjustment;

	const waveformHeight = getWaveformHeight({
		item,
		trackHeight,
	});

	const itemWidthPx = item.durationInFrames * pixelsPerFrame;

	const height = getWaveformHeight({
		item,
		trackHeight,
	});

	if (!peaks) {
		return (
			<TimelineWaveformBackground
				waveformHeight={waveformHeight}
				trackMuted={trackMuted}
			/>
		);
	}

	return (
		<TimelineWaveformBackground
			waveformHeight={waveformHeight}
			trackMuted={trackMuted}
		>
			{FEATURE_AUDIO_FADE_CONTROL && (
				<>
					<FadeCurve
						item={item}
						height={waveformHeight}
						width={itemWidthPx}
						fadeType="audio"
					/>
				</>
			)}
			<WaveformCanvas
				peaks={peaks}
				color="grey"
				width={waveformWidthPx}
				height={height}
				leftOffset={marginLeft}
				volume={volume}
			/>
			{children}
		</TimelineWaveformBackground>
	);
};

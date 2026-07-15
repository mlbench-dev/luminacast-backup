import React, {useLayoutEffect, useRef, useState} from 'react';
import {VideoItem} from '../../../items/video/video-item-type';
import {usePreferredLocalUrl} from '../../../utils/find-asset-by-id';
import {
	clearOldFrames,
	FrameDatabaseKey,
	getFrameFromFrameDatabase,
	getKeysFromFrameDatabase,
	getTimestampFromFrameDatabaseKey,
	setFrameInFrameDatabase,
} from '../../../utils/frame-database';
import {getVisibleFrames} from '../../../utils/get-visible-frames';
import {resizeVideoFrame} from '../../../utils/resize-video-frame';
import {useAssetFromItem, useTimelineContext} from '../../../utils/use-context';
import {useTimelineSize} from '../../utils/use-timeline-size';
import {extractFrames, useInitializationPromise} from './extract-frames';
import {watchVisibleSlots} from './watch-visible-slots';

const WEBCODECS_TIMESCALE = 1_000_000;

// Keeping this value outside of the component to not cause bugs when moving across displays.
const DEVICE_PIXEL_RATIO =
	typeof window !== 'undefined' ? window.devicePixelRatio : 1;

// May be higher if there is a video item and it has no audio, then it will fill out the item height.
export const FILMSTRIP_HEIGHT_IF_THERE_IS_AUDIO = 46;

const getDurationOfOneImage = ({
	aspectRatio,
	pxPerSecond,
	height,
}: {
	aspectRatio: number;
	pxPerSecond: number;
	height: number;
}) => {
	const widthOfOneFrame = height * aspectRatio;
	return Math.round((widthOfOneFrame / pxPerSecond) * WEBCODECS_TIMESCALE);
};

const fixRounding = (value: number) => {
	if (value % 1 >= 0.49999999) {
		return Math.ceil(value);
	}

	return Math.floor(value);
};

export const getMaxDistance = (durationOfOneImage: number) => {
	return (durationOfOneImage / 2) * 3;
};

const calculateTimestampSlots = ({
	fromSeconds,
	toSeconds,
	aspectRatio,
	height,
	pxPerSecond,
	durationOfOneImage,
}: {
	fromSeconds: number;
	toSeconds: number;
	aspectRatio: number;
	height: number;
	pxPerSecond: number;
	durationOfOneImage: number;
}) => {
	const widthOfOneFrame = height * aspectRatio;
	const width = (toSeconds - fromSeconds) * pxPerSecond;
	const framesFitInWidth = Math.ceil(width / widthOfOneFrame + 0.5) + 1;

	const timestampTargets: number[] = [];

	const getTarget = (i: number) => {
		return fromSeconds * WEBCODECS_TIMESCALE + durationOfOneImage * i;
	};

	const getSnappedToDuration = (i: number) => {
		const target = getTarget(i);
		return fixRounding(target / durationOfOneImage) * durationOfOneImage;
	};

	for (let i = 0; i < framesFitInWidth + 1; i++) {
		const snappedToDuration = getSnappedToDuration(i);
		timestampTargets.push(snappedToDuration + 0.5 * durationOfOneImage);
	}

	return {
		timestampTargets,
		firstIndex: fixRounding(getTarget(0) / durationOfOneImage) - 1,
	};
};

const ensureSlots = ({
	filledSlots,
	timestampTargets,
}: {
	filledSlots: Map<number, number | undefined>;
	timestampTargets: number[];
}) => {
	for (const timestamp of timestampTargets) {
		if (!filledSlots.has(timestamp)) {
			filledSlots.set(timestamp, undefined);
		}
	}
};

const drawSlot = ({
	frame,
	ctx,
	filledSlots,
	timestamp,
	slotIndex,
	setAsFilled,
}: {
	frame: VideoFrame;
	ctx: CanvasRenderingContext2D;
	filledSlots: Map<number, number | undefined>;
	timestamp: number;
	slotIndex: number;
	setAsFilled: boolean;
}) => {
	const thumbnailWidth = frame.displayWidth / DEVICE_PIXEL_RATIO;
	const left = Math.floor(slotIndex * thumbnailWidth);
	const right = Math.ceil((slotIndex + 1) * thumbnailWidth);

	ctx.drawImage(
		frame,
		left,
		0,
		right - left,
		frame.displayHeight / DEVICE_PIXEL_RATIO,
	);

	if (setAsFilled) {
		filledSlots.set(timestamp, frame.timestamp);
	}
};

const getBestFrameFromDatabase = ({
	keys,
	timestamp,
}: {
	keys: FrameDatabaseKey[];
	timestamp: number;
}) => {
	let bestKey: FrameDatabaseKey | undefined;
	let bestDistance = Infinity;
	for (const key of keys) {
		const distance = Math.abs(
			getTimestampFromFrameDatabaseKey(key) - timestamp,
		);
		if (distance < bestDistance) {
			bestDistance = distance;
			bestKey = key;
		}
	}

	if (!bestKey) {
		return null;
	}
	return {bestKey, bestDistance};
};

const fillWithBestPossibleFrame = ({
	keys,
	timestamp,
	filledSlots,
	ctx,
	slotIndex,
	maxDistanceInWebCodecsUnits,
}: {
	keys: FrameDatabaseKey[];
	timestamp: number;
	filledSlots: Map<number, number | undefined>;
	ctx: CanvasRenderingContext2D;
	slotIndex: number;
	maxDistanceInWebCodecsUnits: number;
}) => {
	const best = getBestFrameFromDatabase({
		keys,
		timestamp,
	});

	if (!best) {
		return;
	}

	const frame = getFrameFromFrameDatabase(best.bestKey);
	if (!frame) {
		throw new Error('Frame not found');
	}

	const alreadyFilled = filledSlots.get(timestamp);

	// Don't fill if a closer frame was already drawn
	if (
		alreadyFilled !== undefined &&
		Math.abs(alreadyFilled - timestamp) <=
			Math.abs(frame.frame.timestamp - timestamp)
	) {
		return;
	}

	frame.lastUsed = Date.now();

	drawSlot({
		ctx,
		frame: frame.frame,
		filledSlots,
		timestamp,
		slotIndex,
		setAsFilled: best.bestDistance <= maxDistanceInWebCodecsUnits,
	});
};

const fillWithCachedFrames = ({
	ctx,
	filledSlots,
	src,
	slotsToFill,
	maxDistanceInWebCodecsUnits,
	visibleSlots,
}: {
	ctx: CanvasRenderingContext2D;
	filledSlots: Map<number, number | undefined>;
	src: string;
	slotsToFill: number[];
	maxDistanceInWebCodecsUnits: number;
	visibleSlots: readonly [number, number];
}) => {
	const keys = getKeysFromFrameDatabase().filter((k) => k.startsWith(src));

	for (const timestamp of slotsToFill) {
		const slotIndex = slotsToFill.indexOf(timestamp);
		if (slotIndex < visibleSlots[0] || slotIndex > visibleSlots[1]) {
			continue;
		}

		fillWithBestPossibleFrame({
			keys,
			timestamp,
			filledSlots,
			ctx,
			slotIndex: slotsToFill.indexOf(timestamp),
			maxDistanceInWebCodecsUnits,
		});
	}
};

const fillFrameWhereItFits = ({
	frame,
	filledSlots,
	ctx,
	durationOfOneImage,
	visibleSlots,
}: {
	frame: VideoFrame;
	filledSlots: Map<number, number | undefined>;
	ctx: CanvasRenderingContext2D;
	durationOfOneImage: number;
	visibleSlots: readonly [number, number];
}) => {
	const slots = Array.from(filledSlots.keys());
	const maxDistanceInWebCodecsUnits = getMaxDistance(durationOfOneImage);

	for (let i = 0; i < slots.length; i++) {
		if (i < visibleSlots[0] || i > visibleSlots[1]) {
			continue;
		}

		const slot = slots[i];
		const doesSatisfyMaxTimeDeviation =
			Math.abs(slot - frame.timestamp) <= maxDistanceInWebCodecsUnits;
		if (!doesSatisfyMaxTimeDeviation) {
			continue;
		}

		const filled = filledSlots.get(slot);
		// Don't fill if a better timestamp was already filled
		if (
			filled !== undefined &&
			Math.abs(filled - slot) <= Math.abs(filled - frame.timestamp)
		) {
			continue;
		}

		drawSlot({
			ctx,
			frame,
			filledSlots,
			timestamp: slot,
			slotIndex: i,
			setAsFilled: doesSatisfyMaxTimeDeviation,
		});
	}
};

const saveFrameToDatabase = ({
	frame,
	src,
	scale,
	timestamp,
}: {
	frame: VideoFrame;
	src: string;
	scale: number;
	timestamp: number;
}) => {
	const transformed = resizeVideoFrame({
		frame,
		scale,
	});

	if (transformed !== frame) {
		frame.close();
	}

	setFrameInFrameDatabase({
		src,
		timestamp,
		frame: transformed,
	});

	return transformed;
};

export const InnerTimelineItemFilmStrip: React.FC<{
	readonly aspectRatio: number;
	readonly pxPerSecond: number;
	readonly fromSeconds: number;
	readonly toSeconds: number;
	readonly roundedDifference: number;
	readonly height: number;
	readonly src: string;
	durationOfAssetInSeconds: number;
}> = ({
	aspectRatio,
	pxPerSecond,
	fromSeconds,
	toSeconds,
	roundedDifference,
	height,
	src,
	durationOfAssetInSeconds,
}) => {
	const durationOfOneImage = getDurationOfOneImage({
		aspectRatio,
		pxPerSecond,
		height,
	});
	const {timestampTargets, firstIndex} = calculateTimestampSlots({
		fromSeconds,
		aspectRatio,
		pxPerSecond,
		height,
		toSeconds,
		durationOfOneImage,
	});

	const [error, setError] = useState<Error | null>(null);

	const widthOfOneFrame = Math.round(height * aspectRatio);
	const visualizationWidth = widthOfOneFrame * timestampTargets.length;

	const virtualOffset = firstIndex * widthOfOneFrame;
	const pxOffset = pxPerSecond * fromSeconds;

	const xOfFirstSlot = virtualOffset - pxOffset - roundedDifference;

	const timestampTargetString = timestampTargets.join(',');
	const canvasRef = useRef<HTMLCanvasElement>(null);

	const initialization = useInitializationPromise(src);

	useLayoutEffect(() => {
		if (error) {
			return;
		}

		const {current: canvas} = canvasRef;
		if (!canvas) {
			return;
		}

		canvas.width = Math.ceil(visualizationWidth);
		canvas.height = height;
		canvas.style.width = `${Math.ceil(visualizationWidth)}px`;
		canvas.style.height = `${height}px`;
		const ctx = canvas.getContext('2d');
		if (!ctx) {
			return;
		}

		// desired-timestamp -> filled-timestamp
		const filledSlots = new Map<number, number | undefined>();

		const timestampTargetSplit = timestampTargetString.split(',').map(Number);

		ensureSlots({
			filledSlots,
			timestampTargets: timestampTargetSplit,
		});

		const maxDistance = getMaxDistance(durationOfOneImage);

		const drawFromCache = () => {
			fillWithCachedFrames({
				ctx,
				filledSlots,
				src,
				slotsToFill: Array.from(filledSlots.keys()),
				maxDistanceInWebCodecsUnits: maxDistance,
				visibleSlots: getVisibleSlots(),
			});
		};

		const {cleanup, getVisibleSlots} = watchVisibleSlots({
			canvas,
			widthOfOneFrame,
			onScrollUpdate: () => {
				const waitForNextFrame =
					typeof requestIdleCallback === 'function'
						? requestIdleCallback
						: requestAnimationFrame;
				waitForNextFrame(() => {
					drawFromCache();
				});
			},
		});

		drawFromCache();

		const unfilled = Array.from(filledSlots.keys()).filter(
			(timestamp) => filledSlots.get(timestamp) === undefined,
		);

		// Don't extract frames if all slots are filled
		if (unfilled.length === 0) {
			return () => {
				cleanup();
			};
		}

		clearOldFrames();

		const controller = new AbortController();

		initialization.promise
			.then((initialized) => {
				return extractFrames({
					timestampsInSeconds: () => {
						ensureSlots({
							filledSlots,
							timestampTargets: timestampTargetSplit,
						});

						const unfilledNow = Array.from(filledSlots.keys()).filter(
							(timestamp) => filledSlots.get(timestamp) === undefined,
						);

						return unfilledNow.map((timestamp) =>
							Math.min(
								timestamp / WEBCODECS_TIMESCALE,
								durationOfAssetInSeconds,
							),
						);
					},
					initialized,
					onFrame: (frame) => {
						const scale = (height / frame.displayHeight) * DEVICE_PIXEL_RATIO;

						const transformed = saveFrameToDatabase({
							frame,
							src,
							scale,
							timestamp: frame.timestamp,
						});

						fillFrameWhereItFits({
							ctx,
							filledSlots,
							frame: transformed,
							durationOfOneImage,
							visibleSlots: getVisibleSlots(),
						});
					},
					signal: controller.signal,
				});
			})

			.then(() => {
				// Some frames we could not fill, maybe because it's a screen recording and the timestamp
				// is not close enough to the target timestamp.
				const unfilledNow = Array.from(filledSlots.keys()).filter(
					(timestamp) => filledSlots.get(timestamp) === undefined,
				);
				if (unfilledNow.length > 0) {
					const keys = getKeysFromFrameDatabase().filter((k) =>
						k.startsWith(src),
					);

					for (const timestamp of unfilledNow) {
						const best = getBestFrameFromDatabase({
							keys,
							timestamp,
						});

						if (!best) {
							continue;
						}

						const frame = getFrameFromFrameDatabase(best.bestKey);
						if (!frame) {
							throw new Error('Frame not found');
						}

						saveFrameToDatabase({
							frame: frame.frame.clone(),
							src,
							scale: 1,
							timestamp,
						});
					}
				}

				// Fill the slots with the frames we have in the database, allowing any deviation
				fillWithCachedFrames({
					ctx,
					filledSlots,
					src,
					slotsToFill: Array.from(filledSlots.keys()),
					maxDistanceInWebCodecsUnits: maxDistance,
					visibleSlots: getVisibleSlots(),
				});
			})
			.catch((e) => {
				// eslint-disable-next-line no-console
				console.error(`Failed to extract frames for ${src}: ${e}`);

				setError(e);
			})
			.finally(() => {
				clearOldFrames();
			});

		return () => {
			controller.abort();
			cleanup();
		};
	}, [
		durationOfAssetInSeconds,
		durationOfOneImage,
		error,
		height,
		initialization.promise,
		src,
		timestampTargetString,
		visualizationWidth,
		widthOfOneFrame,
	]);

	const containerStyle = React.useMemo<React.CSSProperties>(
		() => ({
			height,
			position: 'absolute',
			top: 0,
			left: 0,
			width: '100%',
			userSelect: 'none',
			pointerEvents: 'none',
			marginLeft: xOfFirstSlot,
		}),
		[height, xOfFirstSlot],
	);

	return (
		<div style={containerStyle}>
			<canvas ref={canvasRef} />
		</div>
	);
};

export const TimelineItemFilmStrip: React.FC<{
	readonly item: VideoItem;
	readonly startFrom: number;
	readonly durationInFrames: number;
	readonly fps: number;
	readonly roundedDifference: number;
	readonly height: number;
	readonly playbackRate: number;
}> = ({
	startFrom,
	durationInFrames,
	fps,
	roundedDifference,
	height,
	item,
	playbackRate,
}) => {
	const {timelineWidth} = useTimelineSize();
	const {durationInFrames: totalDurationInFrames} = useTimelineContext();
	const visibleFrames = getVisibleFrames({
		fps: fps,
		totalDurationInFrames,
	});
	if (timelineWidth === null) {
		throw new Error('Timeline width is null');
	}

	const fromSeconds = startFrom / fps;
	const toSeconds = (startFrom + durationInFrames * playbackRate) / fps;

	const pxPerSecond = timelineWidth / (visibleFrames / fps) / playbackRate;

	const asset = useAssetFromItem(item);
	const src = usePreferredLocalUrl(asset);
	if (asset.type !== 'video') {
		throw new Error('Asset is not a video');
	}

	return (
		<InnerTimelineItemFilmStrip
			aspectRatio={asset.width / asset.height}
			pxPerSecond={pxPerSecond}
			fromSeconds={fromSeconds}
			toSeconds={toSeconds}
			roundedDifference={roundedDifference}
			height={height}
			src={src}
			durationOfAssetInSeconds={asset.durationInSeconds}
		/>
	);
};

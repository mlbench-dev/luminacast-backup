import {
	ALL_FORMATS,
	Input,
	InputDisposedError,
	InputFormat,
	InputVideoTrack,
	UrlSource,
	VideoSampleSink,
} from 'mediabunny';
import {useEffect, useRef} from 'react';

type Options = {
	track: {width: number; height: number};
	container: string;
	durationInSeconds: number | null;
};

export type ExtractFramesTimestampsInSecondsFn = (
	options: Options,
) => Promise<number[]> | number[];

export type ExtractFramesProps = {
	timestampsInSeconds: number[] | ExtractFramesTimestampsInSecondsFn;
	onFrame: (frame: VideoFrame) => void;
	initialized: InitializedExtractFrames;
	signal?: AbortSignal;
};

type InitializedExtractFrames = {
	durationInSeconds: number;
	format: InputFormat;
	videoTrack: InputVideoTrack;
	input: Input;
};

const initializeExtractFrames = async (
	src: string,
): Promise<InitializedExtractFrames> => {
	const input = new Input({
		formats: ALL_FORMATS,
		source: new UrlSource(src),
	});

	const [durationInSeconds, format, videoTrack] = await Promise.all([
		input.computeDuration(),
		input.getFormat(),
		input.getPrimaryVideoTrack(),
	]);

	if (!videoTrack) {
		throw new Error('No video track found in the input');
	}

	return {
		durationInSeconds,
		format,
		videoTrack,
		input,
	};
};

export const useInitializationPromise = (src: string) => {
	const ref = useRef<{
		src: string;
		promise: Promise<InitializedExtractFrames>;
	} | null>(null);

	if (!ref.current) {
		ref.current = {src, promise: initializeExtractFrames(src)};
	}
	if (ref.current.src !== src) {
		ref.current.promise.then(({input}) => {
			input.dispose();
		});
		ref.current = {src, promise: initializeExtractFrames(src)};
	}

	useEffect(() => {
		return () => {};
	}, []);

	return ref.current;
};

export async function extractFrames({
	timestampsInSeconds,
	onFrame,
	signal,
	initialized: {input, durationInSeconds, format, videoTrack},
}: ExtractFramesProps): Promise<void> {
	try {
		const timestamps =
			typeof timestampsInSeconds === 'function'
				? await timestampsInSeconds({
						track: {
							width: videoTrack.displayWidth,
							height: videoTrack.displayHeight,
						},
						container: format.name,
						durationInSeconds,
					})
				: timestampsInSeconds;

		if (timestamps.length === 0) {
			return;
		}

		const sink = new VideoSampleSink(videoTrack);

		for await (const videoSample of sink.samplesAtTimestamps(timestamps)) {
			if (!videoSample) {
				continue;
			}

			if (signal?.aborted) {
				videoSample.close();
				break;
			}

			const videoFrame = videoSample.toVideoFrame();

			onFrame(videoFrame);
			videoSample.close();
		}
	} catch (error) {
		if (error instanceof InputDisposedError) {
			return;
		}
		if (input.disposed) {
			return;
		}
		if (signal?.aborted) {
			return;
		}

		throw error;
	}
}

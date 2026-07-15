// Cache the thumbnails of the timeline

export type FrameDatabaseKey = string & {__brand: 'FrameDatabaseKey'};

export const makeFrameDatabaseKey = (
	src: string,
	timestamp: number,
): FrameDatabaseKey => `${src}|${timestamp}` as FrameDatabaseKey;

type VideoFrameAndLastUsed = {
	frame: VideoFrame;
	lastUsed: number;
};

const frameDatabase: Map<FrameDatabaseKey, VideoFrameAndLastUsed> = new Map();

export const getFrameFromFrameDatabase = (key: FrameDatabaseKey) => {
	const frame = frameDatabase.get(key);
	if (!frame) {
		return null;
	}
	return frame;
};

export const getKeysFromFrameDatabase = () => {
	return Array.from(frameDatabase.keys());
};

export const setFrameInFrameDatabase = ({
	src,
	timestamp,
	frame,
}: {
	src: string;
	timestamp: number;
	frame: VideoFrame;
}) => {
	const databaseKey = makeFrameDatabaseKey(src, timestamp);
	const existingFrame = frameDatabase.get(databaseKey);
	if (existingFrame) {
		existingFrame.frame.close();
	}

	frameDatabase.set(databaseKey, {frame, lastUsed: Date.now()});
};

export const getTimestampFromFrameDatabaseKey = (key: FrameDatabaseKey) => {
	const split = key.split('|');
	return Number(split[split.length - 1]);
};

// a 16:9 thumbnail is 43x23px wide - 43 * 23 * 4 = 4052 bytes
// Our allowance is a 50MB frame cache, so we can store 12340 thumbnails

const MAX_FRAMES_IN_CACHE = 12340;

export const clearOldFrames = () => {
	if (frameDatabase.size <= MAX_FRAMES_IN_CACHE) {
		return;
	}

	const framesToRemove = Array.from(frameDatabase.entries()).sort(
		(a, b) => a[1].lastUsed - b[1].lastUsed,
	);

	for (const [key, frame] of framesToRemove.slice(
		0,
		framesToRemove.length - MAX_FRAMES_IN_CACHE,
	)) {
		frame.frame.close();
		frameDatabase.delete(key);
	}
};

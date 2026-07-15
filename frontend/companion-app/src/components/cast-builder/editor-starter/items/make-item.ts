import {PlayerRef} from '@remotion/player';
import {ALL_FORMATS, BlobSource, Input} from 'mediabunny';
import {DropPosition} from '../assets/add-asset';
import {EditorStarterAsset} from '../assets/assets';
import {cacheAssetLocally} from '../caching/indexeddb';
import {setLocalUrl} from '../caching/load-to-blob-url';
import {detectFileType} from '../utils/detect-file-type';
import {generateRandomId} from '../utils/generate-random-id';
import {getSvgDimensions} from '../utils/get-svg-dimensions';
import {makeAudioItem} from './audio/make-audio-item';
import {makeGifItem} from './gif/make-gif-item';
import {makeImageItem} from './image/make-image-item';
import {EditorStarterItem} from './item-type';
import {makeVideoItem} from './video/make-video-item';

export const makeItem = async ({
	file,
	playerRef,
	dropPosition,
	fps,
	compositionWidth,
	compositionHeight,
	filename,
	remoteUrl,
	remoteFileKey,
}: {
	file: Blob;
	playerRef: React.RefObject<PlayerRef | null>;
	dropPosition: DropPosition | null;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	remoteUrl: string | null;
	remoteFileKey: string | null;
	filename: string;
}): Promise<{item: EditorStarterItem; asset: EditorStarterAsset}> => {
	const assetId = generateRandomId();
	const url = URL.createObjectURL(file);
	setLocalUrl(assetId, url);

	await cacheAssetLocally({assetId, value: file});

	const fileType = await detectFileType(file);
	const currentFrame = playerRef.current?.getCurrentFrame() ?? 0;

	// Handle images directly (GIF, PNG, JPEG, WebP, BMP)
	if (fileType.category === 'image') {
		if (!fileType.dimensions) {
			throw new Error('Could not get dimensions for image');
		}

		if (fileType.type === 'gif') {
			return makeGifItem({
				file,
				fps,
				compositionWidth,
				compositionHeight,
				currentFrame,
				dropPosition,
				dimensions: fileType.dimensions,
				assetId,
				blobUrl: url,
				filename,
				remoteUrl,
				remoteFileKey,
			});
		}

		return makeImageItem({
			file,
			fps,
			compositionWidth,
			compositionHeight,
			currentFrame,
			dropPosition,
			assetId,
			filename,
			remoteUrl,
			remoteFileKey,
			dimensions: fileType.dimensions,
		});
	}

	// Handle SVG (not detected by magic bytes, check MIME type)
	if (file.type === 'image/svg+xml') {
		const svgText = await file.text();
		const dimensions = getSvgDimensions(svgText);
		return makeImageItem({
			file,
			fps,
			compositionWidth,
			compositionHeight,
			currentFrame,
			dropPosition,
			dimensions,
			assetId,
			filename,
			remoteUrl,
			remoteFileKey,
		});
	}

	// Handle video and audio using Mediabunny
	if (fileType.category === 'video' || fileType.category === 'audio') {
		const input = new Input({
			formats: ALL_FORMATS,
			source: new BlobSource(file),
		});

		try {
			const durationInSeconds = await input.computeDuration();
			const videoTrack = await input.getPrimaryVideoTrack();
			const audioTrack = await input.getPrimaryAudioTrack();

			// Audio-only file
			if (!videoTrack && audioTrack) {
				return makeAudioItem({
					file,
					fps,
					durationInSeconds,
					currentFrame,
					size: file.size,
					assetId,
					filename,
					remoteUrl,
					remoteFileKey,
				});
			}

			// Video file
			if (videoTrack) {
				return makeVideoItem({
					file,
					fps,
					compositionWidth,
					compositionHeight,
					durationInSeconds,
					dimensions: {
						width: videoTrack.displayWidth,
						height: videoTrack.displayHeight,
					},
					currentFrame,
					dropPosition,
					assetId,
					hasAudioTrack: audioTrack !== null,
					filename,
					remoteUrl,
					remoteFileKey,
				});
			}

			throw new Error('No video or audio track found in media file');
		} finally {
			input.dispose();
		}
	}

	throw new Error('Unknown asset type');
};

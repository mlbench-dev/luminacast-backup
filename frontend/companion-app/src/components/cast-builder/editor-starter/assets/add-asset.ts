import {PlayerRef} from '@remotion/player';
import {toast} from 'sonner';
import {TimelineWriteOnlyContext} from '../context-provider';
import {makeItem} from '../items/make-item';
import {addAssetToState} from '../state/actions/add-asset-to-state';
import {addItem} from '../state/actions/add-item';
import {TrackType} from '../state/types';
import {getErrorStack, performAssetUpload} from '../utils/asset-upload-utils';
import {isTimelineEmpty} from '../utils/is-timeline-empty';
import {getUploadUrls} from '../utils/use-uploader';

export type DropPosition = {
	x: number;
	y: number;
};

const innerAddAsset = async ({
	file,
	timelineWriteContext,
	playerRef,
	dropPosition,
	fps,
	compositionWidth,
	compositionHeight,
	tracks,
	filename,
}: {
	file: Blob;
	timelineWriteContext: TimelineWriteOnlyContext;
	playerRef: React.RefObject<PlayerRef | null>;
	dropPosition: DropPosition | null;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	tracks: TrackType[];
	filename: string;
}) => {
	const {setState} = timelineWriteContext;

	// Try to get upload URLs, but don't fail if this doesn't work
	const presignResultPromise = getUploadUrls(file);

	const {item, asset} = await makeItem({
		file,
		fps,
		compositionWidth,
		compositionHeight,
		playerRef,
		dropPosition,
		remoteUrl: null,
		remoteFileKey: null,
		filename,
	});

	const isEmpty = isTimelineEmpty(tracks);

	// Add asset and item to state
	setState({
		update: (state) => {
			const withItem = addItem({
				state,
				item: item,
				select: true,
				position: {type: 'front'},
			});
			const withAsset = addAssetToState({state: withItem, asset});
			return withAsset;
		},
		commitToUndoStack: true,
	});

	if (isEmpty) {
		setState({
			update: (state) => {
				return {
					...state,
					compositionWidth: item.width,
					compositionHeight: item.height,
				};
			},
			commitToUndoStack: true,
		});
	}

	// Handle cloud upload or local-only mode
	await performAssetUpload({setState, asset, presignResultPromise, file});
};

export const addAsset = async ({
	file,
	filename,
	timelineWriteContext,
	playerRef,
	dropPosition,
	fps,
	compositionWidth,
	compositionHeight,
	tracks,
}: {
	file: Blob;
	filename: string;
	timelineWriteContext: TimelineWriteOnlyContext;
	playerRef: React.RefObject<PlayerRef | null>;
	dropPosition: DropPosition | null;
	fps: number;
	compositionWidth: number;
	compositionHeight: number;
	tracks: TrackType[];
}) => {
	try {
		await innerAddAsset({
			file,
			fps,
			compositionWidth,
			compositionHeight,
			tracks,
			timelineWriteContext,
			playerRef,
			dropPosition,
			filename,
		});
	} catch (error: unknown) {
		const message = getErrorStack(error);
		toast.error('Error processing asset', {
			description: message,
		});
	}
};

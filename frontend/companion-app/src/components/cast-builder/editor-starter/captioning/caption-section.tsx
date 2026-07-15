import React, {useCallback} from 'react';
import {taskIndicatorRef} from '../action-row/tasks-indicator/tasks-indicator';
import {TextButton} from '../action-row/text-button';
import {
	getAssetDurationInSeconds,
	getAssetStartInSeconds,
	getDurationInSecondsOfCaptionAsset,
} from '../assets/utils';
import {InspectorLabel} from '../inspector/components/inspector-label';
import {CollapsableInspectorSection} from '../inspector/components/inspector-section';
import {AudioItem} from '../items/audio/audio-item-type';
import {VideoItem} from '../items/video/video-item-type';
import {addCaptionAsset} from '../state/actions/add-caption-asset';
import {addItem} from '../state/actions/add-item';
import {usePreferredLocalUrl} from '../utils/find-asset-by-id';
import {generateRandomId} from '../utils/generate-random-id';
import {
	useAssetFromItem,
	useCaptionState,
	useDimensions,
	useFps,
	useTracks,
	useWriteContext,
} from '../utils/use-context';
import {MAX_DURATION_ALLOWING_CAPTIONING_IN_SEC} from './audio-buffer-to-wav';
import {getCaptions} from './caption-state';

const DEFAULT_HIGHLIGHT_COLOR = '#39E508';

export const GenerateCaptionSection: React.FC<{
	item: VideoItem | AudioItem;
}> = ({item}) => {
	const asset = useAssetFromItem(item);

	const {fps} = useFps();
	const captionState = useCaptionState();
	const {setState} = useWriteContext();
	const {tracks} = useTracks();
	const {compositionWidth} = useDimensions();

	const src = usePreferredLocalUrl(asset);

	const caption = useCallback(async () => {
		const captionItemId = generateRandomId();
		taskIndicatorRef.current?.open();

		if (asset.type !== 'audio' && asset.type !== 'video') {
			throw new Error(
				'Captioning is only supported for audio and video assets',
			);
		}

		const captions = await getCaptions({
			src,
			setState,
			asset,
			captionItemId,
		});

		const startInSeconds = getAssetStartInSeconds(item);

		if (!captions) {
			return;
		}

		const videoTrackIndex = tracks.findIndex((track) =>
			track.items.some((i) => i === item.id),
		);

		if (videoTrackIndex === -1) {
			throw new Error('Could not find track for video item');
		}

		const defaultLineHeight = 1.2;
		const defaultLetterSpacing = 0;
		const defaultFontSize = 80;
		const defaultMaxLines = 2;

		const width = Math.min(compositionWidth, 900) - 40;
		// Place the caption item in the newly created track
		setState({
			commitToUndoStack: true,
			update: (state) => {
				const {state: stateWithAsset, asset: captionAsset} = addCaptionAsset({
					state,
					captions,
					filename: 'captions.srt',
				});

				return addItem({
					state: stateWithAsset,
					item: {
						type: 'captions',
						assetId: captionAsset.id,
						durationInFrames: Math.round(
							(getDurationInSecondsOfCaptionAsset(captionAsset) -
								(startInSeconds ?? 0)) *
								fps,
						),
						from: item.from,
						height: defaultFontSize * defaultLineHeight * defaultMaxLines,
						id: captionItemId,
						isDraggingInTimeline: false,
						left: (state.undoableState.compositionWidth - width) / 2,
						top: state.undoableState.compositionHeight / 2 + 150,
						width: width,
						opacity: 1,
						fontFamily: 'TikTok Sans',
						fontStyle: {
							variant: 'normal',
							weight: '600',
						},
						rotation: 0,
						lineHeight: defaultLineHeight,
						letterSpacing: defaultLetterSpacing,
						fontSize: defaultFontSize,
						align: 'center',
						color: 'white',
						highlightColor: DEFAULT_HIGHLIGHT_COLOR,
						direction: 'ltr',
						pageDurationInMilliseconds: 2000,
						captionStartInSeconds: startInSeconds ?? 0,
						strokeWidth: 4,
						strokeColor: 'black',
						maxLines: defaultMaxLines,
						fadeInDurationInSeconds: 0,
						fadeOutDurationInSeconds: 0,
					},
					select: true,
					position: {type: 'directly-above', trackIndex: videoTrackIndex},
				});
			},
		});
	}, [item, setState, compositionWidth, tracks, src, fps, asset]);

	const existingCaptioningTask = captionState.find(
		(task) => task.assetId === asset.id,
	);

	const duration = getAssetDurationInSeconds(asset);

	const isOverLimit =
		duration === null
			? true
			: duration > MAX_DURATION_ALLOWING_CAPTIONING_IN_SEC;

	const captioningDone =
		existingCaptioningTask && existingCaptioningTask.status.type === 'done';
	const captioningInProgress =
		existingCaptioningTask &&
		(existingCaptioningTask.status.type === 'extracting-audio' ||
			existingCaptioningTask.status.type === 'uploading-audio' ||
			existingCaptioningTask.status.type === 'captioning');

	return (
		<CollapsableInspectorSection
			summary={<InspectorLabel>Captions</InspectorLabel>}
			id={`captions-${item.id}`}
			defaultOpen={false}
		>
			{captioningDone ? (
				<div className="mb-2 text-xs text-neutral-400">
					Captions already generated
				</div>
			) : null}

			{isOverLimit ? (
				<div className="mb-2 text-xs text-neutral-400 italic">
					Audio is too long to caption
				</div>
			) : (
				<TextButton onClick={caption} disabled={captioningInProgress}>
					{captioningInProgress
						? 'Generating captions...'
						: captioningDone
							? 'Regenerate captions'
							: `Caption ${item.type === 'video' ? 'video' : 'audio'}`}{' '}
				</TextButton>
			)}
		</CollapsableInspectorSection>
	);
};

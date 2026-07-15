import {
	getAssetFromItem,
	getAssetMaxDurationInFramesFromItem,
} from '../../../assets/utils';
import {SetState} from '../../../context-provider';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from '../../../force-specific-cursor';
import {getItemPlaybackRate} from '../../../items/get-item-playback-rate';
import {getMinimumFromWhenExtendingLeftBasedOnAsset} from '../../../state/actions/extend-left';
import {
	markItemAsBeingTrimmed,
	unmarkItemAsBeingTrimmed,
} from '../../../state/actions/mark-item-as-being-trimmed';
import {rollEdit} from '../../../state/actions/roll-edit';
import {EditorState} from '../../../state/types';
import {getCompositionDuration} from '../../../utils/get-composition-duration';
import {getVisibleFrames} from '../../../utils/get-visible-frames';
import {getOffsetOfTrack} from '../../../utils/position-utils';
import {timelineScrollContainerRef} from '../../../utils/restore-scroll-after-zoom';
import {hasExceededMoveThreshold} from '../../../utils/selection-utils';
import {TimelineItemAdjacency} from '../../timeline-track/timeline-track-rolling-edit';
import {getTrackIndexOfItem} from '../../utils/get-track-index-of-item';

export const onRollingEditHandler = ({
	pointerDownEvent,
	setState,
	timelineWidth,
	stateAsRef,
	adjacency,
	height,
	onDragEnd,
}: {
	pointerDownEvent: React.PointerEvent<HTMLDivElement>;
	setState: SetState;
	timelineWidth: number;
	stateAsRef: React.RefObject<EditorState>;
	adjacency: TimelineItemAdjacency;
	height: number;
	onDragEnd?: () => void;
}) => {
	const {undoableState} = stateAsRef.current;
	const {tracks, items, fps, assets} = undoableState;

	const trackIndex = getTrackIndexOfItem({
		itemId: adjacency.previous,
		tracks,
	});

	setState({
		update: (state) => {
			let newState = state;
			const firstItem = items[adjacency.previous];
			const secondItem = items[adjacency.next];
			const firstItemMinimumFrom = getMinimumFromWhenExtendingLeftBasedOnAsset({
				fps: state.undoableState.fps,
				prevItem: firstItem,
			});

			const secondItemMinimumFrom = getMinimumFromWhenExtendingLeftBasedOnAsset(
				{
					fps: state.undoableState.fps,
					prevItem: secondItem,
				},
			);

			const firstItemMaxDuration = getAssetMaxDurationInFramesFromItem({
				item: firstItem,
				assets,
				fps: state.undoableState.fps,
				playbackRate: getItemPlaybackRate(firstItem),
			});
			const secondItemMaxDuration = getAssetMaxDurationInFramesFromItem({
				item: secondItem,
				assets,
				fps: state.undoableState.fps,
				playbackRate: getItemPlaybackRate(secondItem),
			});

			const top = getOffsetOfTrack({
				trackIndex,
				tracks,
				items,
			});

			newState = markItemAsBeingTrimmed({
				state: newState,
				itemId: adjacency.previous,
				side: 'right',
				maxDurationInFrames: firstItemMaxDuration,
				minFrom: firstItemMinimumFrom,
				trackIndex,
				top,
				height,
			});
			newState = markItemAsBeingTrimmed({
				state: newState,
				itemId: adjacency.next,
				side: 'left',
				maxDurationInFrames: secondItemMaxDuration,
				minFrom: secondItemMinimumFrom,
				trackIndex,
				top,
				height,
			});

			return newState;
		},
		commitToUndoStack: false,
	});

	forceSpecificCursor('ew-resize');

	const startX = pointerDownEvent.clientX;

	const initialScrollLeft = timelineScrollContainerRef.current?.scrollLeft ?? 0;

	const compositionDurationInFrames = getCompositionDuration(
		Object.values(items),
	);

	const visibleFrames = getVisibleFrames({
		fps: fps,
		totalDurationInFrames: compositionDurationInFrames,
	});

	let lastOffsetInFrames = 0;

	const fromPointerEvent = (
		pointerEvent: PointerEvent,
		commitToUndoStack: boolean,
	) => {
		// Account for timeline container scroll changes during dragging
		const currentScrollLeft =
			timelineScrollContainerRef.current?.scrollLeft ?? 0;
		const scrollDelta = currentScrollLeft - initialScrollLeft;

		const offsetX = pointerEvent.clientX - startX + scrollDelta;
		const offsetInFrames = Math.round(
			(offsetX / timelineWidth) * visibleFrames,
		);
		if (offsetInFrames === lastOffsetInFrames && !commitToUndoStack) {
			return;
		}

		const draggingDirection =
			offsetInFrames > lastOffsetInFrames ? 'right' : 'left';

		lastOffsetInFrames = offsetInFrames;

		setState({
			update: (state) => {
				const firstItem = items[adjacency.previous];
				const secondItem = items[adjacency.next];

				// it's better to not rely on the order of the items in the track items array
				// but ensure we're getting the previous item by sorting them by their `from` property
				const trackItemsSorted = tracks[trackIndex].items.sort(
					(a, b) => items[a].from - items[b].from,
				);

				const firstItemIndex = trackItemsSorted.findIndex(
					(trackItem) => trackItem === firstItem.id,
				);
				const secondItemIndex = trackItemsSorted.findIndex(
					(trackItem) => trackItem === secondItem.id,
				);

				const firstItemAsset = getAssetFromItem({item: firstItem, assets});

				return rollEdit({
					state,
					adjacency,
					firstItemIndex,
					secondItemIndex,
					firstItemInitialDurationInFrames: firstItem.durationInFrames,
					firstItemInitialFrom: firstItem.from,
					secondItemInitialDurationInFrames: secondItem.durationInFrames,
					secondItemInitialFrom: secondItem.from,
					firstItemAsset,
					trackItemsSorted,
					offsetInFrames,
					draggingDirection,
					timelineWidth,
					visibleFrames,
				});
			},
			commitToUndoStack,
		});
	};

	let hasStartedExtend = false;
	let lastPointerEvent: PointerEvent | null = null;

	const onPointerMove = (pointerMoveEvent: PointerEvent) => {
		lastPointerEvent = pointerMoveEvent;
		if (!hasStartedExtend) {
			if (!hasExceededMoveThreshold(startX, 0, pointerMoveEvent.clientX, 0)) {
				return; // wait until threshold exceeded
			}
			hasStartedExtend = true;
		}
		fromPointerEvent(pointerMoveEvent, false);
	};

	// account for `useTimelineContainerAutoScroll`
	const onScroll = () => {
		if (hasStartedExtend && lastPointerEvent) {
			fromPointerEvent(lastPointerEvent, false);
		}
	};

	const onPointerUp = (pointerUpEvent: PointerEvent) => {
		stopForcingSpecificCursor();
		if (hasStartedExtend) {
			fromPointerEvent(pointerUpEvent, true);
		}

		setState({
			update: (state) => {
				let newState = state;

				newState = unmarkItemAsBeingTrimmed({
					state: newState,
					itemId: adjacency.previous,
				});
				newState = unmarkItemAsBeingTrimmed({
					state: newState,
					itemId: adjacency.next,
				});

				return newState;
			},
			commitToUndoStack: false,
		});

		window.removeEventListener('pointermove', onPointerMove);
		window.removeEventListener('pointerup', onPointerUp);
		timelineScrollContainerRef.current?.removeEventListener('scroll', onScroll);
		onDragEnd?.();
	};

	window.addEventListener('pointermove', onPointerMove);
	window.addEventListener('pointerup', onPointerUp);
	timelineScrollContainerRef.current?.addEventListener('scroll', onScroll);
};

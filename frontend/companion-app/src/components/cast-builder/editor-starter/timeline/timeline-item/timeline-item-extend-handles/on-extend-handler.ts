import {
	getAssetFromItem,
	getAssetMaxDurationInFramesFromItem,
} from '../../../assets/utils';
import {DEFAULT_TIMELINE_SNAPPING_THRESHOLD_PIXELS} from '../../../constants';
import {SetState} from '../../../context-provider';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from '../../../force-specific-cursor';
import {getItemPlaybackRate} from '../../../items/get-item-playback-rate';
import {applySnapPoint} from '../../../state/actions/apply-snap-point';
import {changeItem} from '../../../state/actions/change-item';
import {mirrorTimingsToBondedPartners} from '../../../utils/bonded-pair';
import {
	extendLeft,
	getMinimumFromWhenExtendingLeftBasedOnAsset,
} from '../../../state/actions/extend-left';
import {extendRight} from '../../../state/actions/extend-right';
import {
	markItemAsBeingTrimmed,
	unmarkItemAsBeingTrimmed,
} from '../../../state/actions/mark-item-as-being-trimmed';
import {setSelectedItems} from '../../../state/actions/set-selected-items';
import {EditorState} from '../../../state/types';
import {getCompositionDuration} from '../../../utils/get-composition-duration';
import {getVisibleFrames} from '../../../utils/get-visible-frames';
import {getOffsetOfTrack} from '../../../utils/position-utils';
import {timelineScrollContainerRef} from '../../../utils/restore-scroll-after-zoom';
import {
	calculateSelectionAndDragState,
	hasExceededMoveThreshold,
} from '../../../utils/selection-utils';
import {getTrackIndexOfItem} from '../../utils/get-track-index-of-item';
import {
	applySnapping,
	collectSnapPoints,
	SnapPoint,
} from '../../utils/snap-points';

type ExtendType =
	| {
			type: 'left';
			clickedItemId: string;
	  }
	| {
			type: 'right';
			clickedItemId: string;
	  };

export const onExtendHandler = ({
	pointerDownEvent,
	setState,
	timelineWidth,
	stateAsRef,
	extend,
	height,
	onDragEnd,
}: {
	pointerDownEvent: React.PointerEvent<HTMLDivElement>;
	setState: SetState;
	timelineWidth: number;
	stateAsRef: React.RefObject<EditorState>;
	extend: ExtendType;
	height: number;
	onDragEnd: () => void;
}) => {
	// Handle selection with multi-select support
	const multiSelect = pointerDownEvent.metaKey || pointerDownEvent.shiftKey;

	const {undoableState, selectedItems} = stateAsRef.current;
	const {tracks, items, fps, assets} = undoableState;

	const {newSelectedItems} = calculateSelectionAndDragState({
		clickedItem: items[extend.clickedItemId],
		currentSelectedItemIds: selectedItems,
		isMultiSelectMode: multiSelect,
	});

	setState({
		update: (state) => {
			let newState = setSelectedItems(state, newSelectedItems);

			for (const itemId of newSelectedItems) {
				const item = items[itemId];

				const playbackRate = getItemPlaybackRate(item);

				const maxDuration = getAssetMaxDurationInFramesFromItem({
					item,
					assets,
					fps: state.undoableState.fps,
					playbackRate,
				});
				const minimumFrom = getMinimumFromWhenExtendingLeftBasedOnAsset({
					prevItem: item,
					fps: state.undoableState.fps,
				});

				const trackIndex = getTrackIndexOfItem({
					itemId,
					tracks,
				});

				const top = getOffsetOfTrack({
					trackIndex,
					tracks,
					items,
				});

				newState = markItemAsBeingTrimmed({
					state: newState,
					itemId,
					side: extend.type === 'left' ? 'left' : 'right',
					maxDurationInFrames: maxDuration,
					minFrom: minimumFrom,
					trackIndex,
					top,
					height,
				});
			}

			return newState;
		},
		commitToUndoStack: false,
	});

	forceSpecificCursor(extend.type === 'left' ? 'e-resize' : 'w-resize');

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

	// Precompute snap points for this trim gesture
	const snapPoints = collectSnapPoints({
		tracks,
		items,
		excludeItemIds: newSelectedItems,
	});

	const fromPointerEvent = (
		pointerEvent: PointerEvent,
		commitToUndoStack: boolean,
	) => {
		// Account for timeline container scroll changes during dragging
		const currentScrollLeft =
			timelineScrollContainerRef.current?.scrollLeft ?? 0;
		const scrollDelta = currentScrollLeft - initialScrollLeft;

		const offsetX = pointerEvent.clientX - startX + scrollDelta;
		let offsetInFrames = Math.round((offsetX / timelineWidth) * visibleFrames);
		const pixelsPerFrame = timelineWidth / visibleFrames;

		// Apply snapping if enabled
		const currentState = stateAsRef.current;
		let snapPointToApply: SnapPoint | null = null;

		if (
			currentState &&
			currentState.isSnappingEnabled === true &&
			newSelectedItems.length > 0
		) {
			// Get the item being trimmed
			const trimmedItem = items[extend.clickedItemId];
			if (trimmedItem) {
				// Calculate what the new duration would be after this offset
				let wouldBeValidTrim = true;
				let newDurationAfterOffset: number;
				let newFromAfterOffset: number;

				if (extend.type === 'left') {
					// For left trim, duration changes inversely with offset
					newDurationAfterOffset =
						trimmedItem.durationInFrames - offsetInFrames;
					newFromAfterOffset = trimmedItem.from + offsetInFrames;

					// Check for collision with previous item
					const trackIndex = getTrackIndexOfItem({
						itemId: extend.clickedItemId,
						tracks,
					});
					if (trackIndex >= 0) {
						const trackItemsSorted = tracks[trackIndex].items
							.slice()
							.sort((a, b) => items[a].from - items[b].from);
						const itemIndex = trackItemsSorted.findIndex(
							(id) => id === extend.clickedItemId,
						);
						const previousItem =
							itemIndex > 0 ? trackItemsSorted[itemIndex - 1] : null;

						if (previousItem) {
							const previousItemEnd =
								items[previousItem].from + items[previousItem].durationInFrames;
							if (newFromAfterOffset < previousItemEnd) {
								wouldBeValidTrim = false;
							}
						}
					}

					// Check if new from would be negative
					if (newFromAfterOffset < 0) {
						wouldBeValidTrim = false;
					}
				} else {
					// For right trim, duration changes directly with offset
					newDurationAfterOffset =
						trimmedItem.durationInFrames + offsetInFrames;

					// Check for collision with next item
					const trackIndex = getTrackIndexOfItem({
						itemId: extend.clickedItemId,
						tracks,
					});
					if (trackIndex >= 0) {
						const trackItemsSorted = tracks[trackIndex].items
							.slice()
							.sort((a, b) => items[a].from - items[b].from);
						const itemIndex = trackItemsSorted.findIndex(
							(id) => id === extend.clickedItemId,
						);
						const nextItem =
							itemIndex < trackItemsSorted.length - 1
								? trackItemsSorted[itemIndex + 1]
								: null;

						if (nextItem) {
							const nextItemStart = items[nextItem].from;
							const newEnd = trimmedItem.from + newDurationAfterOffset;
							if (newEnd > nextItemStart) {
								wouldBeValidTrim = false;
							}
						}
					}
				}

				// Check if the trim would result in a valid duration (minimum 1 frame)
				if (newDurationAfterOffset < 1) {
					wouldBeValidTrim = false;
				}

				// Only apply snapping if the trim operation would be valid
				if (wouldBeValidTrim) {
					// Calculate target position based on trim side
					let targetFrame: number;
					if (extend.type === 'left') {
						// For left trim, we're adjusting the start position
						targetFrame = trimmedItem.from + offsetInFrames;
					} else {
						// For right trim, we're adjusting the end position
						targetFrame =
							trimmedItem.from + trimmedItem.durationInFrames + offsetInFrames;
					}

					// Apply snapping
					const {snappedFrame, activeSnapPoint} = applySnapping({
						targetFrame,
						snapPoints: snapPoints,
						pixelThreshold: DEFAULT_TIMELINE_SNAPPING_THRESHOLD_PIXELS,
						timelineWidth,
						visibleFrames,
						isSnappingEnabled: currentState.isSnappingEnabled,
					});
					if (!commitToUndoStack) {
						snapPointToApply = activeSnapPoint;
					}

					// Adjust offset based on snapping
					if (extend.type === 'left') {
						offsetInFrames = snappedFrame - trimmedItem.from;
					} else {
						offsetInFrames =
							snappedFrame - (trimmedItem.from + trimmedItem.durationInFrames);
					}
				}
			}
		}
		if (offsetInFrames === lastOffsetInFrames && !commitToUndoStack) {
			return;
		}

		lastOffsetInFrames = offsetInFrames;

		setState({
			update: (state) => {
				let newState = state;
				for (const itemId of newSelectedItems) {
					const item = items[itemId];
					const trackIndex = getTrackIndexOfItem({
						itemId,
						tracks,
					});
					// it's better to not rely on the order of the items in the track items array
					// but ensure we're getting the previous item by sorting them by their `from` property
					// Avoid mutating the original array as it is part of state
					const trackItemsSorted = tracks[trackIndex].items
						.slice()
						.sort((a, b) => items[a].from - items[b].from);
					if (extend.type === 'left') {
						newState = changeItem(newState, item.id, (prevItem) => {
							return extendLeft({
								prevItem,
								trackItemsSorted,
								itemIndex: trackItemsSorted.findIndex(
									(trackItem) => trackItem === item.id,
								),
								initialFrom: item.from,
								fps,
								items,
								initialDurationInFrames: item.durationInFrames,
								offsetInFrames,
								pixelsPerFrame,
							});
						});
					}
					if (extend.type === 'right') {
						newState = changeItem(newState, item.id, (prevItem) => {
							return extendRight({
								initialDurationInFrames: item.durationInFrames,
								initialFrom: item.from,
								prevItem,
								trackItemsSorted,
								itemIndex: trackItemsSorted.findIndex(
									(trackItem) => trackItem === item.id,
								),
								asset: getAssetFromItem({item, assets}),
								items,
								fps,
								offsetInFrames,
								pixelsPerFrame,
								visibleFrames,
							});
						});
					}

					newState = applySnapPoint({
						state: newState,
						snapPoint: snapPointToApply,
					});
				}

				// FIX 5 (bonded tracks) ── mirror new from / duration onto
				// each trimmed item's bonded partner so avatar-video and
				// voice-audio of the same block stay aligned.
				const mirroredItems = mirrorTimingsToBondedPartners({
					items: newState.undoableState.items,
					changedIds: newSelectedItems,
				});
				if (mirroredItems !== newState.undoableState.items) {
					newState = {
						...newState,
						undoableState: {
							...newState.undoableState,
							items: mirroredItems,
						},
					};
				}

				return newState;
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

				for (const itemId of newSelectedItems) {
					newState = unmarkItemAsBeingTrimmed({
						state: newState,
						itemId,
					});
				}

				newState = applySnapPoint({
					state: newState,
					snapPoint: null,
				});

				return newState;
			},
			commitToUndoStack: false,
		});

		onDragEnd?.();

		window.removeEventListener('pointermove', onPointerMove);
		window.removeEventListener('pointerup', onPointerUp);
		timelineScrollContainerRef.current?.removeEventListener('scroll', onScroll);
	};

	window.addEventListener('pointermove', onPointerMove);
	window.addEventListener('pointerup', onPointerUp);
	timelineScrollContainerRef.current?.addEventListener('scroll', onScroll);
};

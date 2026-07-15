import {PlayerRef} from '@remotion/player';
import React, {
	createContext,
	useCallback,
	useContext,
	useMemo,
	useState,
} from 'react';
import {MAX_AUTOSCROLL_SPEED} from './constants';
import {EditorStarterItem} from './items/item-type';
import {getTrackHeight} from './state/items';
import {TrackType} from './state/types';
import {
	getItemLeftOffset,
	getItemRoundedPosition,
	getItemWidth,
	getOffsetOfTrack,
} from './utils/position-utils';
import {timelineScrollableContainerRef} from './utils/restore-scroll-after-zoom';
import {useTimelineContainerAutoScroll} from './utils/use-timeline-container-auto-scroll';

export interface ItemMeta {
	id: string;
	trackIndex: number;
	from: number;
	durationInFrames: number;
	initialOffset: {x: number; y: number};
	height: number;
}

interface DragOverlayState {
	isDragging: boolean;
	draggedItemIds: string[];
	itemsMeta: ItemMeta[];
	tracks: TrackType[] | null;
	cursorPosition: {x: number; y: number} | null;
	timelineWidth: number;
	visibleFrames: number;
	playerRef: React.RefObject<PlayerRef | null> | null;
	snappedPositions: Record<string, number> | null; // itemId -> snapped frame position
}

interface StartDragParams {
	itemIds: string[];
	clickedItemId: string;
	timelineWidth: number;
	visibleFrames: number;
	clickX: number;
	clickY: number;
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
}

interface DragOverlayContextValue extends DragOverlayState {
	startDrag: (params: StartDragParams) => void;
	updateCursorPosition: (x: number, y: number) => void;
	stopDrag: () => void;
	setSnappedPositions: (positions: Record<string, number> | null) => void;
}

const DragOverlayContext = createContext<DragOverlayContextValue | null>(null);

export const useDragOverlay = () => {
	const context = useContext(DragOverlayContext);
	if (!context) {
		throw new Error('useDragOverlay must be used within a DragOverlayProvider');
	}
	return context;
};

interface DragOverlayProviderProps {
	children: React.ReactNode;
	playerRef: React.RefObject<PlayerRef | null>;
}

const isSamePositions = ({
	positions1,
	positions2,
}: {
	positions1: Record<string, number> | null;
	positions2: Record<string, number> | null;
}) => {
	if (positions1 === positions2) {
		return true;
	}
	if (positions1 === null || positions2 === null) {
		return false;
	}
	if (Object.keys(positions1).length !== Object.keys(positions2).length) {
		return false;
	}
	for (const key of Object.keys(positions1)) {
		if (positions1[key] !== positions2[key]) {
			return false;
		}
	}
	return true;
};

export const DragOverlayProvider: React.FC<DragOverlayProviderProps> = ({
	children,
	playerRef,
}) => {
	const [state, setState] = useState<DragOverlayState>({
		isDragging: false,
		draggedItemIds: [],
		itemsMeta: [],
		cursorPosition: null,
		timelineWidth: 0,
		visibleFrames: 0,
		playerRef: playerRef || null,
		tracks: [],
		snappedPositions: null,
	});

	useTimelineContainerAutoScroll({
		isDragging: state.isDragging,
		edgeThreshold: 50,
		maxScrollSpeed: MAX_AUTOSCROLL_SPEED,
		xAxis: true,
		yAxis: true,
	});

	const startDrag = useCallback(
		(params: StartDragParams) => {
			const {
				itemIds,
				clickedItemId,
				timelineWidth,
				visibleFrames,
				clickX,
				clickY,
				tracks,
				items,
			} = params;
			// Find the track index of the clicked item
			let clickedTrackIndex = -1;
			for (let i = 0; i < tracks.length; i++) {
				if (tracks[i].items.some((item) => item === clickedItemId)) {
					clickedTrackIndex = i;
					break;
				}
			}

			if (clickedTrackIndex === -1) {
				throw new Error('Clicked item not found in tracks');
			}

			// Get the timeline container using the existing ref
			const containerRect =
				timelineScrollableContainerRef.current?.getBoundingClientRect();

			if (!containerRect) {
				return;
			}

			// Build metadata for each dragged item
			const itemsMeta: ItemMeta[] = [];

			for (const itemId of itemIds) {
				// Find the item and its track
				let foundTrackIndex = -1;
				let foundItem: string | null = null;

				for (let i = 0; i < tracks.length; i++) {
					const item = tracks[i].items.find((it) => it === itemId);
					if (item) {
						foundTrackIndex = i;
						foundItem = item;
						break;
					}
				}

				if (!foundItem || foundTrackIndex === -1) {
					continue;
				}

				const rawItemLeft = getItemLeftOffset({
					timelineWidth,
					totalDurationInFrames: visibleFrames,
					from: items[foundItem].from,
				});

				const {roundedLeft: itemLeft} = getItemRoundedPosition(
					rawItemLeft,
					getItemWidth({
						itemDurationInFrames: items[foundItem].durationInFrames,
						timelineWidth,
						totalDurationInFrames: visibleFrames,
					}),
				);

				// Calculate the absolute position of the timeline item
				const itemTop =
					containerRect.top +
					getOffsetOfTrack({trackIndex: foundTrackIndex, tracks, items});
				const itemAbsoluteLeft = containerRect.left + itemLeft;

				// Calculate the offset from click position to item's top-left corner
				const offsetX = clickX - itemAbsoluteLeft;
				const offsetY = clickY - itemTop;

				const height = getTrackHeight({
					track: tracks[foundTrackIndex],
					items,
				});

				itemsMeta.push({
					id: itemId,
					trackIndex: foundTrackIndex,
					from: items[foundItem].from,
					durationInFrames: items[foundItem].durationInFrames,
					initialOffset: {x: offsetX, y: offsetY},
					height,
				});
			}

			setState({
				isDragging: true,
				draggedItemIds: itemIds,
				itemsMeta,
				cursorPosition: {x: clickX, y: clickY},
				timelineWidth,
				visibleFrames,
				playerRef: playerRef || null,
				tracks,
				snappedPositions: null,
			});
		},
		[playerRef],
	);

	const updateCursorPosition = useCallback((x: number, y: number) => {
		setState((prev) => ({
			...prev,
			cursorPosition: {x, y},
		}));
	}, []);

	const setSnappedPositions = useCallback(
		(positions: Record<string, number> | null) => {
			setState((prev) => {
				if (
					isSamePositions({
						positions1: positions,
						positions2: prev.snappedPositions,
					})
				) {
					return prev;
				}

				return {
					...prev,
					snappedPositions: positions,
				};
			});
		},
		[],
	);

	const stopDrag = useCallback(() => {
		setState({
			isDragging: false,
			draggedItemIds: [],
			itemsMeta: [],
			cursorPosition: null,
			timelineWidth: 0,
			visibleFrames: 0,
			playerRef: null,
			tracks: null,
			snappedPositions: null,
		});
	}, []);

	const contextValue = useMemo(
		() => ({
			...state,
			startDrag,
			updateCursorPosition,
			stopDrag,
			setSnappedPositions,
		}),
		[state, startDrag, updateCursorPosition, stopDrag, setSnappedPositions],
	);

	return (
		<DragOverlayContext.Provider value={contextValue}>
			{children}
		</DragOverlayContext.Provider>
	);
};

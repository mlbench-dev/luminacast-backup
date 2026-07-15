import {EditorStarterItem} from '../../../items/item-type';
import {TrackType} from '../../../state/types';
import {mirrorTimingsToBondedPartners} from '../../../utils/bonded-pair';
import {removeEmptyTracks} from '../../../utils/remove-empty-tracks';
import {DragPreviewState, PreviewPosition} from '../../drag-preview-provider';
import {insertNewTracks} from './insert-new-tracks';
import {TrackInsertions} from './types';

const calculateOriginalTrackIndex = ({
	currentIndex,
	trackInsertions,
}: {
	currentIndex: number;
	trackInsertions: TrackInsertions | null;
}): number => {
	if (!trackInsertions) {
		return currentIndex;
	}

	switch (trackInsertions.type) {
		case 'top':
			return currentIndex - trackInsertions.count;
		case 'bottom':
			return currentIndex;
		case 'between':
			if (currentIndex < trackInsertions.trackIndex) {
				// Tracks before insertion point keep their original index
				return currentIndex;
			}
			if (currentIndex < trackInsertions.trackIndex + trackInsertions.count) {
				// New tracks have no original index
				return -1;
			}
			// Tracks after insertion point are shifted back
			return currentIndex - trackInsertions.count;
		default:
			throw new Error(
				`Unexpected trackInsertions: ${JSON.stringify(trackInsertions satisfies never)}`,
			);
	}
};

const getTrackItemsWithoutDraggedOnes = ({
	originalIdx,
	prevTracks,
	itemsToDrag,
}: {
	originalIdx: number;
	prevTracks: TrackType[];
	itemsToDrag: string[];
}): string[] => {
	if (originalIdx < 0 || originalIdx >= prevTracks.length) {
		return [];
	}

	return prevTracks[originalIdx].items.filter(
		(itemId) => !itemsToDrag.includes(itemId),
	);
};

const buildTrackItemsMap = ({
	expandedTracks,
	prevTracks,
	itemsToDrag,
	trackInsertions,
}: {
	expandedTracks: TrackType[];
	prevTracks: TrackType[];
	itemsToDrag: string[];
	trackInsertions: TrackInsertions | null;
}): Map<number, string[]> => {
	const trackItemsMap = new Map<number, string[]>();

	for (let idx = 0; idx < expandedTracks.length; idx++) {
		const originalIdx = calculateOriginalTrackIndex({
			currentIndex: idx,
			trackInsertions,
		});
		const trackItems = getTrackItemsWithoutDraggedOnes({
			originalIdx,
			prevTracks,
			itemsToDrag,
		});
		trackItemsMap.set(idx, trackItems);
	}

	return trackItemsMap;
};

const applyPositionsToMaps = ({
	trackItemsMap,
	items,
	newPositions,
}: {
	trackItemsMap: Map<number, string[]>;
	items: Record<string, EditorStarterItem>;
	newPositions: PreviewPosition[];
}): Record<string, EditorStarterItem> => {
	const newItems: Record<string, EditorStarterItem> = {...items};

	for (const newPos of newPositions) {
		const trackItems = trackItemsMap.get(newPos.trackIndex) || [];
		trackItems.push(newPos.id);
		trackItemsMap.set(newPos.trackIndex, trackItems);

		newItems[newPos.id] = {
			...items[newPos.id],
			from: newPos.from,
			durationInFrames: newPos.durationInFrames,
			isDraggingInTimeline: false,
		};
	}

	return newItems;
};

/**
 * Rebuilds tracks from the track items map, sorting items by position
 */
const rebuildTracksFromMap = ({
	expandedTracks,
	trackItemsMap,
	newItems,
}: {
	expandedTracks: TrackType[];
	trackItemsMap: Map<number, string[]>;
	newItems: Record<string, EditorStarterItem>;
}): TrackType[] => {
	return expandedTracks.map((track, idx) => {
		const trackItems = trackItemsMap.get(idx);
		if (!trackItems) {
			throw new Error(
				`Track items not found for index: ${idx}, expandedTracks length: ${expandedTracks.length}`,
			);
		}

		trackItems.sort((a, b) => {
			const itemA = newItems[a];
			const itemB = newItems[b];
			return itemA.from - itemB.from;
		});

		return {
			...track,
			items: trackItems,
		};
	});
};

export const applyNewPositionsToState = ({
	prevTracks,
	dragPreview,
	prevItems,
	shouldRemoveEmptyTracks,
}: {
	prevTracks: TrackType[];
	dragPreview: DragPreviewState;
	prevItems: Record<string, EditorStarterItem>;
	shouldRemoveEmptyTracks: boolean;
}): {tracks: TrackType[]; items: Record<string, EditorStarterItem>} => {
	const expandedTracks = insertNewTracks({
		tracks: prevTracks,
		trackInsertions: dragPreview.trackInsertions,
	});

	const trackItemsMap = buildTrackItemsMap({
		expandedTracks,
		prevTracks,
		itemsToDrag: dragPreview.itemsBeingDragged,
		trackInsertions: dragPreview.trackInsertions,
	});

	const newItemsRaw = applyPositionsToMaps({
		trackItemsMap,
		items: prevItems,
		newPositions: dragPreview.positions,
	});

	// FIX 5 (bonded tracks) ── after the drag positions have been applied,
	// mirror the new from / duration onto each item's bonded partner so the
	// avatar-video and voice-audio of the same block move together.
	const movedIds = dragPreview.positions.map((p) => p.id);
	const newItems = mirrorTimingsToBondedPartners({
		items: newItemsRaw,
		changedIds: movedIds,
	});

	const newTracks = rebuildTracksFromMap({
		expandedTracks,
		trackItemsMap,
		newItems,
	});

	const finalTracks = shouldRemoveEmptyTracks
		? removeEmptyTracks(newTracks)
		: newTracks;

	return {
		tracks: finalTracks,
		items: newItems,
	};
};

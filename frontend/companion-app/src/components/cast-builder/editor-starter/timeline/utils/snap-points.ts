import {EditorStarterItem} from '../../items/item-type';
import {TrackType} from '../../state/types';

export type SnapPoint = {
	frame: number;
	type: 'item-start' | 'item-end';
	itemId: string;
};

/**
 * Collects all potential snap points from timeline items
 */
export const collectSnapPoints = ({
	tracks,
	items,
	excludeItemIds,
}: {
	tracks: TrackType[];
	items: Record<string, EditorStarterItem>;
	excludeItemIds: string[];
}): SnapPoint[] => {
	const snapPoints: SnapPoint[] = [];

	// Collect snap points from all items (except those being dragged)
	for (const track of tracks) {
		for (const itemId of track.items) {
			if (excludeItemIds.includes(itemId)) {
				continue;
			}

			const item = items[itemId];
			if (!item) {
				continue;
			}

			// Add start position
			snapPoints.push({
				frame: item.from,
				type: 'item-start',
				itemId,
			});

			// Add end position
			snapPoints.push({
				frame: item.from + item.durationInFrames,
				type: 'item-end',
				itemId,
			});
		}
	}

	// Remove duplicates and sort by frame
	const uniqueSnapPoints = Array.from(
		new Map(snapPoints.map((sp) => [sp.frame, sp])).values(),
	);

	return uniqueSnapPoints.sort((a, b) => a.frame - b.frame);
};

/**
 * Finds the nearest snap point within the threshold
 */
export const findNearestSnapPoint = ({
	targetFrame,
	snapPoints,
	thresholdInFrames,
}: {
	targetFrame: number;
	snapPoints: SnapPoint[];
	thresholdInFrames: number;
}): SnapPoint | null => {
	if (snapPoints.length === 0) {
		return null;
	}

	// snapPoints are expected to be sorted by frame asc
	let lo = 0;
	let hi = snapPoints.length; // invariant: answer in [lo, hi)
	while (lo < hi) {
		const mid = (lo + hi) >>> 1;
		if (snapPoints[mid].frame < targetFrame) {
			lo = mid + 1;
		} else {
			hi = mid;
		}
	}

	// lo is the first index with frame >= targetFrame
	const candidates: SnapPoint[] = [];
	if (lo < snapPoints.length) {
		candidates.push(snapPoints[lo]);
	}
	if (lo - 1 >= 0) {
		candidates.push(snapPoints[lo - 1]);
	}

	let best: SnapPoint | null = null;
	let bestDist = thresholdInFrames;
	for (const c of candidates) {
		const d = Math.abs(c.frame - targetFrame);
		if (d <= bestDist) {
			bestDist = d;
			best = c;
		}
	}

	return best;
};

/**
 * Converts pixel threshold to frame threshold based on timeline zoom
 */
export const pixelsToFrames = ({
	pixels,
	timelineWidth,
	visibleFrames,
}: {
	pixels: number;
	timelineWidth: number;
	visibleFrames: number;
}): number => {
	const pixelsPerFrame = timelineWidth / visibleFrames;
	return Math.ceil(pixels / pixelsPerFrame);
};

/**
 * Applies snapping to a target frame position
 */
export const applySnapping = ({
	targetFrame,
	snapPoints,
	pixelThreshold,
	timelineWidth,
	visibleFrames,
	isSnappingEnabled,
}: {
	targetFrame: number;
	snapPoints: SnapPoint[];
	pixelThreshold: number;
	timelineWidth: number;
	visibleFrames: number;
	isSnappingEnabled: boolean;
}): {
	snappedFrame: number;
	activeSnapPoint: SnapPoint | null;
} => {
	if (!isSnappingEnabled || snapPoints.length === 0) {
		return {
			snappedFrame: targetFrame,
			activeSnapPoint: null,
		};
	}

	const thresholdInFrames = pixelsToFrames({
		pixels: pixelThreshold,
		timelineWidth,
		visibleFrames,
	});

	const nearestSnapPoint = findNearestSnapPoint({
		targetFrame,
		snapPoints,
		thresholdInFrames,
	});

	if (nearestSnapPoint) {
		return {
			snappedFrame: nearestSnapPoint.frame,
			activeSnapPoint: nearestSnapPoint,
		};
	}

	return {
		snappedFrame: targetFrame,
		activeSnapPoint: null,
	};
};

export type ItemEdge = {frame: number; type: 'left' | 'right'; itemId: string};

/**
 * Finds the best snap point from multiple item edges
 */
export const findBestSnapForMultipleEdges = ({
	itemEdges,
	snapPoints,
	pixelThreshold,
	timelineWidth,
	visibleFrames,
	isSnappingEnabled,
}: {
	itemEdges: ItemEdge[];
	snapPoints: SnapPoint[];
	pixelThreshold: number;
	timelineWidth: number;
	visibleFrames: number;
	isSnappingEnabled: boolean;
}): {
	snapOffset: number | null;
	activeSnapPoint: SnapPoint | null;
	snappedEdge: ItemEdge | null;
} => {
	if (!isSnappingEnabled || snapPoints.length === 0 || itemEdges.length === 0) {
		return {
			snapOffset: null,
			activeSnapPoint: null,
			snappedEdge: null,
		};
	}

	const thresholdInFrames = pixelsToFrames({
		pixels: pixelThreshold,
		timelineWidth,
		visibleFrames,
	});

	let bestSnapPoint: SnapPoint | null = null;
	let bestEdge: (typeof itemEdges)[0] | null = null;
	let minDistance = thresholdInFrames + 1;

	// Check each edge against all snap points
	for (const edge of itemEdges) {
		const nearestSnapPoint = findNearestSnapPoint({
			targetFrame: edge.frame,
			snapPoints,
			thresholdInFrames,
		});

		if (nearestSnapPoint) {
			const distance = Math.abs(nearestSnapPoint.frame - edge.frame);
			if (distance < minDistance) {
				minDistance = distance;
				bestSnapPoint = nearestSnapPoint;
				bestEdge = edge;
			}
		}
	}

	if (bestSnapPoint && bestEdge) {
		const snapOffset = bestSnapPoint.frame - bestEdge.frame;
		return {
			snapOffset,
			activeSnapPoint: bestSnapPoint,
			snappedEdge: bestEdge,
		};
	}

	return {
		snapOffset: null,
		activeSnapPoint: null,
		snappedEdge: null,
	};
};

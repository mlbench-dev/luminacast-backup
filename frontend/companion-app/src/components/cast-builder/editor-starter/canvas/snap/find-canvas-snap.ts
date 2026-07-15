import {CANVAS_SNAP_THRESHOLD_PIXELS} from '../../constants';
import {
	CanvasSnapPoint,
	CanvasSnapResult,
	CanvasSnapTarget,
	ItemSnapEdge,
	SelectionBounds,
} from './canvas-snap-types';

type EdgeCheck = {
	edge: ItemSnapEdge;
	position: number;
};

const EPSILON = 0.000001;

export const findCanvasSnap = ({
	selectionBounds,
	targets,
	scale,
}: {
	selectionBounds: SelectionBounds;
	targets: CanvasSnapTarget[];
	scale: number;
}): CanvasSnapResult => {
	// Convert threshold from screen pixels to composition units
	const thresholdInCompositionUnits = CANVAS_SNAP_THRESHOLD_PIXELS / scale;

	const horizontalTargets = targets.filter(
		(t) => t.orientation === 'horizontal',
	);
	const verticalTargets = targets.filter((t) => t.orientation === 'vertical');

	// Find best horizontal snap (affects X position)
	const horizontalSnap = findBestSnapForOrientation({
		bounds: selectionBounds,
		targets: horizontalTargets,
		threshold: thresholdInCompositionUnits,
		getEdges: (bounds) => [
			{edge: 'left', position: bounds.left},
			{edge: 'center-x', position: bounds.centerX},
			{edge: 'right', position: bounds.right},
		],
	});

	// Find best vertical snap (affects Y position)
	const verticalSnap = findBestSnapForOrientation({
		bounds: selectionBounds,
		targets: verticalTargets,
		threshold: thresholdInCompositionUnits,
		getEdges: (bounds) => [
			{edge: 'top', position: bounds.top},
			{edge: 'center-y', position: bounds.centerY},
			{edge: 'bottom', position: bounds.bottom},
		],
	});

	const activeSnapPoints: CanvasSnapPoint[] = [];
	if (horizontalSnap) {
		activeSnapPoints.push(horizontalSnap.snapPoint);
	}
	if (verticalSnap) {
		activeSnapPoints.push(verticalSnap.snapPoint);
	}

	return {
		snapOffsetX: horizontalSnap?.offset ?? null,
		snapOffsetY: verticalSnap?.offset ?? null,
		activeSnapPoints,
	};
};

const findBestSnapForOrientation = ({
	bounds,
	targets,
	threshold,
	getEdges,
}: {
	bounds: SelectionBounds;
	targets: CanvasSnapTarget[];
	threshold: number;
	getEdges: (bounds: SelectionBounds) => EdgeCheck[];
}): {offset: number; snapPoint: CanvasSnapPoint} | null => {
	const edges = getEdges(bounds);
	let bestSnaps: {offset: number; snapPoint: CanvasSnapPoint}[] = [];
	let bestDistance = Infinity;

	for (const target of targets) {
		for (const {edge, position} of edges) {
			if (target.type === 'horizontal-center' && edge !== 'center-x') {
				continue;
			}
			if (target.type === 'vertical-center' && edge !== 'center-y') {
				continue;
			}
			if (target.type === 'left' && edge === 'center-x') {
				continue;
			}
			if (target.type === 'right' && edge === 'center-x') {
				continue;
			}
			if (target.type === 'top' && edge === 'center-y') {
				continue;
			}
			if (target.type === 'bottom' && edge === 'center-y') {
				continue;
			}

			const distance = Math.abs(position - target.position);
			if (distance <= threshold && distance <= bestDistance + EPSILON) {
				if (distance < bestDistance - EPSILON) {
					bestSnaps = [];
				}
				bestDistance = distance;
				const offset = target.position - position;
				bestSnaps.push({
					offset,
					snapPoint: {
						target,
						edge,
						distance,
					},
				});
			}
		}
	}

	// For a full width/height item being centered, all of left / right / center edges with align.
	// If there are multiple matches, we fix it to the center.
	if (bestSnaps.length > 1) {
		const chooseCenter = bestSnaps.find(
			(s) =>
				s.snapPoint.target.type === 'horizontal-center' ||
				s.snapPoint.target.type === 'vertical-center',
		);
		if (chooseCenter) {
			return chooseCenter;
		}
	}

	return bestSnaps[0] ?? null;
};

export const findCanvasSnapForResize = ({
	selectionBounds,
	targets,
	scale,
	resizingEdges,
}: {
	selectionBounds: SelectionBounds;
	targets: CanvasSnapTarget[];
	scale: number;
	resizingEdges: {
		left: boolean;
		right: boolean;
		top: boolean;
		bottom: boolean;
	};
}): CanvasSnapResult => {
	const thresholdInCompositionUnits = CANVAS_SNAP_THRESHOLD_PIXELS / scale;

	const horizontalTargets = targets.filter(
		(t) => t.orientation === 'horizontal',
	);
	const verticalTargets = targets.filter((t) => t.orientation === 'vertical');

	// For resize, only check the edges being resized
	const horizontalEdges: EdgeCheck[] = [];
	if (resizingEdges.left) {
		horizontalEdges.push({edge: 'left', position: selectionBounds.left});
	}
	if (resizingEdges.right) {
		horizontalEdges.push({edge: 'right', position: selectionBounds.right});
	}

	const verticalEdges: EdgeCheck[] = [];
	if (resizingEdges.top) {
		verticalEdges.push({edge: 'top', position: selectionBounds.top});
	}
	if (resizingEdges.bottom) {
		verticalEdges.push({edge: 'bottom', position: selectionBounds.bottom});
	}

	const horizontalSnap =
		horizontalEdges.length > 0
			? findBestSnapForOrientation({
					bounds: selectionBounds,
					targets: horizontalTargets,
					threshold: thresholdInCompositionUnits,
					getEdges: () => horizontalEdges,
				})
			: null;

	const verticalSnap =
		verticalEdges.length > 0
			? findBestSnapForOrientation({
					bounds: selectionBounds,
					targets: verticalTargets,
					threshold: thresholdInCompositionUnits,
					getEdges: () => verticalEdges,
				})
			: null;

	const activeSnapPoints: CanvasSnapPoint[] = [];
	if (horizontalSnap) {
		activeSnapPoints.push(horizontalSnap.snapPoint);
	}

	if (verticalSnap) {
		activeSnapPoints.push(verticalSnap.snapPoint);
	}

	return {
		snapOffsetX: horizontalSnap?.offset ?? null,
		snapOffsetY: verticalSnap?.offset ?? null,
		activeSnapPoints,
	};
};

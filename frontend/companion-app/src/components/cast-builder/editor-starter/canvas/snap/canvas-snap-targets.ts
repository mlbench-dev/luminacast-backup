import {CanvasSnapTarget} from './canvas-snap-types';

/**
 * Generates all snap targets for the canvas.
 * Currently only generates canvas boundary targets (edges and center).
 * Could be extended to include other items in the future.
 */
export const getCanvasSnapTargets = (
	compositionWidth: number,
	compositionHeight: number,
): CanvasSnapTarget[] => {
	const targets: CanvasSnapTarget[] = [];

	// Horizontal targets (for snapping left/right/centerX of items)
	// These are vertical lines on the canvas (orientation: 'horizontal' means they snap horizontal positions)
	targets.push({
		position: 0,
		type: 'left',
		orientation: 'horizontal',
	});
	targets.push({
		position: compositionWidth / 2,
		type: 'horizontal-center',
		orientation: 'horizontal',
	});
	targets.push({
		position: compositionWidth,
		type: 'right',
		orientation: 'horizontal',
	});

	// Vertical targets (for snapping top/bottom/centerY of items)
	// These are horizontal lines on the canvas (orientation: 'vertical' means they snap vertical positions)
	targets.push({
		position: 0,
		type: 'top',
		orientation: 'vertical',
	});
	targets.push({
		position: compositionHeight / 2,
		type: 'vertical-center',
		orientation: 'vertical',
	});
	targets.push({
		position: compositionHeight,
		type: 'bottom',
		orientation: 'vertical',
	});

	return targets;
};

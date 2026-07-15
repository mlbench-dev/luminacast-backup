/**
 * Canvas snap types for snap-to-center and snap-to-edges functionality.
 */

/**
 * Types of snap targets on the canvas.
 */
export type CanvasSnapTargetType =
	| 'left'
	| 'right'
	| 'top'
	| 'bottom'
	| 'horizontal-center'
	| 'vertical-center';

/**
 * Orientation of a snap target line for rendering.
 */
export type CanvasSnapOrientation = 'horizontal' | 'vertical';

/**
 * Represents a position on the canvas that items can snap to.
 */
export type CanvasSnapTarget = {
	/** Pixel position (x for vertical lines, y for horizontal lines) */
	position: number;
	/** Target type identifier */
	type: CanvasSnapTargetType;
	/** Line orientation for rendering */
	orientation: CanvasSnapOrientation;
};

/**
 * Which edge or center of an item is snapping.
 */
export type ItemSnapEdge =
	| 'left'
	| 'right'
	| 'center-x'
	| 'top'
	| 'bottom'
	| 'center-y';

/**
 * Represents an active snap alignment (item edge/center aligned to target).
 */
export type CanvasSnapPoint = {
	/** The snap target being aligned to */
	target: CanvasSnapTarget;
	/** Which item edge/center is snapping */
	edge: ItemSnapEdge;
	/** Distance that was snapped (for debugging) */
	distance: number;
};

/**
 * Result of a snap calculation attempt.
 */
export type CanvasSnapResult = {
	/** X offset to apply for snap (null if no horizontal snap) */
	snapOffsetX: number | null;
	/** Y offset to apply for snap (null if no vertical snap) */
	snapOffsetY: number | null;
	/** All active snaps (0-2: up to one horizontal + one vertical) */
	activeSnapPoints: CanvasSnapPoint[];
};

/**
 * Bounding box representation for snap calculations.
 */
export type ItemBounds = {
	/** Left edge X position */
	left: number;
	/** Top edge Y position */
	top: number;
	/** Right edge X position (left + width) */
	right: number;
	/** Bottom edge Y position (top + height) */
	bottom: number;
	/** Horizontal center (left + width/2) */
	centerX: number;
	/** Vertical center (top + height/2) */
	centerY: number;
	/** Total width */
	width: number;
	/** Total height */
	height: number;
};

/**
 * Bounding box for multiple selected items.
 */
export type SelectionBounds = {
	/** Minimum left edge across all items */
	left: number;
	/** Minimum top edge across all items */
	top: number;
	/** Total width */
	width: number;
	/** Total height */
	height: number;
	/** Maximum right edge across all items */
	right: number;
	/** Maximum bottom edge across all items */
	bottom: number;
	/** Center of combined bounds */
	centerX: number;
	/** Center of combined bounds */
	centerY: number;
};

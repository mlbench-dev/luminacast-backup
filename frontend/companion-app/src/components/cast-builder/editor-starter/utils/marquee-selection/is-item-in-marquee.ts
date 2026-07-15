import {MarqueeSelection} from '../../marquee-selection';

export function isItemInMarquee({
	marquee,
	itemX,
	itemY,
	itemEndX,
	itemEndY,
}: {
	marquee: MarqueeSelection;
	itemX: number;
	itemY: number;
	itemEndX: number;
	itemEndY: number;
}): boolean {
	// Normalize marquee coordinates
	const marqueeLeft = Math.min(marquee.start.x, marquee.end.x);
	const marqueeRight = Math.max(marquee.start.x, marquee.end.x);
	const marqueeTop = Math.min(marquee.start.y, marquee.end.y);
	const marqueeBottom = Math.max(marquee.start.y, marquee.end.y);

	// Normalize item coordinates
	const itemLeft = Math.min(itemX, itemEndX);
	const itemRight = Math.max(itemX, itemEndX);
	const itemTop = Math.min(itemY, itemEndY);
	const itemBottom = Math.max(itemY, itemEndY);

	// Check for intersection
	const isIntersecting =
		marqueeLeft <= itemRight &&
		marqueeRight >= itemLeft &&
		marqueeTop <= itemBottom &&
		marqueeBottom >= itemTop;

	return isIntersecting;
}

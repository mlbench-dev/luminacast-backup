import React, {useMemo} from 'react';
import {createPortal} from 'react-dom';
import {TIMELINE_HORIZONTAL_PADDING} from './constants';
import {ItemMeta, useDragOverlay} from './drag-overlay-provider';
import {EditorStarterItem} from './items/item-type';
import {TimelineItemDragOverlay} from './timeline/timeline-item/timeline-item-drag-overlay';
import {getItemLeftOffset} from './utils/position-utils';
import {timelineScrollableContainerRef} from './utils/restore-scroll-after-zoom';
import {useAllItems} from './utils/use-context';
import {Z_INDEX_DRAG_OVERLAY} from './z-indices';

const DragOverlayItem: React.FC<{
	item: EditorStarterItem;
	meta: ItemMeta;
	cursorPosition: {x: number; y: number};
	timelineWidth: number;
	visibleFrames: number;
	trackMuted: boolean;
	snappedFrame: number | undefined;
}> = ({
	item,
	meta,
	cursorPosition,
	timelineWidth,
	visibleFrames,
	trackMuted,
	snappedFrame,
}) => {
	const style: React.CSSProperties = useMemo(() => {
		let left: number;

		if (snappedFrame !== undefined) {
			// When snapping, calculate absolute position based on snapped frame
			const containerRect =
				timelineScrollableContainerRef.current?.getBoundingClientRect();
			if (!containerRect) {
				throw new Error('Container rect not found');
			}
			const itemLeft = getItemLeftOffset({
				timelineWidth,
				totalDurationInFrames: visibleFrames,
				from: snappedFrame,
			});
			left = containerRect.left + itemLeft + TIMELINE_HORIZONTAL_PADDING;
		} else {
			// No snapping, use cursor position with offset
			left =
				cursorPosition.x - meta.initialOffset.x + TIMELINE_HORIZONTAL_PADDING;
		}

		return {
			position: 'fixed',
			top: cursorPosition.y - meta.initialOffset.y,
			left,
			pointerEvents: 'none',
			zIndex: Z_INDEX_DRAG_OVERLAY,
		};
	}, [cursorPosition, meta, snappedFrame, timelineWidth, visibleFrames]);

	return (
		<div key={meta.id} style={style}>
			<TimelineItemDragOverlay
				item={item}
				timelineWidth={timelineWidth}
				visibleFrames={visibleFrames}
				height={meta.height}
				trackMuted={trackMuted}
			/>
		</div>
	);
};

const InnerDragOverlay: React.FC<{
	itemsMeta: ItemMeta[];
	cursorPosition: {x: number; y: number};
	timelineWidth: number;
	visibleFrames: number;
	trackMuted: boolean;
	snappedPositions: Record<string, number> | null;
}> = ({
	itemsMeta,
	cursorPosition,
	timelineWidth,
	visibleFrames,
	trackMuted,
	snappedPositions,
}) => {
	const allItems = useAllItems();

	return createPortal(
		<>
			{itemsMeta.map((meta) => {
				const item = allItems.items[meta.id];
				if (!item || !item.isDraggingInTimeline) {
					return null;
				}

				const snappedFrame = snappedPositions?.[meta.id];

				return (
					<DragOverlayItem
						key={meta.id}
						item={item}
						meta={meta}
						cursorPosition={cursorPosition}
						timelineWidth={timelineWidth}
						visibleFrames={visibleFrames}
						trackMuted={trackMuted}
						snappedFrame={snappedFrame}
					/>
				);
			})}
		</>,
		document.body,
	);
};

export const DraggingTimelineItems: React.FC = () => {
	const {
		isDragging,
		draggedItemIds,
		itemsMeta,
		cursorPosition,
		timelineWidth,
		visibleFrames,
		snappedPositions,
	} = useDragOverlay();

	if (!isDragging || draggedItemIds.length === 0 || !cursorPosition) {
		return null;
	}

	return (
		<InnerDragOverlay
			itemsMeta={itemsMeta}
			cursorPosition={cursorPosition}
			timelineWidth={timelineWidth}
			visibleFrames={visibleFrames}
			trackMuted={false}
			snappedPositions={snappedPositions}
		/>
	);
};

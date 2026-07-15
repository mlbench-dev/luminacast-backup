import React, {memo, useCallback, useMemo, useState} from 'react';
import {EditorStarterItem} from '../../../items/item-type';
import {isLeftClick} from '../../../utils/is-left-click';
import {
	useCurrentStateAsRef,
	useWriteContext,
} from '../../../utils/use-context';
import {useTimelineContainerAutoScroll} from '../../../utils/use-timeline-container-auto-scroll';
import {TimelineItemExtendHandle} from '../timeline-item-extend-handle';
import {onExtendHandler} from './on-extend-handler';

interface TimelineItemExtendHandles {
	item: EditorStarterItem;
	timelineWidth: number;
	width: number;
	height: number;
}

const EXTEND_HANDLE_TARGET_WIDTH = 6;

export const TimelineItemExtendHandles = memo(
	({item, timelineWidth, width, height}: TimelineItemExtendHandles) => {
		const {setState} = useWriteContext();
		const stateAsRef = useCurrentStateAsRef();
		const [isDragging, setIsDragging] = useState(false);

		useTimelineContainerAutoScroll({
			isDragging,
			edgeThreshold: 10,
			maxScrollSpeed: 5,
			xAxis: true,
			yAxis: false,
		});

		const onPointerDownHandleLeft = useCallback(
			(pointerDownEvent: React.PointerEvent<HTMLDivElement>) => {
				if (!isLeftClick(pointerDownEvent)) {
					return;
				}

				setIsDragging(true);

				pointerDownEvent.preventDefault();
				pointerDownEvent.stopPropagation();
				onExtendHandler({
					pointerDownEvent,
					setState,
					timelineWidth,
					stateAsRef,
					extend: {
						type: 'left',
						clickedItemId: item.id,
					},
					height,
					onDragEnd: () => setIsDragging(false),
				});
			},
			[setState, stateAsRef, timelineWidth, item.id, height],
		);

		const onPointerDownHandleRight = useCallback(
			(pointerDownEvent: React.PointerEvent<HTMLDivElement>) => {
				if (!isLeftClick(pointerDownEvent)) {
					return;
				}

				pointerDownEvent.preventDefault();
				pointerDownEvent.stopPropagation();

				setIsDragging(true);

				onExtendHandler({
					pointerDownEvent,
					setState,
					timelineWidth,
					stateAsRef,
					extend: {
						type: 'right',
						clickedItemId: item.id,
					},
					height,
					onDragEnd: () => setIsDragging(false),
				});
			},
			[setState, stateAsRef, timelineWidth, item.id, height],
		);

		const leftStyle = useMemo(() => {
			return {
				width: Math.max(1, Math.min(width / 2, EXTEND_HANDLE_TARGET_WIDTH)),
				left: -1,
			};
		}, [width]);

		const rightStyle = useMemo(() => {
			return {
				width: Math.max(1, Math.min(width / 2, EXTEND_HANDLE_TARGET_WIDTH)),
				right: -1,
			};
		}, [width]);

		return (
			<>
				<TimelineItemExtendHandle
					className="cursor-e-resize"
					onPointerDown={onPointerDownHandleLeft}
					style={leftStyle}
				/>
				<TimelineItemExtendHandle
					className="cursor-w-resize"
					onPointerDown={onPointerDownHandleRight}
					style={rightStyle}
				/>
			</>
		);
	},
);

TimelineItemExtendHandles.displayName = 'TimelineItemActions';

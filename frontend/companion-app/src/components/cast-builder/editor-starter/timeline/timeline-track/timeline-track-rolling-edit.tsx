import {useCallback, useMemo, useState} from 'react';
import {isLeftClick} from '../../utils/is-left-click';
import {getItemLeftOffset} from '../../utils/position-utils';
import {useCurrentStateAsRef, useWriteContext} from '../../utils/use-context';
import {useTimelineContainerAutoScroll} from '../../utils/use-timeline-container-auto-scroll';
import {onRollingEditHandler} from '../timeline-item/timeline-item-extend-handles/on-rolling-edit-handler';
import {useTimelineSize} from '../utils/use-timeline-size';

export type TimelineItemAdjacency = {
	previous: string;
	next: string;
	from: number;
};

export const ROLLING_EDIT_HANDLE_WIDTH = 4;

export const TimelineTrackRollingEdit: React.FC<{
	adjacency: TimelineItemAdjacency;
	visibleFrames: number;
	top: number;
	height: number;
}> = ({adjacency, visibleFrames, top, height}) => {
	const {timelineWidth} = useTimelineSize();
	const {setState} = useWriteContext();
	const stateAsRef = useCurrentStateAsRef();
	const [isDragging, setIsDragging] = useState(false);

	useTimelineContainerAutoScroll({
		isDragging,
		edgeThreshold: 5,
		maxScrollSpeed: 5,
		xAxis: true,
		yAxis: false,
	});

	if (timelineWidth === null) {
		throw new Error('Timeline width is null');
	}

	const left = getItemLeftOffset({
		timelineWidth,
		totalDurationInFrames: visibleFrames,
		from: adjacency.from,
	});

	const style: React.CSSProperties = useMemo(() => {
		return {
			top,
			height,
			left: left - ROLLING_EDIT_HANDLE_WIDTH / 2,
			width: ROLLING_EDIT_HANDLE_WIDTH,
			position: 'absolute',
			cursor: 'ew-resize',
		};
	}, [height, left, top]);

	const onPointerDown = useCallback(
		(e: React.PointerEvent<HTMLDivElement>) => {
			if (!isLeftClick(e)) {
				return;
			}
			e.preventDefault();
			e.stopPropagation();

			setIsDragging(true);

			onRollingEditHandler({
				pointerDownEvent: e,
				setState,
				timelineWidth,
				stateAsRef,
				adjacency,
				height,
				onDragEnd: () => setIsDragging(false),
			});
		},
		[timelineWidth, setState, stateAsRef, adjacency, height],
	);

	return <div style={style} onPointerDown={onPointerDown} />;
};

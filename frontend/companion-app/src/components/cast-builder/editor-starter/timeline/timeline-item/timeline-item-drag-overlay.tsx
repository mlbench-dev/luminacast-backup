import {memo, useMemo} from 'react';
import {EditorStarterItem} from '../../items/item-type';
import {
	getItemLeftOffset,
	getItemRoundedPosition,
	getItemWidth,
} from '../../utils/position-utils';
import {TimelineItemContent} from './timeline-item-content';
import {TimelineItemPreviewContainer} from './timeline-item-preview-container';

interface TimelineItemDragOverlayProps {
	item: EditorStarterItem;
	timelineWidth: number;
	visibleFrames: number;
	height: number;
	trackMuted: boolean;
}

// The reason we need a separate overlay is
// because most of the times you don't want to bind all the event listerers just for the preview
// so this is "presentational" component
const TimelineItemDragOverlay = memo(
	({
		item,
		timelineWidth,
		visibleFrames,
		height,
		trackMuted,
	}: TimelineItemDragOverlayProps) => {
		const itemLeft = getItemLeftOffset({
			timelineWidth,
			totalDurationInFrames: visibleFrames,
			from: item.from,
		});

		const timelineItemWidth = getItemWidth({
			itemDurationInFrames: item.durationInFrames,
			timelineWidth,
			totalDurationInFrames: visibleFrames,
		});

		const {width, roundedDifference} = getItemRoundedPosition(
			itemLeft,
			timelineItemWidth,
		);

		const style = useMemo(() => {
			return {
				width,
				height,
			};
		}, [width, height]);

		return (
			<TimelineItemPreviewContainer isSelected style={style}>
				<TimelineItemContent
					item={item}
					height={height}
					width={width}
					roundedDifference={roundedDifference}
					trackMuted={trackMuted}
				/>
			</TimelineItemPreviewContainer>
		);
	},
);

TimelineItemDragOverlay.displayName = 'TimelineItemDragOverlay';

export {TimelineItemDragOverlay};

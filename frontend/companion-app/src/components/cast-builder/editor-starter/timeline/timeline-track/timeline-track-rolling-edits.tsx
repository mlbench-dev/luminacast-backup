import React, {useMemo} from 'react';
import {EditorStarterItem} from '../../items/item-type';
import {
	TimelineItemAdjacency,
	TimelineTrackRollingEdit,
} from './timeline-track-rolling-edit';

export const TimelineTrackRollingEdits: React.FC<{
	items: string[];
	allItems: Record<string, EditorStarterItem>;
	visibleFrames: number;
	top: number;
	height: number;
}> = ({items, allItems, visibleFrames, top, height}) => {
	const itemsSortedByStartTime = React.useMemo(() => {
		return [...items].sort((a, b) => {
			return allItems[a].from - allItems[b].from;
		});
	}, [items, allItems]);

	const adjacencies = useMemo(() => {
		const adjancentItemsArray: TimelineItemAdjacency[] = [];
		for (let i = 0; i < itemsSortedByStartTime.length; i++) {
			const item = itemsSortedByStartTime[i];
			const nextItem = itemsSortedByStartTime[i + 1];
			if (!nextItem) {
				continue;
			}
			if (
				allItems[nextItem].from ===
				allItems[item].from + allItems[item].durationInFrames
			) {
				adjancentItemsArray.push({
					previous: item,
					next: nextItem,
					from: allItems[item].from + allItems[item].durationInFrames,
				});
			}
		}

		return adjancentItemsArray;
	}, [itemsSortedByStartTime, allItems]);

	return adjacencies.map((adjacency) => {
		return (
			<TimelineTrackRollingEdit
				key={adjacency.previous}
				adjacency={adjacency}
				visibleFrames={visibleFrames}
				top={top}
				height={height}
			/>
		);
	});
};

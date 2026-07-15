import React from 'react';

export const playheadRef = React.createRef<HTMLDivElement>();
export const timelineScrollableContainerRef = React.createRef<HTMLDivElement>();
export const timelineScrollContainerRef = React.createRef<HTMLDivElement>();
export const timelineRightSide = React.createRef<HTMLDivElement>();
export const timelineContainerRef = React.createRef<HTMLDivElement>();
export const sidePanelRef = React.createRef<HTMLDivElement>();

export const restoreScrollAfterZoom = () => {
	const playheadOffsetBefore = playheadRef.current?.offsetLeft;
	if (playheadOffsetBefore === undefined) {
		throw new Error('Playhead ref not found');
	}

	return {
		restore: () => {
			const leftOffsetNow = playheadRef.current?.offsetLeft;
			if (leftOffsetNow === undefined) {
				throw new Error('Playhead ref not found');
			}
			if (timelineScrollableContainerRef.current === undefined) {
				throw new Error('Scroll container ref not found');
			}

			const difference = leftOffsetNow - playheadOffsetBefore;
			timelineScrollContainerRef.current!.scrollLeft += difference;
		},
	};
};

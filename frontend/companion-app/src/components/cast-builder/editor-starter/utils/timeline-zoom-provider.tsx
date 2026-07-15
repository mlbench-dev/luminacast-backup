import {createContext, useMemo, useState} from 'react';
import {flushSync} from 'react-dom';
import {MIN_TIMELINE_ZOOM} from '../constants';
import {restoreScrollAfterZoom} from './restore-scroll-after-zoom';

interface TimelineZoomState {
	zoom: number;
	setZoom: (zoom: number | ((prev: number) => number)) => void;
}

export const TimelineZoomContext = createContext<TimelineZoomState>({
	zoom: 1,
	setZoom: () => {},
});

type TimelineProviderProps = {
	children: React.ReactNode;
};

/**
 * This is the provider for the timeline zoom
 * It's separated from the main provider to avoid unnecessary re-renders
 */
export const TimelineZoomProvider = ({children}: TimelineProviderProps) => {
	const [zoom, setZoom] = useState(MIN_TIMELINE_ZOOM);

	const state = useMemo(
		() => ({
			zoom,
			setZoom: (newZoom: number | ((prev: number) => number)) => {
				const restore = restoreScrollAfterZoom();
				flushSync(() => {
					setZoom(newZoom);
				});

				restore.restore();
			},
		}),
		[zoom, setZoom],
	);

	return (
		<TimelineZoomContext.Provider value={state}>
			{children}
		</TimelineZoomContext.Provider>
	);
};

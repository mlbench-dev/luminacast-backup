import {memo, useMemo} from 'react';
import {TRACK_DIVIDER_HEIGHT} from '../../state/items';
import {clsx} from '../../utils/clsx';
import {sidePanelRef} from '../../utils/restore-scroll-after-zoom';
import {useTimelineHeight} from '../../utils/use-context';
import {Z_INDEX_SIDE_PANEL} from '../../z-indices';
import {TICKS_HEIGHT} from '../ticks/constants';
import {
	getTracksHeight,
	TimelineTrackAndLayout,
} from '../utils/drag/calculate-track-heights';
import {TrackHeader} from './timeline-track-header';

export const SIDE_PANEL_WIDTH = 110;

export const SidePanel: React.FC<{
	tracks: TimelineTrackAndLayout[];
	inbetweenTrackDropTrackIndex: number | null;
}> = memo(({tracks: tracksAndLayout, inbetweenTrackDropTrackIndex}) => {
	const sidePanelStyle = useMemo(
		() => ({
			minWidth: SIDE_PANEL_WIDTH,
			zIndex: Z_INDEX_SIDE_PANEL,
		}),
		[],
	);

	const timelineHeight = useTimelineHeight();

	const trackHeadersStyle = useMemo(
		() => ({
			minHeight: timelineHeight + TICKS_HEIGHT,
			height: getTracksHeight({tracks: tracksAndLayout}) + TICKS_HEIGHT,
			paddingTop: TICKS_HEIGHT,
		}),
		[tracksAndLayout, timelineHeight],
	);

	const patchStyle = useMemo(() => {
		return {
			height: TICKS_HEIGHT,
			width: SIDE_PANEL_WIDTH - 1,
			zIndex: Z_INDEX_SIDE_PANEL,
		};
	}, []);

	const trackBorder = useMemo(() => {
		return {
			borderBottomWidth: TRACK_DIVIDER_HEIGHT,
		};
	}, []);

	return (
		<>
			<div
				ref={sidePanelRef}
				className="bg-editor-starter-bg sticky left-0 flex h-full flex-col"
				style={sidePanelStyle}
			>
				<div
					id="track-headers"
					className="border-editor-starter-border sticky left-0 flex w-full shrink-0 flex-col border-r-[1px]"
					style={trackHeadersStyle}
				>
					{tracksAndLayout.map((trackAndLayout, idx) => (
						<div
							className={clsx(
								'flex w-full select-none',
								inbetweenTrackDropTrackIndex !== null &&
									idx === inbetweenTrackDropTrackIndex - 1
									? 'border-editor-starter-accent'
									: 'border-black/20',
							)}
							key={trackAndLayout.track.id}
							style={trackBorder}
						>
							<TrackHeader
								name={`${tracksAndLayout.length - idx}`}
								trackAndLayout={trackAndLayout}
							/>
						</div>
					))}
				</div>
			</div>
			<div
				style={patchStyle}
				className="bg-editor-starter-panel absolute top-0 left-0"
			/>
		</>
	);
});

SidePanel.displayName = 'SidePanel';

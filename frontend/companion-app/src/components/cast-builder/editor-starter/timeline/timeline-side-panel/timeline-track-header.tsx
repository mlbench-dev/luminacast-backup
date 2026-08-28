import {useCallback, useMemo} from 'react';
import {FEATURE_HIDE_TRACKS} from '../../flags';
import {TimelineTrackAndLayout} from '../utils/drag/calculate-track-heights';
import {TimelineHideTrack} from './timeline-hide-track';
import {useWriteContext} from '../../utils/use-context';
import {deleteTrack} from '../../state/actions/delete-track';
import {confirmAction} from '@/lib/swal';

export const TrackHeader = ({
	name,
	trackAndLayout,
}: {
	name: string;
	trackAndLayout: TimelineTrackAndLayout;
}) => {
	const {setState} = useWriteContext();

	const style = useMemo(() => {
		return {
			height: trackAndLayout.height,
		};
	}, [trackAndLayout]);

	const handleDelete = useCallback(
		async (e: React.MouseEvent) => {
			e.stopPropagation();
			const itemCount = trackAndLayout.track.items.length;
			const ok = await confirmAction({
				title: 'Delete this track?',
				text: itemCount
					? `${itemCount} clip${itemCount === 1 ? '' : 's'} on this track will be removed from the timeline.`
					: 'This empty track will be removed.',
				confirmButtonText: 'Delete',
				cancelButtonText: 'Cancel',
				icon: 'warning',
			});
			if (!ok) return;
			setState({
				update: (state) => deleteTrack(state, trackAndLayout.track.id),
				commitToUndoStack: true,
			});
		},
		[setState, trackAndLayout.track.id, trackAndLayout.track.items.length],
	);

	return (
		<div
			className="group bg-editor-starter-bg flex w-full shrink-0 items-center gap-2 truncate pl-4 text-xs"
			style={style}
		>
			<div className="w-4 text-right text-neutral-400">{name}</div>
			<div className="flex items-center">
				{FEATURE_HIDE_TRACKS && (
					<TimelineHideTrack track={trackAndLayout.track} />
				)}
				{/* Delete track — single click opens a confirm dialog. */}
				<button
					onClick={handleDelete}
					onPointerDown={(e) => e.stopPropagation()}
					className="px-1 py-0.5 text-[11px] font-medium rounded transition-colors opacity-0 group-hover:opacity-100 text-neutral-500 hover:text-red-400 hover:bg-red-500/10"
					title="Delete track"
				>
					✕
				</button>
			</div>
		</div>
	);
};

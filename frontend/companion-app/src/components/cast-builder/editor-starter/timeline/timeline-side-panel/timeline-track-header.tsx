import {useCallback, useState, useMemo} from 'react';
import {FEATURE_HIDE_TRACKS, FEATURE_MUTE_TRACKS} from '../../flags';
import {TimelineTrackAndLayout} from '../utils/drag/calculate-track-heights';
import {TimelineHideTrack} from './timeline-hide-track';
import {TimelineMuteTrack} from './timeline-mute-track';
import {useWriteContext} from '../../utils/use-context';
import {soloTrack, unsoloTrack} from '../../state/actions/solo-track';
import {deleteTrack} from '../../state/actions/delete-track';

export const TrackHeader = ({
	name,
	trackAndLayout,
}: {
	name: string;
	trackAndLayout: TimelineTrackAndLayout;
}) => {
	const {setState} = useWriteContext();
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

	const style = useMemo(() => {
		return {
			height: trackAndLayout.height,
		};
	}, [trackAndLayout]);

	const isSolo = trackAndLayout.track.solo ?? false;

	const handleSoloToggle = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			setState({
				update: (state) =>
					isSolo
						? unsoloTrack(state, trackAndLayout.track.id)
						: soloTrack(state, trackAndLayout.track.id),
				commitToUndoStack: true,
			});
		},
		[isSolo, setState, trackAndLayout.track.id],
	);

	const handleDelete = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			if (!showDeleteConfirm) {
				setShowDeleteConfirm(true);
				return;
			}
			setState({
				update: (state) => deleteTrack(state, trackAndLayout.track.id),
				commitToUndoStack: true,
			});
			setShowDeleteConfirm(false);
		},
		[showDeleteConfirm, setState, trackAndLayout.track.id],
	);

	return (
		<div
			className="group bg-editor-starter-bg flex w-full shrink-0 items-center gap-2 truncate pl-4 text-xs"
			style={style}
			onMouseLeave={() => setShowDeleteConfirm(false)}
		>
			<div className="w-4 text-right text-neutral-400">{name}</div>
			<div className="flex items-center">
				{FEATURE_HIDE_TRACKS && (
					<TimelineHideTrack track={trackAndLayout.track} />
				)}
				{FEATURE_MUTE_TRACKS && (
					<TimelineMuteTrack track={trackAndLayout.track} />
				)}
				{/* Phase 4.8.5 — Solo toggle */}
				<button
					onClick={handleSoloToggle}
					onPointerDown={(e) => e.stopPropagation()}
					className={`px-1 py-0.5 text-[9px] font-bold rounded transition-colors ${
						isSolo
							? 'bg-yellow-500/30 text-yellow-300'
							: 'text-neutral-500 hover:text-neutral-300'
					}`}
					title={isSolo ? 'Unsolo track' : 'Solo track'}
				>
					S
				</button>
				{/* Phase 4.8.5 — Delete track */}
				<button
					onClick={handleDelete}
					onPointerDown={(e) => e.stopPropagation()}
					className={`px-1 py-0.5 text-[9px] font-medium rounded transition-colors opacity-0 group-hover:opacity-100 ${
						showDeleteConfirm
							? 'bg-red-500/30 text-red-300'
							: 'text-neutral-500 hover:text-red-400'
					}`}
					title={showDeleteConfirm ? 'Click again to confirm' : 'Delete track'}
				>
					{showDeleteConfirm ? '✓' : '✕'}
				</button>
			</div>
		</div>
	);
};

import {useCallback} from 'react';
import {IconButton} from '../../icon-button';
import {MuteIcon} from '../../icons/mute';
import {UnmuteIcon} from '../../icons/unmute';
import {muteTrack, unmuteTrack} from '../../state/actions/mute-track';
import {TrackType} from '../../state/types';
import {useWriteContext} from '../../utils/use-context';

export const TimelineMuteTrack = ({track}: {track: TrackType}) => {
	const {setState} = useWriteContext();

	const toggle = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			e.preventDefault();

			if (track.muted) {
				setState({
					update: (state) => {
						return unmuteTrack(state, track.id);
					},
					commitToUndoStack: true,
				});
			} else {
				setState({
					update: (state) => {
						return muteTrack(state, track.id);
					},
					commitToUndoStack: true,
				});
			}
		},
		[track.muted, setState, track.id],
	);

	const onPointerDown = useCallback((e: React.PointerEvent) => {
		// Prevent items from being unselected
		e.stopPropagation();
	}, []);

	return (
		<IconButton
			onClick={toggle}
			onPointerDown={onPointerDown}
			aria-label={track.muted ? 'Unmute Track' : 'Mute Track'}
		>
			{track.muted ? (
				<UnmuteIcon className="text-editor-starter-accent size-4" />
			) : (
				<MuteIcon className="size-4 text-neutral-400" />
			)}
		</IconButton>
	);
};

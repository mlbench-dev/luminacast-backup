import React, {useCallback} from 'react';
import {IconButton} from '../../icon-button';
import {EyeIcon, EyeOffIcon} from '../../icons/visibility';
import {hideTrack, unhideTrack} from '../../state/actions/hide-track';
import {TrackType} from '../../state/types';
import {useWriteContext} from '../../utils/use-context';

export const TimelineHideTrack = ({track}: {track: TrackType}) => {
	const {setState} = useWriteContext();

	const toggle = useCallback(
		(e: React.MouseEvent) => {
			e.stopPropagation();
			e.preventDefault();

			if (track.hidden) {
				setState({
					update: (state) => {
						return unhideTrack(state, track.id);
					},
					commitToUndoStack: false,
				});
			} else {
				setState({
					update: (state) => {
						return hideTrack(state, track.id);
					},
					commitToUndoStack: false,
				});
			}
		},
		[track.hidden, setState, track.id],
	);

	const onPointerDown = useCallback((e: React.PointerEvent) => {
		// Prevent items from being unselected
		e.stopPropagation();
	}, []);

	return (
		<IconButton
			onClick={toggle}
			onPointerDown={onPointerDown}
			aria-label={track.hidden ? 'Unhide Track' : 'Hide Track'}
		>
			{track.hidden ? (
				<EyeOffIcon className="text-editor-starter-accent size-4" />
			) : (
				<EyeIcon className="size-4 text-neutral-400 hover:text-white" />
			)}
		</IconButton>
	);
};
